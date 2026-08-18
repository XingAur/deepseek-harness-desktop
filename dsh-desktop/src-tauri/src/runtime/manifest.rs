use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde_json::{Map, Value};

use super::model::{RuntimeFailure, RuntimeFailureCode, RuntimeManifest, RuntimeTarget};
use super::paths::validate_relative_path;

pub const DEV_RELEASE_PUBLIC_KEY_BASE64URL: &str = "cmFlmJvjXIrMN8AbIXxF2c6Gnpt9rDFd_Zhbl0U7AlI";

pub fn release_public_key() -> &'static str {
    option_env!("DSH_DESKTOP_RELEASE_PUBLIC_KEY").unwrap_or(DEV_RELEASE_PUBLIC_KEY_BASE64URL)
}

pub fn parse_and_verify_manifest(
    bytes: &[u8],
    expected_target: RuntimeTarget,
    public_key: &str,
) -> Result<RuntimeManifest, RuntimeFailure> {
    let value: Value = serde_json::from_slice(bytes)
        .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Signature, format!("运行时清单不是有效 JSON：{cause}")))?;
    let signature_text = value.get("signature").and_then(Value::as_str)
        .ok_or_else(|| RuntimeFailure::new(RuntimeFailureCode::Signature, "运行时清单缺少签名"))?;
    let payload = canonical_payload(&value, "signature")?;
    verify_ed25519(&payload, signature_text, public_key)?;

    let manifest: RuntimeManifest = serde_json::from_value(value)
        .map_err(|cause| RuntimeFailure::new(RuntimeFailureCode::Signature, format!("运行时清单字段无效：{cause}")))?;
    validate_manifest(&manifest, expected_target)?;
    Ok(manifest)
}

pub fn canonical_payload(value: &Value, omitted_key: &str) -> Result<Vec<u8>, RuntimeFailure> {
    fn sort(value: &Value, omitted_key: &str, root: bool) -> Value {
        match value {
            Value::Object(object) => {
                let mut keys = object.keys().collect::<Vec<_>>();
                keys.sort_unstable();
                let mut sorted = Map::new();
                for key in keys {
                    if root && key == omitted_key { continue; }
                    sorted.insert(key.clone(), sort(&object[key], omitted_key, false));
                }
                Value::Object(sorted)
            }
            Value::Array(items) => Value::Array(items.iter().map(|item| sort(item, omitted_key, false)).collect()),
            scalar => scalar.clone(),
        }
    }
    serde_json::to_vec(&sort(value, omitted_key, true)).map_err(RuntimeFailure::internal)
}

fn verify_ed25519(payload: &[u8], encoded_signature: &str, encoded_key: &str) -> Result<(), RuntimeFailure> {
    let key_bytes = URL_SAFE_NO_PAD.decode(encoded_key)
        .map_err(|_| RuntimeFailure::new(RuntimeFailureCode::Signature, "运行时公钥编码无效"))?;
    let key_array: [u8; 32] = key_bytes.try_into()
        .map_err(|_| RuntimeFailure::new(RuntimeFailureCode::Signature, "运行时公钥长度无效"))?;
    let key = VerifyingKey::from_bytes(&key_array)
        .map_err(|_| RuntimeFailure::new(RuntimeFailureCode::Signature, "运行时公钥无效"))?;
    let signature_bytes = URL_SAFE_NO_PAD.decode(encoded_signature)
        .map_err(|_| RuntimeFailure::new(RuntimeFailureCode::Signature, "运行时签名编码无效"))?;
    let signature = Signature::from_slice(&signature_bytes)
        .map_err(|_| RuntimeFailure::new(RuntimeFailureCode::Signature, "运行时签名长度无效"))?;
    key.verify(payload, &signature)
        .map_err(|_| RuntimeFailure::new(RuntimeFailureCode::Signature, "运行时清单签名校验失败"))
}

fn validate_manifest(manifest: &RuntimeManifest, expected_target: RuntimeTarget) -> Result<(), RuntimeFailure> {
    if manifest.schema_version != 1 {
        return Err(RuntimeFailure::new(RuntimeFailureCode::Signature, "不支持的运行时清单版本"));
    }
    if manifest.target != expected_target {
        return Err(RuntimeFailure::new(RuntimeFailureCode::Signature, "运行时清单与当前平台不匹配"));
    }
    if manifest.url.scheme() != "https" && manifest.url.scheme() != "file" {
        return Err(RuntimeFailure::new(RuntimeFailureCode::Network, "运行时制品必须使用 HTTPS 或本地开发文件"));
    }
    if manifest.size == 0 || manifest.size > 4 * 1024 * 1024 * 1024 {
        return Err(RuntimeFailure::new(RuntimeFailureCode::Archive, "运行时制品大小无效"));
    }
    if manifest.sha256.len() != 64 || !manifest.sha256.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(RuntimeFailure::new(RuntimeFailureCode::Signature, "运行时 SHA-256 无效"));
    }
    validate_relative_path(&manifest.entrypoint, "entrypoint")?;
    if !manifest.health_path.starts_with('/') || manifest.health_path.contains("..") {
        return Err(RuntimeFailure::new(RuntimeFailureCode::Signature, "健康检查路径无效"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_json_sorts_keys_and_omits_signature() {
        let input = serde_json::json!({"z": 1, "signature": "x", "a": {"c": 2, "b": 1}});
        assert_eq!(canonical_payload(&input, "signature").unwrap(), br#"{"a":{"b":1,"c":2},"z":1}"#);
    }

    #[test]
    fn rejects_unsafe_entrypoint() {
        let manifest = RuntimeManifest {
            schema_version: 1,
            version: semver::Version::new(1, 0, 0),
            dsh_version: "0.1.0-rc.7".parse().unwrap(),
            target: RuntimeTarget::WindowsX86_64,
            url: "https://example.invalid/runtime.zip".parse().unwrap(),
            size: 10,
            sha256: "a".repeat(64),
            signature: String::new(),
            archive: super::super::model::ArchiveKind::Zip,
            entrypoint: "../node.exe".into(),
            args: vec![],
            health_path: "/health".into(),
        };
        assert!(validate_manifest(&manifest, RuntimeTarget::WindowsX86_64).is_err());
    }
}
