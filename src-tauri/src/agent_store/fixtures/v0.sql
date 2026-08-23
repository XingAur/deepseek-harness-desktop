PRAGMA user_version = 0;

CREATE TABLE legacy_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL
);

INSERT INTO legacy_settings (setting_key, setting_value)
VALUES ('legacy-provider-mode', 'preserve-me');
