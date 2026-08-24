use rusqlite::{Connection, Transaction, TransactionBehavior};

pub const CURRENT_SCHEMA_VERSION: i64 = 3;

const V1_SCHEMA: &str = r#"
CREATE TABLE agents (
    agent_id TEXT PRIMARY KEY,
    provider_id TEXT,
    adapter_kind TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (provider_id) REFERENCES providers(provider_id)
);

CREATE TABLE providers (
    provider_id TEXT PRIMARY KEY,
    provider_kind TEXT NOT NULL,
    display_name TEXT NOT NULL,
    credential_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (credential_id) REFERENCES credential_metadata(credential_id)
);

CREATE TABLE credential_metadata (
    credential_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_verified_at TEXT
);

CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    workspace_path TEXT NOT NULL,
    permission_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);

CREATE TABLE worker_sessions (
    task_id TEXT PRIMARY KEY,
    worker_session_id TEXT NOT NULL UNIQUE,
    adapter_kind TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE TABLE content_references (
    content_ref_id TEXT PRIMARY KEY,
    content_kind TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE event_checkpoints (
    task_id TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL CHECK (last_sequence >= 0),
    last_event_kind TEXT NOT NULL,
    content_ref_id TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
    FOREIGN KEY (content_ref_id) REFERENCES content_references(content_ref_id)
);

CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    capability_kind TEXT NOT NULL,
    canonical_scope TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE TABLE task_grants (
    grant_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    capability_kind TEXT NOT NULL,
    canonical_scope TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, capability_kind, canonical_scope),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE TABLE extensions (
    extension_id TEXT PRIMARY KEY,
    extension_kind TEXT NOT NULL,
    display_name TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE extension_versions (
    extension_id TEXT NOT NULL,
    version TEXT NOT NULL,
    integrity_sha256 TEXT NOT NULL,
    manifest_ref_id TEXT,
    installed_at TEXT NOT NULL,
    PRIMARY KEY (extension_id, version),
    FOREIGN KEY (extension_id) REFERENCES extensions(extension_id) ON DELETE CASCADE,
    FOREIGN KEY (manifest_ref_id) REFERENCES content_references(content_ref_id)
);

CREATE TABLE compatibility_results (
    result_id TEXT PRIMARY KEY,
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    subject_version TEXT NOT NULL,
    app_version TEXT NOT NULL,
    runtime_version TEXT,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    UNIQUE (subject_kind, subject_id, subject_version, app_version, runtime_version)
);

CREATE TABLE audit_summaries (
    audit_id TEXT PRIMARY KEY,
    task_id TEXT,
    event_kind TEXT NOT NULL,
    capability_kind TEXT,
    canonical_scope TEXT,
    result_category TEXT NOT NULL,
    error_code TEXT,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE SET NULL
);
"#;

pub fn user_version(connection: &Connection) -> rusqlite::Result<i64> {
    connection.query_row("PRAGMA user_version", [], |row| row.get(0))
}

pub fn validate_current_schema(connection: &Connection) -> rusqlite::Result<()> {
    const REQUIRED_COLUMNS: &[(&str, &[&str])] = &[
        (
            "approvals",
            &[
                "approval_id",
                "request_id",
                "generation_id",
                "risk_class",
                "policy_version",
                "decision",
                "result_category",
                "error_code",
                "expires_at",
            ],
        ),
        (
            "task_grants",
            &[
                "grant_id",
                "task_id",
                "generation_id",
                "capability_kind",
                "canonical_scope",
                "policy_version",
                "expires_at",
            ],
        ),
        (
            "audit_summaries",
            &[
                "audit_id",
                "task_id",
                "request_id",
                "generation_id",
                "risk_class",
                "policy_version",
                "decision",
                "canonical_scope",
                "error_code",
            ],
        ),
    ];
    for (table, columns) in REQUIRED_COLUMNS {
        let actual = connection
            .prepare(&format!("PRAGMA table_info({table})"))?
            .query_map([], |row| row.get::<_, String>(1))?
            .collect::<Result<std::collections::HashSet<_>, _>>()?;
        if columns.iter().any(|column| !actual.contains(*column)) {
            return Err(rusqlite::Error::InvalidQuery);
        }
    }
    let index_exists: bool = connection.query_row(
        "SELECT EXISTS (
             SELECT 1 FROM sqlite_master
             WHERE type = 'index' AND name = 'idx_approvals_task_request_generation'
         )",
        [],
        |row| row.get(0),
    )?;
    if !index_exists {
        return Err(rusqlite::Error::InvalidQuery);
    }
    Ok(())
}

pub fn migrate_to_current(connection: &mut Connection) -> rusqlite::Result<()> {
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Exclusive)?;
    migrate_to_current_in_transaction(&transaction)?;
    transaction.commit()
}

pub fn migrate_to_current_in_transaction(transaction: &Transaction<'_>) -> rusqlite::Result<()> {
    let version = user_version(transaction)?;
    match version {
        0 => transaction.execute_batch(V1_SCHEMA)?,
        1 | 2 => {}
        CURRENT_SCHEMA_VERSION => return Ok(()),
        _ => return Err(rusqlite::Error::InvalidQuery),
    }
    if version <= 1 {
        transaction.execute_batch(V2_SCHEMA)?;
    }
    if version <= 2 {
        transaction.execute_batch(V3_SCHEMA)?;
    }
    transaction.pragma_update(None, "user_version", CURRENT_SCHEMA_VERSION)?;
    Ok(())
}

const V2_SCHEMA: &str = r#"
ALTER TABLE approvals ADD COLUMN request_id TEXT NOT NULL DEFAULT '';
ALTER TABLE approvals ADD COLUMN generation_id TEXT NOT NULL DEFAULT '';
ALTER TABLE approvals ADD COLUMN risk_class TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE approvals ADD COLUMN policy_version TEXT NOT NULL DEFAULT 'agent-policy-v1';
ALTER TABLE approvals ADD COLUMN decision TEXT;
ALTER TABLE approvals ADD COLUMN result_category TEXT;
ALTER TABLE approvals ADD COLUMN error_code TEXT;
ALTER TABLE approvals ADD COLUMN expires_at TEXT NOT NULL DEFAULT '';
UPDATE approvals SET request_id = approval_id WHERE request_id = '';
UPDATE approvals SET generation_id = 'legacy' WHERE generation_id = '';
UPDATE approvals SET expires_at = requested_at WHERE expires_at = '';
CREATE UNIQUE INDEX idx_approvals_task_request_generation
    ON approvals(task_id, request_id, generation_id);

ALTER TABLE audit_summaries ADD COLUMN request_id TEXT;
ALTER TABLE audit_summaries ADD COLUMN generation_id TEXT;
ALTER TABLE audit_summaries ADD COLUMN risk_class TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE audit_summaries ADD COLUMN policy_version TEXT NOT NULL DEFAULT 'agent-policy-v1';
ALTER TABLE audit_summaries ADD COLUMN decision TEXT;
INSERT INTO audit_summaries (
    audit_id, task_id, request_id, generation_id, event_kind,
    capability_kind, canonical_scope, risk_class, policy_version,
    decision, result_category, error_code, occurred_at
)
SELECT approval_id || ':legacy-reauthorization', task_id, request_id, generation_id,
       'legacy-approval-invalidated', capability_kind, 'legacy-redacted', risk_class,
       policy_version, 'deny', 'legacy-requires-reauthorization',
       'legacy-generation', requested_at
FROM approvals
WHERE generation_id = 'legacy' AND status IN ('pending', 'approved-once', 'approved-for-task');
UPDATE approvals
SET status = 'cancelled',
    result_category = 'legacy-requires-reauthorization',
    error_code = 'legacy-generation'
WHERE generation_id = 'legacy' AND status IN ('pending', 'approved-once', 'approved-for-task');

INSERT INTO audit_summaries (
    audit_id, task_id, request_id, generation_id, event_kind,
    capability_kind, canonical_scope, risk_class, policy_version,
    decision, result_category, error_code, occurred_at
)
SELECT grant_id || ':legacy-reauthorization', task_id, NULL, 'legacy',
       'legacy-grant-invalidated', capability_kind, 'legacy-redacted', 'unknown',
       'agent-policy-v1', 'deny', 'legacy-requires-reauthorization',
       'legacy-generation', created_at
FROM task_grants;

ALTER TABLE task_grants RENAME TO task_grants_v1;
CREATE TABLE task_grants (
    grant_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    capability_kind TEXT NOT NULL,
    canonical_scope TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, generation_id, capability_kind, canonical_scope),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);
