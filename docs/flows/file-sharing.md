# Files and code in Gajala

Ask “Send me the README from this project” or “Share ~/Downloads/report.pdf”.
The assistant uses `filemanager` with `share <path>`; `/files share <path>` also
works directly. Regular files up to 200 MB are copied to a unique uploads folder.
Original files are untouched. The authenticated file endpoint serves the copy,
so changing project does not break an earlier attachment.

Tap the chat file card, then Download. Text/code previews are limited to 128 KB;
images support zoom. Open in another app hands the downloaded file to Android.
PDFs, Office documents, archives, and other types require an installed viewer.
HTML is displayed as source, never executed inside chat. App-private downloaded
copies remain available to the Android viewer; this is not a public sharing link.

Code inside triple-backtick fences has a language label and a Copy button that
copies the code verbatim. File markers inside code fences remain code, not cards.
Old chats containing file markers get the same cards when reopened.

Files on the Mac must be readable by the running Gajala service. Shares are
snapshots, not live synchronized files; ask for a new share after editing one.
