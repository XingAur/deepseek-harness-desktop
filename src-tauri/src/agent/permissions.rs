#[cfg(test)]
mod tests {
    use std::{fs, path::PathBuf};

    use chrono::{Duration, Utc};
    use tempfile::tempdir;
    use uuid::Uuid;

    use super::super::model::{
        AgentCapability, AgentPermissionMode, CapabilityRequest, Decision, NetworkOperation,
        ProfileBoundary, TaskContext, TaskGrant, TaskLifecycle,
    };
    use super::{evaluate, resolve_path_scope};

    fn context(root: PathBuf, mode: AgentPermissionMode, boundary: ProfileBoundary) -> TaskContext {
        TaskContext {
            task_id: Uuid::new_v4(),
            generation_id: "generation-a".to_owned(),
            workspace_root: root,
            permission_mode: mode,
            profile_boundary: boundary,
            lifecycle: TaskLifecycle::Active,
            now: Utc::now(),
            explicit_grants: Vec::new(),
            declared_capabilities: Vec::new(),
            explicit_full_access: false,
            approved_processes: Vec::new(),
            approved_terminal_tools: Vec::new(),
        }
    }

    fn request(context: &TaskContext, capability: AgentCapability) -> CapabilityRequest {
        CapabilityRequest {
            request_id: Uuid::new_v4(),
            task_id: context.task_id,
            generation_id: context.generation_id.clone(),
            issued_at: context.now,
            expires_at: context.now + Duration::minutes(5),
            capability,
            disclosed: true,
        }
    }

    #[test]
    fn request_approval_allows_only_non_sensitive_observation() {
        let root = tempdir().unwrap();
        let file = root.path().join("README.md");
        fs::write(&file, "hello").unwrap();
        let context = context(
            root.path().to_path_buf(),
            AgentPermissionMode::RequestApproval,
            ProfileBoundary::WorkspaceWrite,
        );

        assert_eq!(
            evaluate(
                &context,
                &request(&context, AgentCapability::FileRead { path: file.clone() })
            ),
            Decision::AllowOnce
        );
        assert!(matches!(
            evaluate(
                &context,
                &request(&context, AgentCapability::FileWrite { path: file })
            ),
            Decision::RequestApproval { .. }
        ));

        let sensitive = root.path().join(".env");
        fs::write(&sensitive, "API_KEY=secret").unwrap();
        assert!(matches!(
            evaluate(
                &context,
                &request(&context, AgentCapability::FileRead { path: sensitive })
            ),
            Decision::RequestApproval { .. }
        ));
        assert_eq!(
            evaluate(
                &context,
                &request(
                    &context,
                    AgentCapability::Network {
                        host: "api.example.com".to_owned(),
                        port: 443,
                        operation: NetworkOperation::Read,
                    }
                )
            ),
            Decision::AllowOnce
        );
        assert!(matches!(
            evaluate(
                &context,
                &request(
                    &context,
                    AgentCapability::Network {
                        host: "api.example.com".to_owned(),
                        port: 443,
                        operation: NetworkOperation::Write,
                    }
                )
            ),
            Decision::RequestApproval { .. }
        ));
    }

    #[test]
    fn smart_approval_allows_workspace_write_but_not_external_or_destructive_actions() {
        let root = tempdir().unwrap();
        let file = root.path().join("notes.md");
        let context = context(
            root.path().to_path_buf(),
            AgentPermissionMode::SmartApproval,
            ProfileBoundary::WorkspaceWrite,
        );

        assert_eq!(
            evaluate(
                &context,
                &request(&context, AgentCapability::FileWrite { path: file })
            ),
            Decision::AllowOnce
        );
        assert!(matches!(
            evaluate(
                &context,
                &request(
                    &context,
                    AgentCapability::GitPush {
                        repository: root.path().to_path_buf(),
                        remote: "origin".to_owned(),
                    }
                )
            ),
            Decision::RequestApproval { .. }
        ));
        assert!(matches!(
            evaluate(
                &context,
                &request(
                    &context,
                    AgentCapability::FileDelete {
                        path: root.path().join("notes.md"),
                    }
                )
            ),
            Decision::RequestApproval { .. }
        ));
    }

