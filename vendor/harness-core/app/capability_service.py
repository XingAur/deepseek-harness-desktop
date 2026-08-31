from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from app.capability_contracts import (
    RESULT_SCHEMA_VERSION,
    CapabilityRequest,
    CapabilityResult,
    MutationLevel,
)
from app.capability_permissions import PermissionDecision
from app.capability_registry import CapabilityDescriptor
from app.capability_runtime import CapabilityExecution, CapabilityPreflight


ROUTING_MODES = frozenset({"legacy", "observe", "enforce"})
ROUTABLE_STATUSES = frozenset(
    {"success", "blocked", "failed", "partial", "unsupported"}
)
_SAFE_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*)*$")
_SENSITIVE_FIELD_TERMS = (
    "authorization",
    "cookie",
    "credential",
    "dsn",
    "password",
    "secret",
    "token",
)
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "key",
        "apikey",
        "accesskey",
        "privatekey",
        "signingkey",
        "encryptionkey",
        "pat",
        "readpat",
        "writepat",
        "apipat",
        "devopspat",
        "aliyundevopspat",
    }
)
_MISSING = object()


class CapabilityRuntimeLike(Protocol):
    def preflight(self, request: CapabilityRequest) -> CapabilityPreflight:
        raise NotImplementedError

    def execute(
        self,
        request: CapabilityRequest,
        *,
        timeout_seconds: int | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> CapabilityExecution:
        raise NotImplementedError


@dataclass(frozen=True)
class CapabilityRouteResult:
    mode: str
    selected: str
    result: Mapping[str, Any]
    comparison: Mapping[str, Any]
    fallback_used: bool


@dataclass(frozen=True)
class LegacyReadFallbackPolicy:
    mutation_level: MutationLevel
    fallback_on_failed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.mutation_level, MutationLevel):
            raise TypeError("mutation_level must be a MutationLevel")
        if not isinstance(self.fallback_on_failed, bool):
            raise TypeError("fallback_on_failed must be a bool")
        if self.mutation_level > MutationLevel.L1:
            raise ValueError("legacy fallback policy only supports L0/L1 reads")


