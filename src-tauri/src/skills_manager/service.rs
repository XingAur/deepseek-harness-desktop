use std::path::{Component, Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::prompts::targets::detect_home;
use crate::skills_manager::model::{InstalledSkill, SkillTarget, SkillsError, Result};

/// ZIP 解包的防御上限(思路同 runtime/archive.rs,量级按 skill 场景放宽)。
const MAX_ZIP_FILES: usize = 4096;
const MAX_ZIP_UNCOMPRESSED: u64 = 256 * 1024 * 1024;
/// 目录拷贝深度上限:防超深嵌套 ZIP 压爆递归。
const MAX_COPY_DEPTH: usize = 64;
const SKILL_MANIFEST: &str = "SKILL.md";

/// Skills 安装器服务(Claude `~/.claude/skills/` 与 Codex `~/.codex/skills/`):
/// - 目标 skills 目录不存在 = 该目标未安装,一律报 TargetNotInstalled,绝不误建目录;
/// - 安装 = 把 ZIP 中 SKILL.md 所在目录整体拷入目标(覆盖前备份到 `.trash-<timestamp>/`)。
pub struct SkillsManagerService {
    home: Option<PathBuf>,
    #[cfg(test)]
    fail_before_commit: std::sync::Mutex<Option<usize>>,
}

impl SkillsManagerService {
    /// 生产构造:从环境解析用户 home(USERPROFILE/HOME)。
    pub fn open() -> Self {
        Self::with_home(&detect_home().unwrap_or_default())
    }

    /// 测试/注入构造:home 为空串视为未解析(所有目标都未安装)。
    pub fn with_home(home: &Path) -> Self {
        Self {
            home: if home.as_os_str().is_empty() { None } else { Some(home.to_path_buf()) },
            #[cfg(test)]
            fail_before_commit: std::sync::Mutex::new(None),
        }
    }

    #[cfg(test)]
    fn fail_before_commit(&self, commit_index: usize) {
        *self.fail_before_commit.lock().unwrap() = Some(commit_index);
    }

    fn check_commit_failpoint(&self, commit_index: usize) -> Result<()> {
        #[cfg(test)]
        if self.fail_before_commit.lock().unwrap().is_some_and(|expected| expected == commit_index) {
            return Err(SkillsError::Io("injected commit failure".into()));
        }
        let _ = commit_index;
        Ok(())
    }

    fn skills_root(&self, target: SkillTarget) -> Option<PathBuf> {
        let home = self.home.as_ref()?;
        Some(match target {
            SkillTarget::Claude => home.join(".claude").join("skills"),
            SkillTarget::Codex => home.join(".codex").join("skills"),
        })
    }

    fn require_skills_root(&self, target: SkillTarget) -> Result<PathBuf> {
        match self.skills_root(target) {
            Some(root) if root.is_dir() => Ok(root),
            _ => Err(SkillsError::TargetNotInstalled(target)),
        }
    }

    /// 列出目标 skills 目录下所有含 SKILL.md 的子目录;目标未安装时报 TargetNotInstalled。
    pub fn list_target(&self, target: SkillTarget) -> Result<Vec<InstalledSkill>> {
        let root = self.require_skills_root(target)?;
        let mut skills = Vec::new();
        for entry in std::fs::read_dir(&root)? {
            let entry = entry?;
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let manifest = path.join(SKILL_MANIFEST);
            if !manifest.is_file() {
                continue;
            }
            skills.push(InstalledSkill {
                name: entry.file_name().to_string_lossy().to_string(),
                target,
                path: path.to_string_lossy().to_string(),
                skill_md_sha256: sha256_file(&manifest)?,
            });
        }
        skills.sort_by(|left, right| left.name.cmp(&right.name));
        Ok(skills)
    }

    /// 从 ZIP 安装 skill 到每个目标:
    /// - ZIP 内定位所有 SKILL.md 所在目录(根布局或子目录布局,多个则分别安装);
    /// - 先校验全部目标已安装并在目标目录内完成 staging;
    /// - 每个目标均提交成功，或对已提交目标执行补偿回滚。
    pub fn install_from_zip(&self, zip_path: &Path, targets: &[SkillTarget]) -> Result<Vec<InstalledSkill>> {
        let mut unique = targets.to_vec();
        unique.sort();
        unique.dedup();
        if unique.is_empty() {
            return Err(SkillsError::InvalidInput("至少选择一个安装目标".into()));
        }
        let roots = unique
            .iter()
            .map(|target| Ok((*target, self.require_skills_root(*target)?)))
            .collect::<Result<Vec<(SkillTarget, PathBuf)>>>()?;

        let staging = tempfile::tempdir().map_err(|error| SkillsError::Io(error.to_string()))?;
        extract_zip(zip_path, staging.path())?;
        let skill_dirs = find_skill_dirs(staging.path());
        if skill_dirs.is_empty() {
            return Err(SkillsError::InvalidInput(format!("ZIP 中未找到 {SKILL_MANIFEST}")));
        }

        let mut sources = Vec::new();
        let mut names = std::collections::BTreeSet::new();
        for source in &skill_dirs {
            let name = resolve_skill_name(source, staging.path())?;
            if !names.insert(name.clone()) {
                return Err(SkillsError::InvalidInput(format!("ZIP 中包含重复 skill 名称: {name}")));
            }
            sources.push((name, source));
        }

        let operation_id = uuid::Uuid::new_v4();
        let mut pending = Vec::new();
        let mut operation_roots = Vec::new();
        let prepared = (|| -> Result<()> {
            for (target, root) in &roots {
                let install_root = root.join(format!(".install-{operation_id}"));
                let transaction_root = root.join(format!(".transaction-{operation_id}"));
                std::fs::create_dir_all(&install_root)?;
                operation_roots.push((install_root.clone(), transaction_root.clone()));
                for (name, source) in &sources {
                    let staged = install_root.join(name);
                    copy_dir_into(source, &staged, 0)?;
                    pending.push(PendingInstall {
                        target: *target,
                        name: name.clone(),
                        staged,
                        destination: root.join(name),
                        transaction_root: transaction_root.clone(),
                        backup: None,
                    });
                }
            }
            Ok(())
        })();
        if let Err(error) = prepared {
            cleanup_operation_roots(&operation_roots);
            return Err(error);
        }

        let mut committed = Vec::new();
        for (index, mut item) in pending.into_iter().enumerate() {
            if let Err(error) = self.check_commit_failpoint(index) {
                rollback_pending(&mut item);
                rollback_committed(&mut committed);
                cleanup_operation_roots(&operation_roots);
                return Err(error);
            }
            if item.destination.exists() {
                let backup = item.transaction_root.join("backup").join(item.target.as_str()).join(&item.name);
                if let Some(parent) = backup.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                if let Err(error) = std::fs::rename(&item.destination, &backup) {
                    rollback_committed(&mut committed);
                    cleanup_operation_roots(&operation_roots);
                    return Err(error.into());
                }
                item.backup = Some(backup);
            }
            if let Err(error) = std::fs::rename(&item.staged, &item.destination) {
                rollback_pending(&mut item);
                rollback_committed(&mut committed);
                cleanup_operation_roots(&operation_roots);
                return Err(error.into());
            }
            committed.push(item);
        }

        let installed = committed
            .iter()
            .map(|item| {
                let manifest = item.destination.join(SKILL_MANIFEST);
                Ok(InstalledSkill {
                    name: item.name.clone(),
                    target: item.target,
                    path: item.destination.to_string_lossy().to_string(),
                    skill_md_sha256: sha256_file(&manifest)?,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        cleanup_operation_roots(&operation_roots);
        Ok(installed)
    }

    /// 卸载:直接删除目标下同名目录;目录不存在时幂等成功(备份机制在安装侧)。
    pub fn uninstall(&self, target: SkillTarget, name: &str) -> Result<()> {
        validate_skill_name(name)?;
        let root = self.require_skills_root(target)?;
        let directory = root.join(name);
        if directory.is_dir() {
            std::fs::remove_dir_all(&directory)?;
        }
        Ok(())
    }

    /// 跨目标同步:把 src 已装的 skill 拷到 dst(存在同名则先备份再覆盖)。
    pub fn sync(&self, src_target: SkillTarget, dst_target: SkillTarget, name: &str) -> Result<InstalledSkill> {
        if src_target == dst_target {
            return Err(SkillsError::InvalidInput("源目标与目标相同,无需同步".into()));
        }
        validate_skill_name(name)?;
        let src_root = self.require_skills_root(src_target)?;
        let dst_root = self.require_skills_root(dst_target)?;
        let source = src_root.join(name);
        if !source.join(SKILL_MANIFEST).is_file() {
            return Err(SkillsError::InvalidInput(format!(
                "源目标 {src_target} 未安装 skill: {name}"
            )));
        }
        backup_existing(&dst_root, name, chrono::Utc::now().timestamp_millis())?;
        let destination = dst_root.join(name);
        copy_dir_into(&source, &destination, 0)?;
        let manifest = destination.join(SKILL_MANIFEST);
        Ok(InstalledSkill {
            name: name.to_owned(),
            target: dst_target,
            path: destination.to_string_lossy().to_string(),
            skill_md_sha256: sha256_file(&manifest)?,
        })
    }
}

struct PendingInstall {
    target: SkillTarget,
    name: String,
    staged: PathBuf,
    destination: PathBuf,
    transaction_root: PathBuf,
    backup: Option<PathBuf>,
}

fn rollback_pending(item: &mut PendingInstall) {
    if item.destination.exists() {
        let _ = std::fs::remove_dir_all(&item.destination);
    }
    if let Some(backup) = &item.backup {
        if backup.exists() {
            let _ = std::fs::rename(backup, &item.destination);
        }
    }
}

fn rollback_committed(committed: &mut [PendingInstall]) {
    for item in committed.iter_mut().rev() {
        rollback_pending(item);
    }
}

fn cleanup_operation_roots(operation_roots: &[(PathBuf, PathBuf)]) {
    for (install_root, transaction_root) in operation_roots {
        let _ = std::fs::remove_dir_all(install_root);
        let _ = std::fs::remove_dir_all(transaction_root);
    }
}

/// 安装名:子目录布局取 SKILL.md 所在目录名;根布局(SKILL.md 在解包根)目录名是
/// 随机临时名,只能取 SKILL.md frontmatter 的 `name:` 字段。
fn resolve_skill_name(source: &Path, staging_root: &Path) -> Result<String> {
    if source == staging_root {
        let bytes = std::fs::read(source.join(SKILL_MANIFEST))?;
        let name = frontmatter_name(&bytes).ok_or_else(|| {
            SkillsError::InvalidInput("根布局 ZIP 的 SKILL.md 缺少 frontmatter name,无法确定 skill 名".into())
        })?;
        validate_skill_name(&name)?;
        return Ok(name);
    }
    let raw = source
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .ok_or_else(|| SkillsError::InvalidInput("skill 目录名无效".into()))?;
    validate_skill_name(&raw)?;
    Ok(raw)
}

/// skill 目录名:单个安全的路径组件(禁分隔符/点号/控制字符),长度有界。
fn validate_skill_name(name: &str) -> Result<()> {
    let invalid =
        |reason: &str| SkillsError::InvalidInput(format!("skill 名称无效({reason}): {name:?}"));
    if name.is_empty() || name.len() > 128 {
        return Err(invalid("须为 1-128 字节"));
    }
    if name == "." || name == ".." {
        return Err(invalid("不得为点号"));
    }
    if name.contains('/') || name.contains('\\') || name.contains('\0') || name.chars().any(char::is_control) {
        return Err(invalid("不得含路径分隔符"));
    }
    Ok(())
}

/// 从 SKILL.md 的 YAML frontmatter 取 `name:` 字段(仅在根布局兜底命名时使用)。
fn frontmatter_name(bytes: &[u8]) -> Option<String> {
    let text = std::str::from_utf8(bytes).ok()?;
    let mut lines = text.lines();
    if lines.next()?.trim() != "---" {
        return None;
    }
    for line in lines {
        let trimmed = line.trim();
        if trimmed == "---" {
            break;
        }
        let Some((key, value)) = trimmed.split_once(':') else { continue };
        if key.trim() != "name" {
            continue;
        }
        let value = value.trim().trim_matches('"').trim_matches('\'').trim();
        if !value.is_empty() {
            return Some(value.to_owned());
        }
    }
    None
}

/// 已有同名安装 → 整体改名进 `.trash-<timestamp>/`(同毫秒撞名时追加 uuid 兜底)。
fn backup_existing(root: &Path, name: &str, timestamp: i64) -> Result<()> {
    let destination = root.join(name);
    if !destination.exists() {
        return Ok(());
    }
    let trash = root.join(format!(".trash-{timestamp}"));
    std::fs::create_dir_all(&trash)?;
    let mut backup = trash.join(name);
    if backup.exists() {
        backup = trash.join(format!("{name}-{}", uuid::Uuid::new_v4()));
    }
    std::fs::rename(&destination, &backup)?;
    Ok(())
}

/// 递归拷贝目录;符号链接等非常规条目跳过(安装源是服务自己解出的受检树)。
fn copy_dir_into(source: &Path, destination: &Path, depth: usize) -> Result<()> {
    if depth > MAX_COPY_DEPTH {
        return Err(SkillsError::Io("skill 目录嵌套过深".into()));
    }
    std::fs::create_dir_all(destination)?;
    for entry in std::fs::read_dir(source)? {
        let entry = entry?;
        let from = entry.path();
        let to = destination.join(entry.file_name());
        let file_type = entry.file_type()?;
        if file_type.is_dir() {
            copy_dir_into(&from, &to, depth + 1)?;
        } else if file_type.is_file() {
            std::fs::copy(&from, &to)?;
        }
    }
    Ok(())
}

/// 深度优先找出解包树里所有 SKILL.md 所在目录(多个则各自成 skill)。
fn find_skill_dirs(root: &Path) -> Vec<PathBuf> {
    let mut stack = vec![root.to_path_buf()];
    let mut found = Vec::new();
    while let Some(directory) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&directory) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else if path.is_file() && entry.file_name() == SKILL_MANIFEST {
                if let Some(parent) = path.parent() {
                    found.push(parent.to_path_buf());
                }
            }
        }
    }
    found.sort();
    found.dedup();
    found
}

/// 解 ZIP 到 staging 目录;条目路径逐组件校验,拒绝 `..`/绝对路径/盘符等 Zip Slip 形状。
fn extract_zip(source: &Path, destination: &Path) -> Result<()> {
    let file = std::fs::File::open(source)
        .map_err(|error| SkillsError::Zip(format!("打开 ZIP 失败: {error}")))?;
    let mut archive = zip::ZipArchive::new(file)
        .map_err(|error| SkillsError::Zip(format!("读取 ZIP 失败: {error}")))?;
    if archive.len() > MAX_ZIP_FILES {
        return Err(SkillsError::Zip("ZIP 条目过多".into()));
    }
    let mut total: u64 = 0;
    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|error| SkillsError::Zip(format!("读取 ZIP 条目失败: {error}")))?;
        if entry.unix_mode().is_some_and(|mode| mode & 0o170000 == 0o120000) {
            return Err(SkillsError::Zip("ZIP 不允许符号链接".into()));
        }
        total = total
            .checked_add(entry.size())
            .ok_or_else(|| SkillsError::Zip("ZIP 解包大小溢出".into()))?;
        if total > MAX_ZIP_UNCOMPRESSED {
            return Err(SkillsError::Zip("ZIP 解包大小超过限制".into()));
        }
        let relative = sanitize_entry_path(entry.name())?;
        let output = destination.join(relative);
        if entry.is_dir() {
            std::fs::create_dir_all(&output)?;
            continue;
        }
        if let Some(parent) = output.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut target = std::fs::File::create(&output)?;
        std::io::copy(&mut entry, &mut target)?;
    }
    Ok(())
}

