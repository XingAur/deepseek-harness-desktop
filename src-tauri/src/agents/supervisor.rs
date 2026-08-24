use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    fs,
    path::{Path, PathBuf},
    sync::{Arc, atomic::{AtomicU64, Ordering}},
    time::{Duration, Instant},
};

use serde::Serialize;
use serde_json::{Value, json};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWriteExt},
    process::{Child, ChildStdin, ChildStdout, Command},
    sync::{Mutex, oneshot},
    task::JoinHandle,
};
use zeroize::{Zeroize, Zeroizing};

#[cfg(windows)]
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE},
    System::{
        Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, TH32CS_SNAPTHREAD, THREADENTRY32, Thread32First, Thread32Next,
        },
        JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
            SetInformationJobObject, TerminateJobObject,
        },
        Threading::{
            OpenProcess, OpenThread, PROCESS_SET_QUOTA, PROCESS_TERMINATE, ResumeThread,
            THREAD_SUSPEND_RESUME,
        },
    },
};

use super::model::{ADAPTER_PROTOCOL_VERSION, AGENT_EVENT_CHANNEL, AgentEventEnvelope};
use crate::credentials::model::SecretValue;

const HANDSHAKE_REQUEST_ID: &str = "handshake";
const SECRET_INIT_REQUEST_ID: &str = "adapter-init";

#[derive(Serialize)]
struct SecretInitFrame<'a> {
    #[serde(rename = "protocolVersion")]
    protocol_version: &'static str,
    #[serde(rename = "requestId")]
    request_id: &'static str,
    #[serde(rename = "sessionId")]
    session_id: &'static str,
    sequence: u64,
    #[serde(rename = "type")]
    frame_type: &'static str,
    payload: SecretInitPayload<'a>,
}

#[derive(Serialize)]
struct SecretInitPayload<'a> {
    #[serde(rename = "credentialId")]
    credential_id: &'a str,
    secret: &'a str,
}

#[derive(Clone, Debug)]
pub struct SupervisorConfig {
    pub adapter_path: PathBuf,
    pub adapter_args: Vec<String>,
    pub adapter_kind: String,
    pub cwd: PathBuf,
    pub allowed_executables: BTreeSet<PathBuf>,
    pub env: BTreeMap<String, String>,
    pub handshake_timeout: Duration,
    pub heartbeat_timeout: Duration,
    pub output_limit: usize,
    pub restart_limit: u8,
}

impl SupervisorConfig {
    pub fn new(adapter_path: PathBuf, cwd: PathBuf) -> Self {
        Self {
            adapter_path,
            adapter_args: Vec::new(),
            adapter_kind: "mock".to_owned(),
            cwd,
            allowed_executables: BTreeSet::new(),
            env: BTreeMap::new(),
            handshake_timeout: Duration::from_secs(5),
            heartbeat_timeout: Duration::from_secs(30),
            output_limit: 32 * 1024,
            restart_limit: 0,
        }
    }

    pub fn with_allowed_executables<I, P>(mut self, paths: I) -> Self
    where
        I: IntoIterator<Item = P>,
        P: Into<PathBuf>,
    {
        self.allowed_executables = paths.into_iter().map(Into::into).collect();
        self
    }

    pub fn with_adapter_args(mut self, args: impl IntoIterator<Item = String>) -> Self {
        self.adapter_args = args.into_iter().collect();
        self
    }

    pub fn with_adapter_kind(mut self, kind: impl Into<String>) -> Self {
        self.adapter_kind = kind.into();
        self
    }

    pub fn with_env(mut self, env: BTreeMap<String, String>) -> Self {
        self.env = env;
        self
    }

    pub fn with_handshake_timeout(mut self, timeout: Duration) -> Self {
        self.handshake_timeout = timeout;
        self
    }

    pub fn with_heartbeat_timeout(mut self, timeout: Duration) -> Self {
        self.heartbeat_timeout = timeout;
        self
    }

    pub fn with_output_limit(mut self, limit: usize) -> Self {
        self.output_limit = limit.max(128);
        self
    }

    pub fn with_restart_limit(mut self, limit: u8) -> Self {
        self.restart_limit = limit.min(3);
        self
    }
}

#[derive(Clone, Debug)]
pub struct WorkerSupervisor {
    config: SupervisorConfig,
}

impl WorkerSupervisor {
    pub fn new(config: SupervisorConfig) -> Self {
        Self { config }
    }

    pub fn with_adapter_path(mut self, path: PathBuf) -> Self {
        self.config.adapter_path = path;
        self
    }

    pub async fn launch(&self, session_id: &str) -> Result<WorkerSession, SupervisorError> {
        let attempts = self.config.restart_limit as usize + 1;
        let mut last_error = None;
        for attempt in 0..attempts {
            match self.launch_once(session_id).await {
                Ok(session) => return Ok(session),
                Err(error)
                    if attempt + 1 < attempts && matches!(error, SupervisorError::WorkerExited) =>
                {
                    last_error = Some(error);
                }
                Err(error) => return Err(error),
            }
        }
        Err(last_error.unwrap_or(SupervisorError::WorkerExited))
    }

