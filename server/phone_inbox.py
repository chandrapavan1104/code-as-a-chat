"""Authenticated, immutable file transfers from the phone to the Mac."""

import asyncio
import errno
import re
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.requests import ClientDisconnect

from server import media

router = APIRouter(prefix="/phone/inbox", tags=["phone inbox"])
MAX_BYTES = 200 * 1024 * 1024
_uploads = asyncio.Semaphore(3)
_ID = re.compile(r"[0-9a-f]{32}")


def inbox() -> Path:
    return media.UPLOADS_DIR / "phone-inbox"


def _receipt(directory: Path) -> dict | None:
    if not directory.is_dir() or directory.is_symlink():
        return None
    files = [file for file in directory.iterdir() if file.is_file() and not file.is_symlink()]
    if len(files) != 1:
        return None
    file = files[0]
    stat = file.stat()
    return {"id": directory.name, "path": str(file), "name": file.name,
            "size": stat.st_size, "received_at": directory.stat().st_mtime}


@router.get("")
def list_receipts():
    root = inbox()
    if not root.is_dir():
        return {"files": []}
    directories = sorted(
        (item for item in root.iterdir() if _ID.fullmatch(item.name)),
        key=lambda item: item.stat().st_mtime, reverse=True,
    )
    return {"files": [receipt for item in directories[:100] if (receipt := _receipt(item))]}


@router.post("/{transfer_id}/attachment")
def prepare_attachment(transfer_id: str):
    if not _ID.fullmatch(transfer_id):
        raise HTTPException(404, "File not found.")
    receipt = _receipt(inbox() / transfer_id)
    if receipt is None:
        raise HTTPException(404, "File not found.")
    path = Path(receipt["path"])
    if "]" in path.name or path.name != path.name.rstrip():
        # Chat uses a closing bracket in file markers. Preserve the original
        # and make a marker-safe filename for this explicit attachment.
        directory = media.UPLOADS_DIR / "phone-attachments" / transfer_id
        directory.mkdir(parents=True, exist_ok=True)
        safe = directory / path.name.replace("]", ")").rstrip()
        try:
            safe.hardlink_to(path)
        except FileExistsError:
            pass
        path = safe
    return {"path": str(path)}


@router.put("/{transfer_id}")
async def receive(transfer_id: str, name: str, request: Request):
    if not _ID.fullmatch(transfer_id):
        raise HTTPException(400, "Invalid transfer ID.")
    if (not name.strip() or name in {".", ".."} or len(name.encode()) > 255
            or any(ord(char) < 32 or char in "/\\\x7f" for char in name)):
        raise HTTPException(400, "Choose a file with a valid filename.")
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdecimal() or len(declared) > 18:
            raise HTTPException(400, "Invalid upload size.")
        if int(declared) > MAX_BYTES:
            raise HTTPException(413, "Each file can be up to 200 MiB.")

    async with _uploads:
        return await _receive_file(transfer_id, name, request)


async def _receive_file(transfer_id: str, name: str, request: Request):
    root = inbox()
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    destination = root / transfer_id

    def existing():
        receipt = _receipt(destination)
        if receipt is None or receipt["name"] != name:
            raise HTTPException(409, "This transfer ID belongs to a different file.")
        return receipt

    if destination.exists() or destination.is_symlink():
        return existing()

    # Keep incomplete uploads outside the directory served by /api/file.
    staging = root.parent.parent / "phone-inbox-staging"
    staging.mkdir(mode=0o700, exist_ok=True)
    # Remove staging left by a killed worker. Active HTTP uploads are bounded.
    cutoff = time.time() - 86400
    for leftover in staging.glob("transfer-*"):
        if not leftover.is_symlink() and leftover.stat().st_mtime < cutoff:
            shutil.rmtree(leftover, ignore_errors=True)

    temporary = Path(tempfile.mkdtemp(prefix="transfer-", dir=staging))
    try:
        size = 0
        with (temporary / name).open("xb") as file:
            async with asyncio.timeout(15 * 60):
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(413, "Each file can be up to 200 MiB.")
                    file.write(chunk)
        declared = request.headers.get("content-length")
        if declared is not None and size != int(declared):
            raise HTTPException(400, "The upload ended before the whole file arrived.")
        try:
            temporary.rename(destination)
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise
            return existing()
        return _receipt(destination)
    except ClientDisconnect:
        raise HTTPException(400, "The phone disconnected before the file arrived.") from None
    except TimeoutError:
        raise HTTPException(408, "The upload timed out. Retry from your phone.") from None
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
