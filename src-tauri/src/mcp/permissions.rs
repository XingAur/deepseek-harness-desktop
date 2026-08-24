use std::{fs, path::PathBuf};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum McpToolEffect {
    Read,
    Write,
    External,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct McpToolRequest {
    pub server_id: String,
    pub tool_name: String,
    pub effect: McpToolEffect,
    pub task_id: String,
    pub scope: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct McpPermissionGrant {
    pub server_id: String,
    pub tool_name: String,
    pub effect: McpToolEffect,
    pub task_id: String,
    pub scope: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum McpPermissionDecision {
    Allow,
    ApprovalRequired,
    Deny,
}

pub fn evaluate(request: &McpToolRequest, grants: &[McpPermissionGrant]) -> McpPermissionDecision {
    if request.server_id.is_empty() || request.tool_name.is_empty() || request.task_id.is_empty() {
        return McpPermissionDecision::Deny;
    }
    if grants.iter().any(|grant| {
        grant.server_id == request.server_id
            && grant.tool_name == request.tool_name
            && grant.effect == request.effect
            && grant.task_id == request.task_id
            && scope_contains(&grant.scope, &request.scope)
    }) {
        McpPermissionDecision::Allow
    } else {
        McpPermissionDecision::ApprovalRequired
    }
}

fn scope_contains(granted: &str, requested: &str) -> bool {
    if granted == requested {
        return true;
    }
    let Some(normalized_granted) = normalize_scope(granted) else {
        return false;
    };
    let Some(normalized_requested) = normalize_scope(requested) else {
        return false;
    };
    if !lexical_scope_contains(&normalized_granted, &normalized_requested) {
        return false;
    }
    match (resolve_existing_path(granted), resolve_existing_path(requested)) {
        (Some(canonical_granted), Some(canonical_requested)) => {
            lexical_scope_contains(&canonical_granted, &canonical_requested)
        }
        _ => false,
    }
}

fn lexical_scope_contains(granted: &str, requested: &str) -> bool {
    if granted == requested || granted == "/" {
        return true;
    }
    let boundary = if granted.ends_with('/') {
        granted.to_owned()
    } else {
        format!("{granted}/")
    };
    requested.starts_with(&boundary)
}

fn resolve_existing_path(value: &str) -> Option<String> {
    let normalized = normalize_scope(value)?;
    let mut candidate = PathBuf::from(&normalized);
    let mut suffix = Vec::new();
    while !candidate.exists() {
        if fs::symlink_metadata(&candidate)
            .map(|metadata| metadata.file_type().is_symlink())
            .unwrap_or(false)
        {
            return None;
        }
        let parent = candidate.parent()?.to_path_buf();
        if parent == candidate {
            return None;
        }
        suffix.push(candidate.file_name()?.to_owned());
        candidate = parent;
    }
    let canonical = fs::canonicalize(candidate).ok()?;
    let mut resolved = canonical;
    for component in suffix.iter().rev() {
        resolved.push(component);
    }
    normalize_scope(&resolved.to_string_lossy())
}

fn normalize_scope(value: &str) -> Option<String> {
    let separators = value.replace('\\', "/");
    let drive = separators
        .as_bytes()
        .get(0..3)
        .filter(|prefix| {
            prefix[0].is_ascii_alphabetic() && prefix[1] == b':' && prefix[2] == b'/'
        })
        .map(|_| &separators[..3]);
    let unc = separators.starts_with("//");
    let posix = separators.starts_with('/') && !unc;
    if drive.is_none() && !unc && !posix {
        return None;
    }
    let prefix = drive.unwrap_or(if unc { "//" } else { "/" });
    let parts = separators[prefix.len()..]
        .split('/')
        .try_fold(Vec::<&str>::new(), |mut parts, part| {
            if part.is_empty() || part == "." {
                return Some(parts);
            }
            if part == ".." {
                parts.pop()?;
            } else {
                parts.push(part);
            }
            Some(parts)
        })?;
    Some(format!("{prefix}{}", parts.join("/")))
}

#[cfg(test)]
mod tests {
    use super::{evaluate, McpPermissionDecision, McpPermissionGrant, McpToolEffect, McpToolRequest};

    fn request(effect: McpToolEffect, scope: &str) -> McpToolRequest {
        McpToolRequest { server_id: "server-a".to_owned(), tool_name: "read_file".to_owned(), effect, task_id: "task-a".to_owned(), scope: scope.to_owned() }
    }

    #[test]
    fn grants_are_bound_to_server_tool_effect_task_and_scope() {
        let grants = vec![McpPermissionGrant { server_id: "server-a".to_owned(), tool_name: "read_file".to_owned(), effect: McpToolEffect::Read, task_id: "task-a".to_owned(), scope: "/workspace".to_owned() }];
        assert_eq!(evaluate(&request(McpToolEffect::Read, "/workspace/src"), &grants), McpPermissionDecision::Allow);
        assert_eq!(evaluate(&request(McpToolEffect::Write, "/workspace/src"), &grants), McpPermissionDecision::ApprovalRequired);
        assert_eq!(evaluate(&request(McpToolEffect::Read, "/other"), &grants), McpPermissionDecision::ApprovalRequired);
        let mut wrong_task = request(McpToolEffect::Read, "/workspace");
        wrong_task.task_id = "task-b".to_owned();
        assert_eq!(evaluate(&wrong_task, &grants), McpPermissionDecision::ApprovalRequired);
    }

    #[test]
    fn malformed_identity_is_denied() {
        let mut malformed = request(McpToolEffect::Read, "/workspace");
        malformed.server_id.clear();
        assert_eq!(evaluate(&malformed, &[]), McpPermissionDecision::Deny);
    }

    #[test]
    fn parent_traversal_cannot_escape_a_granted_scope() {
        let grants = vec![McpPermissionGrant {
            server_id: "server-a".to_owned(),
            tool_name: "read_file".to_owned(),
            effect: McpToolEffect::Read,
            task_id: "task-a".to_owned(),
            scope: "/workspace".to_owned(),
        }];
        assert_eq!(
            evaluate(&request(McpToolEffect::Read, "/workspace/../outside"), &grants),
            McpPermissionDecision::ApprovalRequired
        );
    }

    #[cfg(unix)]
    #[test]
    fn symlink_cannot_escape_a_granted_filesystem_scope() {
        use std::os::unix::fs::symlink;

        let root = tempfile::tempdir().unwrap();
        let workspace = root.path().join("workspace");
        let outside = root.path().join("outside");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        symlink(&outside, workspace.join("link")).unwrap();
        let grants = vec![McpPermissionGrant {
            server_id: "server-a".to_owned(),
            tool_name: "read_file".to_owned(),
            effect: McpToolEffect::Read,
            task_id: "task-a".to_owned(),
            scope: workspace.to_string_lossy().into_owned(),
        }];
        let requested = workspace.join("link/secret.txt");
        assert_eq!(
            evaluate(
                &request(McpToolEffect::Read, &requested.to_string_lossy()),
                &grants
            ),
            McpPermissionDecision::ApprovalRequired
        );
    }

    #[cfg(unix)]
    #[test]
    fn dangling_symlink_requires_approval() {
        use std::os::unix::fs::symlink;

        let root = tempfile::tempdir().unwrap();
        let workspace = root.path().join("workspace");
        std::fs::create_dir_all(&workspace).unwrap();
        symlink(root.path().join("missing"), workspace.join("dangling")).unwrap();
        let grants = vec![McpPermissionGrant {
            server_id: "server-a".to_owned(),
            tool_name: "read_file".to_owned(),
            effect: McpToolEffect::Read,
            task_id: "task-a".to_owned(),
            scope: workspace.to_string_lossy().into_owned(),
        }];
        let requested = workspace.join("dangling/secret.txt");
        assert_eq!(
            evaluate(
                &request(McpToolEffect::Read, &requested.to_string_lossy()),
                &grants
            ),
            McpPermissionDecision::ApprovalRequired
        );
    }
}
