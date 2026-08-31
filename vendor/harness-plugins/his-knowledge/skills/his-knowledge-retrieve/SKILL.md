---
name: his-knowledge-retrieve
description: Use when a user explicitly asks to look up HIS knowledge, history, rules, code paths, or support boundaries and needs scoped evidence rather than a change.
---

# HIS Knowledge Retrieve

Use `knowledge.retrieve` with provider `his-knowledge` in preview/L0 mode.
When the installed `his-knowledge` MCP is available, prefer its
`knowledge_search`, `knowledge_get`, `knowledge_related`, and `knowledge_health`
tools for the equivalent read-only lookup. The MCP is not a maintenance path.

## Boundary

- Stay read-only: preview/L0 allows no persistent or external write; never
  create, import, review, or promote knowledge.
- Use this skill only for an explicit lookup. If materially different results need hospital, region, module, repository, or branch scope, request that material scope.
- Never fall back to direct Yunxiao, Git, database, network, or shell actions.

## Response contract

Surface each result's authority, scope, freshness, conflict state, source refs,
backend, and score breakdown. Treat stale-only or conflicting evidence as
non-answerable; say what scope or verified evidence is missing.

Keep these support claims separate: code support, runtime support,
database/config support, and production truth. Retrieved history is not proof
of current runtime or production state.
