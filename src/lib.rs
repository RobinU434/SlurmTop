use std::{cmp::Ordering, io, time::Instant};

use color_eyre::Result;
use crossterm::{
    event::{self, Event, KeyCode},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    layout::{Alignment, Constraint, Direction, Layout, Margin, Rect},
    prelude::{CrosstermBackend, Frame, Terminal},
    style::{Color, Modifier, Style},
    text::Line,
    widgets::{
        Block, Borders, Cell, Clear, List, ListItem, ListState, Paragraph, Row, Table, Tabs,
    },
};
use sysinfo::{Process, ProcessStatus, System};

pub fn run() -> Result<()> {
    color_eyre::install()?;
    let mut app = App::default();

    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let res = run_app(&mut terminal, &mut app);

    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;

    res
}

fn run_app<B: ratatui::backend::Backend>(
    terminal: &mut Terminal<B>,
    app: &mut App,
) -> Result<()> {
    loop {
        app.refresh_metrics();
        terminal.draw(|f| ui(f, app))?;

        if crossterm::event::poll(std::time::Duration::from_millis(250))? {
            if let Event::Key(key) = event::read()? {
                match key.code {
                    KeyCode::Char('q') | KeyCode::Esc => {
                        if app.show_help {
                            app.show_help = false;
                        } else {
                            return Ok(());
                        }
                    }
                    KeyCode::Char('?') => app.show_help = !app.show_help,
                    _ if app.show_help => {}
                    KeyCode::Up => app.previous_job(),
                    KeyCode::Down => app.next_job(),
                    KeyCode::Left => app.focus_previous_jobs_list(),
                    KeyCode::Right => app.focus_next_jobs_list(),
                    KeyCode::Tab => app.next_action(),
                    KeyCode::BackTab => app.previous_action(),
                    KeyCode::Char(c) => app.activate_action_by_char(c),
                    _ => {}
                }
            }
        }
    }
}

fn ui(frame: &mut Frame<'_>, app: &App) {
    let vertical = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(0), Constraint::Length(1)])
        .split(frame.size());

    let root = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(45), Constraint::Percentage(55)])
        .split(vertical[0]);

    draw_jobs_column(frame, root[0], app);
    draw_actions_column(frame, root[1], app);
    draw_footer(frame, vertical[1]);

    if app.show_help {
        draw_help_overlay(frame, app);
    }
}

fn draw_jobs_column(frame: &mut Frame<'_>, area: Rect, app: &App) {
    let column_chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(area);

    let current_jobs: Vec<ListItem> = app
        .current_jobs
        .iter()
        .map(|job| ListItem::new(format!("#{} {} ({})", job.id, job.name, job.state)))
        .collect();

    let past_jobs: Vec<ListItem> = app
        .past_jobs
        .iter()
        .map(|job| ListItem::new(format!("#{} {} ({})", job.id, job.name, job.state)))
        .collect();

    let mut current_state = ListState::default();
    if !app.current_jobs.is_empty() {
        current_state.select(Some(app.selected_current.min(app.current_jobs.len() - 1)));
    }

    let mut past_state = ListState::default();
    if !app.past_jobs.is_empty() {
        past_state.select(Some(app.selected_past.min(app.past_jobs.len() - 1)));
    }

    let highlight = Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD);

    let current_block_style = if matches!(app.focused_jobs, JobsListFocus::Current) {
        Style::default().fg(Color::Yellow)
    } else {
        Style::default()
    };

    let past_block_style = if matches!(app.focused_jobs, JobsListFocus::Past) {
        Style::default().fg(Color::Yellow)
    } else {
        Style::default()
    };

    frame.render_stateful_widget(
        List::new(current_jobs)
            .block(
                Block::default()
                    .title("Current Jobs")
                    .borders(Borders::ALL)
                    .border_style(current_block_style),
            )
            .highlight_style(highlight.clone())
            .highlight_symbol("▶ "),
        column_chunks[0],
        &mut current_state,
    );

    frame.render_stateful_widget(
        List::new(past_jobs)
            .block(
                Block::default()
                    .title("Past Jobs")
                    .borders(Borders::ALL)
                    .border_style(past_block_style),
            )
            .highlight_style(highlight)
            .highlight_symbol("▶ "),
        column_chunks[1],
        &mut past_state,
    );
}

