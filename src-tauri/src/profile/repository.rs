use std::{
    path::PathBuf,
    sync::{Mutex, MutexGuard},
};

use chrono::Utc;
use semver::Version;
use uuid::Uuid;

use super::model::{
    ActivationReason, FailedActivation, LastKnownGood, PendingActivation, ProfileDraft,
    ProfileListSnapshot, ProfilePatch, ProfileRecord, ProfileSelection, ProfileState,
    ProfileStatus, ProfileSummary,
};
use crate::{
    runtime::model::RuntimeFailure,
    storage::atomic_json::{read_optional, write_atomic},
};

pub struct ProfileRepository {
    profiles_path: PathBuf,
    state_path: PathBuf,
    transaction: Mutex<()>,
}

impl ProfileRepository {
    pub fn open(root: PathBuf) -> Result<Self, RuntimeFailure> {
        std::fs::create_dir_all(&root).map_err(RuntimeFailure::internal)?;
        Ok(Self {
            profiles_path: root.join("profiles.json"),
            state_path: root.join("state.json"),
            transaction: Mutex::new(()),
        })
    }

    pub fn create(&self, draft: ProfileDraft) -> Result<ProfileRecord, RuntimeFailure> {
        let _guard = self.lock()?;
        validate_draft(&draft)?;
        let mut profiles = self.load_profiles()?;
        ensure_unique(&profiles, None, &draft)?;
        let now = Utc::now();
        let profile = ProfileRecord {
            id: Uuid::new_v4(),
            name: draft.name.trim().to_string(),
            data_root: draft.data_root,
            permission_mode: draft.permission_mode,
            revision: 1,
            created_at: now,
            updated_at: now,
        };
        profiles.push(profile.clone());
        write_atomic(&self.profiles_path, &profiles)?;
        Ok(profile)
    }

    pub fn list(&self) -> Result<Vec<ProfileRecord>, RuntimeFailure> {
        let _guard = self.lock()?;
        self.load_profiles()
    }

    pub fn snapshot(&self) -> Result<ProfileListSnapshot, RuntimeFailure> {
        let _guard = self.lock()?;
        let profiles = self.load_profiles()?;
        let state = self.load_state()?;
        let selected_profile_id = state
            .selected_profile
            .as_ref()
            .map(|value| value.profile_id);
        let pending_profile_id = state.pending.as_ref().map(|value| value.target.profile_id);
        let last_known_good_profile_id =
            state.last_known_good.as_ref().map(|value| value.profile_id);
        let summaries = profiles
            .into_iter()
            .map(|profile| {
                let status = if pending_profile_id == Some(profile.id) {
                    ProfileStatus::Switching
                } else if selected_profile_id == Some(profile.id) {
                    ProfileStatus::Active
                } else if last_known_good_profile_id == Some(profile.id) {
                    ProfileStatus::Recovered
                } else if state.failed_attempts.iter().rev().any(|failed| {
                    failed.target.profile_id == profile.id
                        && failed.target.revision == profile.revision
                }) {
                    ProfileStatus::Invalid
                } else {
                    ProfileStatus::Ready
                };
                let runtime_version = state
                    .last_known_good
                    .as_ref()
                    .filter(|value| value.profile_id == profile.id)
                    .map(|value| value.runtime_version.clone());
                ProfileSummary {
                    profile,
                    runtime_version,
                    status,
                }
            })
            .collect();
        Ok(ProfileListSnapshot {
            selected_profile_id,
            pending_profile_id,
            last_known_good_profile_id,
            profiles: summaries,
        })
    }

    pub fn get(&self, id: &Uuid) -> Result<ProfileRecord, RuntimeFailure> {
        let _guard = self.lock()?;
        find_profile(&self.load_profiles()?, id)
    }

