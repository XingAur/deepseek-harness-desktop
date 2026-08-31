---
name: his-knowledge-answer
description: Use when a user asks an ordinary HIS question or wants a customer-service-style answer, but is not asking to implement or change code, data, or configuration.
---

# HIS Knowledge Answer

Use `knowledge.answer` with provider `his-knowledge` in preview/L0 mode for
ordinary, customer-service-style questions only.

Preview/L0 allows no persistent or external write.

## Boundary

- Route any implementation or change request out of answer mode to the suggested
  `harness.task`; do not perform the change.
- For current Yunxiao, production, runtime, or database facts, suggest only
  `workitem.read` or `database.inspect` as applicable; never execute them.
- Do not claim universal coverage. Historical knowledge is not current Yunxiao
  evidence or production certainty.

## Response contract

Preserve these five statuses exactly: `answered`, `needs_clarification`,
`needs_live_evidence`, `conflicted`, and `unsupported`.

Lead with a conclusion only for `answered` and answerable evidence. Otherwise
explain the missing material scope or evidence and return only the appropriate
suggestion; do not imply that the suggestion has run.
