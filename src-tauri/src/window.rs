use url::Url;

use crate::runtime::RuntimeFailure;

pub fn runtime_renderer_url(
    mut renderer: Url,
    expected_port: u16,
    session_token: &str,
) -> Result<Url, RuntimeFailure> {
    if renderer.scheme() != "http"
        || renderer.host_str() != Some("127.0.0.1")
        || renderer.port() != Some(expected_port)
    {
        return Err(RuntimeFailure::internal(
            "拒绝导航到非受管 DeepSeek Harness 地址",
        ));
    }
    renderer
        .query_pairs_mut()
        .append_pair("dsh-desktop-mode", "advanced")
        .append_pair(
            "dsh-desktop-platform",
            if cfg!(target_os = "macos") {
                "darwin"
            } else {
                "win32"
            },
        )
        .append_pair("dsh-desktop-token", session_token);
    Ok(renderer)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decorates_the_exact_managed_loopback_url() {
        let url = runtime_renderer_url(
            Url::parse("http://127.0.0.1:39000/").unwrap(),
            39000,
            "session-token",
        )
        .unwrap();

        assert_eq!(url.host_str(), Some("127.0.0.1"));
        assert_eq!(url.port(), Some(39000));
        assert!(url.query().unwrap().contains("dsh-desktop-mode=advanced"));
    }

    #[test]
    fn rejects_another_port_or_host() {
        assert!(
            runtime_renderer_url(
                Url::parse("http://127.0.0.1:39001/").unwrap(),
                39000,
                "token"
            )
            .is_err()
        );
        assert!(
            runtime_renderer_url(
                Url::parse("http://localhost:39000/").unwrap(),
                39000,
                "token"
            )
            .is_err()
        );
    }
}
