from __future__ import annotations

import json
import hashlib
import importlib
import os
import re
import time
from datetime import date, datetime, time as datetime_time
from decimal import Decimal
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import UUID


PG_POLICY_SCHEMA_VERSION = "1.0-pg-evidence-profiles"
ALLOWED_ENVIRONMENTS = frozenset({"test", "development"})
DEFAULT_SENSITIVE_COLUMN_PATTERNS = (
    "name",
    "phone",
    "mobile",
    "idcard",
    "identity",
    "address",
    "patient",
)
PROFILE_DSN_PATTERN = re.compile(r"^pg_([a-z0-9_]+)_readonly_dsn$")
NAMED_PARAMETER_PATTERN = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s")
FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b("
    r"insert|update|delete|merge|create|alter|drop|truncate|grant|revoke|copy|call|do|"
    r"set|begin|commit|rollback|lock|vacuum|analyze|refresh|cluster|reindex|listen|notify|unlisten"
    r")\b|\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b",
    re.IGNORECASE,
)
TABLE_REFERENCE_PATTERN = re.compile(
    r"\bfrom\s+(?:\"?([A-Za-z_][A-Za-z0-9_$]*)\"?\.)?\"?([A-Za-z_][A-Za-z0-9_$]*)\"?",
    re.IGNORECASE,
)
SQL_IDENTIFIER = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)'
SQL_FUNCTION_PATTERN = re.compile(
    rf'(?<![A-Za-z0-9_$"])'
    rf"({SQL_IDENTIFIER}(?:\s*\.\s*{SQL_IDENTIFIER})*)\s*\(",
    re.IGNORECASE,
)
NON_FUNCTION_PAREN_KEYWORDS = frozenset(
    {"and", "as", "exists", "from", "having", "in", "not", "on", "or", "where"}
)
SIMPLE_PROJECTION_PATTERN = re.compile(
    rf"^(?:\*|{SQL_IDENTIFIER}(?:\.{SQL_IDENTIFIER})*(?:\.\*)?)$"
)
SOURCE_EXTENSIONS = frozenset({".java", ".xml", ".sql", ".yml", ".yaml", ".properties"})
MAX_SOURCE_FILES = 200
MAX_SOURCE_FILE_BYTES = 256 * 1024
MAX_SOURCE_TOTAL_BYTES = 2 * 1024 * 1024
CANDIDATE_READY_SCORE = 70
IGNORED_SOURCE_DIRECTORIES = frozenset(
    {".git", ".gradle", ".idea", "__pycache__", "build", "dist", "node_modules", "target"}
)


@dataclass(frozen=True)
class PgProfile:
    name: str
    dsn_configured: bool
    user_configured: bool
    password_configured: bool
    credential_prefix: str

    @property
    def credentials_complete(self) -> bool:
        return self.dsn_configured and self.user_configured and self.password_configured

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dsn_configured": self.dsn_configured,
            "user_configured": self.user_configured,
            "password_configured": self.password_configured,
            "credentials_complete": self.credentials_complete,
            "credential_prefix": self.credential_prefix,
        }


@dataclass(frozen=True)
class PgProfilePolicy:
    name: str
    environment: str
    enabled: bool
    max_rows: int
    connect_timeout_seconds: int
    query_timeout_seconds: int
    total_timeout_seconds: int
    max_metadata_queries: int
    sensitive_column_patterns: tuple[str, ...]
    blockers: tuple[str, ...] = ()
    schemas: tuple[str, ...] = ()

    @property
    def executable(self) -> bool:
        return self.enabled and self.environment in ALLOWED_ENVIRONMENTS and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["executable"] = self.executable
        return payload


@dataclass(frozen=True)
class PgEvidencePolicy:
    schema_version: str
    default_mode: str
    profiles: dict[str, PgProfilePolicy]
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class SqlGuardResult:
    status: str
    blockers: tuple[str, ...] = ()
    parameter_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class PgEvidenceRequest:
    subject: str
    keywords: tuple[str, ...] = ()
    sql: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PgEvidenceCandidate:
    profile: str
    schema: str
    table: str
    score: int
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PgEvidencePlan:
    status: str
    request: PgEvidenceRequest
    guard: SqlGuardResult
    selected_profile: str = ""
    selected_table: str = ""
    candidates: tuple[PgEvidenceCandidate, ...] = ()
    metadata_queries_remaining: int = 0
    blockers: tuple[str, ...] = ()
    query_template_id: str = ""
    max_rows: int = 0
    connect_timeout_seconds: int = 0
    query_timeout_seconds: int = 0
    total_timeout_seconds: int = 0
    sensitive_column_patterns: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "subject": self.request.subject,
            "keywords": list(self.request.keywords),
            "guard": asdict(self.guard),
            "selected_profile": self.selected_profile,
            "selected_table": self.selected_table,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "metadata_queries_remaining": self.metadata_queries_remaining,
            "blockers": list(self.blockers),
            "query_template_id": self.query_template_id,
            "parameter_names": list(self.guard.parameter_names),
        }


