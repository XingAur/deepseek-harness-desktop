use std::{path::{Path, PathBuf}, time::Duration};

use reqwest::redirect::{Attempt, Policy};
use semver::Version;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use url::Url;

use super::model::{AppUpdateFailure, AppUpdateMode, UpdateInfo};

pub const PRODUCTION_MANIFEST_ENDPOINT: &str = "https://github.com/XingAur/deepseek-harness-desktop/releases/latest/download/desktop-release.json";
const REPOSITORY: &str = "XingAur/deepseek-harness-desktop";
const MAX_MANIFEST_BYTES: u64 = 1024 * 1024;
const MANIFEST_FETCH_ATTEMPTS: usize = 2;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DesktopReleaseManifest {
    schema_version: u8,
    version: String,
    tag: String,
    published_at: String,
    notes: String,
    release_page_url: String,
    platforms: Platforms,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Platforms {
    #[serde(rename = "windows-x86_64")]
    windows_x86_64: WindowsRelease,
    #[serde(rename = "darwin-aarch64")]
    darwin_aarch64: ManualDmgRelease,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct WindowsRelease {
    mode: AppUpdateMode,
    url: String,
    signature_url: String,
    sha256: String,
    size: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ManualDmgRelease {
    mode: AppUpdateMode,
    url: String,
    sha256: String,
    size: u64,
    developer_id_signed: bool,
    notarized: bool,
}

pub async fn fetch_manual_update() -> Result<Option<UpdateInfo>, AppUpdateFailure> {
    let endpoint = manifest_endpoint()?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        // GitHub 的 latest/download 会经过 CDN 重定向；部分代理对 HTTP/2
        // 响应流处理不完整，reqwest 会因此报 "error decoding response body"。
        .http1_only()
        .redirect(Policy::custom(restrict_redirect))
        .build()
        .map_err(|cause| failure("configuration", cause))?;
    let mut last_failure = None;
    for attempt in 0..MANIFEST_FETCH_ATTEMPTS {
        match fetch_manifest_bytes(&client, &endpoint).await {
            Ok(bytes) => {
                let json = std::str::from_utf8(&bytes).map_err(|cause| failure("manifest", cause))?;
                return manual_update_from_json(json, env!("CARGO_PKG_VERSION"), std::env::consts::ARCH);
            }
            Err(error) if error.code == "check" && attempt + 1 < MANIFEST_FETCH_ATTEMPTS => {
                last_failure = Some(error);
                tokio::time::sleep(Duration::from_millis(250)).await;
            }
            Err(error) => return Err(error),
        }
    }
    Err(last_failure.unwrap_or_else(|| AppUpdateFailure::new("check", "macOS 更新清单请求失败")))
}

async fn fetch_manifest_bytes(
    client: &reqwest::Client,
    endpoint: &Url,
) -> Result<Vec<u8>, AppUpdateFailure> {
    let response = client
        .get(endpoint.clone())
        .header(reqwest::header::ACCEPT, "application/json")
        .header(reqwest::header::CACHE_CONTROL, "no-cache")
        .send()
        .await
        .map_err(|cause| failure("check", cause))?;
    if !response.status().is_success() {
        return Err(AppUpdateFailure::new(
            "check",
            format!("macOS 更新清单请求失败: HTTP {}", response.status()),
        ));
    }
    if response
        .content_length()
        .is_some_and(|size| size > MAX_MANIFEST_BYTES)
    {
        return Err(AppUpdateFailure::new(
            "manifest",
            "macOS 更新清单超过 1 MB 限制",
        ));
    }
    let bytes = response
        .bytes()
        .await
        .map_err(|cause| failure("check", cause))?;
    if bytes.len() > MAX_MANIFEST_BYTES as usize {
        return Err(AppUpdateFailure::new(
            "manifest",
            "macOS 更新清单超过 1 MB 限制",
        ));
    }
    Ok(bytes.to_vec())
}

pub fn manual_update_from_json(
    json: &str,
    current_version: &str,
    architecture: &str,
) -> Result<Option<UpdateInfo>, AppUpdateFailure> {
    if architecture != "aarch64" {
        return Ok(None);
    }
    let current = Version::parse(current_version).map_err(|cause| failure("version", cause))?;
    let manifest: DesktopReleaseManifest =
        serde_json::from_str(json).map_err(|cause| failure("manifest", cause))?;
    if manifest.schema_version != 1 {
        return Err(AppUpdateFailure::new(
            "manifest",
            "desktop release schemaVersion 必须是 1",
        ));
    }
    let version = Version::parse(&manifest.version).map_err(|cause| failure("manifest", cause))?;
    if !version.pre.is_empty() || !version.build.is_empty() {
        return Err(AppUpdateFailure::new(
            "manifest",
            "桌面更新版本必须是稳定 SemVer",
        ));
    }
    if version <= current {
        return Ok(None);
    }
    let expected_tag = format!("desktop-v{}", manifest.version);
    if manifest.tag != expected_tag {
        return Err(AppUpdateFailure::new(
            "manifest",
            "桌面更新 tag 与 version 不一致",
        ));
    }
    chrono::DateTime::parse_from_rfc3339(&manifest.published_at)
        .map_err(|cause| failure("manifest", cause))?;
    let expected_release_page =
        format!("https://github.com/{REPOSITORY}/releases/tag/{expected_tag}");
    validate_exact_github_url(
        &manifest.release_page_url,
        &expected_release_page,
        "Release 页面",
    )?;

    let windows = &manifest.platforms.windows_x86_64;
    if windows.mode != AppUpdateMode::InApp
        || windows.size == 0
        || !valid_sha256(&windows.sha256)
        || windows.url.trim().is_empty()
        || windows.signature_url.trim().is_empty()
    {
        return Err(AppUpdateFailure::new("manifest", "Windows 更新元数据无效"));
    }

    let dmg = manifest.platforms.darwin_aarch64;
    if dmg.mode != AppUpdateMode::ManualDmg {
        return Err(AppUpdateFailure::new(
            "manifest",
            "macOS 更新模式必须是 manual-dmg",
        ));
    }
    if dmg.size == 0 || !valid_sha256(&dmg.sha256) {
        return Err(AppUpdateFailure::new(
            "manifest",
            "macOS DMG 大小或 SHA-256 无效",
        ));
    }
    if dmg.developer_id_signed || dmg.notarized {
        return Err(AppUpdateFailure::new(
            "manifest",
            "当前免费发布通道只接受明确标注为未签名且未公证的 DMG",
        ));
    }
    let download_url = validate_dmg_url(&dmg.url, &expected_tag, &manifest.version)?;
    Ok(Some(UpdateInfo {
        version: manifest.version,
        notes: Some(manifest.notes).filter(|notes| !notes.trim().is_empty()),
        size: Some(dmg.size),
        sha256: Some(dmg.sha256),
        mode: AppUpdateMode::ManualDmg,
        download_url: Some(download_url.to_string()),
        developer_id_signed: Some(false),
        notarized: Some(false),
    }))
}

/// 下载并校验已在更新清单中验证过的 macOS DMG。macOS 当前发布物仍是
/// 手动替换安装，因此这里先落到应用数据目录，再交给 Finder 打开，避免
/// 把未经校验的网络响应直接交给系统。
pub async fn download_manual_dmg(
    url: &Url,
    expected_sha256: &str,
    expected_size: u64,
    target_dir: &Path,
) -> Result<PathBuf, AppUpdateFailure> {
    if !valid_sha256(expected_sha256) || expected_size == 0 {
        return Err(AppUpdateFailure::new("manifest", "macOS DMG 校验信息无效"));
    }
    let filename = url
        .path_segments()
        .and_then(|segments| segments.last())
        .filter(|value| !value.is_empty() && !value.contains('/') && value.ends_with(".dmg"))
        .ok_or_else(|| AppUpdateFailure::new("manifest", "macOS DMG 文件名无效"))?;
    let target = target_dir.join(filename);
    let temporary = target.with_extension("dmg.part");
    tokio::fs::create_dir_all(target_dir)
        .await
        .map_err(|cause| failure("download-file", cause))?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(30 * 60))
        .http1_only()
        .redirect(Policy::custom(restrict_redirect))
        .build()
        .map_err(|cause| failure("configuration", cause))?;
    tokio::fs::create_dir_all(target_dir)
        .await
        .map_err(|cause| failure("download-file", cause))?;
    let result = async {
        let response = client
            .get(url.clone())
            .header(reqwest::header::ACCEPT, "application/octet-stream")
            .send()
            .await
            .map_err(|cause| failure("download-network", cause))?;
        if !response.status().is_success() {
            return Err(AppUpdateFailure::new(
                "download-http",
                format!("macOS DMG 下载失败: HTTP {}", response.status()),
            ));
        }
        if response.content_length().is_some_and(|size| size > expected_size) {
            return Err(AppUpdateFailure::new("download-size", "macOS DMG 超过清单声明大小"));
        }
        let mut file = tokio::fs::File::create(&temporary)
            .await
            .map_err(|cause| failure("download-file", cause))?;
        let mut hasher = Sha256::new();
        let mut size = 0_u64;
        let mut response = response;
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|cause| failure("download-network", cause))?
        {
            size = size.saturating_add(chunk.len() as u64);
            if size > expected_size {
                return Err(AppUpdateFailure::new("download-size", "macOS DMG 超过清单声明大小"));
            }
            hasher.update(&chunk);
            tokio::io::AsyncWriteExt::write_all(&mut file, &chunk)
                .await
                .map_err(|cause| failure("download-file", cause))?;
        }
        tokio::io::AsyncWriteExt::flush(&mut file)
            .await
            .map_err(|cause| failure("download-file", cause))?;
        if size != expected_size {
            return Err(AppUpdateFailure::new(
                "download-size",
                format!("macOS DMG 大小校验失败（收到 {size} 字节，清单声明 {expected_size} 字节）"),
            ));
        }
        let actual = hex::encode(hasher.finalize());
        if !actual.eq_ignore_ascii_case(expected_sha256) {
            return Err(AppUpdateFailure::new(
                "download-integrity",
                "macOS DMG SHA-256 校验失败，已阻止打开",
            ));
        }
        tokio::fs::rename(&temporary, &target)
            .await
            .map_err(|cause| failure("download-file", cause))?;
        Ok(target)
    }
    .await;
    if result.is_err() {
        let _ = tokio::fs::remove_file(&temporary).await;
    }
    result
}

fn validate_dmg_url(url: &str, tag: &str, version: &str) -> Result<Url, AppUpdateFailure> {
    let parsed = parse_trusted_url(url, "DMG 下载地址")?;
    let prefix = format!("/{REPOSITORY}/releases/download/{tag}/");
    if !parsed.path().starts_with(&prefix) {
        return Err(AppUpdateFailure::new(
            "manifest",
            "DMG 下载地址仓库或 tag 不匹配",
        ));
    }
    let filename = parsed.path().strip_prefix(&prefix).unwrap_or_default();
    if filename.is_empty()
        || filename.contains('/')
        || filename.contains("..")
        || filename.to_ascii_lowercase().contains("%2f")
        || filename.to_ascii_lowercase().contains("%5c")
        || !filename.ends_with(&format!("_{version}_aarch64.dmg"))
    {
        return Err(AppUpdateFailure::new(
            "manifest",
            "DMG 文件名与版本或架构不匹配",
        ));
    }
    Ok(parsed)
}

fn validate_exact_github_url(
    actual: &str,
    expected: &str,
    label: &str,
) -> Result<(), AppUpdateFailure> {
    let parsed = parse_trusted_url(actual, label)?;
    if parsed.as_str() != expected {
        return Err(AppUpdateFailure::new("manifest", format!("{label}不匹配")));
    }
    Ok(())
}

fn parse_trusted_url(value: &str, label: &str) -> Result<Url, AppUpdateFailure> {
    let url = Url::parse(value).map_err(|cause| failure("manifest", cause))?;
    if url.scheme() != "https"
        || url.host_str() != Some("github.com")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(AppUpdateFailure::new(
            "manifest",
            format!("{label}不是受信任的 GitHub HTTPS 地址"),
        ));
    }
    Ok(url)
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn restrict_redirect(attempt: Attempt<'_>) -> reqwest::redirect::Action {
    if attempt.previous().len() >= 5 || !is_trusted_redirect(attempt.url()) {
        attempt.stop()
    } else {
        attempt.follow()
    }
}

pub(crate) fn is_trusted_redirect(url: &Url) -> bool {
    url.scheme() == "https"
        && url.port().is_none()
        && url.username().is_empty()
        && url.password().is_none()
        && matches!(
            url.host_str(),
            Some(
                "github.com"
                    | "objects.githubusercontent.com"
                    | "release-assets.githubusercontent.com"
            )
        )
}

fn manifest_endpoint() -> Result<Url, AppUpdateFailure> {
    #[cfg(feature = "e2e")]
    if let Ok(value) = std::env::var("DSH_DESKTOP_E2E_APP_UPDATE_MANIFEST_URL") {
        let url = Url::parse(&value).map_err(|cause| failure("configuration", cause))?;
        if matches!(url.host_str(), Some("127.0.0.1" | "localhost"))
            && matches!(url.scheme(), "http" | "https")
            && url.username().is_empty()
            && url.password().is_none()
            && url.query().is_none()
            && url.fragment().is_none()
        {
            return Ok(url);
        }
        return Err(AppUpdateFailure::new(
            "configuration",
            "e2e 应用更新清单只能使用无凭证的 loopback 地址",
        ));
    }
    Url::parse(PRODUCTION_MANIFEST_ENDPOINT).map_err(|cause| failure("configuration", cause))
}

fn failure(code: &str, cause: impl std::fmt::Display) -> AppUpdateFailure {
    AppUpdateFailure::new(code, cause.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app_update::model::AppUpdateMode;

    #[test]
    fn accepts_a_newer_exact_apple_silicon_dmg() {
        let update = manual_update_from_json(&valid_manifest("0.1.13"), "0.1.12", "aarch64")
            .unwrap()
            .unwrap();
        assert_eq!(update.mode, AppUpdateMode::ManualDmg);
        assert!(update.download_url.as_deref().unwrap().ends_with(".dmg"));
        assert_eq!(update.developer_id_signed, Some(false));
        assert_eq!(update.notarized, Some(false));
        assert_eq!(update.sha256, Some("a".repeat(64)));
    }

    #[test]
    fn equal_or_older_versions_and_other_architectures_are_noop() {
        assert!(
            manual_update_from_json(&valid_manifest("0.1.12"), "0.1.12", "aarch64")
                .unwrap()
                .is_none()
        );
        assert!(
            manual_update_from_json(&valid_manifest("0.1.11"), "0.1.12", "aarch64")
                .unwrap()
                .is_none()
        );
        assert!(
            manual_update_from_json(&valid_manifest("0.1.13"), "0.1.12", "x86_64")
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn rejects_malformed_or_untrusted_manifest_fields() {
        let cases = [
            valid_manifest("latest"),
            valid_manifest("0.1.13").replace("desktop-v0.1.13", "desktop-v0.1.14"),
            valid_manifest("0.1.13").replace("https://github.com", "http://github.com"),
            valid_manifest("0.1.13").replace("https://github.com/", "https://attacker@github.com/"),
            valid_manifest("0.1.13")
                .replace("XingAur/deepseek-harness-desktop", "attacker/example"),
            valid_manifest("0.1.13").replace(".dmg\"", ".dmg?download=1\""),
            valid_manifest("0.1.13").replace(".dmg\"", ".dmg#fragment\""),
            valid_manifest("0.1.13").replace("manual-dmg", "in-app"),
            valid_manifest("0.1.13").replace(&"a".repeat(64), "ABC"),
            valid_manifest("0.1.13").replace("_0.1.13_aarch64.dmg", "_0.1.12_aarch64.dmg"),
        ];
        for manifest in cases {
            assert!(
                manual_update_from_json(&manifest, "0.1.12", "aarch64").is_err(),
                "{manifest}"
            );
        }
    }

    #[test]
    fn redirect_policy_accepts_only_credential_free_github_https_hosts() {
        for url in [
            "https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v0.1.13/desktop-release.json",
            "https://objects.githubusercontent.com/example",
            "https://release-assets.githubusercontent.com/example",
        ] {
            assert!(is_trusted_redirect(&url.parse().unwrap()));
        }
        for url in [
            "http://github.com/example",
            "https://attacker@github.com/example",
            "https://github.example.com/example",
            "https://example.com/example",
        ] {
            assert!(!is_trusted_redirect(&url.parse().unwrap()));
        }
    }

    fn valid_manifest(version: &str) -> String {
        format!(
            r#"{{
          "schemaVersion": 1,
          "version": "{version}",
          "tag": "desktop-v{version}",
          "publishedAt": "2026-08-23T08:30:00.000Z",
          "notes": "同步新版 DeepSeek Harness",
          "releasePageUrl": "https://github.com/XingAur/deepseek-harness-desktop/releases/tag/desktop-v{version}",
          "platforms": {{
            "windows-x86_64": {{
              "mode": "in-app",
              "url": "https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v{version}/DeepSeek.Harness.Desktop_{version}_x64-setup.exe",
              "signatureUrl": "https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v{version}/DeepSeek.Harness.Desktop_{version}_x64-setup.exe.sig",
              "sha256": "{sha}",
              "size": 7
            }},
            "darwin-aarch64": {{
              "mode": "manual-dmg",
              "url": "https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v{version}/DeepSeek.Harness.Desktop_{version}_aarch64.dmg",
              "sha256": "{sha}",
              "size": 3,
              "developerIdSigned": false,
              "notarized": false
            }}
          }}
        }}"#,
            sha = "a".repeat(64)
        )
    }
}
