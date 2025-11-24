# SlurmTop

A starter terminal UI (TUI) prototype for monitoring Slurm jobs. The layout focuses on:

- **Left column** – split into _Current Jobs_ and _Past Jobs_ lists.
- **Right column** – top tabs for quick actions (error log, output log, job script) and a bottom _scontrol_-style job inspector.

- **Actions column** – tabs now include CPU + GPU utilization previews before the log/script panes. The CPU tab mimics an `htop` snapshot (per-core bars, top tasks, memory/load), while the GPU tab mimics `nvtop` (device table with temps/utilization).

This repository currently ships only the layout scaffolding so you can learn Rust + Ratatui while iterating on the data plumbing later.

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

| Key                 | Action                                             |
|---------------------|----------------------------------------------------|
| ↑ / ↓               | Move within the focused jobs list                  |
| ←                   | Focus the upper `Current Jobs` pane                |
| →                   | Focus the lower `Past Jobs` pane                   |
| `c` / `g`           | Jump directly to CPU / GPU tabs                    |
| `e` / `o` / `j`     | Jump to Error Log / Output Log / Job Script tabs   |
| `k`                 | Connect to the selected Slurm job                  |
| `n`                 | Launch a new interactive job using saved config    |
| `i`                 | Open the interactive-job configuration overlay     |
| `Tab` / `Shift+Tab` | Cycle through action tabs sequentially            |
| `?`                 | Toggle the on-screen help & shortcuts overlay      |
| `q` / `Esc`         | Quit the TUI (also closes the help overlay)        |

### Interactive job config

- Defaults live in `config/interactive_job.toml`. Adjust them manually or press `i` in the TUI to open the inline editor.
- Launch a new interactive Slurm session (mocked locally) with `n`; the request summary pulls from the config above.
- Press `k` while a job is highlighted to attempt a connection; finished jobs surface a notice in the inspector panel.

## Quick start

1. **Install Rust (rustup recommended):**

	```bash
	curl --proto '=https' --tlsv1.2 https://sh.rustup.rs -sSf | sh
	```

	Then run `source "$WORK/.cargo/env"` (or restart the shell) so `cargo` is on your `PATH`.

