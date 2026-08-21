use semver::Version;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RuntimeDecision {
    FastStart,
    Upgrade,
    KeepCurrent,
}

pub fn decide_runtime(
    current: (&Version, &str),
    candidate_version: &Version,
    candidate_sha256: &str,
) -> RuntimeDecision {
    let (current_version, current_sha256) = current;
    match candidate_version.cmp(current_version) {
        std::cmp::Ordering::Greater => RuntimeDecision::Upgrade,
        std::cmp::Ordering::Less => RuntimeDecision::KeepCurrent,
        std::cmp::Ordering::Equal if !candidate_sha256.eq_ignore_ascii_case(current_sha256) => {
            RuntimeDecision::Upgrade
        }
        std::cmp::Ordering::Equal => RuntimeDecision::FastStart,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compares_version_and_archive_identity() {
        let v1 = Version::parse("1.0.0").unwrap();
        let v2 = Version::parse("2.0.0").unwrap();
        assert_eq!(
            decide_runtime((&v1, "a"), &v1, "a"),
            RuntimeDecision::FastStart
        );
        assert_eq!(
            decide_runtime((&v1, &"A".repeat(64)), &v1, &"a".repeat(64)),
            RuntimeDecision::FastStart
        );
        assert_eq!(
            decide_runtime((&v1, "a"), &v1, "b"),
            RuntimeDecision::Upgrade
        );
        assert_eq!(
            decide_runtime((&v1, "a"), &v2, "b"),
            RuntimeDecision::Upgrade
        );
        assert_eq!(
            decide_runtime((&v2, "a"), &v1, "b"),
            RuntimeDecision::KeepCurrent
        );
    }
}
