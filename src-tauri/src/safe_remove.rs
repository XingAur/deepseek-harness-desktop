use std::path::Path;

use crate::runtime::model::RuntimeFailure;

#[cfg(windows)]
pub(crate) fn remove_tree_without_following_reparse_points(
    path: &Path,
) -> Result<(), RuntimeFailure> {
    remove_tree_windows(path, &mut |_| {})
}

#[cfg(windows)]
fn remove_tree_windows(
    path: &Path,
    before_enumerate: &mut dyn FnMut(&Path),
) -> Result<(), RuntimeFailure> {
    use std::{fs, mem::size_of, os::windows::ffi::OsStrExt};

    use windows_sys::Win32::{
        Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE},
        Storage::FileSystem::{
            BY_HANDLE_FILE_INFORMATION, CreateFileW, DELETE, FILE_ATTRIBUTE_DIRECTORY,
            FILE_ATTRIBUTE_READONLY, FILE_ATTRIBUTE_REPARSE_POINT, FILE_BASIC_INFO,
            FILE_DISPOSITION_FLAG_DELETE, FILE_DISPOSITION_INFO_EX, FILE_FLAG_BACKUP_SEMANTICS,
            FILE_FLAG_OPEN_REPARSE_POINT, FILE_LIST_DIRECTORY, FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_WRITE_ATTRIBUTES, FileBasicInfo,
            FileDispositionInfoEx, GetFileInformationByHandle, OPEN_EXISTING,
            SetFileInformationByHandle,
        },
    };

    struct HeldHandle(HANDLE);
    impl Drop for HeldHandle {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }

    let mut wide = path.as_os_str().encode_wide().collect::<Vec<_>>();
    wide.push(0);
    let raw = unsafe {
        CreateFileW(
            wide.as_ptr(),
            DELETE | FILE_READ_ATTRIBUTES | FILE_WRITE_ATTRIBUTES | FILE_LIST_DIRECTORY,
            // Deliberately omit FILE_SHARE_DELETE. While this handle is alive,
            // the path cannot be renamed or swapped for a junction.
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            std::ptr::null_mut(),
        )
    };
    if raw == INVALID_HANDLE_VALUE {
        return Err(RuntimeFailure::internal(format!(
            "无法安全打开待删除路径 {}：{}",
            path.display(),
            std::io::Error::last_os_error()
        )));
    }
    let handle = HeldHandle(raw);
    let mut information = unsafe { std::mem::zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    if unsafe { GetFileInformationByHandle(handle.0, &mut information) } == 0 {
        return Err(RuntimeFailure::internal(format!(
            "无法读取待删除路径的句柄信息 {}：{}",
            path.display(),
            std::io::Error::last_os_error()
        )));
    }

    let attributes = information.dwFileAttributes;
    let is_directory = attributes & FILE_ATTRIBUTE_DIRECTORY != 0;
    let is_reparse = attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0;
    if is_directory && !is_reparse {
        before_enumerate(path);
        let entries = fs::read_dir(path).map_err(|error| {
            RuntimeFailure::internal(format!("无法枚举待删除目录 {}：{error}", path.display()))
        })?;
        for entry in entries {
            let entry = entry.map_err(RuntimeFailure::internal)?;
            remove_tree_windows(&entry.path(), before_enumerate)?;
        }
    }

    if attributes & FILE_ATTRIBUTE_READONLY != 0 {
        let basic = FILE_BASIC_INFO {
            CreationTime: 0,
            LastAccessTime: 0,
            LastWriteTime: 0,
            ChangeTime: 0,
            FileAttributes: attributes & !FILE_ATTRIBUTE_READONLY,
        };
        if unsafe {
            SetFileInformationByHandle(
                handle.0,
                FileBasicInfo,
                (&basic as *const FILE_BASIC_INFO).cast(),
                size_of::<FILE_BASIC_INFO>() as u32,
            )
        } == 0
        {
            return Err(RuntimeFailure::internal(format!(
                "无法清除只读属性 {}：{}",
                path.display(),
                std::io::Error::last_os_error()
            )));
        }
    }

    let disposition = FILE_DISPOSITION_INFO_EX {
        Flags: FILE_DISPOSITION_FLAG_DELETE,
    };
    if unsafe {
        SetFileInformationByHandle(
            handle.0,
            FileDispositionInfoEx,
            (&disposition as *const FILE_DISPOSITION_INFO_EX).cast(),
            size_of::<FILE_DISPOSITION_INFO_EX>() as u32,
        )
    } == 0
    {
        return Err(RuntimeFailure::internal(format!(
            "无法按句柄删除路径 {}：{}",
            path.display(),
            std::io::Error::last_os_error()
        )));
    }
    drop(handle);
    Ok(())
}

