from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest import mock

from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT


HARNESS_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = HARNESS_ROOT.parent
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))
os.environ.setdefault("HARNESS_ENABLE_STAGED_PLUGIN_TESTS", "1")
os.environ.setdefault(
    "HARNESS_STAGED_PLUGIN_ROOT",
    str(PLUGIN_SOURCE_ROOT),
)

from app import delivery_closure as legacy_delivery
from app import harness as legacy_core
from app import pg_evidence as legacy_pg
from app import yunxiao_read as legacy_yunxiao
from app.acceptance_matrix import build_acceptance_matrix
from app.capability_contracts import (
    CapabilityAuthorization,
    CapabilityRequest,
    CapabilityResult,
    MutationLevel,
)
from app.capability_permissions import PermissionDecision
from app.capability_registry import CapabilityDescriptor
from app.capability_runtime import CapabilityExecution, CapabilityPreflight
from app.capability_service import CapabilityService


@dataclass(frozen=True)
class AllowedDifference:
    path: str
    reason: str
    predicate: Callable[[Any, Any], bool]


ALLOWLIST = (
    AllowedDifference(
        path="$.schema_version",
        reason="legacy projection and capability-result envelopes publish different schemas",
        predicate=lambda legacy, plugin: (
            legacy == "legacy-equivalence.v1"
            and plugin == "his-capability-result.v1"
        ),
    ),
    AllowedDifference(
        path="$.audit.capability_contract",
        reason="the capability route adds its exact public contract identifier",
        predicate=lambda legacy, plugin: (
            legacy is None and plugin == "his-capability-result.v1"
        ),
    ),
    AllowedDifference(
        path="$.data.audit.masked_columns",
        reason="the capability audit adds the exact masking summary",
        predicate=lambda legacy, plugin: (
            legacy is None and plugin == "patient_phone"
        ),
    ),
    AllowedDifference(
        path="$.data.audit.row_count",
        reason="the capability audit adds the exact returned-row count",
        predicate=lambda legacy, plugin: legacy is None and plugin == 1,
    ),
    AllowedDifference(
        path="$.data.raw_classification",
        reason="legacy delivery and git.inspect name the same unusable workspace differently",
        predicate=lambda legacy, plugin: (
            legacy == "unsafe_repository_state" and plugin == "unsupported"
        ),
    ),
)

ALLOWLIST_BY_PATH = {rule.path: rule for rule in ALLOWLIST}
ENVELOPE_ALLOWLIST = (
    ALLOWLIST_BY_PATH["$.schema_version"],
    ALLOWLIST_BY_PATH["$.audit.capability_contract"],
)
GIT_ALLOWLIST = (
    *ENVELOPE_ALLOWLIST,
    ALLOWLIST_BY_PATH["$.data.raw_classification"],
)
PG_ALLOWLIST = (
    *ENVELOPE_ALLOWLIST,
    ALLOWLIST_BY_PATH["$.data.audit.masked_columns"],
    ALLOWLIST_BY_PATH["$.data.audit.row_count"],
)

FROZEN_GIT_DELIVERY_ORACLE = {
    "workspace_classification": "task_owned_exact",
    "blockers": [],
    "task_branch": "feature-DFHIS-90002",
    "commit_message": (
        "feat: DFHIS-90002-https://example.invalid/DFHIS-90002 "
        "《脱敏交付任务》"
    ),
    "actions": {
        "create_task_branch": True,
        "commit": True,
        "push_feature": False,
        "cherry_pick_integration": False,
        "push_integration": False,
        "gitlab_write": None,
        "github_write": None,
        "yunxiao_comment": False,
        "yunxiao_transition": False,
    },
    "state_transitions": [
        "waiting_release_runtime_acceptance",
        "release_runtime_accepted",
        "task_commit_created",
        "waiting_rc_runtime_acceptance",
        "rc_runtime_accepted",
        "gitlab_delivery_pending",
        "github_delivery_pending",
        "completed",
    ],
}

FROZEN_CORE_GOVERNANCE_ORACLE = {
    "calibration_status": "needs_human_confirmation",
    "can_patch": True,
    "ownership": "ready",
        "requirement_blockers": {
            "governance": [
                "需求目标或规则尚未校准为可开发状态。",
                "缺少已解析的参数和值域默认行为。",
            ],
        "contract": ["治理结果未被批准为 ready_for_local_change。"],
    },
}


def _resolve_allowlisted_leaf(
    testcase: unittest.TestCase,
    value: Any,
    path: str,
) -> Any:
    testcase.assertRegex(
        path,
        r"^\$\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$",
        f"{path}: allowlist path must identify an exact mapping leaf",
    )
    testcase.assertNotIn(
        "[",
        path,
        f"{path}: array/index allowlist paths are forbidden",
    )
    current = value
    for token in path[2:].split("."):
        testcase.assertIsInstance(
            current,
            dict,
            f"{path}: parent is not a mapping",
        )
        testcase.assertIn(token, current, f"{path}: path does not exist")
        current = current[token]
    testcase.assertNotIsInstance(
        current,
        (dict, list),
        f"{path}: allowlist target must be a JSON leaf",
    )
    return current


