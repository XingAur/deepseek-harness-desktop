---
name: yunxiao-workitem-write
description: "Use only when a user explicitly requests a concrete Yunxiao mutation: comment, transition, assignment, iteration change, or upload."
---

# Yunxiao Work Item Write

Use this skill only when explicit user intent names a concrete Yunxiao mutation. Explicit user intent is required even to prepare a write plan.

## Current boundary

- The default and only current action is a non-executing `preview`.
- `workitem.write` is currently `enabled=false`; no apply operation exists in this version.
- A stored `yunxiao_write` PAT proves credential availability, not authorization. A read PAT must never be used for writes.
- User confirmation does not enable execution or bypass the capability registry.

## Preview result

Prepare a preview that states the requested operation, target, and required authorization scope. Stop with the manifest `disabled_reason`: `首版仅交付独立写技能与授权边界，不开放真实云效写入。` A future apply would still require exact operation/scope authorization and revalidation; this version cannot execute it.