INSERT INTO task_grants (
    grant_id, task_id, generation_id, capability_kind, canonical_scope,
    policy_version, expires_at, created_at
)
SELECT grant_id, task_id,
       'legacy',
       capability_kind, canonical_scope,
       'agent-policy-v1', expires_at, created_at
FROM task_grants_v1;
DROP TABLE task_grants_v1;
UPDATE task_grants
SET generation_id = 'legacy',
    expires_at = '1970-01-01T00:00:00Z';
"#;

const V3_SCHEMA: &str = r#"
INSERT INTO audit_summaries (
    audit_id, task_id, request_id, generation_id, event_kind,
    capability_kind, canonical_scope, risk_class, policy_version,
    decision, result_category, error_code, occurred_at
)
SELECT approval_id || ':schema-v3-reauthorization', task_id, request_id, 'legacy',
       'approval-invalidated-on-schema-upgrade', capability_kind, 'legacy-redacted',
       risk_class, policy_version, 'deny', 'schema-upgrade-requires-reauthorization',
       'schema-v3-legacy-generation', requested_at
FROM approvals
WHERE status IN ('pending', 'approved-once', 'approved-for-task');

UPDATE approvals
SET generation_id = 'legacy',
    status = 'cancelled',
    result_category = 'schema-upgrade-requires-reauthorization',
    error_code = 'schema-v3-legacy-generation'
