---
name: yunxiao-workitem-read
description: Read Aliyun DevOps Yunxiao work item details, comments, inline files, and attachments with the read-only credential class. Use for requirement or bug evidence lookup. Never writes comments, transitions, assignees, iterations, or attachments.
---

# Yunxiao Work Item Read

Use this skill to collect requirement or bug evidence without changing a Yunxiao work item.

Skill is the manual; MCP is the connector that performs the Yunxiao read. The
Skill never opens a network connection or reads a credential value. Yunxiao
reads do not require Harness human confirmation; Harness still validates the
exact work-item target, page budget, evidence scope, and audit metadata.

## When to use

Use it when a governed task has an exact Yunxiao work-item ID or URL and needs requirement, bug, comment, inline-file, attachment, or parent-relation evidence before analysis or implementation. If the target cannot be identified exactly, stop and request the missing ID or URL.

## Read-only contract

- Select only the `yunxiao_read` credential class. The legacy keys `aliyun_devops_pat` and `aliyun_devops_organization_id` are credential lookup handles only; never reveal their values.
- Never load or fall back to `aliyun_devops_write_pat`.
- Invoke `workitem.read` only in `preview` mode at mutation level `L1`; all Yunxiao requests must be `GET` behavior.
- Collect details, body, comments, inline files, and attachments. Do not comment, edit, transition, assign, change iteration, or upload.

## Invocation

- Call the semantic capability `workitem.read`; the MCP connection resolves credentials and performs the external read.
- MCP failure must not fall back to a Provider, browser, direct client, write credential, or another token.
- Start with the work-item body and a bounded page. Include comments and file metadata only when the task needs them.
- Follow pagination only while the missing evidence can change the decision. Do not repeatedly fetch an unchanged page.
- Stop on invalid input, unavailable credentials, primary-item read failure, or an evidence decision gate that requires requirement confirmation.

## Evidence handoff

Return the source identity, freshness, evidence reference, decision gate and its reasons. Clearly distinguish missing evidence from runtime truth. Evidence can support analysis of the requirement; it does not prove a production or runtime result.

## Token discipline

- Reuse the returned evidence reference and normalized fields instead of replaying the full work item.
- Prefer the smallest page and omit comments or file metadata when they cannot affect the current decision.
- Persist only stable conclusions and evidence identifiers in task context; refetch volatile details when needed.
