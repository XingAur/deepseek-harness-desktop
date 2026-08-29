use std::path::{Path, PathBuf};

use crate::prompts::backup::backup_live_file;
use crate::prompts::model::{
    ActivateOutcome, ConflictCandidate, Flow, PresetSummary, PromptPreset, PromptTarget, PromptsError, Result,
    SaveOutcome, TargetStatus, MAX_PROMPT_BYTES,
};
use crate::prompts::store::PromptsStore;
use crate::prompts::targets;

pub struct PromptsService {
    store: PromptsStore,
    backup_root: PathBuf,
    home: Option<PathBuf>,
    profiles_root: PathBuf,
}

impl PromptsService {
    pub fn open(paths: &crate::storage::app_paths::AppPaths) -> Result<Self> {
        Self::open_with_env(
            &targets::detect_home().unwrap_or_default(),
            &paths.profiles,
            &paths.state.join("prompts.db"),
            &paths.backups.join("prompts"),
        )
    }

    pub fn open_with_env(
        home: &Path,
        profiles_root: &Path,
        database_path: &Path,
        backup_root: &Path,
    ) -> Result<Self> {
        Ok(Self {
            store: PromptsStore::open(database_path)?,
            backup_root: backup_root.to_path_buf(),
            home: if home.as_os_str().is_empty() { None } else { Some(home.to_path_buf()) },
            profiles_root: profiles_root.to_path_buf(),
        })
    }

    fn now_ms() -> i64 {
        chrono::Utc::now().timestamp_millis()
    }

    fn validate_title(title: &str) -> Result<()> {
        let trimmed = title.trim();
        if trimmed.is_empty() || trimmed.chars().count() > 200 {
            return Err(PromptsError::InvalidInput("标题须为 1-200 字符".into()));
        }
        Ok(())
    }

    fn validate_content(content: &str) -> Result<()> {
        if content.len() > MAX_PROMPT_BYTES {
            return Err(PromptsError::TooLarge);
        }
        Ok(())
    }

    pub fn list(&self) -> Result<Vec<PresetSummary>> {
        let mut summaries = Vec::new();
        for preset in self.store.list_presets()? {
            summaries.push(PresetSummary {
                id: preset.id.clone(),
                title: preset.title.clone(),
                updated_at: preset.updated_at,
                activated_targets: self.store.activated_targets(&preset.id)?,
            });
        }
        Ok(summaries)
    }

    pub fn get(&self, preset_id: &str) -> Result<PromptPreset> {
        self.store
            .get_preset(preset_id)?
            .ok_or_else(|| PromptsError::InvalidInput(format!("预设不存在: {preset_id}")))
    }

    /// 保存:新建或更新。
    /// 更新已激活预设时:≥2 份不同分歧 live → Flow::Conflict(DB 未写入);
    /// 恰 1 份分歧 → 先落 backup-{ms} 备份预设吸收外部修改,再以用户提交内容更新 DB;
    /// 最后强制重投影(备份+原子写,不比对),保证任何选择都能收敛。
    pub fn save(&self, preset_id: Option<&str>, title: &str, content: &str) -> Result<Flow<SaveOutcome>> {
        Self::validate_title(title)?;
        Self::validate_content(content)?;
        let now = Self::now_ms();
        let stored = match preset_id {
            None => {
                let id = uuid::Uuid::new_v4().to_string();
                self.store.insert_preset(&id, title.trim(), content, now, now)?;
                PromptPreset { id, title: title.trim().to_owned(), content: content.to_owned(), created_at: now, updated_at: now }
            }
            Some(existing_id) => {
                let existing = self.get(existing_id)?;
                let divergent = self.divergent_live_contents(existing_id, &existing.content);
                if divergent.len() >= 2 {
                    // 冲突在检测阶段返回:此刻尚未写库,外部修改原样保留。
                    return Ok(Flow::Conflict { preset_id: existing_id.to_owned(), candidates: divergent });
                }
                if let Some(candidate) = divergent.first() {
                    if candidate.content != content {
                        let backup_id = uuid::Uuid::new_v4().to_string();
                        self.store.insert_preset(&backup_id, &format!("backup-{now}"), &candidate.content, now, now)?;
                    }
                }
                self.store.update_preset(existing_id, title.trim(), content, now)?;
                PromptPreset { id: existing.id, title: title.trim().to_owned(), content: content.to_owned(), created_at: existing.created_at, updated_at: now }
            }
        };
        let projected = self.project_active_targets_forced(&stored)?;
        Ok(Flow::Done(SaveOutcome::Saved { preset: stored, projected }))
    }

