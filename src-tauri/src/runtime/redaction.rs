use std::sync::OnceLock;

use regex::Regex;

/// Redacts credential-like values from runtime output before it reaches disk.
/// The patterns cover headers, env/query assignments and quoted JSON fields.
pub fn redact_secrets(input: &str) -> String {
    static AUTHORIZATION: OnceLock<Regex> = OnceLock::new();
    static NAMED_SECRET: OnceLock<Regex> = OnceLock::new();
    static LOOSE_BEARER: OnceLock<Regex> = OnceLock::new();
    static COOKIE: OnceLock<Regex> = OnceLock::new();
    static PRIVATE_KEY_BLOCK: OnceLock<Regex> = OnceLock::new();

    let authorization = AUTHORIZATION.get_or_init(|| {
        Regex::new(r#"(?i)\b(authorization)([\"']?\s*[:=]\s*[\"']?)(?:bearer\s+)?[^\s,;&\"'}]+"#)
            .expect("static authorization regex")
    });
    let named_secret = NAMED_SECRET.get_or_init(|| {
        Regex::new(r#"(?i)\b([a-z0-9_]*(?:api[_-]?key|session[_-]?token|oauth[_-]?token|access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|private[_-]?key|password|passwd))([\"']?\s*[:=]\s*[\"']?)[^\s,;&\"'}]+"#)
            .expect("static named-secret regex")
    });
    let loose_bearer = LOOSE_BEARER.get_or_init(|| {
        Regex::new(r#"(?i)\b(bearer)(\s+)[^\s,;&\"'}]+"#).expect("static bearer regex")
    });
    let cookie = COOKIE.get_or_init(|| {
        Regex::new(r#"(?i)\b(cookie|set-cookie)([\"']?\s*[:=]\s*[\"']?)[^\r\n]+"#)
            .expect("static cookie regex")
    });
    let private_key_block = PRIVATE_KEY_BLOCK.get_or_init(|| {
        Regex::new(r"(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----")
            .expect("static private key regex")
    });

    let output = private_key_block.replace_all(input, "[REDACTED PRIVATE KEY]");
    let output = authorization.replace_all(&output, "$1$2[REDACTED]");
    let output = cookie.replace_all(&output, "$1$2[REDACTED]");
    let output = named_secret.replace_all(&output, "$1$2[REDACTED]");
    loose_bearer
        .replace_all(&output, "$1$2[REDACTED]")
        .into_owned()
}

/// Redacts secrets and caps diagnostic text before it can enter a log or IPC frame.
pub fn redact_bounded(input: &str, limit: usize) -> String {
    let redacted = redact_secrets(input);
    if redacted.len() <= limit {
        return redacted;
    }
    let suffix = "…[TRUNCATED]";
    if limit < suffix.len() {
        let mut end = 0;
        for (index, character) in redacted.char_indices() {
            if index + character.len_utf8() > limit {
                break;
            }
            end = index + character.len_utf8();
        }
        return redacted[..end].to_owned();
    }
    let mut end = limit.saturating_sub(suffix.len());
    while end > 0 && !redacted.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}{}", &redacted[..end], suffix)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_headers_assignments_json_and_loose_bearer_tokens() {
        let input = concat!(
            "Authorization: Bearer header-secret\n",
            "api_key=query-secret&session_token=session-secret\n",
            r#"{"sessionToken":"json-secret"}"#,
            "\nDSH_DESKTOP_SESSION_TOKEN=env-secret\n",
            "request failed for Bearer loose-secret"
        );

        let output = redact_secrets(input);

        for secret in [
            "header-secret",
            "query-secret",
            "session-secret",
            "json-secret",
            "env-secret",
            "loose-secret",
        ] {
            assert!(!output.contains(secret), "leaked {secret}: {output}");
        }
        assert!(output.contains("Authorization: [REDACTED]"));
        assert!(output.contains(r#""sessionToken":"[REDACTED]"#));
    }

    #[test]
    fn bounded_redaction_caps_output_after_secret_redaction() {
        let output = redact_bounded("Authorization: Bearer secret\nxxxxxxxxxxxxxxxx", 16);
        assert!(output.len() <= 16 + "…[TRUNCATED]".len());
        assert!(!output.contains("secret"));
    }

    #[test]
    fn redacts_oauth_private_key_cookie_and_tiny_limits() {
        let input = concat!(
            "OAUTH_TOKEN=oauth-secret\n",
            "PRIVATE_KEY=private-secret\n",
            "Cookie: session=cookie-secret\n",
            "-----BEGIN PRIVATE KEY-----\nkey-material\n-----END PRIVATE KEY-----"
        );
        let output = redact_secrets(input);
        for secret in [
            "oauth-secret",
            "private-secret",
            "cookie-secret",
            "key-material",
        ] {
            assert!(!output.contains(secret), "leaked {secret}: {output}");
        }
        assert!(redact_bounded("abcdef", 3).len() <= 3);
    }
}
