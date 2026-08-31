from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from history_manager import (  # noqa: E402
    apply_project_patch,
    archive_evidence,
    archive_project_patch,
    build_parser,
    create_project_worktree,
    record_change_decision,
    record_codex_review,
    record_project,
    record_stage,
    record_verification,
    rebuild_state,
    validate_task,
)


def write_fixture(root: Path, *, ticket_id: str = "DFHIS-90001") -> Path:
    file_path = root / "files" / "item-1" / "original.png"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"original requirement")
    evidence = {
        "contract_version": "requirement-evidence.v2",
        "provider": "yunxiao",
        "mode": "readonly",
        "source": {
            "input": ticket_id,
            "requested_id": ticket_id,
            "resolved_work_item_id": "item-1",
            "fetched_at": "2026-07-24T14:30:00+08:00",
        },
        "policy": {"allowed_actions": ["read"], "blocked_actions": ["comment"]},
        "decision_gate": {"state": "ready_for_analysis", "reasons": []},
        "completeness": {
            "status": "complete",
            "request_count": 1,
            "failed_request_count": 0,
        },
        "root_work_item_id": "item-1",
        "lineage": ["item-1"],
        "work_items": [
            {
                "id": "item-1",
                "serial_number": ticket_id,
                "title": "测试原始需求",
                "category": "Bug",
                "role": "requested",
                "attachments": [
                    {
                        "name": "original.png",
                        "source_url": "https://example.invalid/file",
                        "download_status": "success",
                        "local_path": "files/item-1/original.png",
                        "size": file_path.stat().st_size,
                        "sha256": hashlib.sha256(file_path.read_bytes()).hexdigest(),
                    }
                ],
                "inline_files": [],
            }
        ],
        "relations": [],
        "warnings": [],
        "errors": [],
        "request_log": [{"operation": "get_work_item", "status": "success"}],
        "integrity": {"algorithm": "sha256", "evidence_sha256": ""},
    }
    rehash(evidence)
    (root / "requirement_evidence.v2.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "requirement_evidence.v2.md").write_text("# 测试原始需求\n", encoding="utf-8")
    return root


def rehash(evidence: dict) -> None:
    payload = dict(evidence)
    payload.pop("integrity")
    evidence["integrity"]["evidence_sha256"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def create_git_repo(root: Path) -> tuple[Path, str]:
    repo = root / "repo"
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(repo, "config", "user.name", "Harness Test")
    git(repo, "config", "user.email", "harness@example.invalid")
    (repo / "app.txt").write_text("before\n", encoding="utf-8")
    (repo / "other.txt").write_text("stable\n", encoding="utf-8")
    git(repo, "add", "app.txt", "other.txt")
    git(repo, "commit", "-m", "baseline")
    return repo, git(repo, "rev-parse", "HEAD")


class HarnessHistoryTests(unittest.TestCase):
    def test_cli_exposes_structured_change_lifecycle(self):
        help_text = build_parser().format_help()
        for command in (
            "record-decision",
            "create-worktree",
            "archive-patch",
            "record-review",
            "record-verification",
            "apply-back",
        ):
            self.assertIn(command, help_text)

    def test_skill_defines_append_only_history_and_external_write_boundary(self):
        skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("TODO", skill_text)
        self.assertIn("append-only", skill_text)
        self.assertIn("云效写操作", skill_text)
        self.assertIn("history_manager.py", skill_text)
        for term in (
            "record-decision",
            "create-worktree",
            "archive-patch",
            "record-review",
            "record-verification",
            "apply-back",
            "detached",
        ):
            self.assertIn(term, skill_text)
        catalog = (SKILL_DIR.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("`harness-history`", catalog)
        self.assertIn("真实 GET-only 验收通过", catalog)

    def test_archives_evidence_and_records_project_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            source = write_fixture(base / "source")
            history_root = base / "HarnessHistory"
            result = archive_evidence(
                source_dir=source,
                history_root=history_root,
                provider="YUNXIAO",
                ticket_id="DFHIS-90001",
                run_id="20260724-143000",
            )
            task = history_root / "YUNXIAO" / "DFHIS-90001"
            archived_file = (
                task
                / "evidence/revisions/20260724-143000/files/item-1/original.png"
            )
            self.assertTrue(archived_file.is_file())
            self.assertEqual("ready_for_analysis", result["decision_gate"])
            self.assertTrue((task / "runs/20260724-143000/run.json").is_file())
            self.assertTrue((task / "worktrees/20260724-143000").is_dir())

            project = record_project(
                task_dir=task,
                run_id="20260724-143000",
                name="df-web-bui",
                repo_path="/Users/lym/Desktop/dongFang/dfcode/df-web-bui",
                role="primary",
                reason="收费页面代码入口",
                historical_commits=["a" * 40],
            )
            self.assertEqual("df-web-bui", project["name"])
            self.assertEqual(["a" * 40], project["historical_commits"])
            run_dir = task / "runs/20260724-143000"
            state = json.loads(
                (run_dir / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual("completed", state["stages"]["project_mapping"])
            self.assertIn(
                "project_mapping: completed",
                (run_dir / "STATUS.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                2,
                len(list((run_dir / "events").glob("*.json"))),
            )
            review = record_stage(
                task_dir=task,
                run_id="20260724-143000",
                stage="codex_review",
                status="blocked",
                summary="等待结构化评审",
            )
            self.assertEqual("blocked", review["status"])
            state = json.loads(
                (run_dir / "run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual("blocked", state["stages"]["codex_review"])
            self.assertTrue(
                (run_dir / "reviews/0003-codex_review.json").is_file()
            )
            mapping_state = record_stage(
                task_dir=task,
                run_id="20260724-143000",
                stage="project_mapping",
                status="completed",
                summary="项目映射复核完成",
            )
            self.assertEqual("completed", mapping_state["status"])
            self.assertTrue(
                (run_dir / "stage-records/0004-project_mapping.json").is_file()
            )
            self.assertEqual([], validate_task(task))

            with self.assertRaises(FileExistsError):
                archive_evidence(
                    source_dir=source,
                    history_root=history_root,
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )

            with self.assertRaisesRegex(ValueError, "invalid status"):
                record_stage(
                    task_dir=task,
                    run_id="20260724-143000",
                    stage="change_decision",
                    status="passed",
                    summary="错误的阶段状态",
                )
            for stage, status in (
                ("change_decision", "can_change"),
                ("implementation", "completed"),
                ("codex_review", "passed"),
                ("verification", "passed"),
                ("apply_back", "applied"),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "structured command",
                ):
                    record_stage(
                        task_dir=task,
                        run_id="20260724-143000",
                        stage=stage,
                        status=status,
                        summary="不允许绕过结构化记录",
                    )
            with self.assertRaises(FileExistsError):
                record_project(
                    task_dir=task,
                    run_id="20260724-143000",
                    name="df-web-bui",
                    repo_path="/repo/df-web-bui",
                    role="primary",
                    reason="重复记录",
                )

            with self.assertRaisesRegex(ValueError, "invalid historical commit"):
                record_project(
                    task_dir=task,
                    run_id="20260724-143000",
                    name="df-web-other",
                    repo_path="/repo/df-web-other",
                    role="affected",
                    reason="测试无效提交",
                    historical_commits=["not-a-commit"],
                )

    def test_rejects_escaping_path_and_ticket_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            source = write_fixture(base / "source")
            evidence_path = source / "requirement_evidence.v2.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["work_items"][0]["attachments"][0]["local_path"] = "../outside.png"
            rehash(evidence)
            evidence_path.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "safe relative path"):
                archive_evidence(
                    source_dir=source,
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )

            clean_source = write_fixture(base / "clean-source")
            with self.assertRaisesRegex(ValueError, "ticket mismatch"):
                archive_evidence(
                    source_dir=clean_source,
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90002",
                    run_id="20260724-143001",
                )

    def test_validation_detects_archived_attachment_tampering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = Path(
                archive_evidence(
                    source_dir=write_fixture(base / "source"),
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )["task_dir"]
            )
            archived_file = (
                task
                / "evidence/revisions/20260724-143000/files/item-1/original.png"
            )
            archived_file.write_bytes(b"tampered")
            self.assertTrue(
                any(
                    "SHA-256 mismatch" in error or "size mismatch" in error
                    for error in validate_task(task)
                )
            )

    def test_validation_cross_checks_manifest_and_optional_intake(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = Path(
                archive_evidence(
                    source_dir=write_fixture(base / "source"),
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )["task_dir"]
            )
            run_dir = task / "runs/20260724-143000"
            intake_dir = run_dir / "intake"
            intake_dir.mkdir()
            request = {
                "contract_version": "harness-intake.v1",
                "provider": "YUNXIAO",
                "ticket_id": "DFHIS-90001",
                "run_id": "20260724-143000",
                "source": "DFHIS-90001",
                "adapter_skill": "yunxiao-workitem-evidence",
                "credential_kind": "read",
                "requested_at": "2026-07-24T14:30:00+08:00",
                "decision_gate": "ready_for_analysis",
                "completeness": "complete",
                "intake_status": "accepted",
                "next_action": "start_readonly_analysis",
            }
            (intake_dir / "request.json").write_text(
                json.dumps(request, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertEqual([], validate_task(task))

            request["source"] = "DFHIS-90001 token=SENTINEL"
            (intake_dir / "request.json").write_text(
                json.dumps(request, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertTrue(
                any("intake source" in error for error in validate_task(task))
            )

            request["source"] = "DFHIS-90001"
            (intake_dir / "request.json").write_text(
                json.dumps(request, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest_path = run_dir / "evidence-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["provider"] = "OTHER"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertTrue(
                any("manifest provider mismatch" in error for error in validate_task(task))
            )

    def test_intake_required_policy_and_source_sanitization_are_enforced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = Path(
                archive_evidence(
                    source_dir=write_fixture(base / "source"),
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                    intake_required=True,
                )["task_dir"]
            )
            run_dir = task / "runs/20260724-143000"
            self.assertTrue(
                any("required intake request is missing" in error for error in validate_task(task))
            )
            intake_dir = run_dir / "intake"
            intake_dir.mkdir()
            request = {
                "contract_version": "harness-intake.v1",
                "provider": "YUNXIAO",
                "ticket_id": "DFHIS-90001",
                "run_id": "20260724-143000",
                "source": "https://user:secret@devops.aliyun.com/organization/x/DFHIS-90001",
                "adapter_skill": "yunxiao-workitem-evidence",
                "credential_kind": "write",
                "requested_at": "2026-07-24T14:30:00+08:00",
                "decision_gate": "ready_for_analysis",
                "completeness": "complete",
                "intake_status": "accepted",
                "next_action": "start_readonly_analysis",
            }
            (intake_dir / "request.json").write_text(
                json.dumps(request, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertTrue(
                any("sanitized official URL" in error for error in validate_task(task))
            )
            request["source"] = "DFHIS-90001"
            (intake_dir / "request.json").write_text(
                json.dumps(request, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertEqual([], validate_task(task))

    def test_validator_reports_malformed_structured_record_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = Path(
                archive_evidence(
                    source_dir=write_fixture(base / "source"),
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )["task_dir"]
            )
            malformed = (
                task
                / "runs/20260724-143000/decisions/9999-change_decision.json"
            )
            malformed.write_text("{", encoding="utf-8")
            errors = validate_task(task)
            self.assertTrue(any("structured record is invalid" in error for error in errors))

    def test_worktree_mapping_must_stay_inside_task_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = Path(
                archive_evidence(
                    source_dir=write_fixture(base / "source"),
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )["task_dir"]
            )
            with self.assertRaisesRegex(ValueError, "must stay inside"):
                record_project(
                    task_dir=task,
                    run_id="20260724-143000",
                    name="df-web-bui",
                    repo_path="/repo/df-web-bui",
                    role="primary",
                    reason="收费页面代码入口",
                    worktree_path="/tmp/outside-worktree",
                )

    def test_full_local_change_lifecycle_is_auditable_and_applies_safely(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            repo, base_commit = create_git_repo(base)
            task = Path(
                archive_evidence(
                    source_dir=write_fixture(base / "source"),
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )["task_dir"]
            )
            run_id = "20260724-143000"
            worktree = task / "worktrees" / run_id / "demo-project"
            record_project(
                task_dir=task,
                run_id=run_id,
                name="demo-project",
                repo_path=repo,
                role="primary",
                reason="测试目标项目",
                worktree_path=worktree,
                base_branch="main",
                base_commit=base_commit,
            )
            record_stage(
                task_dir=task,
                run_id=run_id,
                stage="analysis",
                status="completed",
                summary="调用链和影响范围已确认",
            )
            decision = record_change_decision(
                task_dir=task,
                run_id=run_id,
                verdict="can_change",
                reason="根因明确且可通过单文件最小改动修复",
                projects=["demo-project"],
                evidence=["analysis/调用链定位"],
                change_scope="仅修改 app.txt",
                allowed_paths={"demo-project": ["app.txt"]},
            )
            self.assertEqual("can_change", decision["verdict"])

            worktree_record = create_project_worktree(
                task_dir=task,
                run_id=run_id,
                project="demo-project",
            )
            self.assertEqual(base_commit, worktree_record["base_commit"])
            self.assertEqual("detached", worktree_record["checkout_mode"])
            (worktree / "app.txt").write_text("after\n", encoding="utf-8")

            patch_record = archive_project_patch(
                task_dir=task,
                run_id=run_id,
                project="demo-project",
            )
            self.assertEqual(["M\tapp.txt"], patch_record["changed_files"])
            self.assertTrue(Path(patch_record["patch_path"]).is_file())

            with self.assertRaisesRegex(ValueError, "unresolved"):
                record_codex_review(
                    task_dir=task,
                    run_id=run_id,
                    verdict="passed",
                    summary="不应通过",
                    can_fix=True,
                    findings=[
                        {
                            "severity": "Important",
                            "title": "仍有重要问题",
                            "resolved": False,
                        }
                    ],
                )
            failed_review = record_codex_review(
                task_dir=task,
                run_id=run_id,
                verdict="failed",
                summary="当前补丁不能安全修复该问题",
                can_fix=False,
                cannot_fix_reason="当前方案破坏兼容路径",
                findings=[
                    {
                        "severity": "Important",
                        "title": "兼容路径被破坏",
                        "resolved": False,
                    }
                ],
            )
            self.assertEqual("explain_and_stop", failed_review["next_action"])
            with self.assertRaisesRegex(ValueError, "terminal"):
                record_codex_review(
                    task_dir=task,
                    run_id=run_id,
                    verdict="passed",
                    summary="不能复用同一补丁重新通过",
                    can_fix=True,
                    findings=[],
                )
            patch_record = archive_project_patch(
                task_dir=task,
                run_id=run_id,
                project="demo-project",
            )
            with self.assertRaisesRegex(ValueError, "terminal"):
                record_codex_review(
                    task_dir=task,
                    run_id=run_id,
                    verdict="passed",
                    summary="重新归档相同内容也不能绕过终止结论",
                    can_fix=True,
                    findings=[],
                )
            (worktree / "app.txt").write_text("after-fix\n", encoding="utf-8")
            patch_record = archive_project_patch(
                task_dir=task,
                run_id=run_id,
                project="demo-project",
            )
            review = record_codex_review(
                task_dir=task,
                run_id=run_id,
                verdict="passed",
                summary="逐行 diff 评审通过",
                can_fix=True,
                findings=[],
            )
            self.assertEqual("verify_patch", review["next_action"])
            with self.assertRaisesRegex(ValueError, "exit_code"):
                record_verification(
                    task_dir=task,
                    run_id=run_id,
                    status="passed",
                    summary="布尔值不能冒充退出码 0",
                    checks=[
                        {
                            "name": "invalid-exit-code",
                            "command": "test app.txt",
                            "exit_code": False,
                            "result": "passed",
                        }
                    ],
                )
            verification = record_verification(
                task_dir=task,
                run_id=run_id,
                status="passed",
                summary="专项验证通过",
                checks=[
                    {
                        "name": "targeted-test",
                        "command": "test app.txt",
                        "exit_code": 0,
                        "result": "passed",
                    }
                ],
            )
            self.assertEqual("passed", verification["status"])

            (worktree / "app.txt").write_text(
                "after-rereview\n",
                encoding="utf-8",
            )
            patch_record = archive_project_patch(
                task_dir=task,
                run_id=run_id,
                project="demo-project",
            )
            reset_state = json.loads(
                (task / "runs" / run_id / "run-state.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("pending", reset_state["stages"]["codex_review"])
            self.assertEqual("pending", reset_state["stages"]["verification"])
            with self.assertRaisesRegex(ValueError, "codex_review=passed"):
                apply_project_patch(
                    task_dir=task,
                    run_id=run_id,
                    project="demo-project",
                    ack_local_write=True,
                )
            record_codex_review(
                task_dir=task,
                run_id=run_id,
                verdict="passed",
                summary="新补丁重新评审通过",
                can_fix=True,
                findings=[],
            )
            record_verification(
                task_dir=task,
                run_id=run_id,
                status="passed",
                summary="新补丁重新验证通过",
                checks=[
                    {
                        "name": "targeted-test",
                        "command": "test app.txt",
                        "exit_code": 0,
                        "result": "passed",
                    }
                ],
            )

            evidence_markdown = (
                task
                / "evidence/revisions/20260724-143000/requirement_evidence.v2.md"
            )
            markdown_content = evidence_markdown.read_text(encoding="utf-8")
            evidence_markdown.unlink()
            with self.assertRaisesRegex(ValueError, "history validation failed"):
                apply_project_patch(
                    task_dir=task,
                    run_id=run_id,
                    project="demo-project",
                    ack_local_write=True,
                )
            evidence_markdown.write_text(markdown_content, encoding="utf-8")

            with self.assertRaisesRegex(PermissionError, "ack_local_write"):
                apply_project_patch(
                    task_dir=task,
                    run_id=run_id,
                    project="demo-project",
                    ack_local_write=False,
                )
            (repo / "app.txt").write_text("after-rereview\n", encoding="utf-8")
            (repo / "other.txt").write_text("unrelated\n", encoding="utf-8")
            mixed = apply_project_patch(
                task_dir=task,
                run_id=run_id,
                project="demo-project",
                ack_local_write=True,
            )
            self.assertEqual("blocked", mixed["status"])
            (repo / "app.txt").write_text("before\n", encoding="utf-8")
            (repo / "other.txt").write_text("stable\n", encoding="utf-8")
            applied = apply_project_patch(
                task_dir=task,
                run_id=run_id,
                project="demo-project",
                ack_local_write=True,
            )
            self.assertEqual("applied", applied["status"])
            self.assertEqual(
                "after-rereview\n",
                (repo / "app.txt").read_text(encoding="utf-8"),
            )

            state = json.loads(
                (task / "runs" / run_id / "run-state.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("can_change", state["stages"]["change_decision"])
            self.assertEqual("completed", state["stages"]["implementation"])
            self.assertEqual("passed", state["stages"]["codex_review"])
            self.assertEqual("passed", state["stages"]["verification"])
            self.assertEqual("applied", state["stages"]["apply_back"])
            self.assertEqual([], validate_task(task))
            Path(patch_record["patch_path"]).write_bytes(b"tampered")
            self.assertTrue(
                any(
                    "archived patch SHA-256 mismatch" in error
                    for error in validate_task(task)
                )
            )

    def test_rename_requires_both_source_and_destination_in_allowlist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            repo, base_commit = create_git_repo(base)
            task = Path(
                archive_evidence(
                    source_dir=write_fixture(base / "source"),
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )["task_dir"]
            )
            run_id = "20260724-143000"
            worktree = task / "worktrees" / run_id / "demo-project"
            record_project(
                task_dir=task,
                run_id=run_id,
                name="demo-project",
                repo_path=repo,
                role="primary",
                reason="测试重命名白名单",
                worktree_path=worktree,
                base_branch="main",
                base_commit=base_commit,
            )
            record_stage(
                task_dir=task,
                run_id=run_id,
                stage="analysis",
                status="completed",
                summary="重命名影响范围已确认",
            )
            record_change_decision(
                task_dir=task,
                run_id=run_id,
                verdict="can_change",
                reason="仅允许创建 renamed.txt",
                projects=["demo-project"],
                evidence=["analysis/重命名检查"],
                allowed_paths={"demo-project": ["renamed.txt"]},
            )
            create_project_worktree(
                task_dir=task,
                run_id=run_id,
                project="demo-project",
            )
            git(worktree, "mv", "other.txt", "renamed.txt")
            with self.assertRaisesRegex(ValueError, "outside decision allowlist"):
                archive_project_patch(
                    task_dir=task,
                    run_id=run_id,
                    project="demo-project",
                )

    def test_new_file_patch_applies_and_validates_with_exact_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            repo, base_commit = create_git_repo(base)
            (repo / ".gitignore").write_text("new.txt\n", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-m", "ignore generated file")
            base_commit = git(repo, "rev-parse", "HEAD")
            task = Path(
                archive_evidence(
                    source_dir=write_fixture(base / "source"),
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )["task_dir"]
            )
            run_id = "20260724-143000"
            worktree = task / "worktrees" / run_id / "demo-project"
            record_project(
                task_dir=task,
                run_id=run_id,
                name="demo-project",
                repo_path=repo,
                role="primary",
                reason="测试新增文件回写",
                worktree_path=worktree,
                base_branch="main",
                base_commit=base_commit,
            )
            record_stage(
                task_dir=task,
                run_id=run_id,
                stage="analysis",
                status="completed",
                summary="新增文件范围已确认",
            )
            record_change_decision(
                task_dir=task,
                run_id=run_id,
                verdict="can_change",
                reason="新增单个允许文件",
                projects=["demo-project"],
                evidence=["analysis/新增文件"],
                allowed_paths={"demo-project": ["new.txt"]},
            )
            create_project_worktree(
                task_dir=task,
                run_id=run_id,
                project="demo-project",
            )
            (worktree / "new.txt").write_text("new\n", encoding="utf-8")
            git(worktree, "add", "-f", "-N", "--", "new.txt")
            patch = archive_project_patch(
                task_dir=task,
                run_id=run_id,
                project="demo-project",
            )
            self.assertEqual(["new.txt"], patch["changed_paths"])
            record_codex_review(
                task_dir=task,
                run_id=run_id,
                verdict="passed",
                summary="新增文件补丁评审通过",
                can_fix=True,
                findings=[],
            )
            record_verification(
                task_dir=task,
                run_id=run_id,
                status="passed",
                summary="新增文件专项验证通过",
                checks=[
                    {
                        "name": "new-file",
                        "command": "test -f new.txt",
                        "exit_code": 0,
                        "result": "passed",
                    }
                ],
            )
            index_tree = git(repo, "write-tree")
            applied = apply_project_patch(
                task_dir=task,
                run_id=run_id,
                project="demo-project",
                ack_local_write=True,
            )
            self.assertEqual("applied", applied["status"])
            self.assertEqual("new\n", (repo / "new.txt").read_text(encoding="utf-8"))
            self.assertEqual(index_tree, git(repo, "write-tree"))
            self.assertEqual("", git(repo, "status", "--porcelain=v1"))
            self.assertEqual([], validate_task(task))

    def test_cannot_change_records_reason_and_blocks_worktree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            repo, base_commit = create_git_repo(base)
            task = Path(
                archive_evidence(
                    source_dir=write_fixture(base / "source"),
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )["task_dir"]
            )
            run_id = "20260724-143000"
            record_project(
                task_dir=task,
                run_id=run_id,
                name="demo-project",
                repo_path=repo,
                role="primary",
                reason="测试目标项目",
                worktree_path=(
                    task / "worktrees" / run_id / "demo-project"
                ),
                base_branch="main",
                base_commit=base_commit,
            )
            record_stage(
                task_dir=task,
                run_id=run_id,
                stage="analysis",
                status="completed",
                summary="确认需求依赖缺失的后端契约",
            )
            decision = record_change_decision(
                task_dir=task,
                run_id=run_id,
                verdict="cannot_change",
                reason="缺少后端字段定义，前端无法安全推断",
                projects=["demo-project"],
                evidence=["analysis/接口字段来源"],
                blockers=["后端契约未确认"],
            )
            self.assertEqual("explain_and_stop", decision["next_action"])
            state = json.loads(
                (task / "runs" / run_id / "run-state.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("skipped", state["stages"]["implementation"])
            with self.assertRaisesRegex(ValueError, "can_change"):
                create_project_worktree(
                    task_dir=task,
                    run_id=run_id,
                    project="demo-project",
                )

    def test_decision_scope_and_path_allowlist_gate_worktree_and_patch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            allowed_repo, base_commit = create_git_repo(base / "allowed-root")
            outside_repo, outside_commit = create_git_repo(base / "outside-root")
            task = Path(
                archive_evidence(
                    source_dir=write_fixture(base / "source"),
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )["task_dir"]
            )
            run_id = "20260724-143000"
            for name, repo, commit in (
                ("allowed", allowed_repo, base_commit),
                ("outside-decision", outside_repo, outside_commit),
            ):
                record_project(
                    task_dir=task,
                    run_id=run_id,
                    name=name,
                    repo_path=repo,
                    role="primary",
                    reason="范围门禁测试",
                    worktree_path=task / "worktrees" / run_id / name,
                    base_branch="main",
                    base_commit=commit,
                )
            record_stage(
                task_dir=task,
                run_id=run_id,
                stage="analysis",
                status="completed",
                summary="已确认只允许修改 allowed/app.txt",
            )
            record_change_decision(
                task_dir=task,
                run_id=run_id,
                verdict="can_change",
                reason="仅 allowed 项目需要修改",
                projects=["allowed"],
                evidence=["analysis/项目范围"],
                change_scope="单文件修改",
                allowed_paths={"allowed": ["app.txt"]},
            )
            with self.assertRaisesRegex(ValueError, "decision scope"):
                create_project_worktree(
                    task_dir=task,
                    run_id=run_id,
                    project="outside-decision",
                )
            create_project_worktree(
                task_dir=task,
                run_id=run_id,
                project="allowed",
            )
            worktree = task / "worktrees" / run_id / "allowed"
            (worktree / "other.txt").write_text("outside\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "allowlist"):
                archive_project_patch(
                    task_dir=task,
                    run_id=run_id,
                    project="allowed",
                )

    def test_validator_rejects_missing_evidence_markdown_and_event_ledger_gap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = Path(
                archive_evidence(
                    source_dir=write_fixture(base / "source"),
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )["task_dir"]
            )
            run_id = "20260724-143000"
            (
                task
                / "evidence/revisions"
                / run_id
                / "requirement_evidence.v2.md"
            ).unlink()
            (task / "runs" / run_id / "events/0001-evidence_archived.json").unlink()
            rebuild_state(task_dir=task, run_id=run_id)
            errors = validate_task(task)
            self.assertTrue(
                any("requirement_evidence.v2.md" in error for error in errors)
            )
            self.assertTrue(
                any("evidence_archived" in error for error in errors)
            )

    def test_worktree_symlink_root_is_rejected_before_git_creation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            repo, base_commit = create_git_repo(base / "repo-root")
            task = Path(
                archive_evidence(
                    source_dir=write_fixture(base / "source"),
                    history_root=base / "history",
                    provider="YUNXIAO",
                    ticket_id="DFHIS-90001",
                    run_id="20260724-143000",
                )["task_dir"]
            )
            run_id = "20260724-143000"
            record_project(
                task_dir=task,
                run_id=run_id,
                name="demo-project",
                repo_path=repo,
                role="primary",
                reason="symlink 逃逸测试",
                worktree_path=task / "worktrees" / run_id / "demo-project",
                base_branch="main",
                base_commit=base_commit,
            )
            record_stage(
                task_dir=task,
                run_id=run_id,
                stage="analysis",
                status="completed",
                summary="已完成只读分析",
            )
            record_change_decision(
                task_dir=task,
                run_id=run_id,
                verdict="can_change",
                reason="测试 worktree 根目录边界",
                projects=["demo-project"],
                evidence=["analysis/symlink"],
                allowed_paths={"demo-project": ["app.txt"]},
            )
            run_worktree_root = task / "worktrees" / run_id
            run_worktree_root.rmdir()
            escaped = base / "escaped"
            escaped.mkdir()
            run_worktree_root.symlink_to(escaped, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                create_project_worktree(
                    task_dir=task,
                    run_id=run_id,
                    project="demo-project",
                )
            self.assertTrue(
                any("symlink" in error for error in validate_task(task))
            )


if __name__ == "__main__":
    unittest.main()
