"""Python bridge for the SlurmTop Rust TUI."""

from __future__ import annotations

from ._slurmtop import launch as _launch


def run() -> None:
    """Launch the SlurmTop TUI from Python."""
    _launch()


def main() -> None:
    """Console-script entrypoint (``python -m slurmtop``)."""
    run()


__all__ = ["run", "main"]