    pub fn update(
        &self,
        id: &Uuid,
        expected_revision: u64,
        patch: ProfilePatch,
    ) -> Result<ProfileRecord, RuntimeFailure> {
        let _guard = self.lock()?;
        let mut profiles = self.load_profiles()?;
        let index = profiles
            .iter()
            .position(|profile| &profile.id == id)
            .ok_or_else(|| fatal("Profile 不存在"))?;
        if profiles[index].revision != expected_revision {
            return Err(fatal("Profile revision 已变化，请刷新后重试"));
        }
        let draft = ProfileDraft {
            name: patch.name.unwrap_or_else(|| profiles[index].name.clone()),
            data_root: patch
                .data_root
                .unwrap_or_else(|| profiles[index].data_root.clone()),
            permission_mode: patch
                .permission_mode
                .unwrap_or(profiles[index].permission_mode),
        };
        validate_draft(&draft)?;
        ensure_unique(&profiles, Some(id), &draft)?;
        let profile = &mut profiles[index];
        profile.name = draft.name.trim().to_string();
        profile.data_root = draft.data_root;
        profile.permission_mode = draft.permission_mode;
        profile.revision += 1;
        profile.updated_at = Utc::now();
        let updated = profile.clone();
        write_atomic(&self.profiles_path, &profiles)?;
        Ok(updated)
    }

    pub fn duplicate(
        &self,
        id: &Uuid,
        draft: ProfileDraft,
    ) -> Result<ProfileRecord, RuntimeFailure> {
        self.get(id)?;
        self.create(draft)
    }

    pub fn delete(&self, id: &Uuid) -> Result<(), RuntimeFailure> {
        let _guard = self.lock()?;
        let state = self.load_state()?;
        let protected = state
            .selected_profile
            .as_ref()
            .is_some_and(|selection| &selection.profile_id == id)
            || state
                .pending
                .as_ref()
                .is_some_and(|pending| &pending.target.profile_id == id)
            || state
                .last_known_good
                .as_ref()
                .is_some_and(|lkg| &lkg.profile_id == id);
        if protected {
            return Err(fatal("不能删除当前、待激活或 last-known-good Profile"));
        }
        let mut profiles = self.load_profiles()?;
        let initial_len = profiles.len();
        profiles.retain(|profile| &profile.id != id);
        if profiles.len() == initial_len {
            return Err(fatal("Profile 不存在"));
        }
        write_atomic(&self.profiles_path, &profiles)
    }

    pub fn state(&self) -> Result<ProfileState, RuntimeFailure> {
        let _guard = self.lock()?;
        self.load_state()
    }

    pub fn begin_activation(
        &self,
        id: &Uuid,
        revision: u64,
        generation_id: impl Into<String>,
        reason: ActivationReason,
    ) -> Result<(), RuntimeFailure> {
        let _guard = self.lock()?;
        let profile = find_profile(&self.load_profiles()?, id)?;
        if profile.revision != revision {
            return Err(fatal("Profile revision 与激活请求不一致"));
        }
        let mut state = self.load_state()?;
        if state.pending.is_some() {
            return Err(fatal("已有 Profile 正在激活"));
        }
        state.pending = Some(PendingActivation {
            target: ProfileSelection {
                profile_id: *id,
                revision,
            },
            previous: state.selected_profile.clone(),
            generation_id: generation_id.into(),
            reason,
            requested_at: Utc::now(),
        });
        write_atomic(&self.state_path, &state)
    }

    pub fn commit_pending(
        &self,
        generation_id: &str,
        runtime_version: Version,
    ) -> Result<(), RuntimeFailure> {
        let _guard = self.lock()?;
        let mut state = self.load_state()?;
        let pending = take_matching_pending(&mut state, generation_id)?;
        state.selected_profile = Some(pending.target.clone());
        state.last_known_good = Some(LastKnownGood {
            profile_id: pending.target.profile_id,
            revision: pending.target.revision,
            runtime_version,
            verified_at: Utc::now(),
        });
        write_atomic(&self.state_path, &state)
    }

    pub fn fail_pending(
        &self,
        generation_id: &str,
        phase: impl Into<String>,
        cause: impl Into<String>,
    ) -> Result<(), RuntimeFailure> {
        let _guard = self.lock()?;
        let mut state = self.load_state()?;
        fail_pending_state(&mut state, generation_id, phase.into(), cause.into())?;
        write_atomic(&self.state_path, &state)
    }

