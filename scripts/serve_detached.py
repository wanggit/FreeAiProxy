"""Start the gateway fully detached so the calling shell never waits on it."""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

root = Path(__file__).resolve().parents[1]
log_dir = root / "data"
log_dir.mkdir(exist_ok=True)

with contextlib.ExitStack() as stack:
    out = stack.enter_context(open(log_dir / "server.log", "ab", buffering=0))
    err = stack.enter_context(open(log_dir / "server.err.log", "ab", buffering=0))
    proc = subprocess.Popen(
        [str(root / ".venv" / "Scripts" / "freeaiproxy.exe"), "start"],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=err,
        close_fds=True,
        creationflags=DETACHED,
    )
print(f"started pid={proc.pid}")
