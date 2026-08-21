use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use crate::runtime::RuntimeFailure;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProcessIdentity {
    pub pid: u32,
    pub parent_pid: u32,
    pub executable: PathBuf,
}

#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "windows")]
mod windows;

pub trait PlatformAdapter: Send + Sync {
    fn legacy_data_roots(&self, stable_root: &Path) -> Vec<PathBuf>;

    fn process_inventory(&self) -> Result<Vec<ProcessIdentity>, RuntimeFailure> {
        Ok(Vec::new())
    }

    fn terminate_process_tree(&self, _pid: u32) -> Result<(), RuntimeFailure> {
        Err(RuntimeFailure::internal("当前平台不支持终止受管 Runtime"))
    }

    fn process_is_running(&self, _pid: u32) -> Result<bool, RuntimeFailure> {
        Ok(false)
    }

    fn move_to_recycle_bin(&self, _path: &Path) -> Result<(), RuntimeFailure> {
        Err(RuntimeFailure::internal("当前平台不支持把目录移入回收站"))
    }
}

#[cfg(target_os = "windows")]
pub fn current() -> Arc<dyn PlatformAdapter> {
    Arc::new(windows::WindowsPlatformAdapter)
}

#[cfg(target_os = "macos")]
pub fn current() -> Arc<dyn PlatformAdapter> {
    Arc::new(macos::MacOsPlatformAdapter)
}

pub fn normalize_legacy_roots(stable: &Path, candidates: Vec<PathBuf>) -> Vec<PathBuf> {
    let stable_key = comparison_key(stable);
    let mut roots: Vec<PathBuf> = Vec::new();
    for candidate in candidates {
        let key = comparison_key(&candidate);
        if key != stable_key && !roots.iter().any(|root| comparison_key(root) == key) {
            roots.push(candidate);
        }
    }
    roots
}

fn comparison_key(path: &Path) -> String {
    let key = path.to_string_lossy().replace('\\', "/");
    if cfg!(target_os = "windows") {
        key.to_lowercase()
    } else {
        key
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::normalize_legacy_roots;

    #[test]
    fn legacy_roots_never_include_the_stable_root_or_duplicates() {
        let stable = PathBuf::from("C:/Users/test/AppData/Local/ai.deepseek.harness.desktop");
        let roots = normalize_legacy_roots(
            &stable,
            vec![
                stable.clone(),
                PathBuf::from("C:/old"),
                PathBuf::from("C:/old"),
            ],
        );
        assert_eq!(roots, vec![PathBuf::from("C:/old")]);
    }
}
