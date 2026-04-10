"""Persistent configuration file for SlurmTop (~/.config/slurmtop/config.toml)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Use tomllib (3.11+) for reading, fall back to manual parsing for writing
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "slurmtop"
CONFIG_FILE = CONFIG_DIR / "config.toml"
LOG_CACHE_FILE = CONFIG_DIR / "log_cache.json"


def load() -> dict:
    """Load persistent config. Returns empty dict if file doesn't exist."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def save(data: dict) -> None:
    """Save persistent config as TOML."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"\n[{key}]")
            for k, v in value.items():
                lines.append(f"{k} = {_toml_value(v)}")
        else:
            lines.append(f"{key} = {_toml_value(value)}")
    CONFIG_FILE.write_text("\n".join(lines) + "\n")


def _toml_value(v) -> str:
    """Format a Python value as a TOML literal."""
    if isinstance(v, list):
        items = ", ".join(f'"{i}"' if isinstance(i, str) else str(i) for i in v)
        return f"[{items}]"
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def get_partition_order() -> list[str] | None:
    """Load partition order from config file."""
    data = load()
    order = data.get("partition_order")
    if isinstance(order, list) and order:
        return order
    return None


def set_partition_order(order: list[str]) -> None:
    """Save partition order to config file, preserving other settings."""
    data = load()
    data["partition_order"] = order
    save(data)


def get_partition_colors() -> dict[str, str] | None:
    """Load custom partition→color mapping from config file."""
    data = load()
    colors = data.get("partition_colors")
    if isinstance(colors, dict) and colors:
        return colors
    return None


def set_partition_colors(colors: dict[str, str]) -> None:
    """Save custom partition→color mapping to config file."""
    data = load()
    data["partition_colors"] = colors
    save(data)


# ---------------------------------------------------------------------------
# Log path cache — remembers StdOut/StdErr paths from scontrol
# ---------------------------------------------------------------------------
# Format: {"<job_id>": {"stdout": "...", "stderr": "...", "command": "...", "workdir": "...", "ts": <epoch>}, ...}


def _load_log_cache() -> dict:
    if not LOG_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(LOG_CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_log_cache(cache: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_CACHE_FILE.write_text(json.dumps(cache))


def cache_job_paths(
    job_id: str,
    stdout_path: str | None = None,
    stderr_path: str | None = None,
    command: str | None = None,
    work_dir: str | None = None,
) -> None:
    """Store paths for a job (called when scontrol provides them)."""
    if not any((stdout_path, stderr_path, command, work_dir)):
        return
    cache = _load_log_cache()
    entry = cache.get(job_id, {})
    if stdout_path:
        entry["stdout"] = stdout_path
    if stderr_path:
        entry["stderr"] = stderr_path
    if command:
        entry["command"] = command
    if work_dir:
        entry["workdir"] = work_dir
    entry["ts"] = time.time()
    cache[job_id] = entry
    _save_log_cache(cache)


# Backwards-compatible alias
cache_log_paths = cache_job_paths


def get_cached_log_paths(job_id: str) -> tuple[str | None, str | None]:
    """Retrieve cached stdout/stderr paths for a job."""
    cache = _load_log_cache()
    entry = cache.get(job_id)
    if not entry:
        return None, None
    return entry.get("stdout") or None, entry.get("stderr") or None


def get_cached_command(job_id: str) -> tuple[str | None, str | None]:
    """Retrieve cached command and workdir for a job. Returns (command, workdir)."""
    cache = _load_log_cache()
    entry = cache.get(job_id)
    if not entry:
        return None, None
    return entry.get("command") or None, entry.get("workdir") or None


def prune_log_cache(max_age_days: int | None = 30) -> None:
    """Remove cache entries older than max_age_days. None = never prune."""
    if max_age_days is None:
        return
    cache = _load_log_cache()
    cutoff = time.time() - max_age_days * 86400
    pruned = {k: v for k, v in cache.items() if v.get("ts", 0) > cutoff}
    if len(pruned) < len(cache):
        _save_log_cache(pruned)