    pub fn recover_interrupted(&self) -> Result<bool, RuntimeFailure> {
        let _guard = self.lock()?;
        let mut state = self.load_state()?;
        let Some(generation_id) = state
            .pending
            .as_ref()
            .map(|pending| pending.generation_id.clone())
        else {
            return Ok(false);
        };
        fail_pending_state(
            &mut state,
            &generation_id,
            "startup-recovery".to_string(),
            "应用在 Profile 激活完成前退出".to_string(),
        )?;
        write_atomic(&self.state_path, &state)?;
        Ok(true)
    }

    fn lock(&self) -> Result<MutexGuard<'_, ()>, RuntimeFailure> {
        self.transaction
            .lock()
            .map_err(|_| fatal("Profile 仓库锁已损坏"))
    }

    fn load_profiles(&self) -> Result<Vec<ProfileRecord>, RuntimeFailure> {
        Ok(read_optional(&self.profiles_path)?.unwrap_or_default())
    }

    fn load_state(&self) -> Result<ProfileState, RuntimeFailure> {
        Ok(read_optional(&self.state_path)?.unwrap_or_default())
    }
}

fn validate_draft(draft: &ProfileDraft) -> Result<(), RuntimeFailure> {
    if draft.name.trim().is_empty() {
        return Err(fatal("Profile 名称不能为空"));
    }
    if !draft.data_root.is_absolute() {
        return Err(fatal("Profile 数据目录必须是绝对路径"));
    }
    Ok(())
}

fn ensure_unique(
    profiles: &[ProfileRecord],
    current_id: Option<&Uuid>,
    draft: &ProfileDraft,
) -> Result<(), RuntimeFailure> {
    if profiles.iter().any(|profile| {
        Some(&profile.id) != current_id
            && (profile.name.eq_ignore_ascii_case(draft.name.trim())
                || profile.data_root == draft.data_root)
    }) {
        return Err(fatal("Profile 名称或数据目录已存在"));
    }
    Ok(())
}

fn find_profile(profiles: &[ProfileRecord], id: &Uuid) -> Result<ProfileRecord, RuntimeFailure> {
    profiles
        .iter()
        .find(|profile| &profile.id == id)
        .cloned()
        .ok_or_else(|| fatal("Profile 不存在"))
}

fn take_matching_pending(
    state: &mut ProfileState,
    generation_id: &str,
) -> Result<PendingActivation, RuntimeFailure> {
    let pending = state
        .pending
        .take()
        .ok_or_else(|| fatal("没有待激活的 Profile"))?;
    if pending.generation_id != generation_id {
        state.pending = Some(pending);
        return Err(fatal("Generation 与待激活 Profile 不匹配"));
    }
    Ok(pending)
}

fn fail_pending_state(
    state: &mut ProfileState,
    generation_id: &str,
    phase: String,
    cause: String,
) -> Result<(), RuntimeFailure> {
    let pending = take_matching_pending(state, generation_id)?;
    state.selected_profile = pending.previous.clone().or_else(|| {
        state.last_known_good.as_ref().map(|lkg| ProfileSelection {
            profile_id: lkg.profile_id,
            revision: lkg.revision,
        })
    });
    state.failed_attempts.push(FailedActivation {
        target: pending.target,
        generation_id: pending.generation_id,
        phase,
        cause,
        failed_at: Utc::now(),
    });
    if state.failed_attempts.len() > 20 {
        state
            .failed_attempts
            .drain(..state.failed_attempts.len() - 20);
    }
    Ok(())
}

fn fatal(message: impl Into<String>) -> RuntimeFailure {
    let mut failure = RuntimeFailure::internal(message.into());
    failure.recoverable = false;
    failure
}

#[cfg(test)]
mod tests {
    use super::ProfileRepository;
    use crate::profile::model::{ActivationReason, ProfileDraft};

