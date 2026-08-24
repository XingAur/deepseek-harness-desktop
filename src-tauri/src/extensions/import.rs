use std::{fs, path::{Path, PathBuf}};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ImportCandidate {
    pub source: PathBuf,
    pub kind: &'static str,
    pub bytes: u64,
}

pub fn scan_read_only(root: &Path, kind: &'static str) -> Result<Vec<ImportCandidate>, String> {
    if !root.is_dir() {
        return Ok(Vec::new());
    }
    let mut candidates = Vec::new();
    for entry in fs::read_dir(root).map_err(|_| "外部扩展目录不可读取".to_owned())? {
        let entry = entry.map_err(|_| "外部扩展目录读取失败".to_owned())?;
        let path = entry.path();
        let metadata = fs::symlink_metadata(&path).map_err(|_| "外部扩展元数据读取失败".to_owned())?;
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            continue;
        }
        candidates.push(ImportCandidate { source: path, kind, bytes: metadata.len() });
        if candidates.len() >= 256 { break; }
    }
    Ok(candidates)
}

#[cfg(test)]
mod tests {
    use std::fs;
    use tempfile::tempdir;

    use super::scan_read_only;

    #[test]
    fn scans_external_files_without_writing_or_following_symlinks() {
        let root = tempdir().unwrap();
        fs::write(root.path().join("skill.md"), b"data").unwrap();
        let before = fs::read_dir(root.path()).unwrap().count();
        let candidates = scan_read_only(root.path(), "skill").unwrap();
        assert_eq!(candidates.len(), 1);
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), before);
    }
}