    #[test]
    fn full_access_requires_explicit_task_scoped_confirmation_and_declared_capability() {
        let root = tempdir().unwrap();
        let file = root.path().join("notes.md");
        let mut context = context(
            root.path().to_path_buf(),
            AgentPermissionMode::FullAccess,
            ProfileBoundary::WorkspaceWrite,
        );
        let write = request(&context, AgentCapability::FileWrite { path: file });

        assert!(matches!(
            evaluate(&context, &write),
            Decision::RequestApproval { .. }
        ));
        context.explicit_full_access = true;
        context.declared_capabilities.push(write.capability.kind());
        assert_eq!(evaluate(&context, &write), Decision::AllowForTask);
    }

    #[test]
    fn read_only_profile_blocks_writes_even_when_full_access_is_selected() {
        let root = tempdir().unwrap();
        let mut context = context(
            root.path().to_path_buf(),
            AgentPermissionMode::FullAccess,
            ProfileBoundary::ReadOnly,
        );
        context.explicit_full_access = true;
        let request = request(
            &context,
            AgentCapability::FileWrite {
                path: root.path().join("blocked.txt"),
            },
        );

        assert!(matches!(
            evaluate(&context, &request),
            Decision::Denied { .. }
        ));
    }

    #[test]
    fn non_overridable_capabilities_are_always_denied() {
        let root = tempdir().unwrap();
        let mut context = context(
            root.path().to_path_buf(),
            AgentPermissionMode::FullAccess,
            ProfileBoundary::WorkspaceWrite,
        );
        context.explicit_full_access = true;
        for capability in [
            AgentCapability::CredentialExport {
                credential_id: "credential-id".to_owned(),
            },
            AgentCapability::AuditDisable,
            AgentCapability::BridgeBypass,
        ] {
            let request = request(&context, capability);
            assert!(matches!(
                evaluate(&context, &request),
                Decision::Denied { .. }
            ));
        }
    }

    #[test]
    fn unknown_capabilities_never_bypass_full_access() {
        let root = tempdir().unwrap();
        let mut context = context(
            root.path().to_path_buf(),
            AgentPermissionMode::FullAccess,
            ProfileBoundary::WorkspaceWrite,
        );
        context.explicit_full_access = true;
        context
            .declared_capabilities
            .push(super::super::model::CapabilityKind::Unknown);
        let request = request(
            &context,
            AgentCapability::Unknown {
                name: "future-capability".to_owned(),
            },
        );
        assert!(matches!(
            evaluate(&context, &request),
            Decision::RequestApproval { .. }
        ));
    }

    #[test]
    fn malformed_expired_cancelled_cross_task_and_undisclosed_requests_fail_closed() {
        let root = tempdir().unwrap();
        let context = context(
            root.path().to_path_buf(),
            AgentPermissionMode::FullAccess,
            ProfileBoundary::WorkspaceWrite,
        );
        let mut expired = request(
            &context,
            AgentCapability::FileRead {
                path: root.path().join("README.md"),
            },
        );
        expired.expires_at = context.now - Duration::seconds(1);
        assert!(matches!(
            evaluate(&context, &expired),
            Decision::Denied { .. }
        ));

        let mut cross_task = request(
            &context,
            AgentCapability::FileRead {
                path: root.path().join("README.md"),
            },
        );
        cross_task.task_id = Uuid::new_v4();
        assert!(matches!(
            evaluate(&context, &cross_task),
            Decision::Denied { .. }
        ));

        let mut undisclosed = request(
            &context,
            AgentCapability::FileRead {
                path: root.path().join("README.md"),
            },
        );
        undisclosed.disclosed = false;
        assert!(matches!(
            evaluate(&context, &undisclosed),
            Decision::Denied { .. }
        ));

        let mut cancelled = context.clone();
        cancelled.lifecycle = TaskLifecycle::Cancelled;
        let request = request(
            &cancelled,
            AgentCapability::FileRead {
                path: root.path().join("README.md"),
            },
        );
        assert!(matches!(
            evaluate(&cancelled, &request),
            Decision::Denied { .. }
        ));
    }