    #[test]
    fn failed_pending_activation_restores_last_known_good() {
        let dir = tempfile::tempdir().unwrap();
        let repo = ProfileRepository::open(dir.path().to_path_buf()).unwrap();
        let a = repo
            .create(ProfileDraft::named("A", dir.path().join("a")))
            .unwrap();
        repo.begin_activation(&a.id, a.revision, "g-a", ActivationReason::Startup)
            .unwrap();
        repo.commit_pending("g-a", semver::Version::new(1, 0, 0))
            .unwrap();
        let b = repo
            .create(ProfileDraft::named("B", dir.path().join("b")))
            .unwrap();
        repo.begin_activation(&b.id, b.revision, "g-b", ActivationReason::UserSwitch)
            .unwrap();
        repo.fail_pending("g-b", "process", "exit 1").unwrap();
        let state = repo.state().unwrap();
        assert_eq!(state.selected_profile.as_ref().unwrap().profile_id, a.id);
        assert_eq!(state.last_known_good.as_ref().unwrap().profile_id, a.id);
        assert!(state.pending.is_none());
    }

    #[test]
    fn interrupted_pending_is_recovered_and_lkg_cannot_be_deleted() {
        let dir = tempfile::tempdir().unwrap();
        let repo = ProfileRepository::open(dir.path().to_path_buf()).unwrap();
        let a = repo
            .create(ProfileDraft::named("A", dir.path().join("a")))
            .unwrap();
        repo.begin_activation(&a.id, a.revision, "g-a", ActivationReason::Startup)
            .unwrap();
        repo.commit_pending("g-a", semver::Version::new(1, 0, 0))
            .unwrap();
        let b = repo
            .create(ProfileDraft::named("B", dir.path().join("b")))
            .unwrap();
        repo.begin_activation(&b.id, b.revision, "g-b", ActivationReason::UserSwitch)
            .unwrap();

        assert!(repo.recover_interrupted().unwrap());
        assert_eq!(
            repo.state().unwrap().selected_profile.unwrap().profile_id,
            a.id
        );
        assert!(repo.delete(&a.id).is_err());
    }

    #[test]
    fn update_increments_revision_and_rejects_stale_writes() {
        let dir = tempfile::tempdir().unwrap();
        let repo = ProfileRepository::open(dir.path().to_path_buf()).unwrap();
        let profile = repo
            .create(ProfileDraft::named("A", dir.path().join("a")))
            .unwrap();
        let updated = repo
            .update(
                &profile.id,
                profile.revision,
                crate::profile::model::ProfilePatch {
                    name: Some("A2".to_string()),
                    ..Default::default()
                },
            )
            .unwrap();
        assert_eq!(updated.revision, profile.revision + 1);
        assert!(
            repo.update(&profile.id, profile.revision, Default::default(),)
                .is_err()
        );
    }

    #[test]
    fn snapshot_joins_activation_and_runtime_metadata() {
        let dir = tempfile::tempdir().unwrap();
        let repo = ProfileRepository::open(dir.path().to_path_buf()).unwrap();
        let active = repo
            .create(ProfileDraft::named("Active", dir.path().join("active")))
            .unwrap();
        repo.begin_activation(
            &active.id,
            active.revision,
            "g-active",
            ActivationReason::Startup,
        )
        .unwrap();
        repo.commit_pending("g-active", semver::Version::new(1, 8, 2))
            .unwrap();
        let pending = repo
            .create(ProfileDraft::named("Pending", dir.path().join("pending")))
            .unwrap();
        repo.begin_activation(
            &pending.id,
            pending.revision,
            "g-pending",
            ActivationReason::UserSwitch,
        )
        .unwrap();

        let snapshot = repo.snapshot().unwrap();

        assert_eq!(snapshot.selected_profile_id, Some(active.id));
        assert_eq!(snapshot.pending_profile_id, Some(pending.id));
        assert_eq!(snapshot.last_known_good_profile_id, Some(active.id));
        assert_eq!(
            snapshot.profiles[0].status,
            crate::profile::model::ProfileStatus::Active
        );
        assert_eq!(
            snapshot.profiles[0]
                .runtime_version
                .as_ref()
                .unwrap()
                .to_string(),
            "1.8.2"
        );
        assert_eq!(
            snapshot.profiles[1].status,
            crate::profile::model::ProfileStatus::Switching
        );
    }
}