@dataclass(frozen=True)
class PgEvidenceResult:
    status: str
    profile: str = ""
    table: str = ""
    rows: tuple[dict[str, Any], ...] = ()
    row_count: int = 0
    masked_columns: tuple[str, ...] = ()
    parameter_audit: tuple[dict[str, str], ...] = ()
    duration_ms: int = 0
    blockers: tuple[str, ...] = ()
    query_template_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PgEvidenceRun:
    status: str
    mode: str
    plan: PgEvidencePlan
    result: PgEvidenceResult
    audit: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "plan": self.plan.to_dict(),
            "result": self.result.to_dict(),
            "audit": dict(self.audit),
        }


class PostgresExecutor(Protocol):
    def discover_metadata(self, **kwargs: Any) -> list[dict[str, str]]: ...

    def execute_select(self, **kwargs: Any) -> list[dict[str, Any]]: ...


class PsycopgPostgresExecutor:
    def __init__(
        self,
        *,
        dsn: str,
        user: str,
        password: str,
        connect_timeout_seconds: int,
        query_timeout_seconds: int,
    ) -> None:
        try:
            self._psycopg = importlib.import_module("psycopg")
            rows_module = importlib.import_module("psycopg.rows")
        except (ImportError, ModuleNotFoundError) as exc:
            raise RuntimeError("缺少可选 PostgreSQL 驱动 psycopg") from exc
        self._dict_row = rows_module.dict_row
        self._dsn = normalize_postgres_dsn(dsn)
        self._user = user
        self._password = password
        self._connect_timeout_seconds = connect_timeout_seconds
        self._query_timeout_seconds = query_timeout_seconds

    def discover_metadata(self, **kwargs: Any) -> list[dict[str, str]]:
        table = str(kwargs.get("table") or "")
        sql = (
            "SELECT table_schema AS schema, table_name AS table "
            "FROM information_schema.tables "
            "WHERE table_name = %(table)s "
            "AND table_type IN ('BASE TABLE', 'VIEW') "
            "ORDER BY table_schema, table_name LIMIT 4"
        )
        return [dict(row) for row in self._query(sql, {"table": table}, max_rows=4)]

    def execute_select(self, **kwargs: Any) -> list[dict[str, Any]]:
        sql = str(kwargs.get("sql") or "")
        parameters = kwargs.get("parameters")
        max_rows = int(kwargs.get("max_rows") or 1)
        return [dict(row) for row in self._query(sql, parameters or {}, max_rows=max_rows)]

    def _query(
        self,
        sql: str,
        parameters: Mapping[str, Any],
        *,
        max_rows: int,
    ) -> list[Mapping[str, Any]]:
        options = (
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={self._query_timeout_seconds * 1000}"
        )
        connect_kwargs = build_postgres_connect_kwargs(self._dsn)
        dsn = connect_kwargs.pop("dsn", None)
        connection_options = {
            "user": self._user,
            "password": self._password,
            "connect_timeout": self._connect_timeout_seconds,
            "options": options,
            "row_factory": self._dict_row,
        }
        if dsn is not None:
            connection = self._psycopg.connect(
                dsn,
                **connection_options,
            )
        else:
            connection = self._psycopg.connect(
                **connect_kwargs,
                **connection_options,
            )
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                return list(cursor.fetchmany(max_rows))
        finally:
            connection.close()


def normalize_postgres_dsn(value: str) -> str:
    """Accept the JDBC URL form commonly stored by the HIS configuration."""
    dsn = value.strip()
    prefix = "jdbc:postgresql://"
    if dsn.lower().startswith(prefix):
        return "postgresql://" + dsn[len(prefix):]
    return dsn


def build_postgres_connect_kwargs(dsn: str) -> dict[str, Any]:
    """Convert a simple PostgreSQL URL into explicit libpq keyword arguments."""
    parts = urlsplit(normalize_postgres_dsn(dsn))
    if parts.scheme not in {"postgresql", "postgres"} or not parts.hostname:
        return {"dsn": normalize_postgres_dsn(dsn)}
    kwargs: dict[str, Any] = {
        "host": parts.hostname,
        "dbname": unquote(parts.path.lstrip("/")),
    }
    if parts.port is not None:
        kwargs["port"] = parts.port
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key in {"sslmode", "target_session_attrs", "gssencmode", "channel_binding"}:
            kwargs[key] = value
    return kwargs


def discover_pg_profiles(credentials: Mapping[str, Any]) -> list[PgProfile]:
    profiles: list[PgProfile] = []
    for key in sorted(str(item) for item in credentials):
        match = PROFILE_DSN_PATTERN.fullmatch(key)
        if not match:
            continue
        name = match.group(1)
        prefix = f"pg_{name}_readonly"
        profiles.append(
            PgProfile(
                name=name,
                dsn_configured=has_value(credentials.get(f"{prefix}_dsn")),
                user_configured=has_value(credentials.get(f"{prefix}_user")),
                password_configured=has_value(credentials.get(f"{prefix}_password")),
                credential_prefix=prefix,
            )
        )
    return profiles


def build_psycopg_executor_factory(credentials: Mapping[str, Any]) -> Any:
    def factory(*, plan: PgEvidencePlan) -> PsycopgPostgresExecutor:
        prefix = f"pg_{plan.selected_profile}_readonly"
        dsn = credentials.get(f"{prefix}_dsn")
        user = credentials.get(f"{prefix}_user")
        password = credentials.get(f"{prefix}_password")
        if not all(has_value(value) for value in (dsn, user, password)):
            raise RuntimeError("所选 Profile 的只读凭证不完整")
        return PsycopgPostgresExecutor(
            dsn=str(dsn),
            user=str(user),
            password=str(password),
            connect_timeout_seconds=plan.connect_timeout_seconds,
            query_timeout_seconds=plan.query_timeout_seconds,
        )

    return factory