    #[test]
    fn explicit_path_scope_never_follows_symlink_or_prefix_lookalike() {
        let root = tempdir().unwrap();
        let outside = tempdir().unwrap();
        let outside_file = outside.path().join("secret.txt");
        fs::write(&outside_file, "secret").unwrap();
        let link = root.path().join("link.txt");
        #[cfg(unix)]
        std::os::unix::fs::symlink(&outside_file, &link).unwrap();

        #[cfg(unix)]
        assert!(resolve_path_scope(root.path(), &link).is_err());
        assert!(resolve_path_scope(root.path(), &outside_file).is_err());
        assert!(resolve_path_scope(
            root.path(),
            &PathBuf::from(format!("{}-other", root.path().display()))
        )
        .is_err());
    }

    #[test]
    fn vague_instruction_does_not_create_a_grant_but_exact_instruction_does() {
        let root = tempdir().unwrap();
        let file = root.path().join("notes.md");
        assert!(super::derive_directed_grant("随便处理一下", root.path(), &file).is_none());
        let grant = super::derive_directed_grant("修改这个文件", root.path(), &file).unwrap();
        assert_eq!(grant.path, resolve_path_scope(root.path(), &file).unwrap());
    }

    #[test]
    fn directed_grant_matches_the_complete_canonical_scope() {
        let root = tempdir().unwrap();
        let context = context(
            root.path().to_path_buf(),
            AgentPermissionMode::RequestApproval,
            ProfileBoundary::WorkspaceWrite,
        );
        let network = AgentCapability::Network {
            host: "api.example.com".to_owned(),
            port: 443,
            operation: NetworkOperation::Read,
        };
        let grant = TaskGrant {
            task_id: context.task_id,
            generation_id: context.generation_id.clone(),
            kind: network.kind(),
            path: None,
            scope: network.canonical_scope(),
            expires_at: Some(context.now + Duration::minutes(5)),
        };
        assert!(grant.matches(
            context.task_id,
            &context.generation_id,
            &network,
            context.now
        ));
        assert!(!grant.matches(
            context.task_id,
            &context.generation_id,
            &AgentCapability::Network {
                host: "other.example.com".to_owned(),
                port: 443,
                operation: NetworkOperation::Read,
            },
            context.now
        ));
    }

    #[test]
    fn private_and_metadata_network_reads_require_a_decision() {
        let root = tempdir().unwrap();
        let context = context(
            root.path().to_path_buf(),
            AgentPermissionMode::RequestApproval,
            ProfileBoundary::WorkspaceWrite,
        );
        for host in [
            "127.0.0.1",
            "10.0.0.8",
            "169.254.169.254",
            "metadata.google.internal",
        ] {
            assert!(matches!(
                evaluate(
                    &context,
                    &request(
                        &context,
                        AgentCapability::Network {
                            host: host.to_owned(),
                            port: 80,
                            operation: NetworkOperation::Read,
                        }
                    )
                ),
                Decision::RequestApproval { .. }
            ));
        }
    }

