use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Component, Path},
    time::Duration,
};

use crate::{
    platform::{self, PlatformAdapter, ProcessIdentity},
    runtime::{
        RuntimeFailure,
        model::{RuntimeFailureCode, RuntimeFailureContext, RuntimeFailureStage},
        paths::RuntimePaths,
    },
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ManagedProcessShutdownReport {
    pub terminated_process_ids: Vec<u32>,
    pub remaining_process_ids: Vec<u32>,
}

#[derive(Clone, Copy)]
struct ShutdownPolicy {
    polls: usize,
    interval: Duration,
}

impl ShutdownPolicy {
    fn production() -> Self {
        Self {
            polls: 30,
            interval: Duration::from_millis(100),
        }
    }

    #[cfg(test)]
    fn test(polls: usize) -> Self {
        Self {
            polls,
            interval: Duration::ZERO,
        }
    }
}

pub fn shutdown_managed_runtimes(
    paths: &RuntimePaths,
) -> Result<ManagedProcessShutdownReport, RuntimeFailure> {
    let adapter = platform::current();
    shutdown_with_policy(
        adapter.as_ref(),
        &paths.root.join("runtime"),
        std::process::id(),
        ShutdownPolicy::production(),
    )
}

/// Terminates a Worker process group. Workers are always spawned into their own
/// group so cancellation cannot leave a CLI child behind.
pub(crate) fn terminate_worker_process_tree(pid: u32) -> Result<(), RuntimeFailure> {
    #[cfg(unix)]
    {
        let process_group = -(pid as i32);
        let result = unsafe { libc::kill(process_group, libc::SIGTERM) };
        if result != 0 {
            let error = std::io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::ESRCH) {
                return Err(RuntimeFailure::new(
                    RuntimeFailureCode::Process,
                    "终止 Agent Worker 进程组失败",
                ));
            }
        }
        return Ok(());
    }
    #[cfg(windows)]
    {
        let status = std::process::Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .status()
            .map_err(|_| {
                RuntimeFailure::new(RuntimeFailureCode::Process, "终止 Agent Worker 进程树失败")
            })?;
        if !status.success() {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Process,
                "终止 Agent Worker 进程树失败",
            ));
        }
        return Ok(());
    }
    #[allow(unreachable_code)]
    Err(RuntimeFailure::new(
        RuntimeFailureCode::Process,
        "当前平台不支持终止 Agent Worker",
    ))
}

pub(crate) fn force_terminate_worker_process_tree(pid: u32) -> Result<(), RuntimeFailure> {
    #[cfg(unix)]
    {
        let result = unsafe { libc::kill(-(pid as i32), libc::SIGKILL) };
        if result != 0 {
            let error = std::io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::ESRCH) {
                return Err(RuntimeFailure::new(
                    RuntimeFailureCode::Process,
                    "强制终止 Agent Worker 进程组失败",
                ));
            }
        }
        return Ok(());
    }
    #[cfg(windows)]
    {
        return terminate_worker_process_tree(pid);
    }
    #[allow(unreachable_code)]
    Err(RuntimeFailure::new(
        RuntimeFailureCode::Process,
        "当前平台不支持强制终止 Agent Worker",
    ))
}

fn shutdown_with_policy(
    adapter: &dyn PlatformAdapter,
    runtime_root: &Path,
    current_pid: u32,
    policy: ShutdownPolicy,
) -> Result<ManagedProcessShutdownReport, RuntimeFailure> {
    let selected = select_managed(runtime_root, current_pid, adapter.process_inventory()?);
    for pid in &selected {
        if adapter.process_is_running(*pid)? {
            adapter
                .terminate_process_tree(*pid)
                .map_err(|cause| cause.with_context(shutdown_context(vec![*pid])))?;
        }
    }

    let mut remaining = selected.clone();
    for poll in 0..policy.polls.max(1) {
        let mut running = Vec::new();
        for pid in &remaining {
            if adapter.process_is_running(*pid)? {
                running.push(*pid);
            }
        }
        remaining = running;
        if remaining.is_empty() {
            return Ok(ManagedProcessShutdownReport {
                terminated_process_ids: selected,
                remaining_process_ids: Vec::new(),
            });
        }
        if poll + 1 < policy.polls && !policy.interval.is_zero() {
            std::thread::sleep(policy.interval);
        }
    }

    Err(RuntimeFailure::new(
        RuntimeFailureCode::Process,
        "旧版 DeepSeek Harness Runtime 未能及时退出",
    )
    .with_context(shutdown_context(remaining)))
}

fn shutdown_context(process_ids: Vec<u32>) -> RuntimeFailureContext {
    RuntimeFailureContext {
        stage: RuntimeFailureStage::ManagedRuntimeShutdown,
        process_ids,
        managed_relative_path: None,
    }
}

pub(crate) fn select_managed(
    runtime_root: &Path,
    current_pid: u32,
    processes: Vec<ProcessIdentity>,
) -> Vec<u32> {
    let versions = runtime_root.join("versions");
    let candidates = runtime_root.join("provisioning").join("candidates");
    let selected = processes
        .into_iter()
        .filter(|process| process.pid != current_pid)
        .filter(|process| {
            path_is_beneath(&process.executable, &versions)
                || path_is_beneath(&process.executable, &candidates)
        })
        .map(|process| (process.pid, process))
        .collect::<BTreeMap<_, _>>();
    let depths = selected
        .values()
        .map(|process| (process.pid, process_depth(process, &selected)))
        .collect::<BTreeMap<_, _>>();
    let mut selected = selected.into_values().collect::<Vec<_>>();
    selected.sort_by_key(|process| std::cmp::Reverse(depths[&process.pid]));
    selected.into_iter().map(|process| process.pid).collect()
}