fn sanitize_entry_path(name: &str) -> Result<PathBuf> {
    let mut sanitized = PathBuf::new();
    for component in Path::new(name).components() {
        match component {
            Component::Normal(part) => sanitized.push(part),
            // `.` 只是冗余;`..`/根/盘符前缀一律视为 Zip Slip。
            Component::CurDir => {}
            _ => return Err(SkillsError::Zip(format!("ZIP 条目路径不安全: {name}"))),
        }
    }
    if sanitized.as_os_str().is_empty() {
        return Err(SkillsError::Zip(format!("ZIP 条目路径不安全: {name}")));
    }
    Ok(sanitized)
}

fn sha256_file(path: &Path) -> Result<String> {
    let bytes = std::fs::read(path)?;
    let mut digest = Sha256::new();
    digest.update(bytes);
    Ok(hex::encode(digest.finalize()))
}

#[cfg(test)]
mod tests {
    use std::io::Write as _;

    use zip::write::SimpleFileOptions;

    use super::*;
    use crate::skills_manager::model::SkillsError;


    /// 测试环境:隔离 home,可选用 .claude/.codex 模拟两个目标已安装。
    struct Env {
        _dir: tempfile::TempDir,
        home: PathBuf,
    }

    fn env_with_targets(claude: bool, codex: bool) -> Env {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("home");
        if claude {
            std::fs::create_dir_all(home.join(".claude/skills")).unwrap();
        }
        if codex {
            std::fs::create_dir_all(home.join(".codex/skills")).unwrap();
        }
        Env { _dir: dir, home }
    }

