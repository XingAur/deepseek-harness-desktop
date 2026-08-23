use std::path::Path;

use base64::{Engine as _, engine::general_purpose::STANDARD};
use minisign_verify::{PublicKey, Signature};

pub fn verify_updater_signature(
    archive_path: &Path,
    signature_path: &Path,
    encoded_public_key: &str,
) -> Result<(), String> {
    let public_key_text = decode_envelope(encoded_public_key, "updater public key")?;
    let public_key = PublicKey::decode(&public_key_text)
        .map_err(|cause| format!("updater public key is invalid: {cause}"))?;
    let encoded_signature = std::fs::read_to_string(signature_path)
        .map_err(|cause| format!("unable to read updater signature: {cause}"))?;
    let signature_text = decode_envelope(&encoded_signature, "updater signature")?;
    let signature = Signature::decode(&signature_text)
        .map_err(|cause| format!("updater signature is invalid: {cause}"))?;
    let archive = std::fs::read(archive_path)
        .map_err(|cause| format!("unable to read updater archive: {cause}"))?;
    public_key
        .verify(&archive, &signature, true)
        .map_err(|cause| {
            format!("updater signature does not match the configured public key: {cause}")
        })
}

fn decode_envelope(value: &str, label: &str) -> Result<String, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() || trimmed.lines().count() != 1 {
        return Err(format!(
            "{label} must be a non-empty single-line base64 value"
        ));
    }
    let decoded = STANDARD
        .decode(trimmed)
        .map_err(|cause| format!("{label} is not valid base64: {cause}"))?;
    String::from_utf8(decoded).map_err(|cause| format!("{label} is not UTF-8: {cause}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    const PUBLIC_KEY: &str = "untrusted comment: minisign public key E7620F1842B4E81F\nRWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3";
    const SIGNATURE: &str = "untrusted comment: signature from minisign secret key\nRWQf6LRCGA9i59SLOFxz6NxvASXDJeRtuZykwQepbDEGt87ig1BNpWaVWuNrm73YiIiJbq71Wi+dP9eKL8OC351vwIasSSbXxwA=\ntrusted comment: timestamp:1555779966\tfile:test\nQtKMXWyYcwdpZAlPF7tE2ENJkRd1ujvKjlj1m9RtHTBnZPa5WKU5uWRs5GoP5M/VqE81QFuMKI5k/SfNQUaOAA==";

    #[test]
    fn accepts_the_same_base64_envelopes_used_by_the_tauri_updater() {
        let root = tempdir().unwrap();
        let archive = root.path().join("update.zip");
        let signature = root.path().join("update.zip.sig");
        std::fs::write(&archive, b"test").unwrap();
        std::fs::write(&signature, STANDARD.encode(SIGNATURE)).unwrap();

        verify_updater_signature(&archive, &signature, &STANDARD.encode(PUBLIC_KEY)).unwrap();
    }

    #[test]
    fn rejects_tampered_archives_and_malformed_envelopes() {
        let root = tempdir().unwrap();
        let archive = root.path().join("update.zip");
        let signature = root.path().join("update.zip.sig");
        std::fs::write(&archive, b"tampered").unwrap();
        std::fs::write(&signature, STANDARD.encode(SIGNATURE)).unwrap();

        assert!(
            verify_updater_signature(&archive, &signature, &STANDARD.encode(PUBLIC_KEY))
                .unwrap_err()
                .contains("does not match")
        );
        assert!(verify_updater_signature(&archive, &signature, "not base64").is_err());
    }
}
