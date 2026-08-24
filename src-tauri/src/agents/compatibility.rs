use std::collections::BTreeSet;
use std::sync::OnceLock;

use regex::Regex;
use semver::Version;

use super::model::{CompatibilityStatus, DiscoveredAgent};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CompatibilityPolicy {
    pub minimum_version: Option<Version>,
    pub maximum_version: Option<Version>,
    pub supported_protocols: BTreeSet<String>,
}

impl CompatibilityPolicy {
    pub fn new<I, S>(
        minimum_version: Option<Version>,
        maximum_version: Option<Version>,
        supported_protocols: I,
    ) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        Self {
            minimum_version,
            maximum_version,
            supported_protocols: supported_protocols.into_iter().map(Into::into).collect(),
        }
    }
}

pub fn parse_cli_version(output: &str) -> Result<Version, String> {
    static VERSION: OnceLock<Regex> = OnceLock::new();
    let pattern = VERSION.get_or_init(|| {
        Regex::new(r"(?i)(?:^|[^0-9])v?(\d+\.\d+\.\d+(?:-[0-9a-z.-]+)?(?:\+[0-9a-z.-]+)?)")
            .expect("static CLI version regex")
    });
    let captures = pattern
        .captures(output)
        .ok_or_else(|| "CLI 未返回可识别版本".to_owned())?;
    Version::parse(&captures[1]).map_err(|_| "CLI 版本格式无效".to_owned())
}

pub fn check_compatibility(
    agent: &DiscoveredAgent,
    policy: &CompatibilityPolicy,
) -> CompatibilityStatus {
    let Some(version) = agent.version.as_ref() else {
        return CompatibilityStatus::VersionUnknown;
    };
    if policy
        .minimum_version
        .as_ref()
        .is_some_and(|minimum| version < minimum)
    {
        return CompatibilityStatus::VersionTooOld;
    }
    if policy
        .maximum_version
        .as_ref()
        .is_some_and(|maximum| version > maximum)
    {
        return CompatibilityStatus::VersionTooNew;
    }
    let Some(protocol) = agent.protocol.as_deref() else {
        return CompatibilityStatus::ProtocolUnknown;
    };
    if !policy.supported_protocols.contains(protocol) {
        return CompatibilityStatus::UnsupportedProtocol;
    }
    CompatibilityStatus::Compatible
}

#[cfg(test)]
mod tests {
    use semver::Version;

    use super::super::model::{AgentProvider, CompatibilityStatus, DiscoveredAgent};
    use super::{CompatibilityPolicy, check_compatibility, parse_cli_version};

    fn agent(version: Option<&str>, protocol: Option<&str>) -> DiscoveredAgent {
        DiscoveredAgent {
            provider: AgentProvider::Codex,
            path: "/tmp/codex".into(),
            source: super::super::model::DiscoverySource::Explicit,
            version: version.map(|item| Version::parse(item).unwrap()),
            protocol: protocol.map(str::to_owned),
        }
    }

    #[test]
    fn parses_common_cli_version_prefixes_without_accepting_arbitrary_text() {
        assert_eq!(
            parse_cli_version("codex 1.2.3").unwrap(),
            Version::parse("1.2.3").unwrap()
        );
        assert_eq!(
            parse_cli_version("Claude Code v0.8.1-beta.2").unwrap(),
            Version::parse("0.8.1-beta.2").unwrap()
        );
        assert!(parse_cli_version("version unavailable").is_err());
    }

    #[test]
    fn enforces_minimum_and_maximum_supported_versions() {
        let policy = CompatibilityPolicy::new(
            Some(Version::parse("1.0.0").unwrap()),
            Some(Version::parse("2.0.0").unwrap()),
            ["dsh-agent-adapter/v1"],
        );
        assert_eq!(
            check_compatibility(&agent(Some("1.5.0"), Some("dsh-agent-adapter/v1")), &policy),
            CompatibilityStatus::Compatible
        );
        assert_eq!(
            check_compatibility(&agent(Some("0.9.9"), Some("dsh-agent-adapter/v1")), &policy),
            CompatibilityStatus::VersionTooOld
        );
        assert_eq!(
            check_compatibility(&agent(Some("2.0.1"), Some("dsh-agent-adapter/v1")), &policy),
            CompatibilityStatus::VersionTooNew
        );
    }

    #[test]
    fn rejects_unknown_versions_and_unsupported_protocols() {
        let policy = CompatibilityPolicy::new(None, None, ["dsh-agent-adapter/v1"]);
        assert_eq!(
            check_compatibility(&agent(None, Some("dsh-agent-adapter/v1")), &policy),
            CompatibilityStatus::VersionUnknown
        );
        assert_eq!(
            check_compatibility(&agent(Some("1.0.0"), Some("dsh-agent-adapter/v0")), &policy),
            CompatibilityStatus::UnsupportedProtocol
        );
        assert_eq!(
            check_compatibility(&agent(Some("1.0.0"), None), &policy),
            CompatibilityStatus::ProtocolUnknown
        );
    }
}