def assert_equivalent(
    testcase: unittest.TestCase,
    legacy: Any,
    plugin: Any,
    *,
    allowlist: tuple[AllowedDifference, ...] = ALLOWLIST,
) -> None:
    rules = {rule.path: rule for rule in allowlist}
    testcase.assertEqual(
        len(rules),
        len(allowlist),
        "allowlist paths must be unique",
    )
    for rule in allowlist:
        testcase.assertTrue(rule.reason.strip(), f"{rule.path} needs a reason")
        testcase.assertNotIn("*", rule.path, f"{rule.path} must be exact")
        testcase.assertFalse(rule.path.endswith("."), f"{rule.path} is incomplete")
        _resolve_allowlisted_leaf(testcase, legacy, rule.path)
        _resolve_allowlisted_leaf(testcase, plugin, rule.path)

    def compare(left: Any, right: Any, path: str) -> None:
        rule = rules.get(path)
        if rule is not None:
            testcase.assertTrue(
                rule.predicate(left, right),
                f"{path}: difference does not satisfy allowlisted predicate; "
                f"legacy={left!r}, plugin={right!r}",
            )
            return
        testcase.assertIs(
            type(left),
            type(right),
            f"{path}: type drift; legacy={type(left).__name__}, "
            f"plugin={type(right).__name__}",
        )
        if isinstance(left, dict):
            testcase.assertEqual(
                set(left),
                set(right),
                f"{path}: missing/extra mapping fields",
            )
            for key in sorted(left):
                compare(left[key], right[key], f"{path}.{key}")
            return
        if isinstance(left, list):
            testcase.assertEqual(
                len(left),
                len(right),
                f"{path}: list length drift",
            )
            for index, (left_item, right_item) in enumerate(zip(left, right)):
                compare(left_item, right_item, f"{path}[{index}]")
            return
        testcase.assertEqual(left, right, f"{path}: value drift")

    compare(legacy, plugin, "$")


def assert_mutation_matrix(
    testcase: unittest.TestCase,
    legacy: Mapping[str, Any],
    plugin: Mapping[str, Any],
    *,
    allowlist: tuple[AllowedDifference, ...] = ALLOWLIST,
) -> None:
    """Prove every compared JSON leaf/container remains fail-closed."""

    allowed_paths = {rule.path for rule in allowlist}

    def json_path(tokens: tuple[object, ...]) -> str:
        path = "$"
        for token in tokens:
            path += f"[{token}]" if isinstance(token, int) else f".{token}"
        return path

    def get_at(value: Any, tokens: tuple[object, ...]) -> Any:
        current = value
        for token in tokens:
            current = current[token]
        return current

    def scalar_locations(
        value: Any,
        tokens: tuple[object, ...] = (),
    ) -> list[tuple[object, ...]]:
        if isinstance(value, dict):
            result: list[tuple[object, ...]] = []
            for key in sorted(value):
                result.extend(scalar_locations(value[key], (*tokens, key)))
            return result
        if isinstance(value, list):
            result = []
            for index, item in enumerate(value):
                result.extend(scalar_locations(item, (*tokens, index)))
            return result
        return [tokens]

    def container_locations(
        value: Any,
        wanted_type: type,
        tokens: tuple[object, ...] = (),
    ) -> list[tuple[object, ...]]:
        result = [tokens] if isinstance(value, wanted_type) else []
        if isinstance(value, dict):
            for key in sorted(value):
                result.extend(
                    container_locations(value[key], wanted_type, (*tokens, key))
                )
        elif isinstance(value, list):
            for index, item in enumerate(value):
                result.extend(
                    container_locations(item, wanted_type, (*tokens, index))
                )
        return result

    def replacement(value: Any) -> Any:
        if value is None:
            return "__mutation__"
        if isinstance(value, bool):
            return not value
        if isinstance(value, str):
            return value + "__mutation__"
        if isinstance(value, int):
            return value + 1
        if isinstance(value, float):
            return value + 0.5
        raise AssertionError(f"non-JSON scalar in equivalence tree: {value!r}")

    for tokens in scalar_locations(plugin):
        path = json_path(tokens)
        if path in allowed_paths:
            continue
        candidate = copy.deepcopy(plugin)
        parent = get_at(candidate, tokens[:-1])
        parent[tokens[-1]] = replacement(parent[tokens[-1]])
        with testcase.subTest(mutation="value", path=path), testcase.assertRaises(
            AssertionError
        ):
            assert_equivalent(
                testcase,
                legacy,
                candidate,
                allowlist=allowlist,
            )

    for tokens in container_locations(plugin, dict):
        candidate = copy.deepcopy(plugin)
        mapping = get_at(candidate, tokens)
        mapping["__unexpected_field__"] = "mutation"
        with testcase.subTest(
            mutation="extra",
            path=json_path(tokens),
        ), testcase.assertRaises(AssertionError):
            assert_equivalent(
                testcase,
                legacy,
                candidate,
                allowlist=allowlist,
            )
        for key in list(mapping):
            if key == "__unexpected_field__":
                continue
            candidate = copy.deepcopy(plugin)
            del get_at(candidate, tokens)[key]
            with testcase.subTest(
                mutation="missing",
                path=f"{json_path(tokens)}.{key}",
            ), testcase.assertRaises(AssertionError):
                assert_equivalent(
                    testcase,
                    legacy,
                    candidate,
                    allowlist=allowlist,
                )

    for tokens in container_locations(plugin, list):
        candidate = copy.deepcopy(plugin)
        get_at(candidate, tokens).append("__unexpected_item__")
        with testcase.subTest(
            mutation="list-extra",
            path=json_path(tokens),
        ), testcase.assertRaises(AssertionError):
            assert_equivalent(
                testcase,
                legacy,
                candidate,
                allowlist=allowlist,
            )
        original = get_at(plugin, tokens)
        if len(original) > 1 and original != list(reversed(original)):
            candidate = copy.deepcopy(plugin)
            get_at(candidate, tokens).reverse()
            with testcase.subTest(
                mutation="list-order",
                path=json_path(tokens),
            ), testcase.assertRaises(AssertionError):
                assert_equivalent(
                    testcase,
                    legacy,
                    candidate,
                    allowlist=allowlist,
                )


