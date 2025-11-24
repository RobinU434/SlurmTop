"""Python implementation of the SlurmTop terminal UI."""

from __future__ import annotations

from .app import SlurmTopApp


def run() -> None:
    """Launch the SlurmTop Textual application."""
    SlurmTopApp().run()


def main() -> None:
    """Console script entrypoint."""
    run()


__all__ = ["SlurmTopApp", "run", "main"]
