use std::{
    fs::{self, File, OpenOptions},
    io::Write,
    path::Path,
};

use serde::{Serialize, de::DeserializeOwned};

use crate::runtime::model::RuntimeFailure;

pub fn read_optional<T: DeserializeOwned>(path: &Path) -> Result<Option<T>, RuntimeFailure> {
    match fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes).map(Some).map_err(|cause| {
            RuntimeFailure::internal(format!("读取 {} 失败: {cause}", path.display()))
        }),
        Err(cause) if cause.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(cause) => Err(RuntimeFailure::internal(format!(
            "读取 {} 失败: {cause}",
            path.display()
        ))),
    }
}

pub fn write_atomic<T: Serialize>(path: &Path, value: &T) -> Result<(), RuntimeFailure> {
    let parent = path
        .parent()
        .ok_or_else(|| RuntimeFailure::internal(format!("{} 没有父目录", path.display())))?;
    fs::create_dir_all(parent).map_err(RuntimeFailure::internal)?;

    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .map(|value| format!("{value}.tmp"))
        .unwrap_or_else(|| "tmp".to_string());
    let temporary = path.with_extension(extension);
    let backup = path.with_extension(
        path.extension()
            .and_then(|value| value.to_str())
            .map(|value| format!("{value}.bak"))
            .unwrap_or_else(|| "bak".to_string()),
    );

    remove_if_exists(&temporary)?;
    let bytes = serde_json::to_vec_pretty(value).map_err(RuntimeFailure::internal)?;
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temporary)
        .map_err(RuntimeFailure::internal)?;
    if let Err(cause) = file.write_all(&bytes).and_then(|_| file.sync_all()) {
        let _ = fs::remove_file(&temporary);
        return Err(RuntimeFailure::internal(cause));
    }
    drop(file);

    let had_previous = path.exists();
    if had_previous {
        remove_if_exists(&backup)?;
        if let Err(cause) = fs::rename(path, &backup) {
            let _ = fs::remove_file(&temporary);
            return Err(RuntimeFailure::internal(cause));
        }
    }

    if let Err(cause) = fs::rename(&temporary, path) {
        if had_previous {
            let _ = fs::rename(&backup, path);
        }
        let _ = fs::remove_file(&temporary);
        return Err(RuntimeFailure::internal(cause));
    }

    sync_directory_if_supported(parent);
    if had_previous {
        remove_if_exists(&backup)?;
    }
    Ok(())
}

fn remove_if_exists(path: &Path) -> Result<(), RuntimeFailure> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(cause) if cause.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(cause) => Err(RuntimeFailure::internal(cause)),
    }
}

fn sync_directory_if_supported(path: &Path) {
    if let Ok(directory) = File::open(path) {
        let _ = directory.sync_all();
    }
}

#[cfg(test)]
mod tests {
    use super::{read_optional, write_atomic};

    #[test]
    fn replaces_json_and_leaves_no_temporary_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        write_atomic(&path, &serde_json::json!({"revision": 1})).unwrap();
        write_atomic(&path, &serde_json::json!({"revision": 2})).unwrap();
        assert_eq!(
            read_optional::<serde_json::Value>(&path).unwrap().unwrap()["revision"],
            2
        );
        assert!(!path.with_extension("json.tmp").exists());
    }

    #[test]
    fn missing_file_is_none_and_invalid_json_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        assert!(read_optional::<serde_json::Value>(&path).unwrap().is_none());
        std::fs::write(&path, b"{").unwrap();
        assert!(read_optional::<serde_json::Value>(&path).is_err());
    }
}
