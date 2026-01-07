"""GPU pane widget for SlurmTop."""

from __future__ import annotations

from typing import Optional

from rich.table import Table
from textual.widgets import Static

from slurmtop.models import GpuInfo, fetch_job_gpu_info


class GpuPane(Static):
    """GPU view that displays GPU information from SLURM jobs."""

    MOCK_DEVICES = [
        ("GPU0 A100", "64°C", "92%", "78%", "62 / 80 GB", "python (alice)"),
        ("GPU1 A100", "59°C", "73%", "65%", "51 / 80 GB", "python (bob)"),
        ("GPU2 A100", "35°C", "6%", "12%", "idle", "-"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.current_job_id: Optional[str] = None
        self.gpu_data: list[GpuInfo] = []
        self.use_mock_data = True

    def on_mount(self) -> None:  # pragma: no cover - Textual hook
        self.render_content()

    def update_gpu_info(self, job_id: Optional[str] = None) -> None:
        """
        Update GPU information for the given job ID.
        
        Args:
            job_id: SLURM job ID to fetch GPU info for. If None, shows message.
        """
        self.current_job_id = job_id
        
        if job_id is None:
            self.gpu_data = []
            self.use_mock_data = True
        else:
            # Fetch GPU data from SLURM
            self.gpu_data = fetch_job_gpu_info(job_id, timeout=0.2)
            self.use_mock_data = len(self.gpu_data) == 0
        
        self.render_content()

    def render_content(self) -> None:
        """Render the GPU table with current data."""
        table = Table(title="Devices", expand=True)
        table.add_column("GPU")
        table.add_column("Temp")
        table.add_column("GPU%")
        table.add_column("Mem%")
        table.add_column("VRAM")
        table.add_column("Process")

        if self.use_mock_data:
            # Show mock data or message
            if self.current_job_id is None:
                table.add_row("No job selected", "-", "-", "-", "-", "-")
            else:
                table.add_row(
                    f"No GPU data for job {self.current_job_id}",
                    "-", "-", "-", "-",
                    "Job may not have GPUs allocated"
                )
        else:
            # Display real GPU data
            for gpu in self.gpu_data:
                table.add_row(
                    gpu.name,
                    gpu.temperature,
                    gpu.gpu_utilization,
                    gpu.memory_utilization,
                    gpu.memory_display,
                    gpu.process,
                )

        self.update(table)
