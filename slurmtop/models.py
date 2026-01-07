"""Data models for SlurmTop."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


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


def fetch_current_jobs(user: Optional[str] = None) -> list[Job]:
    """
    Fetch current (RUNNING, PENDING, etc.) jobs from SLURM using squeue.
    
    Args:
        user: Optional username to filter jobs. If None, fetches all jobs.
    
    Returns:
        List of Job objects representing current jobs.
    """
    try:
        # squeue format: JobID, Name, State, User, SubmitTime, TimeUsed, NodeList, Reason
        cmd = [
            "squeue",
            "--noheader",
            "--format=%i|%j|%T|%u|%V|%M|%N|%r",
        ]
        
        if user:
            cmd.extend(["--user", user])
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        
        if result.returncode != 0:
            return DEFAULT_CURRENT_JOBS.copy()
        
        jobs = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            
            parts = line.split("|")
            if len(parts) != 8:
                continue
            
            job_id, name, state, job_user, submitted, runtime, nodes, reason = parts
            
            # Clean up values
            submitted = submitted.strip() if submitted.strip() else datetime.now().strftime("%Y-%m-%d %H:%M")
            runtime = runtime.strip() if runtime.strip() else "--"
            nodes = nodes.strip() if nodes.strip() else "N/A"
            reason = reason.strip() if reason.strip() else "None"
            
            jobs.append(Job(
                job_id=job_id.strip(),
                name=name.strip(),
                state=state.strip(),
                user=job_user.strip(),
                submitted=submitted,
                runtime=runtime,
                nodes=nodes,
                reason=reason,
            ))
        
        return jobs if jobs else DEFAULT_CURRENT_JOBS.copy()
    
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError, OSError):
        # Fallback to default if SLURM is not available or command fails
        return DEFAULT_CURRENT_JOBS.copy()


def fetch_past_jobs(user: Optional[str] = None, limit: int = 50) -> list[Job]:
    """
    Fetch completed/failed jobs from SLURM using sacct.
    
    Args:
        user: Optional username to filter jobs. If None, fetches all jobs.
        limit: Maximum number of past jobs to fetch.
    
    Returns:
        List of Job objects representing past jobs.
    """
    try:
        # sacct format: JobID, JobName, State, User, Submit, Elapsed, NodeList, Reason
        cmd = [
            "sacct",
            "--noheader",
            "--format=JobID,JobName,State,User,Submit,Elapsed,NodeList,Reason",
            "--parsable2",
            "--allocations",  # Only show main job allocations, not steps
            f"--starttime={_get_past_days_timestamp(7)}",  # Last 7 days
        ]
        
        if user:
            cmd.extend(["--user", user])
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        
        if result.returncode != 0:
            return DEFAULT_PAST_JOBS.copy()
        
        jobs = []
        seen_ids = set()
        
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            
            parts = line.split("|")
            if len(parts) != 8:
                continue
            
            job_id, name, state, job_user, submitted, runtime, nodes, reason = parts
            
            # Skip job steps (those with dots like 123.0, 123.batch)
            if "." in job_id:
                continue
            
            # Skip if we've seen this job ID
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            
            # Only include completed/failed/cancelled jobs
            state_upper = state.strip().upper()
            if state_upper not in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL"}:
                continue
            
            # Clean up values
            submitted = submitted.strip() if submitted.strip() else "Unknown"
            runtime = runtime.strip() if runtime.strip() else "--"
            nodes = nodes.strip() if nodes.strip() else "N/A"
            reason = reason.strip() if reason.strip() else "None"
            
            jobs.append(Job(
                job_id=job_id.strip(),
                name=name.strip(),
                state=state.strip(),
                user=job_user.strip(),
                submitted=submitted,
                runtime=runtime,
                nodes=nodes,
                reason=reason,
            ))
            
            if len(jobs) >= limit:
                break
        
        return jobs if jobs else DEFAULT_PAST_JOBS.copy()
    
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError, OSError):
        # Fallback to default if SLURM is not available or command fails
        return DEFAULT_PAST_JOBS.copy()


def _get_past_days_timestamp(days: int = 7) -> str:
    """Get timestamp for N days ago in SLURM format."""
    from datetime import timedelta
    past = datetime.now() - timedelta(days=days)
    return past.strftime("%Y-%m-%dT%H:%M:%S")


@dataclass
class GpuInfo:
    """Container for GPU information from nvidia-smi."""
    
    name: str
    temperature: str
    gpu_utilization: str
    memory_utilization: str
    memory_used: str
    memory_total: str
    process: str = "-"
    
    @property
    def memory_display(self) -> str:
        """Format memory for display."""
        if self.memory_used == "N/A" or self.memory_total == "N/A":
            return "N/A"
        return f"{self.memory_used} / {self.memory_total}"


def fetch_job_gpu_info(job_id: str, timeout: float = 10.0) -> list[GpuInfo]:
    """
    Fetch GPU information for a specific SLURM job using srun and nvidia-smi.
    
    Args:
        job_id: The SLURM job ID to query
        timeout: Command timeout in seconds (default 10.0)
    
    Returns:
        List of GpuInfo objects, empty list if command fails
    """
    try:
        # Use srun to execute nvidia-smi on the job's allocated nodes
        # Format: gpu_name, temperature, gpu_util, mem_util, mem_used, mem_total
        cmd = [
            "srun",
            f"--jobid={job_id}",
            "--pty",
            "nvidia-smi",
            "--query-gpu=name,temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        
        if result.returncode != 0:
            return []
        
        gpus = []
        for idx, line in enumerate(result.stdout.strip().split("\n")):
            line = line.strip()
            if not line:
                continue
            
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            
            name, temp, gpu_util, mem_util, mem_used, mem_total = parts[:6]
            
            gpus.append(GpuInfo(
                name=f"GPU{idx} {name}",
                temperature=f"{temp}°C",
                gpu_utilization=f"{gpu_util}%",
                memory_utilization=f"{mem_util}%",
                memory_used=f"{mem_used} MiB",
                memory_total=f"{mem_total} MiB",
                process="-",  # Process info requires additional parsing
            ))
        
        return gpus
    
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError, OSError):
        return []