WHERE status IN ('pending', 'approved-once', 'approved-for-task');

INSERT INTO audit_summaries (
    audit_id, task_id, request_id, generation_id, event_kind,
    capability_kind, canonical_scope, risk_class, policy_version,
    decision, result_category, error_code, occurred_at
)
SELECT grant_id || ':schema-v3-grant-invalidation', task_id, NULL,
       'legacy:' || grant_id, 'grant-invalidated-on-schema-upgrade',
       capability_kind, 'legacy-redacted', 'unknown', policy_version,
       'deny', 'schema-upgrade-requires-reauthorization',
       'schema-v3-legacy-generation', created_at
FROM task_grants;

UPDATE task_grants
SET generation_id = 'legacy:' || grant_id,
    expires_at = '1970-01-01T00:00:00Z';
"#;

#[cfg(test)]
mod tests {
    use rusqlite::Connection;

    use super::{
        migrate_to_current_in_transaction, user_version, validate_current_schema,
        CURRENT_SCHEMA_VERSION, V1_SCHEMA, V2_SCHEMA,
    };

    #[test]
    fn upgrades_an_existing_v1_schema_and_normalizes_legacy_approval_ids() {
        let mut connection = Connection::open_in_memory().unwrap();
        connection.execute_batch(V1_SCHEMA).unwrap();
        connection
            .pragma_update(None, "user_version", 1_i64)
            .unwrap();
        connection
            .execute_batch(
                "INSERT INTO agents (agent_id, adapter_kind, display_name, status, created_at, updated_at)
                 VALUES ('agent-a', 'mock', 'Mock Agent', 'active', '2026-08-24T00:00:00Z', '2026-08-24T00:00:00Z');
                 INSERT INTO tasks (task_id, agent_id, workspace_path, permission_mode, status, created_at, updated_at)
                 VALUES ('task-a', 'agent-a', '/workspace', 'request-approval', 'active', '2026-08-24T00:00:00Z', '2026-08-24T00:00:00Z');
                 INSERT INTO approvals (
                    approval_id, task_id, capability_kind, canonical_scope, status, requested_at
                 ) VALUES
                    ('approval-a', 'task-a', 'file-write', '/workspace/a', 'pending', '2026-08-24T00:00:00Z'),
                    ('approval-b', 'task-a', 'file-write', '/workspace/b', 'pending', '2026-08-24T00:00:01Z');",
            )
            .unwrap();
        connection
            .execute_batch(
                "INSERT INTO worker_sessions (
                    task_id, worker_session_id, adapter_kind, generation_id, updated_at
                 ) VALUES (
                    'task-a', 'worker-a', 'mock', 'generation-current', '2026-08-24T00:00:00Z'
                 );
                 INSERT INTO task_grants (
                    grant_id, task_id, capability_kind, canonical_scope, expires_at, created_at
                 ) VALUES (
                    'grant-a', 'task-a', 'file-write', '/workspace/a', NULL, '2026-08-24T00:00:00Z'
                 );",
            )
            .unwrap();

