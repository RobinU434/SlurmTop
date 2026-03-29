# SlurmTop

A terminal UI for monitoring Slurm HPC jobs — like `htop` for your cluster.

SlurmTop gives you a live overview of your running and past jobs, lets you read
stdout/stderr logs, inspect resource usage, monitor CPU and GPU activity on compute
nodes, cancel or resubmit jobs, and more — all from a single terminal.

```
+-----------------------+------------------------------------------------+
| Active Jobs           | Job Details  [stdout] [stderr] [cpu] [gpu]     |
|  2465501  train  0:42 |  Epoch 12/100, loss=0.0342                     |
|  2465499  eval   1:15 |  Epoch 13/100, loss=0.0318                     |
|  2465485  vs     3:22 |  ...                                           |
+-----------------------+                                                |
| Terminated Jobs       +------------------------------------------------+
|  2465400  prep  DONE  | Job Metadata [Resources] [Submission] [Raw]    |
|  2465312  sweep FAIL  |  Partition: gpu    Nodes: 1   CPUs: 8          |
|  2465301  test  DONE  |  GPU: gres/gpu:rtx2080ti=1   Memory: 40G      |
+-----------------------+------------------------------------------------+
```

## Installation

Requires Python 3.10+ and access to Slurm CLI tools (`squeue`, `sacct`, `scontrol`).

```bash
# Clone the repository
git clone https://github.com/your-org/slurmtop.git
cd slurmtop

# Install with uv (recommended)
uv pip install -e .

# Or with pip
pip install -e .
```

## Quick Start

```bash
# Run on a cluster login node
slurmtop

# Run from your local machine, monitoring a remote cluster
slurmtop --remote user@login.hpc.edu

# Customize refresh rate and time window
slurmtop --refresh 3 --days 14
```

## Layout

SlurmTop has a four-panel layout:

| Panel | Position | Content |
|-------|----------|---------|
| **Active Jobs** | Top-left | Running and pending jobs from `squeue` |
| **Terminated Jobs** | Bottom-left | Completed, failed, timed-out, and cancelled jobs from `sacct` |
| **Job Details** | Top-right (2/3) | Tabbed view: stdout, stderr, live CPU, live GPU, resource stats |
| **Job Metadata** | Middle-right | Tabbed view: Resources, Submission info, Raw scontrol output |
| **Command Log** | Bottom-right | Timestamped log of actions and responses |

A **cluster overview bar** at the top shows your running/pending counts and partition
availability.

## Key Bindings

### Navigation

| Key | Action |
|-----|--------|
| `Up` / `Down` | Navigate job list (wraps between Active and Terminated) |
| `Tab` / `Shift+Tab` | Switch focus between right-side panels |
| `Left` / `Right` | Switch focus between right-side panels |
| `[` / `]` | Switch tabs within the focused panel |

### Actions

| Key | Action |
|-----|--------|
| `/` | Open search bar — filter jobs by ID, name, or partition. Press `Escape` to close and clear |
| `m` | Bookmark / unbookmark the selected job. Bookmarked jobs show a ★ prefix and are pinned to the top of their table |
| `c` | Cancel the selected job (with confirmation prompt) |
| `s` | Resubmit a terminated job using its original sbatch script (with confirmation) |
| `o` | SSH to the selected job's compute node. Suspends the TUI; type `exit` to return |
| `r` | Force refresh all job data |
| `?` | Toggle the help screen (also closes with `Escape`) |
| `q` | Quit |

## Detail Tabs

Select a job in either table and use `[` / `]` to switch between these tabs:

### stdout / stderr

Displays the tail of the job's standard output and error log files. SlurmTop finds log
files by reading `StdOut` / `StdErr` from `scontrol show job`. For older jobs not in
Slurm's memory, it falls back to `sacct` and searches the working directory for common
patterns (`slurm-JOBID.out`, `JOBNAME-JOBID.out`, `logs/` subdirectory, etc).

### cpu

Live process listing from the job's compute node, similar to `top`. Shows PID, %CPU,
%MEM, RSS, VSZ, elapsed time, and command name. Auto-refreshes while the tab is active.

### gpu

Live `nvidia-smi` output showing **only the GPUs allocated to the selected job**. Uses
`srun --overlap --jobid` to run nvidia-smi inside the job's cgroup, so GPU visibility
is automatically restricted to the job's allocation. The header shows
`CUDA_VISIBLE_DEVICES` for confirmation. Auto-refreshes while the tab is active.

### stats

Accounting statistics from `sstat` (running jobs) and `sacct`:

- **CPU** — average CPU time, total CPU, frequency, wall time
- **Memory** — requested, max/average RSS, max/average VM size, peak node/task
- **GPU** — allocated GPU count and type from TRES
- **Disk I/O** — average and max read/write

## Metadata Tabs

### Resources

Partition, node count, CPUs, memory, GPU/GRES allocation, TRES, time limit, runtime,
account, and QoS.

### Submission

Submit time, start/end times, working directory, stdout/stderr paths, and the original
submit command.

### Raw

All key-value pairs from `scontrol show job` or `sacct`, displayed verbatim.

## Visual Features

### Color-Coded Partitions

