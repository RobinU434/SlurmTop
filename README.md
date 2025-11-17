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

| Key              | Action                                             |
|------------------|----------------------------------------------------|
| ↑ / ↓            | Move within the focused jobs list                  |
| ←                | Focus the upper `Current Jobs` pane                |
| →                | Focus the lower `Past Jobs` pane                   |
| `c` / `g`        | Jump directly to CPU / GPU tabs                    |
| `e` / `o` / `j`  | Jump to Error Log / Output Log / Job Script tabs   |
| `Tab` / `Shift+Tab` | Cycle through action tabs sequentially        |
| `?`              | Toggle the on-screen help & shortcuts overlay      |
| `q` / `Esc`      | Quit the TUI (also closes the help overlay)        |

## Quick start

1. **Install Rust (rustup recommended):**

	```bash
	curl --proto '=https' --tlsv1.2 https://sh.rustup.rs -sSf | sh
	```

	Then run `source "$HOME/.cargo/env"` (or restart the shell) so `cargo` is on your `PATH`.

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