#[cfg(not(windows))]
pub(crate) fn remove_tree_without_following_reparse_points(
    path: &Path,
) -> Result<(), RuntimeFailure> {
    use std::fs;

    let metadata = fs::symlink_metadata(path).map_err(RuntimeFailure::internal)?;
    if metadata.file_type().is_symlink() {
        return fs::remove_file(path).map_err(RuntimeFailure::internal);
    }
    if !metadata.is_dir() {
        clear_readonly(path, &metadata)?;
        return fs::remove_file(path).map_err(RuntimeFailure::internal);
    }

    for entry in fs::read_dir(path).map_err(RuntimeFailure::internal)? {
        let entry = entry.map_err(RuntimeFailure::internal)?;
        remove_tree_without_following_reparse_points(&entry.path())?;
    }
    fs::remove_dir(path).map_err(RuntimeFailure::internal)
}

#[cfg(not(windows))]
fn clear_readonly(path: &Path, metadata: &std::fs::Metadata) -> Result<(), RuntimeFailure> {
    let mut permissions = metadata.permissions();
    if permissions.readonly() {
        permissions.set_readonly(false);
        std::fs::set_permissions(path, permissions).map_err(RuntimeFailure::internal)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::*;

    #[test]
    fn removes_a_tree_without_following_directory_links() {
        let root = tempfile::tempdir().unwrap();
        let pending = root.path().join("pending");
        let outside = root.path().join("outside");
        fs::create_dir_all(pending.join("nested")).unwrap();
        fs::create_dir_all(&outside).unwrap();
        fs::write(pending.join("nested/file.txt"), "delete").unwrap();
        fs::write(outside.join("keep.txt"), "keep").unwrap();

        #[cfg(windows)]
        let link_created =
            std::os::windows::fs::symlink_dir(&outside, pending.join("outside-link")).is_ok();
        #[cfg(unix)]
        let link_created =
            std::os::unix::fs::symlink(&outside, pending.join("outside-link")).is_ok();

        remove_tree_without_following_reparse_points(&pending).unwrap();
        assert!(!pending.exists());
        assert_eq!(
            fs::read_to_string(outside.join("keep.txt")).unwrap(),
            "keep"
        );
        if link_created {
            assert!(outside.exists());
        }
    }

    #[cfg(windows)]
    #[test]
    fn removes_a_readonly_file_by_its_open_handle() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("readonly.txt");
        fs::write(&file, "delete").unwrap();
        let mut permissions = fs::metadata(&file).unwrap().permissions();
        permissions.set_readonly(true);
        fs::set_permissions(&file, permissions).unwrap();

        remove_tree_without_following_reparse_points(&file).unwrap();
        assert!(!file.exists());
    }

    #[cfg(windows)]
    #[test]
    fn removes_a_junction_without_following_its_target() {
        let root = tempfile::tempdir().unwrap();
        let pending = root.path().join("pending");
        let outside = root.path().join("outside");
        let junction = pending.join("junction");
        fs::create_dir_all(&pending).unwrap();
        fs::create_dir_all(&outside).unwrap();
        fs::write(outside.join("keep.txt"), "keep").unwrap();
        let output = std::process::Command::new("cmd")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(&outside)
            .output()
            .unwrap();
        if !output.status.success() {
            return;
        }

        remove_tree_without_following_reparse_points(&pending).unwrap();
        assert!(!pending.exists());
        assert_eq!(
            fs::read_to_string(outside.join("keep.txt")).unwrap(),
            "keep"
        );
    }

    #[cfg(windows)]
    #[test]
    fn an_open_delete_handle_blocks_concurrent_root_replacement() {
        let root = tempfile::tempdir().unwrap();
        let pending = root.path().join("pending");
        let moved = root.path().join("moved");
        fs::create_dir_all(&pending).unwrap();
        fs::write(pending.join("delete.txt"), "delete").unwrap();
        let mut replacement_blocked = false;

        remove_tree_windows(&pending, &mut |opened| {
            if opened == pending {
                replacement_blocked = fs::rename(&pending, &moved).is_err();
            }
        })
        .unwrap();

        assert!(replacement_blocked);
        assert!(!pending.exists());
        assert!(!moved.exists());
    }
}
