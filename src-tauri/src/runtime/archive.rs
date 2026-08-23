use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
};

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

use flate2::read::GzDecoder;

use super::{
    model::{ArchiveKind, RuntimeFailure, RuntimeFailureCode},
    paths::validate_relative_path,
};

const MAX_FILES: usize = 200_000;
const MAX_UNCOMPRESSED: u64 = 8 * 1024 * 1024 * 1024;
const COPY_BUFFER_SIZE: usize = 64 * 1024;

pub type ExtractionProgress<'a> = &'a (dyn Fn(u64, u64) + Send + Sync);

pub fn extract_archive(
    archive: &Path,
    destination: &Path,
    kind: ArchiveKind,
) -> Result<(), RuntimeFailure> {
    extract_archive_with_progress(archive, destination, kind, &|_, _| {})
}

pub fn extract_archive_with_progress(
    archive: &Path,
    destination: &Path,
    kind: ArchiveKind,
    progress: ExtractionProgress<'_>,
) -> Result<(), RuntimeFailure> {
    fs::create_dir_all(destination).map_err(RuntimeFailure::internal)?;
    match kind {
        ArchiveKind::Zip => extract_zip(archive, destination, progress),
        ArchiveKind::TarGz => extract_tar_gz(archive, destination, progress),
    }
}

fn confined_destination(root: &Path, value: &Path) -> Result<PathBuf, RuntimeFailure> {
    let text = value
        .to_str()
        .ok_or_else(|| RuntimeFailure::new(RuntimeFailureCode::Archive, "归档路径不是 UTF-8"))?;
    let relative = validate_relative_path(text, "归档条目")?;
    Ok(root.join(relative))
}

fn validate_zip(source: &Path, destination: &Path) -> Result<u64, RuntimeFailure> {
    let file = File::open(source).map_err(RuntimeFailure::internal)?;
    let mut zip = zip::ZipArchive::new(file)
        .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Archive, cause.to_string()))?;
    if zip.len() > MAX_FILES {
        return Err(RuntimeFailure::new(
            RuntimeFailureCode::Archive,
            "Runtime 归档文件数量过多",
        ));
    }
    let mut total = 0_u64;
    for index in 0..zip.len() {
        let entry = zip
            .by_index(index)
            .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Archive, cause.to_string()))?;
        if entry
            .unix_mode()
            .is_some_and(|mode| mode & 0o170000 == 0o120000)
        {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Archive,
                "Runtime ZIP 不允许符号链接",
            ));
        }
        total = total.checked_add(entry.size()).ok_or_else(|| {
            RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime 解压大小溢出")
        })?;
        if total > MAX_UNCOMPRESSED {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Archive,
                "Runtime 解压大小超过限制",
            ));
        }
        let enclosed = entry.enclosed_name().ok_or_else(|| {
            RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime ZIP 包含逃逸路径")
        })?;
        confined_destination(destination, &enclosed)?;
    }
    Ok(total)
}

fn extract_zip(
    source: &Path,
    destination: &Path,
    progress: ExtractionProgress<'_>,
) -> Result<(), RuntimeFailure> {
    let total = validate_zip(source, destination)?;
    progress(0, total);
    let file = File::open(source).map_err(RuntimeFailure::internal)?;
    let mut zip = zip::ZipArchive::new(file)
        .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Archive, cause.to_string()))?;
    let mut completed = 0_u64;
    for index in 0..zip.len() {
        let mut entry = zip
            .by_index(index)
            .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Archive, cause.to_string()))?;
        let enclosed = entry.enclosed_name().ok_or_else(|| {
            RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime ZIP 包含逃逸路径")
        })?;
        let output = confined_destination(destination, &enclosed)?;
        if entry.is_dir() {
            fs::create_dir_all(&output).map_err(RuntimeFailure::internal)?;
            continue;
        }
        if let Some(parent) = output.parent() {
            fs::create_dir_all(parent).map_err(RuntimeFailure::internal)?;
        }
        let mut target = File::create(&output).map_err(RuntimeFailure::internal)?;
        copy_with_progress(&mut entry, &mut target, &mut completed, total, progress)?;
        target.flush().map_err(RuntimeFailure::internal)?;
    }
    progress(total, total);
    Ok(())
}