def load_pg_policy(path: str | Path) -> PgEvidencePolicy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("PG 证据策略根节点必须是 JSON 对象。")
    blockers: list[str] = []
    if payload.get("schema_version") != PG_POLICY_SCHEMA_VERSION:
        blockers.append(f"schema_version 必须为 {PG_POLICY_SCHEMA_VERSION}。")
    default_mode = str(payload.get("default_mode") or "")
    if default_mode != "off":
        blockers.append("default_mode 必须为 off，普通需求不得隐式连接数据库。")
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, dict):
        raise ValueError("profiles 必须是对象。")
    profiles = {
        str(name): build_profile_policy(name=str(name), payload=item)
        for name, item in raw_profiles.items()
    }
    return PgEvidencePolicy(
        schema_version=PG_POLICY_SCHEMA_VERSION,
        default_mode=default_mode,
        profiles=profiles,
        blockers=tuple(blockers),
    )


def build_profile_policy(*, name: str, payload: Any) -> PgProfilePolicy:
    if not isinstance(payload, dict):
        return PgProfilePolicy(
            name=name,
            environment="",
            enabled=False,
            max_rows=0,
            connect_timeout_seconds=0,
            query_timeout_seconds=0,
            total_timeout_seconds=0,
            max_metadata_queries=0,
            sensitive_column_patterns=(),
            blockers=("Profile 策略必须是对象。",),
        )
    environment = str(payload.get("environment") or "").strip().lower()
    enabled = payload.get("enabled") is True
    blockers: list[str] = []
    if environment not in ALLOWED_ENVIRONMENTS:
        blockers.append("environment 必须为 test 或 development。")
    if not enabled:
        blockers.append("enabled 必须显式为 true。")
    limits = {
        "max_rows": positive_integer(payload.get("max_rows"), 50, "max_rows", blockers),
        "connect_timeout_seconds": positive_integer(
            payload.get("connect_timeout_seconds"), 5, "connect_timeout_seconds", blockers
        ),
        "query_timeout_seconds": positive_integer(
            payload.get("query_timeout_seconds"), 10, "query_timeout_seconds", blockers
        ),
        "total_timeout_seconds": positive_integer(
            payload.get("total_timeout_seconds"), 45, "total_timeout_seconds", blockers
        ),
        "max_metadata_queries": positive_integer(
            payload.get("max_metadata_queries"), 3, "max_metadata_queries", blockers
        ),
    }
    if limits["max_rows"] > 50:
        blockers.append("max_rows 不能大于 50。")
    if limits["connect_timeout_seconds"] > 5:
        blockers.append("connect_timeout_seconds 不能大于 5。")
    if limits["query_timeout_seconds"] > 10:
        blockers.append("query_timeout_seconds 不能大于 10。")
    if limits["total_timeout_seconds"] > 45:
        blockers.append("total_timeout_seconds 不能大于 45。")
    if limits["max_metadata_queries"] > 3:
        blockers.append("max_metadata_queries 不能大于 3。")
    patterns = tuple(
        dict.fromkeys(
            (
                *DEFAULT_SENSITIVE_COLUMN_PATTERNS,
                *normalize_patterns(payload.get("sensitive_column_patterns")),
            )
        )
    )
    schemas = normalize_schema_aliases(payload.get("schemas"), name, blockers)
    return PgProfilePolicy(
        name=name,
        environment=environment,
        enabled=enabled,
        sensitive_column_patterns=patterns,
        blockers=tuple(blockers),
        schemas=schemas,
        **limits,
    )


def normalize_schema_aliases(
    value: Any,
    profile_name: str,
    blockers: list[str],
) -> tuple[str, ...]:
    """Bind physical PostgreSQL schemas to a logical readonly credential profile."""
    raw = [profile_name] if value is None else value
    if not isinstance(raw, list) or not raw:
        blockers.append("schemas 必须为非空数组。")
        return (profile_name,)
    aliases: list[str] = []
    for item in raw:
        alias = str(item).strip().lower() if isinstance(item, str) else ""
        # ``*`` delegates schema authorization to PostgreSQL for this
        # credential.  The SQL guard and readonly row/timeout limits still
        # apply; the Harness must not become a second, stale privilege model.
        if alias == "*":
            if aliases:
                blockers.append("schemas 使用 * 时不能同时配置其他 schema。")
                continue
            return ("*",)
        if not re.fullmatch(r"[a-z_][a-z0-9_$]*", alias):
            blockers.append("schemas 只能包含合法 PostgreSQL schema 标识符。")
            continue
        if alias not in aliases:
            aliases.append(alias)
    return tuple(aliases) or (profile_name,)


