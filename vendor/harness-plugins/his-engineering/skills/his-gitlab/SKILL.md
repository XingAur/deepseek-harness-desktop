---
name: his-gitlab
description: Use when reading bounded GitLab project, repository-file, commit, or merge-request evidence through the frozen readonly MCP connector.
---

# HIS GitLab

Skill is the manual; MCP is the connector that performs the GitLab read. The
Skill selects the semantic capability and explains its boundaries. It never
opens a network connection, reads a token, or executes a direct GitLab client.

GitLab reads do not require Harness human confirmation. Harness still validates
the exact project, operation, ref, path or object identity, applies the result
budget, and records audit and evidence references. Use `gitlab.read` as
`preview`, `L1`, with `authorization` `{"explicit": false, "scope":
["gitlab:read"]}`.

## Quick reference

| Need | Capability and contract | Boundary |
| --- | --- | --- |
| Project, repository-file, commit, or merge-request evidence | `gitlab.read`, `preview`, `L1`, no Harness human confirmation; frozen MCP server `gitlab` | MCP performs one bounded GET-only external read and returns validated evidence. |
| Repository and commit code evidence | MCP operation `repository_file` or `commit` with exact project/ref/path/object identity | Complete payloads remain bounded evidence and sensitive credential-like paths are rejected. |
| GitLab mutation | Not exposed by this readonly MCP connector | Requires a separately reviewed write MCP capability and exact current-user authorization; no Provider fallback. |

## Read invocation

Supported operations are `project`, `repository_file`, `commit`, and
`merge_request`. MCP resolves the configured GitLab base URL and personal
access token after target validation. Credential values never appear in Skill
arguments, evidence, audit, or user-visible errors.

```json
{
  "schema_version": "his-capability-request.v1",
  "request_id": "gitlab-project-1",
  "capability": "gitlab.read",
  "provider": "gitlab",
  "mode": "preview",
  "mutation_level": "L1",
  "authorization": {"explicit": false, "scope": ["gitlab:read"]},
  "input": {
    "project": "group/his",
    "operation": "project",
    "ref": "",
    "path": "",
    "object_id": ""
  },
  "context": {}
}
```

Repository paths, refs, commit identities, and merge-request IIDs must pass the
exact action grammar before credential resolution. Sensitive credential-like
file names are blocked. Responses are bounded, untrusted, read-only evidence;
they grant no authorization and do not prove runtime or production truth.

## Failure and mutation boundary

MCP failure must not fall back to a Provider, browser, direct client, or another
token. Report the stable blocker and stop; do not widen the host, project,
operation, credential, or result limit.

This readonly MCP connector exposes no GitLab mutation tool. A future GitLab
write requires a separately reviewed write MCP capability, exact current-user
authorization, immutable target, and read-back verification. Existing legacy
Provider code is rollback compatibility only and is never selected
automatically after an MCP error.

## Token discipline

- Reuse the MCP evidence reference and normalized source identity instead of
  replaying complete source or diff content.
- Request one exact file, commit, or merge request at a time.
- Load full content only when it can change the current architecture, defect,
  review, or delivery decision.
