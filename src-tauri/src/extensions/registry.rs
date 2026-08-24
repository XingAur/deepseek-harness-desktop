use std::path::{Path, PathBuf};

use super::{manifest::validate_manifest, model::ExtensionManifest};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExtensionReview {
    pub id: String,
    pub version: String,
    pub source: String,
    pub destination: PathBuf,
    pub permissions: Vec<String>,
    pub requires_reapproval: bool,
}

pub fn review_install(
    root: &Path,
    manifest: &ExtensionManifest,
    active_permissions: &[String],
) -> Result<ExtensionReview, String> {
    validate_manifest(manifest)?;
    let destination = root.join(&manifest.id).join(manifest.version.to_string());
    let requires_reapproval = manifest
        .capabilities
        .iter()
        .any(|capability| !active_permissions.contains(capability));
    Ok(ExtensionReview {
        id: manifest.id.clone(),
        version: manifest.version.to_string(),
        source: format!("{:?}", manifest.source).to_lowercase(),
        destination,
        permissions: manifest.capabilities.clone(),
        requires_reapproval,
    })
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::review_install;
    use crate::extensions::manifest::parse_manifest;

    #[test]
    fn review_is_read_only_and_flags_permission_expansion() {
        let json = r#"{
          "schemaVersion":1,"id":"demo.plugin","kind":"plugin","source":"builtin",
          "version":"1.0.0","protocolRange":"dsh-agent/v1","integritySha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "entrypoint":"index.js","platforms":["any"],"capabilities":["file-read"],"credentialReferences":[],
          "healthCheck":{"kind":"static","timeoutMs":1000},"update":{"source":"builtin","exactVersion":"1.0.0"},
          "rollback":{"lastKnownGoodVersion":null,"quarantineOnCrashLoop":true}
        }"#;
        let manifest = parse_manifest(json.as_bytes()).unwrap();
        let review = review_install(Path::new("/safe/extensions"), &manifest, &[]).unwrap();
        assert!(review.requires_reapproval);
        assert_eq!(review.destination, Path::new("/safe/extensions/demo.plugin/1.0.0"));
    }
}