def validate_readonly_sql(sql: str, parameters: Mapping[str, Any]) -> SqlGuardResult:
    normalized = strip_sql_literals_and_comments(sql).strip()
    blockers: list[str] = []
    if not normalized:
        blockers.append("SQL 不能为空。")
    elif normalized.count(";") > 1 or (";" in normalized and not normalized.endswith(";")):
        blockers.append("只允许单条 SQL 语句。")
    statement = normalized[:-1].strip() if normalized.endswith(";") else normalized
    if statement and not (statement.lower().startswith("select") or statement.lower().startswith("with")):
        blockers.append("只允许顶层 SELECT 或只读 WITH 查询。")
    if FORBIDDEN_SQL_PATTERN.search(statement):
        blockers.append("SQL 包含禁止的写入、事务或锁定关键字。")
    if contains_sql_function(statement):
        blockers.append("SQL 不允许调用函数或表函数。")
    if not has_only_simple_select_projections(statement):
        blockers.append("SELECT 投影只允许普通列引用或 *，不允许别名或表达式。")
    parameter_names = tuple(dict.fromkeys(NAMED_PARAMETER_PATTERN.findall(sql)))
    missing = [name for name in parameter_names if name not in parameters]
    if missing:
        blockers.append("缺少命名参数：" + ", ".join(missing) + "。")
    return SqlGuardResult(
        status="blocked" if blockers else "pass",
        blockers=tuple(blockers),
        parameter_names=parameter_names,
    )


def build_pg_evidence_plan(
    request: PgEvidenceRequest,
    policy: PgEvidencePolicy,
    profiles: Sequence[PgProfile],
    project_root: Path,
) -> PgEvidencePlan:
    guard = validate_readonly_sql(request.sql, request.parameters)
    query_template_id = build_query_template_id(request.sql)
    blockers = list(policy.blockers)
    if guard.status != "pass":
        blockers.extend(guard.blockers)

    configured_profiles = {profile.name: profile for profile in profiles}
    eligible_policies = {
        name: profile_policy
        for name, profile_policy in policy.profiles.items()
        if profile_policy.executable
        and name in configured_profiles
        and configured_profiles[name].credentials_complete
    }
    if not eligible_policies:
        blockers.append("没有同时满足策略与只读凭证完整性要求的 PG Profile。")

    schema, table = extract_table_reference(request.sql)
    if not table:
        blockers.append("未能从 SQL 中识别查询表。")
    if blockers:
        return PgEvidencePlan(
            status="blocked",
            request=request,
            guard=guard,
            blockers=tuple(dict.fromkeys(blockers)),
            query_template_id=query_template_id,
        )

    source_evidence = scan_source_evidence(project_root, tuple(eligible_policies), table)
    candidates = score_pg_candidates(
        request=request,
        schema=schema,
        table=table,
        eligible_profiles=tuple(eligible_policies),
        source_evidence=source_evidence,
        profile_policies=eligible_policies,
    )

    if schema:
        matching = [candidate for candidate in candidates if candidate.schema == schema]
        if len(matching) != 1:
            return PgEvidencePlan(
                status="needs_evidence",
                request=request,
                guard=guard,
                candidates=tuple(candidates),
                blockers=(f"SQL 指定的 schema {schema} 未对应唯一可执行 Profile。",),
                query_template_id=query_template_id,
            )
        return plan_for_candidate(
            status="ready",
            request=request,
            guard=guard,
            candidate=matching[0],
            candidates=candidates,
            profile_policy=eligible_policies[matching[0].profile],
            selected_table=f"{schema}.{table}",
            metadata_queries_remaining=0,
            query_template_id=query_template_id,
        )

    strong_candidates = [candidate for candidate in candidates if candidate.score >= CANDIDATE_READY_SCORE]
    if strong_candidates and (
        len(strong_candidates) == 1
        or strong_candidates[0].score > strong_candidates[1].score
    ):
        selected = strong_candidates[0]
        return plan_for_candidate(
            status="ready",
            request=request,
            guard=guard,
            candidate=selected,
            candidates=candidates,
            profile_policy=eligible_policies[selected.profile],
            selected_table=f"{selected.schema}.{table}",
            metadata_queries_remaining=0,
            query_template_id=query_template_id,
        )

    if len(eligible_policies) == 1:
        selected_name, selected_policy = next(iter(eligible_policies.items()))
        selected = next(candidate for candidate in candidates if candidate.profile == selected_name)
        return plan_for_candidate(
            status="metadata_required",
            request=request,
            guard=guard,
            candidate=selected,
            candidates=candidates,
            profile_policy=selected_policy,
            selected_table=table,
            metadata_queries_remaining=min(1, selected_policy.max_metadata_queries),
            query_template_id=query_template_id,
        )

    return PgEvidencePlan(
        status="needs_evidence",
        request=request,
        guard=guard,
        candidates=tuple(candidates),
        blockers=("候选数据库不唯一；为避免盲查，未执行元数据或业务查询。",),
        query_template_id=query_template_id,
    )


