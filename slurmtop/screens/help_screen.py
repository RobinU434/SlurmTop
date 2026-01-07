"""Help screen modal for SlurmTop."""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static


class HelpScreen(ModalScreen[None]):
    """Modal overlay showing all keyboard shortcuts."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    def __init__(self, shortcuts: list[tuple[str, str]]) -> None:
        super().__init__()
        self.shortcuts = shortcuts

    def compose(self) -> ComposeResult:  # pragma: no cover - UI composition
        table = Table.grid(padding=(0, 2))
        table.add_column("Shortcut", style="bold cyan")
        table.add_column("Description", style="white")
        for key, description in self.shortcuts:
            table.add_row(key, description)

        yield Static(Panel(table, title="SlurmTop Shortcuts"), id="help-panel")

    async def action_dismiss(self, result: None = None) -> None:
        self.dismiss(result)
