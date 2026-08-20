pub mod create;
pub mod metadata;
pub mod recycle;

use crate::{DesktopFoundation, profile::model::ProfileRecord, runtime::RuntimeFailure};

pub fn active_profile(foundation: &DesktopFoundation) -> Result<ProfileRecord, RuntimeFailure> {
    let selection = foundation
        .profiles
        .state()?
        .selected_profile
        .ok_or_else(|| RuntimeFailure::internal("当前没有活动 Profile"))?;
    foundation.profiles.get(&selection.profile_id)
}

pub fn metadata_repository(
    foundation: &DesktopFoundation,
) -> Result<metadata::ProjectMetadataRepository, RuntimeFailure> {
    Ok(metadata::ProjectMetadataRepository::new(
        active_profile(foundation)?.data_root,
    ))
}
