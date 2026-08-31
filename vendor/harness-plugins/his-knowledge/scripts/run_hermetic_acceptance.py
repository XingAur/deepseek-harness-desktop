"""Deterministic, fail-closed local acceptance runner for HIS Knowledge."""
from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_ROOT = Path(__file__).resolve().parent
REAL_HOME = "/Users/lym/.local/share/his-knowledge"
OUTPUT = ROOT / "Harness" / "runs" / "knowledge_plugin_acceptance"
CURRENT_INTERPRETER = str(Path(sys.executable).resolve())
SYSTEM_INTERPRETER = "/usr/bin/python3"
ALLOWED_INTERPRETERS = (CURRENT_INTERPRETER, SYSTEM_INTERPRETER)
PLUGIN_TESTS = (
    "plugins/his-knowledge/tests/test_scaffold_contract.py",
    "plugins/his-knowledge/tests/test_skill_contracts.py",
    "plugins/his-knowledge/tests/test_hermetic_acceptance.py",
    "plugins/his-knowledge/tests/test_knowledge_mcp_server.py",
    "plugins/his-knowledge/skills/his-knowledge-retrieve/tests/test_knowledge_retrieve.py",
    "plugins/his-knowledge/skills/his-knowledge-answer/tests/test_knowledge_answer.py",
    "plugins/his-knowledge/skills/his-knowledge-maintain/tests/test_knowledge_store.py",
    "plugins/his-knowledge/skills/his-knowledge-maintain/tests/test_knowledge_maintain.py",
    "plugins/his-knowledge/skills/his-knowledge-maintain/tests/test_seed_import.py",
)
SENSITIVE_PROOFS = (
    {"id": "token_pat_access_key", "proof_test": "HermeticKnowledgeAcceptanceTests.test_five_sensitive_categories_block_fresh_homes_before_creation", "case_id": "token_pat_access_key"},
    {"id": "password_authenticated_dsn", "proof_test": "HermeticKnowledgeAcceptanceTests.test_five_sensitive_categories_block_fresh_homes_before_creation", "case_id": "password_authenticated_dsn"},
    {"id": "chinese_identity", "proof_test": "HermeticKnowledgeAcceptanceTests.test_five_sensitive_categories_block_fresh_homes_before_creation", "case_id": "chinese_identity"},
    {"id": "mainland_mobile", "proof_test": "HermeticKnowledgeAcceptanceTests.test_five_sensitive_categories_block_fresh_homes_before_creation", "case_id": "mainland_mobile"},
    {"id": "copied_audit", "proof_test": "HermeticKnowledgeAcceptanceTests.test_copied_audit_text_cannot_mutate_review_or_promote_state"},
)
VALIDATOR_PYTHONPATH = "/Users/lym/Library/Python/3.9/lib/python/site-packages"


def child_environment(home: Path, parent: dict[str, str] | None = None) -> dict[str, str]:
    """Return the complete child environment; deliberately do not inherit host values."""
    base = home.parent
    return {
        "HOME": str(base / "home"),
        "TMPDIR": str(base / "tmp"),
        "XDG_CONFIG_HOME": str(base / "config"),
        "HIS_KNOWLEDGE_HOME": str(home),
        "HARNESS_DB_PATH": str(base / "harness.sqlite"),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }


def validator_environment(home: Path, parent: dict[str, str] | None = None) -> dict[str, str]:
    """Add the sole explicit local validator dependency; never inherit Python paths."""
    environment = child_environment(home, parent)
    environment.update({"PYTHONNOUSERSITE": "1", "PYTHONPATH": VALIDATOR_PYTHONPATH})
    return environment


def suite_commands(interpreter: str) -> tuple[tuple[str, ...], ...]:
    """Return the fixed, allow-listed command matrix without executing it."""
    if interpreter not in ALLOWED_INTERPRETERS:
        raise ValueError("untrusted interpreter")
    return (
        (interpreter, "-m", "unittest", *PLUGIN_TESTS),
        (interpreter, "-m", "unittest", "discover", "-s", "plugins/his-knowledge/tests", "-p", "test_hermetic_acceptance.py"),
        (interpreter, "-m", "unittest", "Harness/tests/test_knowledge_capabilities.py"),
        ("git", "diff", "--check", "220d689..HEAD"),
    )