    fn env() -> Env {
        env_with_targets(true, true)
    }

    fn service(env: &Env) -> SkillsManagerService {
        SkillsManagerService::with_home(&env.home)
    }

    fn write_zip(path: &Path, entries: &[(&str, &[u8])]) {
        let file = std::fs::File::create(path).unwrap();
        let mut writer = zip::ZipWriter::new(file);
        let options =
            SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
        for (name, contents) in entries {
            writer.start_file(*name, options).unwrap();
            writer.write_all(contents).unwrap();
        }
        writer.finish().unwrap();
    }

    fn make_zip(env: &Env, entries: &[(&str, &[u8])]) -> PathBuf {
        let path = env._dir.path().join(format!("{}.zip", uuid::Uuid::new_v4()));
        write_zip(&path, entries);
        path
    }

    fn write_skill(root: &Path, name: &str, manifest: &str) {
        let directory = root.join(name);
        std::fs::create_dir_all(&directory).unwrap();
        std::fs::write(directory.join(SKILL_MANIFEST), manifest).unwrap();
    }

    #[test]
    fn list_reports_skills_with_sha_and_skips_plain_dirs_and_files() {
        let env = env();
        let root = env.home.join(".claude/skills");
        write_skill(&root, "demo", "# Demo");
        std::fs::create_dir_all(root.join("no-manifest")).unwrap();
        std::fs::write(root.join("stray.txt"), b"not a skill").unwrap();

        let skills = service(&env).list_target(SkillTarget::Claude).unwrap();
        assert_eq!(skills.len(), 1);
        assert_eq!(skills[0].name, "demo");
        assert_eq!(skills[0].target, SkillTarget::Claude);
        assert_eq!(Path::new(&skills[0].path), root.join("demo"));
        assert_eq!(
            skills[0].skill_md_sha256,
            crate::prompts::targets::sha256_hex(b"# Demo"),
            "sha256 用于跨目标比对,须为 SKILL.md 内容摘要"
        );

        // 列表按名称排序,便于稳定回显
        write_skill(&root, "alpha", "# Alpha");
        let listed = service(&env).list_target(SkillTarget::Claude).unwrap();
        let names: Vec<&str> = listed.iter().map(|skill| skill.name.as_str()).collect();
        assert_eq!(names, vec!["alpha", "demo"]);
    }

