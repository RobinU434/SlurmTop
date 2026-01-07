"""CPU pane widget for SlurmTop."""

from __future__ import annotations

import psutil
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from slurmtop.utils import human_bytes, safe_load_avg, sample_processes, usage_bar


class CpuPane(Static):
    """Widget that renders live CPU metrics using psutil."""

    def on_mount(self) -> None:  # pragma: no cover - Textual hook
        self.set_interval(2.0, self.update_metrics)
        self.update_metrics()

    def update_metrics(self) -> None:
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        overall = sum(per_core) / max(len(per_core), 1)
        mem = psutil.virtual_memory()
        load = safe_load_avg()

        summary = Text(
            f"Overall CPU: {overall:5.1f}%\n"
            f"Memory: {mem.percent:5.1f}% ({human_bytes(mem.used)} / {human_bytes(mem.total)})\n"
            f"Load average: {load[0]:.2f} {load[1]:.2f} {load[2]:.2f}",
            style="cyan",
        )

        cores = Table.grid(expand=True)
        cores.add_column(justify="left")
        cores.add_column(justify="right")
        for idx, value in enumerate(per_core):
            bar = usage_bar(value, width=20)
            cores.add_row(f"CPU{idx:02}", f"{bar} {value:5.1f}%")

        procs = sample_processes()
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
