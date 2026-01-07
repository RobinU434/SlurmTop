"""Job list item widget for SlurmTop."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import ListItem, Static

from slurmtop.models import Job


class JobListItem(ListItem):
    """List item that retains the underlying Job."""

    def __init__(self, job: Job) -> None:
        super().__init__()
        self.job = job
        self._label = Static(job.display)
    
    def compose(self) -> ComposeResult:
        yield self._label
    
    def refresh_display(self) -> None:
        """Refresh the displayed label when job data changes."""
        self._label.update(self.job.display)
