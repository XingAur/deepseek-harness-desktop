use std::fmt;

use serde::Serialize;
use uuid::Uuid;
use zeroize::{Zeroize, ZeroizeOnDrop};

#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize)]
#[serde(transparent)]
pub struct CredentialId(String);

impl CredentialId {
    pub fn new() -> Self {
        Self(Uuid::new_v4().to_string())
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for CredentialId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum CredentialStatus {
    Configured,
    NotConfigured,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CredentialMetadata {
    pub credential_id: CredentialId,
    pub status: CredentialStatus,
}

pub struct SecretValue {
    bytes: Vec<u8>,
}

impl Zeroize for SecretValue {
    fn zeroize(&mut self) {
        self.bytes.zeroize();
    }
}

impl Drop for SecretValue {
    fn drop(&mut self) {
        self.zeroize();
    }
}

impl ZeroizeOnDrop for SecretValue {}

impl SecretValue {
    pub fn new(value: impl Into<String>) -> Self {
        Self {
            bytes: value.into().into_bytes(),
        }
    }

    pub(crate) fn from_bytes(bytes: Vec<u8>) -> Self {
        Self { bytes }
    }

    pub(crate) fn expose_for_backend(&self) -> &str {
        std::str::from_utf8(&self.bytes).expect("SecretValue is constructed from UTF-8 text")
    }

    pub(crate) fn expose_bytes_for_backend(&self) -> &[u8] {
        &self.bytes
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VaultErrorCode {
    NotConfigured,
    SecureStoreLocked,
    SecureStoreUnavailable,
}

#[derive(Debug)]
pub struct VaultError {
    code: VaultErrorCode,
}

impl VaultError {
    pub(crate) fn new(code: VaultErrorCode) -> Self {
        Self { code }
    }

    pub fn code(&self) -> &'static str {
        match self.code {
            VaultErrorCode::NotConfigured => "not_configured",
            VaultErrorCode::SecureStoreLocked => "secure_store_locked",
            VaultErrorCode::SecureStoreUnavailable => "secure_store_unavailable",
        }
    }
}

impl fmt::Display for VaultError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self.code {
            VaultErrorCode::NotConfigured => "credential is not configured",
            VaultErrorCode::SecureStoreLocked => "secure credential store is locked",
            VaultErrorCode::SecureStoreUnavailable => "secure credential store is unavailable",
        };
        formatter.write_str(message)
    }
}

impl std::error::Error for VaultError {}
