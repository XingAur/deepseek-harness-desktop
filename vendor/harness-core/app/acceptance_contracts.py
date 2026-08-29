from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ACCEPTANCE_CONTRACT_RESULT_VERSION = "1.0-acceptance-contract-result"
REQUIRED_ORDERING_CHECKS = (
    "same_sequence_uses_source_index",
    "parent_uses_earliest_descendant",
    "unsorted_preserves_relative_order",
)


@dataclass(frozen=True)
class AcceptanceContractResult:
    schema_version: str
    status: str
    contract_id: str
    kind: str
    verify_command: str
    source_order: tuple[str, ...] = ()
    target_leaf_order: tuple[str, ...] = ()
    checks: dict[str, str] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()
    implementation_evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def ordering_contract_required(*, title: str, demand_text: str) -> bool:
    text = "\n".join([title, demand_text])
    has_ordering = any(token in text for token in ("排序", "顺序号", "显示顺序"))
    has_tree = any(token in text for token in ("方案树", "科室树", "树形", "树结构"))
    has_schedule_relation = "右侧排班" in text or "排班卡片" in text
    has_explicit_consistency = any(token in text for token in ("保持一致", "同步")) and any(
        token in text for token in ("排班", "卡片")
    )
    has_relation = has_schedule_relation or has_explicit_consistency
    return has_ordering and has_tree and has_relation


