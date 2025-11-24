use std::{
    env,
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    process::Command,
    time::{Duration, Instant},
};

use chrono::{Duration as ChronoDuration, Utc};
use color_eyre::eyre::{eyre, Context, Result};

const DEFAULT_REFRESH_INTERVAL: Duration = Duration::from_secs(5);
const DEFAULT_LOOKBACK_HOURS: i64 = 72;
const JOB_LIMIT: usize = 64;
const LOG_TAIL_BYTES: u64 = 32 * 1024;
const SCRIPT_PREVIEW_BYTES: u64 = 64 * 1024;

#[derive(Debug, Clone, Default)]
pub struct Job {
    pub id: String,
    pub name: String,
    pub state: String,
    pub user: String,
    pub submitted: String,
    pub runtime: String,
    pub nodes: String,
    pub workdir: String,
    pub reason: String,
}

#[derive(Debug, Clone, Default)]
pub struct JobDetails {
    pub job_id: String,
    pub scontrol: String,
    pub stdout_path: Option<String>,
    pub stderr_path: Option<String>,
    pub script_path: Option<String>,
    pub stdout_preview: String,
    pub stderr_preview: String,
    pub script_preview: String,
}

#[derive(Debug, Clone, Default)]
pub struct SlurmSnapshot {
    pub current_jobs: Vec<Job>,
    pub past_jobs: Vec<Job>,
}

pub struct SlurmBackend {
    refresh_interval: Duration,
    lookback_hours: i64,
    last_fetch: Option<Instant>,
    cache: SlurmSnapshot,
}

impl SlurmBackend {
    pub fn new() -> Self {
        Self {
            refresh_interval: DEFAULT_REFRESH_INTERVAL,
            lookback_hours: DEFAULT_LOOKBACK_HOURS,
            last_fetch: None,
            cache: SlurmSnapshot::default(),
        }
    }

    pub fn force_refresh(&mut self) -> Result<&SlurmSnapshot> {
        self.last_fetch = None;
        self.refresh_if_needed()
    }

    pub fn refresh_if_needed(&mut self) -> Result<&SlurmSnapshot> {
        let should_refresh = self
            .last_fetch
            .map(|ts| ts.elapsed() >= self.refresh_interval)
            .unwrap_or(true);

        if should_refresh {
            let snapshot = SlurmSnapshot {
                current_jobs: fetch_current_jobs(JOB_LIMIT)?,
                past_jobs: fetch_recent_jobs(JOB_LIMIT, self.lookback_hours)?,
            };
            self.cache = snapshot;
            self.last_fetch = Some(Instant::now());
        }

        Ok(&self.cache)
    }
}

impl Default for SlurmBackend {
    fn default() -> Self {
        Self::new()
    }
}

fn fetch_current_jobs(limit: usize) -> Result<Vec<Job>> {
    let user_filter = desired_user_filter();
    let filter_slice = user_filter.as_ref().map(|list| list.as_slice());
    let format = "%i|%j|%T|%u|%V|%M|%N|%Z|%R";

    let mut owned_args: Vec<String> = Vec::new();
    let mut args: Vec<&str> = vec!["-h", "-o", format, "-S", "t"];
    if let Some(users) = filter_slice {
        for user in users {
            owned_args.push("-u".into());
            owned_args.push(user.clone());
        }
        args.extend(owned_args.iter().map(|value| value.as_str()));
    }

    let stdout = run_command("squeue", &args)?;

    let mut jobs = Vec::new();
    for line in stdout.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Some(fields) = parse_fields(line, 9) {
            if is_step(&fields[0]) {
                continue;
            }
            if !should_include_user(&fields[3], filter_slice) {
                continue;
            }
            jobs.push(Job {
                id: fields[0].clone(),
                name: fields[1].clone(),
                state: fields[2].clone(),
                user: fields[3].clone(),
                submitted: fields[4].clone(),
                runtime: fields[5].clone(),
                nodes: fields[6].clone(),
                workdir: fields[7].clone(),
                reason: fields[8].clone(),
            });
            if jobs.len() >= limit {
                break;
            }
        }
    }

    Ok(jobs)
}

fn fetch_recent_jobs(limit: usize, lookback_hours: i64) -> Result<Vec<Job>> {
    let user_filter = desired_user_filter();
    let filter_slice = user_filter.as_ref().map(|list| list.as_slice());
    let start_time = Utc::now() - ChronoDuration::hours(lookback_hours);
    let start_arg = format!("--starttime={}", start_time.format("%Y-%m-%dT%H:%M:%S"));
    let format = "JobIDRaw|JobName|State|User|Submit|Elapsed|NodeList|WorkDir|DerivedExitCode";
    let mut owned_args: Vec<String> = Vec::new();
    let mut args: Vec<&str> = vec![
        "-n",
        "-P",
        "-X",
        "--state=COMPLETED,FAILED,CANCELLED,TIMEOUT",
        "--format",
        format,
        start_arg.as_str(),
    ];
    if let Some(users) = filter_slice {
        owned_args.push("--user".into());
        owned_args.push(users.join(","));
        args.extend(owned_args.iter().map(|value| value.as_str()));
    }

    let stdout = run_command("sacct", &args)?;

    let mut jobs = Vec::new();
    for line in stdout.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Some(fields) = parse_fields(line, 9) {
            if is_step(&fields[0]) {
                continue;
            }
            if !should_include_user(&fields[3], filter_slice) {
                continue;
            }
            jobs.push(Job {
                id: fields[0].clone(),
                name: fields[1].clone(),
                state: fields[2].clone(),
                user: fields[3].clone(),
                submitted: fields[4].clone(),
                runtime: fields[5].clone(),
                nodes: fields[6].clone(),
                workdir: fields[7].clone(),
                reason: if fields[8] == "-" {
                    fields[2].clone()
                } else {
                    format!("{} (exit {})", fields[2], fields[8])
                },
            });
            if jobs.len() >= limit {
                break;
            }
        }
    }

    // Sort newest first
    jobs.sort_by(|a, b| b.submitted.cmp(&a.submitted));
    Ok(jobs)
}

