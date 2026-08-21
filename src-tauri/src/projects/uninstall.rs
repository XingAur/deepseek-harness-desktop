use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

use crate::{
    data_cleanup::live_app_data_root, profile::model::ProfileRecord,
    runtime::model::RuntimeFailure, safe_remove::remove_tree_without_following_reparse_points,
    storage::atomic_json::read_optional,
};

use super::recycle::{
    ProtectedRoots, list_registered_workspaces, path_key, validate_recycle_target,
};

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ProjectInventory {
    pub projects: Vec<PathBuf>,
    pub failures: Vec<String>,
}

const PREVIEW_PREFIX: &str = "deepseek-harness-uninstall-projects-";
const REPORT_PREFIX: &str = "deepseek-harness-uninstall-report-";

pub(crate) fn collect_registered_projects(app_root: &Path) -> ProjectInventory {
    let profiles_path = app_root.join("profiles/profiles.json");
    let profiles = match read_optional::<Vec<ProfileRecord>>(&profiles_path) {
        Ok(Some(profiles)) => profiles,
        Ok(None) => return ProjectInventory::default(),
        Err(error) => {
            return ProjectInventory {
                projects: Vec::new(),
                failures: vec![error.message],
            };
        }
    };
    let runtime_root = app_root.join("runtime");
    let mut projects = BTreeMap::<String, PathBuf>::new();
    let mut failures = Vec::new();

    for profile in profiles {
        if !profile.data_root.is_absolute() {
            failures.push(format!("{}：Profile 数据目录不是绝对路径", profile.name));
            continue;
        }
        match list_registered_workspaces(&profile.data_root) {
            Ok(paths) => {
                for candidate in paths {
                    let result = ProtectedRoots::detect(
                        &candidate,
                        app_root.to_path_buf(),
                        profile.data_root.clone(),
                        runtime_root.clone(),
                    )
                    .and_then(|protected| validate_recycle_target(&candidate, &protected));
                    match result {
                        Ok(()) => {
                            projects.entry(path_key(&candidate)).or_insert(candidate);
                        }
                        Err(error) => failures.push(format!("{}：{}", profile.name, error.message)),
                    }
                }
            }
            Err(error) => failures.push(format!("{}：{}", profile.name, error.message)),
        }
    }

    ProjectInventory {
        projects: projects.into_values().collect(),
        failures,
    }
}

pub(crate) fn preview_path(token: u32) -> PathBuf {
    std::env::temp_dir().join(format!("{PREVIEW_PREFIX}{token}.txt"))
}

pub(crate) fn report_path(token: u32) -> PathBuf {
    std::env::temp_dir().join(format!("{REPORT_PREFIX}{token}.txt"))
}

fn utf16_bytes(text: &str) -> Vec<u8> {
    let mut bytes = vec![0xff, 0xfe];
    for unit in text.encode_utf16() {
        bytes.extend_from_slice(&unit.to_le_bytes());
    }
    bytes
}

fn write_new_utf16(path: &Path, text: &str) -> Result<(), RuntimeFailure> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let mut created = false;
    let result = (|| {
        let mut file = options.open(path).map_err(|error| {
            RuntimeFailure::internal(format!(
                "拒绝覆盖已有或不安全的卸载临时文件 {}：{error}",
                path.display()
            ))
        })?;
        created = true;
        let metadata = file.metadata().map_err(RuntimeFailure::internal)?;
        if !metadata.file_type().is_file() {
            return Err(RuntimeFailure::internal("卸载临时文件不是普通文件"));
        }
        file.write_all(&utf16_bytes(text))
            .map_err(RuntimeFailure::internal)?;
        file.sync_all().map_err(RuntimeFailure::internal)
    })();
    if result.is_err() && created {
        let _ = fs::remove_file(path);
    }
    result
}

fn inventory_text(inventory: &ProjectInventory) -> String {
    let mut lines = vec![format!("COUNT={}", inventory.projects.len())];
    lines.extend(
        inventory
            .projects
            .iter()
            .map(|path| path.display().to_string()),
    );
    format!("{}\r\n", lines.join("\r\n"))
}

pub(crate) fn write_preview(token: u32) -> Result<(), RuntimeFailure> {
    if token == 0 {
        return Err(RuntimeFailure::internal("卸载进程标识无效"));
    }
    let inventory = collect_registered_projects(&live_app_data_root()?);
    if !inventory.failures.is_empty() {
        write_new_utf16(&report_path(token), &inventory.failures.join("\r\n"))?;
        return Err(RuntimeFailure::internal("无法安全读取全部本地项目"));
    }
    write_new_utf16(&preview_path(token), &inventory_text(&inventory))
}