def execute_pg_evidence_plan(
    plan: PgEvidencePlan,
    executor: PostgresExecutor,
) -> PgEvidenceResult:
    started_at = time.monotonic()
    if plan.status not in {"ready", "metadata_required"}:
        return build_nonexecuted_result(plan, started_at)

    selected_table = plan.selected_table
    query_sql = plan.request.sql
    try:
        if plan.status == "metadata_required":
            if plan.metadata_queries_remaining <= 0:
                return build_nonexecuted_result(
                    plan,
                    started_at,
                    status="needs_evidence",
                    blockers=("元数据查询预算为 0，未执行数据库查询。",),
                )
            metadata = executor.discover_metadata(
                profile=plan.selected_profile,
                table=plan.selected_table,
                max_queries=plan.metadata_queries_remaining,
                connect_timeout_seconds=plan.connect_timeout_seconds,
                query_timeout_seconds=plan.query_timeout_seconds,
            )
            matches = unique_metadata_matches(metadata, plan.selected_table)
            if len(matches) != 1:
                return build_nonexecuted_result(
                    plan,
                    started_at,
                    status="needs_evidence",
                    blockers=("元数据未识别出唯一 schema/table，未执行业务查询。",),
                )
            selected_table = f"{matches[0][0]}.{matches[0][1]}"
            query_sql = qualify_unqualified_table(query_sql, selected_table)

        rows = executor.execute_select(
            profile=plan.selected_profile,
            sql=query_sql,
            parameters=plan.request.parameters,
            max_rows=plan.max_rows,
            connect_timeout_seconds=plan.connect_timeout_seconds,
            query_timeout_seconds=plan.query_timeout_seconds,
            total_timeout_seconds=plan.total_timeout_seconds,
        )
    except Exception as exc:
        error_status = "timeout" if is_timeout_error(exc) else "failed"
        return build_nonexecuted_result(
            plan,
            started_at,
            status=error_status,
            blockers=(safe_error_summary(exc, status=error_status),),
            table=selected_table,
        )

    limited_rows = rows[: plan.max_rows]
    masked_rows, masked_columns = mask_sensitive_rows(
        limited_rows,
        plan.sensitive_column_patterns,
    )
    return PgEvidenceResult(
        status="passed",
        profile=plan.selected_profile,
        table=selected_table,
        rows=tuple(masked_rows),
        row_count=len(masked_rows),
        masked_columns=masked_columns,
        parameter_audit=build_parameter_audit(plan.request.parameters),
        duration_ms=elapsed_milliseconds(started_at),
        query_template_id=plan.query_template_id,
    )


def run_pg_evidence(
    *,
    request: PgEvidenceRequest,
    policy: PgEvidencePolicy,
    profiles: Sequence[PgProfile],
    project_root: Path,
    mode: str = "plan",
    executor_factory: Any = None,
) -> PgEvidenceRun:
    if mode not in {"plan", "execute"}:
        raise ValueError("mode 必须为 plan 或 execute。")
    plan = build_pg_evidence_plan(request, policy, profiles, project_root)
    if mode == "plan":
        result = PgEvidenceResult(
            status="not_executed",
            profile=plan.selected_profile,
            table=plan.selected_table,
            parameter_audit=build_parameter_audit(request.parameters),
            blockers=("plan 模式不创建数据库执行器，也不建立数据库连接。",),
            query_template_id=plan.query_template_id,
        )
        return PgEvidenceRun(
            status="planned",
            mode=mode,
            plan=plan,
            result=result,
            audit=build_run_audit(mode=mode, plan=plan, result=result, executor_created=False),
        )

    if plan.status not in {"ready", "metadata_required"}:
        result = build_nonexecuted_result(plan, time.monotonic())
        return PgEvidenceRun(
            status=result.status,
            mode=mode,
            plan=plan,
            result=result,
            audit=build_run_audit(mode=mode, plan=plan, result=result, executor_created=False),
        )
    if executor_factory is None:
        result = PgEvidenceResult(
            status="blocked",
            profile=plan.selected_profile,
            table=plan.selected_table,
            parameter_audit=build_parameter_audit(request.parameters),
            blockers=("未配置 PostgreSQL 执行器。",),
            query_template_id=plan.query_template_id,
        )
        return PgEvidenceRun(
            status="blocked",
            mode=mode,
            plan=plan,
            result=result,
            audit=build_run_audit(mode=mode, plan=plan, result=result, executor_created=False),
        )

    try:
        executor = executor_factory(plan=plan)
    except Exception as exc:
        result = PgEvidenceResult(
            status="blocked",
            profile=plan.selected_profile,
            table=plan.selected_table,
            parameter_audit=build_parameter_audit(request.parameters),
            blockers=(safe_executor_factory_error(exc),),
            query_template_id=plan.query_template_id,
        )
        return PgEvidenceRun(
            status="blocked",
            mode=mode,
            plan=plan,
            result=result,
            audit=build_run_audit(mode=mode, plan=plan, result=result, executor_created=False),
        )

    result = execute_pg_evidence_plan(plan, executor)
    return PgEvidenceRun(
        status=result.status,
        mode=mode,
        plan=plan,
        result=result,
        audit=build_run_audit(mode=mode, plan=plan, result=result, executor_created=True),
    )


def render_pg_evidence_outputs(run: PgEvidenceRun) -> str:
    return json.dumps(run.to_dict(), ensure_ascii=False, indent=2)


