use std::path::{Path, PathBuf};

use super::{PlatformAdapter, normalize_legacy_roots};

pub struct WindowsPlatformAdapter;

impl PlatformAdapter for WindowsPlatformAdapter {
    fn legacy_data_roots(&self, stable_root: &Path) -> Vec<PathBuf> {
        let candidates = std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .map(|root| root.join("DeepSeekHarnessDesktop"))
            .into_iter()
            .collect();
        normalize_legacy_roots(stable_root, candidates)
    }

    fn move_to_recycle_bin(&self, path: &Path) -> Result<(), crate::runtime::RuntimeFailure> {
        trash::delete(path).map_err(|error| {
            crate::runtime::RuntimeFailure::internal(format!(
                "无法把项目目录移入回收站 {}：{error}",
                path.display()
            ))
        })
    }
}
