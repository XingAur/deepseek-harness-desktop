use std::{
    fs,
    path::{Path, PathBuf},
};

use super::{
    archive::extract_archive,
    model::{CurrentRuntime, RuntimeFailure, RuntimeManifest},
    paths::RuntimePaths,
};

pub struct ActivationReceipt {
    installed_version: semver::Version,
    previous: Option<CurrentRuntime>,
    replaced_directory: Option<PathBuf>,
    candidate_restore: Option<PathBuf>,
}

pub fn read_current(paths: &RuntimePaths) -> Result<Option<CurrentRuntime>, RuntimeFailure> {
    match fs::read(&paths.current) {
        Ok(bytes) => serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(RuntimeFailure::internal),
        Err(cause) if cause.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(cause) => Err(RuntimeFailure::internal(cause)),
    }
}

pub fn read_active_manifest(
    paths: &RuntimePaths,
) -> Result<Option<RuntimeManifest>, RuntimeFailure> {
    let Some(current) = read_current(paths)? else {
        return Ok(None);
    };
    let bytes = fs::read(paths.version_dir(&current.version).join("manifest.json"))
        .map_err(RuntimeFailure::internal)?;
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(RuntimeFailure::internal)
}

pub fn stage_and_activate(
    paths: &RuntimePaths,
    archive: &Path,
    manifest: &RuntimeManifest,
    operation_id: &str,
) -> Result<ActivationReceipt, RuntimeFailure> {
    let receipt = stage(paths, archive, manifest, operation_id)?;
    if let Err(cause) = activate(paths, &receipt) {
        let _ = rollback(paths, receipt);
        return Err(cause);
    }
    Ok(receipt)
}

pub fn stage(
    paths: &RuntimePaths,
    archive: &Path,
    manifest: &RuntimeManifest,
    operation_id: &str,
) -> Result<ActivationReceipt, RuntimeFailure> {
    let previous = read_current(paths)?;
    let final_dir = paths.version_dir(&manifest.version);
    let staging = paths
        .versions
        .join(format!("{}.staging-{operation_id}", manifest.version));
    let backup = paths
        .versions
        .join(format!("{}.rollback-{operation_id}", manifest.version));
    if staging.exists() {
        fs::remove_dir_all(&staging).map_err(RuntimeFailure::internal)?;
    }
    if backup.exists() {
        fs::remove_dir_all(&backup).map_err(RuntimeFailure::internal)?;
    }
    fs::create_dir_all(&staging).map_err(RuntimeFailure::internal)?;
    if let Err(cause) = extract_archive(archive, &staging, manifest.archive) {
        let _ = fs::remove_dir_all(&staging);
        return Err(cause);
    }
    fs::write(
        staging.join("manifest.json"),
        serde_json::to_vec_pretty(manifest).map_err(RuntimeFailure::internal)?,
    )
    .map_err(RuntimeFailure::internal)?;
    let replaced_directory = if final_dir.exists() {
        fs::rename(&final_dir, &backup).map_err(RuntimeFailure::internal)?;
        Some(backup)
    } else {
        None
    };
    if let Err(cause) = fs::rename(&staging, &final_dir) {
        if let Some(replaced) = &replaced_directory {
            let _ = fs::rename(replaced, &final_dir);
        }
        return Err(RuntimeFailure::internal(cause));
    }
    Ok(ActivationReceipt {
        installed_version: manifest.version.clone(),
        previous,
        replaced_directory,
        candidate_restore: None,
    })
}

pub fn stage_candidate(
    paths: &RuntimePaths,
    candidate: &Path,
    manifest: &RuntimeManifest,
    operation_id: &str,
) -> Result<ActivationReceipt, RuntimeFailure> {
    let previous = read_current(paths)?;
    let final_dir = paths.version_dir(&manifest.version);
    let backup = paths
        .versions
        .join(format!("{}.rollback-{operation_id}", manifest.version));
    if backup.exists() {
        fs::remove_dir_all(&backup).map_err(RuntimeFailure::internal)?;
    }
    let replaced_directory = if final_dir.exists() {
        fs::rename(&final_dir, &backup).map_err(RuntimeFailure::internal)?;
        Some(backup)
    } else {
        None
    };
    if let Err(cause) = fs::rename(candidate, &final_dir) {
        if let Some(replaced) = &replaced_directory {
            let _ = fs::rename(replaced, &final_dir);
        }
        return Err(RuntimeFailure::internal(cause));
    }
    Ok(ActivationReceipt {
        installed_version: manifest.version.clone(),
        previous,
        replaced_directory,
        candidate_restore: Some(candidate.to_path_buf()),
    })
}

