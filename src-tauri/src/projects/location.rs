use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::runtime::RuntimeFailure;

const MAX_PROJECT_NAME_CHARS: usize = 32;
const MAX_UNIQUE_ATTEMPTS: usize = 10_000;
const GENERIC_PREFIXES: &[&str] = &[
    "帮我做一个",
    "帮我做个",
    "创建一个",
    "创建个",
    "构建一个",
    "构建个",
    "做一个",
    "做个",
];

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectLocationPreview {
    pub project_name: String,
    pub suggested_path: PathBuf,
}

pub fn project_name(idea: &str) -> Result<String, RuntimeFailure> {
    let mut value = idea.split_whitespace().collect::<Vec<_>>().join(" ");
    if value.is_empty() {
        return Err(RuntimeFailure::internal("请先描述你想创建的项目"));
    }
    if let Some(prefix) = GENERIC_PREFIXES
        .iter()
        .find(|prefix| value.starts_with(**prefix))
    {
        value = value[prefix.len()..].trim_start().to_owned();
    }

    let mut cleaned = String::new();
    let mut previous_dash = false;
    for character in value.chars().take(MAX_PROJECT_NAME_CHARS) {
        let replacement = matches!(
            character,
            '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|' | '，' | ',' | '。' | ';' | '；'
        );
        if replacement {
            if !previous_dash && !cleaned.is_empty() {
                cleaned.push('-');
                previous_dash = true;
            }
        } else {
            cleaned.push(character);
            previous_dash = character == '-';
        }
    }

    let cleaned = cleaned
        .trim_matches(|character: char| character == ' ' || character == '.' || character == '-');
    let mut name = if cleaned.is_empty() {
        "新项目".to_owned()
    } else {
        cleaned.to_owned()
    };
    let reserved = name
        .split('.')
        .next()
        .unwrap_or_default()
        .to_ascii_uppercase();
    if matches!(reserved.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        || reserved.strip_prefix("COM").is_some_and(|value| {
            matches!(value, "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9")
        })
        || reserved.strip_prefix("LPT").is_some_and(|value| {
            matches!(value, "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9")
        })
    {
        name.push_str("-project");
    }
    Ok(name)
}

pub fn preview_project_location(
    idea: &str,
    documents: &Path,
) -> Result<ProjectLocationPreview, RuntimeFailure> {
    let project_name = project_name(idea)?;
    let root = projects_root(documents)?;
    let suggested_path = first_available_path(&root, &project_name)?;
    Ok(ProjectLocationPreview {
        project_name,
        suggested_path,
    })
}

pub fn create_project_location(
    requested_name: &str,
    documents: &Path,
) -> Result<PathBuf, RuntimeFailure> {
    let safe_name = project_name(requested_name)?;
    let root = projects_root(documents)?;
    for attempt in 1..=MAX_UNIQUE_ATTEMPTS {
        let name = candidate_name(&safe_name, attempt);
        let candidate = root.join(name);
        match std::fs::create_dir(&candidate) {
            Ok(()) => return Ok(candidate),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => {
                return Err(RuntimeFailure::internal(format!(
                    "无法创建项目目录：{error}"
                )));
            }
        }
    }
    Err(RuntimeFailure::internal("无法生成不重复的项目目录名称"))
}

fn projects_root(documents: &Path) -> Result<PathBuf, RuntimeFailure> {
    if !documents.is_absolute() {
        return Err(RuntimeFailure::internal("用户文档目录无效"));
    }
    let owner = documents.join("DeepSeek Harness");
    match std::fs::create_dir(&owner) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(_) => {
            return Err(RuntimeFailure::internal(
                "无法创建项目保存目录，请检查文档目录权限",
            ));
        }
    }
    reject_link_or_reparse(&owner)?;

    let projects = owner.join("Projects");
    match std::fs::create_dir(&projects) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(_) => {
            return Err(RuntimeFailure::internal(
                "无法创建项目保存目录，请检查文档目录权限",
            ));
        }
    }
    reject_link_or_reparse(&projects)?;
    Ok(projects)
}