fn validate_tar_gz(source: &Path, destination: &Path) -> Result<u64, RuntimeFailure> {
    let file = File::open(source).map_err(RuntimeFailure::internal)?;
    let mut archive = tar::Archive::new(GzDecoder::new(file));
    let mut files = 0_usize;
    let mut total = 0_u64;
    let entries = archive
        .entries()
        .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Archive, cause.to_string()))?;
    for entry in entries {
        let entry = entry
            .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Archive, cause.to_string()))?;
        files += 1;
        if files > MAX_FILES {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Archive,
                "Runtime 归档文件数量过多",
            ));
        }
        let kind = entry.header().entry_type();
        if !(kind.is_file() || kind.is_dir()) {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Archive,
                "Runtime tar.gz 不允许链接或设备条目",
            ));
        }
        total = total.checked_add(entry.size()).ok_or_else(|| {
            RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime 解压大小溢出")
        })?;
        if total > MAX_UNCOMPRESSED {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Archive,
                "Runtime 解压大小超过限制",
            ));
        }
        let path = entry.path().map_err(RuntimeFailure::internal)?;
        confined_destination(destination, &path)?;
    }
    Ok(total)
}

fn extract_tar_gz(
    source: &Path,
    destination: &Path,
    progress: ExtractionProgress<'_>,
) -> Result<(), RuntimeFailure> {
    let total = validate_tar_gz(source, destination)?;
    progress(0, total);
    let file = File::open(source).map_err(RuntimeFailure::internal)?;
    let mut archive = tar::Archive::new(GzDecoder::new(file));
    let entries = archive
        .entries()
        .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Archive, cause.to_string()))?;
    let mut completed = 0_u64;
    for entry in entries {
        let mut entry = entry
            .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Archive, cause.to_string()))?;
        let kind = entry.header().entry_type();
        let mode = entry.header().mode().map_err(RuntimeFailure::internal)?;
        let path = entry.path().map_err(RuntimeFailure::internal)?;
        let output = confined_destination(destination, &path)?;
        if kind.is_dir() {
            fs::create_dir_all(&output).map_err(RuntimeFailure::internal)?;
            restore_permissions(&output, mode)?;
        } else {
            if let Some(parent) = output.parent() {
                fs::create_dir_all(parent).map_err(RuntimeFailure::internal)?;
            }
            let mut target = File::create(&output).map_err(RuntimeFailure::internal)?;
            copy_with_progress(&mut entry, &mut target, &mut completed, total, progress)?;
            target.flush().map_err(RuntimeFailure::internal)?;
            restore_permissions(&output, mode)?;
        }
    }
    progress(total, total);
    Ok(())
}

fn restore_permissions(path: &Path, mode: u32) -> Result<(), RuntimeFailure> {
    #[cfg(unix)]
    {
        fs::set_permissions(path, fs::Permissions::from_mode(mode & 0o7777))
            .map_err(RuntimeFailure::internal)?;
    }
    #[cfg(not(unix))]
    {
        let _ = (path, mode);
    }
    Ok(())
}

fn copy_with_progress(
    source: &mut impl Read,
    destination: &mut impl Write,
    completed: &mut u64,
    total: u64,
    progress: ExtractionProgress<'_>,
) -> Result<(), RuntimeFailure> {
    let mut buffer = [0_u8; COPY_BUFFER_SIZE];
    loop {
        let read = source.read(&mut buffer).map_err(RuntimeFailure::internal)?;
        if read == 0 {
            return Ok(());
        }
        destination
            .write_all(&buffer[..read])
            .map_err(RuntimeFailure::internal)?;
        *completed = completed.checked_add(read as u64).ok_or_else(|| {
            RuntimeFailure::new(RuntimeFailureCode::Archive, "Runtime 解压进度溢出")
        })?;
        progress(*completed, total);
    }
}