Each partition is assigned a consistent color across both job tables. Colors are
deterministic (based on the partition name) so they stay stable across sessions. You can
override colors in the config file (see [Configuration](#configuration)).

### Color-Coded Job States

| State | Color |
|-------|-------|
| COMPLETED | Green |
| FAILED, OUT_OF_MEMORY, NODE_FAIL | Red |
| TIMEOUT | Yellow |
| CANCELLED | Dim grey |
| PREEMPTED | Dim yellow |

### Cluster Overview Bar

The top line shows a summary of your jobs and cluster partitions:

```
mot824  5 running  2 pending    gpu:10/5/1/16  cpu:42/58/0/100
```

Partition format is `name:A/I/O/T`:

| Field | Meaning |
|-------|---------|
| **A** | Allocated — nodes currently running jobs |
| **I** | Idle — nodes available for new jobs |
| **O** | Other — nodes that are down, drained, or in maintenance |
| **T** | Total — total nodes in the partition |

### Bookmarks

Press `m` to bookmark any job. Bookmarked jobs are pinned to the top of their table with
a ★ prefix. Bookmarks persist for the duration of the session.

### Job Completion Notifications

When a running job finishes (completes, fails, times out, etc.), SlurmTop:
- Rings the terminal bell
- Attempts a desktop notification via `notify-send` (Linux)
- Logs the event in the Command Log panel

### Command Log

The bottom-right panel shows a timestamped log of all actions and their results:

```
14:23:05 refresh
  >>> complete
14:23:12 scancel 2465400
  >>> Job 2465400 cancelled.
14:23:30 ssh galvani-cn109
  >>> session to galvani-cn109 closed
14:24:01 job completed
  >>> 2465485 COMPLETED
```

## Remote Mode

Run SlurmTop on your local machine while monitoring a remote cluster:

```bash
slurmtop --remote user@login.hpc.edu
```

All Slurm commands (`squeue`, `sacct`, `scontrol`, `sstat`, `scancel`, `sbatch`) are
transparently tunneled via SSH. Log files are read remotely. The GPU tab uses
`srun --overlap` on the remote cluster. The SSH-to-node feature (`o`) connects through
the login node via ProxyJump.

For best performance, configure SSH connection multiplexing in `~/.ssh/config`:

```
Host login.hpc.edu
    ControlMaster auto
    ControlPath ~/.ssh/sockets/%r@%h-%p
    ControlPersist 600
```

## Configuration

SlurmTop stores persistent settings in `~/.config/slurmtop/config.toml` (respects
`$XDG_CONFIG_HOME`). The file is created automatically when you use `--partition-order`,
or you can create it by hand.

### Example config file

```toml
# All CLI arguments can be set here as defaults.
# CLI arguments always override config file values.
# When a CLI arg overrides a config value, it is shown in the Command Log.

refresh = 3.0            # -r/--refresh: auto-refresh interval in seconds
days = 14                # -d/--days: how many days back for terminated jobs
user = "myuser"          # -u/--user: Slurm user to monitor
partition = ""           # -p/--partition: filter by partition (empty = all)
no_gpu = false           # --no-gpu: disable GPU monitoring tab
no_live = false          # --no-live: disable live CPU/GPU monitoring
remote = ""              # -H/--remote: SSH target for remote mode

# Partition display order in the cluster bar.
# Partitions not listed appear after these in their default order.
# Set via CLI: slurmtop --partition-order gpu,cpu,fat
partition_order = ["gpu", "cpu", "fat"]

# Custom partition colors in the job tables.
# Overrides the automatic hash-based coloring.
# Valid color names: cyan, magenta, yellow, green, blue, red,
# bright_cyan, bright_magenta, bright_green, white, dim, bold,
# or any Rich color (e.g. "dark_orange", "grey50").
[partition_colors]
gpu = "green"
cpu = "cyan"
fat = "magenta"
debug = "dim"
```

### CLI vs config file

All CLI arguments can be set in the config file. The precedence is:

**CLI argument > config file > built-in default**

When a CLI argument overrides a config file value that differs, the override is logged in
the Command Log panel at startup.

### Partition order

To set a custom partition order for the cluster bar:

```bash
# Set once — automatically saved for future sessions
slurmtop --partition-order gpu,cpu,fat
```

## CLI Reference

```
slurmtop [-h] [-r SEC] [-d N] [-u USER] [-p PARTITION]
         [--no-gpu] [--no-live] [--partition-order P1,P2,...] [-H HOST]
```

| Flag | Description | Default |
|------|-------------|---------|
| `-r`, `--refresh` | Auto-refresh interval in seconds | 5 |
| `-d`, `--days` | How many days back to show terminated jobs | 7 |
| `-u`, `--user` | Slurm user to monitor | `$USER` |
| `-p`, `--partition` | Filter jobs by partition | (all) |
| `--no-gpu` | Disable the GPU monitoring tab | off |
| `--no-live` | Disable live CPU and GPU monitoring (no SSH/srun to nodes) | off |
| `--partition-order` | Comma-separated partition display order for cluster bar | (sinfo order) |
| `-H`, `--remote` | SSH target for remote mode (e.g. `user@login.hpc.edu`) | (local) |

## Requirements

- Python 3.10+
- Slurm CLI tools: `squeue`, `sacct`, `scontrol`, `sstat`, `scancel`
- [Textual](https://textual.textualize.io/) (installed automatically)
- For GPU monitoring: `nvidia-smi` on compute nodes, `srun --overlap` support
- For remote mode: SSH access to the cluster login node

## License

[MIT](LICENSE)