fn first_available_path(root: &Path, name: &str) -> Result<PathBuf, RuntimeFailure> {
    for attempt in 1..=MAX_UNIQUE_ATTEMPTS {
        let candidate = root.join(candidate_name(name, attempt));
        if std::fs::symlink_metadata(&candidate)
            .is_err_and(|error| error.kind() == std::io::ErrorKind::NotFound)
        {
            return Ok(candidate);
        }
    }
    Err(RuntimeFailure::internal("无法生成不重复的项目目录名称"))
}

fn candidate_name(name: &str, attempt: usize) -> String {
    if attempt == 1 {
        name.to_owned()
    } else {
        format!("{name}-{attempt}")
    }
}

fn reject_link_or_reparse(path: &Path) -> Result<(), RuntimeFailure> {
    let metadata = std::fs::symlink_metadata(path)
        .map_err(|error| RuntimeFailure::internal(format!("无法检查项目保存目录：{error}")))?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;

        if metadata.file_attributes() & 0x0000_0400 != 0 {
            return Err(RuntimeFailure::internal("项目保存目录不能是链接或重解析点"));
        }
    }
    #[cfg(not(windows))]
    if metadata.file_type().is_symlink() {
        return Err(RuntimeFailure::internal("项目保存目录不能是符号链接"));
    }
    if !metadata.is_dir() {
        return Err(RuntimeFailure::internal("项目保存位置不是目录"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::*;

    #[test]
    fn derives_a_readable_safe_name_from_a_chinese_requirement() {
        assert_eq!(
            project_name("  做一个笔记页面，把我的笔记记录下来  ").unwrap(),
            "笔记页面-把我的笔记记录下来"
        );
        assert_eq!(project_name("创建一个 CON").unwrap(), "CON-project");
        assert_eq!(project_name("\\/:*?\"<>|").unwrap(), "新项目");
    }

    #[test]
    fn previews_and_creates_below_the_fixed_projects_root() {
        let documents = tempfile::tempdir().unwrap();
        let preview = preview_project_location("做一个记账应用", documents.path()).unwrap();
        assert_eq!(preview.project_name, "记账应用");
        assert_eq!(
            preview.suggested_path,
            documents
                .path()
                .join("DeepSeek Harness")
                .join("Projects")
                .join("记账应用")
        );
        let created = create_project_location(&preview.project_name, documents.path()).unwrap();
        assert_eq!(created, preview.suggested_path);
    }

    #[test]
    fn atomically_uses_incrementing_suffixes_for_existing_names() {
        let documents = tempfile::tempdir().unwrap();
        let root = documents.path().join("DeepSeek Harness").join("Projects");
        std::fs::create_dir_all(root.join("记账应用-2")).unwrap();
        std::fs::create_dir(root.join("记账应用")).unwrap();
        let created = create_project_location("记账应用", documents.path()).unwrap();
        assert_eq!(created, root.join("记账应用-3"));
    }

    #[test]
    fn rejects_a_symlinked_managed_projects_root() {
        let owner = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let managed = owner.path().join("DeepSeek Harness");
        std::fs::create_dir(&managed).unwrap();
        #[cfg(windows)]
        let linked =
            std::os::windows::fs::symlink_dir(outside.path(), managed.join("Projects")).is_ok();
        #[cfg(unix)]
        let linked = std::os::unix::fs::symlink(outside.path(), managed.join("Projects")).is_ok();
        if linked {
            assert!(create_project_location("demo", owner.path()).is_err());
            assert!(!outside.path().join("demo").exists());
        }
    }

    #[cfg(windows)]
    #[test]
    fn rejects_a_junctioned_managed_projects_root() {
        use std::os::windows::process::CommandExt;

        let owner = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let managed = owner.path().join("DeepSeek Harness");
        std::fs::create_dir(&managed).unwrap();
        let junction = managed.join("Projects");
        let status = std::process::Command::new("cmd")
            .args([
                "/d",
                "/c",
                "mklink",
                "/J",
                junction.to_string_lossy().as_ref(),
                outside.path().to_string_lossy().as_ref(),
            ])
            .creation_flags(0x08000000)
            .status();
        if !status.is_ok_and(|value| value.success()) {
            return;
        }
        assert!(create_project_location("demo", owner.path()).is_err());
        assert!(!outside.path().join("demo").exists());
    }

    #[test]
    fn rejects_relative_documents_roots() {
        assert!(preview_project_location("demo", &PathBuf::from("Documents")).is_err());
    }
}
