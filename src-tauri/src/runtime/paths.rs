use std::path::{Component, Path, PathBuf};

use crate::storage::app_paths::AppPaths;

use super::model::{RuntimeFailure, RuntimeFailureCode};

#[derive(Clone, Debug)]
pub struct RuntimePaths {
    pub root: PathBuf,
    pub versions: PathBuf,
    pub downloads: PathBuf,
    pub logs: PathBuf,
    pub diagnostics: PathBuf,
    pub current: PathBuf,
    pub bundled_runtime: PathBuf,
    pub user_downloads: PathBuf,
}

impl RuntimePaths {
    pub fn from_app_paths(app_paths: &AppPaths) -> Result<Self, RuntimeFailure> {
        let root = app_paths.active_root.clone();
        let paths = Self {
            versions: app_paths.runtime.join("versions"),
            downloads: app_paths.runtime.join("downloads"),
            logs: app_paths.logs.clone(),
            diagnostics: app_paths.diagnostics.clone(),
            current: app_paths.runtime.join("current.json"),
            bundled_runtime: app_paths.bundled_runtime.clone(),
            user_downloads: app_paths.user_downloads.clone(),
            root,
        };
        paths.create()?;
        Ok(paths)
    }

    fn create(&self) -> Result<(), RuntimeFailure> {
        for path in [
            &self.versions,
            &self.downloads,
            &self.logs,
            &self.diagnostics,
        ] {
            std::fs::create_dir_all(path).map_err(RuntimeFailure::internal)?;
        }
        Ok(())
    }

    pub fn version_dir(&self, version: &semver::Version) -> PathBuf {
        self.versions.join(version.to_string())
    }
}

pub fn validate_relative_path(value: &str, label: &str) -> Result<PathBuf, RuntimeFailure> {
    if value.is_empty() || value.contains('\0') {
        return Err(RuntimeFailure::new(
            RuntimeFailureCode::Archive,
            format!("{label} 为空或包含 NUL"),
        ));
    }
    let path = Path::new(value);
    if path.is_absolute()
        || path.components().any(|part| {
            matches!(
                part,
                Component::ParentDir | Component::Prefix(_) | Component::RootDir
            )
        })
    {
        return Err(RuntimeFailure::new(
            RuntimeFailureCode::Archive,
            format!("{label} 必须位于运行时目录内"),
        ));
    }
    Ok(path.to_path_buf())
}

pub fn join_confined(root: &Path, relative: &str, label: &str) -> Result<PathBuf, RuntimeFailure> {
    Ok(root.join(validate_relative_path(relative, label)?))
}
