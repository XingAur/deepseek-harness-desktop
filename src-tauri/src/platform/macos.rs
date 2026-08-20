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
}
