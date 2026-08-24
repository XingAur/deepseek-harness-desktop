use std::path::{Component, Path};

use regex::Regex;
use serde_json::Value;
use sha2::{Digest, Sha256};

use super::model::ExtensionManifest;

pub const MANIFEST_SCHEMA_VERSION: u32 = 1;

pub fn parse_manifest(bytes: &[u8]) -> Result<ExtensionManifest, String> {
    let value: Value = serde_json::from_slice(bytes).map_err(|_| "扩展清单不是有效 JSON".to_owned())?;
    let manifest: ExtensionManifest = serde_json::from_value(value)
        .map_err(|_| "扩展清单字段无效或包含未知字段".to_owned())?;
    validate_manifest(&manifest)?;
    Ok(manifest)
}

pub fn validate_manifest(manifest: &ExtensionManifest) -> Result<(), String> {
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION {
        return Err("不支持的扩展清单版本".to_owned());
    }
    let id = Regex::new(r"^[a-z0-9][a-z0-9._-]{0,127}$").expect("static extension id regex");
    if !id.is_match(&manifest.id) {
        return Err("扩展 ID 无效".to_owned());
    }
    if manifest.protocol_range.trim().is_empty() || manifest.protocol_range.len() > 128 {
        return Err("扩展协议范围无效".to_owned());
    }
    if manifest.integrity_sha256.len() != 64
        || !manifest.integrity_sha256.bytes().all(|byte| byte.is_ascii_hexdigit())
    {
        return Err("扩展完整性校验值无效".to_owned());
    }
    validate_relative_entrypoint(&manifest.entrypoint)?;
    if manifest.platforms.is_empty()
        || manifest.platforms.iter().any(|platform| {
            !matches!(platform.as_str(), "darwin" | "win32" | "linux" | "any")
        })
    {
        return Err("扩展平台声明无效".to_owned());
    }
    if manifest.health_check.kind.trim().is_empty()
        || !(1..=60_000).contains(&manifest.health_check.timeout_ms)
    {
        return Err("扩展健康检查无效".to_owned());
    }
    if manifest.update.exact_version != manifest.version.to_string() {
        return Err("扩展更新版本必须是精确版本".to_owned());
    }
    if manifest.rollback.quarantine_on_crash_loop == false {
        return Err("扩展必须启用崩溃循环隔离".to_owned());
    }
    if manifest
        .capabilities
        .iter()
        .any(|capability| capability.trim().is_empty() || capability.len() > 128)
    {
        return Err("扩展能力声明无效".to_owned());
    }
    if manifest
        .credential_references
        .iter()
        .any(|reference| !safe_identifier(reference))
    {
        return Err("扩展凭证引用无效".to_owned());
    }
    Ok(())
}

pub fn validate_relative_entrypoint(value: &str) -> Result<(), String> {
    let path = Path::new(value);
    if value.is_empty()
        || path.is_absolute()
        || path.components().any(|component| {
            matches!(component, Component::ParentDir | Component::Prefix(_) | Component::RootDir)
        })
    {
        return Err("扩展入口必须是受限相对路径".to_owned());
    }
    Ok(())
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn safe_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-') && index > 0
        })
}

#[cfg(test)]
mod tests {
    use super::{parse_manifest, sha256_hex};

    pub(crate) fn manifest() -> String {
        format!(
            r#"{{
              "schemaVersion":1,"id":"demo.skill","kind":"skill","source":"builtin",
              "version":"1.2.3","protocolRange":"dsh-agent/v1","integritySha256":"{}",
              "entrypoint":"skill.md","platforms":["any"],"capabilities":[],"credentialReferences":[],
              "healthCheck":{{"kind":"static","timeoutMs":1000}},
              "update":{{"source":"builtin","exactVersion":"1.2.3"}},
              "rollback":{{"lastKnownGoodVersion":"1.2.3","quarantineOnCrashLoop":true}}
            }}"#,
            "a".repeat(64)
        )
    }

    #[test]
    fn parses_a_strict_versioned_manifest() {
        let parsed = parse_manifest(manifest().as_bytes()).unwrap();
        assert_eq!(parsed.id, "demo.skill");
        assert_eq!(sha256_hex(b"demo").len(), 64);
    }

    #[test]
    fn rejects_unknown_fields_and_unsafe_entrypoints() {
        assert!(parse_manifest(manifest().replace("\"skill.md\"", "\"../run.sh\"").as_bytes()).is_err());
        let unknown = manifest().replace("\"schemaVersion\":1", "\"unknown\":true,\"schemaVersion\":1");
        assert!(parse_manifest(unknown.as_bytes()).is_err());
    }
}

#[cfg(test)]
pub(crate) fn test_manifest_json() -> String {
    format!(
        r#"{{
          "schemaVersion":1,"id":"demo.skill","kind":"skill","source":"builtin",
          "version":"1.2.3","protocolRange":"dsh-agent/v1","integritySha256":"{}",
          "entrypoint":"skill.md","platforms":["any"],"capabilities":[],"credentialReferences":[],
          "healthCheck":{{"kind":"static","timeoutMs":1000}},
          "update":{{"source":"builtin","exactVersion":"1.2.3"}},
          "rollback":{{"lastKnownGoodVersion":"1.2.3","quarantineOnCrashLoop":true}}
        }}"#,
        "a".repeat(64)
    )
}
