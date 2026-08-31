from __future__ import annotations

import difflib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.worktree_executor import (
    DEFAULT_WORKTREE_ROOT,
    PATCH_TIMEOUT_SECONDS,
    VERIFY_TIMEOUT_SECONDS,
    build_side_effect_report,
    capture_worktree_snapshot,
    cleanup_git_worktree,
    normalize_relative_path,
    prepare_dependency_links,
    run_command,
    run_shell_command,
    summarize_command,
    truncate_text,
    validate_patch,
    validate_relative_path,
)
from app.worktree_lifecycle import create_worktree_marker, remove_worktree_marker


@dataclass
class FullstackPatchTarget:
    key: str
    name: str
    role: str
    project_path: str
    patch_kind: str
    allowed_paths: list[str] = field(default_factory=list)
    verify_commands: list[str] = field(default_factory=list)


@dataclass
class FullstackExecutionOptions:
    run_id: int
    demand_text: str
    report_markdown: str
    project_root: str
    authority_mode: str
    technical_decision: dict | None = None
    worktree_root: str = DEFAULT_WORKTREE_ROOT
    verify_commands: list[str] = field(default_factory=list)
    authoritative_contract: dict | None = None
    apply_to_project: bool = True
    cleanup_worktree: bool = True


@dataclass
class FullstackExecutionResult:
    status: str
    summary: str
    targets: list[dict] = field(default_factory=list)
    final_diffs: dict[str, str] = field(default_factory=dict)
    apply_to_projects: dict[str, dict] = field(default_factory=dict)
    cleanup: dict[str, dict] = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def plan_to_markdown(self) -> str:
        lines = [
            "## v0.8.9 Fullstack Patch Plan",
            "",
            "- 技术决策：全栈契约修复。",
            "- 合入策略：所有项目 worktree 验证通过后，才统一合入原业务目录。",
            "- 禁止动作：不提交、不推送、不发布、不真实流转云效。",
            "",
            "| 项目 | 角色 | 白名单 | 验证命令 |",
            "| --- | --- | --- | --- |",
        ]
        for target in self.targets:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(target.get("name") or target.get("key") or "-"),
                        str(target.get("role") or "-"),
                        "<br>".join(target.get("allowed_paths") or []) or "-",
                        "<br>".join(target.get("verify_commands") or []) or "-",
                    ]
                )
                + " |"
            )
        return "\n".join(lines)

    def to_markdown(self) -> str:
        lines = [
            "## v0.8.9 多项目 Fullstack Worktree 结果",
            "",
            f"- 状态：{self.status}",
            f"- 结论：{self.summary}",
            f"- 项目数：{len(self.targets)}",
            "- 禁止动作：不提交、不推送、不发布、不真实流转云效。",
            "",
            "| 项目 | 状态 | 合入 | 清理 | 变更文件 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for target in self.targets:
            apply_result = self.apply_to_projects.get(target.get("key") or "", {})
            cleanup_result = self.cleanup.get(target.get("key") or "", {})
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(target.get("name") or target.get("key") or "-"),
                        str(target.get("status") or "-"),
                        str(apply_result.get("status") or "not_run"),
                        str(cleanup_result.get("status") or "not_run"),
                        "<br>".join(target.get("changed_paths") or []) or "-",
                    ]
                )
                + " |"
            )
        for key, diff in self.final_diffs.items():
            if not diff.strip():
                continue
            lines.extend(
                [
                    "",
                    f"### {key} final.diff 摘要",
                    "",
                    "```diff",
                    truncate_text(diff, 5000),
                    "```",
                ]
            )
        return "\n".join(lines)