    async fn launch_once(&self, session_id: &str) -> Result<WorkerSession, SupervisorError> {
        if !self.config.cwd.is_dir() {
            return Err(SupervisorError::WorkingDirectoryUnavailable);
        }
        let adapter_path = fs::canonicalize(&self.config.adapter_path)
            .map_err(|_| SupervisorError::ExecutableUnavailable)?;
        let allowlisted = self
            .config
            .allowed_executables
            .iter()
            .filter_map(|path| fs::canonicalize(path).ok())
            .any(|path| path == adapter_path);
        if !allowlisted {
            return Err(SupervisorError::ExecutableNotAllowlisted);
        }
        if self
            .config
            .env
            .keys()
            .any(|key| is_secret_like_environment_name(key))
        {
            return Err(SupervisorError::SecretEnvironmentRejected);
        }

        let mut command = worker_command(&adapter_path, &self.config.adapter_args)?;
        command
            .current_dir(&self.config.cwd)
            .env_clear()
            .envs(&self.config.env)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);
        #[cfg(unix)]
        command.process_group(0);
        #[cfg(windows)]
        command.creation_flags(0x0800_0000 | 0x0000_0004);
        let mut child = command.spawn().map_err(|_| SupervisorError::SpawnFailed)?;
        let pid = child.id().ok_or(SupervisorError::SpawnFailed)?;
        #[cfg(windows)]
        let worker_job = match WorkerJob::create(pid) {
            Ok(job) => job,
            Err(error) => {
                let _ = child.start_kill();
                let _ = child.wait().await;
                return Err(error);
            }
        };
        #[cfg(windows)]
        if let Err(error) = worker_job.resume(pid) {
            worker_job.terminate();
            let _ = child.start_kill();
            let _ = child.wait().await;
            return Err(error);
        }
        let stdin = child.stdin.take().ok_or(SupervisorError::SpawnFailed)?;
        let stdout = child.stdout.take().ok_or(SupervisorError::SpawnFailed)?;
        let stderr = child.stderr.take().ok_or(SupervisorError::SpawnFailed)?;
        let stderr_task = tokio::spawn(drain_bounded(stderr, self.config.output_limit));
        let mut session = WorkerSession {
            child: Arc::new(Mutex::new(child)),
            stdin: Arc::new(Mutex::new(stdin)),
            stdout,
            stderr_task: Some(stderr_task),
            pid,
            output_limit: self.config.output_limit,
            adapter_kind: self.config.adapter_kind.clone(),
            session_id: session_id.to_owned(),
            input_sequence: Arc::new(AtomicU64::new(1)),
            pending_responses: Arc::new(Mutex::new(HashMap::new())),
            heartbeat_timeout: self.config.heartbeat_timeout,
            last_heartbeat: Instant::now(),
            secret_initialized: false,
            #[cfg(windows)]
            worker_job,
        };
        if let Err(error) = session
            .handshake(session_id, self.config.handshake_timeout)
            .await
        {
            session.cancel().await?;
            return Err(error);
        }
        Ok(session)
    }
}

fn worker_command(adapter_path: &Path, args: &[String]) -> Result<Command, SupervisorError> {
    #[cfg(windows)]
    if adapter_path.extension().is_some_and(|extension| {
        extension.eq_ignore_ascii_case("cmd") || extension.eq_ignore_ascii_case("bat")
    }) {
        if adapter_path
            .to_string_lossy()
            .chars()
            .any(is_unsafe_cmd_character)
            || args
                .iter()
                .any(|arg| arg.chars().any(is_unsafe_cmd_character))
        {
            return Err(SupervisorError::UnsafeWindowsCommandLine);
        }
        let shell = std::env::var_os("COMSPEC").unwrap_or_else(|| "cmd.exe".into());
        let mut command_line = quote_windows_argument(&adapter_path.to_string_lossy());
        for arg in args {
            command_line.push(' ');
            command_line.push_str(&quote_windows_argument(arg));
        }
        let mut command = Command::new(shell);
        command.args(["/D", "/S", "/C"]).arg(command_line);
        return Ok(command);
    }
    let mut command = Command::new(adapter_path);
    command.args(args);
    Ok(command)
}

#[cfg(windows)]
fn is_unsafe_cmd_character(character: char) -> bool {
    matches!(
        character,
        '&' | '|'
            | '<'
            | '>'
            | '^'
            | '%'
            | '!'
            | '('
            | ')'
            | ';'
            | ','
            | '`'
            | '"'
            | '\r'
            | '\n'
            | '\0'
    )
}

#[cfg(windows)]
fn quote_windows_argument(value: &str) -> String {
    if !value.chars().any(char::is_whitespace) && !value.contains('"') {
        return value.to_owned();
    }
    format!("\"{}\"", value.replace('"', "\\\""))
}

#[derive(Debug)]
pub struct WorkerSession {
    child: Arc<Mutex<Child>>,
    stdin: Arc<Mutex<ChildStdin>>,
    stdout: ChildStdout,
    stderr_task: Option<JoinHandle<Result<String, SupervisorError>>>,
    pid: u32,
    output_limit: usize,
    adapter_kind: String,
    session_id: String,
    input_sequence: Arc<AtomicU64>,
    pending_responses: Arc<Mutex<HashMap<String, oneshot::Sender<Result<(), SupervisorError>>>>>,
    heartbeat_timeout: Duration,
    last_heartbeat: Instant,
    secret_initialized: bool,
    #[cfg(windows)]
    worker_job: WorkerJob,
}

#[cfg(windows)]
#[derive(Debug)]
struct WorkerJob {
    handle: HANDLE,
}

#[derive(Clone)]
pub struct ApprovalControl {
    stdin: Arc<Mutex<ChildStdin>>,
    input_sequence: Arc<AtomicU64>,
    output_limit: usize,
    session_id: String,
    child: Arc<Mutex<Child>>,
    pid: u32,
    pending_responses: Arc<Mutex<HashMap<String, oneshot::Sender<Result<(), SupervisorError>>>>>,
    response_timeout: Duration,
}

impl ApprovalControl {
    pub fn session_id(&self) -> &str {
        &self.session_id
    }

    async fn cancel_worker(&self) -> Result<(), SupervisorError> {
        let mut child = self.child.lock().await;
        let terminate_failed = crate::runtime::process_cleanup::terminate_worker_process_tree(self.pid)
            .is_err();
        let wait = tokio::time::timeout(Duration::from_secs(2), child.wait()).await;
        match wait {
            Ok(Ok(_)) => {
                if terminate_failed {
                    return Err(SupervisorError::CleanupFailed);
                }
                Ok(())
            }
            Ok(Err(_)) | Err(_) => {
                let force_failed = crate::runtime::process_cleanup::force_terminate_worker_process_tree(self.pid)
                    .is_err();
                let kill_failed = child.start_kill().is_err();
                let wait = tokio::time::timeout(Duration::from_secs(2), child.wait()).await;
                if force_failed || kill_failed || !matches!(wait, Ok(Ok(_))) {
                    return Err(SupervisorError::CleanupFailed);
                }
                Ok(())
            }
        }
    }

    pub async fn resolve_approval(
        &self,
        approval_id: &str,
        approved: bool,
    ) -> Result<(), SupervisorError> {
        if !is_safe_identifier(approval_id) {
            return Err(SupervisorError::Protocol);
        }
        let request_id = format!("approval-{approval_id}");
        let (sender, receiver) = oneshot::channel();
        if self
            .pending_responses
            .lock()
            .await
            .insert(request_id.clone(), sender)
            .is_some()
        {
            return Err(SupervisorError::Protocol);
        }
        let sequence = self.input_sequence.fetch_add(1, Ordering::Relaxed);
        let frame = approval_resolution_frame(&self.session_id, approval_id, sequence, approved);
        let mut bytes = serde_json::to_vec(&frame).map_err(|_| SupervisorError::Protocol)?;
        if bytes.len() + 1 > self.output_limit {
            self.pending_responses.lock().await.remove(&request_id);
            return Err(SupervisorError::OutputLimitExceeded);
        }
        bytes.push(b'\n');
        let mut stdin = self.stdin.lock().await;
        if stdin.write_all(&bytes).await.is_err() || stdin.flush().await.is_err() {
            drop(stdin);
            self.pending_responses.lock().await.remove(&request_id);
            return Err(SupervisorError::WorkerExited);
        }
        drop(stdin);
        match tokio::time::timeout(self.response_timeout, receiver).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err(SupervisorError::WorkerExited),
            Err(_) => {
                self.pending_responses.lock().await.remove(&request_id);
                self.cancel_worker().await?;
                Err(SupervisorError::HeartbeatLost)
            }
        }
    }
}

