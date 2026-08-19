use std::path::{Component, Path, PathBuf};
use tauri::{AppHandle, Manager};

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
}

impl RuntimePaths {
    pub fn resolve(app: &AppHandle) -> Result<Self, RuntimeFailure> {
        let root = app
            .path()
            .app_local_data_dir()
            .map_err(RuntimeFailure::internal)?;
        let resource = app
            .path()
            .resource_dir()
            .map_err(RuntimeFailure::internal)?;
        let paths = Self {
            versions: root.join("runtime").join("versions"),
            downloads: root.join("runtime").join("downloads"),
            logs: root.join("logs"),
            diagnostics: root.join("diagnostics"),
            current: root.join("runtime").join("current.json"),
            bundled_runtime: resource.join("runtime"),
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