fn draw_actions_column(frame: &mut Frame<'_>, area: Rect, app: &App) {
    let column = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(60), Constraint::Percentage(40)])
        .split(area);

    let action_area = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Min(0)])
        .split(column[0]);

    let tab_titles: Vec<Line> = app
        .actions
        .iter()
        .map(|pane| Line::from(pane.display_title()))
        .collect();

    let tabs = Tabs::new(tab_titles)
        .block(Block::default().title("Actions").borders(Borders::ALL))
        .highlight_style(Style::default().fg(Color::Yellow))
        .select(app.selected_action);

    frame.render_widget(tabs, action_area[0]);

    render_action_body(frame, action_area[1], app);

    let inspector_block = Block::default()
        .title("Job Inspector (scontrol)")
        .borders(Borders::ALL);

    let inspector_area = column[1].inner(&Margin {
        horizontal: 1,
        vertical: 1,
    });

    let job = app.focused_job();
    let inspector_rows = [[
        format!("JobID: {}", job.id),
        format!("User: {}", job.user),
    ], [
        format!("State: {}", job.state),
        format!("Nodes: {}", job.nodes),
    ], [
        format!("Submit: {}", job.submitted),
        format!("Runtime: {}", job.runtime),
    ], [
        format!("Workdir: {}", job.workdir),
        format!("Reason: {}", job.reason),
    ]];

    frame.render_widget(inspector_block, column[1]);

    let inspector_table = Table::new(
        inspector_rows
            .into_iter()
            .map(|pair| ratatui::widgets::Row::new(pair)),
        [Constraint::Percentage(50), Constraint::Percentage(50)],
    )
    .column_spacing(1)
    .style(Style::default().fg(Color::Gray));

    frame.render_widget(inspector_table, inspector_area);
}

fn draw_footer(frame: &mut Frame<'_>, area: Rect) {
    let footer = Paragraph::new("Help [?]   Quit [q / Esc]")
        .style(Style::default().fg(Color::DarkGray))
        .alignment(Alignment::Center);
    frame.render_widget(footer, area);
}

fn draw_help_overlay(frame: &mut Frame<'_>, app: &App) {
    let area = centered_rect(70, 70, frame.size());
    let mut lines: Vec<String> = vec![
        "Navigation:".into(),
        "  Up / Down  - Move within the focused job list".into(),
        "  Left       - Focus Current Jobs (upper pane)".into(),
        "  Right      - Focus Past Jobs (lower pane)".into(),
        String::new(),
        "Action tabs:".into(),
    ];

    for (shortcut, title) in app.action_shortcuts() {
        lines.push(format!("  {} - {}", shortcut.to_ascii_uppercase(), title));
    }

    lines.extend(vec![
        String::new(),
        "Other controls:".into(),
        "  Tab / Shift+Tab - Cycle action tabs".into(),
        "  ? - Toggle this help".into(),
        "  q / Esc - Exit SlurmTop".into(),
    ]);

    let help = Paragraph::new(lines.join("\n"))
        .block(Block::default().title("Help").borders(Borders::ALL))
        .alignment(Alignment::Left)
        .wrap(ratatui::widgets::Wrap { trim: true });

    frame.render_widget(Clear, area);
    frame.render_widget(help, area);
}

fn render_action_body(frame: &mut Frame<'_>, area: Rect, app: &App) {
    match &app.current_action().content {
        PaneContent::Text(body) => {
            let action_body = Paragraph::new(*body)
                .block(Block::default().title("Pane Preview").borders(Borders::ALL))
                .wrap(ratatui::widgets::Wrap { trim: true });
            frame.render_widget(action_body, area);
        }
        PaneContent::Cpu => render_cpu_pane(frame, area, &app.cpu),
        PaneContent::Gpu => render_gpu_pane(frame, area, &app.gpu),
    }
}

