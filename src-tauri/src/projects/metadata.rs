use std::{collections::BTreeMap, fs, path::PathBuf};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::{runtime::RuntimeFailure, storage::atomic_json::write_atomic};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProjectMetadataSnapshot {
    pub schema_version: u32,
    pub projects: BTreeMap<String, ProjectMetadata>,
}

impl Default for ProjectMetadataSnapshot {
    fn default() -> Self {
        Self {
            schema_version: 1,
            projects: BTreeMap::new(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProjectMetadata {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cover: Option<ProjectCover>,
    pub pinned: bool,
    #[serde(default)]
    pub local_app: bool,
    pub updated_at: DateTime<Utc>,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ProjectCover {
    AuroraBlue,
    Sunset,
    Forest,
    Graphite,
    Violet,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectMetadataPatch {
    pub cover: Option<ProjectCover>,
    pub pinned: Option<bool>,
    pub local_app: Option<bool>,
}

pub struct ProjectMetadataRepository {
    path: PathBuf,
}

impl ProjectMetadataRepository {
    pub fn new(profile_data_root: PathBuf) -> Self {
        Self {
            path: profile_data_root.join("state").join("desktop-projects.json"),
        }
    }

    #[cfg(test)]
    pub fn path(&self) -> &std::path::Path {
        &self.path
    }

    pub fn snapshot(&self) -> Result<ProjectMetadataSnapshot, RuntimeFailure> {
        let bytes = match fs::read(&self.path) {
            Ok(bytes) => bytes,
            Err(cause) if cause.kind() == std::io::ErrorKind::NotFound => {
                return Ok(ProjectMetadataSnapshot::default());
            }
            Err(cause) => return Err(RuntimeFailure::internal(cause)),
        };
        match serde_json::from_slice::<ProjectMetadataSnapshot>(&bytes) {
            Ok(snapshot) if snapshot.schema_version == 1 => Ok(snapshot),
            Ok(_) | Err(_) => {
                self.quarantine_corrupt()?;
                Ok(ProjectMetadataSnapshot::default())
            }
        }
    }

    pub fn patch(
        &self,
        workspace_id: &str,
        patch: ProjectMetadataPatch,
    ) -> Result<ProjectMetadataSnapshot, RuntimeFailure> {
        validate_workspace_id(workspace_id)?;
        let mut snapshot = self.snapshot()?;
        let project = snapshot
            .projects
            .entry(workspace_id.to_string())
            .or_insert_with(|| ProjectMetadata {
                cover: None,
                pinned: false,
                local_app: false,
                updated_at: Utc::now(),
            });
        if let Some(cover) = patch.cover {
            project.cover = Some(cover);
        }
        if let Some(pinned) = patch.pinned {
            project.pinned = pinned;
        }
        if let Some(local_app) = patch.local_app {
            project.local_app = local_app;
        }
        project.updated_at = Utc::now();
        write_atomic(&self.path, &snapshot)?;
        Ok(snapshot)
    }

    pub fn remove(&self, workspace_id: &str) -> Result<ProjectMetadataSnapshot, RuntimeFailure> {
        validate_workspace_id(workspace_id)?;
        let mut snapshot = self.snapshot()?;
        if snapshot.projects.remove(workspace_id).is_some() {
            write_atomic(&self.path, &snapshot)?;
        }
        Ok(snapshot)
    }

    fn quarantine_corrupt(&self) -> Result<(), RuntimeFailure> {
        let parent = self.path.parent().ok_or_else(|| {
            RuntimeFailure::internal("项目元数据文件缺少父目录")
        })?;
        let timestamp = Utc::now().format("%Y%m%dT%H%M%S%.3fZ");
        let quarantine = parent.join(format!(
            "desktop-projects.corrupt-{timestamp}-{}.json",
            uuid::Uuid::new_v4()
        ));
        fs::rename(&self.path, quarantine).map_err(RuntimeFailure::internal)
    }
}

fn validate_workspace_id(workspace_id: &str) -> Result<(), RuntimeFailure> {
    if workspace_id.is_empty() || workspace_id.len() > 256 {
        return Err(RuntimeFailure::internal("Workspace ID 无效"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{ProjectCover, ProjectMetadataPatch, ProjectMetadataRepository};

    #[test]
    fn metadata_is_isolated_by_profile_and_corruption_falls_back() {
        let dir = tempfile::tempdir().unwrap();
        let a_root = dir.path().join("a");
        let a = ProjectMetadataRepository::new(a_root.clone());
        let b = ProjectMetadataRepository::new(dir.path().join("b"));
        a.patch(
            "w-1",
            ProjectMetadataPatch {
                cover: Some(ProjectCover::AuroraBlue),
                pinned: Some(true),
                local_app: None,
            },
        )
        .unwrap();
        assert!(a.snapshot().unwrap().projects["w-1"].pinned);
        assert!(b.snapshot().unwrap().projects.is_empty());
        std::fs::write(a.path(), b"{").unwrap();
        assert!(a.snapshot().unwrap().projects.is_empty());
        assert!(
            std::fs::read_dir(a_root.join("state"))
                .unwrap()
                .any(|entry| {
                    entry
                        .unwrap()
                        .file_name()
                        .to_string_lossy()
                        .starts_with("desktop-projects.corrupt-")
                })
        );
    }

    #[test]
    fn patch_persists_local_app_flag() {
        let dir = tempfile::tempdir().unwrap();
        let repository = ProjectMetadataRepository::new(dir.path().to_path_buf());
        repository
            .patch(
                "w-1",
                ProjectMetadataPatch {
                    cover: None,
                    pinned: None,
                    local_app: Some(true),
                },
            )
            .unwrap();
        assert!(repository.snapshot().unwrap().projects["w-1"].local_app);
    }

    #[test]
    fn patch_and_remove_leave_no_atomic_temporary_file() {
        let dir = tempfile::tempdir().unwrap();
        let repository = ProjectMetadataRepository::new(dir.path().to_path_buf());
        repository
            .patch(
                "w-1",
                ProjectMetadataPatch {
                    cover: Some(ProjectCover::Forest),
                    pinned: Some(false),
                    local_app: None,
                },
            )
            .unwrap();
        repository.remove("w-1").unwrap();
        assert!(repository.snapshot().unwrap().projects.is_empty());
        assert!(!repository.path().with_extension("json.tmp").exists());
    }
}