#[cfg(windows)]
impl WorkerJob {
    fn create(pid: u32) -> Result<Self, SupervisorError> {
        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err(SupervisorError::SpawnFailed);
        }
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                std::ptr::addr_of!(limits).cast(),
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if configured == 0 {
            unsafe {
                CloseHandle(handle);
            }
            return Err(SupervisorError::SpawnFailed);
        }

        let process = unsafe { OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid) };
        if process.is_null() {
            unsafe {
                CloseHandle(handle);
            }
            return Err(SupervisorError::SpawnFailed);
        }
        let assigned = unsafe { AssignProcessToJobObject(handle, process) };
        unsafe {
            CloseHandle(process);
        }
        if assigned == 0 {
            unsafe {
                CloseHandle(handle);
            }
            return Err(SupervisorError::SpawnFailed);
        }
        Ok(Self { handle })
    }

    fn terminate(&self) {
        unsafe {
            let _ = TerminateJobObject(self.handle, 1);
        }
    }

    fn resume(&self, pid: u32) -> Result<(), SupervisorError> {
        let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
        if snapshot == INVALID_HANDLE_VALUE {
            return Err(SupervisorError::SpawnFailed);
        }
        let mut entry = THREADENTRY32 {
            dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
            ..Default::default()
        };
        let mut resumed = false;
        let mut has_entry = unsafe { Thread32First(snapshot, &mut entry) } != 0;
        while has_entry {
            if entry.th32OwnerProcessID == pid {
                let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
                if !thread.is_null() {
                    let result = unsafe { ResumeThread(thread) };
                    unsafe {
                        CloseHandle(thread);
                    }
                    if result == u32::MAX {
                        unsafe {
                            CloseHandle(snapshot);
                        }
                        return Err(SupervisorError::SpawnFailed);
                    }
                    resumed = true;
                }
            }
            has_entry = unsafe { Thread32Next(snapshot, &mut entry) } != 0;
        }
        unsafe {
            CloseHandle(snapshot);
        }
        if resumed {
            Ok(())
        } else {
            Err(SupervisorError::SpawnFailed)
        }
    }
}

#[cfg(windows)]
impl Drop for WorkerJob {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            unsafe {
                CloseHandle(self.handle);
            }
        }
    }
}

impl WorkerSession {
    pub fn session_id(&self) -> &str {
        &self.session_id
    }

    pub fn approval_control(&self) -> ApprovalControl {
        ApprovalControl {
            stdin: Arc::clone(&self.stdin),
            input_sequence: Arc::clone(&self.input_sequence),
            output_limit: self.output_limit,
            session_id: self.session_id.clone(),
            child: Arc::clone(&self.child),
            pid: self.pid,
            pending_responses: Arc::clone(&self.pending_responses),
            response_timeout: self.heartbeat_timeout,
        }
    }

    async fn handshake(
        &mut self,
        session_id: &str,
        timeout: Duration,
    ) -> Result<(), SupervisorError> {
        let frame = json!({
            "protocolVersion": ADAPTER_PROTOCOL_VERSION,
            "requestId": HANDSHAKE_REQUEST_ID,
            "sessionId": session_id,
            "sequence": 0,
            "type": "handshake",
            "payload": {"adapterKind": self.adapter_kind}
        });
        self.send_frame(frame).await?;
        let response = tokio::time::timeout(timeout, self.read_frame())
            .await
            .map_err(|_| SupervisorError::HandshakeTimeout)??;
        validate_handshake_response(&response, HANDSHAKE_REQUEST_ID, session_id)
    }

    pub async fn send_frame(&self, frame: Value) -> Result<(), SupervisorError> {
        let mut bytes = serde_json::to_vec(&frame).map_err(|_| SupervisorError::Protocol)?;
        if bytes.len() + 1 > self.output_limit {
            return Err(SupervisorError::OutputLimitExceeded);
        }
        bytes.push(b'\n');
        let mut stdin = self.stdin.lock().await;
        stdin
            .write_all(&bytes)
            .await
            .map_err(|_| SupervisorError::WorkerExited)?;
        stdin
            .flush()
            .await
            .map_err(|_| SupervisorError::WorkerExited)
    }

    pub async fn start_agent_task(
        &mut self,
        prompt: &str,
        permission: &str,
    ) -> Result<(), SupervisorError> {
        if prompt.trim().is_empty() || prompt.len() > 16 * 1024 || !is_safe_permission(permission) {
            return Err(SupervisorError::Protocol);
        }
        let request_id = "session-start";
        let sequence = self.input_sequence.fetch_add(1, Ordering::Relaxed);
        self.send_frame(json!({
            "protocolVersion": ADAPTER_PROTOCOL_VERSION,
            "requestId": request_id,
            "sessionId": self.session_id,
            "sequence": sequence,
            "type": "session.start",
            "payload": {"permission": permission, "prompt": prompt}
        }))
        .await?;
        let response = tokio::time::timeout(self.heartbeat_timeout, self.read_frame())
            .await
            .map_err(|_| SupervisorError::HeartbeatLost)??;
        validate_response_ok(&response, request_id, &self.session_id)
    }

    pub async fn resolve_approval(
        &self,
        approval_id: &str,
        approved: bool,
    ) -> Result<(), SupervisorError> {
        if !is_safe_identifier(approval_id) {
            return Err(SupervisorError::Protocol);
        }
        let sequence = self.input_sequence.fetch_add(1, Ordering::Relaxed);
        self.send_frame(approval_resolution_frame(
            &self.session_id,
            approval_id,
            sequence,
            approved,
        ))
        .await
    }