        let transaction = connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Exclusive)
            .unwrap();
        migrate_to_current_in_transaction(&transaction).unwrap();
        transaction.commit().unwrap();

        assert_eq!(user_version(&connection).unwrap(), CURRENT_SCHEMA_VERSION);
        validate_current_schema(&connection).unwrap();
        let mut statement = connection
            .prepare(
                "SELECT request_id, generation_id, status, result_category FROM approvals
                 ORDER BY approval_id",
            )
            .unwrap();
        let values = statement
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, Option<String>>(3)?,
                ))
            })
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        drop(statement);
        assert_eq!(
            values,
            vec![
                (
                    "approval-a".to_owned(),
                    "legacy".to_owned(),
                    "cancelled".to_owned(),
                    Some("legacy-requires-reauthorization".to_owned()),
                ),
                (
                    "approval-b".to_owned(),
                    "legacy".to_owned(),
                    "cancelled".to_owned(),
                    Some("legacy-requires-reauthorization".to_owned()),
                ),
            ]
        );
        let grant: (String, Option<String>) = connection
            .query_row(
                "SELECT generation_id, expires_at FROM task_grants WHERE grant_id = 'grant-a'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(grant.0, "legacy:grant-a");
        assert_eq!(grant.1.as_deref(), Some("1970-01-01T00:00:00Z"));

        let transaction = connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Exclusive)
            .unwrap();
        migrate_to_current_in_transaction(&transaction).unwrap();
        transaction.commit().unwrap();
        assert_eq!(user_version(&connection).unwrap(), CURRENT_SCHEMA_VERSION);
    }

    #[test]
    fn upgrades_an_existing_v2_schema_and_invalidates_active_authorizations() {
        let mut connection = Connection::open_in_memory().unwrap();
        connection.execute_batch(V1_SCHEMA).unwrap();
        connection.execute_batch(V2_SCHEMA).unwrap();
        connection
            .pragma_update(None, "user_version", 2_i64)
            .unwrap();
        connection
            .execute_batch(
                "INSERT INTO agents (agent_id, adapter_kind, display_name, status, created_at, updated_at)
                 VALUES ('agent-a', 'mock', 'Mock Agent', 'active', '2026-08-24T00:00:00Z', '2026-08-24T00:00:00Z');
                 INSERT INTO tasks (task_id, agent_id, workspace_path, permission_mode, status, created_at, updated_at)
                 VALUES ('task-a', 'agent-a', '/workspace', 'request-approval', 'active', '2026-08-24T00:00:00Z', '2026-08-24T00:00:00Z');
                 INSERT INTO approvals (
                    approval_id, task_id, request_id, generation_id, capability_kind,
                    canonical_scope, risk_class, policy_version, status, requested_at,
                    expires_at
                 ) VALUES (
                    'approval-a', 'task-a', 'request-a', 'generation-current', 'file-write',
                    '/workspace/a', 'high', 'agent-policy-v1', 'pending',
                    '2026-08-24T00:00:00Z', '2026-08-24T00:05:00Z'
                 );
                 INSERT INTO task_grants (
                    grant_id, task_id, generation_id, capability_kind, canonical_scope,
                    policy_version, expires_at, created_at
                 ) VALUES (
                    'grant-a', 'task-a', 'generation-current', 'file-write', '/workspace/a',
                    'agent-policy-v1', '2026-08-24T00:05:00Z', '2026-08-24T00:00:00Z'
                 );",
            )
            .unwrap();

        let transaction = connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Exclusive)
            .unwrap();
        migrate_to_current_in_transaction(&transaction).unwrap();
        transaction.commit().unwrap();

        assert_eq!(user_version(&connection).unwrap(), CURRENT_SCHEMA_VERSION);
        validate_current_schema(&connection).unwrap();
        let approval: (String, String, String, String) = connection
            .query_row(
                "SELECT generation_id, status, result_category, error_code
                 FROM approvals WHERE approval_id = 'approval-a'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .unwrap();
        assert_eq!(
            approval,
            (
                "legacy".to_owned(),
                "cancelled".to_owned(),
                "schema-upgrade-requires-reauthorization".to_owned(),
                "schema-v3-legacy-generation".to_owned(),
            )
        );
        let grant: (String, String) = connection
            .query_row(
                "SELECT generation_id, expires_at FROM task_grants WHERE grant_id = 'grant-a'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(grant.0, "legacy:grant-a");
        assert_eq!(grant.1, "1970-01-01T00:00:00Z");
        let audit_count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM audit_summaries
                 WHERE audit_id = 'approval-a:schema-v3-reauthorization'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(audit_count, 1);
        let grant_audit_count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM audit_summaries
                 WHERE audit_id = 'grant-a:schema-v3-grant-invalidation'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(grant_audit_count, 1);
    }
}
