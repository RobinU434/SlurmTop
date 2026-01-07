"""Widget components for SlurmTop."""

from slurmtop.widgets.cpu_pane import CpuPane
from slurmtop.widgets.gpu_pane import GpuPane
from slurmtop.widgets.job_list import JobListItem

__all__ = ["CpuPane", "GpuPane", "JobListItem"]