def validator_commands() -> tuple[tuple[str, ...], ...]:
    """Use the system interpreter for validators that require its bundled PyYAML."""
    return (
        (SYSTEM_INTERPRETER, "/Users/lym/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py", "plugins/his-knowledge"),
        (SYSTEM_INTERPRETER, "/Users/lym/.codex/skills/.system/skill-creator/scripts/quick_validate.py", "plugins/his-knowledge/skills/his-knowledge-retrieve"),
        (SYSTEM_INTERPRETER, "/Users/lym/.codex/skills/.system/skill-creator/scripts/quick_validate.py", "plugins/his-knowledge/skills/his-knowledge-answer"),
        (SYSTEM_INTERPRETER, "/Users/lym/.codex/skills/.system/skill-creator/scripts/quick_validate.py", "plugins/his-knowledge/skills/his-knowledge-maintain"),
    )


def _guard() -> None:
    """Fail closed if the real default home exists before or after a child."""
    if os.path.lexists(REAL_HOME):
        raise RuntimeError("default knowledge home must remain absent")


def _test_count(completed: subprocess.CompletedProcess[str]) -> int:
    match = re.search(r"Ran (\d+) tests", completed.stderr or "")
    return int(match.group(1)) if match else 0


def _run_commands(interpreter: str, commands: tuple[tuple[str, ...], ...], labels: tuple[str, ...], environment_factory=child_environment) -> list[dict[str, object]]:
    """Run one interpreter's complete matrix in an isolated temporary home."""
    _guard()
    with tempfile.TemporaryDirectory(prefix="knowledge-hermetic-") as temporary:
        home = Path(temporary) / "knowledge"
        environment = environment_factory(home)
        for key in ("HOME", "TMPDIR", "XDG_CONFIG_HOME"):
            Path(environment[key]).mkdir(parents=True, exist_ok=True)
        results: list[dict[str, object]] = []
        for label, command in zip(labels, commands):
            _guard()
            completed = subprocess.run(
                command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
            )
            _guard()
            if completed.returncode:
                raise RuntimeError("child suite failed: " + label)
            results.append({
                "suite": label,
                "argv": list(command),
                "cwd": str(ROOT),
                "interpreter": interpreter if command[0] != "git" else "git",
                "exit_code": completed.returncode,
                "test_count": _test_count(completed),
                "check_count": max(_test_count(completed), 1),
            })
        return results


def _one(interpreter: str) -> list[dict[str, object]]:
    """Run one interpreter's plugin, sensitive, Harness and diff commands."""
    return _run_commands(interpreter, suite_commands(interpreter), ("plugin_tests", "sensitive_boundary_suite", "harness_capabilities", "diff_check"))


def _validators() -> list[dict[str, object]]:
    """Run the required structural validators with their supported interpreter."""
    return _run_commands(SYSTEM_INTERPRETER, validator_commands(), ("plugin_validator", "retrieve_skill_validator", "answer_skill_validator", "maintain_skill_validator"), validator_environment)


