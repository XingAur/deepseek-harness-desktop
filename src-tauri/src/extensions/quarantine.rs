#[derive(Clone, Debug, PartialEq, Eq)]
pub struct QuarantineReason {
    pub code: &'static str,
    pub message: String,
}

pub fn crash_loop(reason: impl Into<String>) -> QuarantineReason {
    QuarantineReason { code: "crash-loop", message: reason.into() }
}
