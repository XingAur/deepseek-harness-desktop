from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from app.code_evidence_artifacts import EvidenceArtifactStore
from app.code_evidence_git import GitDiffEvidenceService
from app.code_evidence_repository import CodeEvidenceRepository
from app.code_evidence_review import CodeEvidenceReviewService
from app.code_evidence_set import CodeEvidenceSetService
from app.code_evidence_source import SourceEvidenceService
from app.code_evidence_history import GitHistoryEvidenceService
from app.code_evidence_verification import CodeEvidenceVerificationService
from app.repository_scope import RepositoryScope
from app.task_intent_service import TaskIntentRoutingResult


_REVIEW = re.compile(
    r"审核|审查|代码正确|多余(?:代码|改动)|改了哪些|具体改了|逐文件|"
    r"\bdiff\b|\bcode review\b|\breview (?:the )?(?:code|changes)\b",
    re.IGNORECASE,
)
_SOURCE = re.compile(
    r"代码|源码|文件|方法|函数|类|接口|调用链|实现在哪|定义在哪|引用|影响路径|"
    r"\bsource\b|\bcall(?:er| graph| chain)?\b|\bimplementation\b",
    re.IGNORECASE,
)
_HISTORY = re.compile(
    r"谁改|何时改|历史|提交|原因|追溯|blame|commit|history",
    re.IGNORECASE,
)
_REQUIREMENT = re.compile(r"需求|缺陷|(?<![A-Za-z])bug(?![A-Za-z])|工作项|工单|业务规则", re.IGNORECASE)
_GITLAB_REMOTE = re.compile(
    r"GitLab|merge request|(?<![A-Za-z])MR\s*[#!]?\d+|远端(?:仓库|分支|提交)",
    re.IGNORECASE,
)
_GITHUB_REMOTE = re.compile(
    r"GitHub|pull request|(?<![A-Za-z])PR\s*[#!]?\d+|GitHub Actions",
    re.IGNORECASE,
)
_FULL_REVIEW_CAPABILITIES = (
    "git.diff",
    "verification.run-local",
    "code.review-local",
)
_GITLAB_REVIEW_CAPABILITIES = (
    "merge_request.read",
    "gitlab.merge_request.commits.read",
    "gitlab.merge_request.diffs.read",
    "gitlab.repository.file.read",
    "gitlab.pipeline.jobs.read",
    "code.review-local",
)
_GITHUB_REVIEW_CAPABILITIES = (
    "github.pull_request.read",
    "github.pull_request.commits.read",
    "github.pull_request.diffs.read",
    "github.repository.file.read",
    "github.actions.run.jobs.read",
    "code.review-local",
)
_IDENTIFIER = re.compile(r"`([A-Za-z_][A-Za-z0-9_.-]{1,127})`|\b([A-Za-z_][A-Za-z0-9_.-]{2,127})\b")
_STOP_WORDS = frozenset((
    "code", "source", "review", "diff", "file", "class", "method", "function",
    "history", "commit", "this", "that", "what", "where", "why", "how",
))


@dataclass(frozen=True)
class CodeEvidencePlan:
    route: str
    required_capabilities: tuple[str, ...]
    repository_aliases: tuple[str, ...]
    blockers: tuple[str, ...]
    mutation_allowed: bool
    yunxiao_required: bool
    provider_status: str = "available"