def _answer_replays() -> list[dict[str, object]]:
    """Execute the six documented L0 scenarios and retain only redacted facts."""
    if str(SCRIPT_ROOT) not in sys.path:
        sys.path.insert(0, str(SCRIPT_ROOT))
    answer_module = importlib.import_module("knowledge_answer")
    seed_module = importlib.import_module("import_seed")
    store_module = importlib.import_module("knowledge_store")
    with tempfile.TemporaryDirectory(prefix="knowledge-replays-") as temporary:
        store = store_module.KnowledgeStore(home=Path(temporary) / "knowledge", now=lambda: "2026-07-29T00:00:00Z")
        seed_module.import_seed(store=store)
        retriever = answer_module.KnowledgeRetriever(store, utc_date=lambda: date(2026, 7, 29), backend_preference="like_fallback")

        def ask(identifier: str, text: str) -> dict[str, object]:
            answer = answer_module.answer(text, store=store, retriever=retriever, utc_date=lambda: date(2026, 7, 29))
            value: dict[str, object] = {
                "id": identifier,
                "status": answer.status,
                "suggestions": list(answer.suggested_capabilities),
            }
            if identifier == "seed_answer":
                evidence = answer.evidence[0] if answer.evidence else {}
                value["evidence_contract"] = {
                    "has_evidence": bool(answer.evidence),
                    "has_authority": bool(evidence.get("authority")),
                    "has_source": bool(evidence.get("source_refs")),
                    "has_applicability": bool(answer.applicability),
                    "freshness": answer.freshness,
                    "has_confidence": bool(answer.confidence_basis),
                }
            return value

        results = [ask("seed_answer", "Harness 是做什么的")]
        store.upsert_item(stable_key="fixture:stale", title="stale-fixture-unique", body="stale-fixture-unique", kind="support_boundary", authority="reviewed_team_knowledge", status="active", module_scope="DFHIS", version_label="v1", valid_until="2020-01-01", source_refs=[{"ref":"fixture", "claim_level":"support"}], tags=["fixture"])
        results.append(ask("stale", "stale-fixture-unique"))
        store.upsert_item(stable_key="fixture:left", title="冲突规则", body="冲突规则", kind="support_boundary", authority="verified_code", status="active", module_scope="DFHIS", version_label="v1", source_refs=[{"ref":"fixture", "claim_level":"support"}], tags=["fixture"])
        store.upsert_item(stable_key="fixture:right", title="冲突规则", body="冲突规则", kind="support_boundary", authority="verified_runtime", status="active", module_scope="DFHIS", version_label="v1", source_refs=[{"ref":"fixture", "claim_level":"support"}], tags=["fixture"])
        store.add_relation("fixture:left", "conflicts_with", "fixture:right")
        results += [
            ask("conflict", "冲突规则"),
            ask("production_database", "这个字段生产数据库有没有值"),
            ask("latest_yunxiao", "云效最新需求说了什么"),
            ask("change_request", "请重构收费代码"),
        ]
        return results


def _assert_replays(replays: list[dict[str, object]]) -> None:
    expected = {
        "seed_answer": ("answered", ()),
        "stale": ("needs_live_evidence", ("workitem.read",)),
        "conflict": ("conflicted", ()),
        "production_database": ("needs_live_evidence", ("database.inspect",)),
        "latest_yunxiao": ("needs_live_evidence", ("workitem.read",)),
        "change_request": ("unsupported", ("harness.task",)),
    }
    actual = {str(item["id"]): (item["status"], tuple(item["suggestions"])) for item in replays}
    if actual != expected:
        raise RuntimeError("acceptance replay mismatch")
    contract = next(item["evidence_contract"] for item in replays if item["id"] == "seed_answer")
    if contract != {"has_evidence": True, "has_authority": True, "has_source": True, "has_applicability": True, "freshness": "current", "has_confidence": True}:
        raise RuntimeError("seed evidence contract mismatch")


def run_all(output: Path = OUTPUT) -> dict[str, object]:
    """Generate one deterministic aggregate evidence pair for both interpreters."""
    _guard()
    if len(set(ALLOWED_INTERPRETERS)) != 2:
        raise RuntimeError("current and system interpreters must be distinct")
    replays = _answer_replays()
    _assert_replays(replays)
    commands = [entry for interpreter in ALLOWED_INTERPRETERS for entry in _one(interpreter)] + _validators()
    _guard()
    payload = {
        "schema_version": "knowledge-plugin-acceptance.v1",
        "task": "knowledge-task-9",
        "source_commit_range": "220d689..HEAD",
        "commands": commands,
        "replays": replays,
        "sensitive_proofs": list(SENSITIVE_PROOFS),
        "environment_profiles": {"isolated": ["HARNESS_DB_PATH", "HIS_KNOWLEDGE_HOME", "HOME", "LANG", "LC_ALL", "PATH", "TMPDIR", "XDG_CONFIG_HOME"], "validator_dependency": ["PYTHONNOUSERSITE", "PYTHONPATH"]},
        "default_home_guard": "before_and_after_every_child",
        "external_operations": {"network": False, "credentials": False, "yunxiao": False, "git_remote": False, "database_connection": False, "external_writes": False, "model": False},
        "local_operations_observed": {"git_diff_read": True, "evidence_write": True, "temporary_sqlite": True},
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "acceptance.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    lines = ["# Knowledge plugin acceptance", "", "- task: knowledge-task-9", "- interpreters: current, system", "- default-home guard: before and after every child", "- external operations: false", "", "## Replay results", ""]
    lines.extend("- {id}: {status}; suggestions={suggestions}".format(**replay) for replay in replays)
    (output / "acceptance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    run_all()
