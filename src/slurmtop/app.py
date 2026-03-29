"""Main SlurmTop Textual application."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, RichLog, Static

from slurmtop import slurm
from slurmtop.models import Config
from slurmtop.widgets.detail_view import DetailView, parse_mem_bytes
from slurmtop.widgets.job_table import ActiveJobTable, CompletedJobTable, JobSelected, set_partition_colors
from slurmtop.widgets.metadata_view import MetadataView


# ---------------------------------------------------------------------------
# Modal screens
# ---------------------------------------------------------------------------


class HelpScreen(ModalScreen[None]):
    """Overlay showing key bindings."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("question_mark", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    HelpScreen > Vertical {
        width: 72;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "[bold underline]SlurmTop Help[/]\n\n"
                "[bold]Navigation[/]\n"
                "  [bold cyan]Up / Down[/]       Navigate job list (wraps between panels)\n"
                "  [bold cyan]Tab / Shift+Tab[/] Switch right panel focus\n"
                "  [bold cyan]Left / Right[/]    Switch right panel focus\n"
                "  [bold cyan]\\[ / \\][/]          Switch tabs within right panel\n"
                "  [bold cyan]Escape[/]          Close search bar\n\n"
                "[bold]Actions[/]\n"
                "  [bold cyan]/[/]               Search / filter jobs by ID, name, or partition\n"
                "  [bold cyan]m[/]               Bookmark / unbookmark job (★ pinned to top)\n"
                "  [bold cyan]c[/]               Cancel selected job (with confirmation)\n"
                "  [bold cyan]s[/]               Resubmit terminated job (with confirmation)\n"
                "  [bold cyan]o[/]               SSH to job's compute node (suspends TUI)\n"
                "  [bold cyan]r[/]               Force refresh all data\n"
                "  [bold cyan]?[/]               Toggle this help screen\n"
                "  [bold cyan]q[/]               Quit\n\n"
                "[bold]Detail Tabs[/]\n"
                "  [bold cyan]stdout[/]  Job standard output log\n"
                "  [bold cyan]stderr[/]  Job standard error log\n"
                "  [bold cyan]cpu[/]     Live process list from node (auto-refreshes)\n"
                "  [bold cyan]gpu[/]     Live nvidia-smi from node (auto-refreshes)\n"
                "  [bold cyan]stats[/]   CPU, memory, GPU, disk I/O + sparkline history\n\n"
                "[bold]Cluster Bar (top line)[/]\n"
                "  Shows your running/pending job counts and partition status.\n"
                "  Partition format: [bold]name[/]:[bold]A[/]/[bold]I[/]/[bold]O[/]/[bold]T[/]\n"
                "    [bold]A[/]=allocated  [bold]I[/]=idle  [bold]O[/]=other  [bold]T[/]=total nodes\n\n"
                "[bold]Notifications[/]\n"
                "  Terminal bell + desktop notification when a running job completes.\n\n"
                "Press [bold]?[/] or [bold]Escape[/] to close."
            )


class ConfirmCancelScreen(ModalScreen[bool]):
    """Confirm job cancellation."""

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "deny", "No"),
        Binding("escape", "deny", "Cancel"),
    ]

    DEFAULT_CSS = """
    ConfirmCancelScreen {
        align: center middle;
    }
    ConfirmCancelScreen > Vertical {
        width: 50;
        height: auto;
        border: round $error;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(self, job_id: str) -> None:
        super().__init__()
        self.job_id = job_id

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"[bold red]Cancel job {self.job_id}?[/]\n\n"
                "Press [bold]y[/] to confirm, [bold]n[/] or [bold]Escape[/] to abort."
            )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)


class ConfirmResubmitScreen(ModalScreen[bool]):
    """Confirm job resubmission."""

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "deny", "No"),
        Binding("escape", "deny", "Cancel"),
    ]

    DEFAULT_CSS = """
    ConfirmResubmitScreen {
        align: center middle;
    }
    ConfirmResubmitScreen > Vertical {
        width: 60;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(self, job_id: str, script: str) -> None:
        super().__init__()
        self.job_id = job_id
        self.script = script

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"[bold]Resubmit job {self.job_id}?[/]\n\n"
                f"Script: [cyan]{self.script}[/]\n\n"
                "Press [bold]y[/] to confirm, [bold]n[/] or [bold]Escape[/] to abort."
            )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------