def write_pg_evidence_outputs(output_dir: str | Path, run: PgEvidenceRun) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    payloads = {
        "pg_evidence_plan.json": run.plan.to_dict(),
        "pg_evidence_result.json": run.result.to_dict(),
        "pg_evidence_audit.json": dict(run.audit),
    }
    for filename, payload in payloads.items():
        (target / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    (target / "pg_evidence_plan.md").write_text(
        pg_evidence_plan_to_markdown(run.plan),
        encoding="utf-8",
    )
    (target / "pg_evidence_result.md").write_text(
        pg_evidence_result_to_markdown(run.result),
        encoding="utf-8",
    )
    return {name: str(target / name) for name in (*payloads, "pg_evidence_plan.md", "pg_evidence_result.md")}


def pg_evidence_plan_to_markdown(plan: PgEvidencePlan) -> str:
    blockers = "\n".join(f"- {item}" for item in plan.blockers) or "- 无"
    candidates = "\n".join(
        f"- `{item.profile}` / `{item.schema}.{item.table}` / score={item.score} / evidence={','.join(item.evidence) or '-'}"
        for item in plan.candidates
    ) or "- 无"
    return (
        "# PostgreSQL 数据证据计划\n\n"
        f"- 状态：`{plan.status}`\n"
        f"- 主题：{plan.request.subject}\n"
        f"- Profile：`{plan.selected_profile or '-'}`\n"
        f"- 表：`{plan.selected_table or '-'}`\n"
        f"- 查询模板：`{plan.query_template_id}`\n"
        f"- 剩余元数据查询预算：{plan.metadata_queries_remaining}\n\n"
        "## 候选\n\n"
        f"{candidates}\n\n"
        "## 阻断项\n\n"
        f"{blockers}\n"
    )


def pg_evidence_result_to_markdown(result: PgEvidenceResult) -> str:
    blockers = "\n".join(f"- {item}" for item in result.blockers) or "- 无"
    return (
        "# PostgreSQL 数据证据结果\n\n"
        f"- 状态：`{result.status}`\n"
        f"- Profile：`{result.profile or '-'}`\n"
        f"- 表：`{result.table or '-'}`\n"
        f"- 行数：{result.row_count}\n"
        f"- 脱敏列：{', '.join(result.masked_columns) or '-'}\n"
        f"- 耗时：{result.duration_ms} ms\n\n"
        "## 阻断项\n\n"
        f"{blockers}\n"
    )


def build_run_audit(
    *,
    mode: str,
    plan: PgEvidencePlan,
    result: PgEvidenceResult,
    executor_created: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0-pg-evidence-audit",
        "mode": mode,
        "plan_status": plan.status,
        "result_status": result.status,
        "profile": plan.selected_profile,
        "table": result.table or plan.selected_table,
        "query_template_id": plan.query_template_id,
        "parameter_audit": list(result.parameter_audit),
        "executor_created": executor_created,
        "retry_count": 0,
        "readonly": True,
    }


def extract_table_reference(sql: str) -> tuple[str, str]:
    match = TABLE_REFERENCE_PATTERN.search(strip_sql_literals_and_comments(sql))
    if not match:
        return "", ""
    return (match.group(1) or "").lower(), match.group(2).lower()


def qualify_unqualified_table(sql: str, qualified_table: str) -> str:
    """Qualify the single unqualified FROM table selected by metadata evidence."""
    schema, table = qualified_table.split(".", 1)
    original_schema, original_table = extract_table_reference(sql)
    if original_schema or original_table != table.lower():
        return sql

    pattern = re.compile(
        r"(\bfrom\s+)(\"?[A-Za-z_][A-Za-z0-9_$]*\"?)",
        re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        token = match.group(2).strip('"').lower()
        if token != table.lower():
            return match.group(0)
        quoted_schema = '"' + schema.replace('"', '""') + '"'
        quoted_table = '"' + table.replace('"', '""') + '"'
        return f"{match.group(1)}{quoted_schema}.{quoted_table}"

    return pattern.sub(replace, sql, count=1)


def scan_source_evidence(
    project_root: Path,
    profile_names: tuple[str, ...],
    table: str,
) -> dict[str, tuple[str, ...]]:
    evidence: dict[str, list[str]] = {name: [] for name in profile_names}
    if not project_root.is_dir():
        return {name: () for name in profile_names}

    total_bytes = 0
    file_count = 0
    for path in iter_source_files(project_root):
        if file_count >= MAX_SOURCE_FILES or total_bytes >= MAX_SOURCE_TOTAL_BYTES:
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_SOURCE_FILE_BYTES or total_bytes + size > MAX_SOURCE_TOTAL_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        file_count += 1
        total_bytes += size
        if table not in text:
            continue
        for profile_name in profile_names:
            if profile_name.lower() in text:
                evidence[profile_name].extend(("source_schema_match", "source_table_match"))
    return {name: tuple(dict.fromkeys(items)) for name, items in evidence.items()}


def iter_source_files(project_root: Path):
    for current_root, directories, filenames in os.walk(project_root):
        directories[:] = sorted(
            name for name in directories if name not in IGNORED_SOURCE_DIRECTORIES
        )
        for filename in sorted(filenames):
            path = Path(current_root) / filename
            if path.suffix.lower() in SOURCE_EXTENSIONS:
                yield path


def score_pg_candidates(
    *,
    request: PgEvidenceRequest,
    schema: str,
    table: str,
    eligible_profiles: tuple[str, ...],
    source_evidence: Mapping[str, tuple[str, ...]],
    profile_policies: Mapping[str, PgProfilePolicy] | None = None,
) -> list[PgEvidenceCandidate]:
    request_text = " ".join((request.subject, *request.keywords)).lower()
    candidates: list[PgEvidenceCandidate] = []
    for profile_name in eligible_profiles:
        policy = profile_policies.get(profile_name) if profile_policies else None
        schema_aliases = (policy.schemas or (profile_name,)) if policy else (profile_name,)
        schema_matches = "*" in schema_aliases or schema in schema_aliases
        selected_schema = schema if schema_matches else schema_aliases[0]
        evidence = list(source_evidence.get(profile_name, ()))
        score = 0
        if schema and schema_matches:
            evidence.extend(("sql_schema_match", "sql_table_match"))
            score += 100
        if "source_schema_match" in evidence and "source_table_match" in evidence:
            score += 80
        if profile_name.lower() in request_text:
            evidence.append("profile_name_match")
            score += 30
        candidates.append(
            PgEvidenceCandidate(
                profile=profile_name,
                schema=selected_schema,
                table=table,
                score=score,
                evidence=tuple(dict.fromkeys(evidence)),
            )
        )
    return sorted(candidates, key=lambda item: (-item.score, item.profile))


def plan_for_candidate(
    *,
    status: str,
    request: PgEvidenceRequest,
    guard: SqlGuardResult,
    candidate: PgEvidenceCandidate,
    candidates: Sequence[PgEvidenceCandidate],
    profile_policy: PgProfilePolicy,
    selected_table: str,
    metadata_queries_remaining: int,
    query_template_id: str,
) -> PgEvidencePlan:
    return PgEvidencePlan(
        status=status,
        request=request,
        guard=guard,
        selected_profile=candidate.profile,
        selected_table=selected_table,
        candidates=tuple(candidates),
        metadata_queries_remaining=metadata_queries_remaining,
        query_template_id=query_template_id,
        max_rows=profile_policy.max_rows,
        connect_timeout_seconds=profile_policy.connect_timeout_seconds,
        query_timeout_seconds=profile_policy.query_timeout_seconds,
        total_timeout_seconds=profile_policy.total_timeout_seconds,
        sensitive_column_patterns=profile_policy.sensitive_column_patterns,
    )


def unique_metadata_matches(
    metadata: Sequence[Mapping[str, Any]],
    table: str,
) -> list[tuple[str, str]]:
    matches = {
        (str(item.get("schema") or "").lower(), str(item.get("table") or "").lower())
        for item in metadata
        if str(item.get("table") or "").lower() == table.lower()
        and str(item.get("schema") or "").strip()
    }
    return sorted(matches)


def mask_sensitive_rows(
    rows: Sequence[Mapping[str, Any]],
    sensitive_column_patterns: Sequence[str],
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    patterns = tuple(pattern.lower() for pattern in sensitive_column_patterns)
    masked_columns: set[str] = set()
    masked_rows: list[dict[str, Any]] = []
    for row in rows:
        masked_row: dict[str, Any] = {}
        for column, value in row.items():
            column_name = str(column)
            if any(pattern in column_name.lower() for pattern in patterns):
                masked_row[column_name] = "[REDACTED]"
                masked_columns.add(column_name)
            else:
                masked_row[column_name] = json_safe_value(value)
        masked_rows.append(masked_row)
    return masked_rows, tuple(sorted(masked_columns))


def json_safe_value(value: Any) -> Any:
    """Keep common PostgreSQL scalar values serializable in the evidence contract."""
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "[BINARY]"
    if isinstance(value, Mapping):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe_value(item) for item in value]
    return value


def build_parameter_audit(parameters: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "name": str(name),
            "type": type(value).__name__,
            "metadata_sha256": hashlib.sha256(
                f"{name}:{type(value).__name__}".encode("utf-8")
            ).hexdigest(),
        }
        for name, value in sorted(parameters.items())
    )


def build_query_template_id(sql: str) -> str:
    normalized = " ".join(strip_sql_literals_and_comments(sql).split()).lower()
    return "pgq_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def build_nonexecuted_result(
    plan: PgEvidencePlan,
    started_at: float,
    *,
    status: str | None = None,
    blockers: tuple[str, ...] | None = None,
    table: str | None = None,
) -> PgEvidenceResult:
    return PgEvidenceResult(
        status=status or plan.status,
        profile=plan.selected_profile,
        table=plan.selected_table if table is None else table,
        parameter_audit=build_parameter_audit(plan.request.parameters),
        duration_ms=elapsed_milliseconds(started_at),
        blockers=plan.blockers if blockers is None else blockers,
        query_template_id=plan.query_template_id,
    )