class FullstackWorktreeExecutor:
    def execute(self, options: FullstackExecutionOptions) -> FullstackExecutionResult:
        started_at = time.time()
        worktree_root = Path(options.worktree_root).expanduser().resolve()
        targets = build_dfhis_31270_targets(options)
        target_records = [target_to_record(target) for target in targets]
        manifest = {
            "run_id": options.run_id,
            "project_root": str(Path(options.project_root).expanduser().resolve()),
            "worktree_root": str(worktree_root),
            "apply_to_project": options.apply_to_project,
            "cleanup_worktree": options.cleanup_worktree,
            "started_at_epoch": started_at,
            "targets": target_records,
        }
        final_diffs: dict[str, str] = {}
        apply_results: dict[str, dict] = {}
        cleanup_results: dict[str, dict] = {}
        created: list[tuple[FullstackPatchTarget, Path]] = []

        contract_error = authoritative_fullstack_boundary_error(
            targets,
            options.authority_mode,
            options.authoritative_contract,
        )
        if contract_error:
            manifest["status"] = "failed"
            manifest["summary"] = contract_error
            manifest["finished_at_epoch"] = time.time()
            return FullstackExecutionResult(
                status="failed",
                summary=contract_error,
                targets=target_records,
                manifest=manifest,
            )

        preflight_error = preflight_targets(targets)
        if preflight_error:
            manifest["status"] = "failed"
            manifest["summary"] = preflight_error
            manifest["finished_at_epoch"] = time.time()
            return FullstackExecutionResult(
                status="failed",
                summary=preflight_error,
                targets=target_records,
                manifest=manifest,
            )

        try:
            for target in targets:
                project_path = Path(target.project_path).expanduser().resolve()
                worktree_path = worktree_root / f"run_{options.run_id}_{safe_worktree_suffix(target.key)}"
                record = record_for_key(target_records, target.key)
                record["worktree_path"] = str(worktree_path)
                setup_error = create_fullstack_worktree(project_path=project_path, worktree_root=worktree_root, worktree_path=worktree_path)
                if setup_error:
                    record["status"] = "setup_failed"
                    record["message"] = setup_error
                    return failed_result(
                        "创建多项目 worktree 失败：" + setup_error,
                        target_records,
                        final_diffs,
                        apply_results,
                        cleanup_results,
                        manifest,
                    )
                created.append((target, worktree_path))
                record["dependency_links"] = prepare_dependency_links(project_path=project_path, worktree_path=worktree_path)

            for target, worktree_path in created:
                record = record_for_key(target_records, target.key)
                result = run_target_patch(target=target, worktree_path=worktree_path)
                record.update(result)
                if result.get("status") != "success":
                    return failed_result(
                        result.get("message") or f"{target.name} patch 失败",
                        target_records,
                        final_diffs,
                        apply_results,
                        cleanup_results,
                        manifest,
                    )
                final_diffs[target.key] = result.get("final_diff", "")

            if options.apply_to_project:
                precheck_error = precheck_all_project_applies(targets=targets, final_diffs=final_diffs, apply_results=apply_results)
                if precheck_error:
                    return failed_result(precheck_error, target_records, final_diffs, apply_results, cleanup_results, manifest)
                apply_error = apply_all_to_projects(targets=targets, final_diffs=final_diffs, apply_results=apply_results)
                if apply_error:
                    return failed_result(apply_error, target_records, final_diffs, apply_results, cleanup_results, manifest)
            else:
                for target in targets:
                    apply_results[target.key] = {"status": "skipped", "message": "配置为不合入原业务目录。"}

            summary = "多项目 worktree 验证通过，final.diff 已合入原业务目录；未提交、未推送、未发布。"
            manifest["status"] = "success"
            manifest["summary"] = summary
            manifest["finished_at_epoch"] = time.time()
            return FullstackExecutionResult(
                status="success",
                summary=summary,
                targets=target_records,
                final_diffs=final_diffs,
                apply_to_projects=apply_results,
                cleanup=cleanup_results,
                manifest=manifest,
            )
        finally:
            if options.cleanup_worktree:
                for target, worktree_path in created:
                    cleanup_results[target.key] = cleanup_git_worktree(
                        project_path=Path(target.project_path).expanduser().resolve(),
                        worktree_path=worktree_path,
                    )
                manifest["cleanup"] = cleanup_results


def build_dfhis_31270_targets(options: FullstackExecutionOptions) -> list[FullstackPatchTarget]:
    root = Path(options.project_root).expanduser().resolve()
    frontend_verify = options.verify_commands or [
        "./node_modules/.bin/vue-cli-service lint --no-fix src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue"
    ]
    return [
        FullstackPatchTarget(
            key="df-web-zhuyuansf",
            name="df-web-zhuyuansf",
            role="frontend-view",
            project_path=str(root / "df-web-zhuyuansf"),
            patch_kind="dfhis_31270_frontend_vue",
            allowed_paths=["src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue"],
            verify_commands=frontend_verify,
        ),
    ]


def validate_authoritative_fullstack_options(
    options: FullstackExecutionOptions,
) -> str:
    return authoritative_fullstack_boundary_error(
        build_dfhis_31270_targets(options),
        options.authority_mode,
        options.authoritative_contract,
    )