    #[test]
    fn additional_credential_paths_require_a_decision() {
        let root = tempdir().unwrap();
        let context = context(
            root.path().to_path_buf(),
            AgentPermissionMode::RequestApproval,
            ProfileBoundary::WorkspaceWrite,
        );
        for name in [
            ".netrc",
            ".kube/config",
            "id_ecdsa",
            "token.json",
            "certificate.p12",
        ] {
            let path = root.path().join(name);
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent).unwrap();
            }
            fs::write(&path, "secret").unwrap();
            assert!(matches!(
                evaluate(
                    &context,
                    &request(&context, AgentCapability::FileRead { path })
                ),
                Decision::RequestApproval { .. }
            ));
        }
    }

    #[test]
    fn generic_shell_and_interpreter_launches_are_not_a_terminal_capability() {
        let root = tempdir().unwrap();
        let workspace = root.path().join("workspace");
        fs::create_dir_all(&workspace).unwrap();
        let mut context = context(
            workspace.clone(),
            AgentPermissionMode::FullAccess,
            ProfileBoundary::WorkspaceWrite,
        );
        context.explicit_full_access = true;
        context.declared_capabilities.push(
            AgentCapability::Terminal {
                executable: "sh".to_owned(),
                args: vec!["-c".to_owned(), "cat .env".to_owned()],
                cwd: workspace.clone(),
            }
            .kind(),
        );
        assert!(matches!(
            evaluate(
                &context,
                &request(
                    &context,
                    AgentCapability::Terminal {
                        executable: "sh".to_owned(),
                        args: vec!["-c".to_owned(), "cat .env".to_owned()],
                        cwd: workspace,
                    }
                )
            ),
            Decision::Denied { .. }
        ));
    }

    #[test]
    fn process_and_terminal_launches_require_task_approved_executables() {
        let root = tempdir().unwrap();
        let executable = root.path().join("codex");
        fs::write(&executable, "placeholder").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut permissions = fs::metadata(&executable).unwrap().permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(&executable, permissions).unwrap();
        }
        let mut context = context(
            root.path().to_path_buf(),
            AgentPermissionMode::FullAccess,
            ProfileBoundary::WorkspaceWrite,
        );
        context.explicit_full_access = true;

        let process_request = request(
            &context,
            AgentCapability::ProcessLaunch {
                executable: executable.clone(),
                cwd: root.path().to_path_buf(),
            },
        );
        context
            .declared_capabilities
            .push(process_request.capability.kind());
        assert!(matches!(
            evaluate(&context, &process_request),
            Decision::Denied { .. }
        ));

        let terminal_request = request(
            &context,
            AgentCapability::Terminal {
                executable: "codex".to_owned(),
                args: vec!["--version".to_owned()],
                cwd: root.path().to_path_buf(),
            },
        );
        context
            .declared_capabilities
            .push(terminal_request.capability.kind());
        assert!(matches!(
            evaluate(&context, &terminal_request),
            Decision::Denied { .. }
        ));

        context.approved_processes.push(executable);
        context.approved_terminal_tools.push("codex".to_owned());
        assert_eq!(evaluate(&context, &process_request), Decision::AllowForTask);
        assert_eq!(
            evaluate(&context, &terminal_request),
            Decision::AllowForTask
        );
    }
}

use std::{
    fs, io,
    net::ToSocketAddrs,
    path::{Component, Path, PathBuf},
};

use chrono::Utc;

use super::model::{
    is_public_network_ip, AgentCapability, CapabilityRequest, Decision, DecisionReason,
    ProfileBoundary, TaskContext, TaskGrant, TaskLifecycle,
};

#[derive(Debug)]
pub enum PermissionError {
    InvalidPath,
    OutsideWorkspace,
    SymlinkNotAllowed,
    UnapprovedExecutable,
    NetworkAddressBlocked,
    Io(io::Error),
}

impl From<io::Error> for PermissionError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DirectedPathGrant {
    pub path: PathBuf,
}

pub fn resolve_path_scope(
    workspace_root: &Path,
    requested: &Path,
) -> Result<PathBuf, PermissionError> {
    resolve_path_scope_with_root(workspace_root, requested, false)
}

pub fn resolve_repository_scope(
    workspace_root: &Path,
    requested: &Path,
) -> Result<PathBuf, PermissionError> {
    resolve_path_scope_with_root(workspace_root, requested, true)
}

