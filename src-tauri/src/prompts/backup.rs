use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::prompts::model::{PromptsError, Result};

pub const MAX_BACKUPS_PER_TARGET: usize = 10;

/// 备份 live 文件当前内容;文件不存在时返回 Ok(None)(无需备份)。
pub fn backup_live_file(live_path: &Path, backup_root: &Path) -> Result<Option<PathBuf>> {
    let Ok(bytes) = std::fs::read(live_path) else {
        return Ok(None);
    };
    std::fs::create_dir_all(backup_root).map_err(|error| PromptsError::Io(error.to_string()))?;
    let timestamp = chrono::Utc::now().format("%Y%m%dT%H%M%S%.3fZ");
    let mut digest = Sha256::new();
    digest.update(&bytes);
    let name = format!("{timestamp}-{}.md", &hex::encode(digest.finalize())[..8]);
    let destination = backup_root.join(name);
    std::fs::write(&destination, &bytes).map_err(|error| PromptsError::Io(error.to_string()))?;
    rotate_backups(backup_root, MAX_BACKUPS_PER_TARGET)?;
    Ok(Some(destination))
}

pub fn rotate_backups(backup_root: &Path, keep: usize) -> Result<()> {
    let mut entries: Vec<PathBuf> = std::fs::read_dir(backup_root)
        .map_err(|error| PromptsError::Io(error.to_string()))?
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .collect();
    entries.sort();
    while entries.len() > keep {
        let oldest = entries.remove(0);
        std::fs::remove_file(&oldest).map_err(|error| PromptsError::Io(error.to_string()))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{backup_live_file, rotate_backups};

    #[test]
    fn backup_copies_current_content_with_target_directory() {
        let dir = tempfile::tempdir().unwrap();
        let live = dir.path().join("home/.claude/CLAUDE.md");
        std::fs::create_dir_all(live.parent().unwrap()).unwrap();
        std::fs::write(&live, b"current").unwrap();
        let backup_root = dir.path().join("backups/claude");

        let backup_path = backup_live_file(&live, &backup_root).unwrap();

        assert!(backup_path.is_some());
        let backup_path = backup_path.unwrap();
        assert!(backup_path.starts_with(&backup_root));
        assert_eq!(std::fs::read(&backup_path).unwrap(), b"current");
    }

    #[test]
    fn missing_live_file_is_not_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let live = dir.path().join("home/.claude/CLAUDE.md");
        let backup_root = dir.path().join("backups/claude");
        assert!(backup_live_file(&live, &backup_root).unwrap().is_none());
    }

    #[test]
    fn rotation_keeps_only_the_latest_ten_backups() {
        let dir = tempfile::tempdir().unwrap();
        let backup_root = dir.path().join("backups/claude");
        std::fs::create_dir_all(&backup_root).unwrap();
        for index in 0..14 {
            std::fs::write(backup_root.join(format!("20260101T0000{index:02}0Z-{index:08}.md")), b"x").unwrap();
        }
        rotate_backups(&backup_root, 10).unwrap();
        let remaining: Vec<String> = std::fs::read_dir(&backup_root)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().to_string())
            .collect();
        assert_eq!(remaining.len(), 10);
        assert!(remaining.iter().all(|name| name.contains("20260101T0000")), "保留的应是最新的 10 份");
        assert!(!remaining.iter().any(|name| name.starts_with("20260101T000000")), "最旧的 4 份应被删除");
    }
}
