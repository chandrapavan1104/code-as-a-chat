"""Structured, revalidated controls for listening TCP sockets."""

import getpass
import os
import signal
import shutil

import psutil
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.skills.ports import _list_ports

router = APIRouter(prefix="/ports", tags=["ports"])


def _process_detail(row: dict) -> dict:
    result = dict(row)
    try:
        proc = psutil.Process(row["pid"])
        result.update(
            cmdline=" ".join(proc.cmdline()) or proc.name(),
            started_at=proc.create_time(),
            owner_uid=proc.uids().real,
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
        result.update(cmdline=None, started_at=None, owner_uid=None)
    result["protected"] = row["pid"] == os.getpid()
    result["can_terminate"] = (
        not result["protected"]
        and result["started_at"] is not None
        and row["pid"] >= 100
        and row["user"] == getpass.getuser()
    )
    return result


@router.get("")
async def list_listeners():
    rows = await _list_ports()
    return {"available": shutil.which("lsof") is not None,
            "ports": [_process_detail(row) for row in rows]}


@router.get("/{port}")
async def port_detail(port: int):
    if not 1 <= port <= 65535:
        raise HTTPException(422, "port must be between 1 and 65535")
    rows = [row for row in await _list_ports() if row["port"] == port]
    if not rows:
        raise HTTPException(404, "nothing is listening on this port")
    return {"port": port, "processes": [_process_detail(row) for row in rows]}


class TerminateRequest(BaseModel):
    pid: int = Field(gt=0)
    command: str = Field(min_length=1, max_length=256)
    address: str = Field(min_length=1, max_length=256)
    started_at: float = Field(gt=0, allow_inf_nan=False)


@router.post("/{port}/terminate")
async def terminate_listener(port: int, request: TerminateRequest):
    if not 1 <= port <= 65535:
        raise HTTPException(422, "port must be between 1 and 65535")
    if request.pid == os.getpid():
        raise HTTPException(403, "refusing to terminate the Code-as-a-chat server")

    # Recheck both the listening socket and process start time immediately before
    # signaling. This prevents a stale screen or recycled PID from hitting a new process.
    matches = [row for row in await _list_ports()
               if row["port"] == port and row["pid"] == request.pid
               and row["command"] == request.command
               and row["address"] == request.address]
    if not matches:
        raise HTTPException(409, "listener changed; refresh before terminating")
    row = matches[0]
    if row["user"] != getpass.getuser():
        raise HTTPException(403, "only processes owned by the current user can be terminated")
    if request.pid < 100:
        raise HTTPException(403, "refusing to terminate a system process")
    try:
        proc = psutil.Process(request.pid)
        if abs(proc.create_time() - request.started_at) > 0.01:
            raise HTTPException(409, "process changed; refresh before terminating")
        if proc.uids().real != os.getuid():
            raise HTTPException(403, "only processes owned by the current user can be terminated")
        proc.send_signal(signal.SIGTERM)
    except psutil.NoSuchProcess as exc:
        raise HTTPException(409, "process exited; refresh the port list") from exc
    except psutil.AccessDenied as exc:
        raise HTTPException(403, "permission denied for this process") from exc
    return {"sent": "SIGTERM", "pid": request.pid, "port": port}
