use std::{
    collections::{BTreeMap, HashSet},
    fs,
    path::{Path, PathBuf},
};

use serde::Deserialize;

use crate::{data_cleanup::current_user_profile_root, runtime::RuntimeFailure};

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
        Self::detect_with_user_root(
            candidate,
            current_user_profile_root()?,
            desktop_data_root,
            profile_root,
            runtime_root,
        )
    }

    fn detect_with_user_root(
        candidate: &Path,
        user_root: PathBuf,
        desktop_data_root: PathBuf,
        profile_root: PathBuf,
        runtime_root: PathBuf,
    ) -> Result<Self, RuntimeFailure> {
        let candidate = canonicalize_protected_root(candidate, "项目目录")?;
        let drive_root = root_path(&candidate)
            .ok_or_else(|| RuntimeFailure::internal("无法确定项目所在磁盘根目录"))?;
        Ok(Self {
            drive_root: canonicalize_protected_root(&drive_root, "磁盘根目录")?,
            user_root: canonicalize_protected_root(&user_root, "当前用户目录")?,
            desktop_data_root: canonicalize_protected_root(&desktop_data_root, "桌面应用数据目录")?,
            profile_root: canonicalize_protected_root(&profile_root, "Profile 数据目录")?,
            runtime_root: canonicalize_protected_root(&runtime_root, "Runtime 目录")?,
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

fn canonicalize_protected_root(path: &Path, label: &str) -> Result<PathBuf, RuntimeFailure> {
    if !path.is_absolute() {
        return Err(RuntimeFailure::internal(format!(
            "{label}不是绝对路径，已拒绝删除"
        )));
    }
    match fs::symlink_metadata(path) {
        Ok(_) => path.canonicalize().map_err(|error| {
            RuntimeFailure::internal(format!("无法解析{label} {}：{error}", path.display()))
        }),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => normalize_absolute_path(path)
            .ok_or_else(|| RuntimeFailure::internal(format!("无法规范化{label}，已拒绝删除"))),
        Err(error) => Err(RuntimeFailure::internal(format!(
            "无法检查{label} {}：{error}",
            path.display()
        ))),
    }
}

fn normalize_absolute_path(path: &Path) -> Option<PathBuf> {
    use std::path::Component;

    if !path.is_absolute() {
        return None;
    }
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Prefix(_) | Component::RootDir | Component::Normal(_) => {
                normalized.push(component.as_os_str());
            }
            Component::CurDir => {}
            Component::ParentDir => {
                if !normalized.pop() {
                    return None;
                }
            }
        }
    }
    Some(normalized)
}

pub fn resolve_registered_workspace(
    profile_root: &Path,
    workspace_id: &str,
) -> Result<PathBuf, RuntimeFailure> {
    if workspace_id.is_empty() || workspace_id.len() > 256 {
        return Err(RuntimeFailure::internal("Workspace ID 无效"));
    }
    let storage = read_workspace_storage(profile_root)?
        .ok_or_else(|| RuntimeFailure::internal("Workspace 注册表不存在"))?;
    let registered: HashSet<&str> = storage
        .global
        .workspace_ids
        .iter()
        .map(String::as_str)
        .collect();
    if !registered.contains(workspace_id) {
        return Err(RuntimeFailure::internal(
            "项目不在当前 Profile 的 Workspace 列表中",
        ));
    }
    let record = storage
        .tables
        .workspaces
        .get(workspace_id)
        .ok_or_else(|| RuntimeFailure::internal("Workspace 注册记录缺失"))?;
    let canonical = record.path.canonicalize().map_err(|error| {
        RuntimeFailure::internal(format!(
            "项目目录不可访问 {}：{error}",
            record.path.display()
        ))
    })?;
    if !canonical.is_dir() {
        return Err(RuntimeFailure::internal("注册的项目路径不是目录"));
    }
    Ok(canonical)
}

fn read_workspace_storage(profile_root: &Path) -> Result<Option<WorkspaceStorage>, RuntimeFailure> {
    let storage_path = profile_root.join("storages").join("workspace.json");
    match std::fs::read(&storage_path) {
        Ok(bytes) => serde_json::from_slice(&bytes).map(Some).map_err(|error| {
            RuntimeFailure::internal(format!("Workspace 注册表格式无效：{error}"))
        }),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(RuntimeFailure::internal(format!(
            "无法读取 Workspace 注册表 {}：{error}",
            storage_path.display()
        ))),
    }
}

pub(crate) fn list_registered_workspaces(
    profile_root: &Path,
) -> Result<Vec<PathBuf>, RuntimeFailure> {
    let Some(storage) = read_workspace_storage(profile_root)? else {
        return Ok(Vec::new());
    };
    storage
        .global
        .workspace_ids
        .iter()
        .map(|workspace_id| {
            let record = storage.tables.workspaces.get(workspace_id).ok_or_else(|| {
                RuntimeFailure::internal(format!("Workspace 注册记录缺失：{workspace_id}"))
            })?;
            let canonical = record.path.canonicalize().map_err(|error| {
                RuntimeFailure::internal(format!(
                    "项目目录不可访问 {}：{error}",
                    record.path.display()
                ))
            })?;
            if !canonical.is_dir() {
                return Err(RuntimeFailure::internal("注册的项目路径不是目录"));
            }
            Ok(canonical)
        })
        .collect()
}

/// 枚举 (workspaceId, canonical path)，跳过不可访问项；用于本地应用的可运行性判定（advisory）。
pub(crate) fn registered_workspace_records(
    profile_root: &Path,
) -> Result<Vec<(String, PathBuf)>, RuntimeFailure> {
    let Some(storage) = read_workspace_storage(profile_root)? else {
        return Ok(Vec::new());
    };
    let mut records = Vec::new();
    for workspace_id in &storage.global.workspace_ids {
        let Some(record) = storage.tables.workspaces.get(workspace_id) else {
            continue;
        };
        let Ok(canonical) = record.path.canonicalize() else {
            continue;
        };
        if canonical.is_dir() {
            records.push((workspace_id.clone(), canonical));
        }
    }
    Ok(records)
}

pub fn validate_recycle_target(
    candidate: &Path,
    protected: &ProtectedRoots,
) -> Result<(), RuntimeFailure> {
    if !candidate.is_absolute() {
        return Err(RuntimeFailure::internal("仅允许回收绝对项目路径"));
    }
    if protected
        .all()
        .iter()
        .any(|root| same_path(candidate, root) || is_descendant(root, candidate))
    {
        return Err(RuntimeFailure::internal(
            "项目路径是受保护目录或其上级目录，已拒绝删除",
        ));
    }
    if [
        &protected.desktop_data_root,
        &protected.profile_root,
        &protected.runtime_root,
    ]
    .iter()
    .any(|root| is_descendant(candidate, root))
    {
        return Err(RuntimeFailure::internal(
            "项目路径位于桌面应用管理目录内，已拒绝删除",
        ));
    }
    Ok(())
}

fn root_path(path: &Path) -> Option<PathBuf> {
    let mut components = path.components();
    let first = components.next()?;
    match first {
        std::path::Component::Prefix(_) => {
            let root = components.next()?;
            matches!(root, std::path::Component::RootDir)
                .then(|| PathBuf::from(first.as_os_str()).join(Path::new("/")))
        }
        std::path::Component::RootDir => Some(PathBuf::from(first.as_os_str())),
        _ => None,
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

pub(crate) fn path_key(path: &Path) -> String {
    let raw = path.to_string_lossy().replace('\\', "/");
    let raw = if cfg!(target_os = "windows") {
        if let Some(network_path) = raw.strip_prefix("//?/UNC/") {
            format!("//{network_path}")
        } else {
            raw.strip_prefix("//?/").unwrap_or(&raw).to_string()
        }
    } else {
        raw
    };
    let is_unc = raw.starts_with("//");
    let is_rooted = raw.starts_with('/') || raw.as_bytes().get(1) == Some(&b':');
    let minimum_segments = if is_unc { 2 } else { 0 };
    let mut segments = Vec::<&str>::new();
    for segment in raw.split('/') {
        match segment {
            "" | "." => {}
            ".." if segments.len() > minimum_segments => {
                segments.pop();
            }
            ".." => {}
            value => segments.push(value),
        }
    }
    let normalized = if is_unc {
        format!("//{}", segments.join("/"))
    } else if is_rooted && raw.starts_with('/') {
        format!("/{}", segments.join("/"))
    } else if raw.as_bytes().get(1) == Some(&b':') {
        let mut value = segments.join("/");
        if value.len() == 2 {
            value.push('/');
        }
        value
    } else {
        segments.join("/")
    };
    let trimmed = normalized.trim_end_matches('/');
    if cfg!(target_os = "windows") {
        trimmed.to_lowercase()
    } else {
        trimmed.to_string()
    }
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    #[cfg(unix)]
    use super::canonicalize_protected_root;
    use super::{
        ProtectedRoots, list_registered_workspaces, resolve_registered_workspace,
        validate_recycle_target,
    };

    #[test]
    fn lists_only_ids_present_in_the_official_workspace_order() {
        let dir = tempfile::tempdir().unwrap();
        let profile = dir.path().join("profile");
        let first = dir.path().join("first");
        let ignored = dir.path().join("ignored");
        std::fs::create_dir_all(&first).unwrap();
        std::fs::create_dir_all(&ignored).unwrap();
        let storage = profile.join("storages/workspace.json");
        std::fs::create_dir_all(storage.parent().unwrap()).unwrap();
        std::fs::write(
            storage,
            serde_json::to_vec(&serde_json::json!({
                "global": { "workspaceIds": ["w-1"] },
                "tables": { "workspaces": {
                    "w-1": { "path": first },
                    "ignored": { "path": ignored }
                }}
            }))
            .unwrap(),
        )
        .unwrap();

        assert_eq!(
            list_registered_workspaces(&profile).unwrap(),
            vec![first.canonicalize().unwrap()]
        );
    }

    #[test]
    fn missing_workspace_storage_is_an_empty_list_but_a_missing_record_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let profile = dir.path().join("profile");
        assert!(list_registered_workspaces(&profile).unwrap().is_empty());
        let storage = profile.join("storages/workspace.json");
        std::fs::create_dir_all(storage.parent().unwrap()).unwrap();
        std::fs::write(
            storage,
            br#"{"global":{"workspaceIds":["missing"]},"tables":{"workspaces":{}}}"#,
        )
        .unwrap();
        assert!(list_registered_workspaces(&profile).is_err());
    }

    #[cfg(windows)]
    #[test]
    fn normalizes_windows_extended_length_prefixes_for_safety_comparisons() {
        assert_eq!(
            super::path_key(Path::new(r"\\?\C:\Users\Test\Project")),
            super::path_key(Path::new(r"C:\Users\Test\Project"))
        );
        assert_eq!(
            super::path_key(Path::new(r"\\?\UNC\server\share\Project")),
            super::path_key(Path::new(r"\\server\share\Project"))
        );
        assert_eq!(
            super::path_key(Path::new(r"C:\Users\Test\App\..\Project")),
            super::path_key(Path::new(r"C:\Users\Test\Project"))
        );
    }

    #[test]
    fn canonicalizes_existing_protected_roots_and_collapses_dot_segments() {
        let dir = tempfile::tempdir().unwrap();
        let app = dir.path().join("app");
        let profile = app.join("profiles/default");
        let runtime = app.join("runtime");
        let project = dir.path().join("project");
        std::fs::create_dir_all(&profile).unwrap();
        std::fs::create_dir_all(&runtime).unwrap();
        std::fs::create_dir_all(&project).unwrap();

        let protected = ProtectedRoots::detect_with_user_root(
            &project,
            dir.path().to_path_buf(),
            app.join("."),
            profile.join("."),
            runtime.join("."),
        )
        .unwrap();

        assert_eq!(protected.user_root, dir.path().canonicalize().unwrap());
        assert_eq!(protected.desktop_data_root, app.canonicalize().unwrap());
        assert_eq!(protected.profile_root, profile.canonicalize().unwrap());
        assert_eq!(protected.runtime_root, runtime.canonicalize().unwrap());
    }

    #[cfg(windows)]
    #[test]
    fn canonicalized_junction_alias_cannot_bypass_a_protected_root() {
        let dir = tempfile::tempdir().unwrap();
        let user = dir.path().join("real-user");
        let alias = dir.path().join("user-alias");
        let app = dir.path().join("app");
        let profile = app.join("profiles/default");
        let runtime = app.join("runtime");
        let project = user.join("Projects/demo");
        for path in [&user, &profile, &runtime, &project] {
            std::fs::create_dir_all(path).unwrap();
        }
        let output = std::process::Command::new("cmd")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&alias)
            .arg(&user)
            .output()
            .unwrap();
        if !output.status.success() {
            return;
        }

        let protected =
            ProtectedRoots::detect_with_user_root(&project, alias, app, profile, runtime).unwrap();
        // 生产流程的候选目录来自 resolve_registered_workspace 的 canonicalize 结果；
        // runner 的 TEMP 可能是 8.3 短名，先规范化再校验，保持与生产一致的比较基准。
        let canonical_user = user.canonicalize().unwrap();
        assert!(validate_recycle_target(&canonical_user, &protected).is_err());
        let canonical_root = dir.path().canonicalize().unwrap();
        assert!(validate_recycle_target(&canonical_root, &protected).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn an_existing_broken_protected_link_fails_closed() {
        let dir = tempfile::tempdir().unwrap();
        let link = dir.path().join("broken");
        std::os::unix::fs::symlink(dir.path().join("missing"), &link).unwrap();
        assert!(canonicalize_protected_root(&link, "test root").is_err());
    }

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
        let temporary = tempfile::tempdir().unwrap();
        let user_root = temporary.path().join("Users/test");
        let desktop_data_root = user_root.join("AppData/Local/dsh");
        let protected = ProtectedRoots::fixture(user_root.clone(), desktop_data_root.clone());
        for candidate in protected.all() {
            assert!(validate_recycle_target(candidate, &protected).is_err());
        }
        assert!(validate_recycle_target(&user_root.join("Projects/demo"), &protected,).is_ok());
        assert!(validate_recycle_target(&desktop_data_root.join("state"), &protected,).is_err());
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
