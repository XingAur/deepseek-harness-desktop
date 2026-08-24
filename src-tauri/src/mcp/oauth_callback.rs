use url::Url;

pub const CALLBACK_PATH: &str = "/oauth/callback";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OAuthCallback {
    pub path: String,
    pub state: String,
    pub code: String,
}

pub fn validate_callback(
    expected_state: &str,
    callback: &OAuthCallback,
    already_used: bool,
) -> Result<(), String> {
    if already_used {
        return Err("oauth-callback-used".to_owned());
    }
    if callback.path != CALLBACK_PATH {
        return Err("oauth-callback-path-mismatch".to_owned());
    }
    if expected_state.is_empty() || callback.state != expected_state {
        return Err("oauth-state-mismatch".to_owned());
    }
    if callback.code.is_empty() || callback.code.len() > 4096 {
        return Err("oauth-code-invalid".to_owned());
    }
    Ok(())
}

pub fn validate_issuer(value: &str) -> Result<String, String> {
    let url = Url::parse(value).map_err(|_| "oauth-issuer-rejected".to_owned())?;
    let loopback = matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "[::1]"));
    if url.username() != ""
        || url.password().is_some()
        || url.fragment().is_some()
        || (url.scheme() != "https" && !(loopback && url.scheme() == "http"))
    {
        return Err("oauth-issuer-rejected".to_owned());
    }
    Ok(url.to_string())
}

#[cfg(test)]
mod tests {
    use super::{validate_callback, validate_issuer, OAuthCallback, CALLBACK_PATH};

    fn callback(state: &str) -> OAuthCallback {
        OAuthCallback { path: CALLBACK_PATH.to_owned(), state: state.to_owned(), code: "code".to_owned() }
    }

    #[test]
    fn callback_is_exact_state_bound_and_single_use() {
        assert!(validate_callback("state", &callback("state"), false).is_ok());
        assert_eq!(validate_callback("state", &callback("wrong"), false), Err("oauth-state-mismatch".to_owned()));
        assert_eq!(validate_callback("state", &callback("state"), true), Err("oauth-callback-used".to_owned()));
    }

    #[test]
    fn issuer_requires_https_except_loopback() {
        assert!(validate_issuer("https://mcp.example.com").is_ok());
        assert!(validate_issuer("http://127.0.0.1:43123").is_ok());
        assert_eq!(validate_issuer("http://mcp.example.com"), Err("oauth-issuer-rejected".to_owned()));
    }
}