2. **Install project dependencies and run the TUI:**

	```bash
	cd /home/robin/projects/SlurmTop
	cargo run
	```

	The binary will start in your terminal; press `q` to exit.

	### Install & run via Python (maturin)

	Prefer `pip install`? The repo ships a thin Python bridge that embeds the Rust TUI via [PyO3](https://pyo3.rs).

	```bash
	# inside the repo
	cd /home/robin/projects/SlurmTop
	pip install maturin
	maturin develop --features python  # builds a native wheel and installs it into the current venv

	# now launch from Python
	python -m slurmtop           # or: python -c "import slurmtop; slurmtop.run()"
	```

	To publish a wheel, run `maturin build --release --features python` and upload the generated files under `target/wheels/`.

	### Filter the displayed jobs

	By default SlurmTop now shows **only your own jobs** so the `Current Jobs` pane stays relevant even on crowded clusters. The username is resolved (in order) from `$SLURMTOP_USER`, `$SLURM_JOB_USER`, `$USER`, `$LOGNAME`, and finally `id -un`, so even stripped-down environments still pick the right account. You can override or extend the filter explicitly:

	```bash
	# Force SlurmTop to show a specific account (comma-separated list allowed)
	export SLURMTOP_USER=project-account,shared-service

	# Or show every job on the cluster (disables filtering entirely)
	export SLURMTOP_ALL_JOBS=1
	```

	Set one of these variables in the shell before launching `slurmtop` (or add them to your `.bashrc`/job wrapper) to tailor the sidebar to your workflow.

## Building with Singularity (cluster-friendly)

When login nodes lack a Rust toolchain, build SlurmTop inside the provided Singularity image. The container is based on Debian 10 (glibc 2.28) so the resulting binaries and wheels can run on the cluster hosts, but it **does not** include Slurm CLI utilities—keep using the host environment for actually talking to the scheduler.

1. **Build or update the image** (needs Singularity/Apptainer with `--fakeroot` support):

	```bash
	cd /home/robin/projects/SlurmTop
	singularity build --fakeroot container/SlurmTop.sif container/SlurmTop.def
	```

2. **Run cargo inside the container** while binding the repo and pointing `CARGO_HOME` at a writable path (on the host).

	```bash
	mkdir -p .cargo  # once per repo clone

	singularity exec \
	  --bind $(pwd):/workspace \
	  --pwd /workspace \
	  --env CARGO_HOME=/workspace/.cargo \
	  container/SlurmTop.sif \
	  cargo build --release
	```

	> **Why override `CARGO_HOME`?** The preinstalled Rust toolchain lives under `/opt/rust`, which is read-only inside the SIF. Sending Cargo's caches to `/workspace/.cargo` keeps all writes on the host filesystem and avoids “Read-only file system” errors.

3. **Build a manylinux_2_28 wheel that matches the cluster's glibc (and Python 3.12 conda env).** Bind the external conda root so the container can reach your interpreter:

	```bash
	CONDA_ROOT=/mnt/lustre/work/martius/mot824/.conda
	PY_ENV=$CONDA_ROOT/envs/slurmtop

	singularity exec \
	  --bind $(pwd):/workspace \
	  --bind ${CONDA_ROOT}:${CONDA_ROOT} \
	  --pwd /workspace \
	  --env CARGO_HOME=/workspace/.cargo \
	  container/SlurmTop.sif \
	  maturin build --release --features python \
	    --interpreter ${PY_ENV}/bin/python \
	    --compatibility manylinux_2_28
	```

	The resulting wheel lands in `target/wheels/` with a tag like `cp312-cp312-manylinux_2_28_x86_64`.

4. **Install the wheel _outside_ the container** (so it uses the real environment's site-packages):

	```bash
	${PY_ENV}/bin/pip install --force-reinstall target/wheels/slurmtop-0.1.0-cp312-cp312-manylinux_2_28_x86_64.whl
	```

	Optionally sanity-check the import:

	```bash
	${PY_ENV}/bin/python -c "import slurmtop; print('slurmtop import ok')"
	```

The image still ships helper scripts under `/usr/local/bin` (`slurmtop-python-build` / `slurmtop-python-run`) for quick smoke tests inside the container, but the recommended workflow for cluster installs is to build wheels via Singularity and install them directly into your external conda environment.

## Submitting a build via sbatch

Automate container builds on a compute node with `scripts/build_slurmtop.sbatch`:

```bash
cd /home/robin/projects/SlurmTop
sbatch scripts/build_slurmtop.sbatch
```

Environment variables you can override when calling `sbatch`:

- `SLURMTOP_REPO` – path to the repo (defaults to `PWD`).
- `SLURMTOP_DEF` – Singularity definition file (defaults to `container/SlurmTop.def`).
- `SLURMTOP_SIF` – destination of the built image.
- `SLURMTOP_LOG_DIR` – directory for job logs (defaults to `logs/`).
- `SLURMTOP_PY_BUILD` – set to `1` to run `slurmtop-python-build` after the Rust build completes.

Edit the `#SBATCH` directives in the script to match your partition/time/memory requirements. The script reuses an existing image if present, rebuilding only when missing, and keeps Slurm CLI access by binding `/etc/slurm` into the container.

## Project structure

```
SlurmTop/
├── Cargo.toml        # crate metadata + dependencies
├── README.md         # you are here
└── src/
	 └── main.rs       # TUI layout + placeholder data
```

## Extending the layout

- Replace the mocked `Job` data with real Slurm output (`squeue`, `sacct`, or RPCs).
- Feed log panes by tailing job-specific stderr/stdout files.
- Add filtering, sorting, and keyboard-driven navigation between panes.
- Persist UI state (selected job, filters) between refreshes.

## Rust starter guide

### 1. Toolchain basics

- **rustup** manages multiple compiler versions (`stable`, `beta`, `nightly`). Check versions with `rustup show`.
- **cargo** is the build system + package manager. Common commands:
  - `cargo new my-app` – scaffold a binary crate.
  - `cargo add crate_name` – add a dependency (via `cargo-edit`).
  - `cargo run` / `cargo test` / `cargo fmt` – execute, test, and format.

### 2. Project anatomy

- `Cargo.toml` declares package metadata and dependencies.
- `src/main.rs` is the binary entrypoint (`fn main`).
- Add helper modules by creating files (e.g., `src/ui.rs`) and declaring `mod ui;` in `main.rs`.

### 3. Language building blocks

- **Variables** are immutable by default; use `let mut value = 42;` for mutability.
- **Ownership** ensures a single owner of heap data; cloning (`.clone()`) duplicates when needed.
- **Borrowing** lets you pass references: `fn draw(area: &Rect)` doesn't take ownership.
- **Enums + pattern matching** power expressive state machines (`match event { ... }`).

### 4. Error handling

- Use `Result<T, E>` for recoverable errors; `?` propagates failures (`fn main() -> Result<()>`).
- Libraries like `color-eyre` add pretty context + backtraces.

### 5. Testing + formatting

- Write unit tests inline with `#[cfg(test)] mod tests { ... }` and run `cargo test`.
- Keep formatting consistent via `cargo fmt` (rustfmt) and lint with `cargo clippy`.

### 6. Recommended learning path

1. Follow _The Rust Programming Language_ (a.k.a. the Rust Book) chapters 1–7.
2. Build tiny utilities (CLI parser, file watcher) to internalize ownership.
3. Explore async runtimes (Tokio) and systems crates once you're comfortable.
4. Read ratatui examples to understand layout + widget composition.

### 7. Handy references

- [The Rust Book](https://doc.rust-lang.org/book/) – canonical learning resource.
- [Rust By Example](https://doc.rust-lang.org/rust-by-example/) – runnable snippets.
- [ratatui docs](https://docs.rs/ratatui/) – widget reference.
- [Crates.io](https://crates.io) – search ecosystem libraries.

Happy hacking! Replace the placeholder data, wire it to your Slurm cluster, and keep iterating on the UI/UX as you learn more Rust.