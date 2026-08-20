#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    let mode = deepseek_harness_desktop_lib::app_mode::ApplicationMode::parse(std::env::args())
        .unwrap_or_else(|cause| {
            eprintln!("{}", cause.message);
            std::process::exit(2);
        });
    deepseek_harness_desktop_lib::run(mode);
}
