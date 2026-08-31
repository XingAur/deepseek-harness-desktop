use semver::{Version, VersionReq};

use super::model::RuntimeManifest;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RuntimeRequirement {
    pub minimum_runtime: Version,
    pub dsh: VersionReq,
}

impl RuntimeRequirement {
    pub fn from_bundled_manifest(manifest: &RuntimeManifest) -> Self {
        Self {
            minimum_runtime: manifest.version.clone(),
            dsh: VersionReq::parse(&format!("^{}", manifest.dsh_version))
                .expect("a verified manifest always contains a valid DSH version"),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LocalRuntimeDecision {
    FastStart(RuntimeManifest),
    UpgradeRequired,
}

pub fn decide_local(
    requirement: &RuntimeRequirement,
    installed: Option<&RuntimeManifest>,
    verification_cache_valid: bool,
) -> LocalRuntimeDecision {
    let Some(installed) = installed else {
        return LocalRuntimeDecision::UpgradeRequired;
    };
    if verification_cache_valid
        && installed.version >= requirement.minimum_runtime
        && requirement.dsh.matches(&installed.dsh_version)
    {
        LocalRuntimeDecision::FastStart(installed.clone())
    } else {
        LocalRuntimeDecision::UpgradeRequired
    }
}

#[cfg(test)]
mod tests {
    use semver::{Version, VersionReq};
    use url::Url;

    use super::{LocalRuntimeDecision, RuntimeRequirement, decide_local};
    use crate::runtime::model::{ArchiveKind, RuntimeManifest, RuntimeTarget};

    fn verified_manifest(runtime: &str, dsh: &str) -> RuntimeManifest {
        RuntimeManifest {
            schema_version: 1,
            version: Version::parse(runtime).unwrap(),
            dsh_version: Version::parse(dsh).unwrap(),
            target: RuntimeTarget::WindowsX86_64,
            url: Url::parse("https://github.com/example/runtime.zip").unwrap(),
            size: 1,
            sha256: "a".repeat(64),
            signature: "signature".to_string(),
            archive: ArchiveKind::Zip,
            entrypoint: "app/node.exe".to_string(),
            args: Vec::new(),
            health_path: "/__desktop/health".to_string(),
            desktop_plugin_sha256: None,
        }
    }

    #[test]
    fn compatible_verified_runtime_starts_without_a_remote_check() {
        let requirement = RuntimeRequirement {
            minimum_runtime: Version::new(1, 8, 0),
            dsh: VersionReq::parse("^0.1.0-rc.7").unwrap(),
        };
        let installed = verified_manifest("1.8.2", "0.1.0-rc.7");
        assert_eq!(
            decide_local(&requirement, Some(&installed), true),
            LocalRuntimeDecision::FastStart(installed)
        );
    }

    #[test]
    fn incompatible_runtime_requires_foreground_upgrade() {
        let requirement = RuntimeRequirement {
            minimum_runtime: Version::new(1, 8, 0),
            dsh: VersionReq::parse("^0.1.0-rc.7").unwrap(),
        };
        let installed = verified_manifest("1.7.0", "0.1.0-rc.6");
        assert_eq!(
            decide_local(&requirement, Some(&installed), true),
            LocalRuntimeDecision::UpgradeRequired
        );
    }
}
