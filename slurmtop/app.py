"""Textual-based implementation of the SlurmTop terminal UI."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Label,
    ListView,
    Log,
    Static,
    TabPane,
    TabbedContent,
)

from slurmtop.config import load_interactive_config
from slurmtop.job_manager import JobListManager
from slurmtop.models import Job
from slurmtop.screens import HelpScreen
from slurmtop.utils import sample_script
from slurmtop.widgets import CpuPane, GpuPane, JobListItem

CSS_FILE = Path(__file__).with_name("styles.tcss")


class SlurmTopApp(App[None]):
    """Textual application that mirrors the original Rust layout."""

    CSS_PATH = CSS_FILE
    TITLE = "SlurmTop"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("?", "show_help", "Help"),
        Binding("k", "connect_job", "Connect", show=False),
        Binding("h", "hook_job", "Hook Job", show=False),
        Binding("n", "launch_interactive", "Interactive", show=False),
        Binding("r", "refresh_metrics", "Refresh", show=False),
        Binding("1", "show_cpu_tab", "CPU", show=False),
        Binding("2", "show_gpu_tab", "GPU", show=False),
        Binding("3", "show_error_tab", "Error Log", show=False),
        Binding("4", "show_output_tab", "Output Log", show=False),
        Binding("5", "show_script_tab", "Job Script", show=False),
        Binding("[", "prev_tab", "Previous Tab", show=False),
        Binding("]", "next_tab", "Next Tab", show=False),
        Binding("tab", "cycle_job_lists", "Switch List", priority=True, show=False),
        Binding(
            "shift+tab", "cycle_job_lists", "Switch List", priority=True, show=False
        ),
    ]

    selected_job: reactive[Optional[Job]] = reactive(None)

    def __init__(
        self,
        *,
        use_slurm: bool = True,
        filter_user: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._interactive_config = load_interactive_config()

        # Initialize job list manager with configurable refresh interval
        refresh_interval = self._interactive_config.get("job_refresh_interval", 1.0)
        self.job_manager = JobListManager(
            use_slurm=use_slurm,
            refresh_interval=float(refresh_interval),
            filter_user=filter_user,
        )

        # Set up callbacks
        self.job_manager.set_on_jobs_updated(self._on_jobs_updated)
        self.job_manager.set_on_selection_changed(self._on_selection_changed)

        self._focused_list = "current"
        self.tabs: TabbedContent | None = None
        self.tab_title: Label | None = None
        self.current_list: ListView | None = None
        self.past_list: ListView | None = None
        self._tab_titles = {
            "tab-cpu": "CPU",
            "tab-gpu": "GPU",
            "tab-error": "Err.Log",
            "tab-output": "Out.Log",
            "tab-script": "Script",
        }
        self._tab_order = ["tab-cpu", "tab-gpu", "tab-error", "tab-output", "tab-script"]

        # variable allocations for widgets
        self.cpu_pane: CpuPane | None = None
        self.gpu_pane: GpuPane | None = None
        self.error_log: Log | None = None
        self.output_log: Log | None = None
        self.script_view: Static | None = None
        self.command_log: Log | None = None

    def compose(self) -> ComposeResult:  # pragma: no cover - Textual hook
        # yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="jobs-column"):
                self.current_list = ListView(
                    *[JobListItem(job) for job in self.job_manager.get_current_jobs()],
                    id="current-jobs",
                )
                self.current_list.border_title = "Current Jobs"
                self.current_list.can_focus = True
                yield self.current_list

                self.past_list = ListView(
                    *[JobListItem(job) for job in self.job_manager.get_past_jobs()],
                    id="past-jobs",
                )
                self.past_list.border_title = "Past Jobs"
                self.past_list.can_focus = True
                yield self.past_list

            with Vertical(id="actions-column"):
                with Vertical(id="inspector-panel"):
                    yield Static("Select a job to inspect", id="inspector")
                    
                self.cpu_pane = CpuPane()
                self.gpu_pane = GpuPane()
                self.error_log = Log(highlight=True, name="error-log")
                self.output_log = Log(highlight=True, name="output-log")
                self.script_view = Static(sample_script(), id="script-view")
                # self.tab_title = Label(self._tab_titles["tab-cpu"], id="tab-title")
                # yield self.tab_title
                self.tabs = TabbedContent(id="actions-tabs", initial="tab-cpu")
                self.tabs.border_title = self._format_tab_title("tab-cpu")
                with self.tabs:
                    yield TabPane("CPU", self.cpu_pane, id="tab-cpu")
                    yield TabPane("GPU", self.gpu_pane, id="tab-gpu")
                    yield TabPane("Error Log", self.error_log, id="tab-error")
                    yield TabPane("Output Log", self.output_log, id="tab-output")
                    yield TabPane("Job Script", self.script_view, id="tab-script")
                self.command_log = Log(highlight=True, name="command-log", id="command-log")
                self.command_log.border_title = "Command Log"
                yield self.command_log
        
        yield Footer()

    def on_mount(self) -> None:  # pragma: no cover - Textual hook
        """Initialize the application when mounted."""
        current_list = self.current_list or self.query_one("#current-jobs", ListView)
        if current_list.children:
            current_list.index = 0
            first = current_list.children[0]
            if isinstance(first, JobListItem):
                self.selected_job = first.job
                self.job_manager.select_job(first.job)
                self.update_inspector()
        self._focus_job_list("current")
        self.error_log.write("stderr tail unavailable (placeholder)")
        self.output_log.write("stdout tail unavailable (placeholder)")
        
        # Initialize command log
        self.set_status("SlurmTop started")
        self.set_status(f"Auto-refresh enabled: every {self.job_manager.refresh_interval:.0f}s")

        # Set up automatic refresh with configurable interval
        self.set_interval(self.job_manager.refresh_interval, self._auto_refresh_jobs)
        # Set up periodic inspector updates to refresh runtime and other dynamic fields
        self.set_interval(0.5, self.update_inspector)
        # Set up periodic GPU info refresh for running jobs
        self.set_interval(0.5, self._refresh_gpu_info)

    def watch_selected_job(self, job: Optional[Job]) -> None:
        """React to changes in selected job."""
        if job is None:
            self.query_one("#inspector", Static).update("Select a job to inspect")
            if self.gpu_pane:
                self.gpu_pane.update_gpu_info(None)
        else:
            self.update_inspector()
            if self.gpu_pane and job.state.upper() == "RUNNING":
                self.gpu_pane.update_gpu_info(job.job_id)

    def update_inspector(self) -> None:
        """Update the job inspector with current job information."""
        target = self.query_one("#inspector", Static)
        job = self.selected_job
        if job is None:
            target.update("Select a job to inspect")
            return

        # Get the latest job data from JobListManager
        current_jobs = self.job_manager.get_current_jobs()
        past_jobs = self.job_manager.get_past_jobs()
        
        # Find the updated job data
        updated_job = None
        for j in current_jobs + past_jobs:
            if j.job_id == job.job_id:
                updated_job = j
                break
        
        # Use updated job if found, otherwise fall back to cached job
        display_job = updated_job if updated_job else job

        table = Table.grid(expand=True)
        table.add_row(f"JobID: {display_job.job_id}", f"User: {display_job.user}")
        table.add_row(f"State: {display_job.state}", f"Nodes: {display_job.nodes}")
        table.add_row(f"Submit: {display_job.submitted}", f"Runtime: {display_job.runtime}")
        table.add_row(f"Reason: {display_job.reason}", "")

        target.update(table)

    def set_status(self, message: str) -> None:
        """Log a command/status message to the command log."""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        if self.command_log:
            self.command_log.write(f"[{timestamp}] {message}\n")

    def action_refresh_metrics(self) -> None:
        self.cpu_pane.update_metrics()
        self.job_manager.refresh()
        self.set_status("Metrics and job lists refreshed")

    def _auto_refresh_jobs(self) -> None:
        """Automatically refresh job lists in the background."""
        self.job_manager.refresh()

    def _refresh_gpu_info(self) -> None:
        """Periodically refresh GPU information for the selected running job."""
        if self.gpu_pane and self.selected_job:
            if self.selected_job.state.upper() == "RUNNING":
                self.gpu_pane.update_gpu_info(self.selected_job.job_id)

    def _on_jobs_updated(self, current_jobs: list[Job], past_jobs: list[Job]) -> None:
        """Callback when job lists are updated."""
        # Update current jobs list
        if self.current_list:
            selected = self.job_manager.update_list_view(
                self.current_list,
                current_jobs,
                preserve_selection=True,
            )
            # Only update selection if a valid job was returned and it's different
            if selected and (not self.selected_job or selected.job_id != self.selected_job.job_id):
                self.selected_job = selected
                self.job_manager.select_job(selected)

        # Update past jobs list
        if self.past_list:
            self.job_manager.update_list_view(
                self.past_list,
                past_jobs,
                preserve_selection=False,
            )

    def _on_selection_changed(self, job: Optional[Job]) -> None:
        """Callback when job selection changes."""
        self.selected_job = job
        # Update GPU widget if job is running
        if self.gpu_pane:
            if job and job.state.upper() == "RUNNING":
                self.gpu_pane.update_gpu_info(job.job_id)
            else:
                self.gpu_pane.update_gpu_info(None)

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen(self._shortcut_entries()))

    def action_cycle_job_lists(self) -> None:
        target = "past" if self._focused_list == "current" else "current"
        self._focused_list = target
        self._focus_job_list(target)

    def action_show_cpu_tab(self) -> None:
        self._show_tab("tab-cpu")

    def action_show_gpu_tab(self) -> None:
        self._show_tab("tab-gpu")

    def action_show_error_tab(self) -> None:
        self._show_tab("tab-error")

    def action_show_output_tab(self) -> None:
        self._show_tab("tab-output")

    def action_show_script_tab(self) -> None:
        self._show_tab("tab-script")
    
    def action_prev_tab(self) -> None:
        """Switch to the previous tab."""
        if self.tabs is None:
            return
        current = self.tabs.active
        if current in self._tab_order:
            current_idx = self._tab_order.index(current)
            prev_idx = (current_idx - 1) % len(self._tab_order)
            self._show_tab(self._tab_order[prev_idx])
    
    def action_next_tab(self) -> None:
        """Switch to the next tab."""
        if self.tabs is None:
            return
        current = self.tabs.active
        if current in self._tab_order:
            current_idx = self._tab_order.index(current)
            next_idx = (current_idx + 1) % len(self._tab_order)
            self._show_tab(self._tab_order[next_idx])

    def action_connect_job(self) -> None:
        job = self.selected_job
        if job is None:
            self.set_status("Select a job before connecting")
            return
        if job.state.upper() not in {"RUNNING", "PENDING"}:
            self.set_status(
                f"Job #{job.job_id} already {job.state.lower()}; showing details instead"
            )
        else:
            self.set_status(
                f"Connecting to job #{job.job_id} on {job.nodes} as {job.user}…"
            )

    def action_hook_job(self) -> None:
        job = self.selected_job
        if job is None:
            self.set_status("Select a job before hooking into it")
            return
        if job.state.upper() != "RUNNING":
            self.set_status(
                f"Job #{job.job_id} is {job.state.lower()}; wait until it is running"
            )
            return
        command = f"srun --jobid {job.job_id} --pty bash"
        self.output_log.write(f"$ {command}")
        self.set_status(f"Hooking into job #{job.job_id}: {command}")

    def action_launch_interactive(self) -> None:
        cfg = self._interactive_config
        self.set_status(
            "Requesting interactive session: "
            f"partition={cfg['partition']} account={cfg['account']} nodes={cfg['nodes']} gpus={cfg['gpus']} time={cfg['time_limit']}"
        )

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, JobListItem):
            self.selected_job = event.item.job
            self.job_manager.select_job(event.item.job)
            self._focused_list = (
                "current" if event.list_view.id == "current-jobs" else "past"
            )
            self._focus_job_list(self._focused_list)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, JobListItem):
            self.selected_job = event.item.job
            self.job_manager.select_job(event.item.job)
            self._focused_list = (
                "current" if event.list_view.id == "current-jobs" else "past"
            )
            self._focus_job_list(self._focused_list)

    def _show_tab(self, tab_id: str) -> None:
        if self.tabs is not None:
            self.tabs.active = tab_id
            self._update_tab_header(tab_id)

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        if event.tabbed_content is self.tabs and event.pane.id:
            self._update_tab_header(event.pane.id)

    def _focus_job_list(self, target: Optional[str] = None) -> None:
        target = target or self._focused_list
        if target not in {"current", "past"}:
            return
        list_view = self.current_list if target == "current" else self.past_list
        if list_view is None or not list_view.children:
            return
        if list_view.index is None:
            list_view.index = 0
        self.set_focus(list_view)

    def _format_tab_title(self, active_tab_id: str, marker: str = "<>") -> str:
        """Format tab title showing all tabs with current one highlighted."""
        assert len(marker) == 2, "Marker must be two characters"
        parts = []
        for tab_id in self._tab_order:
            name = self._tab_titles.get(tab_id, tab_id)
            if tab_id == active_tab_id:
                parts.append(f"{marker[0]}{name}{marker[1]}")
            else:
                parts.append(name)
        return " - ".join(parts)
    
    def _update_tab_header(self, tab_id: str) -> None:
        if self.tabs is None:
            return
        self.tabs.border_title = self._format_tab_title(tab_id)
        
    def _shortcut_entries(self) -> list[tuple[str, str]]:
        refresh_info = f"Auto-refreshes every {self.job_manager.refresh_interval:.0f}s"
        return [
            ("Arrow keys", "Navigate Current/Past job lists"),
            ("Tab", "Toggle between Current and Past job lists"),
            ("1", "Show CPU metrics"),
            ("2", "Show GPU metrics"),
            ("3", "Show error log"),
            ("4", "Show output log"),
            ("5", "Show job script"),
            ("[", "Previous tab panel"),
            ("]", "Next tab panel"),
            ("k", "Connect to selected job"),
            ("h", "Hook into selected job via srun"),
            ("n", "Launch interactive job request"),
            ("r", f"Refresh metrics and job lists ({refresh_info})"),
            ("?", "Show shortcuts overlay"),
            ("q", "Quit SlurmTop"),
        ]
