# SlurmTop

A pure-Python terminal UI (TUI) built with [Textual](https://textual.textualize.io/) for monitoring Slurm jobs. The layout mirrors the earlier Rust prototype while remaining easy to iterate on with familiar Python tooling. The interface focuses on:

- **Left column** – split into _Current Jobs_ and _Past Jobs_ lists.
- **Right column** – top tabs for quick actions (error log, output log, job script) and a bottom _scontrol_-style job inspector.

- **Actions column** – tabs now include CPU + GPU utilization previews before the log/script panes. The CPU tab mimics an `htop` snapshot (per-core bars, top tasks, memory/load), while the GPU tab mimics `nvtop` (device table with temps/utilization).

This repository currently ships mocked data plus local host metrics (via `psutil`) so you can experiment with Textual widgets before wiring in real Slurm plumbing.

## Layout preview

```
+----------------------+-------------------------------+
|   Current Jobs       | Actions (tabs)                 |
|   (list)             |   [Error Log][Output Log][...]|
|----------------------|-------------------------------|
|   Past Jobs          | Pane Preview                  |
|   (list)             |                               |
+----------------------+-------------------------------+
|                      Job Inspector (scontrol-style)  |
+-----------------------------------------------------+
```

### Key bindings

| Key        | Action                                                          |
|------------|-----------------------------------------------------------------|
| Arrow keys | Navigate the `Current` / `Past` job lists                       |
| `Tab`      | Toggle focus between `Current` and `Past` job lists             |
| `1` – `5`  | Switch to CPU, GPU, Error Log, Output Log, or Job Script tabs   |
| `k`        | Connect to the highlighted job (mock connection message)        |
| `h`        | Hook into the selected running job via `srun --jobid`           |
| `n`        | Launch a mocked interactive job using `config/interactive_job.toml` |
| `r`        | Force-refresh local CPU metrics                                 |
| `q`        | Quit the application                                            |
| `?`        | Show the in-app shortcut reference overlay                      |

### Interactive job config

- Defaults live in `config/interactive_job.toml`. Tweak them manually to change the values referenced by the `n` shortcut.
- Press `n` to simulate launching an interactive job; the status banner reflects the config values.
- Press `k` while a job is highlighted to attempt a connection; finished jobs surface a notice in the inspector panel.

## Quick start

1. **Install/upgrade [uv](https://github.com/astral-sh/uv) (one time):**

	```bash
	curl -LsSf https://astral.sh/uv/install.sh | sh
	```

2. **Create and activate a virtual environment with uv:**

	```bash
	cd /home/robin/projects/Slurmtop
	uv venv .venv
	source .venv/bin/activate
	```

3. **Install SlurmTop (editable) via uv:**

	```bash
	uv pip install -e .
	```

4. **Run the TUI:**

	```bash
	uv run python -m slurmtop
	# or: uv run slurmtop
	```

Press `q` at any time to exit the interface.
## Project structure

```
SlurmTop/
├── README.md         # you are here
├── pyproject.toml    # hatch-based Python packaging metadata
├── config/
│   └── interactive_job.toml  # editable config used by the `n` shortcut
└── slurmtop/
	 ├── app.py        # Textual widgets + layout + metrics
	 ├── __init__.py   # Package exports and console-script entry
	 ├── __main__.py   # Enables `python -m slurmtop`
	 └── styles.tcss   # Textual CSS theme
```

## Extending the layout

- Replace the mocked `Job` data with real Slurm output (`squeue`, `sacct`, or RPCs).
- Feed log panes by tailing job-specific stderr/stdout files.
- Hook the CPU/GPU panes up to real telemetry sources (NVML, `nvidia-smi`, etc.).
- Add filtering, sorting, or per-user views to the job lists.

Happy hacking! Swap in real data sources, extend the widgets, and keep iterating on the UI/UX as you learn more Textual.