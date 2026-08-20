use crate::runtime::model::{RuntimeFailure, RuntimeFailureCode};

pub fn install() -> Result<(), RuntimeFailure> {
    Err(RuntimeFailure::new(
        RuntimeFailureCode::Internal,
        "捆绑 Runtime 安装尚未初始化",
    ))
}