_MAX_HISTORY = 60  # max sparkline samples per job


class SlurmTopApp(App):
    """SlurmTop — a TUI for monitoring Slurm jobs."""

    TITLE = "SlurmTop"
    CSS_PATH = "slurmtop.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("question_mark", "help", "Help", show=True, key_display="?"),
        Binding("slash", "toggle_search", "Search", show=True, key_display="/"),
        Binding("m", "toggle_bookmark", "Bookmark", show=True),
        Binding("c", "cancel_job", "Cancel", show=True),
        Binding("s", "resubmit_job", "Resubmit", show=True),
        Binding("o", "ssh_to_node", "SSH", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("tab", "focus_next_right", "Next Panel", show=False),
        Binding("shift+tab", "focus_prev_right", "Prev Panel", show=False),
        Binding("left", "focus_prev_right", show=False),
        Binding("right", "focus_next_right", show=False),
        Binding("left_square_bracket", "prev_tab", "Prev Tab", show=False),
        Binding("right_square_bracket", "next_tab", "Next Tab", show=False),
    ]

    def __init__(self, config: Config | None = None, config_overrides: list[str] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.config = config or Config()
        self._config_overrides = config_overrides or []

    # Track which right panel has focus: "detail" or "metadata"
    _right_focus: str = "detail"
    # Currently selected job
    _selected_job_id: str | None = None
    _selected_source: str = "active"
    # Node of selected job (for live monitoring)
    _selected_node: str = ""
    # Help screen toggle
    _help_open: bool = False
    # Search
    _search_visible: bool = False
    # Bookmarks (session-only)
    _bookmarked_ids: set[str] = set()
    # Job completion tracking
    _known_running_ids: set[str] = set()
    _first_poll_done: bool = False
    # Sparkline resource history: job_id -> {"memory": [...], "cpu": [...]}
    _resource_history: dict[str, dict[str, list[float]]] = {}
    # Resubmit state
    _resubmit_script: str = ""
    _resubmit_work_dir: str = ""

    def compose(self) -> ComposeResult:
        show_gpu = not self.config.no_gpu and not self.config.no_live
        yield Static(id="cluster-bar")
        with Horizontal(id="main-container"):
            with Vertical(id="left-column"):
                yield Input(
                    placeholder="Filter by ID, name, partition...",
                    id="search-input",
                )
                yield ActiveJobTable(id="active-jobs")
                yield CompletedJobTable(id="completed-jobs")
            with Vertical(id="right-column"):
                yield DetailView(id="detail-view", show_gpu=show_gpu)
                yield MetadataView(id="metadata-view")
                yield RichLog(id="command-log", wrap=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        slurm.set_config(self.config)
        set_partition_colors(self.config.partition_colors)

        self.query_one("#active-jobs").border_title = "Active Jobs"
        self.query_one("#completed-jobs").border_title = "Terminated Jobs"
        self.query_one("#detail-view").border_title = "Job Details"
        self.query_one("#metadata-view").border_title = "Job Metadata"
        self.query_one("#command-log").border_title = "Command Log"

        # Hide search input initially
        self.query_one("#search-input").display = False

        if self.config.remote:
            self.title = f"SlurmTop [{self.config.remote}]"

        self.query_one("#active-jobs", ActiveJobTable).focus()

        # Log config overrides
        for override in self._config_overrides:
            self._log("config override", override)

        # Polling
        self.set_interval(self.config.refresh, self._poll_jobs)
        self.call_after_refresh(self._poll_jobs)

        if not self.config.no_live:
            self.set_interval(self.config.refresh, self._refresh_live_monitors)

    # ------------------------------------------------------------------
    # Data polling
    # ------------------------------------------------------------------

    async def _poll_jobs(self) -> None:
        active_table = self.query_one("#active-jobs", ActiveJobTable)
        completed_table = self.query_one("#completed-jobs", CompletedJobTable)

        running, completed, summary = await asyncio.gather(
            slurm.get_running_jobs(self.config),
            slurm.get_completed_jobs(self.config),
            slurm.get_cluster_summary(self.config),
        )
        active_table.update_jobs(running)
        completed_table.update_jobs(completed)

        # Update cluster bar
        self.query_one("#cluster-bar", Static).update(summary)

        # Job completion notifications
        current_ids = {j.job_id for j in running}
        if self._first_poll_done:
            disappeared = self._known_running_ids - current_ids
            for job_id in disappeared:
                final = next(
                    (c.state for c in completed if c.job_id == job_id),
                    "completed",
                )
                self._notify_job_done(job_id, final)
        self._known_running_ids = current_ids
        self._first_poll_done = True

        # Collect sparkline samples for selected running job
        if self._selected_job_id and self._selected_job_id in current_ids:
            await self._collect_resource_sample(self._selected_job_id)

        # Auto-select first job if nothing is selected
        if self._selected_job_id is None:
            if active_table.row_count > 0:
                jid = active_table.get_selected_job_id()
                if jid:
                    self._selected_job_id = jid
                    self._selected_source = "active"
                    await self._load_job_details(jid)
            elif completed_table.row_count > 0:
                jid = completed_table.get_selected_job_id()
                if jid:
                    self._selected_job_id = jid
                    self._selected_source = "completed"
                    await self._load_job_details(jid)

    # ------------------------------------------------------------------
    # Job completion notification
    # ------------------------------------------------------------------

    def _notify_job_done(self, job_id: str, state: str) -> None:
        self._log("job completed", f"{job_id} {state}")
        self.bell()
        # Try desktop notification (non-blocking, silent failure)
        asyncio.create_task(self._try_desktop_notify(
            "SlurmTop", f"Job {job_id} {state}",
        ))

    @staticmethod
    async def _try_desktop_notify(title: str, body: str) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "notify-send", title, body,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Sparkline resource history
    # ------------------------------------------------------------------

    async def _collect_resource_sample(self, job_id: str) -> None:
        stats = await slurm.get_job_stats(job_id)
        if stats is None:
            return
        hist = self._resource_history.setdefault(job_id, {"memory": [], "cpu": []})
        mem = parse_mem_bytes(stats.max_rss)
        if mem is not None:
            hist["memory"].append(mem)
            if len(hist["memory"]) > _MAX_HISTORY:
                hist["memory"] = hist["memory"][-_MAX_HISTORY:]
        # Parse CPU: try total_cpu as seconds-like value, or just count samples
        # For sparklines, use max_rss as primary metric; CPU is harder to normalize
        # Use ave_rss as a proxy for CPU activity (non-zero means active)
        cpu_val = parse_mem_bytes(stats.ave_rss)
        if cpu_val is not None:
            hist["cpu"].append(cpu_val)
            if len(hist["cpu"]) > _MAX_HISTORY:
                hist["cpu"] = hist["cpu"][-_MAX_HISTORY:]

    # ------------------------------------------------------------------
    # Job selection handling
    # ------------------------------------------------------------------

    async def on_job_selected(self, message: JobSelected) -> None:
        self._selected_job_id = message.job_id
        self._selected_source = message.source_table
        await self._load_job_details(message.job_id)

    async def _load_job_details(self, job_id: str) -> None:
        detail_view = self.query_one("#detail-view", DetailView)
        metadata_view = self.query_one("#metadata-view", MetadataView)

        detail = await slurm.get_job_detail(job_id)
        if detail is None:
            detail_view.clear_all()
            metadata_view.load_detail(None)
            self._selected_node = ""
            return

        self._selected_node = detail.node_list

        tasks = [
            slurm.read_log_file(detail.stdout_path),
            slurm.read_log_file(detail.stderr_path),
            slurm.get_job_stats(job_id),
        ]
        if not self.config.no_live:
            tasks.append(slurm.get_node_processes(self._selected_node, self.config.user))
            if not self.config.no_gpu:
                tasks.append(slurm.get_gpu_status(self._selected_node, self._selected_job_id or ""))

        results = await asyncio.gather(*tasks)

        detail_view.load_stdout(results[0])
        detail_view.load_stderr(results[1])
        # Pass sparkline history to stats
        history = self._resource_history.get(job_id)
        detail_view.load_stats(results[2], history=history)
        if not self.config.no_live:
            detail_view.load_cpu(results[3])
            if not self.config.no_gpu and len(results) > 4:
                detail_view.load_gpu(results[4])
        metadata_view.load_detail(detail)

    # ------------------------------------------------------------------
    # Live CPU/GPU auto-refresh
    # ------------------------------------------------------------------

    async def _refresh_live_monitors(self) -> None:
        if not self._selected_node or self._selected_node in ("N/A", "None", "(null)"):
            return

        detail_view = self.query_one("#detail-view", DetailView)
        tabs = detail_view.query_one("#detail-tabs")
        active_tab = tabs.active

        if active_tab == "tab-cpu":
            content = await slurm.get_node_processes(self._selected_node, self.config.user)
            detail_view.load_cpu(content)
        elif active_tab == "tab-gpu" and not self.config.no_gpu:
            content = await slurm.get_gpu_status(self._selected_node, self._selected_job_id or "")
            detail_view.load_gpu(content)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def on_key(self, event) -> None:
        # If search input is focused, Escape closes it
        search = self.query_one("#search-input", Input)
        if search.has_focus and event.key == "escape":
            event.prevent_default()
            search.value = ""
            search.display = False
            self._search_visible = False
            self._apply_filter("")
            self.query_one("#active-jobs", ActiveJobTable).focus()
            return

        active = self.query_one("#active-jobs", ActiveJobTable)
        completed = self.query_one("#completed-jobs", CompletedJobTable)

        if event.key == "down" and active.has_focus:
            if active.row_count > 0 and active.cursor_coordinate.row >= active.row_count - 1:
                if completed.row_count > 0:
                    event.prevent_default()
                    completed.focus()
                    completed.move_cursor(row=0)
        elif event.key == "up" and completed.has_focus:
            if completed.cursor_coordinate.row <= 0:
                if active.row_count > 0:
                    event.prevent_default()
                    active.focus()
                    active.move_cursor(row=active.row_count - 1)
        elif event.key == "down" and completed.has_focus:
            if completed.row_count > 0 and completed.cursor_coordinate.row >= completed.row_count - 1:
                if active.row_count > 0:
                    event.prevent_default()
                    active.focus()
                    active.move_cursor(row=0)
        elif event.key == "up" and active.has_focus:
            if active.cursor_coordinate.row <= 0:
                if completed.row_count > 0:
                    event.prevent_default()
                    completed.focus()
                    completed.move_cursor(row=completed.row_count - 1)

    # ------------------------------------------------------------------
    # Search / filter
    # ------------------------------------------------------------------

    def action_toggle_search(self) -> None:
        search = self.query_one("#search-input", Input)
        if self._search_visible:
            search.value = ""
            search.display = False
            self._search_visible = False
            self._apply_filter("")
            self.query_one("#active-jobs", ActiveJobTable).focus()
        else:
            search.display = True
            self._search_visible = True
            search.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self._apply_filter(event.value)

    def _apply_filter(self, text: str) -> None:
        self.query_one("#active-jobs", ActiveJobTable).apply_filter(text)
        self.query_one("#completed-jobs", CompletedJobTable).apply_filter(text)

    # ------------------------------------------------------------------
    # Bookmarks
    # ------------------------------------------------------------------

    def action_toggle_bookmark(self) -> None:
        if self._selected_job_id is None:
            return
        if self._selected_job_id in self._bookmarked_ids:
            self._bookmarked_ids.discard(self._selected_job_id)
        else:
            self._bookmarked_ids.add(self._selected_job_id)
        self.query_one("#active-jobs", ActiveJobTable).set_bookmarks(self._bookmarked_ids)
        self.query_one("#completed-jobs", CompletedJobTable).set_bookmarks(self._bookmarked_ids)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_help(self) -> None:
        if self._help_open:
            self.app.pop_screen()
            self._help_open = False
        else:
            self._help_open = True
            self.push_screen(HelpScreen(), callback=self._on_help_dismissed)

    def _on_help_dismissed(self, _result: None) -> None:
        self._help_open = False

    def action_cancel_job(self) -> None:
        if self._selected_job_id is None:
            self._set_status("No job selected")
            return
        self.push_screen(
            ConfirmCancelScreen(self._selected_job_id),
            callback=self._on_cancel_confirmed,
        )

    async def _on_cancel_confirmed(self, confirmed: bool | None) -> None:
        if not confirmed or self._selected_job_id is None:
            return
        self._log(f"scancel {self._selected_job_id}")
        success, msg = await slurm.cancel_job(self._selected_job_id)
        self._log("cancel", msg)
        if success:
            await self._poll_jobs()

    async def action_resubmit_job(self) -> None:
        if self._selected_job_id is None:
            self._set_status("No job selected")
            return
        if self._selected_source != "completed":
            self._set_status("Resubmit is only available for terminated jobs")
            return

        detail = await slurm.get_job_detail(self._selected_job_id)
        if detail is None:
            self._set_status("Cannot get job details")
            return

        script = detail.submit_line
        if not script or script == "N/A":
            self._set_status("Cannot determine submit script for this job")
            return

        self._resubmit_script = script
        self._resubmit_work_dir = detail.work_dir
        self.push_screen(
            ConfirmResubmitScreen(self._selected_job_id, script),
            callback=self._on_resubmit_confirmed,
        )

    async def _on_resubmit_confirmed(self, confirmed: bool | None) -> None:
        if not confirmed:
            return
        self._log(f"sbatch {self._resubmit_script}")
        success, msg = await slurm.resubmit_job(
            self._resubmit_script, self._resubmit_work_dir,
        )
        self._log("resubmit", msg)
        if success:
            await self._poll_jobs()

    async def action_ssh_to_node(self) -> None:
        if not self._selected_node or self._selected_node in ("N/A", "None", "(null)", ""):
            self._log("ssh", "no node assigned to this job")
            return

        node = slurm._first_node(self._selected_node)
        cmd_parts = ["ssh"]
        if self.config.remote:
            cmd_parts.extend(["-J", self.config.remote])
        cmd_parts.append(node)
        cmd_str = " ".join(cmd_parts)

        self._log(f"ssh {node}")
        with self.suspend():
            # Clear terminal and show greeting
            os.system("clear")
            job_info = f" (job {self._selected_job_id})" if self._selected_job_id else ""
            via = f" via {self.config.remote}" if self.config.remote else ""
            print(f"SlurmTop — connecting to {node}{via}{job_info}")
            print(f"Type 'exit' to return to SlurmTop.\n")
            os.system(cmd_str)
        self._log("ssh", f"session to {node} closed")

    async def action_refresh(self) -> None:
        self._log("refresh")
        await self._poll_jobs()
        if self._selected_job_id:
            await self._load_job_details(self._selected_job_id)
        self._log("refresh", "complete")

    def _log(self, action: str, result: str = "") -> None:
        """Write a timestamped entry to the command log panel."""
        log = self.query_one("#command-log", RichLog)
        ts = datetime.now().strftime("%H:%M:%S")
        log.write(f"[dim]{ts}[/] {action}")
        if result:
            log.write(f"  [dim]>>> {result}[/]")

    def _set_status(self, text: str) -> None:
        """Log a message to the command log panel."""
        if text:
            self._log(text)
        # Also update the footer subtitle for one-line visibility


    def action_focus_next_right(self) -> None:
        detail = self.query_one("#detail-view", DetailView)
        metadata = self.query_one("#metadata-view", MetadataView)
        if self._right_focus == "detail":
            self._right_focus = "metadata"
            metadata.query_one("TabbedContent").focus()
        else:
            self._right_focus = "detail"
            detail.query_one("TabbedContent").focus()

    def action_focus_prev_right(self) -> None:
        self.action_focus_next_right()

    def action_next_tab(self) -> None:
        if self._right_focus == "detail":
            self.query_one("#detail-view", DetailView).switch_tab(1)
        else:
            self.query_one("#metadata-view", MetadataView).switch_tab(1)

    def action_prev_tab(self) -> None:
        if self._right_focus == "detail":
            self.query_one("#detail-view", DetailView).switch_tab(-1)
        else:
            self.query_one("#metadata-view", MetadataView).switch_tab(-1)