fn render_cpu_pane(frame: &mut Frame<'_>, area: Rect, cpu: &CpuPane) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(5),
            Constraint::Length((cpu.cores.len() as u16).saturating_add(3)),
            Constraint::Min(5),
        ])
        .split(area);

    let summary_text = cpu
        .summary
        .iter()
        .chain(cpu.memory.iter())
        .cloned()
        .collect::<Vec<_>>()
        .join("\n");

    let summary = Paragraph::new(summary_text)
        .block(Block::default().title("System Summary").borders(Borders::ALL));
    frame.render_widget(summary, chunks[0]);

    let core_items: Vec<ListItem> = cpu
        .cores
        .iter()
        .map(|core| {
            let bar = usage_bar(core.usage as u16, 24);
            ListItem::new(format!("{:<5} [{}] {:>3}%", core.label, bar, core.usage))
        })
        .collect();

    let cores = List::new(core_items)
        .block(Block::default().title("Per-core load").borders(Borders::ALL));
    frame.render_widget(cores, chunks[1]);

    let rows = cpu.tasks.iter().map(|task| {
        Row::new(vec![
            task.pid.clone().into(),
            task.user.clone().into(),
            format!("{:.1}", task.cpu),
            format!("{:.1}", task.mem),
            task.time.clone().into(),
            task.command.clone().into(),
        ])
    });

    let table = Table::new(
        rows,
        [
            Constraint::Length(6),
            Constraint::Length(8),
            Constraint::Length(6),
            Constraint::Length(6),
            Constraint::Length(10),
            Constraint::Percentage(100),
        ],
    )
    .header(Row::new(vec!["PID", "USER", "%CPU", "%MEM", "TIME+", "Command"]).style(
        Style::default().add_modifier(Modifier::BOLD),
    ))
    .block(Block::default().title("Top tasks").borders(Borders::ALL))
    .column_spacing(1);

    frame.render_widget(table, chunks[2]);
}

fn render_gpu_pane(frame: &mut Frame<'_>, area: Rect, gpu: &GpuPane) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(4), Constraint::Min(4)])
        .split(area);

    let header = Paragraph::new(gpu.header.join("\n"))
        .block(Block::default().title("GPU Summary").borders(Borders::ALL));
    frame.render_widget(header, chunks[0]);

    let rows = gpu.devices.iter().map(|device| {
        Row::new(vec![
            Cell::from(device.name),
            Cell::from(format!("{}°C", device.temp)),
            Cell::from(format!("{}%", device.utilization)),
            Cell::from(format!("{}%", device.memory_util)),
            Cell::from(format!("{}%", device.fan)),
            Cell::from(device.memory),
            Cell::from(device.process),
        ])
    });

    let table = Table::new(
        rows,
        [
            Constraint::Length(9),
            Constraint::Length(8),
            Constraint::Length(6),
            Constraint::Length(6),
            Constraint::Length(6),
            Constraint::Length(12),
            Constraint::Percentage(100),
        ],
    )
    .header(Row::new(vec![
        "GPU",
        "Temp",
        "GPU%",
        "Mem%",
        "Fan",
        "VRAM",
        "Process",
    ])
    .style(Style::default().add_modifier(Modifier::BOLD)))
    .block(Block::default().title("Devices").borders(Borders::ALL))
    .column_spacing(1);

    frame.render_widget(table, chunks[1]);
}

fn usage_bar(percent: u16, width: usize) -> String {
    let filled = (percent as usize * width) / 100;
    let empty = width.saturating_sub(filled);
    format!("{}{}", "█".repeat(filled), "░".repeat(empty))
}

fn percent(part: u64, total: u64) -> f64 {
    if total == 0 {
        0.0
    } else {
        part as f64 / total as f64 * 100.0
    }
}

fn format_bytes(kib: u64) -> String {
    const UNITS: [&str; 5] = ["KiB", "MiB", "GiB", "TiB", "PiB"];
    if kib == 0 {
        return "0 KiB".into();
    }
    let mut value = kib as f64;
    let mut unit_index = 0;
    while value >= 1024.0 && unit_index < UNITS.len() - 1 {
        value /= 1024.0;
        unit_index += 1;
    }
    if unit_index == 0 {
        format!("{value:.0} {}", UNITS[unit_index])
    } else {
        format!("{value:.1} {}", UNITS[unit_index])
    }
}

fn format_duration(seconds: u64) -> String {
    let days = seconds / 86_400;
    let hours = (seconds % 86_400) / 3_600;
    let minutes = (seconds % 3_600) / 60;
    let secs = seconds % 60;
    if days > 0 {
        format!("{days}d {hours:02}:{minutes:02}:{secs:02}")
    } else {
        format!("{hours:02}:{minutes:02}:{secs:02}")
    }
}

