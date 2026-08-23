pub(crate) mod model;
pub(crate) mod vault;

#[cfg(test)]
mod tests {
    #[cfg(target_os = "macos")]
    use super::vault::map_keyring_error;
    use super::{
        model::{CredentialStatus, SecretValue},
        vault::{
            BackendCredentialVault, BackendError, BackendErrorKind, CredentialVault, MemoryBackend,
            NativeCredentialVault, PlatformAccessCode, SERVICE_NAME, classify_platform_access,
        },
    };

    const FIRST_SECRET: &str = "sk-unit-test-first-secret";
    const SECOND_SECRET: &str = "sk-unit-test-second-secret";

    fn assert_zeroize_on_drop<T: zeroize::ZeroizeOnDrop>() {}

    #[test]
    fn every_owned_secret_buffer_has_a_pinned_zeroize_drop_boundary() {
        assert_zeroize_on_drop::<SecretValue>();
        assert_zeroize_on_drop::<BackendError>();
        let manifest = include_str!("../../Cargo.toml");
        assert!(manifest.contains("zeroize = \"=1.9.0\""));
        let backend_source = include_str!("vault.rs");
        assert!(backend_source.contains("HashMap<String, Zeroizing<Vec<u8>>>"));
    }

    #[test]
    fn generated_ids_are_opaque_uuid_accounts_under_the_fixed_service() {
        let backend = MemoryBackend::default();
        let vault = BackendCredentialVault::new(backend.clone());

        let metadata = vault.put(None, SecretValue::new(FIRST_SECRET)).unwrap();

        assert!(uuid::Uuid::parse_str(metadata.credential_id.as_str()).is_ok());
        assert_eq!(metadata.status, CredentialStatus::Configured);
        assert_eq!(
            SERVICE_NAME,
            "ai.deepseek.harness.desktop.agent-credentials.v1"
        );
        assert_eq!(backend.accounts(), vec![metadata.credential_id.to_string()]);
        assert!(!backend.accounts()[0].contains("deepseek"));
        assert!(!backend.accounts()[0].contains("sk-"));
    }

    #[test]
    fn put_with_an_existing_id_overwrites_only_that_secure_entry() {
        let backend = MemoryBackend::default();
        let vault = BackendCredentialVault::new(backend);
        let first = vault.put(None, SecretValue::new(FIRST_SECRET)).unwrap();
        let other = vault.put(None, SecretValue::new("other-secret")).unwrap();

        let updated = vault
            .put(Some(&first.credential_id), SecretValue::new(SECOND_SECRET))
            .unwrap();

        assert_eq!(updated.credential_id, first.credential_id);
        assert_eq!(
            vault
                .resolve(&first.credential_id)
                .unwrap()
                .expose_for_backend(),
            SECOND_SECRET
        );
        assert_eq!(
            vault
                .resolve(&other.credential_id)
                .unwrap()
                .expose_for_backend(),
            "other-secret"
        );
    }

    #[test]
    fn delete_and_missing_entries_return_non_secret_status() {
        let vault = BackendCredentialVault::new(MemoryBackend::default());
        let metadata = vault.put(None, SecretValue::new(FIRST_SECRET)).unwrap();

        assert_eq!(
            vault.status(&metadata.credential_id).unwrap(),
            CredentialStatus::Configured
        );
        assert_eq!(
            vault.delete(&metadata.credential_id).unwrap(),
            CredentialStatus::NotConfigured
        );
        assert_eq!(
            vault.status(&metadata.credential_id).unwrap(),
            CredentialStatus::NotConfigured
        );
        assert_eq!(
            vault.delete(&metadata.credential_id).unwrap(),
            CredentialStatus::NotConfigured
        );
        let error = match vault.resolve(&metadata.credential_id) {
            Ok(_) => panic!("deleted credential unexpectedly resolved"),
            Err(error) => error,
        };
        assert_eq!(error.code(), "not_configured");
    }

