use semver::Version;
use serde::{Deserialize, Serialize};

use crate::{profile::model::ProfileSelection, runtime::model::RuntimeFailure};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum GenerationPhase {
    Idle,
    ResolvingProfile,
    PreparingRuntime,
    Starting,
    Probing,
    Activating,
    Active,
    Draining,
    Stopped,
    Failed,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct GenerationSnapshot {
    pub generation_id: String,
    pub phase: GenerationPhase,
    pub profile: ProfileSelection,
    pub runtime_version: Version,
    pub renderer_url: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct GenerationProgress {
    pub phase: GenerationPhase,
    pub completed: u64,
    pub total: Option<u64>,
    pub message: String,
    pub installed_version: Option<Version>,
    pub required_version: Option<Version>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum DesktopEvent {
    GenerationProgress {
        #[serde(rename = "generationId")]
        generation_id: String,
        payload: GenerationProgress,
    },
    GenerationActive {
        #[serde(rename = "generationId")]
        generation_id: String,
        snapshot: GenerationSnapshot,
    },
    GenerationFailed {
        #[serde(rename = "generationId")]
        generation_id: String,
        failure: RuntimeFailure,
    },
    ProfileRecovered {
        #[serde(rename = "generationId")]
        generation_id: String,
        profile: ProfileSelection,
        reason: String,
    },
}

#[cfg(test)]
mod tests {
    use semver::Version;
    use uuid::Uuid;

    use super::{DesktopEvent, GenerationPhase, GenerationSnapshot};
    use crate::profile::model::ProfileSelection;

    #[test]
    fn serializes_generation_scoped_active_event() {
        let generation_id = "g-2".to_string();
        let event = DesktopEvent::GenerationActive {
            generation_id: generation_id.clone(),
            snapshot: GenerationSnapshot {
                generation_id,
                phase: GenerationPhase::Active,
                profile: ProfileSelection {
                    profile_id: Uuid::nil(),
                    revision: 3,
                },
                runtime_version: Version::new(1, 8, 2),
                renderer_url: Some("http://127.0.0.1:39000/".to_string()),
            },
        };
        let value = serde_json::to_value(event).unwrap();
        assert_eq!(value["kind"], "generation-active");
        assert_eq!(value["generationId"], "g-2");
        assert_eq!(value["snapshot"]["profile"]["revision"], 3);
    }
}
