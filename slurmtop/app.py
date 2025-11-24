"""Textual-based implementation of the SlurmTop terminal UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
import os

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - fallback for <3.11
    import tomli as tomllib  # type: ignore[no-redef]

import psutil
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Log, Static, TabPane, TabbedContent

CONFIG_PATH = Path("config/interactive_job.toml")
CSS_FILE = Path(__file__).with_name("styles.tcss")


@dataclass
class Job:
    """Simple container for mock Slurm job metadata."""

    job_id: str
    name: str
    state: str
    user: str
    submitted: str
    runtime: str
    nodes: str
    reason: str

    @property
    def display(self) -> str:
        return f"#{self.job_id} {self.name} ({self.state})"


DEFAULT_CURRENT_JOBS: list[Job] = [
    Job("581939", "deep-learning", "RUNNING", "alice", "2025-11-15 14:22", "02:13:11", "gpu[7-8]", "Scaling to 4 GPUs"),
    Job("581940", "preprocess", "PENDING", "bob", "2025-11-16 09:03", "--", "cpu[1-2]", "Priority hold"),
]

DEFAULT_PAST_JOBS: list[Job] = [
    Job("581901", "render", "COMPLETED", "carol", "2025-11-14 11:01", "00:43:08", "cpu[3-4]", "Finished successfully"),
    Job("581875", "simulation", "FAILED", "dave", "2025-11-13 20:22", "06:10:55", "gpu[3]", "Out of memory"),
]

DEFAULT_INTERACTIVE_CONFIG = {
    "partition": "debug",
    "account": "research",
    "time_limit": "01:00:00",
    "nodes": 1,
    "gpus": 1,
}


class JobListItem(ListItem):
    """List item that retains the underlying Job."""

    def __init__(self, job: Job) -> None:
        super().__init__(Static(job.display))
        self.job = job


class CpuPane(Static):
    """Widget that renders live CPU metrics using psutil."""

    def on_mount(self) -> None:  # pragma: no cover - Textual hook
        self.set_interval(2.0, self.update_metrics)
        self.update_metrics()

    def update_metrics(self) -> None:
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        overall = sum(per_core) / max(len(per_core), 1)
        mem = psutil.virtual_memory()
        load = _safe_load_avg()

        summary = Text(
            f"Overall CPU: {overall:5.1f}%\n"
            f"Memory: {mem.percent:5.1f}% ({_human_bytes(mem.used)} / {_human_bytes(mem.total)})\n"
            f"Load average: {load[0]:.2f} {load[1]:.2f} {load[2]:.2f}",
            style="cyan",
        )

        cores = Table.grid(expand=True)
        cores.add_column(justify="left")
        cores.add_column(justify="right")
        for idx, value in enumerate(per_core):
            bar = _usage_bar(value, width=20)
            cores.add_row(f"CPU{idx:02}", f"{bar} {value:5.1f}%")

        procs = _sample_processes()
        proc_table = Table(title="Top Tasks", expand=True)
        proc_table.add_column("PID", justify="right")
        proc_table.add_column("User")
        proc_table.add_column("CPU%", justify="right")
        proc_table.add_column("MEM%", justify="right")
        proc_table.add_column("Command")

        for proc in procs:
            proc_table.add_row(
                str(proc["pid"]),
                proc["user"],
                f"{proc['cpu']:4.1f}",
                f"{proc['mem']:4.1f}",
                proc["name"],
            )

        self.update(Group(summary, Panel(cores, title="Per-core"), proc_table))


class GpuPane(Static):
    """Placeholder GPU view until real metrics are wired up."""

    MOCK_DEVICES = [
        ("GPU0 A100", "64°C", "92%", "78%", "62 / 80 GB", "python (alice)"),
        ("GPU1 A100", "59°C", "73%", "65%", "51 / 80 GB", "python (bob)"),
        ("GPU2 A100", "35°C", "6%", "12%", "idle", "-"),
    ]

    def on_mount(self) -> None:  # pragma: no cover - Textual hook
        self.render_content()

    def render_content(self) -> None:
        table = Table(title="Devices", expand=True)
        table.add_column("GPU")
        table.add_column("Temp")
        table.add_column("GPU%")
        table.add_column("Mem%")
        table.add_column("VRAM")
        table.add_column("Process")

        for row in self.MOCK_DEVICES:
            table.add_row(*row)

        self.update(table)

class HelpScreen(ModalScreen[None]):
    """Modal overlay showing all keyboard shortcuts."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:  # pragma: no cover - UI composition
        shortcuts = [
            ("Arrow keys", "Navigate Current/Past job lists"),
            ("Tab", "Cycle focus between widgets"),
            ("k", "Connect to selected job"),
            ("n", "Launch interactive job request"),
            ("r", "Refresh CPU metrics"),
            ("q", "Quit SlurmTop"),
            ("?", "Show this shortcuts overlay"),
        ]

        table = Table.grid(padding=(0, 2))
        table.add_column("Shortcut", style="bold cyan")
        table.add_column("Description", style="white")
        for key, description in shortcuts:
            table.add_row(key, description)

        yield Static(Panel(table, title="SlurmTop Shortcuts"), id="help-panel")

    def action_dismiss(self) -> None:
        self.dismiss(None)


