use std::time::{Duration, SystemTime};

use tokio::fs;

use super::{activation::read_current, paths::RuntimePaths};

const DOWNLOAD_MAX_AGE: Duration = Duration::from_secs(7 * 24 * 60 * 60);
const LOG_FILES_KEPT: usize = 10;

/// Best-effort 清理已被取代的运行时残留。失败只浪费磁盘空间，
/// 下次启动会重试，因此所有错误都被忽略。
pub async fn sweep(paths: &RuntimePaths) {
    let _ = sweep_versions(paths).await;
    let _ = sweep_downloads(paths).await;
    let _ = sweep_logs(paths).await;
}

async fn sweep_versions(paths: &RuntimePaths) -> std::io::Result<()> {
    // 没有可信的激活指针时无法判断哪个版本是陈旧的。此时只清理明确
    // 属于中断操作的临时目录，保留所有完整版本供后续修复/回滚使用。
    let keep = read_current(paths).ok().flatten().map(|current| {
        let mut keep = vec![current.version];
        if let Some(previous) = current.previous_version {
            keep.push(previous);
        }
        keep
    });
    let mut entries = fs::read_dir(&paths.versions).await?;
    while let Some(entry) = entries.next_entry().await? {
        let name = entry.file_name().to_string_lossy().to_string();
        if name.contains(".staging-") || name.contains(".rollback-") {
            let _ = fs::remove_dir_all(entry.path()).await;
            continue;
        }
        if let Some(keep) = &keep {
            match name.parse::<semver::Version>() {
                Ok(version) if !keep.contains(&version) => {
                    let _ = fs::remove_dir_all(entry.path()).await;
                }
                _ => {}
            }
        }
    }
    Ok(())
}

async fn sweep_downloads(paths: &RuntimePaths) -> std::io::Result<()> {
    let cutoff = SystemTime::now()
        .checked_sub(DOWNLOAD_MAX_AGE)
        .unwrap_or(SystemTime::UNIX_EPOCH);
    let mut entries = fs::read_dir(&paths.downloads).await?;
    while let Some(entry) = entries.next_entry().await? {
        let Ok(metadata) = entry.metadata().await else {
            continue;
        };
        if !metadata.is_file() {
            continue;
        }
        if metadata
            .modified()
            .map(|time| time < cutoff)
            .unwrap_or(false)
        {
            let _ = fs::remove_file(entry.path()).await;
        }
    }
    Ok(())
}

async fn sweep_logs(paths: &RuntimePaths) -> std::io::Result<()> {
    // 日志文件名形如 dsh-YYYY-MM-DD.log，字典序即时间序。
    let mut names = Vec::new();
    let mut entries = fs::read_dir(&paths.logs).await?;
    while let Some(entry) = entries.next_entry().await? {
        let name = entry.file_name().to_string_lossy().to_string();
        if name.starts_with("dsh-") && name.ends_with(".log") {
            names.push(name);
        }
    }
    names.sort();
    let excess = names.len().saturating_sub(LOG_FILES_KEPT);
    for name in names.into_iter().take(excess) {
        let _ = fs::remove_file(paths.logs.join(name)).await;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn test_paths(root: &std::path::Path) -> RuntimePaths {
        RuntimePaths {
            versions: root.join("runtime/versions"),
            downloads: root.join("runtime/downloads"),
            logs: root.join("logs"),
            diagnostics: root.join("diagnostics"),
            current: root.join("runtime/current.json"),
            bundled_runtime: root.join("bundled"),
            root: root.to_path_buf(),
        }
    }

    #[tokio::test]
    async fn sweep_removes_stale_versions_and_keeps_current() {
        let temporary = tempfile::tempdir().unwrap();
        let paths = test_paths(temporary.path());
        for name in [
            "0.9.0",
            "1.0.0",
            "1.0.0.staging-op1",
            "1.0.0.rollback-op2",
            "1.1.0",
        ] {
            fs::create_dir_all(paths.versions.join(name)).unwrap();
        }
        fs::create_dir_all(paths.current.parent().unwrap()).unwrap();
        fs::write(
            &paths.current,
            serde_json::to_string(
                &serde_json::json!({ "version": "1.0.0", "previousVersion": "0.9.0" }),
            )
            .unwrap(),
        )
        .unwrap();

        sweep(&paths).await;

        assert!(paths.versions.join("1.0.0").is_dir());
        assert!(paths.versions.join("0.9.0").is_dir());
        assert!(!paths.versions.join("1.1.0").exists());
        assert!(!paths.versions.join("1.0.0.staging-op1").exists());
        assert!(!paths.versions.join("1.0.0.rollback-op2").exists());
    }

    #[tokio::test]
    async fn sweep_keeps_installed_versions_when_current_pointer_is_missing() {
        let temporary = tempfile::tempdir().unwrap();
        let paths = test_paths(temporary.path());
        fs::create_dir_all(paths.versions.join("1.0.0")).unwrap();
        fs::create_dir_all(paths.versions.join("1.1.0.staging-op1")).unwrap();

        sweep(&paths).await;

        assert!(paths.versions.join("1.0.0").is_dir());
        assert!(!paths.versions.join("1.1.0.staging-op1").exists());
    }

    #[tokio::test]
    async fn sweep_keeps_installed_versions_when_current_pointer_is_corrupt() {
        let temporary = tempfile::tempdir().unwrap();
        let paths = test_paths(temporary.path());
        fs::create_dir_all(paths.versions.join("1.0.0")).unwrap();
        fs::create_dir_all(paths.current.parent().unwrap()).unwrap();
        fs::write(&paths.current, "not-json").unwrap();

        sweep(&paths).await;

        assert!(paths.versions.join("1.0.0").is_dir());
    }
}