    pub async fn read_frame(&mut self) -> Result<Value, SupervisorError> {
        if let Err(error) = self.check_stderr_budget().await {
            let _ = self.cancel().await;
            return Err(error);
        }
        let heartbeat_deadline = self.last_heartbeat + self.heartbeat_timeout;
        let read_result = tokio::select! {
            result = read_bounded_line(&mut self.stdout, self.output_limit) => result,
            _ = tokio::time::sleep_until(tokio::time::Instant::from_std(heartbeat_deadline)) => {
                Err(SupervisorError::HeartbeatLost)
            }
        };
        let line = match read_result {
            Ok(line) => line,
            Err(SupervisorError::HeartbeatLost) => {
                let _ = self.cancel().await;
                return Err(SupervisorError::HeartbeatLost);
            }
            Err(error) => {
                let _ = self.cancel().await;
                return Err(error);
            }
        };
        if let Err(error) = self.check_stderr_budget().await {
            let _ = self.cancel().await;
            return Err(error);
        }
        let response: Value = match serde_json::from_str(&line) {
            Ok(response) => response,
            Err(_) => {
                let _ = self.cancel().await;
                return Err(SupervisorError::Protocol);
            }
        };
        if response.get("type").and_then(Value::as_str) == Some("worker.heartbeat") {
            self.last_heartbeat = Instant::now();
        }
        Ok(response)
    }

    pub async fn next_agent_event(
        &mut self,
        generation_id: &str,
        task_id: &str,
    ) -> Result<Option<AgentEventEnvelope>, SupervisorError> {
        if !is_safe_identifier(generation_id) || !is_safe_identifier(task_id) {
            return Err(SupervisorError::Protocol);
        }
        loop {
            let frame = self.read_frame().await?;
            let frame_session_id = frame.get("sessionId").and_then(Value::as_str);
            if frame.get("protocolVersion").and_then(Value::as_str)
                != Some(ADAPTER_PROTOCOL_VERSION)
                || frame_session_id != Some(self.session_id.as_str())
            {
                return Err(SupervisorError::Protocol);
            }
            let frame_type = frame
                .get("type")
                .and_then(Value::as_str)
                .ok_or(SupervisorError::Protocol)?;
            if frame_type.starts_with("response.") {
                self.resolve_pending_response(&frame).await;
                continue;
            }
            if frame_type == "worker.heartbeat" {
                continue;
            }
            return map_worker_event(&frame, generation_id, task_id, &self.session_id).map(Some);
        }
    }

    async fn resolve_pending_response(&self, frame: &Value) {
        let Some(request_id) = frame.get("requestId").and_then(Value::as_str) else {
            return;
        };
        let sender = self.pending_responses.lock().await.remove(request_id);
        let Some(sender) = sender else {
            return;
        };
        let result = if frame.get("type").and_then(Value::as_str) == Some("response.ok") {
            validate_response_ok(frame, request_id, &self.session_id)
        } else {
            Err(SupervisorError::Protocol)
        };
        let _ = sender.send(result);
    }

    pub async fn fail_pending_responses(&self) {
        let senders = self
            .pending_responses
            .lock()
            .await
            .drain()
            .map(|(_, sender)| sender)
            .collect::<Vec<_>>();
        for sender in senders {
            let _ = sender.send(Err(SupervisorError::WorkerExited));
        }
    }

    pub async fn pump_agent_events<F>(
        &mut self,
        generation_id: &str,
        task_id: &str,
        mut emit: F,
    ) -> Result<(), SupervisorError>
    where
        F: FnMut(AgentEventEnvelope) -> Result<(), SupervisorError>,
    {
        while let Some(event) = self.next_agent_event(generation_id, task_id).await? {
            let terminal = matches!(event.event_type.as_str(), "task.completed" | "task.failed");
            emit(event)?;
            if terminal {
                return Ok(());
            }
        }
        Ok(())
    }

    async fn check_stderr_budget(&mut self) -> Result<(), SupervisorError> {
        let finished = self
            .stderr_task
            .as_ref()
            .is_some_and(JoinHandle::is_finished);
        if !finished {
            return Ok(());
        }
        let task = self.stderr_task.take().expect("finished stderr task");
        task.await.map_err(|_| SupervisorError::WorkerExited)??;
        Ok(())
    }

    pub async fn initialize_secret(
        &mut self,
        credential_id: &str,
        secret: SecretValue,
    ) -> Result<(), SupervisorError> {
        if self.secret_initialized {
            return Err(SupervisorError::SecretInitializationAlreadySent);
        }
        if !is_safe_identifier(credential_id) {
            return Err(SupervisorError::Protocol);
        }
        let mut secret_text =
            String::from_utf8_lossy(secret.expose_bytes_for_backend()).into_owned();
        if secret_text.is_empty() {
            secret_text.zeroize();
            return Err(SupervisorError::Protocol);
        }
        if let Err(error) = self
            .send_secret_init_frame(credential_id, &secret_text)
            .await
        {
            let _ = self.cancel().await;
            secret_text.zeroize();
            return Err(error);
        }
        let result = match tokio::time::timeout(self.heartbeat_timeout, self.read_frame()).await {
            Ok(Ok(response)) => response,
            Ok(Err(error)) => {
                secret_text.zeroize();
                return Err(error);
            }
            Err(_) => {
                let _ = self.cancel().await;
                secret_text.zeroize();
                return Err(SupervisorError::HeartbeatLost);
            }
        };
        secret_text.zeroize();
        if let Err(error) = validate_response_ok(&result, SECRET_INIT_REQUEST_ID, "private-init") {
            let _ = self.cancel().await;
            return Err(error);
        }
        self.secret_initialized = true;
        Ok(())
    }

    async fn send_secret_init_frame(
        &self,
        credential_id: &str,
        secret: &str,
    ) -> Result<(), SupervisorError> {
        let frame = SecretInitFrame {
            protocol_version: ADAPTER_PROTOCOL_VERSION,
            request_id: SECRET_INIT_REQUEST_ID,
            session_id: "private-init",
            sequence: 1,
            frame_type: "adapter.init",
            payload: SecretInitPayload {
                credential_id,
                secret,
            },
        };
        let mut bytes =
            Zeroizing::new(serde_json::to_vec(&frame).map_err(|_| SupervisorError::Protocol)?);
        if bytes.len() + 1 > self.output_limit {
            return Err(SupervisorError::OutputLimitExceeded);
        }
        bytes.push(b'\n');
        let mut stdin = self.stdin.lock().await;
        stdin
            .write_all(&bytes)
            .await
            .map_err(|_| SupervisorError::WorkerExited)?;
        stdin
            .flush()
            .await
            .map_err(|_| SupervisorError::WorkerExited)
    }

