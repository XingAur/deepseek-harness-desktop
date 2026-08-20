use std::path::{Path, PathBuf};

use chrono::Utc;
use semver::VersionReq;
use uuid::Uuid;

use super::model::{PreparedProvisioning, ProvisioningReceipt};
use crate::{
    runtime::model::RuntimeFailure,
    storage::atomic_json::{read_optional, write_atomic},
};

pub struct ProvisioningReceiptStore {
    prepared_path: PathBuf,
    final_path: PathBuf,
    runtime_root: PathBuf,
}

impl ProvisioningReceiptStore {
    pub fn new(state_root: PathBuf) -> Self {
        let active_root = state_root.parent().unwrap_or(&state_root);
        Self {
            prepared_path: state_root.join("provisioning-prepared.json"),
            final_path: state_root.join("provisioning.json"),
            runtime_root: active_root.join("runtime"),
        }
    }

    pub fn read_prepared(&self) -> Result<Option<PreparedProvisioning>, RuntimeFailure> {
        read_optional(&self.prepared_path)
    }

    pub fn read_final(&self) -> Result<Option<ProvisioningReceipt>, RuntimeFailure> {
        read_optional(&self.final_path)
    }

    pub fn write_prepared(&self, prepared: &PreparedProvisioning) -> Result<(), RuntimeFailure> {
        validate_candidate(&self.runtime_root, &prepared.candidate_dir)?;
        write_atomic(&self.prepared_path, prepared)
    }

    pub fn commit(
        &self,
        session_id: Uuid,
        manifest_hash: &str,
    ) -> Result<ProvisioningReceipt, RuntimeFailure> {
        let prepared = self.validate_prepared(session_id, manifest_hash)?;
        let active_dir = prepared.candidate_dir.clone();
        self.finalize(&prepared, active_dir)
    }

    pub fn validate_prepared(
        &self,
        session_id: Uuid,
        manifest_hash: &str,
    ) -> Result<PreparedProvisioning, RuntimeFailure> {
        let prepared = self
            .read_prepared()?
            .ok_or_else(|| RuntimeFailure::internal("没有待提交的 Runtime provisioning receipt"))?;
        if prepared.session_id != session_id || prepared.manifest_sha256 != manifest_hash {
            return Err(RuntimeFailure::internal(
                "Provisioning session 或 manifest hash 不匹配",
            ));
        }
        validate_candidate(&self.runtime_root, &prepared.candidate_dir)?;
        Ok(prepared)
    }

    pub fn finalize(
        &self,
        prepared: &PreparedProvisioning,
        active_dir: PathBuf,
    ) -> Result<ProvisioningReceipt, RuntimeFailure> {
        let active_dir = validate_candidate(&self.runtime_root, &active_dir)?;
        let receipt = ProvisioningReceipt {
            schema_version: 1,
            verifier_version: 1,
            session_id: prepared.session_id,
            desktop_version: prepared.desktop_version.clone(),
            compatibility_requirement: VersionReq::parse(&format!("^{}", prepared.runtime_version))
                .map_err(RuntimeFailure::internal)?,
            target: prepared.target,
            runtime_version: prepared.runtime_version.clone(),
            manifest_sha256: prepared.manifest_sha256.clone(),
            payload_sha256: prepared.payload_sha256.clone(),
            active_dir,
            probe_contract_version: prepared.probe_contract_version,
            completed_at: Utc::now(),
        };
        write_atomic(&self.final_path, &receipt)?;
        // final receipt 已经持久化后，prepared 只剩可回收的暂存状态；清理失败不能
        // 让调用方回滚一个已被记录为成功的 Runtime pointer。
        let _ = remove_prepared(&self.prepared_path);
        Ok(receipt)
    }

    pub fn discard(&self, session_id: Uuid) -> Result<(), RuntimeFailure> {
        let prepared = self
            .read_prepared()?
            .ok_or_else(|| RuntimeFailure::internal("没有待丢弃的 provisioning session"))?;
        if prepared.session_id != session_id {
            return Err(RuntimeFailure::internal("Provisioning session 不匹配"));
        }
        remove_prepared(&self.prepared_path)
    }
}

fn validate_candidate(runtime_root: &Path, candidate: &Path) -> Result<PathBuf, RuntimeFailure> {
    let runtime_root = runtime_root
        .canonicalize()
        .map_err(RuntimeFailure::internal)?;
    let candidate = candidate.canonicalize().map_err(RuntimeFailure::internal)?;
    if candidate == runtime_root || !candidate.starts_with(&runtime_root) {
        return Err(RuntimeFailure::internal(
            "Provisioning candidate 不在受管 Runtime 目录内",
        ));
    }
    Ok(candidate)
}

fn remove_prepared(path: &Path) -> Result<(), RuntimeFailure> {
    std::fs::remove_file(path).map_err(RuntimeFailure::internal)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> (tempfile::TempDir, ProvisioningReceiptStore, PathBuf) {
        let dir = tempfile::tempdir().unwrap();
        let state = dir.path().join("state");
        let candidate = dir.path().join("runtime/candidates/候选 A");
        std::fs::create_dir_all(&state).unwrap();
        std::fs::create_dir_all(&candidate).unwrap();
        (dir, ProvisioningReceiptStore::new(state), candidate)
    }

    #[test]
    fn prepared_receipt_is_not_final_until_the_matching_session_commits() {
        let (_dir, store, candidate) = fixture();
        let prepared = PreparedProvisioning::fixture("session-a", "1.8.2", "manifest-a", candidate);
        store.write_prepared(&prepared).unwrap();
        assert!(store.read_final().unwrap().is_none());
        assert!(store.commit(Uuid::new_v4(), "manifest-a").is_err());
        let final_receipt = store.commit(prepared.session_id, "manifest-a").unwrap();
        assert_eq!(final_receipt.runtime_version.to_string(), "1.8.2");
        assert!(store.read_prepared().unwrap().is_none());
        assert_eq!(store.read_final().unwrap().unwrap(), final_receipt);
    }

    #[test]
    fn discard_removes_only_the_uncommitted_session() {
        let (_dir, store, candidate) = fixture();
        let prepared = PreparedProvisioning::fixture("session-a", "1.8.2", "manifest-a", candidate);
        store.write_prepared(&prepared).unwrap();
        assert!(store.discard(Uuid::new_v4()).is_err());
        store.discard(prepared.session_id).unwrap();
        assert!(store.read_prepared().unwrap().is_none());
    }

    #[test]
    fn rejects_a_candidate_outside_the_managed_runtime_root() {
        let (dir, store, _candidate) = fixture();
        let outside = dir.path().join("outside");
        std::fs::create_dir_all(&outside).unwrap();
        let prepared = PreparedProvisioning::fixture("session-a", "1.8.2", "manifest-a", outside);
        assert!(store.write_prepared(&prepared).is_err());
    }
}