def safe_error_summary(error: Exception, *, status: str = "failed") -> str:
    if status == "timeout":
        return f"{type(error).__name__}: PG 证据查询超过时间预算；未重试。"
    category, message = classify_pg_error(error)
    return f"{category}: {message}；未重试。"


def classify_pg_error(error: Exception) -> tuple[str, str]:
    """Classify a PostgreSQL failure without returning driver details or secrets."""
    names: set[str] = set()
    sqlstates: set[str] = set()
    messages: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()

    for _ in range(5):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        names.add(type(current).__name__.lower())
        sqlstate = getattr(current, "sqlstate", None)
        if isinstance(sqlstate, str):
            sqlstates.add(sqlstate.upper())
        pgcode = getattr(current, "pgcode", None)
        if isinstance(pgcode, str):
            sqlstates.add(pgcode.upper())
        try:
            messages.append(str(current).lower())
            messages.append(repr(current).lower())
        except Exception:
            pass
        args = getattr(current, "args", ())
        if isinstance(args, tuple):
            messages.extend(item.lower() for item in args if isinstance(item, str))
        diagnostic = getattr(current, "diag", None)
        if diagnostic is not None:
            diagnostic_sqlstate = getattr(diagnostic, "sqlstate", None)
            if isinstance(diagnostic_sqlstate, str):
                sqlstates.add(diagnostic_sqlstate.upper())
            for field_name in ("message_primary", "message_detail", "severity"):
                field_value = getattr(diagnostic, field_name, None)
                if isinstance(field_value, str):
                    messages.append(field_value.lower())
        current = current.__cause__ or current.__context__

    message_text = " ".join(messages)
    if any(state.startswith("28") for state in sqlstates) or any(
        marker in message_text
        for marker in (
            "password authentication failed",
            "authentication failed",
            "no password supplied",
            "authentication method",
        )
    ) or any("password" in name or "auth" in name for name in names):
        return "PG_AUTH_FAILED", "PostgreSQL 认证失败"
    if any(state in {"3D000"} for state in sqlstates) or any(
        marker in names for marker in ("invalidcatalogname", "databasenotfound")
    ) or "database does not exist" in message_text:
        return "PG_DATABASE_NOT_FOUND", "PostgreSQL 数据库不存在"
    if any(state in {"3F000", "42P01"} for state in sqlstates) or any(
        marker in names
        for marker in ("invalidschemaname", "undefinedtable", "undefinedobject")
    ) or "does not exist" in message_text and any(
        marker in message_text for marker in ("schema", "relation", "table")
    ):
        return "PG_SCHEMA_OR_TABLE_NOT_FOUND", "PostgreSQL 模式或表不存在"
    if "42501" in sqlstates or "insufficientprivilege" in names or "permission denied" in message_text:
        return "PG_PERMISSION_DENIED", "PostgreSQL 权限不足"
    if any(
        marker in names
        for marker in (
            "connectionrefusederror",
            "connectionreseterror",
            "gaierror",
        )
    ) or any(
        marker in message_text
        for marker in (
            "connection refused",
            "connection reset",
            "could not translate host name",
            "network is unreachable",
            "connection timed out",
        )
    ):
        return "PG_NETWORK_UNREACHABLE", "PostgreSQL 网络连接失败"
    if "operationalerror" in names:
        return "PG_CONNECTION_FAILED", "PostgreSQL 连接失败"
    return "PG_QUERY_FAILED", "PostgreSQL 查询执行失败"