    #[test]
    fn metadata_debug_and_serialization_never_contain_secret_material() {
        let vault = BackendCredentialVault::new(MemoryBackend::default());
        let metadata = vault.put(None, SecretValue::new(FIRST_SECRET)).unwrap();

        let debug = format!("{metadata:?}");
        let serialized = serde_json::to_string(&metadata).unwrap();

        assert!(!debug.contains(FIRST_SECRET));
        assert!(!serialized.contains(FIRST_SECRET));
        assert!(!serialized.contains("secret"));
    }

    #[test]
    fn backend_errors_cross_the_vault_boundary_only_as_redacted_codes() {
        let backend = MemoryBackend::default();
        let vault = BackendCredentialVault::new(backend.clone());
        let metadata = vault.put(None, SecretValue::new(FIRST_SECRET)).unwrap();
        backend.fail_next(
            BackendErrorKind::Locked,
            format!("interaction denied while handling {FIRST_SECRET}"),
        );

        let error = match vault.resolve(&metadata.credential_id) {
            Ok(_) => panic!("locked secure store unexpectedly resolved a credential"),
            Err(error) => error,
        };

        assert_eq!(error.code(), "secure_store_locked");
        assert!(!format!("{error:?}").contains(FIRST_SECRET));
        assert!(!error.to_string().contains(FIRST_SECRET));

        backend.fail_next(
            BackendErrorKind::Unavailable,
            format!("backend unavailable for {SECOND_SECRET}"),
        );
        let error = vault.status(&metadata.credential_id).unwrap_err();
        assert_eq!(error.code(), "secure_store_unavailable");
        assert!(!format!("{error:?}").contains(SECOND_SECRET));
    }

    #[test]
    fn native_backend_constructor_does_not_access_the_real_secure_store() {
        let _vault = NativeCredentialVault::new();
    }

    #[test]
    fn platform_classifier_locks_only_verified_interaction_denial() {
        assert!(matches!(
            classify_platform_access(PlatformAccessCode::MacOs(-25308)),
            BackendErrorKind::Locked
        ));
        for code in [-61, -25244, -25291, -25292, -25294, -25295] {
            assert!(matches!(
                classify_platform_access(PlatformAccessCode::MacOs(code)),
                BackendErrorKind::Unavailable
            ));
        }
        assert!(matches!(
            classify_platform_access(PlatformAccessCode::Windows(1312)),
            BackendErrorKind::Unavailable
        ));
        assert!(matches!(
            classify_platform_access(PlatformAccessCode::Windows(5)),
            BackendErrorKind::Unavailable
        ));
        assert!(matches!(
            classify_platform_access(PlatformAccessCode::Unclassified),
            BackendErrorKind::Unavailable
        ));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn typed_macos_keyring_errors_preserve_the_locked_boundary() {
        let locked = map_keyring_error(keyring::Error::PlatformFailure(Box::new(
            security_framework::base::Error::from_code(-25308),
        )))
        .into_vault_error();
        assert_eq!(locked.code(), "secure_store_locked");

        let read_only = map_keyring_error(keyring::Error::NoStorageAccess(Box::new(
            security_framework::base::Error::from_code(-25292),
        )))
        .into_vault_error();
        assert_eq!(read_only.code(), "secure_store_unavailable");
    }

    #[test]
    fn renderer_commands_do_not_expose_secret_resolution() {
        assert!(!crate::RENDERER_COMMAND_NAMES.is_empty());
        for command in crate::RENDERER_COMMAND_NAMES {
            let leaf = command.rsplit("::").next().unwrap();
            assert!(!leaf.contains("secret"));
            assert!(!leaf.contains("credential"));
            for forbidden_prefix in ["resolve", "get", "fetch", "read"] {
                assert!(!leaf.starts_with(forbidden_prefix));
            }
        }
        let commands = include_str!("../commands.rs");
        assert!(!commands.contains("SecretValue"));
        assert!(!commands.contains("CredentialVault"));
    }
}
