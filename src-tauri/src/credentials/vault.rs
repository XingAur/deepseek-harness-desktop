use super::model::{
    CredentialId, CredentialMetadata, CredentialStatus, SecretValue, VaultError, VaultErrorCode,
};

pub const SERVICE_NAME: &str = "ai.deepseek.harness.desktop.agent-credentials.v1";

pub trait CredentialVault: Send + Sync {
    fn put(
        &self,
        credential_id: Option<&CredentialId>,
        secret: SecretValue,
    ) -> Result<CredentialMetadata, VaultError>;

    fn resolve(&self, credential_id: &CredentialId) -> Result<SecretValue, VaultError>;

    fn delete(&self, credential_id: &CredentialId) -> Result<CredentialStatus, VaultError>;

    fn status(&self, credential_id: &CredentialId) -> Result<CredentialStatus, VaultError>;
}

#[derive(Clone, Copy)]
pub(crate) enum BackendErrorKind {
    Missing,
    Locked,
    Unavailable,
}

pub(crate) struct BackendError {
    kind: BackendErrorKind,
    private_detail: Vec<u8>,
}

impl BackendError {
    fn new(kind: BackendErrorKind, detail: impl Into<String>) -> Self {
        Self {
            kind,
            private_detail: detail.into().into_bytes(),
        }
    }

    fn into_vault_error(self) -> VaultError {
        let code = match self.kind {
            BackendErrorKind::Missing => VaultErrorCode::NotConfigured,
            BackendErrorKind::Locked => VaultErrorCode::SecureStoreLocked,
            BackendErrorKind::Unavailable => VaultErrorCode::SecureStoreUnavailable,
        };
        VaultError::new(code)
    }
}

impl Drop for BackendError {
    fn drop(&mut self) {
        self.private_detail.fill(0);
    }
}

pub(crate) trait SecureStoreBackend: Send + Sync {
    fn set(&self, account: &str, secret: &SecretValue) -> Result<(), BackendError>;
    fn get(&self, account: &str) -> Result<SecretValue, BackendError>;
    fn delete(&self, account: &str) -> Result<(), BackendError>;
}

pub(crate) struct BackendCredentialVault<B> {
    backend: B,
}

impl<B> BackendCredentialVault<B> {
    pub(crate) fn new(backend: B) -> Self {
        Self { backend }
    }
}

impl<B: SecureStoreBackend> CredentialVault for BackendCredentialVault<B> {
    fn put(
        &self,
        credential_id: Option<&CredentialId>,
        secret: SecretValue,
    ) -> Result<CredentialMetadata, VaultError> {
        let credential_id = credential_id.cloned().unwrap_or_else(CredentialId::new);
        self.backend
            .set(credential_id.as_str(), &secret)
            .map_err(BackendError::into_vault_error)?;
        Ok(CredentialMetadata {
            credential_id,
            status: CredentialStatus::Configured,
        })
    }

    fn resolve(&self, credential_id: &CredentialId) -> Result<SecretValue, VaultError> {
        self.backend
            .get(credential_id.as_str())
            .map_err(BackendError::into_vault_error)
    }

    fn delete(&self, credential_id: &CredentialId) -> Result<CredentialStatus, VaultError> {
        match self.backend.delete(credential_id.as_str()) {
            Ok(())
            | Err(BackendError {
                kind: BackendErrorKind::Missing,
                ..
            }) => Ok(CredentialStatus::NotConfigured),
            Err(error) => Err(error.into_vault_error()),
        }
    }

    fn status(&self, credential_id: &CredentialId) -> Result<CredentialStatus, VaultError> {
        match self.backend.get(credential_id.as_str()) {
            Ok(secret) => {
                drop(secret);
                Ok(CredentialStatus::Configured)
            }
            Err(BackendError {
                kind: BackendErrorKind::Missing,
                ..
            }) => Ok(CredentialStatus::NotConfigured),
            Err(error) => Err(error.into_vault_error()),
        }
    }
}

struct KeyringBackend;

impl KeyringBackend {
    fn entry(account: &str) -> Result<keyring::Entry, BackendError> {
        keyring::Entry::new(SERVICE_NAME, account).map_err(map_keyring_error)
    }
}

impl SecureStoreBackend for KeyringBackend {
    fn set(&self, account: &str, secret: &SecretValue) -> Result<(), BackendError> {
        Self::entry(account)?
            .set_password(secret.expose_for_backend())
            .map_err(map_keyring_error)
    }