fn format_command(process: &Process) -> String {
    if process.cmd().is_empty() {
        process.name().to_string()
    } else {
        process.cmd().join(" ")
    }
}

fn centered_rect(percent_x: u16, percent_y: u16, area: Rect) -> Rect {
    let vertical = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y).saturating_div(2)),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y).saturating_div(2)),
        ])
        .split(area);

    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x).saturating_div(2)),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x).saturating_div(2)),
        ])
        .split(vertical[1])[1]
}

#[derive(Debug, Clone)]
struct Job {
    id: &'static str,
    name: &'static str,
    state: &'static str,
    user: &'static str,
    submitted: &'static str,
    runtime: &'static str,
    nodes: &'static str,
    workdir: &'static str,
    reason: &'static str,
}

struct ActionPane {
    title: &'static str,
    shortcut: Option<char>,
    content: PaneContent,
}

impl ActionPane {
    fn display_title(&self) -> String {
        if let Some(shortcut) = self.shortcut {
            format!("{} ({})", self.title, shortcut)
        } else {
            self.title.to_string()
        }
    }
}

enum PaneContent {
    Text(&'static str),
    Cpu,
    Gpu,
}

#[derive(Copy, Clone, PartialEq, Eq)]
enum JobsListFocus {
    Current,
    Past,
}

struct CpuPane {
    summary: Vec<String>,
    memory: Vec<String>,
    cores: Vec<CpuCore>,
    tasks: Vec<TaskRow>,
}

struct CpuCore {
    label: String,
    usage: u8,
}

struct TaskRow {
    pid: String,
    user: String,
    cpu: f32,
    mem: f32,
    time: String,
    command: String,
}

struct GpuPane {
    header: Vec<String>,
    devices: Vec<GpuDevice>,
}

struct GpuDevice {
    name: &'static str,
    temp: u8,
    utilization: u8,
    memory_util: u8,
    fan: u8,
    memory: &'static str,
    process: &'static str,
}

struct App {
    current_jobs: Vec<Job>,
    past_jobs: Vec<Job>,
    actions: Vec<ActionPane>,
    selected_current: usize,
    selected_past: usize,
    selected_action: usize,
    focused_jobs: JobsListFocus,
    system: System,
    cpu: CpuPane,
    gpu: GpuPane,
    last_metrics_update: Instant,
    show_help: bool,
}

impl App {
    fn default_jobs() -> (Vec<Job>, Vec<Job>) {
        let current = vec![
            Job {
                id: "581939",
                name: "deep-learning",
                state: "RUNNING",
                user: "alice",
                submitted: "2025-11-15T14:22",
                runtime: "02:13:11",
                nodes: "gpu[7-8]",
                workdir: "/scratch/alice",
                reason: "Scaling to 4 GPUs",
            },
            Job {
                id: "581940",
                name: "preprocess",
                state: "PENDING",
                user: "bob",
                submitted: "2025-11-16T09:03",
                runtime: "--",
                nodes: "cpu[1-2]",
                workdir: "/scratch/bob",
                reason: "Priority hold",
            },
        ];

        let past = vec![
            Job {
                id: "581901",
                name: "render",
                state: "COMPLETED",
                user: "carol",
                submitted: "2025-11-14T11:01",
                runtime: "00:43:08",
                nodes: "cpu[3-4]",
                workdir: "/proj/render",
                reason: "Finished successfully",
            },
            Job {
                id: "581875",
                name: "simulation",
                state: "FAILED",
                user: "dave",
                submitted: "2025-11-13T20:22",
                runtime: "06:10:55",
                nodes: "gpu[3]",
                workdir: "/proj/sim",
                reason: "Out of memory",
            },
        ];

        (current, past)
    }