    #[test]
    fn install_zip_root_layout_names_from_frontmatter_and_installs_into_every_target() {
        let env = env();
        let manifest = b"---\nname: root-skill\n---\n# Root skill\n".as_slice();
        let zip = make_zip(&env, &[
            (SKILL_MANIFEST, manifest),
            ("refs/note.md", b"attached".as_slice()),
        ]);
        let installed =
            service(&env).install_from_zip(&zip, &[SkillTarget::Claude, SkillTarget::Codex]).unwrap();
        assert_eq!(installed.len(), 2, "勾选的每个目标各装一份");
        for skill in &installed {
            assert_eq!(skill.name, "root-skill", "根布局以 frontmatter name 命名");
        }
        assert_eq!(
            std::fs::read_to_string(env.home.join(".claude/skills/root-skill/refs/note.md")).unwrap(),
            "attached",
            "附属文件随目录一起落盘"
        );
        assert_eq!(
            std::fs::read_to_string(env.home.join(".codex/skills/root-skill/SKILL.md")).unwrap(),
            "---\nname: root-skill\n---\n# Root skill\n"
        );
    }

    #[test]
    fn install_zip_root_layout_without_frontmatter_name_is_rejected() {
        let env = env();
        let zip = make_zip(&env, &[(SKILL_MANIFEST, b"# no name".as_slice())]);
        let error = service(&env).install_from_zip(&zip, &[SkillTarget::Claude]).unwrap_err();
        assert!(matches!(error, SkillsError::InvalidInput(_)), "根布局缺 frontmatter name: {error}");
        assert!(env.home.join(".claude/skills").read_dir().unwrap().next().is_none(), "拒绝时不落任何目录");
    }