pub(crate) fn cleanup_projects(token: u32) -> Result<(), RuntimeFailure> {
    if token == 0 {
        return Err(RuntimeFailure::internal("卸载进程标识无效"));
    }
    let inventory = collect_registered_projects(&live_app_data_root()?);
    if !inventory.failures.is_empty() {
        write_new_utf16(&report_path(token), &inventory.failures.join("\r\n"))?;
        return Err(RuntimeFailure::internal("项目清单包含不安全或损坏的记录"));
    }

    let failures = delete_inventory(&inventory);
    if failures.is_empty() {
        write_new_utf16(&report_path(token), "OK\r\n")?;
        Ok(())
    } else {
        write_new_utf16(&report_path(token), &failures.join("\r\n"))?;
        Err(RuntimeFailure::internal("部分本地项目未能删除"))
    }
}

fn delete_inventory(inventory: &ProjectInventory) -> Vec<String> {
    if !inventory.failures.is_empty() {
        return inventory.failures.clone();
    }
    let mut failures = Vec::new();
    for project in &inventory.projects {
        if let Err(error) = remove_tree_without_following_reparse_points(project) {
            failures.push(format!("{}：{}", project.display(), error.message));
        }
    }
    failures
}

#[cfg(test)]
mod tests {
    use chrono::Utc;
    use uuid::Uuid;

    use super::*;
    use crate::profile::model::PermissionMode;

    fn profile(name: &str, data_root: PathBuf) -> ProfileRecord {
        ProfileRecord {
            id: Uuid::new_v4(),
            name: name.into(),
            data_root,
            permission_mode: PermissionMode::WorkspaceWrite,
            revision: 1,
            created_at: Utc::now(),
            updated_at: Utc::now(),
        }
    }