def authoritative_fullstack_boundary_error(
    targets: list[FullstackPatchTarget],
    authority_mode: str,
    contract: dict | None,
) -> str:
    if authority_mode not in {"legacy", "enforce"}:
        return "fullstack authority mode 无效，禁止执行。"
    if authority_mode == "legacy":
        if contract is not None:
            return "fullstack legacy 模式禁止携带权威能力契约。"
        return ""
    if not isinstance(contract, dict) or not contract:
        return "fullstack enforce 模式必须提供非空权威能力契约。"
    repositories = contract.get("repositories")
    allowed_paths = contract.get("allowed_paths")
    verify_commands = contract.get("verify_commands")
    if not isinstance(repositories, (list, tuple)):
        return "fullstack 权威能力契约仓库边界无效，禁止执行。"
    if not isinstance(allowed_paths, (list, tuple)):
        return "fullstack 权威能力契约路径边界无效，禁止执行。"
    if not isinstance(verify_commands, (list, tuple)):
        return "fullstack 权威能力契约验证边界无效，禁止执行。"
    repository_keys = {
        (
            str(item.get("name") or ""),
            str(Path(str(item.get("path") or "")).expanduser().resolve()),
        )
        for item in repositories
        if isinstance(item, dict)
    }
    contract_paths = set(allowed_paths)
    contract_commands = set(verify_commands)
    for target in targets:
        repository_key = (
            target.name,
            str(Path(target.project_path).expanduser().resolve()),
        )
        if repository_key not in repository_keys:
            return f"fullstack 固定仓库目标未被权威能力契约覆盖：{target.name}"
        if not set(target.allowed_paths) <= contract_paths:
            return f"fullstack 固定路径目标未被权威能力契约覆盖：{target.name}"
        if not set(target.verify_commands) <= contract_commands:
            return f"fullstack 固定验证命令未被权威能力契约覆盖：{target.name}"
    return ""


