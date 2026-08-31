use rusqlite::{Connection, TransactionBehavior};

pub const CURRENT_SCHEMA_VERSION: i64 = 1;

const V1_SCHEMA: &str = r#"
CREATE TABLE prompts (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE prompt_activations (
    target       TEXT PRIMARY KEY CHECK (target IN ('claude','codex','dsh')),
    preset_id    TEXT REFERENCES prompts(id) ON DELETE SET NULL,
    activated_at INTEGER NOT NULL
);
"#;

pub fn user_version(connection: &Connection) -> rusqlite::Result<i64> {
    connection.query_row("PRAGMA user_version", [], |row| row.get(0))
}

pub fn validate_current_schema(connection: &Connection) -> rusqlite::Result<()> {
    for table in ["prompts", "prompt_activations"] {
        let exists: bool = connection.query_row(
            "SELECT EXISTS (SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1)",
            [table],
            |row| row.get(0),
        )?;
        if !exists {
            return Err(rusqlite::Error::InvalidQuery);
        }
    }
    Ok(())
}

pub fn migrate_to_current(connection: &mut Connection) -> rusqlite::Result<()> {
    let version = user_version(connection)?;
    if version == CURRENT_SCHEMA_VERSION {
        return Ok(());
    }
    if version != 0 {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Exclusive)?;
    transaction.execute_batch(V1_SCHEMA)?;
    transaction.pragma_update(None, "user_version", CURRENT_SCHEMA_VERSION)?;
    transaction.commit()
}

#[cfg(test)]
mod tests {
    use rusqlite::Connection;
    use super::{migrate_to_current, user_version, validate_current_schema, CURRENT_SCHEMA_VERSION};

    #[test]
    fn fresh_database_gets_prompts_schema() {
        let mut connection = Connection::open_in_memory().unwrap();
        migrate_to_current(&mut connection).unwrap();
        assert_eq!(user_version(&connection).unwrap(), CURRENT_SCHEMA_VERSION);
        validate_current_schema(&connection).unwrap();
        let activation_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM prompt_activations", [], |row| row.get(0))
            .unwrap();
        assert_eq!(activation_count, 0);
    }

    #[test]
    fn single_active_target_is_enforced_by_primary_key() {
        let mut connection = Connection::open_in_memory().unwrap();
        migrate_to_current(&mut connection).unwrap();
        connection.execute_batch(
            "INSERT INTO prompts (id, title, content, created_at, updated_at) VALUES
             ('p1', 'A', 'content-a', 1, 1);
             INSERT INTO prompt_activations (target, preset_id, activated_at) VALUES
             ('claude', 'p1', 1);",
        ).unwrap();
        let replaced = connection.execute(
            "INSERT INTO prompt_activations (target, preset_id, activated_at) VALUES ('claude', 'p1', 2)",
            [],
        );
        assert!(replaced.is_err(), "同一目标二次激活必须被主键拒绝");
    }

    #[test]
    fn future_schema_version_is_rejected() {
        let mut connection = Connection::open_in_memory().unwrap();
        connection.pragma_update(None, "user_version", CURRENT_SCHEMA_VERSION + 1).unwrap();
        assert!(migrate_to_current(&mut connection).is_err());
    }

    #[test]
    fn migrate_to_current_is_idempotent() {
        let mut connection = Connection::open_in_memory().unwrap();
        migrate_to_current(&mut connection).unwrap();
        migrate_to_current(&mut connection).unwrap();
        assert_eq!(user_version(&connection).unwrap(), CURRENT_SCHEMA_VERSION);
        validate_current_schema(&connection).unwrap();
    }

    #[test]
    fn activation_check_constraint_rejects_unknown_target() {
        let mut connection = Connection::open_in_memory().unwrap();
        migrate_to_current(&mut connection).unwrap();
        connection.execute_batch(
            "INSERT INTO prompts (id, title, content, created_at, updated_at) VALUES
             ('p1', 'A', 'content-a', 1, 1);",
        ).unwrap();
        let rejected = connection.execute(
            "INSERT INTO prompt_activations (target, preset_id, activated_at) VALUES ('gemini', 'p1', 1)",
            [],
        );
        assert!(rejected.is_err(), "CHECK 约束必须拒绝非法激活目标");
    }
}
