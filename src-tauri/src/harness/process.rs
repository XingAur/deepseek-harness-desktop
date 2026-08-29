use std::path::{Path, PathBuf};

use super::model::HarnessError;

pub fn validate_sidecar_path(path: &Path, allowed_root: &Path) -> Result<PathBuf, HarnessError> {
    if !path.is_absolute() || path.is_symlink() || !path.is_file() {
        return Err(HarnessError::SidecarPathNotAllowed);
    }
    let canonical = path
        .canonicalize()
        .map_err(|_| HarnessError::SidecarPathNotAllowed)?;
    let root = allowed_root
        .canonicalize()
        .map_err(|_| HarnessError::SidecarPathNotAllowed)?;
    if !canonical.starts_with(&root) {
        return Err(HarnessError::SidecarPathNotAllowed);
    }
    Ok(canonical)
}

pub fn validate_development_host_path(path: &Path) -> Result<PathBuf, HarnessError> {
    if !path.is_absolute() || path.is_symlink() || !path.is_file() {
        return Err(HarnessError::SidecarPathNotAllowed);
    }
    path.canonicalize()
        .map_err(|_| HarnessError::SidecarPathNotAllowed)
}