def plan_code_evidence(
    message: str,
    routing_result: TaskIntentRoutingResult,
    *,
    repository_aliases: Sequence[str],
) -> CodeEvidencePlan:
    """Choose the smallest deterministic evidence plan; never authorize mutation."""

    if not isinstance(message, str) or not message.strip():
        raise ValueError("code_evidence_plan_invalid")
    if not isinstance(routing_result, TaskIntentRoutingResult):
        raise ValueError("code_evidence_plan_invalid")
    aliases = _aliases(repository_aliases)
    text = message.strip()
    decision = routing_result.decision
    review_requested = _REVIEW.search(text) is not None
    source_requested = _SOURCE.search(text) is not None
    history_requested = _HISTORY.search(text) is not None
    gitlab_remote_requested = _GITLAB_REMOTE.search(text) is not None
    github_remote_requested = _GITHUB_REMOTE.search(text) is not None

    if review_requested and github_remote_requested:
        route = "github_code_review"
        capabilities = _GITHUB_REVIEW_CAPABILITIES
    elif review_requested and gitlab_remote_requested:
        route = "gitlab_code_review"
        capabilities = _GITLAB_REVIEW_CAPABILITIES
    elif review_requested:
        route = "code_review"
        capabilities = _FULL_REVIEW_CAPABILITIES
    elif decision.mode == "task" and (
        routing_result.mutation_requested or _REQUIREMENT.search(text) is not None
    ):
        route = "requirement_workflow"
        capabilities = _FULL_REVIEW_CAPABILITIES
    elif source_requested or history_requested:
        route = "code_inquiry"
        capabilities = ("source.search", "source.read")
        if history_requested:
            capabilities += ("git.history",)
    else:
        route = "requirement_workflow" if decision.mode == "task" else "knowledge"
        capabilities = _FULL_REVIEW_CAPABILITIES if decision.mode == "task" else ()

    if route in {"gitlab_code_review", "github_code_review"}:
        blockers = (f"{route.removesuffix('_code_review')}_remote_evidence_orchestrator_unavailable",)
    else:
        blockers = (
            ("code_evidence_repository_unavailable",)
            if capabilities and not aliases
            else ()
        )
    return CodeEvidencePlan(
        route=route,
        required_capabilities=capabilities,
        repository_aliases=aliases,
        blockers=blockers,
        mutation_allowed=bool(
            route == "requirement_workflow" and routing_result.mutation_requested
        ),
        yunxiao_required=False,
        provider_status=(
            "unsupported"
            if route in {"gitlab_code_review", "github_code_review"}
            else "available"
        ),
    )