fn resolve_path_scope_with_root(
    workspace_root: &Path,
    requested: &Path,
    allow_root: bool,
) -> Result<PathBuf, PermissionError> {
    let root = fs::canonicalize(workspace_root).map_err(PermissionError::Io)?;
    let candidate = if requested.is_absolute() {
        requested.to_path_buf()
    } else {
        root.join(requested)
    };
    if candidate
        .components()
        .any(|component| component == Component::ParentDir)
    {
        return Err(PermissionError::InvalidPath);
    }
    if let Ok(relative) = candidate.strip_prefix(&root) {
        let mut current = root.clone();
        for component in relative.components() {
            if let Component::Normal(name) = component {
                current.push(name);
                if fs::symlink_metadata(&current)
                    .map(|metadata| metadata.file_type().is_symlink())
                    .unwrap_or(false)
                {
                    return Err(PermissionError::SymlinkNotAllowed);
                }
            }
        }
    }
    let resolved = if candidate.exists() {
        fs::canonicalize(&candidate).map_err(PermissionError::Io)?
    } else {
        let mut ancestor = candidate.clone();
        let mut suffix = Vec::new();
        while !ancestor.exists() {
            let Some(name) = ancestor.file_name().map(PathBuf::from) else {
                return Err(PermissionError::InvalidPath);
            };
            suffix.push(name);
            if !ancestor.pop() {
                return Err(PermissionError::InvalidPath);
            }
        }
        let mut resolved = fs::canonicalize(ancestor).map_err(PermissionError::Io)?;
        for component in suffix.iter().rev() {
            resolved.push(component);
        }
        resolved
    };
    if (!allow_root && resolved == root) || resolved.strip_prefix(&root).is_err() {
        return Err(PermissionError::OutsideWorkspace);
    }
    Ok(resolved)
}

pub fn derive_directed_grant(
    instruction: &str,
    workspace_root: &Path,
    path: &Path,
) -> Option<DirectedPathGrant> {
    if instruction.trim() != "修改这个文件" {
        return None;
    }
    resolve_path_scope(workspace_root, path)
        .ok()
        .map(|path| DirectedPathGrant { path })
}

pub fn evaluate(context: &TaskContext, request: &CapabilityRequest) -> Decision {
    evaluate_capability(context, request)
        .map(|(decision, _)| decision)
        .unwrap_or(Decision::Denied {
            reason: DecisionReason::ProductBoundary,
        })
}

pub fn validate_network_destination(capability: &AgentCapability) -> Result<(), PermissionError> {
    let AgentCapability::Network { host, port, .. } = capability else {
        return Ok(());
    };
    let addresses = (host.as_str(), *port)
        .to_socket_addrs()
        .map_err(PermissionError::Io)?
        .collect::<Vec<_>>();
    if addresses.is_empty()
        || addresses
            .iter()
            .any(|address| !is_public_network_ip(address.ip()))
    {
        return Err(PermissionError::NetworkAddressBlocked);
    }
    Ok(())
}

pub fn evaluate_capability(
    context: &TaskContext,
    request: &CapabilityRequest,
) -> Result<(Decision, AgentCapability), PermissionError> {
    if request.request_id.is_nil() || request.expires_at <= request.issued_at {
        return Ok((
            Decision::Denied {
                reason: DecisionReason::MalformedRequest,
            },
            request.capability.clone(),
        ));
    }
    if request.task_id != context.task_id {
        return Ok((
            Decision::Denied {
                reason: DecisionReason::TaskMismatch,
            },
            request.capability.clone(),
        ));
    }
    if request.generation_id != context.generation_id {
        return Ok((
            Decision::Denied {
                reason: DecisionReason::GenerationMismatch,
            },
            request.capability.clone(),
        ));
    }
    if request.issued_at > context.now || request.expires_at <= context.now {
        return Ok((
            Decision::Denied {
                reason: DecisionReason::ExpiredRequest,
            },
            request.capability.clone(),
        ));
    }
    if !request.disclosed {
        return Ok((
            Decision::Denied {
                reason: DecisionReason::UndisclosedCapability,
            },
            request.capability.clone(),
        ));
    }
    if !matches!(context.lifecycle, TaskLifecycle::Active) {
        return Ok((
            Decision::Denied {
                reason: DecisionReason::TaskNotActive,
            },
            request.capability.clone(),
        ));
    }
    let canonical_capability = canonicalize_capability(context, &request.capability)?;
    let mut canonical_request = request.clone();
    canonical_request.capability = canonical_capability.clone();
    Ok((
        evaluate_inner(context, &canonical_request),
        canonical_capability,
    ))
}