def preflight_targets(targets: list[FullstackPatchTarget]) -> str:
    if not targets:
        return "fullstack-worktree 没有可执行项目目标。"
    for target in targets:
        project_path = Path(target.project_path).expanduser().resolve()
        if not project_path.exists() or not project_path.is_dir():
            return f"项目路径不存在或不是目录：{project_path}"
        root_result = run_command(["git", "rev-parse", "--show-toplevel"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        if root_result["returncode"] != 0:
            return f"{target.name} 不是 Git 仓库。"
        git_root = Path(root_result["stdout"].strip()).resolve()
        if git_root != project_path:
            return f"{target.name} project_path 必须指向 Git 根目录：当前根目录 {git_root}"
        status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        if status["returncode"] != 0:
            return f"无法读取 {target.name} git status。"
        if status["stdout"].strip():
            return f"{target.name} 存在未提交改动，拒绝进入 fullstack-worktree。"
        for allowed_path in target.allowed_paths:
            safety = validate_relative_path(allowed_path)
            if safety:
                return f"{target.name} 白名单路径不安全：{safety}"
            if not (project_path / allowed_path).is_file():
                return f"{target.name} 白名单文件不存在：{allowed_path}"
    return ""


def create_fullstack_worktree(*, project_path: Path, worktree_root: Path, worktree_path: Path) -> str:
    worktree_root.mkdir(parents=True, exist_ok=True)
    run_command(["git", "worktree", "prune"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    if worktree_path.exists():
        return f"目标 worktree 已存在，拒绝覆盖；请先使用安全清理预览处理：{worktree_path}"
    create_worktree_marker(
        worktree_root=worktree_root,
        worktree_path=worktree_path,
        project_path=project_path,
        run_id=worktree_path.name.removeprefix("run_"),
        role="fullstack",
    )
    result = run_command(["git", "worktree", "add", "--detach", str(worktree_path), "HEAD"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
    if result["returncode"] != 0:
        remove_worktree_marker(worktree_root=worktree_root, worktree_path=worktree_path)
        return summarize_command(result)
    return ""


def run_target_patch(*, target: FullstackPatchTarget, worktree_path: Path) -> dict:
    patch = build_target_patch(target=target, worktree_path=worktree_path)
    record: dict = {"status": "running", "patch": patch, "changed_paths": []}
    validation = validate_patch(patch, allowed_paths=target.allowed_paths)
    record["changed_paths"] = validation.changed_paths
    if not validation.ok:
        record["status"] = "rejected"
        record["message"] = validation.message
        return record
    apply_check = run_command(["git", "apply", "--check", "-"], cwd=worktree_path, input_text=patch, timeout=PATCH_TIMEOUT_SECONDS)
    record["apply_check"] = apply_check
    if apply_check["returncode"] != 0:
        record["status"] = "apply_check_failed"
        record["message"] = "git apply --check 失败"
        return record
    apply_result = run_command(["git", "apply", "-"], cwd=worktree_path, input_text=patch, timeout=PATCH_TIMEOUT_SECONDS)
    record["apply"] = apply_result
    if apply_result["returncode"] != 0:
        record["status"] = "apply_failed"
        record["message"] = "git apply 失败"
        return record
    diff_check = run_command(["git", "diff", "--check"], cwd=worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
    record["diff_check"] = diff_check
    if diff_check["returncode"] != 0:
        record["status"] = "diff_check_failed"
        record["message"] = "git diff --check 失败"
        return record
    verify_results = []
    for command in target.verify_commands:
        before = capture_worktree_snapshot(worktree_path)
        verify = run_shell_command(command, cwd=worktree_path, timeout=VERIFY_TIMEOUT_SECONDS)
        after = capture_worktree_snapshot(worktree_path)
        side_effect = build_side_effect_report(before_snapshot=before, after_snapshot=after, side=target.key)
        verify = {**verify, "before_snapshot": before, "after_snapshot": after, "side_effects": side_effect}
        verify_results.append(verify)
        if verify["returncode"] != 0:
            record["status"] = "verify_failed"
            record["message"] = f"{target.name} 验证命令失败：{command}"
            record["verify"] = verify_results
            return record
        if side_effect.get("changed"):
            record["status"] = "verify_side_effect_failed"
            record["message"] = f"{target.name} 验证命令修改了临时 worktree。"
            record["verify"] = verify_results
            return record
    final_diff = run_command(["git", "diff", "--no-ext-diff"], cwd=worktree_path, timeout=PATCH_TIMEOUT_SECONDS)
    record["verify"] = verify_results
    record["final_diff"] = final_diff["stdout"]
    record["status"] = "success"
    record["message"] = "Patch 已在独立 worktree 中通过验证。"
    return record


def build_target_patch(*, target: FullstackPatchTarget, worktree_path: Path) -> str:
    relative = target.allowed_paths[0]
    path = worktree_path / relative
    original = path.read_text(encoding="utf-8")
    if target.patch_kind == "dfhis_31270_service_dto":
        updated = add_java_bei_zhu_field(original)
    elif target.patch_kind == "dfhis_31270_bff_graphql":
        updated = add_graphql_bei_zhu_field(original)
    elif target.patch_kind == "dfhis_31270_frontend_vue":
        updated = add_vue_bei_zhu_column(original)
    elif target.patch_kind == "mock_append_marker":
        marker = "# FULLSTACK_WORKTREE_SELF_CHECK"
        updated = original if marker in original else original.rstrip("\n") + "\n" + marker + "\n"
    else:
        return f"NO_PATCH: 不支持的 fullstack patch_kind：{target.patch_kind}"
    if updated == original:
        return f"NO_PATCH: {target.name} 已满足或无法生成最小 patch。"
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )


def add_java_bei_zhu_field(text: str) -> str:
    if re.search(r"private\s+String\s+beiZhu\s*;", text):
        return text
    anchor = "    private String pingZhengHao;\n"
    insert = (
        "\n"
        "    /**\n"
        "     *备注\n"
        "     *ZY_YUJIAOKUAN~BEIZHU~varchar~备注~\n"
        "     */\n"
        "    private String beiZhu;\n"
    )
    if anchor not in text:
        return text
    return text.replace(anchor, anchor + insert, 1)


def add_graphql_bei_zhu_field(text: str) -> str:
    block_match = re.search(r"type DTO_ZY_YuJiaoKuan\{(?P<body>.*?)\n\}", text, re.DOTALL)
    if not block_match:
        return text
    body = block_match.group("body")
    if re.search(r"\bbeiZhu\s*:", body):
        return text
    old = "     pingZhengHao : String,\n     zhiFuFsMc : String,"
    new = "     pingZhengHao : String,\n     beiZhu : String,\n     zhiFuFsMc : String,"
    if old not in text:
        return text
    return text.replace(old, new, 1)


def add_vue_bei_zhu_column(text: str) -> str:
    updated = text
    if 'grid-view-name="jieSuanInfo_YuJiaoKuanInfo"' not in updated:
        updated = re.sub(
            r"(?m)^([ \t]*):allowColumnConfig=\"false\"(\r?\n[ \t]*:grid-data-columns=\"yuJiaoJinColumns\")",
            r'\1grid-view-name="jieSuanInfo_YuJiaoKuanInfo"\2',
            updated,
            count=1,
        )
    if "dataField: 'beiZhu'" in updated:
        return updated
    anchor = (
        "        {\n"
        "          caption: '收款人',\n"
        "          dataField: 'shouKuanRenXm',\n"
        "          width: 120,\n"
        "          allowSorting: false\n"
        "        },\n"
    )
    insert = (
        "        {\n"
        "          caption: '备注',\n"
        "          dataField: 'beiZhu',\n"
        "          width: 140,\n"
        "          allowSorting: false\n"
        "        },\n"
    )
    if anchor in updated:
        return updated.replace(anchor, anchor + insert, 1)
    anchor_match = re.search(
        r"        \{\n"
        r"          caption: '收款人',\n"
        r"          dataField: 'shouKuanRenXm',\n"
        r"          width: 120,\n"
        r"          allowSorting: false\n"
        r"        \},?",
        updated,
    )
    if not anchor_match:
        return updated
    anchor_text = anchor_match.group(0)
    replacement = anchor_text if anchor_text.endswith(",") else anchor_text + ","
    replacement += "\n" + insert.rstrip("\n")
    return updated[: anchor_match.start()] + replacement + updated[anchor_match.end() :]


def precheck_all_project_applies(*, targets: list[FullstackPatchTarget], final_diffs: dict[str, str], apply_results: dict[str, dict]) -> str:
    for target in targets:
        diff = final_diffs.get(target.key, "")
        project_path = Path(target.project_path).expanduser().resolve()
        apply_results[target.key] = {"status": "prechecking", "project_path": str(project_path)}
        if not diff.strip():
            return f"{target.name} final.diff 为空，拒绝合入。"
        status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        apply_results[target.key]["pre_apply_status"] = status
        if status["returncode"] != 0:
            return f"无法读取 {target.name} 原目录 git status。"
        if status["stdout"].strip():
            return f"{target.name} 原目录存在未提交改动，拒绝合入。"
        check = run_command(["git", "apply", "--check", "-"], cwd=project_path, input_text=diff, timeout=PATCH_TIMEOUT_SECONDS)
        apply_results[target.key]["apply_check"] = check
        if check["returncode"] != 0:
            return f"{target.name} 原目录 git apply --check 失败。"
    return ""


def apply_all_to_projects(*, targets: list[FullstackPatchTarget], final_diffs: dict[str, str], apply_results: dict[str, dict]) -> str:
    for target in targets:
        project_path = Path(target.project_path).expanduser().resolve()
        diff = final_diffs.get(target.key, "")
        result = apply_results.setdefault(target.key, {"project_path": str(project_path)})
        apply = run_command(["git", "apply", "-"], cwd=project_path, input_text=diff, timeout=PATCH_TIMEOUT_SECONDS)
        result["apply"] = apply
        if apply["returncode"] != 0:
            result["status"] = "failed"
            result["message"] = f"{target.name} git apply 失败。"
            return result["message"]
        diff_check = run_command(["git", "diff", "--check"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        result["diff_check"] = diff_check
        if diff_check["returncode"] != 0:
            result["status"] = "failed"
            result["message"] = f"{target.name} 已合入但 git diff --check 失败，需要人工处理。"
            return result["message"]
        post_status = run_command(["git", "status", "--porcelain"], cwd=project_path, timeout=PATCH_TIMEOUT_SECONDS)
        result["post_apply_status"] = post_status
        result["changed_paths"] = parse_status_paths(post_status.get("stdout", ""))
        result["status"] = "success"
        result["message"] = "final.diff 已合入原业务目录；未提交、未推送、未发布。"
    return ""


def parse_status_paths(status_text: str) -> list[str]:
    paths: list[str] = []
    for line in status_text.splitlines():
        if not line.strip():
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            paths.extend(part.strip() for part in path.split(" -> ", 1))
        else:
            paths.append(path.strip())
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        normalized = normalize_relative_path(path)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def failed_result(
    summary: str,
    targets: list[dict],
    final_diffs: dict[str, str],
    apply_results: dict[str, dict],
    cleanup_results: dict[str, dict],
    manifest: dict,
) -> FullstackExecutionResult:
    manifest["status"] = "failed"
    manifest["summary"] = summary
    manifest["finished_at_epoch"] = time.time()
    return FullstackExecutionResult(
        status="failed",
        summary=summary,
        targets=targets,
        final_diffs=final_diffs,
        apply_to_projects=apply_results,
        cleanup=cleanup_results,
        manifest=manifest,
    )


def target_to_record(target: FullstackPatchTarget) -> dict:
    return {
        **asdict(target),
        "status": "pending",
        "message": "",
        "changed_paths": [],
        "worktree_path": "",
    }


def record_for_key(records: list[dict], key: str) -> dict:
    for record in records:
        if record.get("key") == key:
            return record
    raise KeyError(key)


def safe_worktree_suffix(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "project"