def load_acceptance_contract(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("验收契约根节点必须是 JSON 对象。")
    return payload


def execute_acceptance_contract(path: str | Path) -> AcceptanceContractResult:
    try:
        payload = load_acceptance_contract(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return blocked_result(blockers=(f"无法读取验收契约：{exc}",))

    blockers = validate_ordering_contract(payload)
    contract_id = str(payload.get("id") or "")
    kind = str(payload.get("kind") or "")
    verify_command = str(payload.get("verify_command") or "").strip()
    implementation_evidence = tuple(normalize_text_list((payload.get("implementation_evidence") or {}).get("all_of")))
    if blockers:
        return blocked_result(
            contract_id=contract_id,
            kind=kind,
            verify_command=verify_command,
            blockers=tuple(blockers),
            implementation_evidence=implementation_evidence,
        )

    try:
        source = payload["source"]
        target = payload["target"]
        fixture = payload["fixture"]
        source_rows = fixture["schedule_rows"]
        tree = fixture["department_tree"]
        source_order, source_keys = build_source_order(rows=source_rows, department_key=str(source["department_key"]))
        sorted_tree, _ = sort_tree_node(
            tree,
            department_key=str(target["leaf_key"]),
            source_keys=source_keys,
        )
        target_leaf_order = flatten_tree_leaves(sorted_tree, leaf_key=str(target["leaf_key"]))
    except (KeyError, TypeError, ValueError) as exc:
        return blocked_result(
            contract_id=contract_id,
            kind=kind,
            verify_command=verify_command,
            blockers=(f"验收 fixture 无法执行：{exc}",),
            implementation_evidence=implementation_evidence,
        )

    checks = evaluate_ordering_checks(
        source_rows=source_rows,
        tree=tree,
        leaf_key=str(target["leaf_key"]),
        source_keys=source_keys,
    )
    execution_blockers = ordering_check_blockers(checks)
    comparison_error = first_order_difference(source_order=source_order, target_leaf_order=target_leaf_order)
    if comparison_error:
        execution_blockers.append(comparison_error)
    if execution_blockers:
        return AcceptanceContractResult(
            schema_version=ACCEPTANCE_CONTRACT_RESULT_VERSION,
            status="blocked",
            contract_id=contract_id,
            kind=kind,
            verify_command=verify_command,
            source_order=tuple(source_order),
            target_leaf_order=tuple(target_leaf_order),
            checks=checks,
            blockers=tuple(execution_blockers),
            implementation_evidence=implementation_evidence,
        )
    return AcceptanceContractResult(
        schema_version=ACCEPTANCE_CONTRACT_RESULT_VERSION,
        status="pass",
        contract_id=contract_id,
        kind=kind,
        verify_command=verify_command,
        source_order=tuple(source_order),
        target_leaf_order=tuple(target_leaf_order),
        checks=checks,
        implementation_evidence=implementation_evidence,
    )


def validate_ordering_contract(payload: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if payload.get("schema_version") != "1.0-acceptance-contract":
        blockers.append("schema_version 必须为 1.0-acceptance-contract。")
    if payload.get("kind") != "ordering_relation":
        blockers.append("kind 必须为 ordering_relation。")
    if not str(payload.get("id") or "").strip():
        blockers.append("缺少契约 id。")

    source = payload.get("source")
    if not isinstance(source, dict):
        blockers.append("source 必须是对象。")
        source = {}
    if source.get("collection") != "schedule_rows":
        blockers.append("source.collection 必须为 schedule_rows。")
    if not str(source.get("department_key") or "").strip():
        blockers.append("缺少 source.department_key。")
    if source.get("order_keys") != ["shunXuHao", "sourceIndex"]:
        blockers.append("source.order_keys 必须为 [shunXuHao, sourceIndex]。")
    if source.get("deduplicate") != "first_department_occurrence":
        blockers.append("source.deduplicate 必须为 first_department_occurrence。")
    if source.get("unsorted_behavior") != "preserve_relative_order":
        blockers.append("source.unsorted_behavior 必须为 preserve_relative_order。")

    target = payload.get("target")
    if not isinstance(target, dict):
        blockers.append("target 必须是对象。")
        target = {}
    if target.get("collection") != "department_tree":
        blockers.append("target.collection 必须为 department_tree。")
    if not str(target.get("leaf_key") or "").strip():
        blockers.append("缺少 target.leaf_key。")
    if target.get("parent_order") != "earliest_descendant_source_key":
        blockers.append("target.parent_order 必须为 earliest_descendant_source_key。")
    if target.get("comparison") != "flattened_leaf_order":
        blockers.append("target.comparison 必须为 flattened_leaf_order。")
    if payload.get("required_checks") != list(REQUIRED_ORDERING_CHECKS):
        blockers.append("required_checks 必须声明同号、父节点和无序号三项验收。")

    fixture = payload.get("fixture")
    if not isinstance(fixture, dict) or not isinstance(fixture.get("schedule_rows"), list) or not isinstance(fixture.get("department_tree"), dict):
        blockers.append("fixture 必须包含 schedule_rows 数组和 department_tree 对象。")
    if not str(payload.get("verify_command") or "").strip():
        blockers.append("缺少 verify_command，fixture 不能替代目标代码专项测试。")
    implementation = payload.get("implementation_evidence")
    if not isinstance(implementation, dict) or not normalize_text_list(implementation.get("all_of")):
        blockers.append("缺少 implementation_evidence.all_of。")
    return blockers


def build_source_order(*, rows: list[dict[str, Any]], department_key: str) -> tuple[list[str], dict[str, tuple[int, int, int]]]:
    decorated: list[tuple[tuple[int, int, int], str]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("schedule_rows 只能包含对象。")
        department_id = str(row.get(department_key) or "").strip()
        if not department_id:
            raise ValueError(f"schedule_rows 缺少 {department_key}。")
        decorated.append((source_sort_key(row), department_id))
    decorated.sort(key=lambda item: item[0])

    order: list[str] = []
    keys: dict[str, tuple[int, int, int]] = {}
    for key, department_id in decorated:
        if department_id not in keys:
            keys[department_id] = key
            order.append(department_id)
    return order, keys


def source_sort_key(row: dict[str, Any]) -> tuple[int, int, int]:
    source_index = parse_integer(row.get("sourceIndex"), "sourceIndex")
    sequence_value = row.get("shunXuHao")
    if sequence_value in (None, "", 0, "0"):
        return 1, 0, source_index
    return 0, parse_integer(sequence_value, "shunXuHao"), source_index


def sort_tree_node(
    node: dict[str, Any],
    *,
    department_key: str,
    source_keys: dict[str, tuple[int, int, int]],
) -> tuple[dict[str, Any], tuple[int, int, int] | None]:
    if not isinstance(node, dict):
        raise ValueError("department_tree 节点必须是对象。")
    copied = dict(node)
    child_entries: list[tuple[dict[str, Any], tuple[int, int, int] | None, int]] = []
    children = node.get("children")
    if children is not None and not isinstance(children, list):
        raise ValueError("department_tree.children 必须是数组。")
    for index, child in enumerate(children or []):
        sorted_child, child_key = sort_tree_node(child, department_key=department_key, source_keys=source_keys)
        child_entries.append((sorted_child, child_key, index))
    child_entries.sort(key=lambda item: (item[1] is None, item[1] or (0, 0, 0), item[2]))
    if children is not None:
        copied["children"] = [item[0] for item in child_entries]

    own_id = str(node.get(department_key) or "").strip()
    keys = [source_keys[own_id]] if own_id in source_keys else []
    keys.extend(item[1] for item in child_entries if item[1] is not None)
    return copied, min(keys) if keys else None


def flatten_tree_leaves(node: dict[str, Any], *, leaf_key: str) -> list[str]:
    children = node.get("children")
    if isinstance(children, list) and children:
        leaves: list[str] = []
        for child in children:
            leaves.extend(flatten_tree_leaves(child, leaf_key=leaf_key))
        return leaves
    value = str(node.get(leaf_key) or "").strip()
    return [value] if value else []


def first_order_difference(*, source_order: list[str], target_leaf_order: list[str]) -> str:
    for index, (source_value, target_value) in enumerate(zip(source_order, target_leaf_order)):
        if source_value != target_value:
            return f"源列表与方案树叶子顺序在第 {index + 1} 项不一致：{source_value} != {target_value}。"
    if len(source_order) != len(target_leaf_order):
        return f"源列表与方案树叶子数量不一致：{len(source_order)} != {len(target_leaf_order)}。"
    return ""


def evaluate_ordering_checks(
    *,
    source_rows: list[dict[str, Any]],
    tree: dict[str, Any],
    leaf_key: str,
    source_keys: dict[str, tuple[int, int, int]],
) -> dict[str, str]:
    sequence_counts: dict[int, int] = {}
    unsorted_count = 0
    for row in source_rows:
        value = row.get("shunXuHao")
        if value in (None, "", 0, "0"):
            unsorted_count += 1
            continue
        sequence = parse_integer(value, "shunXuHao")
        sequence_counts[sequence] = sequence_counts.get(sequence, 0) + 1
    return {
        "same_sequence_uses_source_index": "pass" if any(count >= 2 for count in sequence_counts.values()) else "blocked",
        "parent_uses_earliest_descendant": "pass"
        if tree_has_descendant_only_parent(tree, leaf_key=leaf_key, source_keys=source_keys)
        else "blocked",
        "unsorted_preserves_relative_order": "pass" if unsorted_count >= 2 else "blocked",
    }


def tree_has_descendant_only_parent(
    node: dict[str, Any],
    *,
    leaf_key: str,
    source_keys: dict[str, tuple[int, int, int]],
    depth: int = 0,
) -> bool:
    children = node.get("children")
    if not isinstance(children, list) or not children:
        return False
    own_id = str(node.get(leaf_key) or "").strip()
    has_source_descendant = any(
        tree_contains_source_key(child, leaf_key=leaf_key, source_keys=source_keys) for child in children
    )
    if depth > 0 and own_id not in source_keys and has_source_descendant:
        return True
    return any(
        tree_has_descendant_only_parent(
            child,
            leaf_key=leaf_key,
            source_keys=source_keys,
            depth=depth + 1,
        )
        for child in children
        if isinstance(child, dict)
    )


def tree_contains_source_key(
    node: dict[str, Any],
    *,
    leaf_key: str,
    source_keys: dict[str, tuple[int, int, int]],
) -> bool:
    own_id = str(node.get(leaf_key) or "").strip()
    if own_id in source_keys:
        return True
    children = node.get("children")
    return isinstance(children, list) and any(
        tree_contains_source_key(child, leaf_key=leaf_key, source_keys=source_keys)
        for child in children
        if isinstance(child, dict)
    )


def ordering_check_blockers(checks: dict[str, str]) -> list[str]:
    messages = {
        "same_sequence_uses_source_index": "fixture 未包含至少一组同顺序号排班，无法验证 sourceIndex 稳定排序。",
        "parent_uses_earliest_descendant": "fixture 未包含由排班子孙节点决定排序键的方案父节点。",
        "unsorted_preserves_relative_order": "fixture 未包含至少两条无顺序号排班，无法验证相对顺序。",
    }
    return [messages[key] for key in REQUIRED_ORDERING_CHECKS if checks.get(key) != "pass"]


def blocked_result(
    *,
    contract_id: str = "",
    kind: str = "",
    verify_command: str = "",
    blockers: tuple[str, ...],
    implementation_evidence: tuple[str, ...] = (),
) -> AcceptanceContractResult:
    return AcceptanceContractResult(
        schema_version=ACCEPTANCE_CONTRACT_RESULT_VERSION,
        status="blocked",
        contract_id=contract_id,
        kind=kind,
        verify_command=verify_command,
        blockers=blockers,
        implementation_evidence=implementation_evidence,
    )


def parse_integer(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是整数。") from exc


def normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
