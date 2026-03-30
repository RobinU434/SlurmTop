"""DataTable-based widget for displaying Slurm job lists."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import DataTable
from rich.text import Text

from slurmtop.models import CompletedJob, RunningJob

# Color mapping for terminated job states
_STATE_STYLES: dict[str, str] = {
    "COMPLETED": "green",
    "FAILED": "red bold",
    "TIMEOUT": "yellow",
    "CANCELLED": "dim",
    "CANCELLED+": "dim",
    "OUT_OF_MEMORY": "red",
    "NODE_FAIL": "red",
    "PREEMPTED": "yellow dim",
}

# Deterministic partition colors
# Color mapping for active job states (applied to Job ID column)
_ACTIVE_STATE_STYLES: dict[str, str] = {
    "RUNNING": "green",
    "PENDING": "yellow",
    "COMPLETING": "dark_orange",
    "REQUEUED": "yellow dim",
    "SUSPENDED": "dim",
    "PREEMPTED": "yellow dim",
}

_PARTITION_COLORS = [
    "cyan", "magenta", "yellow", "green", "blue",
    "red", "bright_cyan", "bright_magenta", "bright_green",
]

# Custom overrides loaded from config, set via set_partition_colors()
_custom_partition_colors: dict[str, str] = {}


def set_partition_colors(colors: dict[str, str] | None) -> None:
    """Set custom partition→color mapping (from config file)."""
    global _custom_partition_colors
    _custom_partition_colors = colors or {}


def _partition_style(partition: str) -> str:
    if not partition:
        return ""
    if partition in _custom_partition_colors:
        return _custom_partition_colors[partition]
    return _PARTITION_COLORS[sum(ord(c) for c in partition) % len(_PARTITION_COLORS)]


def _styled_state(state: str) -> Text:
    """Return a Rich Text object with color based on job state."""
    base_state = state.split(" ")[0] if " " in state else state
    style = _STATE_STYLES.get(base_state, "")
    return Text(state, style=style)


class JobSelected(Message):
    """Posted when the user moves the cursor to a different job."""

    def __init__(self, job_id: str, source_table: str) -> None:
        super().__init__()
        self.job_id = job_id
        self.source_table = source_table


# ---------------------------------------------------------------------------
# Active Jobs Table
# ---------------------------------------------------------------------------


class ActiveJobTable(DataTable):
    """Upper-left panel: currently running / pending jobs."""

    COLUMNS = ("Job ID", "Name", "Elapsed", "Partition")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._all_jobs: list[RunningJob] = []
        self._filter_text: str = ""
        self._bookmarked: set[str] = set()

    def on_mount(self) -> None:
        for col in self.COLUMNS:
            self.add_column(col, key=col)
        self.cursor_type = "row"
        self.zebra_stripes = True

    def watch_has_focus(self, focused: bool) -> None:
        self.show_cursor = focused
        if focused:
            job_id = self.get_selected_job_id()
            if job_id:
                self.post_message(JobSelected(job_id, "active"))

    def update_jobs(self, jobs: list[RunningJob]) -> None:
        self._all_jobs = jobs
        self._rebuild()

    def apply_filter(self, text: str) -> None:
        self._filter_text = text.lower()
        self._rebuild()

    def set_bookmarks(self, ids: set[str]) -> None:
        self._bookmarked = ids
        self._rebuild()

    def _rebuild(self) -> None:
        """Rebuild table from _all_jobs, applying filter and bookmark sorting."""
        filtered = self._all_jobs
        if self._filter_text:
            filtered = [
                j for j in filtered
                if self._filter_text in j.job_id.lower()
                or self._filter_text in j.name.lower()
                or self._filter_text in j.partition.lower()
            ]

        # Sort: bookmarked first, then original order
        bookmarked = [j for j in filtered if j.job_id in self._bookmarked]
        rest = [j for j in filtered if j.job_id not in self._bookmarked]

        new_data: dict[str, tuple] = {}
        for job in bookmarked + rest:
            name = f"★ {job.name}" if job.job_id in self._bookmarked else job.name
            state_style = _ACTIVE_STATE_STYLES.get(job.state, "")
            id_text = Text(job.job_id, style=state_style)
            part_text = Text(job.partition, style=_partition_style(job.partition))
            new_data[job.job_id] = (id_text, name, job.elapsed, part_text)

        self._apply_diff(new_data)

    def _apply_diff(self, new_data: dict[str, tuple]) -> None:
        existing_keys: set[str] = set()
        for i in range(self.row_count):
            try:
                row_key, _ = self.coordinate_to_cell_key(
                    self.cursor_coordinate._replace(row=i, column=0)
                )
                existing_keys.add(str(row_key.value))
            except Exception:
                break

        new_keys = set(new_data.keys())
        added = new_keys - existing_keys
        removed = existing_keys - new_keys

        # If rows were added or removed, full rebuild to preserve sort order
        if added or removed:
            old_selected = self.get_selected_job_id()
            self.clear()
            for key, values in new_data.items():
                self.add_row(*values, key=key)
            if old_selected and old_selected in new_keys:
                try:
                    idx = self.get_row_index(old_selected)
                    self.move_cursor(row=idx)
                except Exception:
                    pass
            return

        # No structural changes — just update changed cells in place
        for key, values in new_data.items():
            for col_key, value in zip(self.COLUMNS, values):
                try:
                    current = self.get_cell(key, col_key)
                    if str(current) != str(value):
                        self.update_cell(key, col_key, value)
                except Exception:
                    pass

    def get_selected_job_id(self) -> str | None:
        if self.row_count == 0:
            return None
        try:
            row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
            return str(row_key.value)
        except Exception:
            return None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        job_id = str(event.row_key.value) if event.row_key else self.get_selected_job_id()
        if job_id:
            self.post_message(JobSelected(job_id, "active"))


# ---------------------------------------------------------------------------
# Completed Jobs Table
# ---------------------------------------------------------------------------


class CompletedJobTable(DataTable):
    """Lower-left panel: past completed / failed / cancelled jobs."""

    COLUMNS = ("Job ID", "Name", "State", "Partition", "Elapsed")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._all_jobs: list[CompletedJob] = []
        self._filter_text: str = ""
        self._bookmarked: set[str] = set()

    def on_mount(self) -> None:
        for col in self.COLUMNS:
            self.add_column(col, key=col)
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.show_cursor = False

    def watch_has_focus(self, focused: bool) -> None:
        self.show_cursor = focused
        if focused:
            job_id = self.get_selected_job_id()
            if job_id:
                self.post_message(JobSelected(job_id, "completed"))

    def update_jobs(self, jobs: list[CompletedJob]) -> None:
        self._all_jobs = jobs
        self._rebuild()

    def apply_filter(self, text: str) -> None:
        self._filter_text = text.lower()
        self._rebuild()

    def set_bookmarks(self, ids: set[str]) -> None:
        self._bookmarked = ids
        self._rebuild()

    def _rebuild(self) -> None:
        filtered = self._all_jobs
        if self._filter_text:
            filtered = [
                j for j in filtered
                if self._filter_text in j.job_id.lower()
                or self._filter_text in j.name.lower()
                or self._filter_text in j.partition.lower()
                or self._filter_text in j.state.lower()
            ]

        bookmarked = [j for j in filtered if j.job_id in self._bookmarked]
        rest = [j for j in filtered if j.job_id not in self._bookmarked]

        new_data: dict[str, tuple] = {}
        for job in bookmarked + rest:
            name = f"★ {job.name}" if job.job_id in self._bookmarked else job.name
            state_text = _styled_state(job.state)
            part_text = Text(job.partition, style=_partition_style(job.partition))
            new_data[job.job_id] = (job.job_id, name, state_text, part_text, job.elapsed)

        self._apply_diff(new_data)

    def _apply_diff(self, new_data: dict[str, tuple]) -> None:
        existing_keys: set[str] = set()
        for i in range(self.row_count):
            try:
                row_key, _ = self.coordinate_to_cell_key(
                    self.cursor_coordinate._replace(row=i, column=0)
                )
                existing_keys.add(str(row_key.value))
            except Exception:
                break

        new_keys = set(new_data.keys())
        added = new_keys - existing_keys
        removed = existing_keys - new_keys

        # If there are new rows or removed rows, do a full rebuild to
        # preserve the correct sort order (new rows must appear at top,
        # not appended at bottom).
        if added or removed:
            old_selected = self.get_selected_job_id()
            self.clear()
            for key, values in new_data.items():
                self.add_row(*values, key=key)
            if old_selected and old_selected in new_keys:
                try:
                    idx = self.get_row_index(old_selected)
                    self.move_cursor(row=idx)
                except Exception:
                    pass
            return

        # No structural changes — just update changed cells in place
        for key, values in new_data.items():
            for col_key, value in zip(self.COLUMNS, values):
                try:
                    current = self.get_cell(key, col_key)
                    if str(current) != str(value):
                        self.update_cell(key, col_key, value)
                except Exception:
                    pass

    def get_selected_job_id(self) -> str | None:
        if self.row_count == 0:
            return None
        try:
            row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
            return str(row_key.value)
        except Exception:
            return None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        job_id = str(event.row_key.value) if event.row_key else self.get_selected_job_id()
        if job_id:
            self.post_message(JobSelected(job_id, "completed"))