    #[test]
    fn install_zip_single_dir_layout_keeps_directory_name() {
        let env = env();
        let zip = make_zip(&env, &[
            ("pdf-tools/SKILL.md", b"# PDF tools".as_slice()),
            ("pdf-tools/scripts/run.py", b"print('hi')".as_slice()),
        ]);
        let installed = service(&env).install_from_zip(&zip, &[SkillTarget::Claude]).unwrap();
        assert_eq!(installed.len(), 1);
        assert_eq!(installed[0].name, "pdf-tools", "子目录布局以目录名为 skill 名");
        assert_eq!(
            installed[0].skill_md_sha256,
            crate::prompts::targets::sha256_hex(b"# PDF tools")
        );
        assert_eq!(
            std::fs::read_to_string(env.home.join(".claude/skills/pdf-tools/scripts/run.py")).unwrap(),
            "print('hi')"
        );
        assert!(!env.home.join(".codex/skills/pdf-tools").exists(), "未勾选的目标不受影响");
    }

    #[test]
    fn install_zip_with_multiple_skill_md_installs_each_directory() {
        let env = env();
        let zip = make_zip(&env, &[
            ("alpha/SKILL.md", b"# Alpha".as_slice()),
            ("beta/SKILL.md", b"# Beta".as_slice()),
            ("nested/gamma/SKILL.md", b"# Gamma".as_slice()),
        ]);
        let installed = service(&env).install_from_zip(&zip, &[SkillTarget::Claude]).unwrap();
        let mut names: Vec<&str> = installed.iter().map(|skill| skill.name.as_str()).collect();
        names.sort_unstable();
        assert_eq!(names, vec!["alpha", "beta", "gamma"], "每个 SKILL.md 各自成 skill");
        assert!(
            env.home.join(".claude/skills/gamma/SKILL.md").is_file(),
            "嵌套 skill 以其所在目录为单位整体拷入"
        );
    }