    pub async fn cleanup(&mut self, _reason: CleanupReason) -> Result<(), SupervisorError> {
        self.cancel().await
    }

    pub async fn cancel(&mut self) -> Result<(), SupervisorError> {
        self.fail_pending_responses().await;
        #[cfg(windows)]
        self.worker_job.terminate();
        let mut child = self.child.lock().await;
        let terminate_failed = child
            .id()
            .map(crate::runtime::process_cleanup::terminate_worker_process_tree)
            .is_some_and(|result| result.is_err());
        let wait = tokio::time::timeout(Duration::from_secs(2), child.wait()).await;
        match wait {
            Ok(Ok(_)) if !terminate_failed => {}
            Ok(Ok(_)) | Ok(Err(_)) | Err(_) => {
                let force_failed = crate::runtime::process_cleanup::force_terminate_worker_process_tree(self.pid)
                    .is_err();
                let kill_failed = child.start_kill().is_err();
                let wait = tokio::time::timeout(Duration::from_secs(2), child.wait()).await;
                if terminate_failed
                    || force_failed
                    || kill_failed
                    || !matches!(wait, Ok(Ok(_)))
                {
                    return Err(SupervisorError::CleanupFailed);
                }
            }
        }
        if let Some(task) = self.stderr_task.take() {
            task.abort();
        }
        Ok(())
    }
}

impl Drop for WorkerSession {
    fn drop(&mut self) {
        #[cfg(windows)]
        self.worker_job.terminate();
        let _ = crate::runtime::process_cleanup::terminate_worker_process_tree(self.pid);
        let _ = crate::runtime::process_cleanup::force_terminate_worker_process_tree(self.pid);
        if let Ok(mut child) = self.child.try_lock() {
            let _ = child.start_kill();
        }
        if let Some(task) = self.stderr_task.take() {
            task.abort();
        }
    }
}

async fn read_bounded_line<R: AsyncRead + Unpin>(
    reader: &mut R,
    limit: usize,
) -> Result<String, SupervisorError> {
    let mut bytes = Vec::new();
    let mut buffer = [0_u8; 1];
    loop {
        let count = reader
            .read(&mut buffer)
            .await
            .map_err(|_| SupervisorError::WorkerExited)?;
        if count == 0 {
            return Err(SupervisorError::WorkerExited);
        }
        if buffer[0] == b'\n' {
            break;
        }
        if bytes.len() >= limit {
            return Err(SupervisorError::OutputLimitExceeded);
        }
        bytes.push(buffer[0]);
    }
    String::from_utf8(bytes).map_err(|_| SupervisorError::Protocol)
}

async fn drain_bounded<R: AsyncRead + Unpin>(
    mut reader: R,
    limit: usize,
) -> Result<String, SupervisorError> {
    let mut output = Vec::new();
    let mut buffer = [0_u8; 1024];
    loop {
        let count = reader
            .read(&mut buffer)
            .await
            .map_err(|_| SupervisorError::WorkerExited)?;
        if count == 0 {
            let text = String::from_utf8_lossy(&output);
            return Ok(crate::runtime::redaction::redact_bounded(&text, limit));
        }
        if output.len().saturating_add(count) > limit {
            return Err(SupervisorError::OutputLimitExceeded);
        }
        output.extend_from_slice(&buffer[..count]);
    }
}

fn validate_handshake_response(
    response: &Value,
    request_id: &str,
    session_id: &str,
) -> Result<(), SupervisorError> {
    validate_response_ok(response, request_id, session_id)
}

fn map_worker_event(
    frame: &Value,
    generation_id: &str,
    task_id: &str,
    session_id: &str,
) -> Result<AgentEventEnvelope, SupervisorError> {
    let source_type = frame
        .get("type")
        .and_then(Value::as_str)
        .ok_or(SupervisorError::Protocol)?;
    let event_type = match source_type {
        "session.started" | "session.resumed" => "task.started",
        "session.completed" => "task.completed",
        "session.failed" => "task.failed",
        "progress.updated" => "task.progress",
        "approval.requested" => "approval.requested",
        "task.waiting-approval" => "task.waiting-approval",
        "approval.resolved" => "approval.resolved",
        "worker.interrupted" => "worker.interrupted",
        "worker.recoverable" => "worker.recoverable",
        "message.delta" => "message.delta",
        "message.completed" => "message.completed",
        "tool.started" => "tool.started",
        "tool.output" => "tool.output",
        "tool.completed" => "tool.completed",
        "command.started" => "command.started",
        "command.output" => "command.output",
        "command.completed" => "command.completed",
        "file.changed" => "file.changed",
        "file.diff.available" => "file.diff.available",
        "usage.updated" => "usage.updated",
        "extension.called" => "extension.called",
        _ => return Err(SupervisorError::Protocol),
    };
    let payload = frame.get("payload").ok_or(SupervisorError::Protocol)?;
    let payload = match source_type {
        "message.delta" | "message.completed" => {
            let object = payload.as_object().ok_or(SupervisorError::Protocol)?;
            if object.len() != 1 || !object.contains_key("text") {
                return Err(SupervisorError::Protocol);
            }
            if !object.get("text").is_some_and(Value::is_string) {
                return Err(SupervisorError::Protocol);
            }
            json!({"text": object.get("text").cloned().ok_or(SupervisorError::Protocol)?})
        }
        "tool.output" | "command.output" | "file.diff.available" => {
            let object = payload.as_object().ok_or(SupervisorError::Protocol)?;
            if object.len() != 1 || !object.contains_key("contentRef") {
                return Err(SupervisorError::Protocol);
            }
            let reference = object.get("contentRef").ok_or(SupervisorError::Protocol)?;
            validate_content_reference(reference)?;
            json!({"contentRef": reference})
        }
        _ => {
            if !payload.as_object().is_some_and(serde_json::Map::is_empty) {
                return Err(SupervisorError::Protocol);
            }
            json!({})
        }
    };
    let sequence = frame
        .get("sequence")
        .and_then(Value::as_u64)
        .filter(|sequence| *sequence >= 1)
        .ok_or(SupervisorError::Protocol)?;
    Ok(AgentEventEnvelope {
        channel: AGENT_EVENT_CHANNEL.to_owned(),
        generation_id: generation_id.to_owned(),
        task_id: task_id.to_owned(),
        session_id: session_id.to_owned(),
        sequence,
        event_type: event_type.to_owned(),
        payload,
    })
}