class CapabilityService:
    def __init__(
        self,
        runtime: CapabilityRuntimeLike,
        *,
        routing_mode: str,
        runtime_environment: Mapping[str, str] | None = None,
        capability_environments: Mapping[
            tuple[str, str], Mapping[str, str]
        ] | None = None,
        legacy_read_policies: Mapping[
            tuple[str, str], LegacyReadFallbackPolicy
        ] | None = None,
    ) -> None:
        if routing_mode not in ROUTING_MODES:
            raise ValueError("routing_mode must be legacy, observe, or enforce")
        if runtime_environment is not None and capability_environments is not None:
            raise ValueError(
                "runtime_environment and capability_environments are mutually exclusive"
            )
        self._runtime = runtime
        self._routing_mode = routing_mode
        self._runtime_environment = (
            None
            if runtime_environment is None
            else {
                str(key): value
                for key, value in runtime_environment.items()
                if isinstance(key, str) and isinstance(value, str)
            }
        )
        self._capability_environments = {
            (str(capability), str(provider)): {
                str(key): value
                for key, value in environment.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            for (capability, provider), environment in (
                {}
                if capability_environments is None
                else capability_environments
            ).items()
            if (
                isinstance(capability, str)
                and isinstance(provider, str)
                and isinstance(environment, Mapping)
            )
        }
        self._legacy_read_policies = self._validate_policies(
            {} if legacy_read_policies is None else legacy_read_policies
        )

    @property
    def routing_mode(self) -> str:
        return self._routing_mode

    def route(
        self,
        request: CapabilityRequest,
        *,
        legacy_callable: Callable[[], Mapping[str, Any]] | None = None,
        equivalence_fields: Sequence[str] = (),
    ) -> CapabilityRouteResult:
        if not isinstance(request, CapabilityRequest):
            raise TypeError("request must be a CapabilityRequest")
        if self._routing_mode == "legacy":
            return self._legacy_route(request, legacy_callable)
        if self._routing_mode == "observe":
            return self._observe_route(
                request,
                legacy_callable=legacy_callable,
                equivalence_fields=equivalence_fields,
            )
        return self._enforce_route(request, legacy_callable)

    def _legacy_route(
        self,
        request: CapabilityRequest,
        legacy_callable: Callable[[], Mapping[str, Any]] | None,
    ) -> CapabilityRouteResult:
        if legacy_callable is None:
            return self._failed_route(request, "CAPABILITY_LEGACY_UNAVAILABLE")
        result = self._call_legacy(request, legacy_callable)
        return CapabilityRouteResult(
            mode="legacy",
            selected="legacy",
            result=result,
            comparison={},
            fallback_used=False,
        )

    def _observe_route(
        self,
        request: CapabilityRequest,
        *,
        legacy_callable: Callable[[], Mapping[str, Any]] | None,
        equivalence_fields: Sequence[str],
    ) -> CapabilityRouteResult:
        preflight_error = self._observe_preflight(request)
        if preflight_error is not None:
            return preflight_error
        if (
            request.mode != "preview"
            or request.mutation_level > MutationLevel.L1
        ):
            return self._failed_route(
                request,
                "CAPABILITY_OBSERVE_REQUIRES_READONLY_PREVIEW",
                comparison={"status": "not_compared", "fields": {}},
            )
        if legacy_callable is None:
            return self._failed_route(request, "CAPABILITY_LEGACY_UNAVAILABLE")
        legacy_result = self._call_legacy(request, legacy_callable)
        capability_result = self._call_capability(request)
        comparison = self._compare(
            legacy_result,
            capability_result,
            equivalence_fields,
        )
        return CapabilityRouteResult(
            mode="observe",
            selected="legacy",
            result=legacy_result,
            comparison=comparison,
            fallback_used=False,
        )

    def _observe_preflight(
        self,
        request: CapabilityRequest,
    ) -> CapabilityRouteResult | None:
        try:
            preflight = self._runtime.preflight(request)
        except Exception:
            return self._failed_route(
                request,
                "CAPABILITY_PREFLIGHT_FAILED",
                comparison={"status": "not_compared", "fields": {}},
            )
        if not self._valid_preflight(preflight, request):
            return self._failed_route(
                request,
                "CAPABILITY_PREFLIGHT_INVALID",
                comparison={"status": "not_compared", "fields": {}},
            )
        descriptor = preflight.descriptor
        permission = preflight.permission
        if not descriptor.enabled:
            return self._blocked_route(
                request,
                "CAPABILITY_DISABLED",
                comparison={"status": "not_compared", "fields": {}},
            )
        if not permission.allowed:
            return self._blocked_route(
                request,
                "CAPABILITY_PERMISSION_DENIED",
                comparison={"status": "not_compared", "fields": {}},
            )
        return None

    @staticmethod
    def _valid_preflight(
        preflight: object,
        request: CapabilityRequest,
    ) -> bool:
        if not isinstance(preflight, CapabilityPreflight):
            return False
        descriptor = preflight.descriptor
        permission = preflight.permission
        if (
            not isinstance(descriptor, CapabilityDescriptor)
            or not isinstance(permission, PermissionDecision)
            or descriptor.name != request.capability
            or descriptor.provider != request.provider
            or permission.required_level is not descriptor.mutation_level
            or permission.status not in {"allowed", "blocked"}
            or permission.allowed != (permission.status == "allowed")
            or (permission.allowed and bool(permission.blockers))
            or (not permission.allowed and not permission.blockers)
        ):
            return False
        return True

    def _enforce_route(
        self,
        request: CapabilityRequest,
        legacy_callable: Callable[[], Mapping[str, Any]] | None,
    ) -> CapabilityRouteResult:
        capability_result = self._call_capability(request)
        status = capability_result["status"]
        route_failed = (
            (capability_result.get("audit") or {}).get("error_code")
            == "CAPABILITY_ROUTE_FAILED"
        )
        policy = self._legacy_read_policies.get(
            (request.capability, request.provider)
        )
        can_fallback = (
            legacy_callable is not None
            and policy is not None
            and request.mode == "preview"
            and request.mutation_level <= MutationLevel.L1
            and request.mutation_level == policy.mutation_level
            and not route_failed
            and (
                status == "unsupported"
                or (status == "failed" and policy.fallback_on_failed)
            )
        )
        if can_fallback:
            return CapabilityRouteResult(
                mode="enforce",
                selected="legacy",
                result=self._call_legacy(request, legacy_callable),
                comparison={"capability_status": status},
                fallback_used=True,
            )
        return CapabilityRouteResult(
            mode="enforce",
            selected="capability",
            result=capability_result,
            comparison={},
            fallback_used=False,
        )

    def _call_capability(self, request: CapabilityRequest) -> Mapping[str, Any]:
        try:
            environment = self._capability_environments.get(
                (request.capability, request.provider)
            )
            if environment is None:
                environment = self._runtime_environment
            execution = (
                self._runtime.execute(request)
                if environment is None
                else self._runtime.execute(
                    request,
                    environment=environment,
                )
            )
            result = execution.result
            if not isinstance(result, CapabilityResult):
                raise TypeError("capability runtime returned an invalid result")
            payload = result.to_dict()
            if payload.get("status") not in ROUTABLE_STATUSES:
                raise ValueError("capability runtime returned an unknown status")
            return payload
        except Exception:
            return self._failed_result(request, "CAPABILITY_ROUTE_FAILED")

    def _call_legacy(
        self,
        request: CapabilityRequest,
        legacy_callable: Callable[[], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        try:
            result = legacy_callable()
            if not isinstance(result, Mapping):
                raise TypeError("legacy adapter returned an invalid result")
            return dict(result)
        except Exception:
            return self._failed_result(request, "CAPABILITY_LEGACY_FAILED")

    @staticmethod
    def _compare(
        legacy_result: Mapping[str, Any],
        capability_result: Mapping[str, Any],
        equivalence_fields: Sequence[str],
    ) -> Mapping[str, Any]:
        status_valid, status_equal = CapabilityService._safe_equal(
            legacy_result.get("status", _MISSING),
            capability_result.get("status", _MISSING),
        )
        status_equal = status_equal if status_valid else False
        fields: dict[str, Mapping[str, bool]] = {
            "status": {"equal": status_equal}
        }
        redacted_count = 0
        different = not status_equal
        for field in equivalence_fields:
            if not isinstance(field, str) or not _SAFE_FIELD.fullmatch(field):
                redacted_count += 1
                different = True
                continue
            try:
                valid, equal = CapabilityService._safe_equal(
                    CapabilityService._field_value(legacy_result, field),
                    CapabilityService._field_value(capability_result, field),
                )
            except Exception:
                valid, equal = False, False
            if CapabilityService._is_sensitive_field(field):
                redacted_count += 1
                different = different or not valid or not equal
                continue
            if not valid:
                redacted_count += 1
                different = True
                continue
            fields[field] = {"equal": equal}
            different = different or not equal
        comparison: dict[str, Any] = {
            "status": "different" if different else "same",
            "fields": fields,
        }
        if redacted_count:
            comparison["redacted_field_count"] = redacted_count
        return comparison

    @staticmethod
    def _is_sensitive_field(field: str) -> bool:
        for segment in field.split("."):
            normalized = re.sub(r"[^a-z0-9]", "", segment.casefold())
            if normalized in _SENSITIVE_FIELD_NAMES:
                return True
            if any(term in normalized for term in _SENSITIVE_FIELD_TERMS):
                return True
        return False

    @staticmethod
    def _safe_equal(left: Any, right: Any) -> tuple[bool, bool]:
        if left is _MISSING or right is _MISSING:
            return True, left is right
        if type(left) is not type(right):
            return False, False
        if left is None:
            return True, True
        if type(left) in {bool, int, str}:
            return True, bool(left == right)
        if type(left) is float:
            if not math.isfinite(left) or not math.isfinite(right):
                return False, False
            return True, bool(left == right)
        return False, False

    @staticmethod
    def _field_value(payload: Mapping[str, Any], field: str) -> Any:
        value: Any = payload
        for segment in field.split("."):
            if not isinstance(value, Mapping):
                return _MISSING
            value = value.get(segment, _MISSING)
            if value is _MISSING:
                return _MISSING
        return value

    @staticmethod
    def _validate_policies(
        policies: Mapping[tuple[str, str], LegacyReadFallbackPolicy],
    ) -> dict[tuple[str, str], LegacyReadFallbackPolicy]:
        validated: dict[tuple[str, str], LegacyReadFallbackPolicy] = {}
        for key, policy in policies.items():
            if (
                not isinstance(key, tuple)
                or len(key) != 2
                or any(not isinstance(item, str) or not item for item in key)
                or not isinstance(policy, LegacyReadFallbackPolicy)
            ):
                raise ValueError("legacy read fallback policy is invalid")
            validated[key] = policy
        return validated

    def _failed_route(
        self,
        request: CapabilityRequest,
        error_code: str,
        *,
        comparison: Mapping[str, Any] | None = None,
    ) -> CapabilityRouteResult:
        return CapabilityRouteResult(
            mode=self._routing_mode,
            selected="none",
            result=self._failed_result(request, error_code),
            comparison={} if comparison is None else dict(comparison),
            fallback_used=False,
        )

    def _blocked_route(
        self,
        request: CapabilityRequest,
        error_code: str,
        *,
        comparison: Mapping[str, Any] | None = None,
    ) -> CapabilityRouteResult:
        return CapabilityRouteResult(
            mode=self._routing_mode,
            selected="none",
            result=self._route_error_result(
                request,
                error_code,
                status="blocked",
            ),
            comparison={} if comparison is None else dict(comparison),
            fallback_used=False,
        )

    @staticmethod
    def _failed_result(
        request: CapabilityRequest,
        error_code: str,
    ) -> Mapping[str, Any]:
        return CapabilityService._route_error_result(
            request,
            error_code,
            status="failed",
        )

    @staticmethod
    def _route_error_result(
        request: CapabilityRequest,
        error_code: str,
        *,
        status: str,
    ) -> Mapping[str, Any]:
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "request_id": request.request_id,
            "capability": request.capability,
            "provider": request.provider,
            "status": status,
            "mutation_level": request.mutation_level.name,
            "changed": False,
            "summary": "能力路由被阻止。" if status == "blocked" else "能力路由失败。",
            "data": {},
            "evidence": [],
            "warnings": [],
            "blockers": [error_code] if status == "blocked" else ["能力路由未执行。"],
            "audit": {"error_code": error_code},
        }
