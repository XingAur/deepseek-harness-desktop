---
name: his-database-change
description: Use when a HIS schema or data change needs a static impact, safety, rollback, and approval review.
---

# HIS Database Change

This skill is plan only. Use `database.change-plan` to review a proposed schema
or data change without loading a credential, creating a database connection, or
attempting to execute SQL. The result always has `changed=false`.

## Static change plan

Submit the proposed DDL/DML and migration description as `preview`, `L0`, with
no authorization. The capability only parses statement types and affected
objects and returns a frozen review plan.

```json
{
  "schema_version": "his-capability-request.v1",
  "request_id": "database-change-plan-1",
  "capability": "database.change-plan",
  "provider": "postgresql",
  "mode": "preview",
  "mutation_level": "L0",
  "authorization": {"explicit": false, "scope": []},
  "input": {
    "environment": "test",
    "objective": "新增配置字段并迁移历史数据",
    "statements": [
      "ALTER TABLE his_test.his_config ADD COLUMN new_value varchar(32)",
      "UPDATE his_test.his_config SET new_value = value WHERE new_value IS NULL"
    ],
    "migration_description": "迁移历史配置到新字段",
    "transaction_strategy": "单事务，任一步失败则整体回滚",
    "rollback_strategy": "删除新增字段并从备份恢复",
    "backup_confirmed": true,
    "validation_queries": [
      "SELECT count(*) FROM his_test.his_config WHERE new_value IS NULL"
    ]
  },
  "context": {}
}
```

Review all of these outputs before calling a plan complete:

- DDL/DML statement types and affected objects.
- Whether a data migration is required.
- Transaction strategy and rollback strategy.
- Recoverable backup confirmation.
- Readonly validation queries and expected results.
- Required application approval and every blocker.

Missing SQL, transaction, rollback, backup, or validation evidence keeps the
plan blocked. Do not invent the missing safety control or reinterpret a blocked
plan as permission to execute.

## High-risk review

A `production` target is outside the first-version boundary and requires an
independent approval path that this skill does not provide. Any 医保 or 收费
change must remain blocked until its adjacent paths, amount/state semantics,
historical compatibility, rollback, and runtime validation have been reviewed.
A static plan is not production approval and is not runtime proof.

## Disabled apply boundary

`database.change` is declared `L5` and `enabled=false`. It has no executable
manifest entrypoint. Neither an `approved=true` input, explicit user wording,
write credential, deadline, nor a completed plan can enable it.

When a real apply is requested, stop with this exact reason:

> 真实数据库变更能力未启用；本次仅生成变更计划，未连接数据库。

Do not run `psql`, database clients, migration frameworks, shell commands,
drivers, stored procedures, DDL, DML, or validation queries as a fallback.
Return the static plan and blockers only.
