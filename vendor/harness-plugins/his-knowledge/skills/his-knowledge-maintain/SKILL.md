---
name: his-knowledge-maintain
description: Use when the current user explicitly asks to remember, record, update, or bootstrap HIS knowledge, or an authorized governance workflow asks to create a review candidate.
---

# HIS Knowledge Maintain

Use only these local L2 apply capabilities and exact scopes:

- `knowledge.candidate.create` — `knowledge:candidate:create`
- `knowledge.candidate.review` — `knowledge:candidate:review`
- `knowledge.item.promote` — `knowledge:item:promote`

## Boundary

- Default candidate-first: use `knowledge.candidate.create`; never write formal
  knowledge directly.
- Keep create, separate review, and separate promotion as distinct operations.
  Candidate creation or an earlier user request is not review approval.
- Promotion requires an approved candidate plus explicit source refs and scope.
- Before persistence, reject a secret, token, password, DSN, patient identifier,
  phone number, or copied sensitive audit text.
- personal memory is opt-in and never has higher authority than verified evidence.
- Never call Yunxiao, Git, an external database, network, or a real plugin path.

## Bootstrap without an active requirement

- An active requirement is not required. When the user explicitly asks to
  initialize or grow the knowledge base, bootstrap from approved local
  evidence instead of waiting for a new work item.
- Allowed source classes are local documents, source repositories, and audit history
  already available to the current task. Reading them is performed by
  the owning read-only tool or Harness skill; this maintenance skill receives
  only bounded, cited candidate payloads.
- Bootstrap is candidate creation only. Create one independently reviewable
  claim per candidate, with source refs, scope, authority, and freshness; do
  not batch unrelated claims or treat extraction as proof.
- Duplicate or conflicting claims remain candidates for review. Never promote
  them automatically, even when the same text appears in several sources.
- If a source is unavailable, sensitive, stale, or cannot be cited precisely,
  report the source gap and continue with the remaining safe sources.

## Response contract

Report only IDs, status, content hash, and the logical local SQLite path. Do
not echo the candidate body or reviewer reason.
