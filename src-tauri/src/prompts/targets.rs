use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::prompts::model::{PromptTarget, PromptsError, Result};

/// 受管 DeepSeek Harness 的全局提示词文件名。
/// Task 1 spike 已确认:@deepseek-ai/dsh-agent-instructions 的 USER_GLOBAL_FILE = "AGENTS.md",
/// 全局层只有 $DSH_HOME/AGENTS.md(桌面侧 DSH_HOME=profile.data_root),置信度高。
pub const DSH_GLOBAL_PROMPT_FILENAME: &str = "AGENTS.md";

#[derive(Clone, Debug)]
pub struct TargetPaths {
    pub installed: bool,
    pub prompt_file: PathBuf,
}

pub fn detect_home() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .filter(|value| !value.is_empty())
        .or_else(|| std::env::var_os("HOME").filter(|value| !value.is_empty()))
        .map(PathBuf::from)
        .filter(|path| path.is_dir())
}

pub fn claude_dir(home: &Path) -> PathBuf {
    home.join(".claude")
}

pub fn codex_dir(home: &Path) -> PathBuf {
    home.join(".codex")
}

pub fn dsh_prompt_path(profile_data_root: &Path) -> Option<PathBuf> {
    Some(profile_data_root.join(DSH_GLOBAL_PROMPT_FILENAME))
}

pub fn install_root(target: PromptTarget, home: &Path) -> Result<TargetPaths> {
    let directory = match target {
        PromptTarget::Claude => claude_dir(home),
        PromptTarget::Codex => codex_dir(home),
        PromptTarget::Dsh => return Err(PromptsError::InvalidInput("DSH 目标须用 dsh_prompt_path".into())),
    };
    let installed = directory.is_dir();
    Ok(TargetPaths { installed, prompt_file: directory.join(target_filename(target)) })
}

fn target_filename(target: PromptTarget) -> &'static str {
    match target {
        PromptTarget::Claude => "CLAUDE.md",
        PromptTarget::Codex => "AGENTS.md",
        PromptTarget::Dsh => DSH_GLOBAL_PROMPT_FILENAME,
    }
}

/// live 文件内容;None = 文件不存在。读取失败向上报错(由调用方决定是否跳过回填)。
pub fn read_live_prompt(path: &Path) -> Result<Option<String>> {
    match std::fs::read(path) {
        Ok(bytes) => Ok(Some(String::from_utf8(bytes).map_err(|error| PromptsError::Io(error.to_string()))?)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(PromptsError::Io(error.to_string())),
    }
}

/// temp 写 + 同卷 rename 原子替换(std::fs::rename 在 Windows 上替换已存在目标)。
/// 注意:sync_all → FlushFileBuffers 要求句柄具备写访问权,故必须以 write 模式
/// 重新打开临时文件;用只读的 `File::open` 在 Windows 上会报 os error 5(拒绝访问)。
pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().ok_or_else(|| PromptsError::Io("目标路径无父目录".into()))?;
    let temporary = parent.join(format!(
        ".{}.tmp-{}",
        path.file_name().and_then(|name| name.to_str()).unwrap_or("prompt"),
        uuid::Uuid::new_v4()
    ));
    let write_result = (|| {
        std::fs::write(&temporary, bytes)?;
        let file = std::fs::OpenOptions::new().write(true).open(&temporary)?;
        file.sync_all()
    })();
    if let Err(error) = write_result {
        let _ = std::fs::remove_file(&temporary);
        return Err(PromptsError::Io(error.to_string()));
    }
    std::fs::rename(&temporary, path).map_err(|error| {
        let _ = std::fs::remove_file(&temporary);
        PromptsError::Io(error.to_string())
    })
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(bytes);
    hex::encode(digest.finalize())
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use crate::prompts::model::PromptTarget;
    use super::{atomic_write, detect_home, dsh_prompt_path, install_root, read_live_prompt, sha256_hex};

    fn home() -> PathBuf {
        let dir = tempfile::tempdir().unwrap();
        dir.keep()
    }

    #[test]
    fn install_root_requires_existing_directory() {
        let root = home();
        assert!(!install_root(PromptTarget::Claude, &root).unwrap().installed);
        std::fs::create_dir_all(root.join(".claude")).unwrap();
        assert!(install_root(PromptTarget::Claude, &root).unwrap().installed);
    }

    #[test]
    fn live_paths_follow_convention() {
        let root = home();
        std::fs::create_dir_all(root.join(".codex")).unwrap();
        let paths = install_root(PromptTarget::Codex, &root).unwrap();
        assert_eq!(paths.prompt_file, root.join(".codex/AGENTS.md"));
        assert_eq!(dsh_prompt_path(&root.join("profiles/p1")), Some(root.join("profiles/p1").join(super::DSH_GLOBAL_PROMPT_FILENAME)));
    }

    #[test]
    fn read_live_reports_missing_and_existing() {
        let root = home();
        std::fs::create_dir_all(root.join(".claude")).unwrap();
        let paths = install_root(PromptTarget::Claude, &root).unwrap();
        assert_eq!(read_live_prompt(&paths.prompt_file).unwrap(), None);
        std::fs::write(&paths.prompt_file, b"hello").unwrap();
        assert_eq!(read_live_prompt(&paths.prompt_file).unwrap(), Some("hello".to_owned()));
    }

    #[test]
    fn atomic_write_replaces_content_and_is_visible_afterwards() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("CLAUDE.md");
        std::fs::write(&target, b"old").unwrap();
        atomic_write(&target, b"new-content").unwrap();
        assert_eq!(std::fs::read(&target).unwrap(), b"new-content");
        let leftovers: Vec<_> = std::fs::read_dir(dir.path()).unwrap().collect::<Result<Vec<_>, _>>().unwrap();
        assert_eq!(leftovers.len(), 1, "不能留下临时文件");
    }

    #[test]
    fn sha256_hex_matches_known_vector() {
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn detect_home_falls_back_between_envs() {
        let _ = detect_home();
    }
}
