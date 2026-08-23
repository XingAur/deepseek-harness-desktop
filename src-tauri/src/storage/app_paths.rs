use std::path::PathBuf;

use tauri::{AppHandle, Manager};

use crate::runtime::model::RuntimeFailure;

#[derive(Clone, Debug)]
pub struct AppPaths {
    pub stable_root: PathBuf,
    pub active_root: PathBuf,
    pub profiles: PathBuf,
    pub runtime: PathBuf,
    pub updates: PathBuf,
    pub diagnostics: PathBuf,
    pub backups: PathBuf,
    pub state: PathBuf,
    pub logs: PathBuf,
    pub bundled_runtime: PathBuf,
    pub provisioning_downloads: PathBuf,
    pub provisioning_candidates: PathBuf,
    pub provisioning_prepared: PathBuf,
    pub provisioning_receipt: PathBuf,
    pub user_downloads: PathBuf,
}

impl AppPaths {
    pub fn resolve(app: &AppHandle) -> Result<Self, RuntimeFailure> {
        let stable_root = app
            .path()
            .app_local_data_dir()
            .map_err(RuntimeFailure::internal)?;
        let expected = app.config().identifier.as_str();
        let actual = stable_root.file_name().and_then(|name| name.to_str());
        if actual != Some(expected) {
            return Err(RuntimeFailure::internal(format!(
                "应用数据目录标识不匹配：期望 {expected}，实际 {}",
                stable_root.display()
            )));
        }
        let resource_root = app
            .path()
            .resource_dir()
            .map_err(RuntimeFailure::internal)?;
        let user_downloads = app
            .path()
            .download_dir()
            .map_err(RuntimeFailure::internal)?;
        let mut paths = Self::from_roots(stable_root, resource_root);
        paths.user_downloads = user_downloads;
        Ok(paths)
    }

    pub fn from_roots(stable_root: PathBuf, resource_root: PathBuf) -> Self {
        Self::with_active_root(stable_root.clone(), stable_root, resource_root)
    }

    pub fn with_active_root(
        stable_root: PathBuf,
        active_root: PathBuf,
        resource_root: PathBuf,
    ) -> Self {
        Self::with_active_root_and_downloads(
            stable_root.clone(),
            active_root,
            resource_root,
            stable_root.join("Downloads"),
        )
    }

    pub fn with_active_root_and_downloads(
        stable_root: PathBuf,
        active_root: PathBuf,
        resource_root: PathBuf,
        user_downloads: PathBuf,
    ) -> Self {
        let state = active_root.join("state");
        let runtime = active_root.join("runtime");
        Self {
            profiles: active_root.join("profiles"),
            provisioning_downloads: runtime.join("provisioning/downloads"),
            provisioning_candidates: runtime.join("provisioning/candidates"),
            provisioning_prepared: state.join("provisioning-prepared.json"),
            provisioning_receipt: state.join("provisioning.json"),
            runtime,
            updates: active_root.join("updates"),
            diagnostics: active_root.join("diagnostics"),
            state,
            logs: active_root.join("logs"),
            backups: stable_root.join("backups"),
            bundled_runtime: resource_root.join("runtime"),
            stable_root,
            active_root,
            user_downloads,
        }
    }

    pub fn create_owned_directories(&self) -> Result<(), RuntimeFailure> {
        for path in [
            &self.stable_root,
            &self.active_root,
            &self.profiles,
            &self.runtime,
            &self.updates,
            &self.diagnostics,
            &self.backups,
            &self.state,
            &self.logs,
            &self.provisioning_downloads,
            &self.provisioning_candidates,
        ] {
            std::fs::create_dir_all(path).map_err(RuntimeFailure::internal)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::AppPaths;

    #[test]
    fn derives_every_owned_directory_from_the_stable_root() {
        let root = std::path::PathBuf::from("C:/data/ai.deepseek.harness.desktop");
        let paths = AppPaths::from_roots(root.clone(), std::path::PathBuf::from("C:/resources"));
        assert_eq!(paths.stable_root, root);
        assert_eq!(paths.active_root, root);
        assert_eq!(paths.profiles, root.join("profiles"));
        assert_eq!(paths.runtime, root.join("runtime"));
        assert_eq!(paths.updates, root.join("updates"));
        assert_eq!(paths.state, root.join("state"));
        assert_eq!(paths.backups, root.join("backups"));
    }
}
