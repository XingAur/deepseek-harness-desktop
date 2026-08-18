use std::{fs::{self, File}, io::{Read, Write}, path::{Path, PathBuf}};

use flate2::read::GzDecoder;

use super::{model::{ArchiveKind, RuntimeFailure, RuntimeFailureCode}, paths::validate_relative_path};

const MAX_FILES: usize = 200_000;
const MAX_UNCOMPRESSED: u64 = 8 * 1024 * 1024 * 1024;

pub fn extract_archive(archive: &Path, destination: &Path, kind: ArchiveKind) -> Result<(), RuntimeFailure> {
    fs::create_dir_all(destination).map_err(RuntimeFailure::internal)?;
    match kind {
        ArchiveKind::Zip => extract_zip(archive, destination),
        ArchiveKind::TarGz => extract_tar_gz(archive, destination),
    }
}

fn confined_destination(root: &Path, value: &Path) -> Result<PathBuf, RuntimeFailure> {
    let text = value.to_str().ok_or_else(|| RuntimeFailure::new(RuntimeFailureCode::Archive, "归档路径不是 UTF-8"))?;
    let relative = validate_relative_path(text, "归档条目")?;
    Ok(root.join(relative))
}

fn extract_zip(source: &Path, destination: &Path) -> Result<(), RuntimeFailure> {
    let file = File::open(source).map_err(RuntimeFailure::internal)?;
    let mut zip = zip::ZipArchive::new(file)
        .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Archive, cause.to_string()))?;
    if zip.len() > MAX_FILES {
        return Err(RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime 归档文件数量过多"));
    }
    let mut total = 0_u64;
    for index in 0..zip.len() {
        let mut entry = zip.by_index(index)
            .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Archive, cause.to_string()))?;
        if entry.unix_mode().is_some_and(|mode| mode & 0o170000 == 0o120000) {
            return Err(RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime ZIP 不允许符号链接"));
        }
        total = total.checked_add(entry.size())
            .ok_or_else(|| RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime 解压大小溢出"))?;
        if total > MAX_UNCOMPRESSED {
            return Err(RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime 解压大小超过限制"));
        }
        let enclosed = entry.enclosed_name()
            .ok_or_else(|| RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime ZIP 包含逃逸路径"))?;
        let output = confined_destination(destination, enclosed)?;
        if entry.is_dir() {
            fs::create_dir_all(&output).map_err(RuntimeFailure::internal)?;
            continue;
        }
        if let Some(parent) = output.parent() { fs::create_dir_all(parent).map_err(RuntimeFailure::internal)?; }
        let mut target = File::create(&output).map_err(RuntimeFailure::internal)?;
        std::io::copy(&mut entry, &mut target).map_err(RuntimeFailure::internal)?;
        target.flush().map_err(RuntimeFailure::internal)?;
    }
    Ok(())
}

fn extract_tar_gz(source: &Path, destination: &Path) -> Result<(), RuntimeFailure> {
    let file = File::open(source).map_err(RuntimeFailure::internal)?;
    let mut archive = tar::Archive::new(GzDecoder::new(file));
    let mut files = 0_usize;
    let mut total = 0_u64;
    let entries = archive.entries().map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Archive, cause.to_string()))?;
    for entry in entries {
        let mut entry = entry.map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Archive, cause.to_string()))?;
        files += 1;
        if files > MAX_FILES { return Err(RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime 归档文件数量过多")); }
        let kind = entry.header().entry_type();
        if !(kind.is_file() || kind.is_dir()) {
            return Err(RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime tar.gz 不允许链接或设备条目"));
        }
        total = total.checked_add(entry.size())
            .ok_or_else(|| RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime 解压大小溢出"))?;
        if total > MAX_UNCOMPRESSED { return Err(RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime 解压大小超过限制")); }
        let path = entry.path().map_err(RuntimeFailure::internal)?;
        let output = confined_destination(destination, &path)?;
        if kind.is_dir() { fs::create_dir_all(output).map_err(RuntimeFailure::internal)?; }
        else {
            if let Some(parent) = output.parent() { fs::create_dir_all(parent).map_err(RuntimeFailure::internal)?; }
            let mut target = File::create(output).map_err(RuntimeFailure::internal)?;
            std::io::copy(&mut entry, &mut target).map_err(RuntimeFailure::internal)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_parent_paths() {
        assert!(confined_destination(Path::new("safe"), Path::new("../escape")).is_err());
    }
}
