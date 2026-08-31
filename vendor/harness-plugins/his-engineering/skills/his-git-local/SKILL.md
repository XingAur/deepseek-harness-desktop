---
name: his-git-local
description: Use when inspecting or safely applying a local Git patch.
---

# HIS Git Local

Use the registered `his-engineering` local capability boundary. Treat a request
as local-only unless the user separately authorizes a different capability.

## Quick reference

| Need | Capability and contract | Boundary |
| --- | --- | --- |
| Repository status evidence | `git.inspect`, `preview`, `L0`, no explicit authorization; input is an absolute local project path | Returns a bounded repository snapshot and never mutates the repository. |
| Apply a reviewed patch | `git.apply-local`, `apply`, `L2`, exact explicit `repository:apply-local` authorization | Requires `expected_diff`, `allowed_paths`, and targeted `verify_commands`. |

Current registered boundary: `git.inspect` returns only a bounded status
snapshot; it does not return a full diff or history. When a request needs
status + full diff + history and the user has not directly provided the
diff/history evidence, report `unsupported` and stop. Do not call direct shell
Git, contact a remote, or use browser/provider fallback. Only user-provided
read-only evidence, or a separately registered and approved future read
capability, can supply that missing detail.

User-provided diff/history is read-only evidence for analysis; it does not
authorize apply, commit, or push.

## Inspect first

Use `git.inspect` for an absolute local project path. Read the returned root,
branch, head, status entries, active operation markers, and warnings before
proposing a patch. A non-Git directory, unsafe repository state, or unavailable
inspection must fail closed; report the blocker rather than substituting shell
commands or another provider.

## Apply only with exact authority

Apply only after the user explicitly asks for this local patch and the request
contains the exact `repository:apply-local` scope. Supply the complete expected
diff, a narrow `allowed_paths` list, and verification commands that test only
the requested change. Preserve unrelated dirty changes. Stop and report rather
than widen scope when repository drift, unsafe paths, unsupported verification,
or non-Git directories are reported.

```json
{
  "schema_version": "his-capability-request.v1",
  "request_id": "inspect-local-1",
  "capability": "git.inspect",
  "provider": "his-engineering",
  "mode": "preview",
  "mutation_level": "L0",
  "authorization": {"explicit": false, "scope": []},
  "input": {"project_path": "/absolute/local/repository"},
  "context": {}
}
```

## Do not extend authorization

Inspect or apply does not authorize branch, commit, push, PR, merge, reset,
clean, deletion, or cleanup of unrelated dirty files. Do not create a branch,
commit a patch, or contact a remote as a follow-up convenience. Those actions
need their own capability and explicit user authority.

## Common errors

- **Need the actual patch/history:** request separate read-only evidence; do
  not claim `git.inspect` returned a full patch or log.
- **Drift or unexpected dirty files:** preserve them and fail closed; ask for a
  narrower approved diff or a fresh inspection.
- **Apply request lacks scope or allowlist:** return the missing boundary; do
  not repair the request by guessing paths or authorization.