    fn write_profiles(app_root: &Path, profiles: &[ProfileRecord]) {
        let path = app_root.join("profiles/profiles.json");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, serde_json::to_vec_pretty(profiles).unwrap()).unwrap();
    }

    fn write_workspace(profile_root: &Path, id: &str, project: &Path) {
        let path = profile_root.join("storages/workspace.json");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(
            path,
            serde_json::to_vec_pretty(&serde_json::json!({
                "global": { "workspaceIds": [id] },
                "tables": { "workspaces": { (id): { "path": project } } }
            }))
            .unwrap(),
        )
        .unwrap();
    }

    #[test]
    fn collects_and_deduplicates_registered_projects_across_profiles() {
        let root = tempfile::tempdir().unwrap();
        let app = root.path().join("app-data");
        let project = root.path().join("project");
        let profile_a = app.join("profiles/a");
        let profile_b = app.join("profiles/b");
        fs::create_dir_all(&project).unwrap();
        write_workspace(&profile_a, "a", &project);
        write_workspace(&profile_b, "b", &project);
        write_profiles(&app, &[profile("A", profile_a), profile("B", profile_b)]);

        let inventory = collect_registered_projects(&app);
        assert!(inventory.failures.is_empty());
        assert_eq!(inventory.projects, vec![project.canonicalize().unwrap()]);
    }

    #[test]
    fn malformed_profile_storage_blocks_a_successful_inventory() {
        let root = tempfile::tempdir().unwrap();
        let app = root.path().join("app-data");
        let profile_root = app.join("profiles/broken");
        fs::create_dir_all(profile_root.join("storages")).unwrap();
        fs::write(profile_root.join("storages/workspace.json"), b"{").unwrap();
        write_profiles(&app, &[profile("Broken", profile_root)]);

        let inventory = collect_registered_projects(&app);
        assert!(inventory.projects.is_empty());
        assert_eq!(inventory.failures.len(), 1);
    }

    #[test]
    fn a_relative_profile_root_is_rejected_without_reading_the_working_directory() {
        let root = tempfile::tempdir().unwrap();
        let app = root.path().join("app-data");
        write_profiles(
            &app,
            &[profile("Relative", PathBuf::from("relative-profile"))],
        );

        let inventory = collect_registered_projects(&app);
        assert!(inventory.projects.is_empty());
        assert_eq!(inventory.failures.len(), 1);
        assert!(inventory.failures[0].contains("不是绝对路径"));
    }

    #[test]
    fn a_registered_path_inside_the_managed_runtime_blocks_the_inventory() {
        let root = tempfile::tempdir().unwrap();
        let app = root.path().join("app-data");
        let profile_root = app.join("profiles/default");
        let protected_project = app.join("runtime/project");
        fs::create_dir_all(&protected_project).unwrap();
        write_workspace(&profile_root, "protected", &protected_project);
        write_profiles(&app, &[profile("Default", profile_root)]);

        let inventory = collect_registered_projects(&app);
        assert!(inventory.projects.is_empty());
        assert_eq!(inventory.failures.len(), 1);
        assert!(inventory.failures[0].contains("已拒绝删除"));
    }

    #[test]
    fn deletes_each_deduplicated_project_once() {
        let root = tempfile::tempdir().unwrap();
        let project = root.path().join("project");
        fs::create_dir_all(&project).unwrap();
        fs::write(project.join("file.txt"), "delete").unwrap();
        let inventory = ProjectInventory {
            projects: vec![project.clone()],
            failures: Vec::new(),
        };

        assert!(delete_inventory(&inventory).is_empty());
        assert!(!project.exists());
    }

    #[test]
    fn recursive_delete_does_not_follow_a_directory_link() {
        let root = tempfile::tempdir().unwrap();
        let project = root.path().join("project");
        let outside = root.path().join("outside");
        fs::create_dir_all(&project).unwrap();
        fs::create_dir_all(&outside).unwrap();
        fs::write(outside.join("keep.txt"), "keep").unwrap();

        #[cfg(windows)]
        let link_created =
            std::os::windows::fs::symlink_dir(&outside, project.join("outside-link")).is_ok();
        #[cfg(unix)]
        let link_created =
            std::os::unix::fs::symlink(&outside, project.join("outside-link")).is_ok();

        let failures = delete_inventory(&ProjectInventory {
            projects: vec![project],
            failures: Vec::new(),
        });
        assert!(failures.is_empty());
        assert_eq!(
            fs::read_to_string(outside.join("keep.txt")).unwrap(),
            "keep"
        );
        if link_created {
            assert!(outside.exists());
        }
    }

    #[test]
    fn inventory_failures_prevent_the_cleanup_entrypoint_from_deleting_projects() {
        let root = tempfile::tempdir().unwrap();
        let project = root.path().join("project");
        fs::create_dir_all(&project).unwrap();
        let inventory = ProjectInventory {
            projects: vec![project.clone()],
            failures: vec!["broken profile".into()],
        };

        assert_eq!(delete_inventory(&inventory), vec!["broken profile"]);
        assert!(project.exists());
    }

    #[test]
    fn partial_delete_returns_the_exact_failed_path() {
        let root = tempfile::tempdir().unwrap();
        let removable = root.path().join("removable");
        let missing = root.path().join("missing");
        fs::create_dir_all(&removable).unwrap();
        let failures = delete_inventory(&ProjectInventory {
            projects: vec![removable.clone(), missing.clone()],
            failures: Vec::new(),
        });

        assert!(!removable.exists());
        assert_eq!(failures.len(), 1);
        assert!(failures[0].contains(&missing.display().to_string()));
    }

    #[test]
    fn formats_preview_as_utf16_ready_count_and_paths() {
        let inventory = ProjectInventory {
            projects: vec![PathBuf::from(r"C:\Projects\first")],
            failures: Vec::new(),
        };
        assert_eq!(
            inventory_text(&inventory),
            "COUNT=1\r\nC:\\Projects\\first\r\n"
        );
    }

    #[test]
    fn writes_utf16le_with_a_bom() {
        let root = tempfile::tempdir().unwrap();
        let output = root.path().join("preview.txt");

        write_new_utf16(&output, "COUNT=0\r\n").unwrap();

        let bytes = fs::read(output).unwrap();
        assert_eq!(&bytes[..2], &[0xff, 0xfe]);
        let units = bytes[2..]
            .chunks_exact(2)
            .map(|chunk| u16::from_le_bytes([chunk[0], chunk[1]]))
            .collect::<Vec<_>>();
        assert_eq!(String::from_utf16(&units).unwrap(), "COUNT=0\r\n");
    }

    #[test]
    fn refuses_to_overwrite_any_existing_uninstall_temp_entry() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("existing.txt");
        let directory = root.path().join("directory.txt");
        fs::write(&file, "attacker").unwrap();
        fs::create_dir(&directory).unwrap();

        assert!(write_new_utf16(&file, "safe").is_err());
        assert_eq!(fs::read_to_string(&file).unwrap(), "attacker");
        assert!(write_new_utf16(&directory, "safe").is_err());
        assert!(directory.is_dir());
    }

    #[test]
    fn refuses_an_existing_link_as_an_uninstall_temp_entry() {
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("target.txt");
        let link = root.path().join("link.txt");
        fs::write(&target, "keep").unwrap();
        #[cfg(windows)]
        let linked = std::os::windows::fs::symlink_file(&target, &link).is_ok();
        #[cfg(unix)]
        let linked = std::os::unix::fs::symlink(&target, &link).is_ok();
        if !linked {
            return;
        }

        assert!(write_new_utf16(&link, "overwrite").is_err());
        assert_eq!(fs::read_to_string(target).unwrap(), "keep");
    }
}