    #[test]
    fn install_zip_rejects_zip_slip_entry_paths() {
        let env = env();
        let outside = env._dir.path().join("escaped.md");
        let slip = make_zip(&env, &[
            (SKILL_MANIFEST, b"# ok".as_slice()),
            ("../evil.md", b"escape".as_slice()),
        ]);
        let error = service(&env).install_from_zip(&slip, &[SkillTarget::Claude]).unwrap_err();
        assert!(matches!(error, SkillsError::Zip(_)), "父目录引用须被拒绝: {error}");
        assert!(!outside.exists(), "不得逃逸出临时目录");

        let drive = make_zip(&env, &[
            (SKILL_MANIFEST, b"# ok".as_slice()),
            ("C:/evil.md", b"escape".as_slice()),
        ]);
        let error = service(&env).install_from_zip(&drive, &[SkillTarget::Claude]).unwrap_err();
        assert!(matches!(error, SkillsError::Zip(_)), "盘符前缀须被拒绝: {error}");

        let absolute = make_zip(&env, &[
            (SKILL_MANIFEST, b"# ok".as_slice()),
            ("/abs.md", b"escape".as_slice()),
        ]);
        let error = service(&env).install_from_zip(&absolute, &[SkillTarget::Claude]).unwrap_err();
        assert!(matches!(error, SkillsError::Zip(_)), "绝对路径须被拒绝: {error}");
    }

