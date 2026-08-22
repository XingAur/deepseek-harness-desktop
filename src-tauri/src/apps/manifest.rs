use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::runtime::{RuntimeFailure, paths::validate_relative_path};

pub const MANIFEST_FILE: &str = "dsh-app.json";
const MAX_ARGS: usize = 16;
const MAX_ARG_CHARS: usize = 512;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AppKind {
    Web,
    Static,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AppManifest {
    pub kind: AppKind,
    pub start: Vec<String>,
    pub port_env: String,
    pub health_path: String,
    pub data_dir: PathBuf,
    pub static_dir: Option<PathBuf>,
}

#[derive(Deserialize)]
struct ManifestFile {
    #[serde(rename = "schemaVersion")]
    schema_version: u32,
    #[serde(rename = "type")]
    kind: String,
    #[serde(default)]
    start: Vec<String>,
    #[serde(rename = "portEnv", default)]
    port_env: Option<String>,
    #[serde(rename = "healthPath", default)]
    health_path: Option<String>,
    #[serde(rename = "dataDir", default)]
    data_dir: Option<String>,
    #[serde(rename = "staticDir", default)]
    static_dir: Option<String>,
}

pub fn read_manifest(project_dir: &Path) -> Result<Option<AppManifest>, RuntimeFailure> {
    let path = project_dir.join(MANIFEST_FILE);
    let bytes = match std::fs::read(&path) {
        Ok(bytes) => bytes,
        Err(cause) if cause.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(cause) => {
            return Err(RuntimeFailure::internal(format!(
                "无法读取 dsh-app.json：{cause}"
            )))
        }
    };
    parse_manifest(&bytes).map(Some)
}

pub fn parse_manifest(bytes: &[u8]) -> Result<AppManifest, RuntimeFailure> {
    let invalid = |message: String| RuntimeFailure::internal(format!("dsh-app.json 无效：{message}"));
    let file: ManifestFile = serde_json::from_slice(bytes)
        .map_err(|cause| invalid(format!("JSON 解析失败：{cause}")))?;
    if file.schema_version != 1 {
        return Err(invalid("schemaVersion 仅支持 1".into()));
    }
    let kind = match file.kind.as_str() {
        "web" => AppKind::Web,
        "static" => AppKind::Static,
        other => return Err(invalid(format!("type 仅支持 web/static：{other}"))),
    };
    let start = validate_args(file.start, kind).map_err(invalid)?;
    let port_env = file.port_env.unwrap_or_else(|| "PORT".to_owned());
    if !is_valid_env_name(&port_env) {
        return Err(invalid(format!("portEnv 不是合法环境变量名：{port_env}")));
    }
    let health_path = file.health_path.unwrap_or_else(|| "/".to_owned());
    if !health_path.starts_with('/') || health_path.len() > 256 || health_path.contains('\0') {
        return Err(invalid("healthPath 必须以 / 开头且不超过 256 字符".into()));
    }
    let data_dir = validate_relative_path(
        &file.data_dir.unwrap_or_else(|| "data".to_owned()),
        "dataDir",
    )
    .map_err(|cause| invalid(cause.message))?;
    let static_dir = match kind {
        AppKind::Static => Some(
            validate_relative_path(
                &file.static_dir.unwrap_or_else(|| "dist".to_owned()),
                "staticDir",
            )
            .map_err(|cause| invalid(cause.message))?,
        ),
        AppKind::Web => match file.static_dir {
            Some(_) => return Err(invalid("web 应用不支持 staticDir".into())),
            None => None,
        },
    };
    Ok(AppManifest {
        kind,
        start,
        port_env,
        health_path,
        data_dir,
        static_dir,
    })
}

fn validate_args(start: Vec<String>, kind: AppKind) -> Result<Vec<String>, String> {
    if matches!(kind, AppKind::Static) {
        if !start.is_empty() {
            return Err("static 应用不使用 start".into());
        }
        return Ok(start);
    }
    if start.is_empty() {
        return Err("web 应用必须提供 start".into());
    }
    if start.len() > MAX_ARGS {
        return Err(format!("start 参数过多（上限 {MAX_ARGS}）"));
    }
    for argument in &start {
        if argument.is_empty() || argument.contains('\0') || argument.len() > MAX_ARG_CHARS {
            return Err("start 含空串、NUL 或超长参数".into());
        }
    }
    if !matches!(start[0].as_str(), "node" | "pnpm") {
        return Err(format!("start 首项仅允许 node/pnpm：{}", start[0]));
    }
    Ok(start)
}

fn is_valid_env_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 64
        && name
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '_')
        && name.chars().next().is_some_and(|first| first.is_ascii_alphabetic() || first == '_')
}

