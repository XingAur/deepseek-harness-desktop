use std::path::{Path, PathBuf};

use crate::{
    projects::recycle::{ProtectedRoots, validate_recycle_target},
    runtime::RuntimeFailure,
};

pub fn create_project_directory(
    requested: &Path,
    desktop_data_root: PathBuf,
    profile_root: PathBuf,
    runtime_root: PathBuf,
) -> Result<PathBuf, RuntimeFailure> {
    if !requested.is_absolute() {
        return Err(RuntimeFailure::internal("仅允许创建绝对项目路径"));
    }
    if requested.exists() {
        return Err(RuntimeFailure::internal("项目路径已存在"));
    }
    let name = requested
        .file_name()
        .filter(|name| !name.is_empty())
        .ok_or_else(|| RuntimeFailure::internal("项目目录名称无效"))?;
    let parent = requested
        .parent()
        .ok_or_else(|| RuntimeFailure::internal("无法确定项目父目录"))?
        .canonicalize()
        .map_err(|error| RuntimeFailure::internal(format!("项目父目录不可访问：{error}")))?;
    if !parent.is_dir() {
        return Err(RuntimeFailure::internal("项目父路径不是目录"));
    }
    let target = parent.join(name);
    // Keep protected roots in the same canonical Windows path namespace as the
    // requested target. Otherwise `\\?\C:\...` and `C:\...` compare as
    // unrelated paths and a project could be created inside managed app data.
    let desktop_data_root = desktop_data_root
        .canonicalize()
        .map_err(|error| RuntimeFailure::internal(format!("无法验证桌面应用数据目录：{error}")))?;
    let profile_root = profile_root.canonicalize().unwrap_or(profile_root);
    let runtime_root = runtime_root.canonicalize().unwrap_or(runtime_root);
    let protected = ProtectedRoots::detect(&target, desktop_data_root, profile_root, runtime_root)?;
    validate_recycle_target(&target, &protected)?;
    std::fs::create_dir(&target)
        .map_err(|error| RuntimeFailure::internal(format!("无法创建项目目录：{error}")))?;
    target.canonicalize().map_err(RuntimeFailure::internal)
}

#[cfg(test)]
mod tests {
    use super::create_project_directory;

    #[test]
    fn creates_one_project_leaf_below_an_existing_parent() {
        let root = tempfile::tempdir().unwrap();
        let parent = root.path().join("projects");
        let desktop = root.path().join("desktop-data");
        std::fs::create_dir_all(&parent).unwrap();
        std::fs::create_dir_all(&desktop).unwrap();

        let created = create_project_directory(
            &parent.join("demo"),
            desktop.clone(),
            desktop.join("profiles/default"),
            desktop.join("runtime"),
        )
        .unwrap();

        assert!(created.is_dir());
        assert_eq!(created, parent.join("demo").canonicalize().unwrap());
    }

    #[test]
    fn rejects_existing_and_managed_paths() {
        let root = tempfile::tempdir().unwrap();
        let desktop = root.path().join("desktop-data");
        let managed_parent = desktop.join("profiles");
        std::fs::create_dir_all(&managed_parent).unwrap();

        assert!(
            create_project_directory(
                &managed_parent.join("demo"),
                desktop.clone(),
                desktop.join("profiles/default"),
                desktop.join("runtime"),
            )
            .is_err()
        );
        assert!(
            create_project_directory(
                &managed_parent,
                desktop.clone(),
                desktop.join("profiles/default"),
                desktop.join("runtime"),
            )
            .is_err()
        );
    }
}