fn approval_resolution_frame(
    session_id: &str,
    approval_id: &str,
    sequence: u64,
    approved: bool,
) -> Value {
    json!({
        "protocolVersion": ADAPTER_PROTOCOL_VERSION,
        "requestId": format!("approval-{approval_id}"),
        "sessionId": session_id,
        "sequence": sequence,
        "type": "approval.resolve",
        "payload": {"approved": approved}
    })
}

fn validate_content_reference(value: &Value) -> Result<(), SupervisorError> {
    let object = value.as_object().ok_or(SupervisorError::Protocol)?;
    if object.len() != 4
        || !object.contains_key("id")
        || !object.contains_key("mediaType")
        || !object.contains_key("byteLength")
        || !object.contains_key("truncated")
        || !object.get("id").is_some_and(Value::is_string)
        || !object.get("mediaType").is_some_and(Value::is_string)
        || !object.get("byteLength").and_then(Value::as_u64).is_some()
        || !object.get("truncated").is_some_and(Value::is_boolean)
    {
        return Err(SupervisorError::Protocol);
    }
    Ok(())
}

fn validate_response_ok(
    response: &Value,
    request_id: &str,
    session_id: &str,
) -> Result<(), SupervisorError> {
    if response.get("protocolVersion").and_then(Value::as_str) != Some(ADAPTER_PROTOCOL_VERSION)
        || response.get("type").and_then(Value::as_str) != Some("response.ok")
        || response.get("requestId").and_then(Value::as_str) != Some(request_id)
        || response.get("sessionId").and_then(Value::as_str) != Some(session_id)
        || response.get("sequence").and_then(Value::as_u64).is_none()
        || response
            .pointer("/payload/accepted")
            .and_then(Value::as_bool)
            != Some(true)
    {
        return Err(SupervisorError::Protocol);
    }
    Ok(())
}

fn is_safe_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-') && index > 0
        })
}

fn is_safe_permission(value: &str) -> bool {
    matches!(value, "request-approval" | "smart-approval" | "full-access")
}

