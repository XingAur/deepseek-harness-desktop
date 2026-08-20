use std::{
    collections::{BTreeMap, HashSet},
    path::{Path, PathBuf},
};

use serde::Deserialize;

use crate::runtime::RuntimeFailure;

#[derive(Debug, Deserialize)]
struct WorkspaceStorage {
    global: WorkspaceGlobal,
    tables: WorkspaceTables,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceGlobal {
    workspace_ids: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct WorkspaceTables {
    workspaces: BTreeMap<String, WorkspaceRecord>,
}

#[derive(Debug, Deserialize)]
struct WorkspaceRecord {
    path: PathBuf,
}

#[derive(Clone, Debug)]
pub struct ProtectedRoots {
    drive_root: PathBuf,
    user_root: PathBuf,
    desktop_data_root: PathBuf,
    profile_root: PathBuf,
    runtime_root: PathBuf,
}

impl ProtectedRoots {
    pub fn detect(
        candidate: &Path,
        desktop_data_root: PathBuf,
        profile_root: PathBuf,
        runtime_root: PathBuf,
    ) -> Result<Self, RuntimeFailure> {
        let user_root = std::env::var_os("USERPROFILE")
            .map(PathBuf::from)
            .ok_or_else(|| RuntimeFailure::internal("无法确定当前用户目录，已拒绝删除"))?;
        let drive_root = root_path(candidate)
            .ok_or_else(|| RuntimeFailure::internal("无法确定项目所在磁盘根目录"))?;
        Ok(Self {
            drive_root,
            user_root,
            desktop_data_root,
            profile_root,
            runtime_root,
        })
    }

    pub fn all(&self) -> [&Path; 5] {
        [
            &self.drive_root,
            &self.user_root,
            &self.desktop_data_root,
            &self.profile_root,
            &self.runtime_root,
        ]
    }

    #[cfg(test)]
    fn fixture(user_root: PathBuf, desktop_data_root: PathBuf) -> Self {
        Self {
            drive_root: root_path(&user_root).unwrap_or_else(|| PathBuf::from("C:/")),
            profile_root: desktop_data_root.join("profiles/default"),
            runtime_root: desktop_data_root.join("runtime"),
            user_root,
            desktop_data_root,
        }
    }
}

pub fn resolve_registered_workspace(
    profile_root: &Path,
    workspace_id: &str,
) -> Result<PathBuf, RuntimeFailure> {
    if workspace_id.is_empty() || workspace_id.len() > 256 {
        return Err(RuntimeFailure::internal("Workspace ID 无效"));
    }
    let storage_path = profile_root.join("storages").join("workspace.json");
    let bytes = std::fs::read(&storage_path).map_err(|error| {
        RuntimeFailure::internal(format!("无法读取 Workspace 注册表 {}：{error}", storage_path.display()))
    })?;
    let storage: WorkspaceStorage = serde_json::from_slice(&bytes).map_err(|error| {
        RuntimeFailure::internal(format!("Workspace 注册表格式无效：{error}"))
    })?;
    let registered: HashSet<&str> = storage.global.workspace_ids.iter().map(String::as_str).collect();
    if !registered.contains(workspace_id) {
        return Err(RuntimeFailure::internal("项目不在当前 Profile 的 Workspace 列表中"));
    }
    let record = storage
        .tables
        .workspaces
        .get(workspace_id)
        .ok_or_else(|| RuntimeFailure::internal("Workspace 注册记录缺失"))?;
    let canonical = record.path.canonicalize().map_err(|error| {
        RuntimeFailure::internal(format!("项目目录不可访问 {}：{error}", record.path.display()))
    })?;
    if !canonical.is_dir() {
        return Err(RuntimeFailure::internal("注册的项目路径不是目录"));
    }
    Ok(canonical)
}

pub fn validate_recycle_target(
    candidate: &Path,
    protected: &ProtectedRoots,
) -> Result<(), RuntimeFailure> {
    if !candidate.is_absolute() {
        return Err(RuntimeFailure::internal("仅允许回收绝对项目路径"));
    }
    if protected.all().iter().any(|root| {
        same_path(candidate, root) || is_descendant(root, candidate)
    }) {
        return Err(RuntimeFailure::internal("项目路径是受保护目录或其上级目录，已拒绝删除"));
    }
    if [
        &protected.desktop_data_root,
        &protected.profile_root,
        &protected.runtime_root,
    ]
    .iter()
    .any(|root| is_descendant(candidate, root))
    {
        return Err(RuntimeFailure::internal("项目路径位于桌面应用管理目录内，已拒绝删除"));
    }
    Ok(())
}

fn root_path(path: &Path) -> Option<PathBuf> {
    let mut components = path.components();
    let prefix = components.next()?;
    let root = components.next()?;
    if matches!(root, std::path::Component::RootDir) {
        Some(PathBuf::from(prefix.as_os_str()).join(Path::new("/")))
    } else {
        None
    }
}

fn same_path(left: &Path, right: &Path) -> bool {
    path_key(left) == path_key(right)
}

fn is_descendant(candidate: &Path, ancestor: &Path) -> bool {
    let candidate = path_key(candidate);
    let ancestor = path_key(ancestor);
    candidate != ancestor && candidate.starts_with(&(ancestor + "/"))
}

fn path_key(path: &Path) -> String {
    let normalized = path.to_string_lossy().replace('\\', "/");
    let trimmed = normalized.trim_end_matches('/');
    if cfg!(target_os = "windows") {
        trimmed.to_lowercase()
    } else {
        trimmed.to_string()
    }
}

#[cfg(test)]
mod tests {
    use std::path::{Path, PathBuf};

    use super::{ProtectedRoots, resolve_registered_workspace, validate_recycle_target};

    #[test]
    fn resolves_only_registered_workspace_paths() {
        let dir = tempfile::tempdir().unwrap();
        let profile = dir.path().join("profile");
        let project = dir.path().join("project");
        std::fs::create_dir_all(&project).unwrap();
        write_workspace_storage(&profile, "w-1", &project);

        let resolved = resolve_registered_workspace(&profile, "w-1").unwrap();

        assert_eq!(resolved, project.canonicalize().unwrap());
        assert!(resolve_registered_workspace(&profile, "missing").is_err());
    }

    #[test]
    fn rejects_protected_roots_and_their_ancestors() {
        let protected = ProtectedRoots::fixture(
            PathBuf::from("C:/Users/test"),
            PathBuf::from("C:/Users/test/AppData/Local/dsh"),
        );
        for candidate in protected.all() {
            assert!(validate_recycle_target(candidate, &protected).is_err());
        }
        assert!(validate_recycle_target(
            Path::new("C:/Users/test/Projects/demo"),
            &protected,
        )
        .is_ok());
        assert!(validate_recycle_target(
            Path::new("C:/Users/test/AppData/Local/dsh/state"),
            &protected,
        )
        .is_err());
    }

    fn write_workspace_storage(profile: &Path, id: &str, path: &Path) {
        let storage = profile.join("storages/workspace.json");
        std::fs::create_dir_all(storage.parent().unwrap()).unwrap();
        std::fs::write(
            storage,
            serde_json::to_vec(&serde_json::json!({
                "global": { "workspaceIds": [id] },
                "tables": { "workspaces": { (id): { "path": path } } }
            }))
            .unwrap(),
        )
        .unwrap();
    }
}
