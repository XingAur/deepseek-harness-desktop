from __future__ import annotations

from typing import Any, Mapping


BUSINESS_ACCEPTANCE_SCHEMA_VERSION = "his-business-acceptance.v1"


def build_business_acceptance_status(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build a truthful business acceptance status.

    Offline technical gates can be recorded as evidence, but they cannot make
    `business_valid` true without explicit runtime/business verification.
    """
    if evidence is None:
        return _status(
            status="not_verified",
            business_valid=False,
            runtime_verified=False,
            prerequisites={
                "his_test_environment": "missing",
                "test_account": "missing",
                "test_data": "missing",
                "manual_or_runtime_evidence": "missing",
            },
            evidence={},
        )
    values = _validate_evidence(evidence)
    accepted = bool(values["accepted"])
    runtime_verified = bool(values["runtime_verified"])
    evidence_complete = bool(
        values["environment"]
        and values["operator"]
        and values["account_alias"]
        and values["test_data_alias"]
        and values["scenarios"]
        and all(_scenario_complete(scenario) for scenario in values["scenarios"])
    )
    business_valid = accepted and runtime_verified and evidence_complete
    if business_valid:
        status = "accepted"
    elif accepted or values["scenarios"]:
        status = "evidence_recorded"
    else:
        status = "rejected"
    return _status(
        status=status,
        business_valid=business_valid,
        runtime_verified=runtime_verified,
        prerequisites={
            "his_test_environment": "passed" if values["environment"] else "missing",
            "test_account": "passed" if values["account_alias"] else "missing",
            "test_data": "passed" if values["test_data_alias"] else "missing",
            "manual_or_runtime_evidence": (
                "passed"
                if values["scenarios"] and all(_scenario_complete(scenario) for scenario in values["scenarios"])
                else "missing"
            ),
        },
        evidence=values,
    )


def _status(
    *,
    status: str,
    business_valid: bool,
    runtime_verified: bool,
    prerequisites: Mapping[str, str],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": BUSINESS_ACCEPTANCE_SCHEMA_VERSION,
        "status": status,
        "business_valid": business_valid,
        "runtime_verified": runtime_verified,
        "prerequisites": dict(prerequisites),
        "evidence": dict(evidence),
        "boundary": "离线测试、回放和 enterprise gate 不能替代 HIS 页面、接口、数据库或人工业务验收。",
    }


def _validate_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        raise ValueError("business evidence must be a mapping")
    environment = evidence.get("environment", "")
    operator = evidence.get("operator", "")
    account_alias = evidence.get("account_alias", "")
    test_data_alias = evidence.get("test_data_alias", "")
    scenarios = evidence.get("scenarios", [])
    if not all(
        isinstance(value, str)
        for value in (environment, operator, account_alias, test_data_alias)
    ):
        raise ValueError("business evidence environment/operator/account_alias/test_data_alias must be strings")
    if not isinstance(evidence.get("accepted", False), bool) or not isinstance(evidence.get("runtime_verified", False), bool):
        raise ValueError("business evidence accepted/runtime_verified must be booleans")
    if not isinstance(scenarios, list):
        raise ValueError("business evidence scenarios must be a list")
    normalized_scenarios = []
    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            raise ValueError("business scenario must be a mapping")
        name = scenario.get("name", "")
        status = scenario.get("status", "")
        expected = scenario.get("expected", "")
        actual = scenario.get("actual", "")
        evidence_text = scenario.get("evidence", "")
        evidence_hashes = scenario.get("evidence_hashes", [])
        if not isinstance(name, str) or not name.strip() or status not in {"passed", "failed", "needs_evidence"}:
            raise ValueError("business scenario shape invalid")
        if not all(isinstance(value, str) for value in (expected, actual, evidence_text)):
            raise ValueError("business scenario expected/actual/evidence must be strings")
        if not isinstance(evidence_hashes, list) or not all(isinstance(item, str) for item in evidence_hashes):
            raise ValueError("business scenario evidence_hashes must be a string list")
        normalized_scenarios.append(
            {
                "name": name.strip(),
                "status": status,
                "expected": expected.strip(),
                "actual": actual.strip(),
                "evidence": evidence_text.strip(),
                "evidence_hashes": [item.strip() for item in evidence_hashes if item.strip()],
            }
        )
    return {
        "environment": environment.strip(),
        "operator": operator.strip(),
        "account_alias": account_alias.strip(),
        "test_data_alias": test_data_alias.strip(),
        "accepted": evidence["accepted"],
        "runtime_verified": evidence["runtime_verified"],
        "scenarios": normalized_scenarios,
    }


def _scenario_complete(scenario: Mapping[str, Any]) -> bool:
    return bool(
        scenario.get("status") == "passed"
        and scenario.get("expected")
        and scenario.get("actual")
        and scenario.get("evidence")
    )