fn is_secret_like_environment_name(name: &str) -> bool {
    let normalized = name.to_ascii_lowercase();
    [
        "token",
        "secret",
        "password",
        "api_key",
        "apikey",
        "authorization",
        "private_key",
    ]
    .iter()
    .any(|marker| normalized.contains(marker))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CleanupReason {
    DesktopExit,
    HeartbeatLost,
    UserCancelled,
}

impl CleanupReason {
    pub fn requires_cleanup(self) -> bool {
        true
    }
}

pub fn heartbeat_expired(last_heartbeat: Instant, now: Instant, timeout: Duration) -> bool {
    now.saturating_duration_since(last_heartbeat) > timeout
}

#[derive(Debug, thiserror::Error)]
pub enum SupervisorError {
    #[error("Worker executable is unavailable")]
    ExecutableUnavailable,
    #[error("Worker executable is not allowlisted")]
    ExecutableNotAllowlisted,
    #[error("Worker working directory is unavailable")]
    WorkingDirectoryUnavailable,
    #[error("Worker environment contains a secret-like variable")]
    SecretEnvironmentRejected,
    #[error("Worker process could not be spawned")]
    SpawnFailed,
    #[error("Worker handshake timed out")]
    HandshakeTimeout,
    #[error("Worker protocol output exceeded the limit")]
    OutputLimitExceeded,
    #[error("Worker exited before completing the request")]
    WorkerExited,
    #[error("Worker protocol frame is invalid")]
    Protocol,
    #[error("Worker heartbeat was lost")]
    HeartbeatLost,
    #[error("Worker secret initialization was already sent")]
    SecretInitializationAlreadySent,
    #[error("Worker Windows command line contains unsafe characters")]
    UnsafeWindowsCommandLine,
    #[error("Worker cleanup failed")]
    CleanupFailed,
}

#[cfg(test)]
mod tests {
    use std::{
        collections::BTreeMap,
        fs,
        path::{Path, PathBuf},
        time::Duration,
    };

    use serde_json::json;
    use tempfile::TempDir;

    use crate::credentials::model::SecretValue;

    use super::{
        ADAPTER_PROTOCOL_VERSION, CleanupReason, SupervisorConfig, SupervisorError,
        WorkerSupervisor, approval_resolution_frame, heartbeat_expired, map_worker_event,
        validate_response_ok,
    };

    fn worker_fixture(root: &Path, name: &str, body: &str) -> PathBuf {
        #[cfg(unix)]
        {
            let path = root.join(name);
            fs::write(&path, format!("#!/bin/sh\n{}\n", body)).unwrap();
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
            path
        }
        #[cfg(windows)]
        {
            let path = root.join(format!("{name}.cmd"));
            fs::write(&path, format!("@echo off\r\n{}\r\n", body)).unwrap();
            path
        }
    }

    fn config(adapter: PathBuf, cwd: PathBuf) -> SupervisorConfig {
        SupervisorConfig::new(adapter.clone(), cwd)
            .with_allowed_executables([adapter])
            .with_handshake_timeout(Duration::from_millis(250))
            .with_output_limit(4096)
            .with_restart_limit(1)
    }

    #[test]
    fn maps_waiting_approval_worker_events_to_desktop_events() {
        let event = map_worker_event(
            &json!({
                "protocolVersion": ADAPTER_PROTOCOL_VERSION,
                "requestId": "event-1",
                "sessionId": "session",
                "sequence": 1,
                "type": "task.waiting-approval",
                "payload": {}
            }),
            "generation-1",
            "task-1",
            "session",
        )
        .unwrap();
        assert_eq!(event.event_type, "task.waiting-approval");
    }

    #[test]
    fn builds_a_session_bound_approval_resolution_request() {
        let frame = approval_resolution_frame("session-1", "approval-1", 2, true);
        assert_eq!(frame["sessionId"], "session-1");
        assert_eq!(frame["requestId"], "approval-approval-1");
        assert_eq!(frame["sequence"], 2);
        assert_eq!(frame["type"], "approval.resolve");
        assert_eq!(frame["payload"]["approved"], true);
    }

    #[test]
    fn accepts_a_correlated_response_with_a_session_sequence() {
        let response = json!({
            "protocolVersion": ADAPTER_PROTOCOL_VERSION,
            "requestId": "approval-1",
            "sessionId": "session-1",
            "sequence": 2,
            "type": "response.ok",
            "payload": {"accepted": true}
        });
        assert!(validate_response_ok(&response, "approval-1", "session-1").is_ok());
    }

    #[tokio::test]
    async fn approval_control_waits_for_the_correlated_worker_response() {
        let temp = TempDir::new().unwrap();
        let acknowledgement = r#"{"protocolVersion":"dsh-agent-adapter/v1","requestId":"approval-approval-1","sessionId":"session","sequence":0,"type":"response.ok","payload":{"accepted":true}}"#;
        let adapter = worker_fixture(
            temp.path(),
            "adapter",
            &format!(
                "read line\nprintf '%s\\n' '{}'\nread line\nprintf '%s\\n' '{}'\n",
                handshake_response(),
                acknowledgement
            ),
        );
        let supervisor = shell_supervisor(adapter, fs::canonicalize(temp.path()).unwrap());
        let mut session = supervisor.launch("session").await.unwrap();
        let control = session.approval_control();
        let pump = tokio::spawn(async move {
            session.next_agent_event("generation", "task").await
        });

        control.resolve_approval("approval-1", true).await.unwrap();
        assert!(pump.await.unwrap().is_err());
    }

    #[tokio::test]
    async fn approval_control_times_out_without_a_worker_response() {
        let temp = TempDir::new().unwrap();
        let adapter = worker_fixture(
            temp.path(),
            "adapter",
            &format!(
                "read line\nprintf '%s\\n' '{}'\nread line\nsleep 30\n",
                handshake_response()
            ),
        );
        let base = shell_supervisor(adapter, fs::canonicalize(temp.path()).unwrap());
        let supervisor = WorkerSupervisor::new(
            base.config
                .clone()
                .with_heartbeat_timeout(Duration::from_millis(40)),
        );
        let mut session = supervisor.launch("session").await.unwrap();
        let control = session.approval_control();
        let error = control
            .resolve_approval("approval-1", true)
            .await
            .unwrap_err();
        assert!(matches!(error, SupervisorError::HeartbeatLost));
        session.cancel().await.unwrap();
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn approval_timeout_terminates_worker_before_delayed_resolution() {
        let temp = TempDir::new().unwrap();
        let marker = temp.path().join("delayed-approval-executed");
        let adapter = worker_fixture(
            temp.path(),
            "adapter",
            &format!(
                "read line\nprintf '%s\\n' '{}'\nread line\nsleep 1\ntouch '{}'\nprintf '%s\\n' '{}'\n",
                handshake_response(),
                marker.display(),
                r#"{"protocolVersion":"dsh-agent-adapter/v1","requestId":"approval-approval-1","sessionId":"session","sequence":1,"type":"response.ok","payload":{"accepted":true}}"#,
            ),
        );
        let base = shell_supervisor(adapter, fs::canonicalize(temp.path()).unwrap());
        let supervisor = WorkerSupervisor::new(
            base.config
                .clone()
                .with_heartbeat_timeout(Duration::from_millis(40)),
        );
        let mut session = supervisor.launch("session").await.unwrap();
        let control = session.approval_control();
        let error = control
            .resolve_approval("approval-1", true)
            .await
            .unwrap_err();

        assert!(matches!(error, SupervisorError::HeartbeatLost));
        tokio::time::sleep(Duration::from_millis(100)).await;
        assert!(!marker.exists());
        session.cancel().await.unwrap();
    }

    fn shell_supervisor(script: PathBuf, cwd: PathBuf) -> WorkerSupervisor {
        #[cfg(unix)]
        let shell = PathBuf::from("/bin/sh");
        #[cfg(windows)]
        let shell = PathBuf::from(std::env::var_os("COMSPEC").unwrap_or_else(|| "cmd.exe".into()));
        #[cfg(unix)]
        let args = vec![script.to_string_lossy().into_owned()];
        #[cfg(windows)]
        let args = vec!["/C".to_owned(), script.to_string_lossy().into_owned()];
        WorkerSupervisor::new(
            SupervisorConfig::new(shell.clone(), cwd)
                .with_adapter_args(args)
                .with_allowed_executables([shell])
                .with_handshake_timeout(Duration::from_millis(250))
                .with_output_limit(4096)
                .with_restart_limit(1),
        )
    }

    fn handshake_response() -> &'static str {
        r#"{"protocolVersion":"dsh-agent-adapter/v1","requestId":"handshake","sessionId":"session","sequence":0,"type":"response.ok","payload":{"accepted":true}}"#
    }

    #[tokio::test]
    async fn launches_only_allowlisted_adapter_with_minimal_environment_and_exact_cwd() {
        let temp = TempDir::new().unwrap();
        let marker = temp.path().join("launch.txt");
        let adapter = worker_fixture(
            temp.path(),
            "adapter",
            &format!(
                "pwd > '{}'\nprintf '%s' \"$UNSAFE_INHERITED\" >> '{}'\nprintf '%s' '{}' >> '{}'\nprintf '%s\\n' '{}'\nsleep 30\n",
                marker.display(),
                marker.display(),
                "safe",
                marker.display(),
                handshake_response(),
            ),
        );
        let cwd = fs::canonicalize(temp.path()).unwrap();
        let marker_path = marker.clone();
        std::mem::forget(temp);
        let mut env = BTreeMap::new();
        env.insert("DSH_WORKER_SAFE".to_owned(), "safe".to_owned());
        let supervisor = shell_supervisor(adapter.clone(), cwd.clone());
        let supervisor = WorkerSupervisor::new(supervisor.config.clone().with_env(env));

        let mut session = supervisor.launch("session").await.unwrap();
        session.cancel().await.unwrap();
        let marker_text = fs::read_to_string(marker_path).unwrap();
        assert!(marker_text.starts_with(cwd.to_string_lossy().as_ref()));
        assert!(!marker_text.contains("UNSAFE_INHERITED"));
    }

    #[tokio::test]
    async fn rejects_non_allowlisted_adapter_before_spawn() {
        let temp = TempDir::new().unwrap();
        let adapter = worker_fixture(temp.path(), "adapter", "exit 0");
        let other = worker_fixture(temp.path(), "other", "exit 0");
        let error = WorkerSupervisor::new(config(adapter, temp.path().to_path_buf()))
            .with_adapter_path(other)
            .launch("session")
            .await
            .unwrap_err();
        assert!(matches!(error, SupervisorError::ExecutableNotAllowlisted));
    }

    #[tokio::test]
    async fn handshake_timeout_kills_the_worker() {
        let temp = TempDir::new().unwrap();
        let adapter = worker_fixture(temp.path(), "adapter", "sleep 30");
        let error = WorkerSupervisor::new(
            config(adapter, temp.path().to_path_buf())
                .with_handshake_timeout(Duration::from_millis(30)),
        )
        .launch("session")
        .await
        .unwrap_err();
        assert!(matches!(error, SupervisorError::HandshakeTimeout));
    }

    #[tokio::test]
    async fn oversized_protocol_output_is_rejected_and_worker_is_cleaned_up() {
        let temp = TempDir::new().unwrap();
        let adapter = worker_fixture(
            temp.path(),
            "adapter",
            "read line\nhead -c 5000 /dev/zero 2>/dev/null || powershell -NoProfile -Command \"'x' * 5000\"",
        );
        let error = WorkerSupervisor::new(
            config(adapter, temp.path().to_path_buf()).with_output_limit(128),
        )
        .launch("session")
        .await
        .unwrap_err();
        assert!(matches!(error, SupervisorError::OutputLimitExceeded));
    }

    #[tokio::test]
    async fn cancellation_terminates_the_worker_tree_and_isolated_sessions_can_run_together() {
        let temp = TempDir::new().unwrap();
        let adapter = worker_fixture(
            temp.path(),
            "adapter",
            "read line\nprintf '%s\\n' \"$line\" | sed 's/\"type\":\"handshake\"/\"type\":\"response.ok\"/; s/\"adapterKind\":\"mock\"/\"accepted\":true/'\nsleep 30",
        );
        let supervisor = shell_supervisor(adapter, fs::canonicalize(temp.path()).unwrap());
        let (first, second) = tokio::join!(supervisor.launch("one"), supervisor.launch("two"));
        let mut first = first.unwrap();
        let mut second = second.unwrap();
        first.cancel().await.unwrap();
        second.cancel().await.unwrap();
    }

    #[tokio::test]
    async fn retries_only_after_a_pre_handshake_crash_and_stops_at_the_bound() {
        let temp = TempDir::new().unwrap();
        let marker = temp.path().join("crash-once.marker");
        let adapter = worker_fixture(
            temp.path(),
            "adapter",
            &format!(
                "if [ ! -f '{}' ]; then : > '{}'; exit 0; fi\nprintf '%s\\n' '{}'\nsleep 30",
                marker.display(),
                marker.display(),
                handshake_response(),
            ),
        );
        let supervisor = shell_supervisor(adapter, fs::canonicalize(temp.path()).unwrap());
        let mut session = supervisor.launch("session").await.unwrap();
        session.cancel().await.unwrap();
        assert!(marker.exists());

        let always_crash = worker_fixture(temp.path(), "always-crash", "exit 0");
        let error = shell_supervisor(always_crash, fs::canonicalize(temp.path()).unwrap())
            .launch("session")
            .await
            .unwrap_err();
        assert!(matches!(error, SupervisorError::WorkerExited));
    }

    #[tokio::test]
    async fn sends_a_private_secret_initialization_frame_only_once() {
        let temp = TempDir::new().unwrap();
        let adapter = worker_fixture(
            temp.path(),
            "adapter",
            &format!(
                "read handshake\nprintf '%s\\n' '{}'\nread init\nprintf '%s\\n' \"$init\" | sed 's/\"type\":\"adapter.init\"/\"type\":\"response.ok\"/; s/\"sequence\":1/\"sequence\":0/; s/\"credentialId\":\"credential-1\",\"secret\":\"private-secret\"/\"accepted\":true/'\nsleep 30",
                handshake_response(),
            ),
        );
        let supervisor = shell_supervisor(adapter, fs::canonicalize(temp.path()).unwrap());
        let mut session = supervisor.launch("session").await.unwrap();
        session
            .initialize_secret("credential-1", SecretValue::new("private-secret"))
            .await
            .unwrap();
        let error = session
            .initialize_secret("credential-1", SecretValue::new("private-secret"))
            .await
            .unwrap_err();
        assert!(matches!(
            error,
            SupervisorError::SecretInitializationAlreadySent
        ));
        session.cleanup(CleanupReason::UserCancelled).await.unwrap();
    }

    #[tokio::test]
    async fn heartbeat_loss_cleans_up_a_silent_worker() {
        let temp = TempDir::new().unwrap();
        let adapter = worker_fixture(
            temp.path(),
            "adapter",
            &format!("printf '%s\\n' '{}'\nsleep 30", handshake_response()),
        );
        let supervisor = shell_supervisor(adapter, fs::canonicalize(temp.path()).unwrap());
        let mut session = WorkerSupervisor::new(
            supervisor
                .config
                .clone()
                .with_heartbeat_timeout(Duration::from_millis(30)),
        )
        .launch("session")
        .await
        .unwrap();
        let error = session.read_frame().await.unwrap_err();
        assert!(matches!(error, SupervisorError::HeartbeatLost));
    }

    #[tokio::test]
    async fn converts_worker_events_into_session_bound_desktop_events() {
        let temp = TempDir::new().unwrap();
        let adapter = worker_fixture(
            temp.path(),
            "adapter",
            &format!(
                "printf '%s\\n' '{}' '{}' '{}'\nsleep 30",
                handshake_response(),
                r#"{"protocolVersion":"dsh-agent-adapter/v1","requestId":"event-1","sessionId":"session","sequence":1,"type":"message.delta","payload":{"text":"hello"}}"#,
                r#"{"protocolVersion":"dsh-agent-adapter/v1","requestId":"event-2","sessionId":"session","sequence":2,"type":"session.completed","payload":{}}"#,
            ),
        );
        let supervisor = shell_supervisor(adapter, fs::canonicalize(temp.path()).unwrap());
        let mut session = supervisor.launch("session").await.unwrap();

        let first = session
            .next_agent_event("generation-1", "task-1")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(first.channel, "dsh-agent/v1");
        assert_eq!(first.generation_id, "generation-1");
        assert_eq!(first.task_id, "task-1");
        assert_eq!(first.session_id, "session");
        assert_eq!(first.sequence, 1);
        assert_eq!(first.event_type, "message.delta");
        assert_eq!(first.payload, serde_json::json!({"text": "hello"}));

        let second = session
            .next_agent_event("generation-1", "task-1")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(second.event_type, "task.completed");
        assert_eq!(second.sequence, 2);
        assert_eq!(second.payload, serde_json::json!({}));
        session.cancel().await.unwrap();
    }

    #[test]
    fn heartbeat_and_desktop_exit_are_explicit_cleanup_conditions() {
        let now = std::time::Instant::now();
        assert!(heartbeat_expired(
            now - Duration::from_secs(5),
            now,
            Duration::from_secs(1)
        ));
        assert!(!heartbeat_expired(now, now, Duration::from_secs(1)));
        assert!(CleanupReason::DesktopExit.requires_cleanup());
        assert!(CleanupReason::HeartbeatLost.requires_cleanup());
    }
}