def is_timeout_error(error: Exception) -> bool:
    if isinstance(error, TimeoutError):
        return True
    error_name = type(error).__name__.lower()
    message = str(error).lower()
    return "timeout" in error_name or "timeout" in message or "statement timeout" in message


def safe_executor_factory_error(error: Exception) -> str:
    message = str(error).lower()
    if "psycopg" in message or "驱动" in message or isinstance(error, (ImportError, ModuleNotFoundError)):
        return "缺少可选 PostgreSQL 驱动 psycopg，未连接数据库。"
    return f"{type(error).__name__}: PostgreSQL 执行器创建失败，未连接数据库。"


def elapsed_milliseconds(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1000))


def strip_sql_literals_and_comments(sql: str) -> str:
    without_block_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    without_line_comments = re.sub(r"--[^\n]*", " ", without_block_comments)
    return re.sub(r"'(?:''|[^'])*'", "''", without_line_comments)


def contains_sql_function(statement: str) -> bool:
    without_parameters = NAMED_PARAMETER_PATTERN.sub("?", statement)
    for match in SQL_FUNCTION_PATTERN.finditer(without_parameters):
        function_name = match.group(1)
        if '"' in function_name:
            return True
        if re.split(r"\s*\.\s*", function_name)[-1].lower() not in NON_FUNCTION_PAREN_KEYWORDS:
            return True
    return False


def has_only_simple_select_projections(statement: str) -> bool:
    for projection in select_projections(statement):
        columns = tuple(item.strip() for item in projection.split(","))
        if not columns or any(
            not item or SIMPLE_PROJECTION_PATTERN.fullmatch(item) is None
            for item in columns
        ):
            return False
    return True


def select_projections(statement: str) -> tuple[str, ...]:
    tokens = tuple(
        re.finditer(r"[A-Za-z_][A-Za-z0-9_$]*|[()]", statement)
    )
    depth = 0
    pending: list[tuple[int, int]] = []
    projections: list[str] = []
    for token in tokens:
        value = token.group(0).lower()
        if value == "(":
            depth += 1
            continue
        if value == ")":
            for index in range(len(pending) - 1, -1, -1):
                select_depth, start = pending[index]
                if select_depth == depth:
                    projections.append(statement[start:token.start()].strip())
                    del pending[index]
                    break
            depth = max(0, depth - 1)
            continue
        if value == "select":
            pending.append((depth, token.end()))
            continue
        if value == "from":
            for index in range(len(pending) - 1, -1, -1):
                select_depth, start = pending[index]
                if select_depth == depth:
                    projections.append(statement[start:token.start()].strip())
                    del pending[index]
                    break
    projections.extend(statement[start:].strip() for _, start in pending)
    return tuple(projections)


def has_value(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def positive_integer(value: Any, default: int, field_name: str, blockers: list[str]) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        blockers.append(f"{field_name} 必须是正整数。")
        return default
    if parsed <= 0:
        blockers.append(f"{field_name} 必须是正整数。")
        return default
    return parsed


def normalize_patterns(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip().lower() for item in value if str(item).strip())