fn evaluate_inner(context: &TaskContext, request: &CapabilityRequest) -> Decision {
    if request.request_id.is_nil() || request.expires_at <= request.issued_at {
        return Decision::Denied {
            reason: DecisionReason::MalformedRequest,
        };
    }
    if request.task_id != context.task_id {
        return Decision::Denied {
            reason: DecisionReason::TaskMismatch,
        };
    }
    if request.generation_id != context.generation_id {
        return Decision::Denied {
            reason: DecisionReason::GenerationMismatch,
        };
    }
    if request.issued_at > context.now || request.expires_at <= context.now {
        return Decision::Denied {
            reason: DecisionReason::ExpiredRequest,
        };
    }
    if !request.disclosed {
        return Decision::Denied {
            reason: DecisionReason::UndisclosedCapability,
        };
    }
    if !matches!(context.lifecycle, TaskLifecycle::Active) {
        return Decision::Denied {
            reason: DecisionReason::TaskNotActive,
        };
    }
    if request.capability.is_always_denied() {
        return Decision::Denied {
            reason: DecisionReason::ProductBoundary,
        };
    }
    if matches!(request.capability, AgentCapability::Unknown { .. }) {
        return Decision::RequestApproval {
            reason: DecisionReason::ProductBoundary,
        };
    }
    if request.capability.is_mutating()
        && matches!(context.profile_boundary, ProfileBoundary::ReadOnly)
    {
        return Decision::Denied {
            reason: DecisionReason::ProfileReadOnly,
        };
    }
    if context.explicit_grants.iter().any(|grant| {
        grant.matches(
            context.task_id,
            &context.generation_id,
            &request.capability,
            context.now,
        )
    }) {
        return Decision::AllowOnce;
    }
    if request.capability.is_observation() {
        return Decision::AllowOnce;
    }
    match context.permission_mode {
        super::model::AgentPermissionMode::RequestApproval => Decision::RequestApproval {
            reason: DecisionReason::UserApprovalRequired,
        },
        super::model::AgentPermissionMode::SmartApproval => {
            if request.capability.is_smart_approval_safe() {
                Decision::AllowOnce
            } else {
                Decision::RequestApproval {
                    reason: DecisionReason::UserApprovalRequired,
                }
            }
        }
        super::model::AgentPermissionMode::FullAccess => {
            if !context.explicit_full_access {
                return Decision::RequestApproval {
                    reason: DecisionReason::UserApprovalRequired,
                };
            }
            if !context
                .declared_capabilities
                .contains(&request.capability.kind())
            {
                return Decision::RequestApproval {
                    reason: DecisionReason::CapabilityNotDeclared,
                };
            }
            Decision::AllowForTask
        }
    }
}

pub fn grant_from_directed_path(
    task_id: uuid::Uuid,
    generation_id: impl Into<String>,
    grant: DirectedPathGrant,
) -> TaskGrant {
    let scope = grant.path.display().to_string();
    TaskGrant {
        task_id,
        generation_id: generation_id.into(),
        kind: super::model::CapabilityKind::FileWrite,
        path: Some(grant.path),
        scope,
        expires_at: Some(Utc::now() + chrono::Duration::minutes(30)),
    }
}

