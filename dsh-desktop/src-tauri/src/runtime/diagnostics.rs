use std::{fs::{self, File}, io::Write, path::{Path, PathBuf}};

use serde_json::json;
use zip::write::SimpleFileOptions;

use super::{model::RuntimeFailure, paths::RuntimePaths};

pub fn export(paths: &RuntimePaths) -> Result<PathBuf, RuntimeFailure> {
    fs::create_dir_all(&paths.diagnostics).map_err(RuntimeFailure::internal)?;
    let output = paths.diagnostics.join(format!("dsh-diagnostics-{}.zip", chrono::Utc::now().format("%Y%m%d-%H%M%S")));
    let file = File::create(&output).map_err(RuntimeFailure::internal)?;
    let mut archive = zip::ZipWriter::new(file);
    let options = SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    let current = fs::read_to_string(&paths.current).ok().and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok());
    let summary = json!({
        "generatedAt": chrono::Utc::now().to_rfc3339(),
        "platform": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "runtime": current,
        "note": "Conversation content, source code, environment variables and credentials are excluded."
    });
    archive.start_file("diagnostics.json", options).map_err(RuntimeFailure::internal)?;
    archive.write_all(serde_json::to_string_pretty(&summary).map_err(RuntimeFailure::internal)?.as_bytes())
        .map_err(RuntimeFailure::internal)?;
    append_recent_logs(&mut archive, &paths.logs, options)?;
    archive.finish().map_err(RuntimeFailure::internal)?;
    Ok(output)
}

fn append_recent_logs(
    archive: &mut zip::ZipWriter<File>,
    logs: &Path,
    options: SimpleFileOptions,
) -> Result<(), RuntimeFailure> {
    let mut files = fs::read_dir(logs).map_err(RuntimeFailure::internal)?
        .filter_map(Result::ok)
        .filter(|entry| entry.path().extension().and_then(|value| value.to_str()) == Some("log"))
        .collect::<Vec<_>>();
    files.sort_by_key(|entry| entry.metadata().and_then(|meta| meta.modified()).ok());
    for entry in files.into_iter().rev().take(3) {
        let name = entry.file_name().to_string_lossy().to_string();
        let bytes = fs::read(entry.path()).map_err(RuntimeFailure::internal)?;
        let start = bytes.len().saturating_sub(512 * 1024);
        let text = String::from_utf8_lossy(&bytes[start..]);
        archive.start_file(format!("logs/{name}"), options).map_err(RuntimeFailure::internal)?;
        archive.write_all(redact(&text).as_bytes()).map_err(RuntimeFailure::internal)?;
    }
    Ok(())
}

fn redact(input: &str) -> String {
    let pattern = regex::Regex::new(r"(?i)(api[_-]?key|authorization|bearer|session[_-]?token)(\s*[:=]\s*|\s+)[^\s,;]+")
        .expect("static diagnostic regex");
    pattern.replace_all(input, "$1$2[REDACTED]").into_owned()
}
