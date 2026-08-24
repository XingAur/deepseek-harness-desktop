use std::{fs, path::Path};

use super::{manifest::validate_manifest, model::ExtensionManifest};

pub fn stage_manifest(root: &Path, manifest: &ExtensionManifest) -> Result<std::path::PathBuf, String> {
    validate_manifest(manifest)?;
    let destination = root.join(&manifest.id).join(manifest.version.to_string());
    if destination.exists() {
        return Err("扩展版本已经存在，必须通过更新审核流程处理".to_owned());
    }
    fs::create_dir_all(&destination).map_err(|_| "扩展暂存目录创建失败".to_owned())?;
    fs::write(
        destination.join("manifest.json"),
        serde_json::to_vec_pretty(manifest).map_err(|_| "扩展清单序列化失败".to_owned())?,
    )
    .map_err(|_| "扩展清单写入失败".to_owned())?;
    Ok(destination)
}

#[cfg(test)]
mod tests {
    use tempfile::tempdir;

    use super::stage_manifest;
    use crate::extensions::manifest::{parse_manifest, test_manifest_json};

    #[test]
    fn stages_only_into_the_harness_versioned_root() {
        let root = tempdir().unwrap();
        let parsed = parse_manifest(test_manifest_json().as_bytes()).unwrap();
        let destination = stage_manifest(root.path(), &parsed).unwrap();
        assert!(destination.join("manifest.json").is_file());
        assert!(stage_manifest(root.path(), &parsed).is_err());
    }
}
