use uuid::Uuid;

use crate::runtime::model::RuntimeFailure;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ApplicationMode {
    Desktop,
    PrepareDataCleanup,
    CleanupPending(Uuid),
    ListUninstallProjects(u32),
    CleanupProjects(u32),
}

fn parse_process_token(value: Option<String>) -> Result<u32, RuntimeFailure> {
    let raw = value.ok_or_else(|| RuntimeFailure::internal("缺少卸载进程标识"))?;
    let token = raw
        .parse::<u32>()
        .map_err(|_| RuntimeFailure::internal("卸载进程标识无效"))?;
    if token == 0 || token.to_string() != raw {
        return Err(RuntimeFailure::internal(
            "卸载进程标识必须是规范的非零十进制 u32",
        ));
    }
    Ok(token)
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
            "--list-uninstall-projects" => {
                Self::ListUninstallProjects(parse_process_token(values.next())?)
            }
            "--cleanup-projects" => Self::CleanupProjects(parse_process_token(values.next())?),
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
            Err(_)
        ));
        assert!(ApplicationMode::parse(["app.exe", "--cleanup-pending", "C:\\Users"]).is_err());
        assert!(ApplicationMode::parse(["app.exe", "--cleanup-pending"]).is_err());
        assert!(ApplicationMode::parse(["app.exe", "--cleanup-app-data", "extra"]).is_err());
        assert!(
            ApplicationMode::parse([
                "app.exe",
                "--install-bundled-runtime",
                "C:\\Users\\someone\\runtime.zip",
            ])
            .is_err()
        );
        assert!(ApplicationMode::parse(["app.exe", "--provision-runtime"]).is_err());
        assert_eq!(
            ApplicationMode::parse(["app.exe", "--list-uninstall-projects", "4321"]).unwrap(),
            ApplicationMode::ListUninstallProjects(4321)
        );
        assert_eq!(
            ApplicationMode::parse(["app.exe", "--cleanup-projects", "4321"]).unwrap(),
            ApplicationMode::CleanupProjects(4321)
        );
        for invalid in ["0", "01", "+1", "-1", "C:\\Users", "4294967296"] {
            assert!(ApplicationMode::parse(["app.exe", "--cleanup-projects", invalid]).is_err());
            assert!(
                ApplicationMode::parse(["app.exe", "--list-uninstall-projects", invalid]).is_err()
            );
        }
        assert!(
            ApplicationMode::parse(["app.exe", "--cleanup-projects", "4321", "extra"]).is_err()
        );
    }
}
