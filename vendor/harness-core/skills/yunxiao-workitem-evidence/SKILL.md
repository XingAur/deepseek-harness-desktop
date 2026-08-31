---
name: yunxiao-workitem-evidence
description: Use when maintaining existing automation or old commands that still reference yunxiao-workitem-evidence. This legacy compatibility path is not for new Yunxiao evidence work; use yunxiao-workitem-read instead.
---

# Yunxiao Work Item Evidence

这是一个兼容入口；新工作必须使用 `$yunxiao-workitem-read`。保留本技能仅为了让已有自动化和旧命令继续收集只读证据。

## Compatibility workflow

The legacy CLI remains available at its old path:

```bash
python3 "<skill-dir>/scripts/collect_evidence.py" "<yunxiao-url-or-id>" \
  --output-dir "<evidence-output-dir>"
```

Use a new empty output directory. Read `requirement_evidence.v2.json` first and preserve its JSON, Markdown, and downloaded files as one evidence revision. The decision gate remains authoritative: `ready_for_analysis` permits analysis, `needs_requirement_confirmation` limits work to read-only investigation, and `fetch_failed` stops retrieval-dependent work.

## Safety Boundary

- This entry and its delegated plugin are GET-only and make no Yunxiao mutations: never comment, upload, assign, transition, update, create, delete, or close.
- Never print, persist, summarize, or forward token values.
- Never send the Yunxiao token to attachment download hosts.
- Send the token only to the official HTTPS OpenAPI host; reject API redirects and untrusted base URLs.
- Do not treat code-level support as runtime or production proof.
- Do not begin code changes when original evidence is inaccessible or materially ambiguous.

## Credentials

The default credential kind is read. Use `ALIYUN_DEVOPS_PAT` and `ALIYUN_DEVOPS_ORGANIZATION_ID` (or compatible lowercase lookup keys) and never expose their values. Any intent to change a Yunxiao work item belongs to `$yunxiao-workitem-write`; holding a write credential is not authorization.

## References

- Read [references/evidence-contract.md](references/evidence-contract.md) for gate and completeness semantics.
- Read [references/yunxiao-openapi.md](references/yunxiao-openapi.md) when endpoints or permissions need diagnosis.
- Validate saved JSON with `python3 "<skill-dir>/scripts/validate_evidence.py" <path>`.
