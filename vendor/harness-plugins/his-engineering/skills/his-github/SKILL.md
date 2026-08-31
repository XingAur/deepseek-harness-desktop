---
name: his-github
description: Use when bounded GitHub repository or pull-request evidence is needed, or when a GitHub PR mutation boundary must be checked.
---

# HIS GitHub

Use `github.read` as `preview`, `L1`, with no explicit authorization. The
formal plugin entrypoint accepts injected, read-only transports and never loads
credentials or opens a network connection itself.

The Manager Provider is fixed to `https://api.github.com`. It supports
repository, issue, pull-request, repository-file, commit, commit-diff, compare,
pull-request commits/files, and Actions run jobs. Code and diff bodies are
caller-only ephemeral responses; durable audits retain only bounded metadata,
hashes, counts, truncation state, and dispatch evidence.

## Write boundary

`github.write` is an enabled, allowlisted `L4` capability, but it is not
standing authorization. GitHub PR creation and PR comments are wired into the
same immutable delivery state machine as GitLab. The current user must request
a concrete remote delivery for the current task, and the plan must bind the
exact `github.com` owner, repository, action and parameters before a credential
is resolved.

Only pull-request creation and a comment on an exact pull-request number are
supported. The Manager plan is confirmed and consumed once; redirects and
environment proxies are disabled. The Harness accepts success only after an
exact verified read-back receipt matches the plan target. GitHub and GitLab
writes cannot coexist in one immutable delivery plan.

Push is separately governed by plan-declared `git.push`. Merge, approval,
review submission, edit/close/reopen, Actions retry/cancel, upload and every
other GitHub mutation remain blocked. A ticket, credential, old confirmation,
free-form input, or a plan for another target cannot authorize or widen the
action.

## Stop conditions

- Reject configurable hosts, sensitive repository paths, malformed refs, short
  commit SHAs, oversized responses, and incomplete pagination.
- Never fall back to browser scraping, another provider, an arbitrary token, or
  direct HTTP when the governed Provider path is unavailable.
- Read evidence supports analysis; it does not prove production behavior.
- Stop on a missing concrete user delivery request, target drift, consumed or
  expired confirmation, plan-hash mismatch, provider mismatch, or unverified
  read-back.
