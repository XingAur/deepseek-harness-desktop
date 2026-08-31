from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.harness_config import (
    build_config_summary,
    build_configuration_import_draft,
    build_configuration_import_review,
    build_configuration_preview,
    build_configuration_share_validation,
    build_configuration_template_index,
    build_configuration_wizard,
    config_summary_to_markdown,
    configuration_import_draft_to_markdown,
    configuration_import_review_to_markdown,
    configuration_preview_to_markdown,
    configuration_share_validation_to_markdown,
    configuration_template_index_to_markdown,
    configuration_wizard_to_markdown,
    write_config_summary_outputs,
    write_configuration_import_draft_outputs,
    write_configuration_import_review_outputs,
    write_configuration_preview_outputs,
    write_configuration_share_validation_outputs,
    write_configuration_template_index_outputs,
    write_configuration_wizard_outputs,
)
from app.config_compat import resolve_legacy_compatible_config
from app.config_resolver import resolved_config_to_markdown, write_resolved_config_outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Check HIS Harness v0.22 rule pack, profile and credential status without printing secrets.")
    parser.add_argument("--rule-pack", default="", help="Rule Pack JSON path; defaults to config/rule_packs/dfhis.default.json")
    parser.add_argument("--profile-config", default="", help="Profile config JSON path; defaults to config/profiles.example.json")
    parser.add_argument("--profile-key", default="", help="Profile key; defaults to profile config default_profile")
    parser.add_argument("--credentials-file", default="", help="Local credentials file path; defaults to HARNESS_CREDENTIALS_FILE or local legacy path")
    parser.add_argument("--check-keychain", action="store_true", help="also check macOS Keychain; never prints full values")
    parser.add_argument("--include-preview", action="store_true", help="also output readonly v0.25 configuration/provider template preview")
    parser.add_argument("--include-share-validation", action="store_true", help="also output readonly v0.26 share-package validation and local override strategy")
    parser.add_argument("--include-import-draft", action="store_true", help="also generate readonly v0.27 import draft files into --draft-output-dir")
    parser.add_argument("--review-import-draft", action="store_true", help="read back v0.27 import draft files from --draft-input-dir and output readonly v0.28 review")
    parser.add_argument("--include-template-index", action="store_true", help="index v0.27 draft files and optionally compare another draft dir; outputs readonly v0.29 template index")
    parser.add_argument("--include-config-wizard", action="store_true", help="output readonly v0.30 configuration wizard; implies preview, share validation, import review and template index")
    parser.add_argument("--include-resolved-config", action="store_true", help="also output readonly v0.34 layered ResolvedConfig")
    parser.add_argument("--team-config", default="", help="explicit v1 team_package layer JSON; requires --include-resolved-config")
    parser.add_argument("--project-config", default="", help="explicit v1 project_config layer JSON; requires --include-resolved-config")
    parser.add_argument("--personal-config", default="", help="explicit v1 personal_override layer JSON; requires --include-resolved-config")
    parser.add_argument("--run-override-json", default="", help="inline JSON object for the current readonly resolution; requires --include-resolved-config")
    parser.add_argument("--draft-output-dir", default="", help="user-selected directory for v0.27 draft files; required with --include-import-draft")
    parser.add_argument("--draft-input-dir", default="", help="directory containing v0.27 draft files; required with --review-import-draft unless --include-import-draft uses --draft-output-dir")
    parser.add_argument("--compare-draft-input-dir", default="", help="optional second draft directory for v0.29 template diff summary")
    parser.add_argument("--overwrite-drafts", action="store_true", help="overwrite existing draft files in --draft-output-dir")
    parser.add_argument("--output-dir", default="", help="optional directory to write harness_config_summary.json/md")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    parser.add_argument("--strict", action="store_true", help="exit non-zero when hard guard validation fails or required credentials are missing")
    args = parser.parse_args()

    layer_args_used = any([args.team_config, args.project_config, args.personal_config, args.run_override_json])
    if layer_args_used and not args.include_resolved_config:
        raise SystemExit("配置层参数需要显式传入 --include-resolved-config；默认 v0.33 行为保持不变。")
    run_overrides = None
    if args.run_override_json:
        try:
            run_overrides = json.loads(args.run_override_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--run-override-json 不是合法 JSON：{exc}") from exc
        if not isinstance(run_overrides, dict):
            raise SystemExit("--run-override-json 根节点必须是 JSON 对象。")

    if args.include_import_draft and not args.draft_output_dir:
        raise SystemExit("--include-import-draft 需要显式传入 --draft-output-dir，避免写到非用户选择目录。")
    review_input_dir = args.draft_input_dir or (args.draft_output_dir if args.include_import_draft else "")
    if args.review_import_draft and not review_input_dir:
        raise SystemExit("--review-import-draft 需要显式传入 --draft-input-dir，避免误读非用户选择目录。")
    if args.include_template_index and not review_input_dir:
        raise SystemExit("--include-template-index 需要显式传入 --draft-input-dir，避免误读非用户选择目录。")
    if args.include_config_wizard and not review_input_dir:
        raise SystemExit("--include-config-wizard 需要显式传入 --draft-input-dir，避免误读非用户选择目录。")

    summary = build_config_summary(
        rule_pack_path=args.rule_pack or None,
        profile_config_path=args.profile_config or None,
        profile_key=args.profile_key,
        credentials_file=args.credentials_file or None,
        check_keychain=args.check_keychain,
    )
    resolved = (
        resolve_legacy_compatible_config(
            rule_pack_path=args.rule_pack or None,
            profile_config_path=args.profile_config or None,
            profile_key=args.profile_key,
            team_config_path=args.team_config or None,
            project_config_path=args.project_config or None,
            personal_config_path=args.personal_config or None,
            run_overrides=run_overrides,
        )
        if args.include_resolved_config
        else None
    )
    resolved_files: dict[str, str] = {}
    preview = build_configuration_preview(summary) if args.include_preview or args.include_config_wizard else None
    share_validation = (
        build_configuration_share_validation(
            summary=summary,
            rule_pack_path=args.rule_pack or None,
            profile_config_path=args.profile_config or None,
        )
        if args.include_share_validation or args.include_config_wizard
        else None
    )
    import_draft = (
        build_configuration_import_draft(
            summary=summary,
            rule_pack_path=args.rule_pack or None,
            profile_config_path=args.profile_config or None,
            draft_output_dir=args.draft_output_dir,
            overwrite=args.overwrite_drafts,
        )
        if args.include_import_draft
        else None
    )
    import_draft_files = {}
    if import_draft:
        import_draft_files = write_configuration_import_draft_outputs(
            output_dir=args.draft_output_dir,
            draft=import_draft,
            overwrite=args.overwrite_drafts,
        )
        import_draft = dict(import_draft)
        import_draft["write_result"] = import_draft_files
    import_review = build_configuration_import_review(draft_dir=review_input_dir) if args.review_import_draft or args.include_config_wizard else None
    import_review_files = {}
    template_index = (
        build_configuration_template_index(
            draft_dirs=[item for item in [review_input_dir, args.compare_draft_input_dir] if item]
        )
        if args.include_template_index or args.include_config_wizard
        else None
    )
    template_index_files = {}
    config_wizard = (
        build_configuration_wizard(
            config_summary=summary,
            config_preview=preview,
            config_share_validation=share_validation,
            config_import_draft=import_draft,
            config_import_review=import_review,
            config_template_index=template_index,
            draft_input_dir=review_input_dir,
            compare_draft_input_dir=args.compare_draft_input_dir or None,
        )
        if args.include_config_wizard
        else None
    )
    config_wizard_files = {}
    files = {}
    if args.output_dir:
        summary_files = write_config_summary_outputs(output_dir=args.output_dir, summary=summary)
        files = summary_files
        if preview:
            preview_files = write_configuration_preview_outputs(output_dir=args.output_dir, preview=preview)
            files = {
                "summary_json": summary_files["json"],
                "summary_markdown": summary_files["markdown"],
                "preview_json": preview_files["json"],
                "preview_markdown": preview_files["markdown"],
            }
        if share_validation:
            share_validation_files = write_configuration_share_validation_outputs(
                output_dir=args.output_dir,
                validation=share_validation,
            )
            if "summary_json" not in files:
                files = {
                    "summary_json": summary_files["json"],
                    "summary_markdown": summary_files["markdown"],
                }
            files["share_validation_json"] = share_validation_files["json"]
            files["share_validation_markdown"] = share_validation_files["markdown"]
        if import_draft_files:
            if "summary_json" not in files:
                files = {
                    "summary_json": summary_files["json"],
                    "summary_markdown": summary_files["markdown"],
                }
            for key in ["profiles_draft", "rule_pack_draft", "credentials_example", "import_guide", "manifest"]:
                if key in import_draft_files:
                    files[f"import_draft_{key}"] = import_draft_files[key]
        if import_review:
            import_review_files = write_configuration_import_review_outputs(
                output_dir=args.output_dir,
                review=import_review,
            )
            if "summary_json" not in files:
                files = {
                    "summary_json": summary_files["json"],
                    "summary_markdown": summary_files["markdown"],
                }
            files["import_review_json"] = import_review_files["json"]
            files["import_review_markdown"] = import_review_files["markdown"]
        if template_index:
            template_index_files = write_configuration_template_index_outputs(
                output_dir=args.output_dir,
                index=template_index,
            )
            if "summary_json" not in files:
                files = {
                    "summary_json": summary_files["json"],
                    "summary_markdown": summary_files["markdown"],
                }
            files["template_index_json"] = template_index_files["json"]
            files["template_index_markdown"] = template_index_files["markdown"]
        if config_wizard:
            config_wizard_files = write_configuration_wizard_outputs(
                output_dir=args.output_dir,
                wizard=config_wizard,
            )
            if "summary_json" not in files:
                files = {
                    "summary_json": summary_files["json"],
                    "summary_markdown": summary_files["markdown"],
                }
            files["config_wizard_json"] = config_wizard_files["json"]
            files["config_wizard_markdown"] = config_wizard_files["markdown"]
        if resolved is not None:
            resolved_files = write_resolved_config_outputs(output_dir=args.output_dir, resolved=resolved)
            files["resolved_config_json"] = resolved_files["json"]
            files["resolved_config_markdown"] = resolved_files["markdown"]
        summary = dict(summary)
        summary["files"] = files
    if preview:
        summary = dict(summary)
        summary["configuration_preview"] = preview
    if share_validation:
        summary = dict(summary)
        summary["config_share_validation"] = share_validation
    if import_draft:
        summary = dict(summary)
        summary["config_import_draft"] = import_draft
    if import_review:
        summary = dict(summary)
        summary["config_import_review"] = import_review
    if template_index:
        summary = dict(summary)
        summary["config_template_index"] = template_index
    if config_wizard:
        summary = dict(summary)
        summary["config_wizard"] = config_wizard
    if resolved is not None:
        summary = dict(summary)
        summary["resolved_config"] = resolved.to_dict()

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(config_summary_to_markdown(summary))
        if preview:
            print("")
            print(configuration_preview_to_markdown(preview))
        if share_validation:
            print("")
            print(configuration_share_validation_to_markdown(share_validation))
        if import_draft:
            print("")
            print(configuration_import_draft_to_markdown(import_draft))
        if import_review:
            print("")
            print(configuration_import_review_to_markdown(import_review))
        if template_index:
            print("")
            print(configuration_template_index_to_markdown(template_index))
        if config_wizard:
            print("")
            print(configuration_wizard_to_markdown(config_wizard))
        if resolved is not None:
            print("")
            print(resolved_config_to_markdown(resolved))
        if files:
            print("")
            print(f"JSON: {files.get('json') or files.get('summary_json')}")
            print(f"Markdown: {files.get('markdown') or files.get('summary_markdown')}")
            if preview:
                print(f"Preview JSON: {files['preview_json']}")
                print(f"Preview Markdown: {files['preview_markdown']}")
            if share_validation:
                print(f"Share Validation JSON: {files['share_validation_json']}")
                print(f"Share Validation Markdown: {files['share_validation_markdown']}")
            if import_draft_files:
                print(f"Import Draft Status: {import_draft_files.get('status')}")
                print(f"Import Draft Dir: {import_draft_files.get('output_dir')}")
                print(f"Import Guide: {import_draft_files.get('import_guide')}")
            if import_review_files:
                print(f"Import Review JSON: {files['import_review_json']}")
                print(f"Import Review Markdown: {files['import_review_markdown']}")
            if template_index_files:
                print(f"Template Index JSON: {files['template_index_json']}")
                print(f"Template Index Markdown: {files['template_index_markdown']}")
            if config_wizard_files:
                print(f"Config Wizard JSON: {files['config_wizard_json']}")
                print(f"Config Wizard Markdown: {files['config_wizard_markdown']}")
            if resolved_files:
                print(f"Resolved Config JSON: {files['resolved_config_json']}")
                print(f"Resolved Config Markdown: {files['resolved_config_markdown']}")

    if args.strict and is_strict_failed(summary):
        raise SystemExit(1)


def is_strict_failed(summary: dict) -> bool:
    validation = summary.get("validation") or {}
    credentials = summary.get("credentials") or {}
    share_validation = summary.get("config_share_validation") or {}
    import_review = summary.get("config_import_review") or {}
    template_index = summary.get("config_template_index") or {}
    config_wizard = summary.get("config_wizard") or {}
    resolved = summary.get("resolved_config") or {}
    return (
        validation.get("status") != "pass"
        or int(credentials.get("required_missing_count") or 0) > 0
        or bool(share_validation and share_validation.get("status") != "pass")
        or bool(import_review and import_review.get("status") != "pass")
        or bool(template_index and template_index.get("status") != "pass")
        or bool(config_wizard and config_wizard.get("status") != "pass")
        or bool(resolved and (resolved.get("validation") or {}).get("status") != "pass")
    )


if __name__ == "__main__":
    main()