    fn get(&self, account: &str) -> Result<SecretValue, BackendError> {
        let bytes = Self::entry(account)?
            .get_password()
            .map(String::into_bytes)
            .map_err(map_keyring_error)?;
        Ok(SecretValue::from_bytes(bytes))
    }

    fn delete(&self, account: &str) -> Result<(), BackendError> {
        Self::entry(account)?
            .delete_credential()
            .map_err(map_keyring_error)
    }
}

fn map_keyring_error(error: keyring::Error) -> BackendError {
    let kind = match error {
        keyring::Error::NoEntry => BackendErrorKind::Missing,
        keyring::Error::NoStorageAccess(_) => BackendErrorKind::Locked,
        _ => BackendErrorKind::Unavailable,
    };
    BackendError::new(kind, "native secure-store operation failed")
}

pub(crate) struct NativeCredentialVault {
    inner: BackendCredentialVault<KeyringBackend>,
}

impl NativeCredentialVault {
    pub(crate) fn new() -> Self {
        Self {
            inner: BackendCredentialVault::new(KeyringBackend),
        }
    }
}

impl CredentialVault for NativeCredentialVault {
    fn put(
        &self,
        credential_id: Option<&CredentialId>,
        secret: SecretValue,
    ) -> Result<CredentialMetadata, VaultError> {
        self.inner.put(credential_id, secret)
    }

    fn resolve(&self, credential_id: &CredentialId) -> Result<SecretValue, VaultError> {
        self.inner.resolve(credential_id)
    }

    fn delete(&self, credential_id: &CredentialId) -> Result<CredentialStatus, VaultError> {
        self.inner.delete(credential_id)
    }

    fn status(&self, credential_id: &CredentialId) -> Result<CredentialStatus, VaultError> {
        self.inner.status(credential_id)
    }
}

#[cfg(test)]
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};

#[cfg(test)]
#[derive(Clone, Default)]
pub(crate) struct MemoryBackend {
    state: Arc<Mutex<MemoryState>>,
}

#[cfg(test)]
#[derive(Default)]
struct MemoryState {
    entries: HashMap<String, Vec<u8>>,
    fail_next: Option<BackendError>,
}

#[cfg(test)]
impl Drop for MemoryState {
    fn drop(&mut self) {
        for secret in self.entries.values_mut() {
            secret.fill(0);
        }
    }
}

#[cfg(test)]
impl MemoryBackend {
    pub(crate) fn accounts(&self) -> Vec<String> {
        let mut accounts = self
            .state
            .lock()
            .unwrap()
            .entries
            .keys()
            .cloned()
            .collect::<Vec<_>>();
        accounts.sort();
        accounts
    }

    pub(crate) fn fail_next(&self, kind: BackendErrorKind, detail: impl Into<String>) {
        self.state.lock().unwrap().fail_next = Some(BackendError::new(kind, detail));
    }

    fn pending_failure(state: &mut MemoryState) -> Result<(), BackendError> {
        match state.fail_next.take() {
            Some(error) => Err(error),
            None => Ok(()),
        }
    }
}

#[cfg(test)]
impl SecureStoreBackend for MemoryBackend {
    fn set(&self, account: &str, secret: &SecretValue) -> Result<(), BackendError> {
        let mut state = self.state.lock().unwrap();
        Self::pending_failure(&mut state)?;
        if let Some(mut previous) = state.entries.insert(
            account.to_owned(),
            secret.expose_for_backend().as_bytes().to_vec(),
        ) {
            previous.fill(0);
        }
        Ok(())
    }

    fn get(&self, account: &str) -> Result<SecretValue, BackendError> {
        let mut state = self.state.lock().unwrap();
        Self::pending_failure(&mut state)?;
        state
            .entries
            .get(account)
            .cloned()
            .map(SecretValue::from_bytes)
            .ok_or_else(|| BackendError::new(BackendErrorKind::Missing, "credential not found"))
    }

    fn delete(&self, account: &str) -> Result<(), BackendError> {
        let mut state = self.state.lock().unwrap();
        Self::pending_failure(&mut state)?;
        match state.entries.remove(account) {
            Some(mut secret) => {
                secret.fill(0);
                Ok(())
            }
            None => Err(BackendError::new(
                BackendErrorKind::Missing,
                "credential not found",
            )),
        }
    }
}
