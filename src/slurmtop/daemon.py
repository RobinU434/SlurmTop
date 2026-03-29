"""Background daemon that caches stdout/stderr paths for running Slurm jobs.

Can run standalone (`slurmtop-daemon start`) or as a thread inside SlurmTop.
Uses a PID file for coordination: SlurmTop checks if the daemon is already
running and skips starting its own caching thread if so.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from slurmtop.config import CONFIG_DIR, cache_log_paths, prune_log_cache

PID_FILE = CONFIG_DIR / "daemon.pid"
DEFAULT_INTERVAL = 30  # seconds between polls


# ---------------------------------------------------------------------------
# PID file management
# ---------------------------------------------------------------------------


def _write_pid() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def _remove_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def is_daemon_running() -> bool:
    """Check if a standalone daemon is already running."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        # Check if process is alive
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        # Stale PID file
        _remove_pid()
        return False


# ---------------------------------------------------------------------------
# Core polling logic (synchronous, used by both daemon and thread)
# ---------------------------------------------------------------------------


def _poll_and_cache(user: str = "", remote: str = "") -> int:
    """Run squeue + scontrol for all running jobs, cache log paths.

    Returns the number of jobs cached.
    """
    user = user or os.environ.get("USER", "")
    if not user:
        return 0

    # Build squeue command
    cmd = ["squeue", "-u", user, "--format=%i", "--noheader", "--states=RUNNING"]
    if remote:
        import shlex
        remote_cmd = " ".join(shlex.quote(a) for a in cmd)
        cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=5",
            "-o", "BatchMode=yes",
            remote, remote_cmd,
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return 0
    except Exception:
        return 0

    job_ids = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    if not job_ids:
        return 0

    cached = 0
    for job_id in job_ids:
        stdout_path, stderr_path = _get_log_paths(job_id, remote)
        if stdout_path or stderr_path:
            cache_log_paths(job_id, stdout_path, stderr_path)
            cached += 1

    return cached


def _get_log_paths(job_id: str, remote: str = "") -> tuple[str | None, str | None]:
    """Get StdOut/StdErr from scontrol for a single job."""
    cmd = ["scontrol", "show", "job", job_id]
    if remote:
        import shlex
        remote_cmd = " ".join(shlex.quote(a) for a in cmd)
        cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=5",
            "-o", "BatchMode=yes",
            remote, remote_cmd,
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return None, None
    except Exception:
        return None, None

    stdout_path = None
    stderr_path = None
    for line in result.stdout.splitlines():
        for token in line.split():
            if token.startswith("StdOut="):
                stdout_path = token.split("=", 1)[1]
            elif token.startswith("StdErr="):
                stderr_path = token.split("=", 1)[1]
    return stdout_path, stderr_path


# ---------------------------------------------------------------------------
# Background thread (used inside SlurmTop when no daemon is running)
# ---------------------------------------------------------------------------


class CacheThread:
    """Background thread that periodically caches log paths."""

    def __init__(self, user: str = "", remote: str = "", interval: float = DEFAULT_INTERVAL) -> None:
        self.user = user
        self.remote = remote
        self.interval = interval
        self._stop = False
        self._thread: __import__("threading").Thread | None = None

    def start(self) -> None:
        import threading
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop:
            try:
                _poll_and_cache(self.user, self.remote)
            except Exception:
                pass
            # Sleep in small increments so we can stop quickly
            for _ in range(int(self.interval)):
                if self._stop:
                    return
                time.sleep(1)


# ---------------------------------------------------------------------------
# Standalone daemon
# ---------------------------------------------------------------------------


def _daemon_loop(user: str, remote: str, interval: float) -> None:
    """Main loop for the standalone daemon process."""
    _write_pid()
    atexit.register(_remove_pid)

    # Handle SIGTERM gracefully
    def _handle_term(signum, frame):
        _remove_pid()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_term)

    # Prune old entries on start
    prune_log_cache(max_age_days=30)

    while True:
        try:
            n = _poll_and_cache(user, remote)
            if n > 0:
                pass  # Silently cached
        except Exception:
            pass
        time.sleep(interval)


def start_daemon(user: str = "", remote: str = "", interval: float = DEFAULT_INTERVAL) -> bool:
    """Start the daemon as a detached background process. Returns True if started."""
    if is_daemon_running():
        return False

    # Fork ourselves as a background process
    cmd = [
        sys.executable, "-m", "slurmtop.daemon",
        "--user", user or os.environ.get("USER", ""),
        "--interval", str(interval),
    ]
    if remote:
        cmd.extend(["--remote", remote])

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def stop_daemon() -> bool:
    """Stop the running daemon. Returns True if stopped."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        _remove_pid()
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        _remove_pid()
        return False


# ---------------------------------------------------------------------------
# CLI entry point: slurmtop-daemon start|stop|status
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="slurmtop-daemon",
        description="Background daemon that caches Slurm job log paths.",
    )
    parser.add_argument(
        "action",
        choices=["start", "stop", "status"],
        help="start/stop the daemon or check its status",
    )
    parser.add_argument("--user", default=os.environ.get("USER", ""))
    parser.add_argument("--remote", default="")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)

    args = parser.parse_args()

    if args.action == "status":
        if is_daemon_running():
            pid = PID_FILE.read_text().strip()
            print(f"slurmtop-daemon is running (PID {pid})")
        else:
            print("slurmtop-daemon is not running")

    elif args.action == "start":
        if is_daemon_running():
            print("slurmtop-daemon is already running")
            sys.exit(1)
        # Run in foreground if called directly as module
        print(f"slurmtop-daemon starting (user={args.user}, interval={args.interval}s)")
        _daemon_loop(args.user, args.remote, args.interval)

    elif args.action == "stop":
        if stop_daemon():
            print("slurmtop-daemon stopped")
        else:
            print("slurmtop-daemon is not running")


if __name__ == "__main__":
    main()