    #[test]
    fn install_over_existing_replaces_atomically_without_transaction_leftovers() {
        let env = env();
        let root = env.home.join(".claude/skills");
        write_skill(&root, "demo", "# old version");
        let zip = make_zip(&env, &[("demo/SKILL.md", b"# new version".as_slice())]);

        let installed = service(&env).install_from_zip(&zip, &[SkillTarget::Claude]).unwrap();
        assert_eq!(installed.len(), 1);
        assert_eq!(
            std::fs::read_to_string(root.join("demo/SKILL.md")).unwrap(),
            "# new version",
            "覆盖安装生效"
        );

        assert!(std::fs::read_dir(&root).unwrap().all(|entry| {
            let name = entry.unwrap().file_name().to_string_lossy().to_string();
            !name.starts_with(".install-") && !name.starts_with(".transaction-")
        }));
    }

    #[test]
    fn install_rolls_back_all_targets_when_a_later_commit_fails() {
        let env = env();
        let claude = env.home.join(".claude/skills");
        let codex = env.home.join(".codex/skills");
        write_skill(&claude, "demo", "# old claude");
        write_skill(&codex, "demo", "# old codex");
        let zip = make_zip(&env, &[("demo/SKILL.md", b"# new version".as_slice())]);
        let manager = service(&env);
        manager.fail_before_commit(1);
        let error = manager.install_from_zip(&zip, &[SkillTarget::Claude, SkillTarget::Codex]).unwrap_err();
        assert!(matches!(error, SkillsError::Io(message) if message == "injected commit failure"));
        assert_eq!(std::fs::read_to_string(claude.join("demo/SKILL.md")).unwrap(), "# old claude");
        assert_eq!(std::fs::read_to_string(codex.join("demo/SKILL.md")).unwrap(), "# old codex");
        for root in [claude, codex] {
            assert!(std::fs::read_dir(root).unwrap().all(|entry| {
                let name = entry.unwrap().file_name().to_string_lossy().to_string();
                !name.starts_with(".install-") && !name.starts_with(".transaction-")
            }));
        }
    }

    #[test]
    fn uninstall_removes_directory_and_is_idempotent() {
        let env = env();
        let root = env.home.join(".claude/skills");
        write_skill(&root, "demo", "# Demo");
        let manager = service(&env);
        manager.uninstall(SkillTarget::Claude, "demo").unwrap();
        assert!(!root.join("demo").exists());
        manager.uninstall(SkillTarget::Claude, "demo").unwrap();
        manager.uninstall(SkillTarget::Codex, "demo").unwrap();
    }

    #[test]
    fn sync_copies_between_targets_and_backs_up_destination() {
        let env = env();
        let claude = env.home.join(".claude/skills");
        let codex = env.home.join(".codex/skills");
        write_skill(&claude, "demo", "# from claude");
        write_skill(&codex, "demo", "# stale codex copy");

        let synced = service(&env).sync(SkillTarget::Claude, SkillTarget::Codex, "demo").unwrap();
        assert_eq!(synced.name, "demo");
        assert_eq!(synced.target, SkillTarget::Codex);
        assert_eq!(
            std::fs::read_to_string(codex.join("demo/SKILL.md")).unwrap(),
            "# from claude",
            "同步覆盖目标副本"
        );
        let trash = std::fs::read_dir(&codex).unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().to_string())
            .find(|name| name.starts_with(".trash-"))
            .expect("同步覆盖前须留备份");
        assert_eq!(
            std::fs::read_to_string(codex.join(trash).join("demo/SKILL.md")).unwrap(),
            "# stale codex copy"
        );

