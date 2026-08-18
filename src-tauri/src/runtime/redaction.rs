use std::sync::OnceLock;

use regex::Regex;

/// Redacts credential-like values from runtime output before it reaches disk.
/// The patterns cover headers, env/query assignments and quoted JSON fields.
pub fn redact_secrets(input: &str) -> String {
    static AUTHORIZATION: OnceLock<Regex> = OnceLock::new();
    static NAMED_SECRET: OnceLock<Regex> = OnceLock::new();
    static LOOSE_BEARER: OnceLock<Regex> = OnceLock::new();

    let authorization = AUTHORIZATION.get_or_init(|| {
        Regex::new(r#"(?i)\b(authorization)([\"']?\s*[:=]\s*[\"']?)(?:bearer\s+)?[^\s,;&\"'}]+"#)
            .expect("static authorization regex")
    });
    let named_secret = NAMED_SECRET.get_or_init(|| {
        Regex::new(r#"(?i)\b([a-z0-9_]*(?:api[_-]?key|session[_-]?token))([\"']?\s*[:=]\s*[\"']?)[^\s,;&\"'}]+"#)
            .expect("static named-secret regex")
    });
    let loose_bearer = LOOSE_BEARER.get_or_init(|| {
        Regex::new(r#"(?i)\b(bearer)(\s+)[^\s,;&\"'}]+"#).expect("static bearer regex")
    });

    let output = authorization.replace_all(input, "$1$2[REDACTED]");
    let output = named_secret.replace_all(&output, "$1$2[REDACTED]");
    loose_bearer
        .replace_all(&output, "$1$2[REDACTED]")
        .into_owned()
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
}
