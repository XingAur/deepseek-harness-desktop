use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Component, Path},
};

use crate::platform::ProcessIdentity;

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
    use std::path::PathBuf;

    use super::*;
    use crate::platform::ProcessIdentity;

    fn identity(pid: u32, executable: &str) -> ProcessIdentity {
        ProcessIdentity {
            pid,
            parent_pid: 0,
            executable: PathBuf::from(executable),
        }
    }

    #[test]
    fn selects_only_processes_beneath_the_managed_runtime_root() {
        let root =
            PathBuf::from(r"C:\Users\test\AppData\Local\ai.deepseek.harness.desktop\runtime");
        let processes = vec![
            identity(
                41,
                r"C:\Users\test\AppData\Local\ai.deepseek.harness.desktop\runtime\versions\0.1.0-preview\node.exe",
            ),
            identity(42, r"C:\Program Files\nodejs\node.exe"),
            identity(43, r"E:\code\project\node_modules\node.exe"),
        ];
        assert_eq!(select_managed(&root, 99, processes), vec![41]);
    }

    #[test]
    fn selector_excludes_the_current_process_and_prefix_lookalikes() {
        let root = PathBuf::from(r"C:\app\runtime");
        let processes = vec![
            identity(7, r"C:\app\runtime\versions\v\node.exe"),
            identity(8, r"C:\app\runtime-evil\node.exe"),
        ];
        assert!(select_managed(&root, 7, processes).is_empty());
    }
}
