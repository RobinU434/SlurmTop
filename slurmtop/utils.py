"""Utility functions for SlurmTop."""

from __future__ import annotations

import os

import psutil


def sample_processes(limit: int = 5) -> list[dict[str, object]]:
    """Get top processes by CPU usage."""
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


def usage_bar(percent: float, *, width: int = 20) -> str:
    """Create a text-based usage bar."""
    blocks = int((percent / 100.0) * width)
    return "█" * blocks + "░" * (width - blocks)


def human_bytes(num: float) -> str:
    """Format bytes in human-readable format."""
    step = 1024.0
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if abs(num) < step:
            return f"{num:0.1f} {unit}"
        num /= step
    return f"{num:0.1f} PiB"


def safe_load_avg() -> tuple[float, float, float]:
    """Get system load average, with Windows compatibility."""
    try:
        return os.getloadavg()
    except OSError:  # Windows compatibility
        return (0.0, 0.0, 0.0)


def sample_script() -> str:
    """Return a sample job script for display."""
    return """#!/bin/bash
#SBATCH --partition=debug
#SBATCH --nodes=1
#SBATCH --gres=gpu:1
#SBATCH --time=01:00:00

module load cuda
srun python train.py --epochs 40
"""