pub fn fetch_job_details(job: &Job) -> Result<JobDetails> {
    let job_id = job.id.as_str();
    let scontrol = run_command("scontrol", &["show", "jobid", "-dd", job_id])?;

    let workdir_hint = normalize_path(extract_field(&scontrol, "WorkDir"));

    let stdout_path = resolve_job_path(
        job,
        workdir_hint.as_deref(),
        normalize_path(extract_field(&scontrol, "StdOut")),
    );
    let stderr_path = resolve_job_path(
        job,
        workdir_hint.as_deref(),
        normalize_path(extract_field(&scontrol, "StdErr")),
    );
    let script_path = resolve_job_path(
        job,
        workdir_hint.as_deref(),
        normalize_path(extract_field(&scontrol, "Command")),
    );

    let stdout_preview = preview_tail(stdout_path.as_deref(), LOG_TAIL_BYTES);
    let stderr_preview = preview_tail(stderr_path.as_deref(), LOG_TAIL_BYTES);
    let script_preview = preview_head(script_path.as_deref(), SCRIPT_PREVIEW_BYTES);

    Ok(JobDetails {
        job_id: job_id.to_string(),
        scontrol,
        stdout_path,
        stderr_path,
        script_path,
        stdout_preview,
        stderr_preview,
        script_preview,
    })
}