#[cfg(test)]
mod tests {
    use super::{AppKind, parse_manifest};

    fn web_json(body: &str) -> String {
        format!("{{\"schemaVersion\":1,\"type\":\"web\",{body}}}")
    }

    #[test]
    fn accepts_minimal_web_manifest_with_defaults() {
        let manifest = parse_manifest(
            web_json("\"start\":[\"pnpm\",\"run\",\"start\"]").as_bytes(),
        )
        .unwrap();
        assert_eq!(manifest.kind, AppKind::Web);
        assert_eq!(manifest.start, vec!["pnpm", "run", "start"]);
        assert_eq!(manifest.port_env, "PORT");
        assert_eq!(manifest.health_path, "/");
        assert_eq!(manifest.data_dir, std::path::Path::new("data"));
    }

    #[test]
    fn accepts_static_manifest() {
        let manifest = parse_manifest(
            b"{\"schemaVersion\":1,\"type\":\"static\",\"staticDir\":\"dist\",\"dataDir\":\"data\"}",
        )
        .unwrap();
        assert_eq!(manifest.kind, AppKind::Static);
        assert_eq!(manifest.static_dir, Some(std::path::Path::new("dist").into()));
    }

    #[test]
    fn rejects_unknown_schema_type_and_command() {
        assert!(parse_manifest(b"{\"schemaVersion\":2,\"type\":\"web\",\"start\":[\"node\",\"x\"]}").is_err());
        assert!(parse_manifest(web_json("\"start\":[\"npm\",\"start\"]").as_bytes()).is_err());
        assert!(parse_manifest(web_json("\"start\":[\"cmd\",\"/c\",\"echo\"]").as_bytes()).is_err());
        assert!(parse_manifest(web_json("\"start\":[]").as_bytes()).is_err());
        assert!(parse_manifest(b"{\"schemaVersion\":1,\"type\":\"api\",\"start\":[\"node\",\"x\"]}").is_err());
    }

    #[test]
    fn rejects_escaping_and_absolute_dirs() {
        // 绝对路径的形式按平台选择：盘符路径仅在 Windows 上是绝对路径。
        let absolute_data_dir = if cfg!(windows) { "C:/tmp" } else { "/tmp" };
        let absolute_static_dir = if cfg!(windows) { "C:/www" } else { "/var/www" };
        assert!(parse_manifest(
            web_json("\"start\":[\"node\",\"a\"],\"dataDir\":\"../out\"").as_bytes()
        )
        .is_err());
        assert!(parse_manifest(
            web_json(&format!(
                "\"start\":[\"node\",\"a\"],\"dataDir\":\"{absolute_data_dir}\""
            ))
            .as_bytes(),
        )
        .is_err());
        assert!(parse_manifest(
            b"{\"schemaVersion\":1,\"type\":\"static\",\"staticDir\":\"..\"}"
        )
        .is_err());
        assert!(parse_manifest(
            format!("{{\"schemaVersion\":1,\"type\":\"static\",\"staticDir\":\"{absolute_static_dir}\"}}").as_bytes(),
        )
        .is_err());
    }

    #[test]
    fn rejects_bad_port_env_and_health_path() {
        assert!(parse_manifest(
            web_json("\"start\":[\"node\",\"a\"],\"portEnv\":\"1BAD\"").as_bytes()
        )
        .is_err());
        assert!(parse_manifest(
            web_json("\"start\":[\"node\",\"a\"],\"healthPath\":\"health\"").as_bytes()
        )
        .is_err());
    }
}