    /// 冲突裁决后的收敛入口:用户已选定最终内容(通常来自 Flow::Conflict 的某个候选)。
    /// 其余分歧 live 内容先落 backup-{ms} 备份预设防丢,再以选定内容更新 DB 并强制重投影。
    /// 与 save 不同:不再做分歧闸门(用户裁决即权威),保证一次调用必然收敛。
    pub fn resolve_save_conflict(&self, preset_id: &str, title: &str, content: &str) -> Result<Flow<SaveOutcome>> {
        Self::validate_title(title)?;
        Self::validate_content(content)?;
        let existing = self.get(preset_id)?;
        let now = Self::now_ms();
        for candidate in self.divergent_live_contents(preset_id, &existing.content) {
            if candidate.content == content {
                continue;
            }
            let backup_id = uuid::Uuid::new_v4().to_string();
            self.store.insert_preset(&backup_id, &format!("backup-{now}"), &candidate.content, now, now)?;
        }
        self.store.update_preset(preset_id, title.trim(), content, now)?;
        let stored = PromptPreset { id: existing.id, title: title.trim().to_owned(), content: content.to_owned(), created_at: existing.created_at, updated_at: now };
        let projected = self.project_active_targets_forced(&stored)?;
        Ok(Flow::Done(SaveOutcome::Saved { preset: stored, projected }))
    }

    /// 删除:任一目标仍激活该预设时拒绝(要求先停用)。
    pub fn delete(&self, preset_id: &str) -> Result<()> {
        if !self.store.activated_targets(preset_id)?.is_empty() {
            return Err(PromptsError::PresetActive(preset_id.to_owned()));
        }
        self.store.delete_preset(preset_id)
    }

    /// 激活(目标 X ← 预设 P,spec §5 修订版):单目标就地回填,无冲突对话框。
    /// ① live 非空且 X 已有激活项 Q:外部修改回填覆盖 Q(Q==P 时跳过,随②覆盖);
    ///    live 非空且无激活项:外部内容落 backup-{ms} 备份预设;
    /// ② 备份 live → 原子写 P 内容 → 落激活记录。
    pub fn activate(&self, preset_id: &str, target: PromptTarget) -> Result<ActivateOutcome> {
        let preset = self.get(preset_id)?;
        let Some(path) = self.prompt_file_for(target)? else {
            return Err(PromptsError::TargetNotInstalled(target));
        };
        let live = targets::read_live_prompt(&path)?;
        if let Some(live_text) = live.filter(|text| !text.is_empty()) {
            match self.store.active_preset_id(target)? {
                None => {
                    let now = Self::now_ms();
                    let backup_id = uuid::Uuid::new_v4().to_string();
                    self.store.insert_preset(&backup_id, &format!("backup-{now}"), &live_text, now, now)?;
                }
                Some(previous_id) => {
                    if previous_id != preset.id {
                        if let Some(previous) = self.store.get_preset(&previous_id)? {
                            if previous.content != live_text {
                                self.store.update_preset(&previous.id, &previous.title, &live_text, Self::now_ms())?;
                            }
                        }
                    }
                }
            }
        }
        backup_live_file(&path, &self.backup_root.join(target.as_str()))?;
        targets::atomic_write(&path, preset.content.as_bytes())?;
        self.store.set_activation(target, preset_id, Self::now_ms())?;
        Ok(ActivateOutcome::Ok { status: self.status_of(target)? })
    }

    /// 停用:清空目标 live 文件(写前备份),删除激活记录。对齐 cc-switch「禁用即清空」。
    pub fn deactivate(&self, target: PromptTarget) -> Result<TargetStatus> {
        if let Some(path) = self.prompt_file_for(target)? {
            backup_live_file(&path, &self.backup_root.join(target.as_str()))?;
            targets::atomic_write(&path, b"")?;
        }
        self.store.clear_activation(target)?;
        self.status_of(target)
    }

