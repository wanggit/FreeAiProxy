#!/usr/bin/env python3
"""Start, stop, restart, build, and inspect the FreeAiProxy server.

Run through the project environment:

    uv run python scripts/manage.py start
    uv run python scripts/manage.py stop
    uv run python scripts/manage.py restart
    uv run python scripts/manage.py build
    uv run python scripts/manage.py status

The server is started as a detached child process. Its PID is stored in
``data/freeaiproxy.pid`` and its output is appended to ``data/freeaiproxy.log``.
Only processes started by this script are stopped; an unmanaged server that is
already listening on the configured port is reported and left alone.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PID_FILE = DATA_DIR / "freeaiproxy.pid"
LOG_FILE = DATA_DIR / "freeaiproxy.log"
DEFAULT_CONFIG = ROOT / "config.toml"


def read_server(config_path: Path) -> tuple[str, int, str]:
    host = "127.0.0.1"
    port = 8000
    if config_path.is_file():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        server = data.get("server", {})
        host = str(server.get("host", host))
        port = int(server.get("port", port))
    check_host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    return host, port, check_host


def fetch_health(host: str, port: int) -> dict | None:
    url = f"http://{host}:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def read_pid_record() -> dict | None:
    if not PID_FILE.is_file():
        return None
    try:
        record = json.loads(PID_FILE.read_text(encoding="utf-8"))
        int(record["pid"])
        float(record["create_time"])
        return record
    except Exception:
        return None


def remove_pid_file() -> None:
    PID_FILE.unlink(missing_ok=True)


def process_matches(record: dict) -> psutil.Process | None:
    try:
        proc = psutil.Process(int(record["pid"]))
        if abs(proc.create_time() - float(record["create_time"])) > 1.0:
            return None
        return proc if proc.is_running() else None
    except (psutil.Error, KeyError, ValueError):
        return None


def managed_process() -> tuple[psutil.Process | None, dict | None]:
    record = read_pid_record()
    if record is None:
        return None, None
    proc = process_matches(record)
    if proc is None:
        remove_pid_file()
        return None, None
    return proc, record


def print_health(data: dict) -> None:
    providers = data.get("providers") or {}
    print(f"  health: {data.get('status', 'unknown')}")
    for name, state in providers.items():
        print(
            f"  {name}: logged_in={state.get('logged_in')} "
            f"state={state.get('state')} "
            f"active={state.get('active_requests')} waiting={state.get('waiting_requests')}"
        )


def wait_for_health(
    host: str, port: int, process: psutil.Process, timeout: float
) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = fetch_health(host, port)
        if data is not None:
            return data
        if not process.is_running():
            return None
        time.sleep(1.0)
    return fetch_health(host, port)


def start(args: argparse.Namespace) -> int:
    config_path = args.config.resolve()
    host, port, check_host = read_server(config_path)
    if not config_path.is_file():
        print(f"config not found: {config_path}")
        print("create it first: cp config.example.toml config.toml")
        return 1

    proc, _ = managed_process()
    if proc is not None:
        print(f"already running (pid {proc.pid})")
        data = fetch_health(check_host, port)
        if data:
            print_health(data)
        print(f"logs: {LOG_FILE}")
        return 0

    if fetch_health(check_host, port) is not None or port_open(check_host, port):
        print(
            f"port {check_host}:{port} is already in use by an unmanaged process; "
            "stop it first"
        )
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "freeaiproxy.cli",
        "start",
        "--config",
        str(config_path),
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    kwargs: dict = {
        "cwd": str(ROOT),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": LOG_FILE.open("ab"),
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True

    try:
        child = subprocess.Popen(cmd, **kwargs)
    except Exception as exc:
        print(f"failed to start: {exc}")
        return 1
    finally:
        kwargs["stdout"].close()

    time.sleep(0.5)
    try:
        proc = psutil.Process(child.pid)
        if not proc.is_running():
            raise psutil.NoSuchProcess(child.pid)
        record = {
            "pid": proc.pid,
            "create_time": proc.create_time(),
            "started_at": time.time(),
            "config": str(config_path),
        }
        PID_FILE.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except psutil.Error:
        print("server process exited during startup")
        child.wait(timeout=5)
        print(f"exit code: {child.returncode}")
        print(f"logs: {LOG_FILE}")
        return 1

    print(f"started (pid {child.pid})")
    print(f"url: http://{check_host}:{port}")
    print(f"logs: {LOG_FILE}")
    print(f"waiting up to {args.wait:g}s for /healthz...")
    data = wait_for_health(check_host, port, proc, args.wait)
    if data is not None:
        print_health(data)
        return 0
    if not proc.is_running():
        print("server exited during startup")
        remove_pid_file()
        print(f"logs: {LOG_FILE}")
        return 1
    print("server is still starting (login may be required in the browser window)")
    print("check again with: uv run python scripts/manage.py status")
    return 0


def stop(args: argparse.Namespace) -> int:
    proc, record = managed_process()
    if proc is None:
        host, port, check_host = read_server(args.config.resolve())
        if fetch_health(check_host, port) is not None:
            print(
                f"a server is responding on http://{check_host}:{port} "
                "but it was not started by this script; refusing to stop it"
            )
        else:
            print("not running")
        return 0

    pid = proc.pid
    print(f"stopping (pid {pid})...")
    children = []
    with contextlib.suppress(psutil.Error):
        children = proc.children(recursive=True)
    procs = [proc, *children]
    for item in procs:
        with contextlib.suppress(psutil.Error):
            item.terminate()
    gone, alive = psutil.wait_procs(procs, timeout=args.timeout)
    if alive:
        print(f"force killing {len(alive)} remaining process(es)...")
        for item in alive:
            with contextlib.suppress(psutil.Error):
                item.kill()
        psutil.wait_procs(alive, timeout=5)

    remaining = []
    with contextlib.suppress(psutil.Error):
        remaining = proc.children(recursive=True)
    for item in remaining:
        with contextlib.suppress(psutil.Error):
            item.kill()

    remove_pid_file()
    host, port, check_host = read_server(args.config.resolve())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and fetch_health(check_host, port) is not None:
        time.sleep(0.5)
    if proc.is_running() and process_matches(record or {}) is not None:
        print(f"failed to stop pid {pid}")
        return 1
    print("stopped")
    return 0


def restart(args: argparse.Namespace) -> int:
    stop(args)
    host, port, check_host = read_server(args.config.resolve())
    if fetch_health(check_host, port) is not None:
        print(
            f"port {check_host}:{port} is still occupied; refusing to start a second instance"
        )
        return 1
    return start(args)


def build(_args: argparse.Namespace) -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("uv not found in PATH")
        return 1
    print("building wheel + sdist with uv...", flush=True)
    result = subprocess.run([uv, "build"], cwd=ROOT)
    if result.returncode != 0:
        print(f"build failed (exit {result.returncode})")
        return result.returncode
    dist = ROOT / "dist"
    if dist.is_dir():
        print("artifacts:")
        for path in sorted(dist.glob("*")):
            if path.is_file() and path.name != ".gitignore":
                print(f"  {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")
    return 0


def status(args: argparse.Namespace) -> int:
    host, port, check_host = read_server(args.config.resolve())
    proc, record = managed_process()
    if proc is not None:
        started = ""
        if record and record.get("started_at"):
            started = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(float(record["started_at"]))
            )
        print(f"managed process: running (pid {proc.pid}{', started ' + started if started else ''})")
    else:
        print("managed process: not running")
    data = fetch_health(check_host, port)
    if data is None:
        print(f"health: unavailable at http://{check_host}:{port}/healthz")
    else:
        print_health(data)
    print(f"pid file: {PID_FILE}")
    print(f"logs: {LOG_FILE}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="config file path (default: config.toml)",
    )
    parser = argparse.ArgumentParser(
        description="Manage the FreeAiProxy server.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", parents=[common], help="start the server")
    p_start.add_argument(
        "--wait", type=float, default=30.0, help="seconds to wait for /healthz"
    )
    p_start.set_defaults(func=start)

    p_stop = sub.add_parser("stop", parents=[common], help="stop the managed server")
    p_stop.add_argument(
        "--timeout", type=float, default=10.0, help="graceful stop timeout in seconds"
    )
    p_stop.set_defaults(func=stop)

    p_restart = sub.add_parser("restart", parents=[common], help="stop then start")
    p_restart.add_argument(
        "--timeout", type=float, default=10.0, help="graceful stop timeout in seconds"
    )
    p_restart.add_argument(
        "--wait", type=float, default=30.0, help="seconds to wait for /healthz"
    )
    p_restart.set_defaults(func=restart)

    p_build = sub.add_parser("build", help="build wheel and sdist with uv")
    p_build.set_defaults(func=build)

    p_status = sub.add_parser("status", parents=[common], help="show process and health")
    p_status.set_defaults(func=status)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
