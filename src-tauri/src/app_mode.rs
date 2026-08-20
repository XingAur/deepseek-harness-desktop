use uuid::Uuid;

use crate::runtime::model::RuntimeFailure;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ApplicationMode {
    Desktop,
    InstallBundledRuntime,
    PrepareDataCleanup,
    CleanupPending(Uuid),
}

impl ApplicationMode {
    pub fn parse<I, S>(arguments: I) -> Result<Self, RuntimeFailure>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let mut values = arguments.into_iter().map(Into::into);
        let _executable = values.next();
        let Some(flag) = values.next() else {
            return Ok(Self::Desktop);
        };

        let mode = match flag.as_str() {
            "--install-bundled-runtime" => Self::InstallBundledRuntime,
            "--cleanup-app-data" => Self::PrepareDataCleanup,
            "--cleanup-pending" => {
                let nonce = values
                    .next()
                    .ok_or_else(|| RuntimeFailure::internal("缺少清理任务标识"))?;
                Self::CleanupPending(
                    Uuid::parse_str(&nonce)
                        .map_err(|_| RuntimeFailure::internal("清理任务标识无效"))?,
                )
            }
            _ => return Err(RuntimeFailure::internal("不支持的应用启动参数")),
        };

        if values.next().is_some() {
            return Err(RuntimeFailure::internal("应用启动参数过多"));
        }
        Ok(mode)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_only_fixed_cleanup_modes_without_paths() {
        assert!(matches!(
            ApplicationMode::parse(["app.exe"]),
            Ok(ApplicationMode::Desktop)
        ));
        assert!(matches!(
            ApplicationMode::parse(["app.exe", "--cleanup-app-data"]),
            Ok(ApplicationMode::PrepareDataCleanup)
        ));
        assert!(matches!(
            ApplicationMode::parse([
                "app.exe",
                "--cleanup-pending",
                "4b8bbca3-fd7f-4c6d-9111-2d955457047a",
            ]),
            Ok(ApplicationMode::CleanupPending(_))
        ));
        assert!(matches!(
            ApplicationMode::parse(["app.exe", "--install-bundled-runtime"]),
            Ok(ApplicationMode::InstallBundledRuntime)
        ));
        assert!(ApplicationMode::parse(["app.exe", "--cleanup-pending", "C:\\Users"]).is_err());
        assert!(ApplicationMode::parse(["app.exe", "--cleanup-pending"]).is_err());
        assert!(ApplicationMode::parse(["app.exe", "--cleanup-app-data", "extra"]).is_err());
        assert!(ApplicationMode::parse([
            "app.exe",
            "--install-bundled-runtime",
            "C:\\Users\\someone\\runtime.zip",
        ])
        .is_err());
        assert!(ApplicationMode::parse(["app.exe", "--provision-runtime"]).is_err());
    }
}
