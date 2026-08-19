use std::{
    fs::{self, File},
    io::Write,
    path::{Path, PathBuf},
};

use serde_json::json;
use zip::write::SimpleFileOptions;

use super::{
    model::{RuntimeDiagnosticSnapshot, RuntimeFailure},
    paths::RuntimePaths,
    redaction::redact_secrets,
};

pub fn export(
    paths: &RuntimePaths,
    snapshot: &RuntimeDiagnosticSnapshot,
) -> Result<PathBuf, RuntimeFailure> {
    fs::create_dir_all(&paths.diagnostics).map_err(RuntimeFailure::internal)?;
    let output = paths.diagnostics.join(format!(
        "dsh-diagnostics-{}.zip",
        chrono::Utc::now().format("%Y%m%d-%H%M%S")
    ));
    let file = File::create(&output).map_err(RuntimeFailure::internal)?;
    let mut archive = zip::ZipWriter::new(file);
    let options = SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    let current = fs::read_to_string(&paths.current)
        .ok()
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok());
    let summary = json!({
        "generatedAt": chrono::Utc::now().to_rfc3339(),
        "platform": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "runtime": current,
        "attempt": snapshot,
        "note": "Conversation content, source code, environment variables and credentials are excluded."
    });
    archive
        .start_file("diagnostics.json", options)
        .map_err(RuntimeFailure::internal)?;
    archive
        .write_all(
            serde_json::to_string_pretty(&summary)
                .map_err(RuntimeFailure::internal)?
                .as_bytes(),
        )
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
    let mut files = fs::read_dir(logs)
        .map_err(RuntimeFailure::internal)?
        .filter_map(Result::ok)
        .filter(|entry| entry.path().extension().and_then(|value| value.to_str()) == Some("log"))
        .collect::<Vec<_>>();
    files.sort_by_key(|entry| entry.metadata().and_then(|meta| meta.modified()).ok());
    for entry in files.into_iter().rev().take(3) {
        let name = entry.file_name().to_string_lossy().to_string();
        let bytes = fs::read(entry.path()).map_err(RuntimeFailure::internal)?;
        let start = bytes.len().saturating_sub(512 * 1024);
        let text = String::from_utf8_lossy(&bytes[start..]);
        archive
            .start_file(format!("logs/{name}"), options)
            .map_err(RuntimeFailure::internal)?;
        archive
            .write_all(redact_secrets(&text).as_bytes())
            .map_err(RuntimeFailure::internal)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::io::Read;

    use semver::Version;

    use super::*;
    use crate::runtime::model::{
        RuntimeDiagnosticSnapshot, RuntimeFailureCode, RuntimePhase, RuntimeTarget,
    };

    #[test]
    fn exports_failed_runtime_metadata_and_redacts_logs() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().to_path_buf();
        let logs = root.join("logs");
        let diagnostics = root.join("diagnostics");
        fs::create_dir_all(&logs).unwrap();
        fs::create_dir_all(&diagnostics).unwrap();
        fs::write(
            logs.join("dsh-2026-08-18.log"),
            "Authorization: Bearer abc123\nsessionToken=secret child exited with code 7",
        )
        .unwrap();
        let paths = RuntimePaths {
            versions: root.join("runtime/versions"),
            downloads: root.join("runtime/downloads"),
            current: root.join("runtime/current.json"),
            bundled_runtime: root.join("bundled-runtime"),
            logs,
            diagnostics,
            root,
        };
        let snapshot = RuntimeDiagnosticSnapshot {
            operation_id: Some("operation-1".into()),
            runtime_version: Some(Version::parse("0.1.0").unwrap()),
            target: Some(RuntimeTarget::WindowsX86_64),
            phase: RuntimePhase::Failed,
            failure_phase: Some(RuntimePhase::Starting),
            failure: Some(RuntimeFailure::new(RuntimeFailureCode::Process, "退出码 7")),
            exit_code: Some(7),
            log_file: Some("dsh-2026-08-18.log".into()),
        };

        let output = export(&paths, &snapshot).unwrap();
        let mut archive = zip::ZipArchive::new(File::open(output).unwrap()).unwrap();
        let mut summary = String::new();
        archive
            .by_name("diagnostics.json")
            .unwrap()
            .read_to_string(&mut summary)
            .unwrap();
        assert!(summary.contains("0.1.0"));
        assert!(summary.contains("windows-x86_64"));
        assert!(summary.contains("starting"));
        assert!(summary.contains("\"failurePhase\": \"starting\""));
        assert!(summary.contains("process"));
        assert!(summary.contains("\"exitCode\": 7"));
        assert!(summary.contains("dsh-2026-08-18.log"));

        let mut log = String::new();
        archive
            .by_name("logs/dsh-2026-08-18.log")
            .unwrap()
            .read_to_string(&mut log)
            .unwrap();
        assert!(!log.contains("secret"));
        assert!(!log.contains("abc123"));
        assert!(log.contains("Authorization: [REDACTED]"));
        assert!(log.contains("sessionToken=[REDACTED]"));
    }
}
