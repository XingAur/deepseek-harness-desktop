use std::path::{Path, PathBuf};

use super::{PlatformAdapter, normalize_legacy_roots};

pub struct MacOsPlatformAdapter;

impl PlatformAdapter for MacOsPlatformAdapter {
    fn legacy_data_roots(&self, stable_root: &Path) -> Vec<PathBuf> {
        let candidates = std::env::var_os("HOME")
            .map(PathBuf::from)
            .map(|root| {
                root.join("Library")
                    .join("Application Support")
                    .join("DeepSeekHarnessDesktop")
            })
            .into_iter()
            .collect();
        normalize_legacy_roots(stable_root, candidates)
    }

    fn documents_dir(&self) -> Result<PathBuf, crate::runtime::RuntimeFailure> {
        let home = std::env::var_os("HOME")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .ok_or_else(|| crate::runtime::RuntimeFailure::internal("无法找到当前用户目录"))?;
        let documents = home.join("Documents");
        if !documents.is_absolute() {
            return Err(crate::runtime::RuntimeFailure::internal("用户文档目录无效"));
        }
        Ok(documents)
    }
}
