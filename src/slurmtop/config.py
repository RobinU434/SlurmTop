"""Persistent configuration file for SlurmTop (~/.config/slurmtop/config.toml)."""

from __future__ import annotations

import os
from pathlib import Path

# Use tomllib (3.11+) for reading, fall back to manual parsing for writing
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "slurmtop"
CONFIG_FILE = CONFIG_DIR / "config.toml"


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