fn run_command(cmd: &str, args: &[&str]) -> Result<String> {
    let output = Command::new(cmd)
        .args(args)
        .output()
        .with_context(|| format!("failed to execute {cmd}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(eyre!("{cmd} failed: {stderr}"));
    }

    String::from_utf8(output.stdout)
        .map(|s| s.trim_end().to_string())
        .with_context(|| format!("{cmd} produced invalid UTF-8"))
}

fn parse_fields(line: &str, expected: usize) -> Option<Vec<String>> {
    let mut parts: Vec<String> = line.split('|').map(|part| sanitize(part)).collect();
    if parts.len() < expected {
        parts.resize(expected, String::new());
    }
    Some(parts)
}

fn sanitize(value: &str) -> String {
    if value.trim().is_empty() {
        "-".into()
    } else {
        value.trim().to_string()
    }
}

fn is_step(job_id: &str) -> bool {
    job_id.contains('.')
}

fn extract_field(output: &str, key: &str) -> Option<String> {
    output
        .split_whitespace()
        .find_map(|token| token.strip_prefix(&format!("{key}=")))
        .map(|value| value.trim().to_string())
}

fn normalize_path(value: Option<String>) -> Option<String> {
    value.and_then(|v| {
        let trimmed = v.trim();
        if trimmed.is_empty()
            || trimmed.eq_ignore_ascii_case("unknown")
            || trimmed.eq_ignore_ascii_case("none")
            || trimmed.eq_ignore_ascii_case("stdin")
            || trimmed.eq_ignore_ascii_case("stdout")
            || trimmed.eq_ignore_ascii_case("stderr")
        {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

fn resolve_job_path(job: &Job, workdir_hint: Option<&str>, raw: Option<String>) -> Option<String> {
    let Some(raw_value) = raw else {
        return None;
    };
    let expanded = expand_slurm_placeholders(&raw_value, job);
    Some(absolutize_path(&expanded, workdir_hint, job))
}

fn absolutize_path(path: &str, workdir_hint: Option<&str>, job: &Job) -> String {
    let candidate = Path::new(path);
    if candidate.is_absolute() {
        return path.to_string();
    }

    let workdir_chain = [workdir_hint, Some(job.workdir.as_str())];
    for dir in workdir_chain.into_iter().flatten() {
        if let Some(base) = parse_workdir(dir) {
            return base.join(path).to_string_lossy().into_owned();
        }
    }

    path.to_string()
}

fn parse_workdir(path: &str) -> Option<PathBuf> {
    let trimmed = path.trim();
    if trimmed.is_empty() || trimmed == "-" {
        return None;
    }
    let candidate = Path::new(trimmed);
    if candidate.is_absolute() {
        Some(candidate.to_path_buf())
    } else {
        None
    }
}

fn expand_slurm_placeholders(template: &str, job: &Job) -> String {
    let (array_master, array_task) = parse_array_components(&job.id);
    let array_task = array_task.unwrap_or_else(|| "0".into());
    let first_node = job
        .nodes
        .split(|c| c == ',' || c == '+')
        .find(|chunk| !chunk.trim().is_empty())
        .map(|chunk| chunk.to_string())
        .unwrap_or_else(|| job.nodes.clone());

    let mut chars = template.chars().peekable();
    let mut output = String::with_capacity(template.len());
    while let Some(ch) = chars.next() {
        if ch != '%' {
            output.push(ch);
            continue;
        }

        match chars.next() {
            Some('%') => output.push('%'),
            Some(code) => {
                let replacement = match code {
                    'A' => array_master.as_str(),
                    'a' => array_task.as_str(),
                    'J' | 'j' => job.id.as_str(),
                    'u' | 'U' => job.user.as_str(),
                    'x' | 'X' => job.name.as_str(),
                    'N' => first_node.as_str(),
                    't' | 'T' => job.state.as_str(),
                    'n' | 's' => "0",
                    _ => {
                        output.push('%');
                        output.push(code);
                        continue;
                    }
                };
                output.push_str(replacement);
            }
            None => output.push('%'),
        }
    }

    output
}

fn parse_array_components(job_id: &str) -> (String, Option<String>) {
    if let Some((base, task)) = job_id.split_once('_') {
        (base.to_string(), Some(task.to_string()))
    } else {
        (job_id.to_string(), None)
    }
}

fn desired_user_filter() -> Option<Vec<String>> {
    if show_all_jobs() {
        return None;
    }

    env_user_values("SLURMTOP_USER")
        .or_else(|| env_user_values("SLURM_JOB_USER"))
        .or_else(|| env_user_values("USER"))
        .or_else(|| env_user_values("LOGNAME"))
        .or_else(fallback_user_from_command)
}

fn env_user_values(key: &str) -> Option<Vec<String>> {
    env::var(key).ok().and_then(|value| parse_user_list(&value))
}

fn fallback_user_from_command() -> Option<Vec<String>> {
    run_command("id", &["-un"])
        .ok()
        .and_then(|value| parse_user_list(&value))
}

fn parse_user_list(raw: &str) -> Option<Vec<String>> {
    let tokens: Vec<String> = raw
        .split(|ch: char| ch == ',' || ch == ' ')
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
        .map(|value| value.to_string())
        .collect();

    if tokens.is_empty() {
        None
    } else {
        Some(tokens)
    }
}

fn show_all_jobs() -> bool {
    matches!(env::var("SLURMTOP_ALL_JOBS"), Ok(value) if value == "1" || value.eq_ignore_ascii_case("true"))
}

fn should_include_user(user: &str, filter: Option<&[String]>) -> bool {
    match filter {
        Some(expected) => expected.iter().any(|candidate| candidate == user),
        None => true,
    }
}

fn preview_tail(path: Option<&str>, limit: u64) -> String {
    match path {
        Some(p) => match read_tail(p, limit) {
            Ok(mut body) => {
                if body.trim().is_empty() {
                    body = "(file is empty)".into();
                }
                body
            }
            Err(err) => format!("Failed to read {p}: {err}"),
        },
        None => "No file associated with this job".into(),
    }
}

fn preview_head(path: Option<&str>, limit: u64) -> String {
    match path {
        Some(p) => match read_head(p, limit) {
            Ok(mut body) => {
                if body.trim().is_empty() {
                    body = "(file is empty)".into();
                }
                body
            }
            Err(err) => format!("Failed to read {p}: {err}"),
        },
        None => "No script associated with this job".into(),
    }
}

fn read_tail(path: &str, limit: u64) -> Result<String> {
    let mut file = File::open(path).with_context(|| format!("opening {path}"))?;
    let metadata = file
        .metadata()
        .with_context(|| format!("reading metadata for {path}"))?;
    let len = metadata.len();
    if len > limit {
        file.seek(SeekFrom::End(-(limit as i64)))
            .with_context(|| format!("seeking within {path}"))?;
    }
    let mut buffer = Vec::new();
    file.read_to_end(&mut buffer)
        .with_context(|| format!("reading {path}"))?;
    let mut text = String::from_utf8_lossy(&buffer).to_string();
    if len > limit {
        if let Some(idx) = text.find('\n') {
            text = text[idx + 1..].to_string();
        }
        text = format!("…tail of {path} (last {} bytes)…\n{text}", limit);
    }
    Ok(text)
}

fn read_head(path: &str, limit: u64) -> Result<String> {
    let mut file = File::open(path).with_context(|| format!("opening {path}"))?;
    let mut buffer = Vec::new();
    {
        let mut limited = (&mut file).take(limit);
        limited
            .read_to_end(&mut buffer)
            .with_context(|| format!("reading {path}"))?;
    }
    let total_len = file
        .metadata()
        .with_context(|| format!("reading metadata for {path}"))?
        .len();
    let mut text = String::from_utf8_lossy(&buffer).to_string();
    if total_len > limit {
        text.push_str(&format!("\n…truncated to first {} bytes…", limit));
    }
    Ok(text)
}
