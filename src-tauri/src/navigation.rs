use url::Url;

use crate::runtime::RuntimeFailure;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExternalDecision {
    OpenSystem,
    Deny,
}

pub struct NavigationPolicy;

impl NavigationPolicy {
    pub fn top_level(url: &Url) -> bool {
        url.username().is_empty()
            && url.password().is_none()
            && (matches!(
                (url.scheme(), url.host_str(), url.port()),
                ("tauri", Some("localhost"), None)
                    | ("http", Some("tauri.localhost"), None)
                    | ("https", Some("tauri.localhost"), None)
            ) || cfg!(debug_assertions)
                && url.scheme() == "http"
                && url.host_str() == Some("localhost")
                && url.port() == Some(1420))
    }

    pub fn external(url: &Url) -> ExternalDecision {
        if url.scheme() == "https"
            && url.host_str().is_some()
            && url.username().is_empty()
            && url.password().is_none()
        {
            ExternalDecision::OpenSystem
        } else {
            ExternalDecision::Deny
        }
    }
}

pub fn validated_https(value: &str) -> Result<Url, RuntimeFailure> {
    let url = Url::parse(value).map_err(|_| denied_external())?;
    let suspicious_fragment = url.fragment().is_some_and(|fragment| {
        fragment
            .trim_start()
            .to_ascii_lowercase()
            .starts_with("javascript:")
    });
    if NavigationPolicy::external(&url) != ExternalDecision::OpenSystem || suspicious_fragment {
        return Err(denied_external());
    }
    Ok(url)
}

fn denied_external() -> RuntimeFailure {
    let mut failure = RuntimeFailure::internal("只允许在系统浏览器中打开无凭据的 HTTPS 链接");
    failure.recoverable = false;
    failure
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn allows_only_local_shell_top_level_and_https_external_requests() {
        assert!(NavigationPolicy::top_level(
            &Url::parse("tauri://localhost/index.html").unwrap()
        ));
        assert!(NavigationPolicy::top_level(
            &Url::parse("http://tauri.localhost/index.html").unwrap()
        ));
        assert!(!NavigationPolicy::top_level(
            &Url::parse("http://127.0.0.1:39000/").unwrap()
        ));
        assert!(!NavigationPolicy::top_level(
            &Url::parse("file:///C:/secret").unwrap()
        ));
        assert_eq!(
            NavigationPolicy::external(&Url::parse("https://example.com/docs").unwrap()),
            ExternalDecision::OpenSystem
        );
        assert_eq!(
            NavigationPolicy::external(&Url::parse("file:///C:/secret").unwrap()),
            ExternalDecision::Deny
        );
    }

    #[test]
    fn rejects_deceptive_or_privileged_top_level_urls() {
        for url in [
            "http://localhost:39000/",
            "https://user:pass@tauri.localhost/",
            "javascript:alert(1)",
            "data:text/html,hello",
            "dsh://localhost/",
        ] {
            assert!(
                !NavigationPolicy::top_level(&Url::parse(url).unwrap()),
                "{url}"
            );
        }
    }

    #[test]
    fn validated_https_rejects_credentials_http_and_javascript_fragments() {
        assert!(validated_https("https://example.com/docs").is_ok());
        assert!(validated_https("http://example.com").is_err());
        assert!(validated_https("https://user:pass@example.com").is_err());
        assert!(validated_https("javascript:alert(1)").is_err());
        assert!(validated_https("https://example.com/#javascript:alert(1)").is_err());
    }
}
