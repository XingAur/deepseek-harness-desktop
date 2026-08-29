---
name: harness-visual-evidence
description: Extract only visible facts from an archived requirement screenshot before technical discovery.
---

# Harness visual evidence

Use only after Yunxiao has archived a local image and before project selection,
call-chain tracing, or code modification.

1. Pass the local archived image, bounded title, and bounded description to an
   explicitly declared host visual adapter.
2. Extract only screenshot-visible `error_text`, `menu`, `action`, and
   `business_scene`; `target_module` is optional.
3. If any required fact is absent, the adapter is unavailable, or the response
   is malformed, leave the visual gate blocked. Do not infer facts from ticket
   background, comments, filenames, or code search.
4. This capability is local/read-only. It does not fetch Yunxiao, invoke MCP,
   choose a model, upload an image, write a work item, or authorize code edits.