    /// 收集激活目标 live 内容与 DB 的分歧项,按内容去重(同内容保留最先目标,target 升序)。
    /// 读取失败/未安装的目标跳过。
    fn divergent_live_contents(&self, preset_id: &str, db_content: &str) -> Vec<ConflictCandidate> {
        let mut candidates: Vec<ConflictCandidate> = Vec::new();
        for target in self.store.activated_targets(preset_id).unwrap_or_default() {
            let Ok(Some(live)) = self.live_content(target) else { continue };
            if live == db_content {
                continue;
            }
            if candidates.iter().any(|candidate| candidate.content == live) {
                continue;
            }
            candidates.push(ConflictCandidate { target, content: live, updated_at: Self::now_ms() });
        }
        candidates
    }

    /// 强制重投影:逐激活目标 备份 → 原子写 → 状态收集,不做 live 比对。
    /// save 路径的分歧裁决(备份预设/冲突)已在调用前完成,此处无条件以预设内容覆盖,保证收敛。
    fn project_active_targets_forced(&self, preset: &PromptPreset) -> Result<Vec<TargetStatus>> {
        let mut projected = Vec::new();
        for target in self.store.activated_targets(&preset.id)? {
            self.write_target(target, &preset.content)?;
            projected.push(self.status_of(target)?);
        }
        Ok(projected)
    }

    fn live_content(&self, target: PromptTarget) -> Result<Option<String>> {
        match self.prompt_file_for(target)? {
            Some(path) => targets::read_live_prompt(&path),
            None => Ok(None),
        }
    }

    pub(crate) fn prompt_file_for(&self, target: PromptTarget) -> Result<Option<PathBuf>> {
        match target {
            PromptTarget::Claude | PromptTarget::Codex => {
                let Some(home) = &self.home else { return Ok(None) };
                let paths = targets::install_root(target, home)?;
                if paths.installed {
                    Ok(Some(paths.prompt_file))
                } else {
                    Ok(None)
                }
            }
            PromptTarget::Dsh => Ok(self
                .active_profile_data_root()
                .and_then(|root| targets::dsh_prompt_path(&root))
                .filter(|path| path.parent().is_some_and(|parent| parent.is_dir()))),
        }
    }

    fn active_profile_data_root(&self) -> Option<PathBuf> {
        let repository = crate::profile::repository::ProfileRepository::open_read_only(self.profiles_root.clone()).ok()?;
        let snapshot = repository.snapshot().ok()?;
        let profile_id = snapshot.selected_profile_id.or_else(|| snapshot.pending_profile_id)?;
        repository.get(&profile_id).ok().map(|record| record.data_root)
    }

    fn write_target(&self, target: PromptTarget, content: &str) -> Result<()> {
        let Some(path) = self.prompt_file_for(target)? else {
            return Err(PromptsError::TargetNotInstalled(target));
        };
        backup_live_file(&path, &self.backup_root.join(target.as_str()))?;
        targets::atomic_write(&path, content.as_bytes())
    }

