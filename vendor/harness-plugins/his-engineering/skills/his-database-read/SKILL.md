---
name: his-database-read
description: Use when runtime or test database evidence is needed because code and Yunxiao evidence cannot confirm a HIS table structure, relationship, configuration, or data-state boundary.
---

# HIS Database Read

Skill is the manual; MCP is the connector that performs the database read. The
Skill chooses `database.inspect` and explains how to interpret evidence. It
never loads a driver, opens a socket, reads a DSN, or receives a password.

Database reads do not require Harness human confirmation. The configured
readonly database address and readonly credential decide whether the technical
connection can succeed. Harness governs only the named target, metadata
operation, schema/table scope, timeout, row budget, redaction, evidence, and
audit.

## Supported evidence

The frozen PostgreSQL MCP connector accepts only these catalog operations:

- `schemas`: list non-system schemas.
- `tables`: list tables in one schema or across non-system schemas.
- `columns`: inspect the ordered column structure of one table.
- `constraints`: inspect primary, unique, check, and other table constraints.
- `indexes`: inspect indexes of one table.
- `foreign_keys`: inspect the table relationship from one table to referenced
  tables and columns.

Credential values never appear in Skill arguments. The argument contains only
a configured alias ending in `_readonly`; MCP resolves the corresponding
readonly address, user, and password internally after input validation.

```json
{
  "schema_version": "his-capability-request.v1",
  "request_id": "database-foreign-keys-1",
  "capability": "database.inspect",
  "provider": "postgresql",
  "mode": "preview",
  "mutation_level": "L1",
  "authorization": {"explicit": false, "scope": ["database:inspect"]},
  "input": {
    "connection_alias": "his_test_readonly",
    "operation": "foreign_keys",
    "schema": "public",
    "table": "orders"
  },
  "context": {}
}
```

The scope is a machine-policy scope, not a human confirmation. A successful
result remains `changed=false` and becomes evidence for the named database
alias only. It does not prove another environment has the same schema or data.

## Absolute mutation boundary

Database write, delete, DDL, migration, and privilege changes are unavailable
in this connector. It exposes no arbitrary statement argument, DML, transaction
control, lock, procedure, COPY, notification, schema-change, or data-migration
tool. The connector starts a readonly transaction and executes only a frozen,
parameterized catalog query selected by the operation enum.

If a future task truly requires database mutation, it must use a separately
designed write MCP connector, an exact target and statement/change set, the
current user's explicit authorization, backup and recovery evidence, and
post-change verification. None of those permissions can be inferred from this
read Skill, a credential, a ticket, or a previous confirmation.

MCP failure must not fall back to a Provider, direct driver, shell client, or
another credential. Report the blocker and stop; never widen the alias, schema,
table, operation, or environment.

## Project-context and token discipline

- Before querying, use the project context pack to identify the relevant
  service, module, entity, mapper, migration, expected schema, and candidate
  table relationship. Do not scan the database blindly.
- Prefer `columns`, `constraints`, `indexes`, and `foreign_keys` for the exact
  candidate table instead of repeatedly listing every schema and table.
- Reuse the evidence reference and normalized schema snapshot in later stages.
  Refetch only when freshness or a changed target can affect the decision.
- Treat missing, blocked, unavailable, timeout, and failed results as missing
  evidence, never as proof that a column, table, relationship, or data state
  does not exist.