    fn default_actions() -> Vec<ActionPane> {
        vec![
            ActionPane {
                title: "CPU Utilization",
                shortcut: Some('c'),
                content: PaneContent::Cpu,
            },
            ActionPane {
                title: "GPU Utilization",
                shortcut: Some('g'),
                content: PaneContent::Gpu,
            },
            ActionPane {
                title: "Error Log",
                shortcut: Some('e'),
                content: PaneContent::Text(
                    "Tail the stderr stream to quickly inspect why a job might be failing.",
                ),
            },
            ActionPane {
                title: "Output Log",
                shortcut: Some('o'),
                content: PaneContent::Text(
                    "Peek at stdout for quick progress updates or metrics your job emits.",
                ),
            },
            ActionPane {
                title: "Job Script",
                shortcut: Some('j'),
                content: PaneContent::Text(
                    "Review the exact sbatch script and resource requests submitted to Slurm.",
                ),
            },
        ]
    }

    fn focused_job(&self) -> &Job {
        match self.focused_jobs {
            JobsListFocus::Current => self
                .current_jobs
                .get(self.selected_current)
                .or_else(|| self.past_jobs.get(self.selected_past))
                .expect("there is at least one job"),
            JobsListFocus::Past => self
                .past_jobs
                .get(self.selected_past)
                .or_else(|| self.current_jobs.get(self.selected_current))
                .expect("there is at least one job"),
        }
    }

    fn current_action(&self) -> &ActionPane {
        &self.actions[self.selected_action]
    }

    fn next_action(&mut self) {
        self.selected_action = (self.selected_action + 1) % self.actions.len();
    }

    fn previous_action(&mut self) {
        if self.selected_action == 0 {
            self.selected_action = self.actions.len() - 1;
        } else {
            self.selected_action -= 1;
        }
    }

    fn next_job(&mut self) {
        match self.focused_jobs {
            JobsListFocus::Current => {
                if !self.current_jobs.is_empty() {
                    self.selected_current = (self.selected_current + 1) % self.current_jobs.len();
                }
            }
            JobsListFocus::Past => {
                if !self.past_jobs.is_empty() {
                    self.selected_past = (self.selected_past + 1) % self.past_jobs.len();
                }
            }
        }
    }

    fn previous_job(&mut self) {
        match self.focused_jobs {
            JobsListFocus::Current => {
                if !self.current_jobs.is_empty() {
                    if self.selected_current == 0 {
                        self.selected_current = self.current_jobs.len() - 1;
                    } else {
                        self.selected_current -= 1;
                    }
                }
            }
            JobsListFocus::Past => {
                if !self.past_jobs.is_empty() {
                    if self.selected_past == 0 {
                        self.selected_past = self.past_jobs.len() - 1;
                    } else {
                        self.selected_past -= 1;
                    }
                }
            }
        }
    }

    fn focus_next_jobs_list(&mut self) {
        self.focused_jobs = JobsListFocus::Past;
    }

    fn focus_previous_jobs_list(&mut self) {
        self.focused_jobs = JobsListFocus::Current;
    }

    fn activate_action_by_char(&mut self, input: char) {
        let needle = input.to_ascii_lowercase();
        if let Some(idx) = self
            .actions
            .iter()
            .position(|pane| pane.shortcut.map(|c| c == needle).unwrap_or(false))
        {
            self.selected_action = idx;
        }
    }

    fn action_shortcuts(&self) -> Vec<(char, &'static str)> {
        self.actions
            .iter()
            .filter_map(|pane| pane.shortcut.map(|c| (c, pane.title)))
            .collect()
    }

