use rusqlite::{Connection, Transaction, TransactionBehavior};

pub const CURRENT_SCHEMA_VERSION: i64 = 1;

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

pub fn migrate_to_v1(connection: &mut Connection) -> rusqlite::Result<()> {
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Exclusive)?;
    migrate_to_v1_in_transaction(&transaction)?;
    transaction.commit()
}

pub fn migrate_to_v1_in_transaction(transaction: &Transaction<'_>) -> rusqlite::Result<()> {
    transaction.execute_batch(V1_SCHEMA)?;
    transaction.pragma_update(None, "user_version", CURRENT_SCHEMA_VERSION)?;
    Ok(())
}