pub fn activate(paths: &RuntimePaths, receipt: &ActivationReceipt) -> Result<(), RuntimeFailure> {
    let next = CurrentRuntime {
        version: receipt.installed_version.clone(),
        previous_version: receipt
            .previous
            .as_ref()
            .map(|current| current.version.clone()),
    };
    write_current(paths, &next)
}

pub fn commit(receipt: ActivationReceipt) -> Result<(), RuntimeFailure> {
    if let Some(replaced) = receipt.replaced_directory {
        if replaced.exists() {
            // Pointer 已提交后，旧目录只属于可延迟清理的缓存；清理失败不能把
            // 已经健康运行的新 Runtime 重新报告为激活失败。
            let _ = fs::remove_dir_all(replaced);
        }
    }
    Ok(())
}

pub fn rollback(paths: &RuntimePaths, failed: ActivationReceipt) -> Result<(), RuntimeFailure> {
    let final_dir = paths.version_dir(&failed.installed_version);
    if final_dir.exists() {
        if let Some(candidate) = &failed.candidate_restore {
            if let Some(parent) = candidate.parent() {
                fs::create_dir_all(parent).map_err(RuntimeFailure::internal)?;
            }
            if candidate.exists() {
                fs::remove_dir_all(candidate).map_err(RuntimeFailure::internal)?;
            }
            fs::rename(&final_dir, candidate).map_err(RuntimeFailure::internal)?;
        } else {
            fs::remove_dir_all(&final_dir).map_err(RuntimeFailure::internal)?;
        }
    }
    if let Some(replaced) = failed.replaced_directory {
        fs::rename(replaced, &final_dir).map_err(RuntimeFailure::internal)?;
    }
    match failed.previous {
        Some(previous) => write_current(paths, &previous),
        None => {
            if paths.current.exists() {
                fs::remove_file(&paths.current).map_err(RuntimeFailure::internal)?;
            }
            Ok(())
        }
    }
}

fn write_current(paths: &RuntimePaths, current: &CurrentRuntime) -> Result<(), RuntimeFailure> {
    let parent = paths
        .current
        .parent()
        .ok_or_else(|| RuntimeFailure::internal("current.json has no parent"))?;
    fs::create_dir_all(parent).map_err(RuntimeFailure::internal)?;
    let temporary: PathBuf = paths.current.with_extension("json.tmp");
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(current).map_err(RuntimeFailure::internal)?,
    )
    .map_err(RuntimeFailure::internal)?;
    if paths.current.exists() {
        fs::remove_file(&paths.current).map_err(RuntimeFailure::internal)?;
    }
    fs::rename(temporary, &paths.current).map_err(RuntimeFailure::internal)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rollback_restores_same_version_directory_and_pointer() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().to_path_buf();
        let paths = RuntimePaths {
            versions: root.join("runtime/versions"),
            downloads: root.join("runtime/downloads"),
            logs: root.join("logs"),
            diagnostics: root.join("diagnostics"),
            current: root.join("runtime/current.json"),
            bundled_runtime: root.join("bundled"),
            root,
        };
        fs::create_dir_all(&paths.versions).unwrap();
        let version: semver::Version = "1.0.0".parse().unwrap();
        let final_dir = paths.version_dir(&version);
        let backup = paths.versions.join("1.0.0.rollback-test");
        fs::create_dir_all(&final_dir).unwrap();
        fs::create_dir_all(&backup).unwrap();
        fs::write(backup.join("old.txt"), b"old").unwrap();
        let previous = CurrentRuntime {
            version: version.clone(),
            previous_version: None,
        };
        write_current(
            &paths,
            &CurrentRuntime {
                version: version.clone(),
                previous_version: Some(version.clone()),
            },
        )
        .unwrap();

        rollback(
            &paths,
            ActivationReceipt {
                installed_version: version,
                previous: Some(previous.clone()),
                replaced_directory: Some(backup),
                candidate_restore: None,
            },
        )
        .unwrap();

        assert!(final_dir.join("old.txt").is_file());
        assert_eq!(
            read_current(&paths).unwrap().unwrap().previous_version,
            previous.previous_version
        );
    }
}
