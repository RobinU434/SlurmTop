"""Async wrappers around Slurm CLI commands."""

from __future__ import annotations

import asyncio
import os
import shlex
from collections import deque
from datetime import datetime, timedelta

from slurmtop.models import CompletedJob, Config, JobDetail, JobStats, RunningJob

USER = os.environ.get("USER", os.environ.get("LOGNAME", ""))

# Module-level config, set once from app.py via set_config().
_config: Config = Config()

_SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=3",
    "-o", "BatchMode=yes",
]
_SSH_TIMEOUT = 8  # seconds


def set_config(config: Config) -> None:
    """Set the module-level config (called once at app startup)."""
    global _config
    _config = config


# ---------------------------------------------------------------------------
# Transport layer
# ---------------------------------------------------------------------------


async def _run_cmd(*args: str) -> tuple[str, str, int]:
    """Run a command locally or via SSH if remote mode is active."""
    if _config.remote:
        # Tunnel through SSH: join args into a single shell command
        remote_cmd = " ".join(shlex.quote(a) for a in args)
        return await _run_ssh(_config.remote, remote_cmd)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
        proc.returncode or 0,
    )


async def _run_ssh(host: str, remote_cmd: str) -> tuple[str, str, int]:
    """Run a command on a remote host via SSH."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", *_SSH_OPTS, host, remote_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_SSH_TIMEOUT,
        )
        return (
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            proc.returncode or 0,
        )
    except asyncio.TimeoutError:
        return "", "SSH timeout", 1
    except OSError as e:
        return "", str(e), 1


async def _ssh_cmd(node: str, remote_cmd: str) -> tuple[str, int]:
    """Run a command on a compute node.

    In local mode: SSH directly to the node.
    In remote mode: SSH via ProxyJump through the login node.
    """
    try:
        cmd = ["ssh", *_SSH_OPTS]
        if _config.remote:
            cmd.extend(["-J", _config.remote])
        cmd.extend([node, remote_cmd])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_SSH_TIMEOUT,
        )
        return stdout.decode(errors="replace"), proc.returncode or 0
    except (asyncio.TimeoutError, OSError):
        return "", 1


# ---------------------------------------------------------------------------
# squeue – running / pending jobs
# ---------------------------------------------------------------------------

_SQUEUE_FORMAT = "%i|%j|%M|%P|%T|%l|%D|%C|%m|%b|%Z"


async def get_running_jobs(config: Config | None = None) -> list[RunningJob]:
    """Fetch current jobs for the user via squeue, sorted by job ID descending."""
    cfg = config or _config
    user = cfg.user or USER
    cmd: list[str] = [
        "squeue", "-u", user,
        f"--format={_SQUEUE_FORMAT}",
        "--noheader",
        "--sort=-i",
    ]
    if cfg.partition:
        cmd.extend(["-p", cfg.partition])

    stdout, _, rc = await _run_cmd(*cmd)
    if rc != 0 or not stdout.strip():
        return []

    jobs: list[RunningJob] = []
    for line in stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 11:
            continue
        jobs.append(RunningJob(
            job_id=parts[0].strip(),
            name=parts[1].strip(),
            elapsed=parts[2].strip(),
            partition=parts[3].strip(),
            state=parts[4].strip(),
            time_limit=parts[5].strip(),
            nodes=parts[6].strip(),
            cpus=parts[7].strip(),
            memory=parts[8].strip(),
            gres=parts[9].strip() or "None",
            work_dir=parts[10].strip(),
        ))
    jobs.sort(key=lambda j: int(j.job_id) if j.job_id.isnumeric() else 0, reverse=True)
    return jobs


# ---------------------------------------------------------------------------
# sacct – completed / past jobs
# ---------------------------------------------------------------------------

_SACCT_FORMAT = "JobID,JobName,State,ExitCode,Start,End,Elapsed,Partition"


async def get_completed_jobs(config: Config | None = None) -> list[CompletedJob]:
    """Fetch past jobs via sacct, sorted by job ID descending (latest first)."""
    cfg = config or _config
    days = cfg.days
    user = cfg.user or USER
    start_time = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    cmd: list[str] = [
        "sacct",
        "-u", user,
        f"--format={_SACCT_FORMAT}",
        f"--starttime={start_time}",
        "--noheader",
        "--parsable2",
    ]

    stdout, _, rc = await _run_cmd(*cmd)
    if rc != 0 or not stdout.strip():
        return []

    jobs: list[CompletedJob] = []
    for line in stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 8:
            continue
        job_id = parts[0].strip()
        if "." in job_id:
            continue
        state = parts[2].strip()
        if state in ("RUNNING", "PENDING", "REQUEUED"):
            continue
        if cfg.partition and parts[7].strip() != cfg.partition:
            continue
        jobs.append(CompletedJob(
            job_id=job_id,
            name=parts[1].strip(),
            state=state,
            exit_code=parts[3].strip(),
            start=parts[4].strip(),
            end=parts[5].strip(),
            elapsed=parts[6].strip(),
            partition=parts[7].strip(),
        ))
    jobs.sort(key=lambda j: int(j.job_id) if j.job_id.isnumeric() else 0, reverse=True)
    return jobs


# ---------------------------------------------------------------------------
# scontrol – job detail
# ---------------------------------------------------------------------------


def _parse_scontrol(output: str) -> dict[str, str]:
    """Parse scontrol show job output into a key-value dict."""
    result: dict[str, str] = {}
    for line in output.splitlines():
        for token in line.split():
            if "=" in token:
                key, _, value = token.partition("=")
                result[key] = value
    return result


async def get_job_detail(job_id: str) -> JobDetail | None:
    """Get detailed info for a job. Tries scontrol first, falls back to sacct."""
    from slurmtop import config as persistent_config

    stdout, _, rc = await _run_cmd("scontrol", "show", "job", job_id)
    if rc == 0 and stdout.strip() and "Invalid job id" not in stdout:
        raw = _parse_scontrol(stdout)
        stdout_path = raw.get("StdOut")
        stderr_path = raw.get("StdErr")
        # Cache paths so they survive after the job leaves scontrol
        persistent_config.cache_log_paths(job_id, stdout_path, stderr_path)
        return JobDetail(
            job_id=job_id,
            raw=raw,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            work_dir=raw.get("WorkDir", ""),
            source="scontrol",
        )
    return await _get_job_detail_sacct(job_id)


async def _get_job_detail_sacct(job_id: str) -> JobDetail | None:
    """Get job detail from sacct as fallback."""
    fmt = (
        "JobID,JobName,State,ExitCode,Partition,NodeList,NCPUS,NNodes,"
        "ReqMem,Timelimit,Elapsed,Submit,Start,End,WorkDir,Account,QOS,"
        "ReqTRES,AllocTRES"
    )
    stdout, _, rc = await _run_cmd(
        "sacct",
        "-j", job_id,
        f"--format={fmt}",
        "--noheader",
        "--parsable2",
    )
    if rc != 0 or not stdout.strip():
        return None

    for line in stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 19:
            continue
        jid = parts[0].strip()
        if "." in jid:
            continue

        raw = {
            "JobID": parts[0].strip(),
            "JobName": parts[1].strip(),
            "State": parts[2].strip(),
            "ExitCode": parts[3].strip(),
            "Partition": parts[4].strip(),
            "Nodelist": parts[5].strip(),
            "NCPUS": parts[6].strip(),
            "NNodes": parts[7].strip(),
            "ReqMem": parts[8].strip(),
            "Timelimit": parts[9].strip(),
            "Elapsed": parts[10].strip(),
            "Submit": parts[11].strip(),
            "Start": parts[12].strip(),
            "End": parts[13].strip(),
            "WorkDir": parts[14].strip(),
            "Account": parts[15].strip(),
            "QoS": parts[16].strip(),
            "ReqTRES": parts[17].strip(),
            "AllocTRES": parts[18].strip(),
        }
        work_dir = raw["WorkDir"]
        job_name = raw["JobName"]

        # First check the persistent cache (paths saved while job was running)
        from slurmtop import config as persistent_config
        cached_out, cached_err = persistent_config.get_cached_log_paths(job_id)
        if cached_out or cached_err:
            stdout_path = cached_out
            stderr_path = cached_err
        else:
            # Fall back to guessing from filename patterns
            stdout_path = await _guess_log_path(work_dir, job_id, "out", job_name)
            stderr_path = await _guess_log_path(work_dir, job_id, "err", job_name)

        # Many clusters merge stdout and stderr into one .out file.
        # If no separate .err file found, fall back to the stdout path.
        if not stderr_path and stdout_path:
            stderr_path = stdout_path
        return JobDetail(
            job_id=job_id,
            raw=raw,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            work_dir=work_dir,
            source="sacct",
        )
    return None


async def _guess_log_path(
    work_dir: str, job_id: str, suffix: str, job_name: str = "",
) -> str | None:
    """Try common Slurm log file naming patterns (local or remote).

    Checks multiple patterns used by different cluster configurations.
    """
    if not work_dir:
        return None

    ext_out = "out" if suffix == "out" else "err"
    candidates = [
        # Default Slurm pattern
        os.path.join(work_dir, f"slurm-{job_id}.{ext_out}"),
        # Common: just .out / .err
        os.path.join(work_dir, f"slurm-{job_id}.{suffix}"),
        # Some clusters use job name in the filename
    ]
    if job_name:
        candidates.extend([
            os.path.join(work_dir, f"{job_name}-{job_id}.{ext_out}"),
            os.path.join(work_dir, f"{job_name}_{job_id}.{ext_out}"),
            os.path.join(work_dir, f"{job_name}.{ext_out}"),
            # sbatch --output/--error with %j pattern
            os.path.join(work_dir, f"{job_name}-%j.{ext_out}".replace("%j", job_id)),
        ])
    # Also check logs/ subdirectory
    candidates.extend([
        os.path.join(work_dir, "logs", f"slurm-{job_id}.{ext_out}"),
        os.path.join(work_dir, "log", f"slurm-{job_id}.{ext_out}"),
    ])

    for path in candidates:
        if await _file_exists(path):
            return path
    return None


async def _file_exists(path: str) -> bool:
    """Check if a file exists (locally or on the remote host)."""
    if _config.remote:
        _, _, rc = await _run_ssh(_config.remote, f"test -f {shlex.quote(path)}")
        return rc == 0
    return await asyncio.to_thread(os.path.isfile, path)


# ---------------------------------------------------------------------------
# sstat – resource usage for running jobs
# ---------------------------------------------------------------------------

_SSTAT_FORMAT = (
    "AveCPU,AveCPUFreq,AveRSS,MaxRSS,AveVMSize,MaxVMSize,"
    "AveDiskRead,AveDiskWrite,MaxDiskRead,MaxDiskWrite,"
    "MaxRSSNode,MaxRSSTask"
)

_SACCT_STATS_FORMAT = (
    "TotalCPU,Elapsed,ReqMem,AllocTRES,ReqTRES"
)


async def get_job_stats(job_id: str) -> JobStats | None:
    """Get resource usage stats combining sstat (running) and sacct data."""
    sstat_result, sacct_result = await asyncio.gather(
        _get_sstat(job_id),
        _get_sacct_stats(job_id),
    )

    if sstat_result is None and sacct_result is None:
        return None

    stats = sstat_result or JobStats(job_id=job_id)

    if sacct_result:
        stats.total_cpu = sacct_result.get("TotalCPU", "N/A")
        stats.elapsed = sacct_result.get("Elapsed", "N/A")
        stats.req_mem = sacct_result.get("ReqMem", "N/A")
        for tres_key in ("AllocTRES", "ReqTRES"):
            tres = sacct_result.get(tres_key, "")
            if "gres/gpu" in tres.lower():
                for part in tres.split(","):
                    if "gres/gpu" in part.lower():
                        stats.gpu_alloc = part.strip()
                        break
                break
        stats.gpu_tres = sacct_result.get("AllocTRES", sacct_result.get("ReqTRES", "N/A"))
        if stats.source == "sstat":
            stats.source = "combined"
        else:
            stats.source = "sacct"

    return stats


async def _get_sstat(job_id: str) -> JobStats | None:
    """Get live resource usage for a running job via sstat."""
    stdout, _, rc = await _run_cmd(
        "sstat",
        "-j", f"{job_id}.batch",
        f"--format={_SSTAT_FORMAT}",
        "--noheader",
        "--parsable2",
    )
    if rc != 0 or not stdout.strip():
        return None

    line = stdout.strip().splitlines()[0]
    parts = line.split("|")
    if len(parts) < 12:
        return None
    return JobStats(
        job_id=job_id,
        ave_cpu=parts[0].strip() or "N/A",
        ave_cpu_freq=parts[1].strip() or "N/A",
        ave_rss=parts[2].strip() or "N/A",
        max_rss=parts[3].strip() or "N/A",
        ave_vm_size=parts[4].strip() or "N/A",
        max_vm_size=parts[5].strip() or "N/A",
        ave_disk_read=parts[6].strip() or "N/A",
        ave_disk_write=parts[7].strip() or "N/A",
        max_disk_read=parts[8].strip() or "N/A",
        max_disk_write=parts[9].strip() or "N/A",
        max_rss_node=parts[10].strip() or "N/A",
        max_rss_task=parts[11].strip() or "N/A",
        source="sstat",
    )


async def _get_sacct_stats(job_id: str) -> dict[str, str] | None:
    """Get accounting stats from sacct."""
    stdout, _, rc = await _run_cmd(
        "sacct",
        "-j", job_id,
        f"--format={_SACCT_STATS_FORMAT}",
        "--noheader",
        "--parsable2",
    )
    if rc != 0 or not stdout.strip():
        return None

    for line in stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 5:
            continue
        return {
            "TotalCPU": parts[0].strip(),
            "Elapsed": parts[1].strip(),
            "ReqMem": parts[2].strip(),
            "AllocTRES": parts[3].strip(),
            "ReqTRES": parts[4].strip(),
        }
    return None


# ---------------------------------------------------------------------------
# Log file reading
# ---------------------------------------------------------------------------

TAIL_LINES = 500


async def read_log_file(path: str | None, tail_lines: int = TAIL_LINES) -> str:
    """Read the tail of a log file (locally or via SSH in remote mode)."""
    if not path:
        return "(no log file path available)"

    if _config.remote:
        # Read file via SSH
        cmd = f"tail -n {tail_lines} {shlex.quote(path)} 2>/dev/null || echo '(file not found: {path})'"
        stdout, _, rc = await _run_ssh(_config.remote, cmd)
        return stdout if stdout.strip() else f"(file not found: {path})"

    if not os.path.isfile(path):
        return f"(file not found: {path})"

    def _read() -> str:
        with open(path, errors="replace") as f:
            lines = deque(f, maxlen=tail_lines)
        return "".join(lines)

    return await asyncio.to_thread(_read)


# ---------------------------------------------------------------------------
# scancel – cancel a job
# ---------------------------------------------------------------------------


async def cancel_job(job_id: str, force: bool = False) -> tuple[bool, str]:
    """Cancel a job. If force=True, sends SIGKILL immediately. Returns (success, msg)."""
    if force:
        _, stderr, rc = await _run_cmd("scancel", "--signal=KILL", job_id)
    else:
        _, stderr, rc = await _run_cmd("scancel", job_id)
    if rc == 0:
        kind = "force-cancelled" if force else "cancelled"
        return True, f"Job {job_id} {kind}."
    return False, f"Failed to cancel job {job_id}: {stderr.strip()}"


async def resubmit_job(command: str, work_dir: str) -> tuple[bool, str]:
    """Resubmit a job using its original sbatch command. Returns (success, msg)."""
    stdout, stderr, rc = await _run_cmd("sbatch", "--chdir", work_dir, command)
    if rc == 0:
        return True, stdout.strip()
    return False, f"Resubmit failed: {stderr.strip()}"


# ---------------------------------------------------------------------------
# Cluster summary (sinfo + squeue counts)
# ---------------------------------------------------------------------------


async def get_cluster_summary(config: Config | None = None) -> str:
    """Get a one-line cluster summary: user job counts + partition availability."""
    cfg = config or _config
    user = cfg.user or USER

    squeue_out, sinfo_out = await asyncio.gather(
        _run_cmd("squeue", "-u", user, "--noheader", "--format=%T"),
        _run_cmd("sinfo", "--noheader", "--summarize", "--format=%P|%a|%F"),
    )

    # Count running/pending from squeue
    running = pending = 0
    stdout_sq = squeue_out[0]
    if stdout_sq.strip():
        for line in stdout_sq.strip().splitlines():
            state = line.strip()
            if state == "RUNNING":
                running += 1
            elif state == "PENDING":
                pending += 1

    parts: list[str] = [
        f"[bold]{user}[/]",
        f"[green]{running}[/] running",
        f"[yellow]{pending}[/] pending",
    ]

    # Parse sinfo: partition|availability|allocated/idle/other/total
    stdout_si = sinfo_out[0]
    if stdout_si.strip():
        part_dict: dict[str, str] = {}
        for line in stdout_si.strip().splitlines():
            fields = line.split("|")
            if len(fields) >= 3:
                pname = fields[0].strip().rstrip("*")
                avail = fields[1].strip()
                nodes = fields[2].strip()  # e.g. "10/5/0/15"
                if avail == "up":
                    part_dict[pname] = f"{pname}:{nodes}"

        # Apply user-specified partition order if given
        if cfg.partition_order:
            ordered = [part_dict[p] for p in cfg.partition_order if p in part_dict]
            # Append remaining partitions not in the order list
            for p in part_dict:
                if p not in cfg.partition_order:
                    ordered.append(part_dict[p])
            part_info = ordered
        else:
            part_info = list(part_dict.values())

        if part_info:
            parts.append("  " + "  ".join(part_info))

    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Live node monitoring via SSH
# ---------------------------------------------------------------------------


async def get_node_processes(node: str, user: str = "") -> str:
    """Get a top-like process listing from a compute node via SSH."""
    if not node or node in ("N/A", "None", "(null)"):
        return "[dim]No node assigned[/]"

    first_node = _first_node(node)
    target_user = user or (_config.user if _config.user else USER)

    stdout, rc = await _ssh_cmd(
        first_node,
        f"ps -u {shlex.quote(target_user)} -o pid,%cpu,%mem,rss:10,vsz:10,etime,comm --sort=-%cpu --no-headers 2>/dev/null | head -30",
    )
    if rc != 0 or not stdout.strip():
        return f"[dim]Could not reach {first_node} (SSH failed)[/]"

    header = f"{'PID':>7}  {'%CPU':>5}  {'%MEM':>5}  {'RSS':>10}  {'VSZ':>10}  {'ELAPSED':>12}  COMMAND\n"
    separator = "-" * 72 + "\n"
    return f"[bold]Node: {first_node}[/]\n\n{header}{separator}{stdout}"


async def get_gpu_status(node: str, job_id: str = "") -> str:
    """Get nvidia-smi output for only the GPUs allocated to a job.

    Uses `srun --overlap --jobid` to run nvidia-smi inside the job's cgroup,
    which automatically restricts visibility to only allocated GPUs.
    Falls back to SSH-based nvidia-smi if srun is not available.
    """
    if not node or node in ("N/A", "None", "(null)"):
        return "[dim]No node assigned[/]"

    first_node = _first_node(node)

    # Strategy 1 (preferred): Run nvidia-smi inside the job's cgroup via srun.
    # Slurm's cgroup automatically restricts CUDA_VISIBLE_DEVICES,
    # so nvidia-smi only sees the allocated GPUs.
    if job_id:
        stdout, stderr, rc = await _run_cmd(
            "srun", "--overlap", f"--jobid={job_id}",
            "bash", "-c",
            "echo CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES; nvidia-smi 2>/dev/null || echo 'nvidia-smi not available'",
        )
        if rc == 0 and stdout.strip():
            # Extract CUDA_VISIBLE_DEVICES from first line for the header
            lines = stdout.strip().splitlines()
            cuda_line = lines[0] if lines[0].startswith("CUDA_VISIBLE_DEVICES=") else ""
            gpu_indices = cuda_line.split("=", 1)[1] if "=" in cuda_line else ""
            nvsmi_output = "\n".join(lines[1:]) if cuda_line else stdout.strip()

            header = f"[bold]Node: {first_node}[/]"
            if gpu_indices:
                header += f"  [dim](CUDA_VISIBLE_DEVICES={gpu_indices})[/]"
            return f"{header}\n\n{nvsmi_output}"

    # Strategy 2 (fallback): SSH to the node and run nvidia-smi directly.
    # This shows all GPUs on the node — not ideal but better than nothing.
    nvsmi_cmd = "nvidia-smi 2>/dev/null || echo 'nvidia-smi not available on this node'"
    stdout, rc = await _ssh_cmd(first_node, nvsmi_cmd)
    if rc != 0 or not stdout.strip():
        return f"[dim]Could not reach {first_node}[/]"

    header = f"[bold]Node: {first_node}[/]"
    if job_id:
        header += f"  [dim yellow](showing all GPUs — srun --overlap failed, falling back to SSH)[/]"
    return f"{header}\n\n{stdout}"


def _first_node(node_spec: str) -> str:
    """Extract the first node name from a Slurm node specification.

    Handles formats like 'node001', 'node[001-003]', 'node001,node002'.
    """
    if "," in node_spec and "[" not in node_spec:
        return node_spec.split(",")[0]
    if "[" in node_spec:
        prefix = node_spec.split("[")[0]
        inside = node_spec.split("[")[1].rstrip("]")
        first_range = inside.split(",")[0]
        first_num = first_range.split("-")[0]
        return f"{prefix}{first_num}"
    return node_spec