pub fn canonicalize_capability(
    context: &TaskContext,
    capability: &AgentCapability,
) -> Result<AgentCapability, PermissionError> {
    let canonical = match capability {
        AgentCapability::FileRead { path } => AgentCapability::FileRead {
            path: resolve_path_scope(&context.workspace_root, path)?,
        },
        AgentCapability::FileWrite { path } => AgentCapability::FileWrite {
            path: resolve_path_scope(&context.workspace_root, path)?,
        },
        AgentCapability::FileDelete { path } => AgentCapability::FileDelete {
            path: resolve_path_scope(&context.workspace_root, path)?,
        },
        AgentCapability::Terminal {
            executable,
            args,
            cwd,
        } => {
            validate_terminal_executable(executable)?;
            if !context
                .approved_terminal_tools
                .iter()
                .any(|approved| approved.eq_ignore_ascii_case(executable))
            {
                return Err(PermissionError::UnapprovedExecutable);
            }
            if args.len() > 128
                || args.iter().any(|argument| {
                    argument.len() > 4_096
                        || argument.chars().any(char::is_control)
                        || argument.contains([';', '|', '&', '`', '$', '>', '<'])
                })
            {
                return Err(PermissionError::InvalidPath);
            }
            validate_terminal_arguments(executable, args)?;
            AgentCapability::Terminal {
                executable: executable.clone(),
                args: args.clone(),
                cwd: resolve_path_scope_with_root(&context.workspace_root, cwd, true)?,
            }
        }
        AgentCapability::Network {
            host,
            port,
            operation,
        } => {
            if *port == 0
                || host.is_empty()
                || host.len() > 253
                || host.chars().any(char::is_control)
                || host.contains(['@', '/', '\\'])
            {
                return Err(PermissionError::InvalidPath);
            }
            AgentCapability::Network {
                host: host.clone(),
                port: *port,
                operation: *operation,
            }
        }
        AgentCapability::ProcessLaunch { executable, cwd } => {
            let cwd = resolve_path_scope_with_root(&context.workspace_root, cwd, true)?;
            let executable = canonicalize_executable(executable, &cwd)?;
            if !context.approved_processes.iter().any(|approved| {
                fs::canonicalize(approved)
                    .map(|path| path == executable)
                    .unwrap_or(false)
            }) {
                return Err(PermissionError::UnapprovedExecutable);
            }
            AgentCapability::ProcessLaunch { executable, cwd }
        }
        AgentCapability::GitCommit { repository } => AgentCapability::GitCommit {
            repository: resolve_repository_scope(&context.workspace_root, repository)?,
        },
        AgentCapability::GitPush { repository, remote } => AgentCapability::GitPush {
            repository: resolve_repository_scope(&context.workspace_root, repository)?,
            remote: validate_bounded_text(remote)?,
        },
        AgentCapability::PackageInstall { manager, package } => AgentCapability::PackageInstall {
            manager: validate_bounded_text(manager)?,
            package: validate_bounded_text(package)?,
        },
        AgentCapability::ExternalWrite {
            service,
            action,
            target,
        } => AgentCapability::ExternalWrite {
            service: validate_bounded_text(service)?,
            action: validate_bounded_text(action)?,
            target: validate_bounded_text(target)?,
        },
        AgentCapability::Deploy { target } => AgentCapability::Deploy {
            target: validate_bounded_text(target)?,
        },
        AgentCapability::CredentialUse { credential_id } => AgentCapability::CredentialUse {
            credential_id: validate_bounded_text(credential_id)?,
        },
        AgentCapability::CredentialExport { credential_id } => AgentCapability::CredentialExport {
            credential_id: validate_bounded_text(credential_id)?,
        },
        AgentCapability::ExtensionCall {
            extension_id,
            capability,
        } => AgentCapability::ExtensionCall {
            extension_id: validate_bounded_text(extension_id)?,
            capability: validate_bounded_text(capability)?,
        },
        AgentCapability::McpCall { server_id, tool } => AgentCapability::McpCall {
            server_id: validate_bounded_text(server_id)?,
            tool: validate_bounded_text(tool)?,
        },
        AgentCapability::AuditDisable => AgentCapability::AuditDisable,
        AgentCapability::BridgeBypass => AgentCapability::BridgeBypass,
        AgentCapability::Unknown { name } => AgentCapability::Unknown {
            name: validate_bounded_text(name)?,
        },
    };
    Ok(canonical)
}

