use std::path::Path;

use deepseek_harness_desktop_lib::updater_signature_verifier::verify_updater_signature;

fn main() {
    if let Err(message) = run() {
        eprintln!("{message}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    if arguments.len() != 2 {
        return Err("usage: verify_updater_signature <artifact> <signature>".into());
    }
    let public_key = std::env::var("TAURI_UPDATER_PUBLIC_KEY")
        .map_err(|_| "TAURI_UPDATER_PUBLIC_KEY is required".to_string())?;
    verify_updater_signature(
        Path::new(&arguments[0]),
        Path::new(&arguments[1]),
        &public_key,
    )
}