class SlurmTopApp(App[None]):
    """Textual application that mirrors the original Rust layout."""

    CSS_PATH = CSS_FILE
    TITLE = "SlurmTop"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("k", "connect_job", "Connect"),
        Binding("n", "launch_interactive", "Interactive"),
        Binding("r", "refresh_metrics", "Refresh Metrics"),
        Binding("?", "show_help", "Help"),
    ]

    selected_job: reactive[Optional[Job]] = reactive(None)

    def __init__(self, *, current_jobs: Optional[Iterable[Job]] = None, past_jobs: Optional[Iterable[Job]] = None) -> None:
        super().__init__()
        self.current_jobs = list(current_jobs or DEFAULT_CURRENT_JOBS)
        self.past_jobs = list(past_jobs or DEFAULT_PAST_JOBS)
        self._focused_list = "current"
        self._interactive_config = load_interactive_config()

    def compose(self) -> ComposeResult:  # pragma: no cover - Textual hook
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="jobs-column"):
                yield Label("Current Jobs", id="current-label")
                yield ListView(*[JobListItem(job) for job in self.current_jobs], id="current-jobs")
                yield Label("Past Jobs", id="past-label")
                yield ListView(*[JobListItem(job) for job in self.past_jobs], id="past-jobs")
            with Vertical(id="actions-column"):
                self.cpu_pane = CpuPane()
                self.gpu_pane = GpuPane()
                self.error_log = Log(highlight=True, name="error-log")
                self.output_log = Log(highlight=True, name="output-log")
                self.script_view = Static(_sample_script(), id="script-view")

                with TabbedContent(id="actions-tabs"):
                    yield TabPane("CPU", self.cpu_pane)
                    yield TabPane("GPU", self.gpu_pane)
                    yield TabPane("Error Log", self.error_log)
                    yield TabPane("Output Log", self.output_log)
                    yield TabPane("Job Script", self.script_view)
                yield Label("Job Inspector", id="inspector-label")
                yield Static("Select a job to inspect", id="inspector")
                yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:  # pragma: no cover - Textual hook
        current_list = self.query_one("#current-jobs", ListView)
        if current_list.children:
            current_list.index = 0
            first = current_list.children[0]
            if isinstance(first, JobListItem):
                self.selected_job = first.job
                self.update_inspector()
        self.error_log.write("stderr tail unavailable (placeholder)")
        self.output_log.write("stdout tail unavailable (placeholder)")

    def watch_selected_job(self, job: Optional[Job]) -> None:
        if job is None:
            self.query_one("#inspector", Static).update("Select a job to inspect")
        else:
            self.update_inspector()

    def update_inspector(self) -> None:
        target = self.query_one("#inspector", Static)
        job = self.selected_job
        if job is None:
            target.update("Select a job to inspect")
            return

        table = Table.grid(expand=True)
        table.add_row(f"JobID: {job.job_id}", f"User: {job.user}")
        table.add_row(f"State: {job.state}", f"Nodes: {job.nodes}")
        table.add_row(f"Submit: {job.submitted}", f"Runtime: {job.runtime}")
        table.add_row(f"Reason: {job.reason}", "")

        target.update(table)

    def set_status(self, message: str) -> None:
        status = self.query_one("#status", Static)
        status.update(Text(message, style="bold green"))

    def action_refresh_metrics(self) -> None:
        self.cpu_pane.update_metrics()
        self.set_status("Metrics refreshed")

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_connect_job(self) -> None:
        job = self.selected_job
        if job is None:
            self.set_status("Select a job before connecting")
            return
        if job.state.upper() not in {"RUNNING", "PENDING"}:
            self.set_status(f"Job #{job.job_id} already {job.state.lower()}; showing details instead")
        else:
            self.set_status(f"Connecting to job #{job.job_id} on {job.nodes} as {job.user}…")

    def action_launch_interactive(self) -> None:
        cfg = self._interactive_config
        self.set_status(
            "Requesting interactive session: "
            f"partition={cfg['partition']} account={cfg['account']} nodes={cfg['nodes']} gpus={cfg['gpus']} time={cfg['time_limit']}"
        )

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, JobListItem):
            self.selected_job = event.item.job
            self._focused_list = "current" if event.list_view.id == "current-jobs" else "past"

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, JobListItem):
            self.selected_job = event.item.job
            self._focused_list = "current" if event.list_view.id == "current-jobs" else "past"


def _sample_processes(limit: int = 5) -> list[dict[str, object]]:
    processes: list[dict[str, object]] = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_percent"]):
        try:
            cpu = proc.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        processes.append(
            {
                "pid": proc.info["pid"],
                "name": proc.info.get("name") or "-",
                "user": (proc.info.get("username") or "-"),
                "cpu": cpu,
                "mem": proc.info.get("memory_percent") or 0.0,
            }
        )
    processes.sort(key=lambda row: row["cpu"], reverse=True)
    return processes[:limit]


def _usage_bar(percent: float, *, width: int = 20) -> str:
    blocks = int((percent / 100.0) * width)
    return "█" * blocks + "░" * (width - blocks)


def _human_bytes(num: float) -> str:
    step = 1024.0
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if abs(num) < step:
            return f"{num:0.1f} {unit}"
        num /= step
    return f"{num:0.1f} PiB"


def _safe_load_avg() -> tuple[float, float, float]:
    try:
        return os.getloadavg()
    except OSError:  # Windows compatibility
        return (0.0, 0.0, 0.0)


def load_interactive_config() -> dict[str, object]:
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
    lines = []
    for key, value in data.items():
        if isinstance(value, str):
            lines.append(f"{key} = \"{value}\"")
        else:
            lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def _sample_script() -> str:
    return """#!/bin/bash
#SBATCH --partition=debug
#SBATCH --nodes=1
#SBATCH --gres=gpu:1
#SBATCH --time=01:00:00

module load cuda
srun python train.py --epochs 40
"""