fn process_depth(process: &ProcessIdentity, selected: &BTreeMap<u32, ProcessIdentity>) -> usize {
    let mut depth = 0;
    let mut parent = process.parent_pid;
    let mut visited = BTreeSet::new();
    while parent != 0 && visited.insert(parent) {
        let Some(ancestor) = selected.get(&parent) else {
            break;
        };
        depth += 1;
        parent = ancestor.parent_pid;
    }
    depth
}

fn path_is_beneath(candidate: &Path, root: &Path) -> bool {
    let candidate = normalized_components(candidate);
    let root = normalized_components(root);
    candidate.len() > root.len() && candidate.starts_with(&root)
}

fn normalized_components(path: &Path) -> Vec<String> {
    path.components()
        .filter_map(|component| match component {
            Component::Prefix(prefix) => Some(prefix.as_os_str().to_string_lossy().to_lowercase()),
            Component::RootDir => Some("/".to_string()),
            Component::CurDir => None,
            Component::ParentDir => Some("..".to_string()),
            Component::Normal(value) => Some(value.to_string_lossy().to_lowercase()),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use std::{
        collections::BTreeMap,
        path::{Path, PathBuf},
        sync::Mutex,
    };

    use super::*;
    use crate::{
        platform::{PlatformAdapter, ProcessIdentity},
        runtime::model::RuntimeFailureStage,
    };

    struct FakePlatformAdapter {
        inventory: Vec<ProcessIdentity>,
        remaining_running_checks: Mutex<BTreeMap<u32, usize>>,
        terminated: Mutex<Vec<u32>>,
    }

    impl FakePlatformAdapter {
        fn new(inventory: Vec<ProcessIdentity>, running_checks: &[(u32, usize)]) -> Self {
            Self {
                inventory,
                remaining_running_checks: Mutex::new(running_checks.iter().copied().collect()),
                terminated: Mutex::new(Vec::new()),
            }
        }

        fn terminated(&self) -> Vec<u32> {
            self.terminated.lock().unwrap().clone()
        }
    }

    impl PlatformAdapter for FakePlatformAdapter {
        fn legacy_data_roots(&self, _stable_root: &Path) -> Vec<PathBuf> {
            Vec::new()
        }

        fn process_inventory(&self) -> Result<Vec<ProcessIdentity>, RuntimeFailure> {
            Ok(self.inventory.clone())
        }

        fn terminate_process_tree(&self, pid: u32) -> Result<(), RuntimeFailure> {
            self.terminated.lock().unwrap().push(pid);
            Ok(())
        }

        fn process_is_running(&self, pid: u32) -> Result<bool, RuntimeFailure> {
            let mut checks = self.remaining_running_checks.lock().unwrap();
            let remaining = checks.entry(pid).or_default();
            if *remaining == 0 {
                return Ok(false);
            }
            *remaining -= 1;
            Ok(true)
        }
    }

    fn identity(pid: u32, executable: impl Into<PathBuf>) -> ProcessIdentity {
        ProcessIdentity {
            pid,
            parent_pid: 0,
            executable: executable.into(),
        }
    }

    fn runtime_root() -> PathBuf {
        if cfg!(windows) {
            PathBuf::from(r"C:\Users\test\AppData\Local\ai.deepseek.harness.desktop\runtime")
        } else {
            PathBuf::from(
                "/Users/test/Library/Application Support/ai.deepseek.harness.desktop/runtime",
            )
        }
    }

    fn external_node_path() -> PathBuf {
        if cfg!(windows) {
            PathBuf::from(r"C:\Program Files\nodejs\node.exe")
        } else {
            PathBuf::from("/usr/local/bin/node")
        }
    }

    #[test]
    fn selects_only_processes_beneath_the_managed_runtime_root() {
        let root = runtime_root();
        let processes = vec![
            identity(41, root.join("versions/0.1.0-preview/node")),
            identity(42, external_node_path()),
            identity(43, PathBuf::from("/tmp/project/node_modules/node")),
        ];
        assert_eq!(select_managed(&root, 99, processes), vec![41]);
    }

    #[test]
    fn selector_excludes_the_current_process_and_prefix_lookalikes() {
        let root = runtime_root();
        let lookalike = root.with_file_name("runtime-evil");
        let processes = vec![
            identity(7, root.join("versions/v/node")),
            identity(8, lookalike.join("node")),
        ];
        assert!(select_managed(&root, 7, processes).is_empty());
    }

    #[test]
    fn shutdown_terminates_only_selected_pids_and_reports_timeouts() {
        let root = runtime_root();
        let fake = FakePlatformAdapter::new(
            vec![
                identity(11, root.join("versions/v/node")),
                identity(12, external_node_path()),
            ],
            &[(11, 10)],
        );

        let error = shutdown_with_policy(&fake, &root, 99, ShutdownPolicy::test(2)).unwrap_err();

        assert_eq!(fake.terminated(), vec![11]);
        let context = error.context.unwrap();
        assert_eq!(context.stage, RuntimeFailureStage::ManagedRuntimeShutdown);
        assert_eq!(context.process_ids, vec![11]);
    }

    #[test]
    fn shutdown_succeeds_after_the_managed_process_exits() {
        let root = runtime_root();
        let fake =
            FakePlatformAdapter::new(vec![identity(21, root.join("versions/v/node"))], &[(21, 1)]);

        let report = shutdown_with_policy(&fake, &root, 99, ShutdownPolicy::test(2)).unwrap();

        assert_eq!(report.terminated_process_ids, vec![21]);
        assert!(report.remaining_process_ids.is_empty());
    }
}