#[cfg(test)]
mod tests {
    use std::{io::Write, sync::Mutex};

    use flate2::{Compression, write::GzEncoder};
    use zip::write::SimpleFileOptions;

    use super::*;

    #[test]
    fn rejects_parent_paths() {
        assert!(confined_destination(Path::new("safe"), Path::new("../escape")).is_err());
    }

    #[test]
    fn reports_monotonic_zip_extraction_progress() {
        let temporary = tempfile::tempdir().unwrap();
        let archive = temporary.path().join("runtime.zip");
        let destination = temporary.path().join("output");
        let file = File::create(&archive).unwrap();
        let mut writer = zip::ZipWriter::new(file);
        let options = SimpleFileOptions::default();
        writer.start_file("first.txt", options).unwrap();
        writer.write_all(b"four").unwrap();
        writer.start_file("second.txt", options).unwrap();
        writer.write_all(b"fives").unwrap();
        writer.finish().unwrap();
        let events = Mutex::new(Vec::new());

        extract_archive_with_progress(&archive, &destination, ArchiveKind::Zip, &|done, total| {
            events.lock().unwrap().push((done, total));
        })
        .unwrap();

        assert_progress(events.into_inner().unwrap(), 9);
    }

    #[test]
    fn reports_monotonic_tar_gz_extraction_progress() {
        let temporary = tempfile::tempdir().unwrap();
        let archive = temporary.path().join("runtime.tar.gz");
        let destination = temporary.path().join("output");
        let encoder = GzEncoder::new(File::create(&archive).unwrap(), Compression::default());
        let mut builder = tar::Builder::new(encoder);
        append_tar_file(&mut builder, "first.txt", b"four");
        append_tar_file(&mut builder, "second.txt", b"fives");
        builder.into_inner().unwrap().finish().unwrap();
        let events = Mutex::new(Vec::new());

        extract_archive_with_progress(
            &archive,
            &destination,
            ArchiveKind::TarGz,
            &|done, total| events.lock().unwrap().push((done, total)),
        )
        .unwrap();

        assert_progress(events.into_inner().unwrap(), 9);
    }

    #[cfg(unix)]
    #[test]
    fn restores_tar_file_execute_permissions() {
        use std::os::unix::fs::PermissionsExt;

        let temporary = tempfile::tempdir().unwrap();
        let archive = temporary.path().join("runtime.tar.gz");
        let destination = temporary.path().join("output");
        let encoder = GzEncoder::new(File::create(&archive).unwrap(), Compression::default());
        let mut builder = tar::Builder::new(encoder);
        append_tar_file_with_mode(&mut builder, "bin/node", b"node", 0o755);
        builder.into_inner().unwrap().finish().unwrap();

        extract_archive(&archive, &destination, ArchiveKind::TarGz).unwrap();

        let mode = fs::metadata(destination.join("bin/node"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o111, 0o111);
    }

    fn append_tar_file(builder: &mut tar::Builder<GzEncoder<File>>, path: &str, contents: &[u8]) {
        append_tar_file_with_mode(builder, path, contents, 0o644);
    }

    fn append_tar_file_with_mode(
        builder: &mut tar::Builder<GzEncoder<File>>,
        path: &str,
        contents: &[u8],
        mode: u32,
    ) {
        let mut header = tar::Header::new_gnu();
        header.set_size(contents.len() as u64);
        header.set_mode(mode);
        header.set_cksum();
        builder.append_data(&mut header, path, contents).unwrap();
    }

    fn assert_progress(events: Vec<(u64, u64)>, total: u64) {
        assert_eq!(events.first(), Some(&(0, total)));
        assert_eq!(events.last(), Some(&(total, total)));
        assert!(events.windows(2).all(|pair| pair[0].0 <= pair[1].0));
        assert!(events.iter().all(|(done, size)| *done <= *size));
    }
}