    /// 单目标状态(来自计划 Task 9,提前实现避免 todo;Task 9 只补 status() 聚合与 import)。
    pub(crate) fn status_of(&self, target: PromptTarget) -> Result<TargetStatus> {
        let Some(path) = self.prompt_file_for(target)? else {
            return Ok(TargetStatus {
                target,
                installed: false,
                live_file_exists: false,
                active_preset_id: self.store.active_preset_id(target)?,
                live_content_sha256: None,
                matches_active_preset: false,
                oversized: false,
            });
        };
        let live = targets::read_live_prompt(&path)?;
        let live_hash = live.as_ref().map(|text| targets::sha256_hex(text.as_bytes()));
        let oversized = live.as_ref().is_some_and(|text| text.len() > MAX_PROMPT_BYTES);
        let active_preset_id = self.store.active_preset_id(target)?;
        let matches = match (&active_preset_id, &live) {
            (Some(preset_id), Some(text)) => self
                .store
                .get_preset(preset_id)?
                .is_some_and(|preset| preset.content == *text),
            _ => false,
        };
        Ok(TargetStatus {
            target,
            installed: true,
            live_file_exists: live.is_some(),
            active_preset_id,
            live_content_sha256: live_hash,
            matches_active_preset: matches,
            oversized,
        })
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use crate::prompts::model::{Flow, PromptTarget, PromptsError, SaveOutcome, MAX_PROMPT_BYTES};
    use crate::prompts::service::PromptsService;

    /// 测试环境:隔离 home(仅建 .claude/.codex 目录)+ 独立 db + 独立 profile 根。
    pub(crate) struct Env {
        pub(crate) _dir: tempfile::TempDir,
        pub(crate) home: PathBuf,
        pub(crate) profiles: PathBuf,
        pub(crate) db: PathBuf,
        pub(crate) backups: PathBuf,
    }

    pub(crate) fn env() -> Env {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("home");
        std::fs::create_dir_all(home.join(".claude")).unwrap();
        std::fs::create_dir_all(home.join(".codex")).unwrap();
        let profiles = dir.path().join("profiles");
        std::fs::create_dir_all(&profiles).unwrap();
        std::fs::create_dir_all(profiles.join("p-default")).unwrap();
        Env {
            home,
            profiles,
            db: dir.path().join("state/prompts.db"),
            backups: dir.path().join("backups"),
            _dir: dir,
        }
    }

    pub(crate) fn service(env: &Env) -> PromptsService {
        PromptsService::open_with_env(&env.home, &env.profiles, &env.db, &env.backups).unwrap()
    }

    #[test]
    fn save_create_update_and_delete_roundtrip() {
        let env = env();
        let service = service(&env);
        let preset = match service.save(None, "第一版", "内容 A").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            Flow::Done(SaveOutcome::BackfillConflict { .. }) => panic!("新建预设不应有回填冲突"),
            Flow::Conflict { .. } => panic!("新建预设不应有冲突"),
        };
        assert!(!preset.id.is_empty());
        assert_eq!(service.list().unwrap().len(), 1);
        let preset = match service.save(Some(&preset.id), "第二版", "内容 B").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!("期望 saved"),
        };
        assert_eq!(preset.title, "第二版");
        service.delete(&preset.id).unwrap();
        assert!(service.list().unwrap().is_empty());
    }

    #[test]
    fn oversized_content_is_rejected() {
        let env = env();
        let service = service(&env);
        let content = "x".repeat(MAX_PROMPT_BYTES + 1);
        let error = service.save(None, "too big", &content).unwrap_err();
        assert!(matches!(error, PromptsError::TooLarge));
    }

    #[test]
    fn empty_title_is_rejected() {
        let env = env();
        let service = service(&env);
        assert!(service.save(None, "   ", "c").is_err());
    }

    fn seed_activated(service: &PromptsService, preset_id: &str, target: PromptTarget) {
        service.store.set_activation(target, preset_id, 1).unwrap();
    }

    #[test]
    fn save_with_single_divergent_live_backs_up_external_and_keeps_user_content() {
        let env = env();
        let service = service(&env);
        let saved = match service.save(None, "P", "v1").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        seed_activated(&service, &saved.id, PromptTarget::Claude);
        let path = service.prompt_file_for(PromptTarget::Claude).unwrap().unwrap();
        std::fs::write(&path, "外部修改").unwrap();

        let outcome = service.save(Some(&saved.id), "P", "v2").unwrap();
        match outcome {
            Flow::Done(SaveOutcome::Saved { preset, projected }) => {
                assert_eq!(preset.content, "v2", "用户提交内容为准");
                assert_eq!(projected.len(), 1);
            }
            _ => panic!("单份分歧不应冲突"),
        }
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "v2");
        assert!(service.list().unwrap().iter().any(|summary| summary.title.starts_with("backup-")), "外部修改须落为备份预设");
    }

    #[test]
    fn save_with_identical_divergent_lives_does_not_conflict() {
        let env = env();
        let service = service(&env);
        let saved = match service.save(None, "P", "v1").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        seed_activated(&service, &saved.id, PromptTarget::Claude);
        seed_activated(&service, &saved.id, PromptTarget::Codex);
        for target in [PromptTarget::Claude, PromptTarget::Codex] {
            let path = service.prompt_file_for(target).unwrap().unwrap();
            std::fs::write(&path, "相同的外部修改").unwrap();
        }
        assert!(matches!(service.save(Some(&saved.id), "P", "v2").unwrap(), Flow::Done(_)), "内容相同的分歧按去重不算冲突");
    }

    #[test]
    fn save_with_two_distinct_divergent_lives_returns_deduped_conflict() {
        let env = env();
        let service = service(&env);
        let saved = match service.save(None, "P", "v1").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        seed_activated(&service, &saved.id, PromptTarget::Claude);
        seed_activated(&service, &saved.id, PromptTarget::Codex);
        let claude_path = service.prompt_file_for(PromptTarget::Claude).unwrap().unwrap();
        let codex_path = service.prompt_file_for(PromptTarget::Codex).unwrap().unwrap();
        std::fs::write(&claude_path, "claude 端修改").unwrap();
        std::fs::write(&codex_path, "codex 端修改").unwrap();
        match service.save(Some(&saved.id), "P", "v2").unwrap() {
            Flow::Conflict { preset_id, candidates } => {
                assert_eq!(preset_id, saved.id);
                assert_eq!(candidates.len(), 2, "两份不同内容各留一个候选");
            }
            _ => panic!("两份不同分歧必须冲突"),
        }
    }

    #[test]
    fn resolve_save_conflict_converges_and_preserves_other_candidates() {
        let env = env();
        let service = service(&env);
        let saved = match service.save(None, "P", "v1").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        seed_activated(&service, &saved.id, PromptTarget::Claude);
        seed_activated(&service, &saved.id, PromptTarget::Codex);
        let claude_path = service.prompt_file_for(PromptTarget::Claude).unwrap().unwrap();
        let codex_path = service.prompt_file_for(PromptTarget::Codex).unwrap().unwrap();
        std::fs::write(&claude_path, "claude 端修改").unwrap();
        std::fs::write(&codex_path, "codex 端修改").unwrap();

        // 冲突:DB 未写入
        match service.save(Some(&saved.id), "P", "v2").unwrap() {
            Flow::Conflict { candidates, .. } => assert_eq!(candidates.len(), 2),
            _ => panic!(),
        }
        assert_eq!(service.get(&saved.id).unwrap().content, "v1", "冲突返回时 DB 不得写入");

        // 裁决以 claude 端为准 → 收敛,另一端内容落备份
        let resolved = match service.resolve_save_conflict(&saved.id, "P", "claude 端修改").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, projected }) => (preset, projected),
            _ => panic!("resolve 必须收敛"),
        };
        assert_eq!(resolved.0.content, "claude 端修改");
        assert_eq!(resolved.1.len(), 2);
        assert_eq!(std::fs::read_to_string(&claude_path).unwrap(), "claude 端修改");
        assert_eq!(std::fs::read_to_string(&codex_path).unwrap(), "claude 端修改");
        let summaries = service.list().unwrap();
        let backups: Vec<&crate::prompts::model::PresetSummary> = summaries.iter()
            .filter(|summary| summary.title.starts_with("backup-"))
            .collect();
        assert_eq!(backups.len(), 1);
        let backup_preset = service.get(&backups[0].id).unwrap();
        assert_eq!(backup_preset.content, "codex 端修改", "未选中一端的分歧内容必须落备份");

        // 收敛后再 save 不再冲突
        assert!(matches!(service.save(Some(&saved.id), "P", "v3").unwrap(), Flow::Done(_)));
    }

    #[test]
    fn backup_preset_from_single_divergence_carries_external_content() {
        let env = env();
        let service = service(&env);
        let saved = match service.save(None, "P", "v1").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        seed_activated(&service, &saved.id, PromptTarget::Claude);
        let path = service.prompt_file_for(PromptTarget::Claude).unwrap().unwrap();
        std::fs::write(&path, "外部修改").unwrap();
        let _ = service.save(Some(&saved.id), "P", "v2").unwrap();
        let backup = service.list().unwrap().into_iter()
            .find(|summary| summary.title.starts_with("backup-"))
            .expect("应有备份预设");
        assert_eq!(service.get(&backup.id).unwrap().content, "外部修改");
    }

    pub(crate) fn write_live(env: &Env, target: PromptTarget, content: &str) {
        let service = service(env);
        let path = service.prompt_file_for(target).unwrap().unwrap();
        std::fs::write(path, content).unwrap();
    }

    pub(crate) fn read_live(env: &Env, target: PromptTarget) -> Option<String> {
        let service = service(env);
        let path = service.prompt_file_for(target).unwrap().unwrap();
        std::fs::read_to_string(path).ok()
    }

    #[test]
    fn activate_writes_file_records_activation_and_backs_up_live() {
        let env = env();
        let service = service(&env);
        write_live(&env, PromptTarget::Claude, "外部原有内容");
        let saved = match service.save(None, "P", "新内容").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        match service.activate(&saved.id, PromptTarget::Claude).unwrap() {
            crate::prompts::model::ActivateOutcome::Ok { status } => {
                assert_eq!(status.active_preset_id.as_deref(), Some(saved.id.as_str()));
                assert!(status.matches_active_preset);
            }
            crate::prompts::model::ActivateOutcome::BackfillConflict { .. } => panic!("activate 无冲突路径"),
        }
        assert_eq!(read_live(&env, PromptTarget::Claude).unwrap(), "新内容");
        assert_eq!(
            service.store.active_preset_id(PromptTarget::Claude).unwrap().as_deref(),
            Some(saved.id.as_str())
        );
        let backups = std::fs::read_dir(env.backups.join("claude")).unwrap().count();
        assert_eq!(backups, 1, "激活前必须留一份外部内容备份");
    }

    #[test]
    fn activate_backfills_external_edit_into_previous_active_preset() {
        let env = env();
        let service = service(&env);
        let first = match service.save(None, "A", "a1").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        service.activate(&first.id, PromptTarget::Codex).unwrap();
        write_live(&env, PromptTarget::Codex, "外部修改");
        let second = match service.save(None, "B", "b1").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        service.activate(&second.id, PromptTarget::Codex).unwrap();
        let first_content = service.get(&first.id).unwrap().content;
        assert_eq!(first_content, "外部修改", "被换下的激活预设必须吸收外部修改");
        assert_eq!(read_live(&env, PromptTarget::Codex).unwrap(), "b1");
    }

    #[test]
    fn activate_skips_uninstalled_target() {
        let env = env();
        let service = service(&env);
        let saved = match service.save(None, "P", "v1").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        let error = service.activate(&saved.id, PromptTarget::Dsh).unwrap_err();
        assert!(matches!(error, PromptsError::TargetNotInstalled(PromptTarget::Dsh)));
    }

    #[test]
    fn deactivate_clears_file_and_record() {
        let env = env();
        let service = service(&env);
        let saved = match service.save(None, "P", "v1").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        service.activate(&saved.id, PromptTarget::Claude).unwrap();
        service.deactivate(PromptTarget::Claude).unwrap();
        assert_eq!(read_live(&env, PromptTarget::Claude), Some(String::new()));
        assert_eq!(service.store.active_preset_id(PromptTarget::Claude).unwrap(), None);
    }

    #[test]
    fn switching_activation_replaces_previous_preset() {
        let env = env();
        let service = service(&env);
        let first = match service.save(None, "A", "a").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        let second = match service.save(None, "B", "b").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        service.activate(&first.id, PromptTarget::Claude).unwrap();
        service.activate(&second.id, PromptTarget::Claude).unwrap();
        assert_eq!(service.store.active_preset_id(PromptTarget::Claude).unwrap().as_deref(), Some(second.id.as_str()));
        assert!(service.store.activated_targets(&first.id).unwrap().is_empty());
    }

    #[test]
    fn delete_is_rejected_while_any_target_activates_the_preset() {
        let env = env();
        let service = service(&env);
        let saved = match service.save(None, "A", "C").unwrap() {
            Flow::Done(SaveOutcome::Saved { preset, .. }) => preset,
            _ => panic!(),
        };
        service.activate(&saved.id, PromptTarget::Claude).unwrap();
        let error = service.delete(&saved.id).unwrap_err();
        assert!(matches!(error, PromptsError::PresetActive(_)));
    }
}