    fn refresh_metrics(&mut self) {
        if self
            .last_metrics_update
            .elapsed()
            .lt(&std::time::Duration::from_millis(1000))
        {
            return;
        }

        self.system
            .refresh_cpu();
        self.system.refresh_memory();
        self.system.refresh_processes();
        self.cpu = CpuPane::from_system(&self.system);
        self.last_metrics_update = Instant::now();
    }
}

impl Default for App {
    fn default() -> Self {
        let (current_jobs, past_jobs) = Self::default_jobs();
        let actions = Self::default_actions();
        let mut system = System::new_all();
        system.refresh_all();
        let cpu = CpuPane::from_system(&system);
        let gpu = GpuPane::mock();
        Self {
            current_jobs,
            past_jobs,
            actions,
            selected_current: 0,
            selected_past: 0,
            selected_action: 0,
            focused_jobs: JobsListFocus::Current,
            system,
            cpu,
            gpu,
            last_metrics_update: Instant::now(),
            show_help: false,
        }
    }
}

impl CpuPane {
    fn from_system(system: &System) -> Self {
        let processes = system.processes();
        let total_tasks = processes.len();
        let running = processes
            .values()
            .filter(|proc| matches!(proc.status(), ProcessStatus::Run))
            .count();
        let sleeping = processes
            .values()
            .filter(|proc| matches!(
                proc.status(),
                ProcessStatus::Sleep | ProcessStatus::Idle
            ))
            .count();
        let stopped = processes
            .values()
            .filter(|proc| matches!(
                proc.status(),
                ProcessStatus::Stop | ProcessStatus::Zombie
            ))
            .count();

        let load = System::load_average();
        let cpu_count = system.cpus().len();
        let summary = vec![
            format!(
                "Tasks: {total_tasks} total, {running} running, {sleeping} sleeping, {stopped} stopped"
            ),
            format!(
                "Load average: {:.2} {:.2} {:.2} ({}-core host)",
                load.one, load.five, load.fifteen, cpu_count.max(1)
            ),
            format!("Uptime: {}", format_duration(System::uptime())),
        ];

        let total_mem = system.total_memory();
        let used_mem = system.used_memory();
        let total_swap = system.total_swap();
        let used_swap = system.used_swap();

        let memory = vec![
            format!(
                "Mem: {} used / {} total ({:.0}%)",
                format_bytes(used_mem),
                format_bytes(total_mem),
                percent(used_mem, total_mem)
            ),
            format!(
                "Swap: {} used / {} total ({:.0}%)",
                format_bytes(used_swap),
                format_bytes(total_swap),
                percent(used_swap, total_swap)
            ),
        ];

        let cores = system
            .cpus()
            .iter()
            .enumerate()
            .map(|(idx, cpu)| CpuCore {
                label: format!("CPU{idx}"),
                usage: cpu.cpu_usage().round().clamp(0.0, 100.0) as u8,
            })
            .collect();

        let mut processes_sorted: Vec<_> = processes.values().collect();
        processes_sorted.sort_by(|a, b| {
            b.cpu_usage()
                .partial_cmp(&a.cpu_usage())
                .unwrap_or(Ordering::Equal)
        });

        let total_mem_f = total_mem.max(1) as f64;

        let tasks = processes_sorted
            .into_iter()
            .take(8)
            .map(|process| {
                let user = process
                    .user_id()
                    .map(|id| id.to_string())
                    .unwrap_or_else(|| "-".into());

                TaskRow {
                    pid: process.pid().to_string(),
                    user,
                    cpu: process.cpu_usage(),
                    mem: ((process.memory() as f64 / total_mem_f) * 100.0) as f32,
                    time: format_duration(process.run_time()),
                    command: format_command(process),
                }
            })
            .collect();

        Self {
            summary,
            memory,
            cores,
            tasks,
        }
    }
}

impl GpuPane {
    fn mock() -> Self {
        Self {
            header: vec![
                "Driver: 550.40.07 | CUDA 12.6".into(),
                "Power draw: 410 W / 520 W (node gpu[7-8])".into(),
            ],
            devices: vec![
                GpuDevice {
                    name: "GPU0 A100",
                    temp: 64,
                    utilization: 92,
                    memory_util: 78,
                    fan: 54,
                    memory: "62 / 80 GB",
                    process: "python (alice)",
                },
                GpuDevice {
                    name: "GPU1 A100",
                    temp: 59,
                    utilization: 73,
                    memory_util: 65,
                    fan: 47,
                    memory: "51 / 80 GB",
                    process: "python (bob)",
                },
                GpuDevice {
                    name: "GPU2 A100",
                    temp: 35,
                    utilization: 6,
                    memory_util: 12,
                    fan: 30,
                    memory: "10 / 80 GB",
                    process: "idle",
                },
            ],
        }
    }
}

#[cfg(feature = "python")]
mod python_bindings {
    use super::*;
    use pyo3::{exceptions::PyRuntimeError, prelude::*, wrap_pyfunction};

    #[pyfunction]
    fn launch() -> PyResult<()> {
        run().map_err(|err| PyRuntimeError::new_err(err.to_string()))
    }

    #[pymodule]
    fn _slurmtop(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(launch, m)?)?;
        Ok(())
    }
}
