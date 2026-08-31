from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from app.capability_contracts import (
    CapabilityContractError,
    CapabilityRequest,
    CapabilityResult,
    MutationLevel,
)
from app.capability_permissions import (
    PermissionDecision,
    evaluate_capability_permission,
    evaluate_capability_result_permission,
)
from app.capability_registry import (
    CapabilityDescriptor,
    CapabilityRegistry,
    PathIdentity,
    _path_identity as registry_path_identity,
)


_MAX_EXECUTION_SOURCE_BYTES = 4 * 1024 * 1024
_DATABASE_RUNTIME_ENVIRONMENT_SCHEMA = "his-database-runtime-environment.v1"
_DATABASE_READONLY_CREDENTIAL_PATTERN = re.compile(
    r"^pg_[a-z0-9_]+_readonly_(?:dsn|user|password)$"
)
_DATABASE_READONLY_SCOPES = (
    "database:metadata:read",
    "database:rows:read",
)


@dataclass(frozen=True)
class CapabilityExecution:
    descriptor: CapabilityDescriptor
    permission: PermissionDecision
    result: CapabilityResult
    duration_ms: int


@dataclass(frozen=True)
class CapabilityPreflight:
    descriptor: CapabilityDescriptor
    permission: PermissionDecision


class CapabilityRuntime:
    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        external_writes_default: bool = False,
        default_timeout_seconds: int = 60,
        environment_allowlist: Sequence[str] = (),
        provider_python: str | None = None,
    ) -> None:
        self._registry = registry
        self._external_writes_default = external_writes_default
        self._default_timeout_seconds = self._valid_timeout(default_timeout_seconds)
        self._environment_allowlist = frozenset(environment_allowlist)
        candidate_python = provider_python or sys.executable
        if not isinstance(candidate_python, str) or not candidate_python:
            raise ValueError("provider_python_invalid")
        try:
            # Keep the venv launcher path instead of resolving it to the
            # system interpreter behind the symlink.  The isolated Harness
            # runtime may intentionally provide dependencies (for example
            # psycopg) that are not installed in the system Python.
            resolved_python = Path(candidate_python).expanduser().absolute()
        except (OSError, RuntimeError):
            raise ValueError("provider_python_invalid") from None
        if not resolved_python.is_file() or not os.access(resolved_python, os.X_OK):
            raise ValueError("provider_python_invalid")
        self._provider_python = str(resolved_python)

    def preflight(self, request: CapabilityRequest) -> CapabilityPreflight:
        if not isinstance(request, CapabilityRequest):
            raise TypeError("request must be a CapabilityRequest")
        descriptor = self._registry.resolve(request.capability, request.provider)
        permission = evaluate_capability_permission(
            request=request,
            declared_level=descriptor.mutation_level,
            declared_scopes=descriptor.scopes,
            external_writes_default=self._external_writes_default,
        )
        return CapabilityPreflight(
            descriptor=descriptor,
            permission=permission,
        )

    def execute(
        self,
        request: CapabilityRequest,
        *,
        timeout_seconds: int | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> CapabilityExecution:
        started = time.monotonic()
        timeout = self._default_timeout_seconds if timeout_seconds is None else self._valid_timeout(timeout_seconds)
        preflight = self.preflight(request)
        descriptor = preflight.descriptor
        permission = preflight.permission
        if not descriptor.enabled:
            return self._execution(descriptor, permission, request, "blocked", "CAPABILITY_DISABLED", started)
        if not permission.allowed:
            return self._execution(descriptor, permission, request, "blocked", "CAPABILITY_PERMISSION_DENIED", started)

        entrypoint = self._safe_entrypoint(descriptor)
        if entrypoint is None:
            return self._execution(descriptor, permission, request, "blocked", "CAPABILITY_ENTRYPOINT_INVALID", started)

        try:
            child_environment, environment_keys, environment_values = self._environment(environment)
        except ValueError:
            return self._execution(
                descriptor, permission, request, "failed", "CAPABILITY_ENVIRONMENT_INVALID", started
            )
        database_environment = {
            key: child_environment.pop(key)
            for key in environment_keys
            if _DATABASE_READONLY_CREDENTIAL_PATTERN.fullmatch(key)
        }
        environment_keys = tuple(
            key for key in environment_keys if key not in database_environment
        )
        environment_values = tuple(
            child_environment[key]
            for key in environment_keys
            if child_environment[key]
        )
        uses_database_environment_transport = (
            self._uses_database_environment_transport(descriptor, entrypoint)
        )
        if uses_database_environment_transport:
            for key in environment_keys:
                child_environment.pop(key, None)
            environment_keys = tuple(sorted(database_environment))
            environment_values = tuple(
                value for value in database_environment.values() if value
            )
        try:
            request_json = json.dumps(request.to_dict())
        except (TypeError, ValueError):
            return self._execution(descriptor, permission, request, "failed", "CAPABILITY_REQUEST_INVALID", started)
        try:
            with tempfile.TemporaryDirectory() as directory:
                # macOS may expose the temporary root through /var -> /private/var.
                # Resolve it before handing request/output paths to providers that
                # deliberately reject symlinked parent directories.
                temp_dir = Path(directory).resolve()
                child_environment["TMPDIR"] = str(temp_dir)
                sensitive_environment_values = (*environment_values, str(temp_dir))
                try:
                    execution_entrypoint = self._execution_entrypoint(
                        descriptor,
                        entrypoint,
                        temp_dir,
                    )
                except (OSError, RuntimeError, ValueError):
                    return self._execution(
                        descriptor,
                        permission,
                        request,
                        "blocked",
                        "CAPABILITY_ENTRYPOINT_INVALID",
                        started,
                        environment_keys,
                    )
                request_path = temp_dir / "request.json"
                result_path = temp_dir / "result.json"
                request_path.write_text(request_json, encoding="utf-8")
                command = [
                    self._provider_python, str(execution_entrypoint), "--request", str(request_path),
                    "--output", str(result_path),
                ]
                if uses_database_environment_transport:
                    runtime_environment_path = temp_dir / "database-runtime-environment.json"
                    runtime_environment = json.dumps(
                        {
                            "schema_version": _DATABASE_RUNTIME_ENVIRONMENT_SCHEMA,
                            "environment": database_environment,
                        },
                        sort_keys=True,
                    ).encode("utf-8")
                    self._write_private_source(
                        runtime_environment_path,
                        runtime_environment,
                    )
                    command.extend(
                        [
                            "--runtime-environment-file",
                            str(runtime_environment_path),
                            "--runtime-environment-sha256",
                            hashlib.sha256(runtime_environment).hexdigest(),
                        ]
                    )
                try:
                    process = subprocess.run(
                        command,
                        shell=False,
                        capture_output=True,
                        text=False,
                        env=child_environment,
                        timeout=timeout,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    return self._execution(descriptor, permission, request, "failed", "CAPABILITY_TIMEOUT", started, environment_keys)
                if process.returncode != 0:
                    return self._execution(descriptor, permission, request, "failed", "CAPABILITY_PROCESS_FAILED", started, environment_keys)
                if process.stdout.strip():
                    return self._execution(descriptor, permission, request, "blocked", "CAPABILITY_STDOUT_NOT_EMPTY", started, environment_keys)
                if not result_path.is_file():
                    return self._execution(descriptor, permission, request, "failed", "CAPABILITY_RESULT_MISSING", started, environment_keys)
                try:
                    payload = json.loads(result_path.read_text(encoding="utf-8"))
                    result = CapabilityResult.from_dict(payload, request=request)
                    self._validate_identity(descriptor, request, result)
                except (OSError, json.JSONDecodeError, CapabilityContractError, ValueError):
                    return self._execution(descriptor, permission, request, "blocked", "CAPABILITY_RESULT_INVALID", started, environment_keys)
        except OSError:
            return self._execution(descriptor, permission, request, "failed", "CAPABILITY_PROCESS_FAILED", started, environment_keys)

        public_result = result.to_dict()
        public_result.pop("audit", None)
        if self._contains_injected_environment_value(
            public_result,
            sensitive_environment_values,
        ):
            return self._execution(
                descriptor, permission, request, "blocked", "CAPABILITY_RESULT_SENSITIVE_OUTPUT",
                started, environment_keys,
            )
        if self._contains_injected_environment_value(
            result.audit,
            sensitive_environment_values,
        ):
            return self._execution(
                descriptor, permission, request, "blocked", "CAPABILITY_RESULT_SENSITIVE_AUDIT",
                started, environment_keys,
            )
        result_permission = evaluate_capability_result_permission(request=request, result=result)
        if not result_permission.allowed:
            return self._execution(descriptor, result_permission, request, "blocked", "CAPABILITY_RESULT_FORBIDDEN", started, environment_keys)
        result = self._with_environment_audit(result, environment_keys)
        return CapabilityExecution(
            descriptor=descriptor,
            permission=result_permission,
            result=result,
            duration_ms=self._duration_ms(started),
        )

    def _safe_entrypoint(self, descriptor: CapabilityDescriptor) -> Path | None:
        declared = descriptor.declared_entrypoint
        if (
            descriptor.plugin_root is None
            or descriptor.plugin_root_identity is None
            or descriptor.entrypoint_identity is None
            or declared is None
            or not declared.exists()
            or not declared.is_file()
        ):
            return None
        try:
            if self._path_identity(descriptor.plugin_root) != descriptor.plugin_root_identity:
                return None
            resolved = declared.resolve(strict=True)
            resolved_root = descriptor.plugin_root.resolve(strict=True)
            resolved.relative_to(resolved_root)
            if self._path_identity(resolved) != descriptor.entrypoint_identity:
                return None
            for dependency, expected_identity in descriptor.dependency_identities:
                resolved_dependency = dependency.resolve(strict=True)
                resolved_dependency.relative_to(resolved_root)
                if (
                    resolved_dependency != dependency
                    or not resolved_dependency.is_file()
                    or self._path_identity(resolved_dependency) != expected_identity
                ):
                    return None
        except (OSError, RuntimeError, ValueError):
            return None
        return resolved if resolved.is_file() else None

    @staticmethod
    def _path_identity(path: Path) -> PathIdentity:
        return registry_path_identity(path)

    def _execution_entrypoint(
        self,
        descriptor: CapabilityDescriptor,
        entrypoint: Path,
        temp_dir: Path,
    ) -> Path:
        if not descriptor.dependency_identities:
            return entrypoint
        if descriptor.plugin_root is None or descriptor.entrypoint_identity is None:
            raise ValueError("snapshot source identity is unavailable")
        root = descriptor.plugin_root.resolve(strict=True)
        snapshot_root = temp_dir / "capability"
        snapshot_root.mkdir(mode=0o700)
        snapshot_directories = {snapshot_root}
        sources = (
            (entrypoint, descriptor.entrypoint_identity),
            *descriptor.dependency_identities,
        )
        destinations: dict[Path, Path] = {}
        for source, expected_identity in sources:
            resolved = source.resolve(strict=True)
            relative = resolved.relative_to(root)
            if resolved != source or relative in destinations:
                raise ValueError("snapshot source path is unsafe")
            destination = snapshot_root / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            current = destination.parent
            while current != snapshot_root:
                snapshot_directories.add(current)
                current = current.parent
            self._write_private_source(
                destination,
                self._read_verified_source(resolved, expected_identity),
            )
            destinations[relative] = destination
        for directory in sorted(
            snapshot_directories,
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            os.chmod(directory, 0o500)
        entrypoint_relative = entrypoint.relative_to(root)
        return destinations[entrypoint_relative]

    @staticmethod
    def _read_verified_source(path: Path, expected: PathIdentity) -> bytes:
        if expected[3] > _MAX_EXECUTION_SOURCE_BYTES:
            raise ValueError("snapshot source is too large")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            metadata = (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
            )
            if (
                metadata != expected[:5]
                or not stat.S_ISREG(before.st_mode)
                or before.st_size > _MAX_EXECUTION_SOURCE_BYTES
            ):
                raise ValueError("snapshot source identity changed")
            chunks: list[bytes] = []
            remaining = _MAX_EXECUTION_SOURCE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            value = b"".join(chunks)
            after = os.fstat(descriptor)
            if (
                len(value) != expected[3]
                or (
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                    after.st_size,
                    after.st_mtime_ns,
                )
                != expected[:5]
                or hashlib.sha256(value).hexdigest() != expected[5]
            ):
                raise ValueError("snapshot source identity changed")
            return value
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_private_source(path: Path, value: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o400)
        try:
            view = memoryview(value)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("snapshot source write failed")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _uses_database_environment_transport(
        descriptor: CapabilityDescriptor,
        entrypoint: Path,
    ) -> bool:
        if (
            descriptor.plugin != "his-engineering"
            or descriptor.name != "database.inspect"
            or descriptor.provider != "postgresql"
            or descriptor.contract_version != "pg-evidence.v2"
            or descriptor.mutation_level is not MutationLevel.L1
            or descriptor.credential_class != "database_readonly"
            or descriptor.enabled is not True
            or descriptor.disabled_reason
            or descriptor.scopes != _DATABASE_READONLY_SCOPES
            or descriptor.plugin_root is None
            or descriptor.declared_entrypoint is None
            or descriptor.plugin_root_identity is None
            or descriptor.entrypoint_identity is None
            or len(descriptor.dependency_identities) != 1
        ):
            return False
        try:
            root = descriptor.plugin_root.resolve(strict=True)
            declared = root / "scripts" / "database_read.py"
            expected_entrypoint = declared.resolve(strict=True)
            declared_dependency = root / "scripts" / "pg_evidence.py"
            expected_dependency = declared_dependency.resolve(strict=True)
            dependency, dependency_identity = descriptor.dependency_identities[0]
            return (
                descriptor.plugin_root == root
                and descriptor.declared_entrypoint == declared
                and descriptor.entrypoint == expected_entrypoint
                and entrypoint == expected_entrypoint
                and declared_dependency == expected_dependency
                and dependency == expected_dependency
                and registry_path_identity(root) == descriptor.plugin_root_identity
                and registry_path_identity(expected_entrypoint)
                == descriptor.entrypoint_identity
                and dependency.resolve(strict=True) == dependency
                and dependency.is_file()
                and registry_path_identity(dependency) == dependency_identity
            )
        except (OSError, RuntimeError, ValueError):
            return False

    def _environment(
        self, supplied: Mapping[str, str] | None
    ) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...]]:
        environment = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
        }
        actual_keys: list[str] = []
        actual_values: list[str] = []
        for key in sorted(self._environment_allowlist):
            if key == "TMPDIR":
                continue
            if supplied is None:
                continue
            try:
                value = supplied[key]
            except KeyError:
                continue
            except Exception as exc:
                raise ValueError("environment mapping 无法读取。") from exc
            if isinstance(value, str):
                environment[key] = value
                actual_keys.append(key)
                if value:
                    actual_values.append(value)
        return environment, tuple(actual_keys), tuple(actual_values)

    @staticmethod
    def _contains_injected_environment_value(value: object, values: tuple[str, ...]) -> bool:
        if isinstance(value, str):
            return any(secret in value for secret in values)
        if isinstance(value, dict):
            return any(
                CapabilityRuntime._contains_injected_environment_value(item, values)
                for pair in value.items() for item in pair
            )
        if isinstance(value, (list, tuple)):
            return any(CapabilityRuntime._contains_injected_environment_value(item, values) for item in value)
        return False

    def _validate_identity(
        self, descriptor: CapabilityDescriptor, request: CapabilityRequest, result: CapabilityResult
    ) -> None:
        if (result.request_id, result.capability, result.provider, result.mutation_level) != (
            request.request_id, descriptor.name, descriptor.provider, descriptor.mutation_level,
        ):
            raise CapabilityContractError("结果身份与 capability 声明不一致。")

    def _execution(
        self,
        descriptor: CapabilityDescriptor,
        permission: PermissionDecision,
        request: CapabilityRequest,
        status: str,
        error_code: str,
        started: float,
        environment_keys: tuple[str, ...] = (),
    ) -> CapabilityExecution:
        result = CapabilityResult(
            request_id=request.request_id,
            capability=request.capability,
            provider=request.provider,
            status=status,
            mutation_level=request.mutation_level,
            changed=False,
            summary=error_code,
            data={},
            evidence=(),
            warnings=(),
            blockers=(error_code,) if status == "blocked" else (),
            audit={"error_code": error_code, "environment_keys": list(environment_keys)},
        )
        return CapabilityExecution(descriptor, permission, result, self._duration_ms(started))

    def _with_environment_audit(self, result: CapabilityResult, keys: tuple[str, ...]) -> CapabilityResult:
        audit = {
            "provider": dict(result.audit),
            "runtime": {"environment_keys": list(keys)},
        }
        return CapabilityResult(
            request_id=result.request_id, capability=result.capability, provider=result.provider,
            status=result.status, mutation_level=result.mutation_level, changed=result.changed,
            summary=result.summary, data=result.data, evidence=result.evidence,
            warnings=result.warnings, blockers=result.blockers,
            audit=audit,
        )

    @staticmethod
    def _valid_timeout(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("timeout_seconds 必须为正整数。")
        return value

    @staticmethod
    def _duration_ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)
