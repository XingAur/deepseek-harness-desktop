use std::collections::BTreeSet;

use url::Url;

use crate::runtime::model::{RuntimeFailure, RuntimeFailureCode};

#[derive(Clone, Debug)]
pub struct RuntimeSourcePolicy {
    endpoint: Url,
    allowed_hosts: BTreeSet<String>,
    max_redirects: usize,
}

impl RuntimeSourcePolicy {
    pub fn new(
        endpoint: Url,
        allowed_hosts: impl IntoIterator<Item = impl Into<String>>,
    ) -> Result<Self, RuntimeFailure> {
        let policy = Self {
            endpoint,
            allowed_hosts: allowed_hosts.into_iter().map(Into::into).collect(),
            max_redirects: 5,
        };
        policy.validate_manifest_url(&policy.endpoint)?;
        Ok(policy)
    }

    pub fn production(endpoint: Url) -> Result<Self, RuntimeFailure> {
        Self::new(
            endpoint,
            [
                "github.com",
                "objects.githubusercontent.com",
                "release-assets.githubusercontent.com",
            ],
        )
    }

    #[cfg(feature = "e2e")]
    pub fn e2e(endpoint: Url) -> Result<Self, RuntimeFailure> {
        let host = endpoint.host_str().unwrap_or_default().to_ascii_lowercase();
        if !matches!(host.as_str(), "127.0.0.1" | "localhost") {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Network,
                "E2E Runtime 来源必须是 loopback HTTPS",
            ));
        }
        Self::new(endpoint, [host])
    }

    pub fn endpoint(&self) -> &Url {
        &self.endpoint
    }

    pub fn validate_manifest_url(&self, url: &Url) -> Result<(), RuntimeFailure> {
        self.validate_https_url(url)
    }

    pub fn validate_redirect(&self, url: &Url) -> Result<(), RuntimeFailure> {
        self.validate_https_url(url)
    }

    pub fn redirect_policy(&self) -> reqwest::redirect::Policy {
        let policy = self.clone();
        reqwest::redirect::Policy::custom(move |attempt| {
            if attempt.previous().len() >= policy.max_redirects {
                return attempt.error("Runtime 下载重定向次数过多");
            }
            match policy.validate_redirect(attempt.url()) {
                Ok(()) => attempt.follow(),
                Err(error) => attempt.error(error),
            }
        })
    }

    fn validate_https_url(&self, url: &Url) -> Result<(), RuntimeFailure> {
        let host = url.host_str().unwrap_or_default().to_ascii_lowercase();
        if url.scheme() != "https"
            || !url.username().is_empty()
            || url.password().is_some()
            || !self.allowed_hosts.contains(&host)
        {
            return Err(RuntimeFailure::new(
                RuntimeFailureCode::Network,
                "Runtime 来源不在允许的 HTTPS 主机范围内",
            ));
        }
        Ok(())
    }
}

pub fn compiled_manifest_endpoint() -> Result<Option<Url>, RuntimeFailure> {
    option_env!("DSH_DESKTOP_RUNTIME_MANIFEST_URL")
        .map(|value| Url::parse(value).map_err(RuntimeFailure::internal))
        .transpose()
}

#[cfg(feature = "e2e")]
pub fn e2e_manifest_endpoint() -> Result<Option<Url>, RuntimeFailure> {
    std::env::var("DSH_DESKTOP_E2E_RUNTIME_MANIFEST_URL")
        .ok()
        .map(|value| Url::parse(&value).map_err(RuntimeFailure::internal))
        .transpose()
}

pub fn manifest_endpoint() -> Result<Option<Url>, RuntimeFailure> {
    #[cfg(feature = "e2e")]
    if let Some(endpoint) = e2e_manifest_endpoint()? {
        RuntimeSourcePolicy::e2e(endpoint.clone())?;
        return Ok(Some(endpoint));
    }
    compiled_manifest_endpoint()
}

pub fn runtime_source_policy(endpoint: Url) -> Result<RuntimeSourcePolicy, RuntimeFailure> {
    #[cfg(feature = "e2e")]
    if e2e_manifest_endpoint()?.as_ref() == Some(&endpoint) {
        return RuntimeSourcePolicy::e2e(endpoint);
    }
    RuntimeSourcePolicy::production(endpoint)
}

#[cfg(feature = "e2e")]
pub fn is_e2e_manifest_endpoint(endpoint: &Url) -> Result<bool, RuntimeFailure> {
    Ok(e2e_manifest_endpoint()?.as_ref() == Some(endpoint))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn production_policy_accepts_only_the_compiled_https_hosts() {
        let policy = RuntimeSourcePolicy::production(
            Url::parse("https://github.com/anywhere-labs/deepseek-harness-desktop/releases/download/runtime-v1/runtime-windows-x86_64.json").unwrap(),
        )
        .unwrap();
        assert!(policy.validate_manifest_url(policy.endpoint()).is_ok());
        assert!(
            policy
                .validate_redirect(
                    &Url::parse("https://objects.githubusercontent.com/runtime.zip").unwrap()
                )
                .is_ok()
        );
        assert!(
            policy
                .validate_redirect(&Url::parse("http://github.com/runtime.zip").unwrap())
                .is_err()
        );
        assert!(
            policy
                .validate_redirect(&Url::parse("file:///C:/runtime.zip").unwrap())
                .is_err()
        );
        assert!(
            policy
                .validate_redirect(&Url::parse("https://example.com/runtime.zip").unwrap())
                .is_err()
        );
        assert!(
            RuntimeSourcePolicy::production(
                Url::parse("https://user:secret@github.com/runtime.json").unwrap()
            )
            .is_err()
        );
    }

    #[cfg(feature = "e2e")]
    #[test]
    fn e2e_policy_accepts_only_loopback_https() {
        let policy =
            RuntimeSourcePolicy::e2e(Url::parse("https://127.0.0.1:43123/manifest.json").unwrap())
                .unwrap();
        assert!(policy.validate_manifest_url(policy.endpoint()).is_ok());
        assert!(
            policy
                .validate_redirect(&Url::parse("https://127.0.0.1:43123/runtime.zip").unwrap())
                .is_ok()
        );
        assert!(
            RuntimeSourcePolicy::e2e(Url::parse("http://127.0.0.1:43123/manifest.json").unwrap())
                .is_err()
        );
        assert!(
            RuntimeSourcePolicy::e2e(Url::parse("https://example.com/manifest.json").unwrap())
                .is_err()
        );
    }
}
