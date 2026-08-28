use std::path::{Path, PathBuf};

use crate::prompts::backup::backup_live_file;
use crate::prompts::model::{
    ConflictCandidate, Flow, PresetSummary, PromptPreset, PromptTarget, PromptsError, Result,
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
        if trimmed.is_empty() || trimmed.len() > 200 {
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

    /// 保存:新建或更新;更新前先回填检测(外部修改单目标→静默采纳;多目标分歧→冲突)。
    pub fn save(&self, preset_id: Option<&str>, title: &str, content: &str) -> Result<Flow<SaveOutcome>> {
        Self::validate_title(title)?;
        Self::validate_content(content)?;
        let now = Self::now_ms();
        let stored = match preset_id {
            None => {
                let id = uuid::Uuid::new_v4().to_string();
                self.store.insert_preset(&id, title.trim(), content, now, now)?;
                PromptPreset {
                    id,
                    title: title.trim().to_owned(),
                    content: content.to_owned(),
                    created_at: now,
                    updated_at: now,
                }
            }
            Some(existing_id) => {
                let existing = self.get(existing_id)?;
                if let Err(candidates) = self.detect_backfill(existing_id, &existing.content) {
                    return Ok(Flow::Conflict { preset_id: existing_id.to_owned(), candidates });
                }
                self.store.update_preset(existing_id, title.trim(), content, now)?;
                PromptPreset {
                    id: existing.id,
                    title: title.trim().to_owned(),
                    content: content.to_owned(),
                    created_at: existing.created_at,
                    updated_at: now,
                }
            }
        };
        let projected = match self.project_active_targets(&stored)? {
            Flow::Done(projected) => projected,
            Flow::Conflict { candidates, .. } => {
                return Ok(Flow::Conflict { preset_id: stored.id.clone(), candidates });
            }
        };
        Ok(Flow::Done(SaveOutcome::Saved { preset: stored, projected }))
    }

    /// 删除:任一目标仍激活该预设时拒绝(要求先停用)。
    pub fn delete(&self, preset_id: &str) -> Result<()> {
        if !self.store.activated_targets(preset_id)?.is_empty() {
            return Err(PromptsError::PresetActive(preset_id.to_owned()));
        }
        self.store.delete_preset(preset_id)
    }

    /// 回填检测(spec §5):
    /// - 无激活目标 → Ok(None);
    /// - 恰一个候选(live ≠ DB)→ Ok(Some(live))(静默回填源);
    /// - ≥2 个候选(live 分歧)→ Err(candidates) 冲突;
    /// - 其余(live 为空/与 DB 一致)→ Ok(None)。
    fn detect_backfill(
        &self,
        preset_id: &str,
        db_content: &str,
    ) -> std::result::Result<Option<String>, Vec<ConflictCandidate>> {
        let mut candidates = Vec::new();
        for target in self.store.activated_targets(preset_id).unwrap_or_default() {
            let Ok(Some(live)) = self.live_content(target) else { continue };
            if live != db_content {
                candidates.push(ConflictCandidate { target, content: live, updated_at: Self::now_ms() });
            }
        }
        match candidates.len() {
            0 => Ok(None),
            1 => Ok(Some(candidates.remove(0).content)),
            _ => Err(candidates),
        }
    }

    /// 把预设内容写入所有激活它的目标(先冲突检测,再逐目标备份+原子写)。
    pub(crate) fn project_active_targets(&self, preset: &PromptPreset) -> Result<Flow<Vec<TargetStatus>>> {
        let activated = self.store.activated_targets(&preset.id)?;
        if activated.is_empty() {
            return Ok(Flow::Done(Vec::new()));
        }
        let mut candidates = Vec::new();
        for target in &activated {
            if let Ok(Some(live)) = self.live_content(*target) {
                if live != preset.content {
                    candidates.push(ConflictCandidate {
                        target: *target,
                        content: live,
                        updated_at: Self::now_ms(),
                    });
                }
            }
        }
        if !candidates.is_empty() {
            return Ok(Flow::Conflict { preset_id: preset.id.clone(), candidates });
        }
        let mut projected = Vec::new();
        for target in &activated {
            self.write_target(*target, &preset.content)?;
            projected.push(self.status_of(*target)?);
        }
        Ok(Flow::Done(projected))
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
}