        // 跨目标 sha 一致,便于面板比对
        let claude_skills = service(&env).list_target(SkillTarget::Claude).unwrap();
        let codex_skills = service(&env).list_target(SkillTarget::Codex).unwrap();
        assert_eq!(claude_skills[0].skill_md_sha256, codex_skills[0].skill_md_sha256);
    }

    #[test]
    fn target_not_installed_errors_without_creating_directories_or_partial_install() {
        let env = env_with_targets(true, false);
        let manager = service(&env);
        assert!(matches!(
            manager.list_target(SkillTarget::Codex),
            Err(SkillsError::TargetNotInstalled(SkillTarget::Codex))
        ));
        assert!(matches!(
            manager.uninstall(SkillTarget::Codex, "demo"),
            Err(SkillsError::TargetNotInstalled(SkillTarget::Codex))
        ));
        assert!(matches!(
            manager.sync(SkillTarget::Claude, SkillTarget::Codex, "demo"),
            Err(SkillsError::TargetNotInstalled(SkillTarget::Codex))
        ));
        let zip = make_zip(&env, &[("demo/SKILL.md", b"# Demo".as_slice())]);
        let error = manager.install_from_zip(&zip, &[SkillTarget::Codex]).unwrap_err();
        assert!(matches!(error, SkillsError::TargetNotInstalled(SkillTarget::Codex)));
        assert!(!env.home.join(".codex").exists(), "未安装目标绝不被误建");

        // fail-fast:多目标请求里只要有一个未安装,整单拒绝,已安装目标不被改动
        write_skill(&env.home.join(".claude/skills"), "demo", "# Demo");
        let error =
            manager.install_from_zip(&zip, &[SkillTarget::Claude, SkillTarget::Codex]).unwrap_err();
        assert!(matches!(error, SkillsError::TargetNotInstalled(SkillTarget::Codex)));
        assert_eq!(
            std::fs::read_to_string(env.home.join(".claude/skills/demo/SKILL.md")).unwrap(),
            "# Demo",
            "fail-fast 不产生部分安装"
        );
    }

    #[test]
    fn install_rejects_zip_without_skill_manifest_and_bad_inputs() {
        let env = env();
        let manager = service(&env);
        let plain = make_zip(&env, &[("readme.txt", b"no skill here".as_slice())]);
        let error = manager.install_from_zip(&plain, &[SkillTarget::Claude]).unwrap_err();
        assert!(matches!(error, SkillsError::InvalidInput(_)));

        let corrupt = env._dir.path().join("corrupt.zip");
        std::fs::write(&corrupt, b"not a zip").unwrap();
        let error = manager.install_from_zip(&corrupt, &[SkillTarget::Claude]).unwrap_err();
        assert!(matches!(error, SkillsError::Zip(_)));

        let zip = make_zip(&env, &[("demo/SKILL.md", b"# Demo".as_slice())]);
        let error = manager.install_from_zip(&zip, &[]).unwrap_err();
        assert!(matches!(error, SkillsError::InvalidInput(_)));
    }

    #[test]
    fn sync_rejects_same_target_and_missing_source_skill() {
        let env = env();
        let manager = service(&env);
        assert!(matches!(
            manager.sync(SkillTarget::Claude, SkillTarget::Claude, "demo"),
            Err(SkillsError::InvalidInput(_))
        ));
        let error = manager.sync(SkillTarget::Claude, SkillTarget::Codex, "missing").unwrap_err();
        assert!(matches!(error, SkillsError::InvalidInput(_)), "源未装该 skill: {error}");
        assert!(!env.home.join(".codex/skills/missing").exists());
    }

    #[test]
    fn skill_names_with_path_separators_are_rejected() {
        let env = env();
        let manager = service(&env);
        for name in ["../escape", "a/b", "a\\b", ".", "..", "", "bad\nname"] {
            assert!(
                manager.uninstall(SkillTarget::Claude, name).is_err(),
                "非法名称须被拒绝: {name:?}"
            );
        }
    }
}