class CodeEvidenceService:
    """Execute a complete immutable diff -> verification -> review chain."""

    def __init__(
        self,
        repository: CodeEvidenceRepository,
        artifact_store: EvidenceArtifactStore,
        scopes: Mapping[str, RepositoryScope],
        *,
        verification_runner=None,
        reviewer_worker=None,
        allow_external_reviewer: bool = False,
    ) -> None:
        self._repository = repository
        self._store = artifact_store
        self._scopes = dict(scopes)
        self._diff = GitDiffEvidenceService(repository, artifact_store, scopes)
        verification_options = (
            {} if verification_runner is None else {"command_runner": verification_runner}
        )
        review_options = (
            {"allow_external_model": allow_external_reviewer}
            if reviewer_worker is None
            else {"worker": reviewer_worker}
        )
        self._verification = CodeEvidenceVerificationService(
            repository, artifact_store, scopes, **verification_options
        )
        self._review = CodeEvidenceReviewService(
            repository, artifact_store, scopes, **review_options
        )
        self._source = SourceEvidenceService(repository, artifact_store, scopes)
        self._history = GitHistoryEvidenceService(repository, artifact_store, scopes)
        self._sets = CodeEvidenceSetService(repository, artifact_store, scopes)
        self.reviewer_external_model_enabled = bool(
            allow_external_reviewer and reviewer_worker is None
        )

    def inspect(
        self,
        *,
        message: str,
        conversation_key: str,
        task_key: str,
        repository_aliases: Sequence[str],
        include_history: bool,
    ) -> dict[str, object]:
        aliases = _aliases(repository_aliases)
        pattern = _search_pattern(message)
        results: list[dict[str, object]] = []
        for ordinal, alias in enumerate(aliases, 1):
            search = self._source.search(
                repository_alias=alias,
                pattern=pattern,
                path_prefix=".",
                bundle_key=f"search-{task_key}-{ordinal}",
                conversation_key=conversation_key,
                task_key=task_key,
            )
            paths = tuple(str(item) for item in search["matched_paths"][:16])
            if not paths:
                results.append({"repository_alias": alias, "search": search, "source": None, "history": []})
                continue
            source = self._source.read(
                repository_alias=alias,
                paths=paths,
                bundle_key=f"source-{task_key}-{ordinal}",
                conversation_key=conversation_key,
                task_key=task_key,
            )
            history: list[dict[str, object]] = []
            if include_history:
                for path_index, path in enumerate(paths[:4], 1):
                    history.append(self._history.capture(
                        repository_alias=alias,
                        path=path,
                        limit=16,
                        bundle_key=f"history-{task_key}-{ordinal}-{path_index}",
                        conversation_key=conversation_key,
                        task_key=task_key,
                    ))
            results.append({"repository_alias": alias, "search": search, "source": source, "history": history})
        return {
            "schema_version": "his-code-evidence-inquiry.v1",
            "status": "complete",
            "pattern_sha256": __import__("hashlib").sha256(pattern.encode("utf-8")).hexdigest(),
            "repositories": results,
            "mutation_performed": False,
            "external_calls": any(
                bool(item["review"].get("external_calls")) for item in reviews
            ),
        }

    def review_changes(
        self,
        *,
        conversation_key: str,
        task_key: str,
        repository_aliases: Sequence[str],
        commands: Mapping[str, Sequence[tuple[str, ...]]],
        timeout_seconds: int = 120,
    ) -> dict[str, object]:
        aliases = _aliases(repository_aliases)
        if not aliases:
            raise ValueError("code_evidence_repository_unavailable")
        reviews: list[dict[str, object]] = []
        for ordinal, alias in enumerate(aliases, 1):
            repository_commands = commands.get(alias)
            if repository_commands is None:
                raise ValueError("code_evidence_verification_unavailable")
            diff = self._diff.capture(
                repository_alias=alias,
                bundle_key=f"diff-{task_key}-{ordinal}",
                conversation_key=conversation_key,
                task_key=task_key,
            )
            verification = self._verification.verify(
                diff_bundle_id=int(diff["bundle_id"]),
                bundle_key=f"verify-{task_key}-{ordinal}",
                conversation_key=conversation_key,
                task_key=task_key,
                commands=repository_commands,
                timeout_seconds=timeout_seconds,
            )
            if verification["verification_status"] != "passed":
                raise ValueError("code_evidence_verification_failed")
            review = self._review.review(
                diff_bundle_id=int(diff["bundle_id"]),
                verification_bundle_id=int(verification["verification_bundle_id"]),
                bundle_key=f"review-{task_key}-{ordinal}",
                conversation_key=conversation_key,
                task_key=task_key,
            )
            reviews.append({"diff": diff, "verification": verification, "review": review})
        evidence_set = None
        if len(reviews) > 1:
            evidence_set = self._sets.create(
                set_key=f"set-{task_key}",
                conversation_key=conversation_key,
                review_bundle_ids=tuple(
                    int(item["review"]["review_bundle_id"]) for item in reviews
                ),
            )
        return {
            "schema_version": "his-code-evidence-flow.v1",
            "status": "approved" if all(
                item["review"]["review_verdict"] == "approved" for item in reviews
            ) else "changes_requested",
            "repositories": reviews,
            "evidence_set": evidence_set,
            "mutation_performed": False,
            "external_calls": False,
        }


def _aliases(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise ValueError("code_evidence_plan_invalid")
    result = tuple(values)
    if (
        len(result) > 16
        or len(result) != len(set(result))
        or any(not isinstance(item, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item) is None for item in result)
    ):
        raise ValueError("code_evidence_plan_invalid")
    return result


def _search_pattern(message: object) -> str:
    if not isinstance(message, str):
        raise ValueError("code_evidence_search_term_unavailable")
    for match in _IDENTIFIER.finditer(message):
        value = next((item for item in match.groups() if item), "")
        if value.lower() not in _STOP_WORDS:
            return value
    raise ValueError("code_evidence_search_term_unavailable")
