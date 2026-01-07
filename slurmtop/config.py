"""Configuration loading for SlurmTop."""

from __future__ import annotations

from pathlib import Path

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - fallback for <3.11
    import tomli as tomllib  # type: ignore[no-redef]


CONFIG_PATH = Path("config/interactive_job.toml")

DEFAULT_INTERACTIVE_CONFIG = {
    "partition": "debug",
    "account": "research",
    "time_limit": "01:00:00",
    "nodes": 1,
    "gpus": 1,
    "job_refresh_interval": 30.0,  # Seconds between job list refreshes
}


def load_interactive_config() -> dict[str, object]:
    """Load interactive job configuration from TOML file."""
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as fh:
            data = tomllib.load(fh)
    else:
        data = DEFAULT_INTERACTIVE_CONFIG.copy()
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(_dict_to_toml(data), encoding="utf-8")
    merged = DEFAULT_INTERACTIVE_CONFIG | data
    return merged


def _dict_to_toml(data: dict[str, object]) -> str:
    """Convert a simple dict to TOML format."""
    lines = []
    for key, value in data.items():
        if isinstance(value, str):
            lines.append(f"{key} = \"{value}\"")
        else:
            lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"
