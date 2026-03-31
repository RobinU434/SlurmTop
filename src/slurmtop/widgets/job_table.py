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

# Module-level display settings, set via set_display_config()
_custom_partition_colors: dict[str, str] = {}
_max_name_width: int = 16
_max_partition_width: int = 16
_abbreviate_states: bool = False

# State abbreviations
_STATE_ABBREV: dict[str, str] = {
    "COMPLETED": "COMP",
    "FAILED": "FAIL",
    "TIMEOUT": "TIME",
    "CANCELLED": "CAN",
    "CANCELLED+": "CAN+",
    "OUT_OF_MEMORY": "OOM",
    "NODE_FAIL": "NFAIL",
    "PREEMPTED": "PREEMPT",
    "RUNNING": "RUN",
    "PENDING": "PEND",
    "COMPLETING": "CG",
    "SUSPENDED": "SUSP",
    "REQUEUED": "REQ",
}


def set_partition_colors(colors: dict[str, str] | None) -> None:
    """Set custom partition→color mapping (from config file)."""
    global _custom_partition_colors
    _custom_partition_colors = colors or {}


def set_display_config(
    max_name: int = 16,
    max_partition: int = 16,
    abbreviate: bool = False,
) -> None:
    """Set column width and abbreviation settings."""
    global _max_name_width, _max_partition_width, _abbreviate_states
    _max_name_width = max_name
    _max_partition_width = max_partition
    _abbreviate_states = abbreviate


def _truncate(text: str, max_width: int) -> str:
    """Truncate text to max_width, adding … if truncated."""
    if max_width <= 0 or len(text) <= max_width:
        return text
    return text[:max_width - 1] + "…"


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
    display = _STATE_ABBREV.get(base_state, state) if _abbreviate_states else state
    return Text(display, style=style)


def _apply_diff(table: DataTable, new_data: dict[str, tuple], force: bool = False) -> None:
    """Apply a diff to a DataTable, preserving scroll when only cells change."""
    existing_keys: set[str] = set()
    for i in range(table.row_count):
        try:
            row_key, _ = table.coordinate_to_cell_key(
                table.cursor_coordinate._replace(row=i, column=0)
            )
            existing_keys.add(str(row_key.value))
        except Exception:
            break

    new_keys = set(new_data.keys())
    added = new_keys - existing_keys
    removed = existing_keys - new_keys

    # Full rebuild when rows changed or force requested (e.g. display settings changed)
    if added or removed or force:
        old_selected = table.get_selected_job_id()
        table.clear()
        for key, values in new_data.items():
            table.add_row(*values, key=key)
        if old_selected and old_selected in new_keys:
            try:
                idx = table.get_row_index(old_selected)
                table.move_cursor(row=idx)
            except Exception:
                pass
        return

    # No structural changes — just update changed cells in place
    for key, values in new_data.items():
        for col_key, value in zip(table.COLUMNS, values):
            try:
                current = table.get_cell(key, col_key)
                if str(current) != str(value):
                    table.update_cell(key, col_key, value)
            except Exception:
                pass


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
        self._force_next: bool = False

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

    def force_rebuild(self) -> None:
        """Force a full table rebuild (e.g. after display settings change)."""
        self._force_next = True
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

        bookmarked = [j for j in filtered if j.job_id in self._bookmarked]
        rest = [j for j in filtered if j.job_id not in self._bookmarked]

        new_data: dict[str, tuple] = {}
        for job in bookmarked + rest:
            prefix = "★ " if job.job_id in self._bookmarked else ""
            name = _truncate(f"{prefix}{job.name}", _max_name_width)
            state_style = _ACTIVE_STATE_STYLES.get(job.state, "")
            id_text = Text(job.job_id, style=state_style)
            part_text = Text(
                _truncate(job.partition, _max_partition_width),
                style=_partition_style(job.partition),
            )
            new_data[job.job_id] = (id_text, name, job.elapsed, part_text)

        force = self._force_next
        self._force_next = False
        _apply_diff(self, new_data, force=force)

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
        self._force_next: bool = False

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

    def force_rebuild(self) -> None:
        """Force a full table rebuild (e.g. after display settings change)."""
        self._force_next = True
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
            prefix = "★ " if job.job_id in self._bookmarked else ""
            name = _truncate(f"{prefix}{job.name}", _max_name_width)
            state_text = _styled_state(job.state)
            part_text = Text(
                _truncate(job.partition, _max_partition_width),
                style=_partition_style(job.partition),
            )
            new_data[job.job_id] = (job.job_id, name, state_text, part_text, job.elapsed)

        force = self._force_next
        self._force_next = False
        _apply_diff(self, new_data, force=force)

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
