use super::model::AgentProvider;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InstallRecipe {
    pub id: String,
    pub provider: AgentProvider,
    pub platforms: Vec<String>,
    pub command: Vec<String>,
    pub source_url: String,
    pub expected_executable: String,
    pub verification_step: Vec<String>,
    pub impact: String,
    pub requires_explicit_confirmation: bool,
}

pub fn recipe_ids() -> Vec<String> {
    vec!["codex-cli".to_owned(), "claude-cli".to_owned()]
}

pub fn install_recipe(provider: AgentProvider) -> Option<InstallRecipe> {
    match provider {
        AgentProvider::Codex => Some(InstallRecipe {
            id: "codex-cli".to_owned(),
            provider,
            platforms: vec!["macos".to_owned(), "windows".to_owned(), "linux".to_owned()],
            command: vec![
                "npm".to_owned(),
                "install".to_owned(),
                "-g".to_owned(),
                "@openai/codex".to_owned(),
            ],
            source_url: "https://github.com/openai/codex".to_owned(),
            expected_executable: "codex".to_owned(),
            verification_step: vec!["codex".to_owned(), "--version".to_owned()],
            impact: "会在用户选择的包管理器中安装 Codex CLI".to_owned(),
            requires_explicit_confirmation: true,
        }),
        AgentProvider::Claude => Some(InstallRecipe {
            id: "claude-cli".to_owned(),
            provider,
            platforms: vec!["macos".to_owned(), "windows".to_owned(), "linux".to_owned()],
            command: vec![
                "npm".to_owned(),
                "install".to_owned(),
                "-g".to_owned(),
                "@anthropic-ai/claude-code".to_owned(),
            ],
            source_url: "https://docs.anthropic.com/en/docs/claude-code".to_owned(),
            expected_executable: "claude".to_owned(),
            verification_step: vec!["claude".to_owned(), "--version".to_owned()],
            impact: "会在用户选择的包管理器中安装 Claude CLI".to_owned(),
            requires_explicit_confirmation: true,
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::{install_recipe, recipe_ids};
    use crate::agents::model::AgentProvider;

    #[test]
    fn install_recipes_are_fixed_data_and_never_auto_execute() {
        let ids = recipe_ids();
        assert!(ids.contains(&"codex-cli".to_owned()));
        assert!(ids.contains(&"claude-cli".to_owned()));
        for provider in [AgentProvider::Codex, AgentProvider::Claude] {
            let recipe = install_recipe(provider).unwrap();
            assert!(!recipe.command.is_empty());
            assert!(recipe.requires_explicit_confirmation);
            assert!(recipe.source_url.starts_with("https://"));
            assert!(!recipe.platforms.is_empty());
            assert!(!recipe.expected_executable.is_empty());
            assert!(!recipe.verification_step.is_empty());
        }
    }
}