def _envelope(data: Mapping[str, Any], *, plugin: bool) -> dict[str, Any]:
    return {
        "schema_version": (
            "his-capability-result.v1" if plugin else "legacy-equivalence.v1"
        ),
        "data": json.loads(json.dumps(dict(data), ensure_ascii=False)),
        "audit": {
            "capability_contract": (
                "his-capability-result.v1" if plugin else None
            ),
            "external_write_attempted": False,
        },
    }


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_delivery_plugin() -> Any:
    package_name = "_task7_delivery_plugin"
    scripts = PLUGIN_SOURCE_ROOT / "his-engineering" / "scripts"
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts)]
    sys.modules[package_name] = package
    _load_module(
        f"{package_name}.delivery_store",
        scripts / "delivery_store.py",
    )
    return _load_module(
        f"{package_name}.delivery_closure",
        scripts / "delivery_closure.py",
    )


def _load_git_capability() -> Any:
    return _load_module(
        "_task7_git_local",
        PLUGIN_SOURCE_ROOT
        / "his-engineering"
        / "scripts"
        / "git_local.py",
    )


def _load_database_capability() -> Any:
    return _load_module(
        "_task7_database_read",
        PLUGIN_SOURCE_ROOT
        / "his-engineering"
        / "scripts"
        / "database_read.py",
    )


def _load_core_capability() -> Any:
    return _load_module(
        "_task7_requirement_governance",
        PLUGIN_SOURCE_ROOT
        / "his-harness-core"
        / "scripts"
        / "requirement_governance.py",
    )


def _load_yunxiao_capability() -> Any:
    return _load_module(
        "_task7_workitem_read",
        PLUGIN_SOURCE_ROOT
        / "yunxiao"
        / "scripts"
        / "workitem_read.py",
    )


class _FakeHttpResponse:
    def __init__(self, payload: object) -> None:
        self.status = 200
        self.headers = {"content-type": "application/json"}
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self, _limit: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


class _SharedYunxiaoTransport:
    def __init__(self, *, comments_ok: bool) -> None:
        self.comments_ok = comments_ok
        self.phase = ""
        self.calls: list[tuple[str, str, str]] = []

    def __call__(
        self,
        request: object,
        timeout: object = None,
    ) -> _FakeHttpResponse:
        del timeout
        url = str(getattr(request, "full_url", request))
        method = str(getattr(request, "get_method", lambda: "GET")())
        self.calls.append((self.phase, method, url))
        if "/comments" in url:
            if not self.comments_ok:
                raise urllib.error.URLError("fixture comment outage")
            return _FakeHttpResponse(_yunxiao_comments())
        if "/attachments" in url and "/files/" not in url:
            return _FakeHttpResponse(_yunxiao_attachments())
        if "relationRecords" in url:
            return _FakeHttpResponse([])
        if "/files/" in url or "workitem/file" in url:
            identifier = (
                "attachment-1" if "attachment-1" in url else "inline-1"
            )
            return _FakeHttpResponse(
                {
                    "id": identifier,
                    "fileName": (
                        "验收说明.txt"
                        if identifier == "attachment-1"
                        else "inline.png"
                    ),
                    "url": (
                        "https://files.example/acceptance.txt"
                        if identifier == "attachment-1"
                        else "https://files.example/inline.png"
                    ),
                }
            )
        return _FakeHttpResponse(_yunxiao_item())


def _yunxiao_item() -> dict[str, Any]:
    body = (
        '<p>仅比较脱敏后的需求正文</p>'
        '<img src="https://files.example/inline.png?fileIdentifier=inline-1">'
    )
    return {
        "id": "DFHIS-90001",
        "serialNumber": "DFHIS-90001",
        "title": "脱敏工作项",
        "description": body,
        "content": {"htmlValue": body},
        "categoryId": "Req",
    }


def _yunxiao_comments() -> list[dict[str, Any]]:
    return [
        {
            "id": "comment-1",
            "author": {"name": "脱敏人员"},
            "content": "第一条评论",
            "createdAt": "2026-07-27T00:00:00Z",
        },
        {
            "id": "comment-2",
            "content": "第二条评论",
            "createdAt": "2026-07-27T00:01:00Z",
        },
    ]


def _yunxiao_attachments() -> list[dict[str, Any]]:
    return [
        {
            "id": "attachment-1",
            "fileName": "验收说明.txt",
            "url": "https://files.example/acceptance.txt?signature=redacted",
            "size": 17,
            "contentType": "text/plain",
        }
    ]


def _strip_recorded_inline_placeholders(
    clean_text: str,
    inline_files: list[dict[str, Any]],
) -> str:
    recorded = Counter(
        f"[内联图片：{identifier}]"
        for item in inline_files
        if str(item.get("kind") or "").startswith("inline")
        for identifier in (str(item.get("identifier") or "").strip(),)
        if identifier
    )
    retained: list[str] = []
    for line in clean_text.splitlines():
        if recorded[line] > 0:
            recorded[line] -= 1
            continue
        retained.append(line)
    return "\n".join(retained)