fn canonicalize_executable(path: &Path, cwd: &Path) -> Result<PathBuf, PermissionError> {
    let candidate = if path.is_absolute() {
        path.to_path_buf()
    } else {
        cwd.join(path)
    };
    let Some(name) = candidate.file_name().and_then(|name| name.to_str()) else {
        return Err(PermissionError::InvalidPath);
    };
    if !is_allowlisted_tool(name) {
        return Err(PermissionError::InvalidPath);
    }
    let metadata = fs::symlink_metadata(&candidate).map_err(PermissionError::Io)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(PermissionError::SymlinkNotAllowed);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o111 == 0 {
            return Err(PermissionError::InvalidPath);
        }
    }
    fs::canonicalize(candidate).map_err(PermissionError::Io)
}

fn validate_terminal_executable(value: &str) -> Result<(), PermissionError> {
    if value.is_empty()
        || value.len() > 256
        || value.chars().any(char::is_control)
        || value.contains(['/', '\\', ';', '|', '&', '`', '$'])
        || !is_allowlisted_tool(value)
        || matches!(
            value.to_ascii_lowercase().as_str(),
            "sh" | "bash"
                | "zsh"
                | "fish"
                | "dash"
                | "cmd"
                | "cmd.exe"
                | "powershell"
                | "powershell.exe"
                | "pwsh"
                | "pwsh.exe"
                | "python"
                | "python3"
                | "node"
                | "deno"
                | "bun"
        )
    {
        Err(PermissionError::InvalidPath)
    } else {
        Ok(())
    }
}

fn validate_terminal_arguments(executable: &str, args: &[String]) -> Result<(), PermissionError> {
    let normalized = executable.to_ascii_lowercase();
    let first = args.first().map(|argument| argument.to_ascii_lowercase());
    let forbidden = match normalized.as_str() {
        "git" => matches!(
            first.as_deref(),
            Some("push" | "pull" | "fetch" | "clone" | "remote" | "credential")
        ),
        "npm" | "pnpm" | "yarn" => matches!(
            first.as_deref(),
            Some("run" | "exec" | "dlx" | "install" | "add" | "remove" | "uninstall")
        ),
        "cargo" => matches!(first.as_deref(), Some("install" | "run" | "publish")),
        _ => false,
    } || args.iter().any(|argument| {
        matches!(
            argument.to_ascii_lowercase().as_str(),
            "-c" | "--command" | "-e" | "--eval" | "--exec" | "--require"
        )
    });
    if forbidden {
        Err(PermissionError::InvalidPath)
    } else {
        Ok(())
    }
}

fn is_allowlisted_tool(value: &str) -> bool {
    let name = value
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or(value)
        .trim_end_matches(".exe")
        .to_ascii_lowercase();
    matches!(
        name.as_str(),
        "git"
            | "npm"
            | "pnpm"
            | "yarn"
            | "cargo"
            | "rustc"
            | "go"
            | "java"
            | "swift"
            | "dotnet"
            | "codex"
            | "claude"
    )
}

fn validate_bounded_text(value: &str) -> Result<String, PermissionError> {
    if value.is_empty() || value.len() > 512 || value.chars().any(char::is_control) {
        return Err(PermissionError::InvalidPath);
    }
    Ok(value.to_owned())
}
