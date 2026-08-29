from __future__ import annotations

import unittest

from app.capability_contracts import (
    CapabilityAuthorization,
    CapabilityRequest,
    CapabilityResult,
    MutationLevel,
)
from app.capability_permissions import (
    evaluate_capability_permission,
    evaluate_capability_result_permission,
)


def request(
    *,
    mode: str = "preview",
    level: MutationLevel = MutationLevel.L1,
    explicit: bool = False,
    scope: tuple[str, ...] = (),
    capability: str = "workitem.read",
    input: dict | None = None,
) -> CapabilityRequest:
    return CapabilityRequest(
        request_id="req-1",
        capability=capability,
        provider="yunxiao",
        mode=mode,
        mutation_level=level,
        authorization=CapabilityAuthorization(explicit=explicit, scope=scope),
        input={} if input is None else input,
        context={},
    )


def result(*, changed: bool, level: MutationLevel = MutationLevel.L1) -> CapabilityResult:
    return CapabilityResult(
        request_id="req-1",
        capability="workitem.read",
        provider="yunxiao",
        status="success",
        mutation_level=level,
        changed=changed,
        summary="",
        data={},
        evidence=(),
        warnings=(),
        blockers=(),
        audit={"event": "test"} if changed else {},
    )


class CapabilityPermissionTests(unittest.TestCase):
    def test_preview_allows_levels_l0_through_l3_without_declared_scope(self) -> None:
        for level in (MutationLevel.L0, MutationLevel.L1, MutationLevel.L2, MutationLevel.L3):
            with self.subTest(level=level):
                decision = evaluate_capability_permission(
                    request=request(level=level),
                    declared_level=level,
                    declared_scopes=("target:local",),
                )

                self.assertTrue(decision.allowed)

    def test_preview_result_cannot_report_changes(self) -> None:
        decision = evaluate_capability_result_permission(
            request=request(), result=result(changed=True)
        )

        self.assertFalse(decision.allowed)
        self.assertIn("预览请求不能返回 changed=true。", decision.blockers)

    def test_apply_l2_allows_exact_local_scope(self) -> None:
        decision = evaluate_capability_permission(
            request=request(
                mode="apply", level=MutationLevel.L2, scope=("target:local",)
            ),
            declared_level=MutationLevel.L2,
            declared_scopes=("target:local",),
        )

        self.assertTrue(decision.allowed)

    def test_apply_l3_requires_explicit_authorization(self) -> None:
        decision = evaluate_capability_permission(
            request=request(
                mode="apply", level=MutationLevel.L3, scope=("target:local",)
            ),
            declared_level=MutationLevel.L3,
            declared_scopes=("target:local",),
        )

        self.assertFalse(decision.allowed)
        self.assertIn("该操作需要明确授权。", decision.blockers)

    def test_apply_l4_requires_exact_capability_and_target_scopes(self) -> None:
        decision = evaluate_capability_permission(
            request=request(
                mode="apply",
                level=MutationLevel.L4,
                explicit=True,
                scope=("target:external", "capability:workitem.read"),
            ),
            declared_level=MutationLevel.L4,
            declared_scopes=("target:external",),
            external_writes_default=True,
        )

        self.assertTrue(decision.allowed)

        missing_capability = evaluate_capability_permission(
            request=request(
                mode="apply",
                level=MutationLevel.L4,
                explicit=True,
                scope=("target:external",),
            ),
            declared_level=MutationLevel.L4,
            declared_scopes=("target:external",),
            external_writes_default=True,
        )
        self.assertFalse(missing_capability.allowed)

        extra_scope = evaluate_capability_permission(
            request=request(
                mode="apply",
                level=MutationLevel.L4,
                explicit=True,
                scope=("target:external", "target:other", "capability:workitem.read"),
            ),
            declared_level=MutationLevel.L4,
            declared_scopes=("target:external",),
            external_writes_default=True,
        )
        self.assertFalse(extra_scope.allowed)

    def test_declared_level_must_match_request_level(self) -> None:
        decision = evaluate_capability_permission(
            request=request(mode="apply", level=MutationLevel.L2, scope=("target:local",)),
            declared_level=MutationLevel.L3,
            declared_scopes=("target:local",),
        )

        self.assertFalse(decision.allowed)
        self.assertIn("请求权限等级与 capability 声明不一致。", decision.blockers)

    def test_request_input_cannot_enable_external_writes(self) -> None:
        decision = evaluate_capability_permission(
            request=request(
                mode="apply",
                level=MutationLevel.L5,
                explicit=True,
                scope=("target:external", "capability:workitem.read"),
                input={"external_writes_default": True},
            ),
            declared_level=MutationLevel.L5,
            declared_scopes=("target:external",),
            external_writes_default=False,
        )

        self.assertFalse(decision.allowed)
        self.assertIn("外部写能力默认关闭。", decision.blockers)


if __name__ == "__main__":
    unittest.main()