def _legacy_yunxiao_projection(evidence: Mapping[str, Any]) -> dict[str, Any]:
    item = evidence["work_item"]
    inline_files = [
        {
            "id": str(item.get("identifier") or ""),
            "name": str(
                item.get("name")
                or Path(
                    urllib.parse.urlsplit(str(item.get("url") or "")).path
                ).name
            ),
        }
        for item in evidence["inline_files"]
        if str(item.get("kind") or "").startswith("inline")
    ]
    body = _strip_recorded_inline_placeholders(
        str(evidence.get("clean_text") or ""),
        list(evidence["inline_files"]),
    )
    return {
        "work_item": {
            "id": str(item.get("serialNumber") or item.get("id") or ""),
            "title": str(item.get("title") or ""),
            "body": body,
        },
        "comments": [
            {
                "id": str(comment.get("id") or ""),
                "author": str(comment.get("author") or ""),
                "content": str(comment.get("content") or ""),
                "created_at": str(comment.get("created_at") or ""),
            }
            for comment in evidence["comments"]
        ],
        "attachments": [
            {
                "id": str(item.get("identifier") or ""),
                "name": str(item.get("name") or ""),
                "size": item.get("size"),
                "content_type": str(item.get("content_type") or ""),
            }
            for item in evidence["attachments"]
        ],
        "inline_files": inline_files,
        "warnings": (
            []
            if evidence["comment_read"]["status"] == "success"
            else ["comments_read_failed"]
        ),
    }


def _plugin_yunxiao_projection(result: Mapping[str, Any]) -> dict[str, Any]:
    evidence = result["data"]
    item = evidence["work_items"][0]
    return {
        "work_item": {
            "id": str(item.get("serial_number") or item.get("id") or ""),
            "title": str(item.get("title") or ""),
            "body": str(item["description"].get("text") or ""),
        },
        "comments": [
            {
                "id": str(comment.get("id") or ""),
                "author": str(comment.get("author") or ""),
                "content": str(comment.get("content") or ""),
                "created_at": str(comment.get("created_at") or ""),
            }
            for comment in item["comments"]
        ],
        "attachments": [
            {
                "id": str(
                    attachment.get("id")
                    or attachment.get("file_id")
                    or ""
                ),
                "name": str(attachment.get("name") or ""),
                "size": attachment.get("size"),
                "content_type": str(attachment.get("content_type") or ""),
            }
            for attachment in item["attachments"]
        ],
        "inline_files": [
            {
                "id": str(item.get("file_id") or ""),
                "name": str(item.get("name") or ""),
            }
            for item in item["inline_files"]
        ],
        "warnings": list(result["warnings"]),
    }


class _FakePgExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def discover_metadata(self, **kwargs: object) -> list[dict[str, str]]:
        self.calls.append("metadata")
        return []

    def execute_select(self, **kwargs: object) -> list[dict[str, object]]:
        self.calls.append("select")
        return [
            {
                "code": "SAFE-CODE",
                "patient_phone": "13800000000",
            }
        ]


class _DirectPluginRuntime:
    def __init__(self, module: Any) -> None:
        self.module = module
        self.preflight_calls: list[CapabilityRequest] = []
        self.execute_calls: list[CapabilityRequest] = []

    @staticmethod
    def descriptor(request: CapabilityRequest) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            plugin="his-engineering",
            plugin_version="0.1.0",
            name=request.capability,
            provider=request.provider,
            contract_version="git-inspect.v1",
            mutation_level=request.mutation_level,
            credential_class="none",
            entrypoint=None,
            enabled=True,
            disabled_reason="",
            scopes=("repository:inspect",),
        )

    @staticmethod
    def permission(request: CapabilityRequest) -> PermissionDecision:
        return PermissionDecision(
            status="allowed",
            allowed=True,
            required_level=request.mutation_level,
            blockers=(),
        )

    def preflight(self, request: CapabilityRequest) -> CapabilityPreflight:
        self.preflight_calls.append(request)
        return CapabilityPreflight(
            descriptor=self.descriptor(request),
            permission=self.permission(request),
        )

    def execute(self, request: CapabilityRequest) -> CapabilityExecution:
        self.execute_calls.append(request)
        raw = self.module.execute_request(request.to_dict())
        return CapabilityExecution(
            descriptor=self.descriptor(request),
            permission=self.permission(request),
            result=CapabilityResult.from_dict(raw, request=request),
            duration_ms=1,
        )


class PluginLegacyEquivalenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="task7-equivalence-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        home = self.root / "home"
        home.mkdir()
        self.environment = {
            "HOME": str(home),
            "HIS_ENGINEERING_HOME": str(self.root / "engineering"),
            "HIS_KNOWLEDGE_HOME": str(self.root / "knowledge"),
            "HARNESS_DB_PATH": str(self.root / "harness.sqlite"),
            "YUNXIAO_CREDENTIALS_FILE": str(self.root / "no-credentials.json"),
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
        }
        self.environment_patch = mock.patch.dict(
            os.environ,
            self.environment,
            clear=True,
        )
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)
        self.network_patch = mock.patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("network forbidden in equivalence tests"),
        )
        self.network_patch.start()
        self.addCleanup(self.network_patch.stop)
        self.process_patch = mock.patch(
            "subprocess.run",
            side_effect=AssertionError(
                "real subprocess/Git forbidden in equivalence tests"
            ),
        )
        self.process_patch.start()
        self.addCleanup(self.process_patch.stop)

    def test_yunxiao_legacy_intake_matches_capability_adapter(self) -> None:
        capability = _load_yunxiao_capability()
        credentials = legacy_yunxiao.YunxiaoCredentialBundle(
            pat="fixture-token",
            organization_id="fixture-org",
        )
        request = {
            "schema_version": "his-capability-request.v1",
            "request_id": "task7-yunxiao",
            "capability": "workitem.read",
            "provider": "yunxiao",
            "mode": "preview",
            "mutation_level": "L1",
            "authorization": {"explicit": False, "scope": []},
            "input": {"entity_id": "DFHIS-90001"},
            "context": {"include_comments": True},
        }
        for comments_ok, expected_warnings in (
            (True, []),
            (False, ["comments_read_failed"]),
        ):
            transport = _SharedYunxiaoTransport(comments_ok=comments_ok)
            transport.phase = "legacy"
            with self.subTest(comments_ok=comments_ok), mock.patch.object(
                legacy_yunxiao,
                "load_yunxiao_credentials",
                return_value=credentials,
            ), mock.patch.object(
                legacy_yunxiao.urllib.request,
                "urlopen",
                side_effect=transport,
            ):
                legacy = legacy_yunxiao.collect_yunxiao_evidence(
                    yunxiao_url="DFHIS-90001",
                    demand_text="",
                    output_dir=None,
                )
            transport.phase = "plugin"
            plugin = capability.execute_request(
                request,
                credential_loader=lambda **_: {
                    "token": "fixture-token",
                    "organization_id": "fixture-org",
                },
                client_factory=lambda values: capability._EVIDENCE.YunxiaoClient(
                    token=values["token"],
                    organization_id=values["organization_id"],
                    opener=transport,
                ),
            )

            self.assertIsNot(
                legacy_yunxiao.collect_yunxiao_evidence,
                capability.execute_request,
            )
            self.assertNotEqual(
                legacy_yunxiao.collect_yunxiao_evidence.__module__,
                capability.execute_request.__module__,
            )
            legacy_envelope = _envelope(
                _legacy_yunxiao_projection(legacy),
                plugin=False,
            )
            plugin_envelope = _envelope(
                _plugin_yunxiao_projection(plugin),
                plugin=True,
            )
            assert_equivalent(
                self,
                legacy_envelope,
                plugin_envelope,
                allowlist=ENVELOPE_ALLOWLIST,
            )
            assert_mutation_matrix(
                self,
                legacy_envelope,
                plugin_envelope,
                allowlist=ENVELOPE_ALLOWLIST,
            )
            orphan = copy.deepcopy(legacy)
            orphan["clean_text"] += "\n[内联图片：orphan-inline]\n"
            orphan_envelope = _envelope(
                _legacy_yunxiao_projection(orphan),
                plugin=False,
            )
            with self.assertRaisesRegex(
                AssertionError,
                r"\$\.data\.work_item\.body",
            ):
                assert_equivalent(
                    self,
                    orphan_envelope,
                    plugin_envelope,
                    allowlist=ENVELOPE_ALLOWLIST,
                )
            phases = [phase for phase, method, _url in transport.calls]
            self.assertIn("legacy", phases)
            self.assertIn("plugin", phases)
            self.assertEqual(
                {"GET"},
                {method for _phase, method, _url in transport.calls},
            )
            self.assertEqual(expected_warnings, plugin["warnings"])
            if not comments_ok:
                self.assertEqual(
                    [],
                    plugin["data"]["work_items"][0]["comments"],
                    "failed comments are represented as an empty ordered list, "
                    "not a missing field",
                )
            serialized = json.dumps(plugin, ensure_ascii=False)
            self.assertNotIn("fixture-token", serialized)
            self.assertNotIn("fixture-org", serialized)

    def test_git_plan_matches_adapter_without_git_mutation(self) -> None:
        direct = _load_delivery_plugin()
        git_capability = _load_git_capability()
        project = self.root / "source"
        project.mkdir()
        snapshot = {
            "classification": "task_owned_exact",
            "blockers": [],
            "head": "fixed-head",
            "allowed_paths": ["src/task.vue"],
            "task_file_state_hash": "fixed-task-state",
        }
        request_data = {
            "entity_kind": "requirement",
            "entity_id": "DFHIS-90002",
            "title": "脱敏交付任务",
            "url": "https://example.invalid/DFHIS-90002",
            "project_path": str(project),
            "expected_diff": "fixed local patch",
            "allowed_paths": ["src/task.vue"],
            "output_dir": str(self.root / "delivery"),
            "verify_commands": ["python -m unittest tests.task7"],
        }
        legacy_request = legacy_delivery.DeliveryRequest(**request_data)
        legacy_policy = legacy_delivery.DeliveryPolicy.from_payload({})
        non_git = subprocess.CompletedProcess(
            args=["git", "rev-parse"],
            returncode=1,
            stdout="",
            stderr="not a repository",
        )
        with mock.patch.object(
            legacy_delivery._closure,
            "_git",
            return_value=non_git,
        ) as legacy_git:
            legacy_inspection = legacy_delivery.inspect_repository(
                legacy_request,
                legacy_policy,
            )
        self.assertGreaterEqual(legacy_git.call_count, 1)

        capability_request = CapabilityRequest(
            request_id="task7-git-inspect",
            capability="git.inspect",
            provider="his-engineering",
            mode="preview",
            mutation_level=MutationLevel.L0,
            authorization=CapabilityAuthorization(explicit=False, scope=()),
            input={"project_path": str(project)},
            context={},
        )
        runtime = _DirectPluginRuntime(git_capability)
        service = CapabilityService(runtime, routing_mode="enforce")
        with mock.patch.object(
            git_capability,
            "_run_git",
            return_value=non_git,
        ) as plugin_git, mock.patch.object(
            service,
            "route",
            wraps=service.route,
        ) as service_entry:
            route = service_entry(capability_request)
        plugin_git.assert_called_once()
        service_entry.assert_called_once()
        self.assertEqual(1, len(runtime.execute_calls))
        plugin_inspection = dict(route.result)

        with mock.patch.object(
            legacy_delivery,
            "build_delivery_plan",
            wraps=legacy_delivery.build_delivery_plan,
        ) as legacy_plan_entry, mock.patch.object(
            direct,
            "build_delivery_plan",
            wraps=direct.build_delivery_plan,
        ) as plugin_plan_entry:
            legacy_plan = legacy_plan_entry(
                legacy_request,
                legacy_policy,
                snapshot,
            )
            plugin_plan = plugin_plan_entry(
                direct.DeliveryRequest(**request_data),
                direct.DeliveryPolicy.from_payload({}),
                copy.deepcopy(snapshot),
            )
        legacy_plan_entry.assert_called_once()
        plugin_plan_entry.assert_called_once()

        # The compatibility adapter currently resolves the canonical plugin
        # implementation.  Equivalence therefore cannot be proved by comparing
        # those two callables to each other; both must match this frozen oracle.
        self.assertIsNot(
            legacy_delivery.build_delivery_plan,
            direct.build_delivery_plan,
        )
        self.assertNotEqual(
            legacy_delivery.build_delivery_plan.__module__,
            direct.build_delivery_plan.__module__,
        )
        plan_projection = lambda plan, state_sequence: {
            "workspace_classification": plan["workspace_classification"],
            "blockers": plan["workspace_blockers"],
            "task_branch": plan["task_branch"],
            "commit_message": plan["commit_message"],
            "actions": plan["actions"],
            "state_transitions": list(state_sequence),
        }
        legacy_delivery_result = plan_projection(
            legacy_plan,
            legacy_delivery.FROZEN_DELIVERY_STATE_SEQUENCE,
        )
        plugin_delivery_result = plan_projection(
            plugin_plan,
            direct.DELIVERY_STATE_SEQUENCE,
        )
        for boundary, actual in (
            ("compatibility-adapter", legacy_delivery_result),
            ("canonical-plugin", plugin_delivery_result),
        ):
            with self.subTest(boundary=boundary):
                assert_equivalent(
                    self,
                    FROZEN_GIT_DELIVERY_ORACLE,
                    actual,
                    allowlist=(),
                )
                assert_mutation_matrix(
                    self,
                    FROZEN_GIT_DELIVERY_ORACLE,
                    actual,
                    allowlist=(),
                )
            removed = copy.deepcopy(actual)
            removed["state_transitions"].pop(2)
            with self.subTest(
                boundary=boundary,
                mutation="state-removed",
            ), self.assertRaises(AssertionError):
                assert_equivalent(
                    self,
                    FROZEN_GIT_DELIVERY_ORACLE,
                    removed,
                    allowlist=(),
                )
            reordered = copy.deepcopy(actual)
            reordered["state_transitions"][1:3] = reversed(
                reordered["state_transitions"][1:3]
            )
            with self.subTest(
                boundary=boundary,
                mutation="state-reordered",
            ), self.assertRaises(AssertionError):
                assert_equivalent(
                    self,
                    FROZEN_GIT_DELIVERY_ORACLE,
                    reordered,
                    allowlist=(),
                )

        inspection_projection = lambda raw_classification, blockers: {
            "raw_classification": raw_classification,
            "workspace_classification": "unusable",
            "inspection_blockers": blockers,
        }
        legacy_envelope = _envelope(
            inspection_projection(
                legacy_inspection["classification"],
                legacy_inspection["blockers"],
            ),
            plugin=False,
        )
        plugin_envelope = _envelope(
            inspection_projection(
                plugin_inspection["data"]["classification"],
                plugin_inspection["blockers"],
            ),
            plugin=True,
        )
        assert_equivalent(
            self,
            legacy_envelope,
            plugin_envelope,
            allowlist=GIT_ALLOWLIST,
        )
        assert_mutation_matrix(
            self,
            legacy_envelope,
            plugin_envelope,
            allowlist=GIT_ALLOWLIST,
        )
        self.assertFalse(plugin_plan["remote_actions_enabled"])
        self.assertTrue(plugin_plan["actions"]["create_task_branch"])
        self.assertTrue(plugin_plan["actions"]["commit"])
        for external_action in (
            "push_feature",
            "cherry_pick_integration",
            "push_integration",
            "yunxiao_comment",
            "yunxiao_transition",
        ):
            self.assertFalse(plugin_plan["actions"][external_action])

    def test_postgresql_legacy_runner_is_retired_in_favor_of_mcp_catalog(self) -> None:
        self.assertTrue(legacy_pg.LEGACY_PG_EVIDENCE_DISABLED)
        self.assertFalse(hasattr(legacy_pg, "run_pg_evidence"))
        self.assertFalse(hasattr(legacy_pg, "PgEvidenceRequest"))
        with self.assertRaisesRegex(
            legacy_pg.LegacyPgEvidenceDisabled,
            legacy_pg.LEGACY_PG_EVIDENCE_ERROR_CODE,
        ):
            legacy_pg.require_database_inspect_mcp()
        descriptor = json.loads(
            (PLUGIN_SOURCE_ROOT / "his-engineering" / ".mcp.json").read_text(encoding="utf-8")
        )
        command = descriptor["mcpServers"]["postgresql"]["args"][-1]
        self.assertEqual("./scripts/postgresql_mcp_server.py", command)

    def test_core_legacy_governance_matches_capability_adapter(self) -> None:
        capability = _load_core_capability()
        project = self.root / "df-web-guahaosf"
        target = project / "src" / "pages" / "guaHaoChaXun" / "index.vue"
        target.parent.mkdir(parents=True)
        target.write_text("<template>脱敏页签</template>\n", encoding="utf-8")
        (project / "package.json").write_text(
            '{"name":"df-web-guahaosf"}\n',
            encoding="utf-8",
        )
        raw = {
            "title": "挂号病人查询保留页签状态",
            "demand_text": (
                "仅修改前端页签状态；切换后保留查询条件和结果，"
                "不修改后端、数据库或接口。"
            ),
            "allowed_path": "src/pages/guaHaoChaXun/index.vue",
        }
        calibration = legacy_core.build_requirement_calibration(
            title=raw["title"],
            demand_text=raw["demand_text"],
            user_instruction=raw["demand_text"],
            project_paths=[str(project)],
        )
        technical_model = legacy_core.build_technical_decision(
            demand_text=raw["demand_text"],
            project_root=self.root,
            explicit_project_paths=[str(project)],
            explicit_allowed_paths=[raw["allowed_path"]],
        )
        technical = technical_model.to_dict()
        ownership = legacy_core.build_change_ownership_matrix(
            user_instruction=raw["demand_text"],
            requirement_text=raw["demand_text"],
            technical_decision=technical,
        ).to_dict()
        acceptance = legacy_core.build_acceptance_matrix(
            title=raw["title"],
            demand_text=raw["demand_text"],
            project_paths=[str(project)],
            verify_commands=technical["recommended_verify_commands"],
            execution_mode="readonly",
        )
        legacy_inputs = {
            "title": raw["title"],
            "user_instruction": raw["demand_text"],
            "source_type": "manual",
            "normalized_requirement_evidence": {
                "readonly": True,
                "external_writes_enabled": False,
                "source_type": "manual",
                "title": raw["title"],
                "description_text": raw["demand_text"],
                "comments": [],
                "attachments": [],
                "warnings": [],
            },
            "yunxiao_evidence": None,
            "requirement_calibration": calibration,
            "technical_decision": technical,
            "change_ownership": ownership,
            "acceptance_matrix": acceptance,
        }
        canonical_builder = legacy_core.build_requirement_governance_outputs
        with mock.patch.object(
            legacy_core,
            "build_requirement_governance_outputs",
            wraps=canonical_builder,
        ) as legacy_entry:
            legacy_governance, legacy_contract, error = legacy_entry(
                **copy.deepcopy(legacy_inputs)
            )
        legacy_entry.assert_called_once()
        legacy_consumed = copy.deepcopy(legacy_entry.call_args.kwargs)
        self.assertEqual("", error)
        request = {
            "schema_version": "his-capability-request.v1",
            "request_id": "task7-core",
            "capability": "requirement.govern",
            "provider": "his-harness-core",
            "mode": "preview",
            "mutation_level": "L0",
            "authorization": {"explicit": False, "scope": []},
            "input": copy.deepcopy(legacy_inputs),
            "context": {},
        }
        with mock.patch.object(
            legacy_core,
            "build_requirement_governance_outputs",
            wraps=canonical_builder,
        ) as plugin_builder_entry, mock.patch.object(
            capability,
            "_load_governance_api",
            wraps=capability._load_governance_api,
        ) as plugin_loader_entry, mock.patch.object(
            capability,
            "execute_request",
            wraps=capability.execute_request,
        ) as plugin_entry, mock.patch.dict(
            os.environ,
            {"HIS_HARNESS_ROOT": str(HARNESS_ROOT)},
            clear=False,
        ):
            plugin = plugin_entry(request)
        plugin_entry.assert_called_once()
        plugin_loader_entry.assert_called_once()
        plugin_builder_entry.assert_called_once()
        plugin_consumed = copy.deepcopy(plugin_builder_entry.call_args.kwargs)
        self.assertIsNot(
            legacy_core.build_requirement_governance_outputs,
            capability.execute_request,
        )
        self.assertNotEqual(
            legacy_core.build_requirement_governance_outputs.__module__,
            capability.execute_request.__module__,
        )
        legacy_governance_data = legacy_governance.to_dict()
        legacy_contract_data = legacy_contract.to_dict()
        plugin_governance = plugin["data"]["governance"]
        plugin_contract = plugin["data"]["single_pass_change_contract"]
        projection = lambda consumed, governance, contract: {
            "calibration_status": consumed["requirement_calibration"]["status"],
            "can_patch": consumed["technical_decision"][
                "implementation_decision"
            ]["can_patch"],
            "ownership": consumed["change_ownership"]["status"],
            "requirement_blockers": {
                "governance": governance["blockers"],
                "contract": contract["blockers"],
            },
        }
        legacy_envelope = _envelope(
            projection(
                legacy_consumed,
                legacy_governance_data,
                legacy_contract_data,
            ),
            plugin=False,
        )
        plugin_envelope = _envelope(
            projection(plugin_consumed, plugin_governance, plugin_contract),
            plugin=True,
        )
        for boundary, actual in (
            ("legacy-public-builder", legacy_envelope["data"]),
            ("plugin-public-consumption", plugin_envelope["data"]),
        ):
            with self.subTest(boundary=boundary):
                assert_equivalent(
                    self,
                    FROZEN_CORE_GOVERNANCE_ORACLE,
                    actual,
                    allowlist=(),
                )
                assert_mutation_matrix(
                    self,
                    FROZEN_CORE_GOVERNANCE_ORACLE,
                    actual,
                    allowlist=(),
                )
        legacy_mutation = copy.deepcopy(legacy_envelope["data"])
        legacy_mutation["calibration_status"] = "ready"
        with self.assertRaises(AssertionError):
            assert_equivalent(
                self,
                FROZEN_CORE_GOVERNANCE_ORACLE,
                legacy_mutation,
                allowlist=(),
            )
        plugin_mutation = copy.deepcopy(plugin_envelope["data"])
        plugin_mutation["requirement_blockers"]["contract"].append(
            "plugin-only-drift"
        )
        with self.assertRaises(AssertionError):
            assert_equivalent(
                self,
                FROZEN_CORE_GOVERNANCE_ORACLE,
                plugin_mutation,
                allowlist=(),
            )
        assert_equivalent(
            self,
            legacy_envelope,
            plugin_envelope,
            allowlist=ENVELOPE_ALLOWLIST,
        )
        assert_mutation_matrix(
            self,
            legacy_envelope,
            plugin_envelope,
            allowlist=ENVELOPE_ALLOWLIST,
        )


class ExactAllowlistMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.legacy = _envelope(
            {
                "id": "DFHIS-90001",
                "ordered": ["first", "second"],
                "nested": {
                    "status": "ready",
                    "can_patch": True,
                    "blockers": [],
                },
                "raw_classification": "unsafe_repository_state",
                "audit": {
                    "masked_columns": None,
                    "row_count": None,
                },
            },
            plugin=False,
        )
        self.plugin = _envelope(self.legacy["data"], plugin=True)
        self.plugin["data"]["raw_classification"] = "unsupported"
        self.plugin["data"]["audit"]["masked_columns"] = "patient_phone"
        self.plugin["data"]["audit"]["row_count"] = 1

    def test_exact_allowlisted_leaves_pass_and_unapproved_values_fail(self) -> None:
        assert_equivalent(self, self.legacy, self.plugin)
        for path, value in (
            ("schema_version", "his-capability-result.v2"),
            ("capability_contract", "whole-audit-object"),
            ("raw_classification", "failed"),
            ("masked_columns", "other_sensitive"),
            ("row_count", 2),
        ):
            mutated = copy.deepcopy(self.plugin)
            if path == "schema_version":
                mutated[path] = value
            elif path == "capability_contract":
                mutated["audit"][path] = value
            elif path == "raw_classification":
                mutated["data"][path] = value
            else:
                mutated["data"]["audit"][path] = value
            with self.subTest(path=path), self.assertRaises(AssertionError):
                assert_equivalent(self, self.legacy, mutated)

    def test_every_nonallowlisted_scalar_drift_fails(self) -> None:
        mutations = (
            ("$.data.id", lambda value: value["data"].__setitem__("id", "DRIFT")),
            (
                "$.data.nested.status",
                lambda value: value["data"]["nested"].__setitem__(
                    "status",
                    "blocked",
                ),
            ),
            (
                "$.data.nested.can_patch",
                lambda value: value["data"]["nested"].__setitem__(
                    "can_patch",
                    False,
                ),
            ),
            (
                "$.audit.external_write_attempted",
                lambda value: value["audit"].__setitem__(
                    "external_write_attempted",
                    True,
                ),
            ),
        )
        for path, mutate in mutations:
            candidate = copy.deepcopy(self.plugin)
            mutate(candidate)
            with self.subTest(path=path), self.assertRaisesRegex(
                AssertionError,
                path.replace("$", r"\$").replace("[", r"\["),
            ):
                assert_equivalent(self, self.legacy, candidate)

    def test_missing_extra_type_and_list_order_drift_fail(self) -> None:
        candidates = []
        missing = copy.deepcopy(self.plugin)
        del missing["data"]["nested"]["status"]
        candidates.append(("missing", missing))
        extra = copy.deepcopy(self.plugin)
        extra["data"]["nested"]["unexpected"] = "drift"
        candidates.append(("extra", extra))
        wrong_type = copy.deepcopy(self.plugin)
        wrong_type["data"]["nested"]["can_patch"] = 1
        candidates.append(("type", wrong_type))
        reordered = copy.deepcopy(self.plugin)
        reordered["data"]["ordered"].reverse()
        candidates.append(("list-order", reordered))
        missing_list = copy.deepcopy(self.plugin)
        del missing_list["data"]["nested"]["blockers"]
        candidates.append(("missing-list", missing_list))

        for label, candidate in candidates:
            with self.subTest(label=label), self.assertRaises(AssertionError):
                assert_equivalent(self, self.legacy, candidate)

    def test_parent_container_missing_and_index_allowlist_paths_are_rejected(
        self,
    ) -> None:
        invalid_paths = (
            "$.data",
            "$.audit",
            "$.data.missing",
            "$.data.ordered",
            "$.data.ordered[0]",
        )
        baseline = copy.deepcopy(self.legacy)
        for path in invalid_paths:
            rule = AllowedDifference(
                path=path,
                reason="negative control",
                predicate=lambda _legacy, _plugin: True,
            )
            with self.subTest(path=path), self.assertRaises(AssertionError):
                assert_equivalent(
                    self,
                    baseline,
                    copy.deepcopy(baseline),
                    allowlist=(rule,),
                )
