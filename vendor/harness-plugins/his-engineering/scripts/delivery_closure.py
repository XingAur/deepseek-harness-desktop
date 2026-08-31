"""Immutable HIS delivery state machine owned by the HIS Engineering plugin.

The module deliberately has no Harness imports.  It creates the task commit
in an ephemeral linked worktree and performs only plan-declared, non-force
remote Git operations with pre/post read-back checks.  GitLab or GitHub
execution is provided by the caller and can complete only with a verified
provider receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import stat
import selectors
import time
import zlib
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import quote, unquote, urlsplit

if __package__:
    from .delivery_store import DeliveryStore
else:
    from delivery_store import DeliveryStore


DELIVERY_SCHEMA_VERSION = "1.0-delivery-closure"
DELIVERY_STATE_SEQUENCE = (
    "waiting_release_runtime_acceptance",
    "release_runtime_accepted",
    "task_commit_created",
    "waiting_rc_runtime_acceptance",
    "rc_runtime_accepted",
    "gitlab_delivery_pending",
    "github_delivery_pending",
    "completed",
)
# Remote delivery is available only when an immutable plan contains an explicit
# user-authorized action.  Runtime acceptance remains evidence, not another
# policy authorization.  A declared GitLab write stays pending until its
# provider reports a verified read-back receipt.
DISABLED_V1_COMPLETION_STATE = "completed"
_RC_PARITY_FIELDS = frozenset(("schema_version", "rc_post_head", "task_commit", "task_patch_hash", "changed_paths"))
GIT_TIMEOUT_SECONDS = 60
CONFIG_REAP_GRACE_SECONDS = 0.05
CONFIG_QUERY_MAX_BYTES = 65536
CONFIG_QUERY_REAP_FAILED = -2
_REMOTE_GIT_VERBS = frozenset(("push", "fetch", "ls-remote", "send-email"))
_FORBIDDEN_GIT_VERBS = frozenset(("reset", "checkout", "clean", "push", "fetch", "ls-remote", "send-email"))
_SAFE_GIT_CONFIG = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "diff.external=", "-c", "commit.gpgSign=false", "-c", "tag.gpgSign=false", "-c", "core.autocrlf=false")
_GITLAB_ALIAS = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_GITLAB_PROJECT_PART = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}\Z")
_GITLAB_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}\Z")
_GITHUB_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_GITHUB_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")


class DeliveryError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


@dataclass
class DeliveryPolicy:
    base_branch: str = "release_2.15.3_250515"
    integration_branch: str = "RC_2.16.1_250514"
    remote_name: str = "origin"
    requirement_branch_template: str = "feature-{id}"
    bug_branch_template: str = "hotfix-{id}"
    task_branch_template: str = "task-{id}"
    requirement_commit_template: str = "feat: {id}-{url} 《{title}》"
    bug_commit_template: str = "fix: {id}-{url} 《{title}》"
    task_commit_template: str = "chore: {id}-{url} 《{title}》"
    push_feature_default: bool = False
    cherry_pick_integration_default: bool = False
    push_integration_default: bool = False
    yunxiao_comment_default: bool = False
    yunxiao_transition_default: bool = False

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "DeliveryPolicy":
        if not isinstance(payload, Mapping):
            raise ValueError("delivery policy must be a mapping")
        defaults = asdict(cls())
        unknown = set(payload) - set(defaults)
        if unknown:
            raise ValueError("delivery policy contains unknown fields")
        values = {key: payload.get(key, default) for key, default in defaults.items()}
        if any(not isinstance(values[key], type(default)) for key, default in defaults.items()):
            raise ValueError("delivery policy contains invalid field types")
        return cls(**values)

    def task_branch(self, *, entity_kind: str, entity_id: str) -> str:
        template = {"requirement": self.requirement_branch_template, "bug": self.bug_branch_template, "task": self.task_branch_template}.get(entity_kind, self.task_branch_template)
        return template.format(id=entity_id)

    def commit_message(self, *, entity_kind: str, entity_id: str, url: str, title: str) -> str:
        template = {"requirement": self.requirement_commit_template, "bug": self.bug_commit_template, "task": self.task_commit_template}.get(entity_kind, self.task_commit_template)
        return template.format(id=entity_id, url=url, title=title)


@dataclass
class DeliveryRequest:
    entity_kind: str
    entity_id: str
    title: str
    url: str
    project_path: str
    expected_diff: str
    allowed_paths: list[str]
    output_dir: str
    task_id: Optional[int] = None
    source_run_id: Optional[int] = None
    verify_commands: Optional[list[str]] = None
    push_feature: Optional[bool] = None
    cherry_pick_integration: Optional[bool] = None
    push_integration: Optional[bool] = None
    create_gitlab_merge_request: bool = False
    gitlab_action: Optional[dict[str, Any]] = None
    create_github_pull_request: bool = False
    github_action: Optional[dict[str, Any]] = None
    yunxiao_comment: Optional[bool] = None
    yunxiao_transition: Optional[bool] = None


def _declared_gitlab_action(value: object) -> dict[str, Any] | None:
    """Normalize the two reviewed GitLab write shapes before hashing a plan."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"action", "parameters"}:
        raise DeliveryError("gitlab_action_invalid", "GitLab 写入必须声明受支持的结构化动作。")
    action = value.get("action")
    parameters = value.get("parameters")
    if action not in {"merge_request.create", "merge_request.comment.write"} or not isinstance(parameters, Mapping):
        raise DeliveryError("gitlab_action_invalid", "GitLab 写入动作不在允许范围内。")
    common = {"host_alias", "project_alias"}
    required = (
        common | {"source_branch", "target_branch", "title"}
        if action == "merge_request.create"
        else common | {"merge_request_iid", "body"}
    )
    has_declared_host = "gitlab_host" in parameters
    if set(parameters) != required | ({"gitlab_host"} if has_declared_host else set()):
        raise DeliveryError("gitlab_action_invalid", "GitLab 写入参数必须完整且不能包含额外字段。")
    host_alias = parameters.get("host_alias")
    project_alias = parameters.get("project_alias")
    if not isinstance(host_alias, str) or _GITLAB_ALIAS.fullmatch(host_alias) is None:
        raise DeliveryError("gitlab_action_invalid", "GitLab 主机别名无效。")
    if not isinstance(project_alias, str) or project_alias.count("/") != 1:
        raise DeliveryError("gitlab_action_invalid", "GitLab 项目别名无效。")
    group, project = project_alias.split("/", 1)
    if _GITLAB_PROJECT_PART.fullmatch(group) is None or _GITLAB_PROJECT_PART.fullmatch(project) is None:
        raise DeliveryError("gitlab_action_invalid", "GitLab 项目别名无效。")
    normalized: dict[str, Any] = {
        "action": action,
        "parameters": {"host_alias": host_alias, "project_alias": project_alias},
    }
    target = normalized["parameters"]
    if has_declared_host:
        gitlab_host = parameters.get("gitlab_host")
        if not isinstance(gitlab_host, str) or _gitlab_host_alias(gitlab_host) != host_alias:
            raise DeliveryError("gitlab_action_invalid", "GitLab 主机与主机别名不匹配。")
        target["gitlab_host"] = gitlab_host
    if action == "merge_request.create":
        source, destination, title = (
            parameters.get("source_branch"),
            parameters.get("target_branch"),
            parameters.get("title"),
        )
        if (
            not isinstance(source, str)
            or not isinstance(destination, str)
            or _GITLAB_BRANCH.fullmatch(source) is None
            or _GITLAB_BRANCH.fullmatch(destination) is None
            or source == destination
            or not isinstance(title, str)
            or not title.strip()
            or len(title) > 256
        ):
            raise DeliveryError("gitlab_action_invalid", "GitLab Merge Request 参数无效。")
        target.update({"source_branch": source, "target_branch": destination, "title": title})
    else:
        iid, body = parameters.get("merge_request_iid"), parameters.get("body")
        if (
            not isinstance(iid, int)
            or isinstance(iid, bool)
            or iid < 1
            or iid > 2_147_483_647
            or not isinstance(body, str)
            or not body.strip()
            or len(body) > 2_000
        ):
            raise DeliveryError("gitlab_action_invalid", "GitLab 评论参数无效。")
        target.update({"merge_request_iid": iid, "body": body})
    return normalized


def _gitlab_host_alias(hostname: str) -> str:
    """Create a stable provider-independent alias for one HTTPS hostname."""
    if (
        not isinstance(hostname, str)
        or hostname != hostname.lower()
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in hostname.split(".")
        )
    ):
        raise DeliveryError("gitlab_action_invalid", "GitLab 主机无效。")
    alias = hostname.replace(".", "-")
    if alias and alias[0].isdigit():
        alias = "gitlab-" + alias
    if _GITLAB_ALIAS.fullmatch(alias) is None:
        raise DeliveryError("gitlab_action_invalid", "GitLab 主机无法生成安全别名。")
    return alias


def _declared_github_action(value: object) -> dict[str, Any] | None:
    """Normalize the two reviewed GitHub write shapes before hashing a plan."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"action", "parameters"}:
        raise DeliveryError("github_action_invalid", "GitHub 写入必须声明受支持的结构化动作。")
    action = value.get("action")
    parameters = value.get("parameters")
    if action not in {
        "github.pull_request.create",
        "github.pull_request.comment.write",
    } or not isinstance(parameters, Mapping):
        raise DeliveryError("github_action_invalid", "GitHub 写入动作不在允许范围内。")
    common = {"owner", "repository"}
    required = (
        common | {"head", "base", "title"}
        if action == "github.pull_request.create"
        else common | {"pull_request_number", "body"}
    )
    if set(parameters) != required:
        raise DeliveryError("github_action_invalid", "GitHub 写入参数必须完整且不能包含额外字段。")
    owner, repository = parameters.get("owner"), parameters.get("repository")
    if not isinstance(owner, str) or _GITHUB_OWNER.fullmatch(owner) is None:
        raise DeliveryError("github_action_invalid", "GitHub owner 无效。")
    if (
        not isinstance(repository, str)
        or _GITHUB_REPOSITORY.fullmatch(repository) is None
        or repository.startswith((".", "-"))
        or ".." in repository
    ):
        raise DeliveryError("github_action_invalid", "GitHub repository 无效。")
    normalized: dict[str, Any] = {
        "action": action,
        "parameters": {"owner": owner, "repository": repository},
    }
    target = normalized["parameters"]
    if action == "github.pull_request.create":
        head, base, title = (
            parameters.get("head"),
            parameters.get("base"),
            parameters.get("title"),
        )
        if (
            not isinstance(head, str)
            or not isinstance(base, str)
            or _GITLAB_BRANCH.fullmatch(head) is None
            or _GITLAB_BRANCH.fullmatch(base) is None
            or ".." in head
            or "//" in head
            or ".." in base
            or "//" in base
            or head == base
            or not isinstance(title, str)
            or not title.strip()
            or len(title.encode("utf-8")) > 256
        ):
            raise DeliveryError("github_action_invalid", "GitHub Pull Request 参数无效。")
        target.update({"head": head, "base": base, "title": title})
    else:
        number, body = parameters.get("pull_request_number"), parameters.get("body")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or not 1 <= number <= 2_147_483_647
            or not isinstance(body, str)
            or not body.strip()
            or len(body.encode("utf-8")) > 2_000
        ):
            raise DeliveryError("github_action_invalid", "GitHub 评论参数无效。")
        target.update({"pull_request_number": number, "body": body})
    return normalized


def _origin_github_pull_request(request: DeliveryRequest, policy: DeliveryPolicy, *, entity_kind: str, entity_id: str) -> dict[str, Any]:
    """Derive one bounded GitHub PR target from the configured HTTPS origin."""
    remote_url = _approved_remote_url(Path(request.project_path).resolve(), policy.remote_name)
    parsed = urlsplit(remote_url)
    if parsed.hostname != "github.com" or parsed.port not in {None, 443}:
        raise DeliveryError("github_remote_unsupported", "GitHub 自动交付仅支持 github.com 标准 HTTPS 主机。")
    path_parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(path_parts) != 2 or not path_parts[1].endswith(".git"):
        raise DeliveryError("github_remote_project_invalid", "无法从 origin 解析 GitHub 仓库。")
    owner, repository = path_parts[0], path_parts[1][:-4]
    return _declared_github_action(
        {
            "action": "github.pull_request.create",
            "parameters": {
                "owner": owner,
                "repository": repository,
                "head": policy.task_branch(entity_kind=entity_kind, entity_id=entity_id),
                "base": policy.integration_branch,
                "title": f"{entity_id} {request.title}",
            },
        }
    ) or {}


def _github_target_alias(owner: str, repository: str, pull_request_number: int | None = None) -> str:
    owner_value, repository_value = owner.lower(), repository.lower()
    target = f"gh-o{len(owner_value)}-{owner_value}-r{len(repository_value)}-{repository_value}"
    return target if pull_request_number is None else f"{target}-p{pull_request_number}"


def _verified_github_receipt_target(action: Mapping[str, Any], receipt: Mapping[str, Any]) -> str | None:
    parameters = action.get("parameters")
    target = receipt.get("target_alias")
    if not isinstance(parameters, Mapping) or not isinstance(target, str):
        return None
    owner, repository = parameters.get("owner"), parameters.get("repository")
    if not isinstance(owner, str) or not isinstance(repository, str):
        return None
    if action.get("action") == "github.pull_request.comment.write":
        number = parameters.get("pull_request_number")
        return target if isinstance(number, int) and target == _github_target_alias(owner, repository, number) else None
    if action.get("action") == "github.pull_request.create":
        prefix = _github_target_alias(owner, repository) + "-p"
        suffix = target[len(prefix):] if target.startswith(prefix) else ""
        if suffix.isascii() and suffix.isdecimal() and str(int(suffix)) == suffix and int(suffix) > 0:
            return target
    return None


def _origin_gitlab_merge_request(request: DeliveryRequest, policy: DeliveryPolicy, *, entity_kind: str, entity_id: str) -> dict[str, Any]:
    """Derive one bounded MR target from the repository's configured origin."""
    remote_url = _approved_remote_url(Path(request.project_path).resolve(), policy.remote_name)
    parsed = urlsplit(remote_url)
    hostname = parsed.hostname
    if not hostname or parsed.port not in {None, 443}:
        raise DeliveryError("gitlab_remote_unsupported", "GitLab 自动交付仅支持标准 HTTPS 主机。")
    path_parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(path_parts) != 2 or not path_parts[1].endswith(".git"):
        raise DeliveryError("gitlab_remote_project_invalid", "无法从 origin 解析 GitLab 项目。")
    group, project = path_parts[0], path_parts[1][:-4]
    if (
        _GITLAB_PROJECT_PART.fullmatch(group) is None
        or _GITLAB_PROJECT_PART.fullmatch(project) is None
    ):
        raise DeliveryError("gitlab_remote_project_invalid", "origin GitLab 项目不在受支持范围内。")
    return _declared_gitlab_action(
        {
            "action": "merge_request.create",
            "parameters": {
                "host_alias": _gitlab_host_alias(hostname),
                "gitlab_host": hostname,
                "project_alias": f"{group}/{project}",
                "source_branch": policy.task_branch(entity_kind=entity_kind, entity_id=entity_id),
                "target_branch": policy.integration_branch,
                "title": f"{entity_id} {request.title}",
            },
        }
    ) or {}


def _gitlab_target_alias(host_alias: str, project_alias: str, merge_request_iid: int | None = None) -> str:
    """Match the provider's length-delimited, non-secret GitLab target ID."""
    group, project = project_alias.split("/", 1)
    target = f"gl-h{len(host_alias)}-{host_alias}-g{len(group)}-{group}-p{len(project)}-{project}"
    return target if merge_request_iid is None else f"{target}-m{merge_request_iid}"


def _verified_gitlab_receipt_target(action: Mapping[str, Any], receipt: Mapping[str, Any]) -> str | None:
    parameters = action.get("parameters")
    target = receipt.get("target_alias")
    if not isinstance(parameters, Mapping) or not isinstance(target, str):
        return None
    host, project = parameters.get("host_alias"), parameters.get("project_alias")
    if not isinstance(host, str) or not isinstance(project, str):
        return None
    if action.get("action") == "merge_request.comment.write":
        iid = parameters.get("merge_request_iid")
        return target if isinstance(iid, int) and target == _gitlab_target_alias(host, project, iid) else None
    if action.get("action") == "merge_request.create":
        prefix = _gitlab_target_alias(host, project) + "-m"
        suffix = target[len(prefix):] if target.startswith(prefix) else ""
        if suffix.isdecimal() and suffix and str(int(suffix)) == suffix and int(suffix) > 0:
            return target
    return None


def _environment(tmpdir: Path) -> dict[str, str]:
    try:
        info = tmpdir.lstat()
        resolved_tmpdir = tmpdir.resolve(strict=True)
    except OSError as exc:
        raise DeliveryError("temporary_directory_unsafe", "Git 子进程临时目录不可读取。") from exc
    if (
        resolved_tmpdir != tmpdir
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or (
            os.name != "nt"
            and (
                info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            )
        )
    ):
        raise DeliveryError("temporary_directory_unsafe", "Git 子进程临时目录不安全。")
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "TMPDIR": str(resolved_tmpdir),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "",
        "GIT_ALLOW_PROTOCOL": "file",
    }


def _trusted_temporary_root() -> Path:
    for candidate in (Path("/private/tmp"), Path("/tmp")):
        try:
            root = candidate.resolve(strict=True)
            info = root.lstat()
        except OSError:
            continue
        if (
            not stat.S_ISLNK(info.st_mode)
            and stat.S_ISDIR(info.st_mode)
            and info.st_uid == 0
            and bool(info.st_mode & stat.S_ISVTX)
            and os.access(root, os.W_OK | os.X_OK)
        ):
            return root
    raise DeliveryError(
        "temporary_root_unsafe",
        "未找到可信的系统临时目录根。",
    )


@contextmanager
def _private_subprocess_environment():
    with tempfile.TemporaryDirectory(
        prefix="his-engineering-subprocess-",
        dir=_trusted_temporary_root(),
    ) as temporary:
        tmpdir = Path(temporary).resolve()
        try:
            tmpdir.chmod(0o700)
        except OSError as exc:
            raise DeliveryError(
                "temporary_directory_unsafe",
                "无法保护 Git 子进程临时目录。",
            ) from exc
        yield _environment(tmpdir)


def _git(cwd: Path, args: list[str], *, input_text: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    if not args or args[0] in _FORBIDDEN_GIT_VERBS or any(item in _REMOTE_GIT_VERBS for item in args):
        raise DeliveryError("git_command_blocked", "交付插件禁止远端或破坏性 Git 命令。")
    argv = list(args)
    if argv[0] == "diff" and "--no-textconv" not in argv:
        argv.insert(1, "--no-textconv")
    with _private_subprocess_environment() as environment:
        return subprocess.run(["git", "-C", str(cwd), *_SAFE_GIT_CONFIG, *argv], input=input_text, text=True, capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=False, shell=False, env=environment)


def _approved_remote_url(project: Path, remote_name: str) -> str:
    """Read and normalize one configured HTTPS remote without exposing credentials."""
    if not isinstance(remote_name, str) or _GITLAB_ALIAS.fullmatch(remote_name) is None:
        raise DeliveryError("remote_name_invalid", "远端名称无效。")
    result = _git_config_query(project, ["--get", "remote." + remote_name + ".url"])
    value = result.stdout.strip()
    if result.returncode != 0 or result.stderr or not value or "\n" in value or "\r" in value:
        raise DeliveryError("remote_url_unreadable", "无法读取受控远端地址。")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise DeliveryError("remote_url_invalid", "远端地址无效或包含凭据。") from exc
    hostname = parsed.hostname
    authority = value[len("https://") :].split("/", 1)[0] if value.startswith("https://") else ""
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or authority != authority.lower()
        or port is not None and not 1 <= port <= 65535
        or not parsed.path.startswith("/")
        or "//" in parsed.path
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in hostname.split(".")
        )
        or re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path)
    ):
        raise DeliveryError("remote_url_invalid", "远端地址无效或包含凭据。")
    parts = [unquote(part) for part in parsed.path.split("/")]
    if any(part in {"", ".", ".."} or "/" in part for part in parts[1:]):
        raise DeliveryError("remote_url_invalid", "远端地址路径无效。")
    return "https://" + hostname + (f":{port}" if port not in {None, 443} else "") + "/" + "/".join(quote(part, safe="._-~") for part in parts[1:])


def _delivery_ref(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("refs/heads/"):
        raise DeliveryError("remote_ref_invalid", "远端分支引用无效。")
    branch = value.removeprefix("refs/heads/")
    if _GITLAB_BRANCH.fullmatch(branch) is None or branch in {"HEAD", "@"} or ".." in branch or branch.endswith("."):
        raise DeliveryError("remote_ref_invalid", "远端分支引用无效。")
    return value


def _remote_git(project: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run only the fixed, non-interactive HTTPS Git commands used by delivery."""
    if len(args) == 4 and args[:2] == ["ls-remote", "--refs"]:
        remote_url, target_ref = args[2], _delivery_ref(args[3])
        argv = ["ls-remote", "--refs", remote_url, target_ref]
    elif len(args) == 5 and args[:3] == ["push", "--porcelain", "--no-verify"]:
        remote_url, refspec = args[3], args[4]
        if refspec.count(":") != 1:
            raise DeliveryError("remote_ref_invalid", "推送 refspec 无效。")
        source_ref, target_ref = (_delivery_ref(part) for part in refspec.split(":", 1))
        argv = ["push", "--porcelain", "--no-verify", remote_url, source_ref + ":" + target_ref]
    else:
        raise DeliveryError("git_command_blocked", "远端 Git 命令不在交付允许范围内。")
    parsed = urlsplit(remote_url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise DeliveryError("remote_url_invalid", "远端地址无效或包含凭据。")
    with _private_subprocess_environment() as environment:
        environment["GIT_ALLOW_PROTOCOL"] = "https"
        try:
            return subprocess.run(
                ["git", "-C", str(project), *_SAFE_GIT_CONFIG, *argv],
                text=True,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
                shell=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeliveryError(
                "remote_dispatch_unknown",
                "远端 Git 调用中断，必须按恢复状态核对。",
                details={"remote_dispatch_attempted": True},
            ) from exc


def _read_remote_ref(project: Path, remote_url: str, target_ref: str) -> str | None:
    result = _remote_git(project, ["ls-remote", "--refs", remote_url, target_ref])
    if result.returncode != 0:
        raise DeliveryError("remote_ref_unreadable", "无法读取远端目标分支。")
    lines = result.stdout.splitlines()
    if not lines:
        return None
    if len(lines) != 1:
        raise DeliveryError("remote_ref_invalid", "远端目标分支返回不唯一。")
    fields = lines[0].split("\t")
    if len(fields) != 2 or fields[1] != target_ref or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", fields[0]) is None:
        raise DeliveryError("remote_ref_invalid", "远端目标分支返回无效。")
    return fields[0]


def _push_verified_ref(project: Path, *, remote_url: str, source_ref: str, target_ref: str, expected_remote: str | None) -> dict[str, Any]:
    source_ref, target_ref = _delivery_ref(source_ref), _delivery_ref(target_ref)
    source_head = _required_git(project, ["rev-parse", source_ref], "source_ref_unreadable")
    observed_before = _read_remote_ref(project, remote_url, target_ref)
    if observed_before != expected_remote:
        raise DeliveryError("remote_ref_drift", "远端分支已变化，拒绝推送。")
    result = _remote_git(project, ["push", "--porcelain", "--no-verify", remote_url, source_ref + ":" + target_ref])
    if result.returncode != 0:
        raise DeliveryError(
            "remote_push_unknown",
            "远端推送返回失败或中断，必须读取远端 ref 后恢复。",
            details={"remote_dispatch_attempted": True, "source_ref": source_ref, "target_ref": target_ref, "expected_remote": expected_remote},
        )
    observed_after = _read_remote_ref(project, remote_url, target_ref)
    if observed_after != source_head:
        raise DeliveryError(
            "remote_push_unknown",
            "远端推送后回读与预期 commit 不一致。",
            details={"remote_dispatch_attempted": True, "source_ref": source_ref, "target_ref": target_ref, "expected_remote": expected_remote},
        )
    return {"source_ref": source_ref, "target_ref": target_ref, "before": observed_before, "after": source_head, "pushed_at": _now_iso()}


def _validated_object_store(project: Path) -> Path:
    """Approve the target object store as data only; never as a config source."""
    identity = _repository_identity(project)
    objects = Path(str(identity["common_dir"])) / "objects"
    try:
        info = objects.lstat()
    except OSError as exc:
        raise DeliveryError("object_store_unsafe", "目标 Git object store 不可读取。") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DeliveryError("object_store_unsafe", "目标 Git object store 不安全。")
    if os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o022):
        raise DeliveryError("object_store_unsafe", "目标 Git object store 权限不安全。")
    return objects


@contextmanager
def _isolated_git(project: Path, *, work_tree: Optional[Path] = None):
    """Use a private Git dir/config; target config is never a construction input."""
    objects = _validated_object_store(project)
    object_format = _repository_object_format(project)
    with tempfile.TemporaryDirectory(
        prefix="his-engineering-isolated-git-",
        dir=_trusted_temporary_root(),
    ) as temporary:
        git_dir = Path(temporary) / "git"
        with _private_subprocess_environment() as environment:
            initialized = subprocess.run(["git", "init", "--template=/dev/null", "--object-format=" + object_format, str(git_dir)], text=True, capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=False, shell=False, env=environment)
        if initialized.returncode != 0:
            raise DeliveryError("isolated_git_init_failed", "无法创建隔离 Git 执行目录。")
        private_dir = git_dir / ".git"
        git_environment = {
            "GIT_DIR": str(private_dir),
            "GIT_WORK_TREE": str(work_tree or project),
            "GIT_OBJECT_DIRECTORY": str(private_dir / "objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(objects),
        }
        def run(args: list[str], *, input_text: Optional[str] = None, include_alternates: bool = True) -> subprocess.CompletedProcess[str]:
            if not args or args[0] in _FORBIDDEN_GIT_VERBS or any(item in _REMOTE_GIT_VERBS for item in args):
                raise DeliveryError("git_command_blocked", "交付插件禁止远端或破坏性 Git 命令。")
            argv = list(args)
            if argv[0] == "diff" and "--no-textconv" not in argv:
                argv.insert(1, "--no-textconv")
            with _private_subprocess_environment() as environment:
                environment.update(git_environment)
                if not include_alternates:
                    environment.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES")
                return subprocess.run(["git", *_SAFE_GIT_CONFIG, *argv], cwd=str(work_tree or project), input=input_text, text=True, capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=False, shell=False, env=environment)
        yield run, private_dir


def _required_git(cwd: Path, args: list[str], code: str) -> str:
    try:
        result = _git(cwd, args)
    except (OSError, subprocess.SubprocessError, DeliveryError) as exc:
        raise DeliveryError(code, "无法读取当前 Git 状态。") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise DeliveryError(code, "无法读取当前 Git 状态。")
    return result.stdout.strip()


def _stop_config_process(process: subprocess.Popen[bytes], deadline: float) -> bool:
    """Kill and reap within the original deadline; never claim an unreaped child is gone."""
    try:
        if process.poll() is None:
            process.kill()
        if process.poll() is not None:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        process.wait(timeout=min(remaining, CONFIG_REAP_GRACE_SECONDS))
        return process.poll() is not None
    except (OSError, subprocess.SubprocessError):
        return False


def _git_config_query(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Read config with bounded dual-pipe draining and a single total deadline."""
    with _private_subprocess_environment() as environment:
        return _git_config_query_in_environment(cwd, args, environment)


def _git_config_query_in_environment(
    cwd: Path,
    args: list[str],
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(["git", "-C", str(cwd), "config", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, env=environment)
    except OSError:
        return subprocess.CompletedProcess(["git", "config"], -1, "", "")
    assert process.stdout is not None and process.stderr is not None
    output, error = bytearray(), bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, output)
    selector.register(process.stderr, selectors.EVENT_READ, error)
    def failed(*, preserve_output: bool = False) -> subprocess.CompletedProcess[str]:
        reaped = _stop_config_process(process, deadline)
        return subprocess.CompletedProcess(process.args, -1 if reaped else CONFIG_QUERY_REAP_FAILED, output.decode("utf-8", "surrogateescape") if preserve_output else "", error.decode("utf-8", "surrogateescape") if preserve_output else "")
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if process.poll() is not None:
                events = selector.select(0)
            else:
                wait_budget = remaining - CONFIG_REAP_GRACE_SECONDS
                if wait_budget <= 0:
                    return failed()
                events = selector.select(wait_budget)
            if not events:
                return failed()
            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = key.data
                if len(buffer) + len(chunk) > CONFIG_QUERY_MAX_BYTES:
                    buffer.extend(chunk[:CONFIG_QUERY_MAX_BYTES - len(buffer)])
                    return failed(preserve_output=True)
                buffer.extend(chunk)
        if process.poll() is not None:
            return subprocess.CompletedProcess(process.args, process.returncode, output.decode("utf-8", "surrogateescape"), error.decode("utf-8", "surrogateescape"))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return failed()
        normal_wait = max(0.0, remaining - CONFIG_REAP_GRACE_SECONDS)
        if normal_wait <= 0:
            return failed()
        try:
            process.wait(timeout=normal_wait)
        except (OSError, subprocess.SubprocessError):
            return failed()
        if process.poll() is None:
            return failed()
        return subprocess.CompletedProcess(process.args, process.returncode, output.decode("utf-8", "surrogateescape"), error.decode("utf-8", "surrogateescape"))
    except OSError:
        return failed()
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def _safe_relative(path: str) -> bool:
    candidate = Path(path)
    return bool(path and path == candidate.as_posix() and not candidate.is_absolute() and "." not in candidate.parts and ".." not in candidate.parts and ".git" not in candidate.parts and "\\" not in path)


def _patch_paths(diff: str) -> list[str]:
    paths = []
    for line in diff.splitlines():
        if line.startswith("diff --git a/"):
            fields = line.split(" ")
            if len(fields) != 4 or not fields[2].startswith("a/") or fields[2][2:] != fields[3][2:]:
                return []
            paths.append(fields[2][2:])
    return paths


def _worktree_diff(project_path: Path, paths: list[str]) -> str:
    _audit_repository_automation(project_path)
    head = _required_git(project_path, ["rev-parse", "HEAD"], "current_head_unreadable")
    with _isolated_git(project_path) as (run, _private_dir):
        commands = (["read-tree", head], ["add", "-A", "--", *paths], ["diff", "--no-textconv", "--cached", "--binary", "--no-ext-diff", head, "--", *paths])
        output = ""
        for args in commands:
            _audit_repository_automation(project_path)
            result = run(list(args))
            if result.returncode != 0:
                raise DeliveryError("task_patch_capture_failed", "无法在隔离 Git index 读取任务 patch。", details={"operation": args[0]})
            output = result.stdout
        return output


def _file_state_hash(project_path: Path, paths: list[str]) -> str:
    records = []
    for relative in paths:
        target = project_path / relative
        if target.is_symlink():
            records.append({"path": relative, "type": "symlink", "value": os.readlink(target)})
        elif target.is_file():
            records.append({"path": relative, "type": "file", "value": hashlib.sha256(target.read_bytes()).hexdigest()})
        else:
            records.append({"path": relative, "type": "missing", "value": ""})
    return stable_hash(records)


def _repository_identity(project: Path) -> dict[str, Any]:
    """Return an independently checkable identity for root and shared Git dir."""
    result = _git(project, ["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"])
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) != 2 or not all(lines):
        raise DeliveryError("repository_identity_unreadable", "无法读取仓库根目录和 Git 公共目录身份。")
    root, common = Path(lines[0]), Path(lines[1])
    if not root.is_absolute() or not common.is_absolute():
        raise DeliveryError("repository_identity_unreadable", "仓库身份路径不是绝对路径。")
    try:
        root_info, common_info = root.lstat(), common.lstat()
    except OSError as exc:
        raise DeliveryError("repository_identity_unreadable", "仓库身份路径不可读取。") from exc
    if any(stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode) for item in (root_info, common_info)):
        raise DeliveryError("repository_identity_unreadable", "仓库身份路径不安全。")
    return {
        "root": str(root.resolve()), "root_device": root_info.st_dev, "root_inode": root_info.st_ino,
        "common_dir": str(common.resolve()), "common_device": common_info.st_dev, "common_inode": common_info.st_ino,
    }


def _changed_paths_between(project: Path, before: str, after: str) -> list[str]:
    _audit_repository_automation(project)
    result = _git(project, ["diff", "--no-textconv", "--name-only", "-z", before, after])
    values = result.stdout.split("\0")
    if result.returncode != 0 or not values or values[-1] != "" or any(not _safe_relative(value) for value in values[:-1]):
        raise DeliveryError("task_commit_drift", "无法验证任务 commit 的完整文件范围。")
    return sorted(values[:-1])


def _repository_object_format(project: Path) -> str:
    result = _git(project, ["rev-parse", "--show-object-format"])
    value = result.stdout.strip()
    if result.returncode != 0 or value not in {"sha1", "sha256"}:
        raise DeliveryError("object_import_failed", "无法确定目标仓库对象哈希格式。")
    return value


def _null_object_id(object_format: str) -> str:
    if object_format == "sha1":
        return "0" * 40
    if object_format == "sha256":
        return "0" * 64
    raise DeliveryError("object_import_failed", "未知 Git 对象哈希格式。")


def _object_digest(payload: bytes, object_format: str) -> str:
    return (hashlib.sha1(payload) if object_format == "sha1" else hashlib.sha256(payload)).hexdigest()


def _verify_loose_object_bytes(compressed: bytes, object_id: str, object_format: str) -> None:
    try:
        payload = zlib.decompress(compressed)
        header, body = payload.split(b"\0", 1)
        object_type, size = header.split(b" ", 1)
        if object_type not in {b"blob", b"tree", b"commit", b"tag"} or not size.isascii() or int(size) != len(body):
            raise ValueError("invalid loose object header")
    except (ValueError, zlib.error, OverflowError) as exc:
        raise DeliveryError("object_source_invalid", "私有 loose object 格式或大小无效。") from exc
    if _object_digest(payload, object_format) != object_id:
        raise DeliveryError("object_source_invalid", "私有 loose object 与声明 OID 不一致。")


def _read_verified_loose_object(path: Path, object_id: str, object_format: str, *, source: bool) -> bytes:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        code = "object_source_missing" if source else "object_target_invalid"
        raise DeliveryError(code, "Git object 文件不存在。") from exc
    except OSError as exc:
        raise DeliveryError("object_source_invalid" if source else "object_target_invalid", "无法安全读取 Git object 文件。") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise DeliveryError("object_source_invalid" if source else "object_target_invalid", "Git object 文件不是安全的普通文件。")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            compressed = handle.read()
    except OSError as exc:
        raise DeliveryError("object_source_invalid" if source else "object_target_invalid", "无法安全读取 Git object 文件。") from exc
    try:
        _verify_loose_object_bytes(compressed, object_id, object_format)
    except DeliveryError as exc:
        if source:
            raise
        raise DeliveryError("object_target_invalid", "目标 Git object 内容与 OID 不一致。") from exc
    return compressed


def _copy_verified_loose_object(compressed: bytes, destination: Path, object_id: str) -> None:
    """Publish prevalidated bytes once, without following or replacing a target."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o444)
    try:
        view = memoryview(compressed)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short object write")
            view = view[written:]
        os.fsync(descriptor)
        if os.name != "nt":
            os.fchmod(descriptor, 0o444)
    except Exception:
        try:
            os.close(descriptor)
        finally:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    else:
        os.close(descriptor)


def _listed_object_ids(result: subprocess.CompletedProcess[str], object_format: str, required_object_id: Optional[str] = None) -> list[str]:
    expected_length = 40 if object_format == "sha1" else 64
    if result.returncode != 0:
        raise DeliveryError("object_import_failed", "无法枚举已验证任务 commit 的私有对象。")
    object_ids = [line.split(" ", 1)[0] for line in result.stdout.splitlines()]
    if not object_ids or len(set(object_ids)) != len(object_ids) or (required_object_id is not None and required_object_id not in object_ids) or any(not re.fullmatch(r"[0-9a-f]{" + str(expected_length) + r"}", object_id) for object_id in object_ids):
        raise DeliveryError("object_import_failed", "Git 对象枚举不完整或格式无效。")
    return object_ids


def _assert_target_prefix(destination_dir: Path) -> bool:
    """Validate every OID prefix before its object entry is inspected.

    The return value records whether this call created target state, which is
    material recovery evidence even if the following object write fails.
    """
    created = False
    try:
        destination_dir.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        pass
    try:
        info = destination_dir.lstat()
    except OSError as exc:
        details = {"repository_mutation_attempted": True, "repository_changed": True} if created else {}
        raise DeliveryError("object_prefix_unsafe", "目标 Git object 前缀目录无法安全复查。", details=details) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o022)):
        details = {"repository_mutation_attempted": True, "repository_changed": True} if created else {}
        raise DeliveryError("object_prefix_unsafe", "目标 Git object 前缀目录不安全。", details=details)
    return created


def _import_private_objects(project: Path, private_dir: Path, commit_id: str, base_head: str, run: Callable[..., subprocess.CompletedProcess[str]]) -> None:
    """Preflight a complete private object graph, then publish it fail-closed."""
    object_format = _repository_object_format(project)
    candidate_graph = _listed_object_ids(run(["rev-list", "--objects", commit_id]), object_format, commit_id)
    base_graph = _listed_object_ids(_git(project, ["rev-list", "--objects", base_head]), object_format)
    object_ids = sorted(set(candidate_graph) - set(base_graph))
    if commit_id not in object_ids:
        raise DeliveryError("object_import_failed", "任务 commit 不是基线之外的新对象。")
    private_objects = private_dir / "objects"
    target = _validated_object_store(project)
    sources = {object_id: _read_verified_loose_object(private_objects / object_id[:2] / object_id[2:], object_id, object_format, source=True) for object_id in object_ids}
    target_write_started = False
    try:
        for object_id in object_ids:
            destination_dir = target / object_id[:2]
            destination = destination_dir / object_id[2:]
            if _assert_target_prefix(destination_dir):
                target_write_started = True
            try:
                destination.lstat()
            except FileNotFoundError:
                target_write_started = True
                _copy_verified_loose_object(sources[object_id], destination, object_id)
            else:
                _read_verified_loose_object(destination, object_id, object_format, source=False)
        observed_candidate = _listed_object_ids(_git(project, ["rev-list", "--objects", commit_id]), object_format, commit_id)
        observed_base = _listed_object_ids(_git(project, ["rev-list", "--objects", base_head]), object_format)
        if set(observed_candidate) - set(observed_base) != set(object_ids):
            raise DeliveryError("object_import_failed", "目标 Git object 图与已验证私有对象集合不一致。")
        for object_id in candidate_graph:
            check = _git(project, ["cat-file", "-e", object_id + "^{object}"])
            if check.returncode != 0:
                raise DeliveryError("object_import_failed", "目标 Git object store 无法读取已导入对象。")
    except DeliveryError as exc:
        if target_write_started:
            exc.details.setdefault("repository_mutation_attempted", True)
            exc.details.setdefault("repository_changed", True)
        raise
    except (OSError, ValueError) as exc:
        details = {"repository_mutation_attempted": True, "repository_changed": True} if target_write_started else {}
        raise DeliveryError("object_import_failed", "无法导入已验证任务对象。", details=details) from exc


def _observe_task_ref(project: Path, branch: str) -> tuple[str, str]:
    """Best-effort post-CAS observation; never lets an observer error escape."""
    try:
        observed = _git(project, ["rev-parse", "--verify", "refs/heads/" + branch])
    except Exception:
        return "unknown", ""
    if observed.returncode != 0:
        return "missing", ""
    value = observed.stdout.strip()
    return ("present", value) if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) else ("unknown", "")


def _audit_repository_automation(project: Path) -> None:
    """Refuse configuration that can execute while Git reads or stages files."""
    result = _git_config_query(project, ["--includes", "--null", "--name-only", "--list"])
    if result.returncode != 0 or len(result.stdout.encode("utf-8", "surrogateescape")) > 65536 or result.stderr:
        raise DeliveryError("repository_config_unreadable", "无法安全审计仓库可执行配置。")
    keys = result.stdout.split("\0")
    if not keys or keys[-1] != "" or any(not key or "\n" in key or "\r" in key for key in keys[:-1]):
        raise DeliveryError("repository_config_unreadable", "仓库配置键列表格式无效。")
    normalized = [key.lower() for key in keys[:-1]]
    executable = any(
        (key.startswith("filter.") and key.rsplit(".", 1)[-1] in {"clean", "smudge", "process"})
        or (key.startswith("diff.") and key.endswith(".textconv"))
        or key in {"commit.gpgsign", "tag.gpgsign", "gpg.program", "core.hookspath", "core.fsmonitor"}
        for key in normalized
    )
    if executable:
        raise DeliveryError("repository_automation_unsupported", "仓库配置包含可执行 filter、textconv、签名、hooks 或 fsmonitor。")


def inspect_repository(request: DeliveryRequest, policy: DeliveryPolicy) -> dict[str, Any]:
    project = Path(request.project_path)
    base: dict[str, Any] = {"schema_version": DELIVERY_SCHEMA_VERSION, "project_path": str(project.resolve()) if project.exists() else str(project), "classification": "unsafe_repository_state", "branch": "", "head": "", "task_patch_hash": hashlib.sha256(request.expected_diff.encode()).hexdigest(), "task_file_state_hash": "", "allowed_paths": [], "task_changed_paths": [], "unrelated_changed_paths": [], "status_entries": [], "blockers": []}
    if not project.is_absolute() or not project.is_dir() or project.is_symlink():
        base["blockers"].append("project_path_missing")
        return base
    project = project.resolve()
    try:
        root = _required_git(project, ["rev-parse", "--show-toplevel"], "not_git_repository")
    except DeliveryError:
        base["blockers"].append("not_git_repository")
        return base
    if Path(root).resolve() != project:
        base["blockers"].append("project_path_not_git_root")
        return base
    git_metadata = project / ".git"
    if not git_metadata.is_dir():
        base["blockers"].append("delivery_project_linked_worktree")
        base["git_metadata_path"] = str(git_metadata)
        base["git_metadata_type"] = "file" if git_metadata.is_file() else "unsupported"
        return base
    try:
        _audit_repository_automation(project)
    except DeliveryError as exc:
        base["blockers"].append("repository_automation_unsupported")
        return base
    if (project / ".gitmodules").exists():
        base["blockers"].append("repository_automation_unsupported")
        return base
    allowed = list(dict.fromkeys(path for path in request.allowed_paths if _safe_relative(path)))
    base["allowed_paths"] = allowed
    if len(allowed) != len(request.allowed_paths) or not allowed:
        base["blockers"].append("unsafe_allowed_path")
        return base
    paths = _patch_paths(request.expected_diff)
    if not request.expected_diff or not paths:
        base["blockers"].append("task_patch_missing")
        return base
    if set(paths) != set(allowed):
        base["blockers"].append("task_patch_outside_allowlist")
        return base
    branch = _required_git(project, ["branch", "--show-current"], "git_reference_unreadable")
    head = _required_git(project, ["rev-parse", "HEAD"], "git_reference_unreadable")
    base.update({"branch": branch, "head": head, "repository_identity": _repository_identity(project), "task_file_state_hash": _file_state_hash(project, allowed)})
    if branch != policy.base_branch:
        base["blockers"].append("wrong_base_branch")
        return base
    status = _git(project, ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=all"])
    if status.returncode != 0:
        base["blockers"].append("git_status_unreadable")
        return base
    entries = []
    for token in status.stdout.split("\0"):
        if len(token) >= 4:
            entries.append({"status": token[:2], "path": token[3:]})
    changed = sorted({item["path"] for item in entries})
    base["status_entries"] = entries
    base["task_changed_paths"] = sorted(set(changed) & set(allowed))
    base["unrelated_changed_paths"] = sorted(set(changed) - set(allowed))
    current = _worktree_diff(project, allowed)
    if current != request.expected_diff:
        base["classification"] = "ambiguous_overlap"
        base["blockers"].append("task_patch_mismatch")
    else:
        base["classification"] = "mixed_separable" if base["unrelated_changed_paths"] else "task_owned_exact"
    return base


def policy_snapshot(policy: DeliveryPolicy) -> dict[str, Any]:
    return asdict(policy)


def build_delivery_plan(request: DeliveryRequest, policy: DeliveryPolicy, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    commands = list(request.verify_commands or [])
    external_write_patterns = (
        r"(?<![\w-])git(?:\s+(?:-[^\s]+|--[^\s]+))*\s+(?:push|send-email)\b",
        r"(?<![\w-])(?:npm|pnpm|cargo)\s+publish\b",
        r"(?<![\w-])yarn(?:\s+npm)?\s+publish\b",
        r"(?<![\w-])(?:docker|podman)\s+push\b",
        r"(?<![\w-])(?:\./)?(?:mvnw?|gradlew?|gradle)(?:\s+\S+)*\s+(?:deploy|publish)\b",
        r"(?<![\w-])gh\s+(?:pr|release|issue)\s+create\b",
        r"(?<![\w-])curl\b[^\n]*(?:--request|-X)\s*(?:POST|PUT|PATCH|DELETE)\b",
        r"(?<![\w-])wget\b[^\n]*(?:--post-data|--post-file)\b",
        r"(?<![\w-])(?:scp|sftp|rsync)\b",
    )
    for command in commands:
        if not isinstance(command, str) or not command.strip():
            raise DeliveryError("verification_command_empty", "专项验证命令不能为空。")
        normalized = re.sub(r"\\\s*\n", " ", command)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in external_write_patterns):
            raise DeliveryError(
                "unsafe_verification_command",
                f"验证命令包含外部写入或发布动作，不能作为专项验证执行：{normalized}",
            )
        if any(token in normalized for token in (";", "|", "&", "`", "$")):
            raise DeliveryError("unsafe_verification_command", "验证命令不满足本地只读边界。")
    kind, entity_id = request.entity_kind.strip().lower() or "task", request.entity_id.strip().upper()
    push_feature = policy.push_feature_default if request.push_feature is None else bool(request.push_feature)
    cherry_pick_integration = (
        policy.cherry_pick_integration_default
        if request.cherry_pick_integration is None
        else bool(request.cherry_pick_integration)
    )
    push_integration = (
        policy.push_integration_default
        if request.push_integration is None
        else bool(request.push_integration)
    )
    if not isinstance(request.create_gitlab_merge_request, bool):
        raise DeliveryError("gitlab_action_invalid", "GitLab MR 创建标记无效。")
    if request.create_gitlab_merge_request and request.gitlab_action is not None:
        raise DeliveryError("gitlab_action_invalid", "不能同时声明自动 GitLab MR 与自定义 GitLab 动作。")
    if not isinstance(request.create_github_pull_request, bool):
        raise DeliveryError("github_action_invalid", "GitHub PR 创建标记无效。")
    if request.create_github_pull_request and request.github_action is not None:
        raise DeliveryError("github_action_invalid", "不能同时声明自动 GitHub PR 与自定义 GitHub 动作。")
    if request.create_gitlab_merge_request:
        # A task-branch-to-RC MR is unusable until its declared source ref is
        # present on the same origin; keep that prerequisite inside one plan.
        push_feature = True
    gitlab_action = (
        _origin_gitlab_merge_request(request, policy, entity_kind=kind, entity_id=entity_id)
        if request.create_gitlab_merge_request
        else _declared_gitlab_action(request.gitlab_action)
    )
    if request.create_github_pull_request:
        push_feature = True
    github_action = (
        _origin_github_pull_request(request, policy, entity_kind=kind, entity_id=entity_id)
        if request.create_github_pull_request
        else _declared_github_action(request.github_action)
    )
    if gitlab_action is not None and github_action is not None:
        raise DeliveryError(
            "multiple_hosting_writes_not_allowed",
            "一个不可变交付计划只能声明一个 GitLab 或 GitHub 写入目标。",
        )
    remote_actions_enabled = any(
        (push_feature, cherry_pick_integration, push_integration, gitlab_action, github_action)
    )
    plan: dict[str, Any] = {"schema_version": DELIVERY_SCHEMA_VERSION, "generated_at": _now_iso(), "task_id": request.task_id, "source_run_id": request.source_run_id, "entity": {"kind": kind, "id": entity_id, "title": request.title, "url": request.url}, "project_path": str(Path(request.project_path).resolve()), "remote": policy.remote_name, "base_branch": policy.base_branch, "base_head": snapshot.get("head") or "", "task_branch": policy.task_branch(entity_kind=kind, entity_id=entity_id), "integration_branch": policy.integration_branch, "commit_message": policy.commit_message(entity_kind=kind, entity_id=entity_id, url=request.url, title=request.title), "allowed_paths": list(snapshot.get("allowed_paths") or []), "verify_commands": commands, "repository_snapshot_hash": stable_hash(snapshot), "task_patch_hash": hashlib.sha256(request.expected_diff.encode()).hexdigest(), "task_file_state_hash": snapshot.get("task_file_state_hash") or "", "workspace_classification": snapshot.get("classification") or "", "workspace_blockers": list(snapshot.get("blockers") or []), "actions": {"create_task_branch": True, "commit": True, "push_feature": push_feature, "cherry_pick_integration": cherry_pick_integration, "push_integration": push_integration, "gitlab_write": gitlab_action, "github_write": github_action, "yunxiao_comment": False, "yunxiao_transition": False}, "remote_actions_enabled": remote_actions_enabled, "output_dir": str(Path(request.output_dir).resolve())}
    plan["plan_hash"] = stable_hash(plan)
    return plan


def delivery_plan_to_markdown(plan: Mapping[str, Any]) -> str:
    """Render the immutable delivery plan and every declared remote action."""
    actions = plan.get("actions") or {}
    verify_commands = list(plan.get("verify_commands") or [])
    formatted_commands = []
    for command in verify_commands:
        escaped = str(command).replace("`", "\\`")
        formatted_commands.append(f"- `{escaped}`")
    lines = [
        "# HIS Harness Git 交付计划",
        "",
        f"- 需求：{(plan.get('entity') or {}).get('id') or '-'} {(plan.get('entity') or {}).get('title') or ''}",
        f"- 原源码仓库：{plan.get('project_path') or '-'}",
        f"- release：{plan.get('base_branch') or '-'} @ {plan.get('base_head') or '-'}",
        f"- 任务分支：{plan.get('task_branch') or '-'}",
        f"- commit：{plan.get('commit_message') or '-'}",
        f"- RC：{plan.get('integration_branch') or '-'}",
        f"- Plan Hash：{plan.get('plan_hash') or '-'}",
        "",
        "## 本计划动作",
        "",
        f"- 创建任务分支：{'是' if actions.get('create_task_branch') else '否'}",
        f"- 创建 commit：{'是' if actions.get('commit') else '否'}",
        f"- 推送需求分支：{'是' if actions.get('push_feature') else '否'}",
        f"- Cherry-pick 到 RC：{'是' if actions.get('cherry_pick_integration') else '否'}",
        f"- 推送 RC：{'是' if actions.get('push_integration') else '否'}",
        f"- GitLab 写入：{(actions.get('gitlab_write') or {}).get('action') or '否'}",
        f"- GitHub 写入：{(actions.get('github_write') or {}).get('action') or '否'}",
        f"- 写云效评论：{'是' if actions.get('yunxiao_comment') else '否'}",
        "",
        "## 专项验证命令",
        "",
        *(formatted_commands or ["- 未配置"]),
        "",
        "## 安全边界",
        "",
        "- 当前仅生成计划，未切分支、未提交、未推送、未写外部系统。",
        "- 只有用户明确交付且计划哈希不变时，才会执行计划中列出的远端动作。",
        "- 禁止 force push；每次远端 ref 写入都要推送前后回读，并在中断时保留恢复证据。",
    ]
    return "\n".join(lines) + "\n"


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _atomic_text(path: Path, text: str) -> None:
    _assert_safe_artifact_ancestry(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_artifact_ancestry(path.parent)
    if path.exists() and path.is_symlink():
        raise DeliveryError("artifact_path_unsafe", "交付证据路径不安全。")
    temporary = path.with_name(path.name + ".tmp")
    try:
        with open(temporary, "x", encoding="utf-8") as handle:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.is_symlink():
            raise DeliveryError("artifact_path_unsafe", "交付证据临时路径不安全。")
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise DeliveryError("artifact_write_failed", "无法原子写入交付证据。") from exc


def _assert_safe_artifact_ancestry(path: Path) -> None:
    """Reject unsafe existing ancestors before private artifact write/publish."""
    if not path.is_absolute():
        raise DeliveryError("artifact_path_unsafe", "交付证据路径必须是绝对路径。")
    current = Path(path.anchor)
    sticky_shared_parent = False
    for part in path.parts[1:]:
        current = current / part
        if not current.exists():
            continue
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise DeliveryError("artifact_path_unsafe", "交付证据路径包含符号链接或非目录祖先。")
        if os.name != "nt":
            writable_by_others = bool(info.st_mode & 0o022)
            # Shared system directories are acceptable only when sticky; an
            # owned artifact ancestor must remain private, never group/world writable.
            if writable_by_others and not (info.st_mode & stat.S_ISVTX):
                raise DeliveryError("artifact_path_unsafe", "交付证据祖先目录可被其他用户改写。")
            if info.st_uid == os.geteuid() and writable_by_others:
                raise DeliveryError("artifact_path_unsafe", "交付证据私有目录权限不安全。")
            if sticky_shared_parent and info.st_uid != os.geteuid():
                raise DeliveryError("artifact_path_unsafe", "共享 sticky 目录后的现有路径不属于当前用户。")
            elif sticky_shared_parent:
                sticky_shared_parent = False
            if writable_by_others and bool(info.st_mode & stat.S_ISVTX):
                sticky_shared_parent = True


def _make_private_directory(path: Path) -> None:
    _assert_safe_artifact_ancestry(path.parent)
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise DeliveryError("artifact_path_unsafe", "交付证据目录不安全。")
    else:
        path.mkdir(mode=0o700)
    if os.name != "nt":
        info = path.lstat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise DeliveryError("artifact_path_unsafe", "交付证据目录必须由当前用户私有拥有。")


class DeliveryClosure:
    def __init__(self, *, store: DeliveryStore, policy: DeliveryPolicy | Mapping[str, Any], on_state_change: Optional[Callable[[int, str], None]] = None) -> None:
        self.store = store
        self.policy = DeliveryPolicy.from_payload(policy) if isinstance(policy, Mapping) else policy
        if not isinstance(self.policy, DeliveryPolicy):
            raise TypeError("policy must be DeliveryPolicy or mapping")
        self.on_state_change = on_state_change or (lambda _id, _state: None)

    def _prepare_state_change(self, transaction_id: int, state: str, journal_path: Path, journal: dict[str, Any]) -> None:
        """Persist callback intent first; ambiguous callbacks require operator reconciliation."""
        callback_identity = {"transaction_id": transaction_id, "transaction_key": journal.get("transaction_key"), "plan_hash": journal.get("plan_hash"), "target_state": state}
        journal["callback_delivery"] = {"state": "pending", **callback_identity}
        journal.setdefault("checkpoints", []).append({"state": "prepare_callback_pending", "at": _now_iso(), "target_state": state})
        _atomic_json(journal_path, journal)
        try:
            self.on_state_change(transaction_id, state)
        except Exception as exc:
            journal["callback_delivery"] = {"state": "ambiguous", **callback_identity}
            journal.setdefault("checkpoints", []).append({"state": "prepare_callback_ambiguous", "at": _now_iso(), "target_state": state})
            try:
                _atomic_json(journal_path, journal)
            except Exception:
                pass
            try:
                self.store.update_transaction(transaction_id, state="prepare_recovery_required", last_error="prepare_callback_ambiguous")
            except Exception:
                pass
            raise DeliveryError("prepare_callback_ambiguous", "交付状态通知结果不明确，需要人工核对后再处理。") from exc
        journal["callback_delivery"] = {"state": "acknowledged", **callback_identity}
        journal.setdefault("checkpoints", []).append({"state": "prepare_callback_acknowledged", "at": _now_iso(), "target_state": state})
        try:
            _atomic_json(journal_path, journal)
        except Exception as exc:
            try:
                self.store.update_transaction(transaction_id, state="prepare_recovery_required", last_error="prepare_callback_ambiguous")
            except Exception:
                pass
            raise DeliveryError("prepare_callback_ambiguous", "交付状态通知结果不明确，需要人工核对后再处理。") from exc

    def prepare(self, request: DeliveryRequest) -> dict[str, Any]:
        snapshot = inspect_repository(request, self.policy)
        if snapshot["classification"] not in {"task_owned_exact", "mixed_separable"}:
            raise DeliveryError("workspace_blocked", "当前源码工作区不满足交付条件。", details={"snapshot": snapshot})
        key = "delivery-" + stable_hash({"request": asdict(request), "snapshot": snapshot, "policy": policy_snapshot(self.policy)})[:24]
        existing = self.store.get_by_key(key)
        if existing:
            # Reconcile the only tolerated incomplete journal shape produced
            # after an ambiguous add_transaction outcome.
            journal_path = Path(str(existing.get("journal_path") or ""))
            try:
                provisional = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                provisional = {}
            callback_delivery = provisional.get("callback_delivery") if isinstance(provisional.get("callback_delivery"), Mapping) else {}
            if callback_delivery.get("state") == "acknowledged":
                expected_state = "waiting_release_runtime_acceptance"
                legacy_keys = {"state", "target_state"}
                current_keys = {"state", "target_state", "transaction_id", "transaction_key", "plan_hash"}
                callback_keys = set(callback_delivery)
                outer_identity_matches = (
                    provisional.get("transaction_id") == existing.get("id")
                    and provisional.get("transaction_key") == key
                    and provisional.get("plan_hash") == existing.get("plan_hash")
                )
                if callback_keys == legacy_keys:
                    if not outer_identity_matches or callback_delivery.get("target_state") != expected_state:
                        raise DeliveryError("prepare_recovery_inconsistent", "已确认的交付状态通知与事务身份不一致。")
                    self._context(existing)
                    callback_delivery = {"state": "acknowledged", "transaction_id": existing["id"], "transaction_key": key, "plan_hash": existing["plan_hash"], "target_state": expected_state}
                    provisional["callback_delivery"] = callback_delivery
                    try:
                        _atomic_json(journal_path, provisional)
                    except Exception as exc:
                        raise DeliveryError("prepare_persistence_failed", "旧版交付状态通知无法完成身份升级。") from exc
                elif callback_keys != current_keys:
                    raise DeliveryError("prepare_recovery_inconsistent", "已确认的交付状态通知格式不完整或包含未识别字段。")
                if (
                    not outer_identity_matches
                    or callback_delivery.get("transaction_id") != existing.get("id")
                    or callback_delivery.get("transaction_key") != key
                    or callback_delivery.get("plan_hash") != existing.get("plan_hash")
                    or callback_delivery.get("target_state") != expected_state
                    or provisional.get("transaction_id") != existing.get("id")
                    or provisional.get("transaction_key") != key
                    or provisional.get("plan_hash") != existing.get("plan_hash")
                ):
                    raise DeliveryError("prepare_recovery_inconsistent", "已确认的交付状态通知与事务身份不一致。")
                if existing.get("state") == "prepare_recovery_required":
                    context = self._context(existing)
                    try:
                        self.store.update_transaction(int(existing["id"]), state=expected_state, last_error="")
                        reconciled = self.store.get_transaction(int(existing["id"]))
                    except Exception as exc:
                        raise DeliveryError("prepare_persistence_failed", "已确认的交付状态通知无法完成存储恢复。") from exc
                    if reconciled is None:
                        raise DeliveryError("transaction_persistence_failed", "已确认的交付状态通知无法回读事务。")
                    return {"transaction": reconciled, "plan": context["plan"], "snapshot": context["snapshot"], "idempotent": True}
            if existing.get("last_error") == "prepare_callback_ambiguous" or callback_delivery.get("state") in {"pending", "ambiguous"}:
                try:
                    self.store.update_transaction(int(existing["id"]), state="prepare_recovery_required", last_error="prepare_callback_ambiguous")
                except Exception:
                    pass
                raise DeliveryError("prepare_callback_ambiguous", "交付状态通知结果不明确，需要人工核对后再处理。")
            if (
                provisional.get("transaction_id") is None
                and (
                    provisional.get("state") == "prepare_store_unknown"
                    or (provisional.get("state") == "prepare_pending_store" and existing.get("state") == "prepare_recovery_required")
                )
            ):
                try:
                    provisional_plan = json.loads(Path(str(provisional["plan_path"])).read_text(encoding="utf-8"))
                    provisional_snapshot = json.loads(Path(str(provisional["snapshot_path"])).read_text(encoding="utf-8"))
                    provisional_patch = Path(str(provisional["patch_path"])).read_text(encoding="utf-8")
                except (OSError, ValueError, KeyError, TypeError):
                    raise DeliveryError("prepare_recovery_inconsistent", "交付恢复记录缺少完整私有证据。")
                proof_ok = (
                    provisional.get("transaction_key") == key
                    and provisional.get("plan_hash") == existing.get("plan_hash")
                    and str(existing.get("journal_path")) == str(journal_path)
                    and provisional_plan.get("plan_hash") == existing.get("plan_hash")
                    and stable_hash({item: value for item, value in provisional_plan.items() if item != "plan_hash"}) == provisional_plan.get("plan_hash")
                    and provisional_plan.get("repository_snapshot_hash") == stable_hash(provisional_snapshot)
                    and provisional_snapshot == existing.get("repository_snapshot") == snapshot
                    and hashlib.sha256(provisional_patch.encode()).hexdigest() == provisional_plan.get("task_patch_hash")
                )
                if not proof_ok:
                    raise DeliveryError("prepare_recovery_inconsistent", "交付恢复记录与私有 journal 不一致。")
                try:
                    provisional["transaction_id"] = existing["id"]
                    provisional["state"] = "waiting_release_runtime_acceptance"
                    provisional.setdefault("checkpoints", []).append({"state": "waiting_release_runtime_acceptance", "at": _now_iso(), "reconciled": True})
                    _atomic_json(journal_path, provisional)
                    self.store.update_transaction(int(existing["id"]), state="waiting_release_runtime_acceptance", last_error="")
                    if not any(event.get("event_type") == "planned" for event in self.store.get_events(int(existing["id"]))):
                        self.store.add_event({"transaction_id": int(existing["id"]), "event_type": "planned", "status": "success", "input_hash": stable_hash(provisional_plan), "details": {"plan_hash": existing["plan_hash"], "remote_actions_enabled": False, "reconciled": True}})
                    self._prepare_state_change(int(existing["id"]), "waiting_release_runtime_acceptance", journal_path, provisional)
                    context = self._context(self.store.get_transaction(int(existing["id"])) or existing)
                    return {"transaction": self.store.get_transaction(int(existing["id"])), "plan": context["plan"], "snapshot": context["snapshot"], "idempotent": True}
                except Exception as exc:
                    if isinstance(exc, DeliveryError) and exc.code == "prepare_callback_ambiguous":
                        raise
                    try:
                        self.store.update_transaction(int(existing["id"]), state="prepare_recovery_required", last_error="prepare_reconcile_failed")
                    except Exception:
                        pass
                    if isinstance(exc, DeliveryError):
                        raise
                    raise DeliveryError("prepare_persistence_failed", "交付准备恢复未能原子完成。") from exc
            if existing.get("state") == "prepare_recovery_required":
                try:
                    context = self._context(existing)
                    journal = context["journal"]
                    if journal.get("transaction_id") != existing.get("id") or journal.get("transaction_key") != key or journal.get("plan_hash") != existing.get("plan_hash") or context["snapshot"] != existing.get("repository_snapshot"):
                        raise DeliveryError("prepare_recovery_inconsistent", "交付恢复记录与私有 journal 不一致。")
                    journal["state"] = "waiting_release_runtime_acceptance"
                    journal.setdefault("checkpoints", []).append({"state": "waiting_release_runtime_acceptance", "at": _now_iso(), "reconciled": True})
                    _atomic_json(Path(str(existing["journal_path"])), journal)
                    self.store.update_transaction(int(existing["id"]), state="waiting_release_runtime_acceptance", last_error="")
                    if not any(event.get("event_type") == "planned" for event in self.store.get_events(int(existing["id"]))):
                        self.store.add_event({"transaction_id": int(existing["id"]), "event_type": "planned", "status": "success", "input_hash": stable_hash(context["plan"]), "details": {"plan_hash": context["plan"]["plan_hash"], "remote_actions_enabled": False, "reconciled": True}})
                    self._prepare_state_change(int(existing["id"]), "waiting_release_runtime_acceptance", Path(str(existing["journal_path"])), journal)
                    repaired = self.store.get_transaction(int(existing["id"]))
                    return {"transaction": repaired, "plan": context["plan"], "snapshot": context["snapshot"], "idempotent": True}
                except Exception as exc:
                    if isinstance(exc, DeliveryError) and exc.code == "prepare_callback_ambiguous":
                        raise
                    try:
                        self.store.update_transaction(int(existing["id"]), state="prepare_recovery_required", last_error="prepare_reconcile_failed")
                    except Exception:
                        pass
                    if isinstance(exc, DeliveryError):
                        raise
                    raise DeliveryError("prepare_persistence_failed", "交付准备恢复未能原子完成。") from exc
            context = self._context(existing)
            return {"transaction": existing, "plan": context["plan"], "snapshot": context["snapshot"], "idempotent": True}
        plan = build_delivery_plan(request, self.policy, snapshot)
        project_root = Path(request.project_path).resolve()
        git_directory = project_root / ".git"
        _assert_safe_artifact_ancestry(git_directory)
        if git_directory.is_symlink() or not git_directory.is_dir() or (os.name != "nt" and git_directory.lstat().st_uid != os.geteuid()):
            raise DeliveryError("artifact_path_unsafe", "Git 交付证据目录不属于当前用户或不安全。")
        _make_private_directory(git_directory / "his-engineering")
        _make_private_directory(git_directory / "his-engineering" / "delivery")
        root = git_directory / "his-engineering" / "delivery" / key
        try:
            root.relative_to(git_directory.resolve())
        except ValueError as exc:
            raise DeliveryError("artifact_path_unsafe", "交付证据目录越出仓库 Git 元数据。") from exc
        if root.exists() or root.is_symlink():
            try:
                _assert_safe_artifact_ancestry(root)
                if root.is_symlink() or not root.is_dir():
                    raise ValueError("unsafe root")
                expected_artifacts = {
                    "plan_path": root / "delivery_plan.json",
                    "snapshot_path": root / "repository_snapshot.json",
                    "patch_path": root / "expected_task.diff",
                    "journal_path": root / "journal.json",
                }
                if {child.name for child in root.iterdir()} != {path.name for path in expected_artifacts.values()}:
                    raise ValueError("unexpected provisional artifacts")
                if os.name != "nt":
                    root_info = root.lstat()
                    if root_info.st_uid != os.geteuid() or stat.S_IMODE(root_info.st_mode) != 0o700:
                        raise ValueError("provisional root is not private")
                for artifact in expected_artifacts.values():
                    info = artifact.lstat()
                    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                        raise ValueError("provisional artifact is not a regular file")
                    if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
                        raise ValueError("provisional artifact is not private")
                unknown = json.loads(expected_artifacts["journal_path"].read_text(encoding="utf-8"))
                if any(Path(str(unknown.get(name) or "")) != path for name, path in expected_artifacts.items() if name != "journal_path"):
                    raise ValueError("provisional paths escape root")
                retry_plan = json.loads(expected_artifacts["plan_path"].read_text(encoding="utf-8"))
                retry_snapshot = json.loads(expected_artifacts["snapshot_path"].read_text(encoding="utf-8"))
                retry_patch = expected_artifacts["patch_path"].read_text(encoding="utf-8")
                retry_plan_core = {name: value for name, value in retry_plan.items() if name not in {"generated_at", "plan_hash"}}
                current_plan_core = {name: value for name, value in plan.items() if name not in {"generated_at", "plan_hash"}}
                valid_unknown = (
                    unknown.get("transaction_key") == key and unknown.get("transaction_id") is None and unknown.get("state") == "prepare_store_unknown"
                    and unknown.get("plan_hash") == retry_plan.get("plan_hash")
                    and stable_hash({name: value for name, value in retry_plan.items() if name != "plan_hash"}) == retry_plan.get("plan_hash")
                    and retry_plan_core == current_plan_core and retry_snapshot == snapshot
                    and retry_plan.get("repository_snapshot_hash") == stable_hash(retry_snapshot)
                    and hashlib.sha256(retry_patch.encode()).hexdigest() == retry_plan.get("task_patch_hash")
                )
                if not valid_unknown:
                    raise ValueError("unknown evidence mismatch")
                for artifact in expected_artifacts.values():
                    artifact.unlink()
                root.rmdir()
            except (OSError, ValueError, KeyError, TypeError, DeliveryError) as exc:
                raise DeliveryError("prepare_retry_artifact_unsafe", "遗留交付证据不满足安全重试条件。") from exc
        _make_private_directory(root)
        transaction_id: Optional[int] = None
        store_add_started = False
        try:
            plan_path, snapshot_path, patch_path, journal_path = root / "delivery_plan.json", root / "repository_snapshot.json", root / "expected_task.diff", root / "journal.json"
            _atomic_json(plan_path, plan)
            _atomic_json(snapshot_path, snapshot)
            _atomic_text(patch_path, request.expected_diff)
            # The provisional journal is durable before any store write, so a
            # later persistence failure has a complete retry/recovery trail.
            journal = {"schema_version": DELIVERY_SCHEMA_VERSION, "transaction_key": key, "transaction_id": None, "state": "prepare_pending_store", "plan_hash": plan["plan_hash"], "plan_path": str(plan_path), "snapshot_path": str(snapshot_path), "patch_path": str(patch_path), "release_acceptance": {}, "rc_acceptance": {}, "checkpoints": [{"state": "prepare_pending_store", "at": _now_iso(), "head": snapshot["head"]}]}
            _atomic_json(journal_path, journal)
            store_add_started = True
            transaction_id = self.store.add_transaction({"transaction_key": key, "task_id": request.task_id, "source_run_id": request.source_run_id, "entity_kind": request.entity_kind, "entity_id": request.entity_id, "project_path": str(Path(request.project_path).resolve()), "state": "waiting_release_runtime_acceptance", "plan_hash": plan["plan_hash"], "policy_snapshot": policy_snapshot(self.policy), "repository_snapshot": snapshot, "output_dir": str(Path(request.output_dir).resolve()), "journal_path": str(journal_path)})
            journal = {"schema_version": DELIVERY_SCHEMA_VERSION, "transaction_key": key, "transaction_id": transaction_id, "state": "waiting_release_runtime_acceptance", "plan_hash": plan["plan_hash"], "plan_path": str(plan_path), "snapshot_path": str(snapshot_path), "patch_path": str(patch_path), "release_acceptance": {}, "rc_acceptance": {}, "checkpoints": [{"state": "waiting_release_runtime_acceptance", "at": _now_iso(), "head": snapshot["head"]}]}
            _atomic_json(journal_path, journal)
            self.store.add_event({"transaction_id": transaction_id, "event_type": "planned", "status": "success", "input_hash": stable_hash(plan), "details": {"plan_hash": plan["plan_hash"], "remote_actions_enabled": False}})
            self._prepare_state_change(transaction_id, "waiting_release_runtime_acceptance", journal_path, journal)
            transaction = self.store.get_transaction(transaction_id)
            if transaction is None:
                raise DeliveryError("transaction_persistence_failed", "交付事务写入后无法回读。")
            return {"transaction": transaction, "plan": plan, "snapshot": snapshot, "idempotent": False}
        except Exception as original:
            if isinstance(original, DeliveryError) and original.code == "prepare_callback_ambiguous":
                raise
            if transaction_id is None:
                try:
                    recovered = self.store.get_by_key(key)
                    transaction_id = int(recovered["id"]) if recovered is not None else None
                except Exception:
                    transaction_id = None
            if transaction_id is not None:
                try:
                    # Keep all paths and the provisional journal: generic
                    # seven-method stores can recover without a delete API.
                    self.store.update_transaction(transaction_id, state="prepare_recovery_required", last_error="prepare_persistence_failed")
                except Exception:
                    pass
            elif store_add_started:
                # An add outcome that cannot be read back is ambiguous.  Keep
                # every private artifact so a later store recovery can bind it.
                try:
                    journal["state"] = "prepare_store_unknown"
                    journal["checkpoints"].append({"state": "prepare_store_unknown", "at": _now_iso()})
                    _atomic_json(journal_path, journal)
                except Exception:
                    pass
                raise DeliveryError("prepare_store_unknown", "交付存储写入结果未知；已保留私有恢复证据。") from original
            else:
                try:
                    _assert_safe_artifact_ancestry(root)
                    for child in root.iterdir():
                        child.unlink()
                    root.rmdir()
                except OSError:
                    pass
            if isinstance(original, DeliveryError):
                raise
            raise DeliveryError("prepare_persistence_failed", "交付准备未能原子完成，未发布任何任务分支。") from original

    def _context(self, transaction: Mapping[str, Any]) -> dict[str, Any]:
        journal_path = Path(str(transaction.get("journal_path") or ""))
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            plan = json.loads(Path(str(journal["plan_path"])).read_text(encoding="utf-8"))
            snapshot = json.loads(Path(str(journal["snapshot_path"])).read_text(encoding="utf-8"))
            expected = Path(str(journal["patch_path"])).read_text(encoding="utf-8")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DeliveryError("delivery_journal_missing", "交付证据不可读取。") from exc
        if journal.get("transaction_id") != transaction.get("id") or journal.get("transaction_key") != transaction.get("transaction_key"):
            raise DeliveryError("delivery_journal_mismatch", "交付 journal 与事务身份不一致。")
        if journal.get("plan_hash") != transaction.get("plan_hash") or plan.get("plan_hash") != transaction.get("plan_hash") or stable_hash({k: v for k, v in plan.items() if k != "plan_hash"}) != plan.get("plan_hash") or hashlib.sha256(expected.encode()).hexdigest() != plan.get("task_patch_hash"):
            raise DeliveryError("delivery_plan_hash_mismatch", "交付计划内容与不可变 plan hash 不一致。")
        if plan.get("repository_snapshot_hash") != stable_hash(snapshot) or stable_hash(transaction.get("repository_snapshot") or {}) != plan.get("repository_snapshot_hash") or transaction.get("repository_snapshot") != snapshot:
            raise DeliveryError("delivery_snapshot_hash_mismatch", "交付 snapshot 与计划或存储证据不一致。")
        return {"transaction": dict(transaction), "journal": journal, "plan": plan, "snapshot": snapshot, "expected_diff": expected}

    def show(self, transaction_id: int) -> dict[str, Any]:
        transaction = self.store.get_transaction(transaction_id)
        if transaction is None:
            raise DeliveryError("transaction_not_found", "未找到交付事务。")
        context = self._context(transaction)
        return {"transaction": context["transaction"], "plan": context["plan"], "snapshot": context["snapshot"], "events": self.store.get_events(transaction_id)}

    def record_runtime_acceptance(self, transaction_id: int, *, phase: str, status: str, summary: str, verifier: str = "user") -> dict[str, Any]:
        if phase not in {"release", "rc"} or status not in {"passed", "failed"} or not summary.strip():
            raise DeliveryError("invalid_acceptance", "运行时验收参数无效。")
        transaction = self.store.get_transaction(transaction_id)
        if transaction is None:
            raise DeliveryError("transaction_not_found", "未找到交付事务。")
        context = self._context(transaction)
        plan, project = context["plan"], Path(transaction["project_path"])
        _audit_repository_automation(project)
        if (project / ".gitmodules").exists():
            raise DeliveryError("repository_automation_unsupported", "仓库包含 submodule 配置。")
        if phase == "release" and transaction.get("state") != "waiting_release_runtime_acceptance":
            raise DeliveryError("release_acceptance_not_ready", "release 验收只能登记一次，失败后必须重新 prepare。")
        if phase == "rc" and transaction.get("state") != "waiting_rc_runtime_acceptance":
            raise DeliveryError("rc_acceptance_not_ready", "RC 验收必须等待已验证的一致性检查点。")
        branch = _required_git(project, ["branch", "--show-current"], "current_branch_unreadable")
        head = _required_git(project, ["rev-parse", "HEAD"], "current_head_unreadable")
        acceptance: dict[str, Any] = {"schema_version": "1.0-delivery-runtime-acceptance", "recorded_at": _now_iso(), "phase": phase, "status": status, "verifier": verifier.strip() or "user", "summary": summary.strip(), "branch": branch, "head": head, "plan_hash": plan["plan_hash"]}
        if phase == "release":
            current = _worktree_diff(project, list(plan["allowed_paths"]))
            acceptance.update({"task_patch_hash": hashlib.sha256(current.encode()).hexdigest(), "task_file_state_hash": _file_state_hash(project, list(plan["allowed_paths"]))})
            if status == "passed" and (branch != plan["base_branch"] or head != plan["base_head"] or acceptance["task_patch_hash"] != plan["task_patch_hash"] or acceptance["task_file_state_hash"] != plan["task_file_state_hash"]):
                raise DeliveryError("release_acceptance_drift", "计划证据已变化，拒绝登记通过。")
            state, field = ("release_runtime_accepted" if status == "passed" else "release_runtime_failed"), "release_acceptance"
        else:
            parity = transaction.get("parity_result") or {}
            if status == "passed" and (branch != plan["integration_branch"] or head != parity.get("integration_head") or parity.get("plan_hash") != plan["plan_hash"]):
                raise DeliveryError("rc_acceptance_not_ready", "RC 一致性检查点已失效。")
            acceptance["parity_hash"] = stable_hash(parity)
            state, field = ("rc_runtime_accepted" if status == "passed" else "rc_runtime_failed"), "rc_acceptance"
        self.store.update_transaction(transaction_id, state=state, **{field: acceptance}, last_error="" if status == "passed" else summary.strip())
        self.store.add_event({"transaction_id": transaction_id, "event_type": phase + "_runtime_accepted", "status": status, "input_hash": stable_hash(acceptance), "details": {"phase": phase, "head": head}})
        self.on_state_change(transaction_id, state)
        return acceptance

    def validate_runtime_acceptance(self, transaction_id: int, *, phase: str) -> dict[str, Any]:
        if phase not in {"release", "rc"}:
            raise DeliveryError("invalid_acceptance_phase", "运行时验收阶段仅支持 release 或 rc。")
        transaction = self.store.get_transaction(transaction_id)
        if transaction is None:
            raise DeliveryError("transaction_not_found", "未找到交付事务。")
        context, acceptance = self._context(transaction), transaction.get("release_acceptance" if phase == "release" else "rc_acceptance", {})
        if acceptance.get("status") != "passed":
            return {"valid": False, "phase": phase, "reasons": ["acceptance_not_passed"]}
        project, plan = Path(transaction["project_path"]), context["plan"]
        reasons = []
        branch, head = _required_git(project, ["branch", "--show-current"], "current_branch_unreadable"), _required_git(project, ["rev-parse", "HEAD"], "current_head_unreadable")
        if branch != acceptance.get("branch"): reasons.append("branch_drift")
        if head != acceptance.get("head"): reasons.append("head_drift")
        if phase == "release" and (hashlib.sha256(_worktree_diff(project, list(plan["allowed_paths"])).encode()).hexdigest() != acceptance.get("task_patch_hash") or _file_state_hash(project, list(plan["allowed_paths"])) != acceptance.get("task_file_state_hash")):
            reasons.append("task_patch_drift")
        if phase == "rc":
            parity = transaction.get("parity_result") or {}
            if branch != plan["integration_branch"] or head != parity.get("integration_head") or stable_hash(parity) != acceptance.get("parity_hash"):
                reasons.append("rc_parity_drift")
        return {"valid": not reasons, "phase": phase, "reasons": reasons, "current_branch": branch, "current_head": head}

    def record_rc_integration_checkpoint(self, transaction_id: int, *, evidence: Mapping[str, Any]) -> dict[str, Any]:
        """Record injected RC parity evidence; this method never integrates or pushes."""
        if not isinstance(evidence, Mapping) or set(evidence) != {"integration_head", "parity"} or not isinstance(evidence.get("integration_head"), str) or not isinstance(evidence.get("parity"), Mapping):
            raise DeliveryError("invalid_rc_checkpoint", "RC 一致性检查点格式无效。")
        transaction = self.store.get_transaction(transaction_id)
        if transaction is None:
            raise DeliveryError("transaction_not_found", "未找到交付事务。")
        if transaction.get("state") != "task_commit_created" or not transaction.get("commit_records"):
            raise DeliveryError("rc_checkpoint_not_ready", "RC 检查点必须建立在已审计任务 commit 之后。")
        context, plan, project = self._context(transaction), None, Path(transaction["project_path"])
        plan = context["plan"]
        _audit_repository_automation(project)
        branch = _required_git(project, ["branch", "--show-current"], "current_branch_unreadable")
        head = _required_git(project, ["rev-parse", "HEAD"], "current_head_unreadable")
        parity = dict(evidence["parity"])
        commit = list(transaction["commit_records"])[-1]
        if set(parity) != _RC_PARITY_FIELDS or parity.get("schema_version") != "1.0-rc-parity" or not isinstance(parity.get("changed_paths"), list):
            raise DeliveryError("invalid_rc_checkpoint", "RC 一致性证据格式无效。")
        expected_paths = sorted(list(plan["allowed_paths"]))
        if (project / ".gitmodules").exists() or branch != plan["integration_branch"] or evidence["integration_head"] != head or parity.get("rc_post_head") != head or parity.get("task_commit") != commit.get("commit") or parity.get("task_patch_hash") != plan["task_patch_hash"] or sorted(parity["changed_paths"]) != expected_paths:
            raise DeliveryError("rc_checkpoint_invalid", "RC HEAD 或一致性证据未绑定到当前任务 commit。")
        ancestor = _git(project, ["merge-base", "--is-ancestor", str(commit["commit"]), head])
        actual_paths = _changed_paths_between(project, str(commit["parent"]), str(commit["commit"]))
        actual_diff = _git(project, ["diff", "--no-textconv", "--binary", "--no-ext-diff", str(commit["parent"]), str(commit["commit"])])
        if ancestor.returncode != 0 or actual_paths != expected_paths or actual_diff.returncode != 0 or actual_diff.stdout != context["expected_diff"]:
            raise DeliveryError("rc_checkpoint_invalid", "RC 一致性证据无法由当前仓库独立验证。")
        stored = {"integration_head": head, "parity": parity, "recorded_at": _now_iso(), "plan_hash": plan["plan_hash"]}
        self.store.update_transaction(transaction_id, state="waiting_rc_runtime_acceptance", parity_result=stored, last_error="")
        self.store.add_event({"transaction_id": transaction_id, "event_type": "rc_integration_checkpoint", "status": "success", "input_hash": stable_hash(stored), "details": stored})
        self.on_state_change(transaction_id, "waiting_rc_runtime_acceptance")
        return stored

    def _append_remote_result(self, transaction_id: int, transaction: Mapping[str, Any], result: Mapping[str, Any]) -> list[dict[str, Any]]:
        records = [dict(item) for item in list(transaction.get("remote_results") or [])]
        records.append(dict(result))
        self.store.update_transaction(transaction_id, remote_results=records, last_error="")
        self.store.add_event({"transaction_id": transaction_id, "event_type": str(result.get("action") or "remote_action"), "status": "success", "input_hash": stable_hash(result), "details": dict(result)})
        return records

    def _integrate_recorded_commit(self, project: Path, plan: Mapping[str, Any], expected_diff: str, record: Mapping[str, Any]) -> dict[str, Any]:
        """Build the exact task patch on the RC head without switching the user worktree."""
        _audit_repository_automation(project)
        task_commit = str(record.get("commit") or "")
        rc_ref = "refs/heads/" + str(plan["integration_branch"])
        rc_pre_head = _required_git(project, ["rev-parse", rc_ref], "integration_ref_unreadable")
        task_diff = _git(project, ["diff", "--no-textconv", "--binary", "--no-ext-diff", task_commit + "^", task_commit, "--", *list(plan["allowed_paths"])])
        if task_diff.returncode != 0 or task_diff.stdout != expected_diff:
            raise DeliveryError("task_commit_drift", "任务 commit 的 patch 已变化，拒绝集成到 RC。")
        integration_commit = ""
        with tempfile.TemporaryDirectory(prefix="his-engineering-rc-integration-", dir=_trusted_temporary_root()) as temporary:
            worktree = Path(temporary) / "worktree"
            worktree.mkdir(mode=0o700)
            with _isolated_git(project, work_tree=worktree) as (run, private_dir):
                setup = (
                    ["read-tree", rc_pre_head],
                    ["checkout-index", "--all", "--prefix=" + str(worktree) + "/"],
                )
                for args in setup:
                    result = run(args)
                    if result.returncode != 0:
                        raise DeliveryError("rc_integration_prepare_failed", "无法构造隔离 RC 集成环境。")
                for args in (["apply", "--check", "--recount", "-"], ["apply", "--recount", "-"]):
                    result = run(args, input_text=expected_diff)
                    if result.returncode != 0:
                        raise DeliveryError("rc_cherry_pick_conflict", "任务 patch 无法安全应用到 RC，未更新 RC 分支。")
                staged = run(["add", "--", *list(plan["allowed_paths"])], include_alternates=False)
                diff = run(["diff", "--cached", "--binary", "--no-ext-diff", rc_pre_head, "--", *list(plan["allowed_paths"])])
                if staged.returncode != 0 or diff.returncode != 0 or diff.stdout != expected_diff:
                    raise DeliveryError("rc_integration_patch_mismatch", "隔离 RC 集成结果与任务 patch 不一致。")
                tree = run(["write-tree", "--missing-ok"], include_alternates=False)
                commit = run(["-c", "user.name=HIS Engineering", "-c", "user.email=his-engineering@local.invalid", "commit-tree", tree.stdout.strip(), "-p", rc_pre_head, "-m", str(record["message"])])
                if tree.returncode != 0 or commit.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit.stdout.strip()):
                    raise DeliveryError("rc_integration_failed", "无法创建 RC 集成 commit。")
                integration_commit = commit.stdout.strip()
                _import_private_objects(project, private_dir, integration_commit, rc_pre_head, run)
        observed = _required_git(project, ["rev-parse", rc_ref], "integration_ref_unreadable")
        if observed != rc_pre_head:
            raise DeliveryError("rc_ref_drift", "RC 本地分支已变化，拒绝发布集成结果。")
        published = _git(project, ["update-ref", rc_ref, integration_commit, rc_pre_head])
        if published.returncode != 0:
            raise DeliveryError("rc_ref_publish_failed", "无法安全发布 RC 集成结果。", details={"repository_mutation_attempted": True})
        return {
            "task_commit": task_commit,
            "rc_pre_head": rc_pre_head,
            "integration_head": integration_commit,
            "changed_paths": list(plan["allowed_paths"]),
            "integrated_at": _now_iso(),
        }

    def execute_pre_rc_remote_phase(self, transaction_id: int, *, approved_plan_hash: str) -> dict[str, Any]:
        """Use the already-approved plan to push the task and construct RC once."""
        transaction = self.store.get_transaction(transaction_id)
        if transaction is None:
            raise DeliveryError("transaction_not_found", "未找到交付事务。")
        context = self._context(transaction)
        plan = context["plan"]
        if approved_plan_hash != plan["plan_hash"]:
            raise DeliveryError("plan_hash_not_approved", "确认的 plan hash 与当前交付计划不一致。")
        actions = plan.get("actions") or {}
        if transaction.get("state") == "waiting_rc_runtime_acceptance":
            return {"state": "waiting_rc_runtime_acceptance", "idempotent": True, "task_push": {"pushed": bool(actions.get("push_feature"))}, "integration": dict(transaction.get("parity_result") or {})}
        if transaction.get("state") != "task_commit_created" or not transaction.get("commit_records"):
            raise DeliveryError("remote_phase_not_ready", "远端交付必须建立在已验证的任务 commit 之后。")
        record = list(transaction["commit_records"])[-1]
        project = Path(transaction["project_path"])
        self._validate_recorded_commit(project, plan, context["expected_diff"], record)
        if not actions.get("push_feature") and not actions.get("cherry_pick_integration"):
            return {"state": "task_commit_created", "idempotent": False, "task_push": {"pushed": False}, "integration": {"integrated": False}}
        remote_url = _approved_remote_url(project, str(plan["remote"]))
        task_push: dict[str, Any] = {"pushed": False}
        integration: dict[str, Any] = {"integrated": False}
        try:
            if actions.get("push_feature"):
                task_ref = _delivery_ref("refs/heads/" + str(plan["task_branch"]))
                receipt = _push_verified_ref(project, remote_url=remote_url, source_ref=task_ref, target_ref=task_ref, expected_remote=None)
                task_push = {"pushed": True, **receipt}
                self._append_remote_result(transaction_id, transaction, {"action": "task_branch_push", **task_push})
                transaction = self.store.get_transaction(transaction_id) or transaction
            if actions.get("cherry_pick_integration"):
                rc_ref = _delivery_ref("refs/heads/" + str(plan["integration_branch"]))
                local_rc_head = _required_git(project, ["rev-parse", rc_ref], "integration_ref_unreadable")
                if _read_remote_ref(project, remote_url, rc_ref) != local_rc_head:
                    raise DeliveryError("rc_remote_ref_drift", "远端 RC 与本地基线不一致，拒绝集成。")
                integration = self._integrate_recorded_commit(project, plan, context["expected_diff"], record)
                parity = {
                    "schema_version": "1.0-rc-parity",
                    "rc_post_head": integration["integration_head"],
                    "task_commit": integration["task_commit"],
                    "task_patch_hash": plan["task_patch_hash"],
                    "changed_paths": integration["changed_paths"],
                }
                stored = {"integration_head": integration["integration_head"], "parity": parity, "recorded_at": _now_iso(), "plan_hash": plan["plan_hash"]}
                records = [dict(item) for item in list((self.store.get_transaction(transaction_id) or transaction).get("remote_results") or [])]
                records.append({"action": "rc_integration", **integration})
                self.store.update_transaction(transaction_id, state="waiting_rc_runtime_acceptance", parity_result=stored, remote_results=records, last_error="")
                self.store.add_event({"transaction_id": transaction_id, "event_type": "rc_integration", "status": "success", "input_hash": stable_hash(integration), "details": integration})
                self.on_state_change(transaction_id, "waiting_rc_runtime_acceptance")
            return {"state": "waiting_rc_runtime_acceptance" if actions.get("cherry_pick_integration") else "task_commit_created", "idempotent": False, "task_push": task_push, "integration": integration}
        except DeliveryError as exc:
            if exc.details.get("remote_dispatch_attempted"):
                self.store.update_transaction(transaction_id, state="recovery_required", last_error=exc.code)
                self.store.add_event({"transaction_id": transaction_id, "event_type": "remote_delivery_recovery_required", "status": "recovery_required", "input_hash": stable_hash({"code": exc.code}), "details": {"code": exc.code, **exc.details}})
                self.on_state_change(transaction_id, "recovery_required")
            raise

    def execute_stage_one(self, transaction_id: int, *, approved_plan_hash: str) -> dict[str, Any]:
        transaction = self.store.get_transaction(transaction_id)
        if transaction is None:
            raise DeliveryError("transaction_not_found", "未找到交付事务。")
        context = self._context(transaction)
        plan = context["plan"]
        if approved_plan_hash != plan["plan_hash"]:
            raise DeliveryError("plan_hash_not_approved", "确认的 plan hash 与当前交付计划不一致。")
        records = list(transaction.get("commit_records") or [])
        if records:
            if transaction.get("state") == "recovery_required":
                raise DeliveryError("recovery_required", "此前本地分支发布后的恢复状态尚未确认。", details={"commit": records[-1]})
            if transaction.get("state") not in {"task_commit_created", "waiting_rc_runtime_acceptance", "rc_runtime_accepted"}:
                raise DeliveryError("idempotent_state_incompatible", "当前事务状态不允许返回幂等任务 commit。")
            self._validate_recorded_commit(Path(transaction["project_path"]), plan, context["expected_diff"], records[-1])
            return {"state": str(transaction["state"]), "commit": records[-1], "idempotent": True, "repository_mutation_attempted": False, "remote_actions_blocked": True}
        validation = self.validate_runtime_acceptance(transaction_id, phase="release")
        if transaction.get("state") != "release_runtime_accepted" or not validation["valid"]:
            raise DeliveryError("release_acceptance_invalid", "release 运行时验收不是当前有效通过状态。", details=validation)
        if plan.get("verify_commands"):
            raise DeliveryError("verification_unavailable", "首版无法在等效隔离边界执行专项验证，拒绝创建本地分支。")
        try:
            commit = self._commit_in_temporary_worktree(transaction_id, Path(transaction["project_path"]), plan, context["snapshot"], context["expected_diff"])
        except DeliveryError as exc:
            published = exc.details.get("published_commit") if isinstance(exc.details, dict) else None
            candidate = published if isinstance(published, dict) else (exc.details.get("candidate_commit") if isinstance(exc.details, dict) else None)
            if isinstance(candidate, dict):
                persisted = False
                try:
                    self.store.update_transaction(transaction_id, state="recovery_required", commit_records=[candidate], last_error=exc.code)
                    persisted = True
                except Exception:
                    persisted = False
                if persisted:
                    try:
                        self.store.add_event({"transaction_id": transaction_id, "event_type": "task_commit_recovery_required", "status": "recovery_required", "input_hash": stable_hash(candidate), "details": {"commit": candidate, "cause": exc.code, "ref_observation": exc.details.get("ref_observation")}})
                    except Exception:
                        pass
                    try:
                        self.on_state_change(transaction_id, "recovery_required")
                    except Exception:
                        pass
                exc.details["recovery_persisted"] = persisted
            elif isinstance(exc.details, dict):
                exc.details.setdefault("recovery_persisted", False)
            raise
        try:
            self.store.update_transaction(transaction_id, state="task_commit_created", commit_records=[commit], last_error="")
            self.store.add_event({"transaction_id": transaction_id, "event_type": "task_commit_created", "status": "success", "input_hash": stable_hash(commit), "details": commit})
            self.on_state_change(transaction_id, "task_commit_created")
        except Exception as exc:
            persisted = False
            try:
                self.store.update_transaction(transaction_id, state="recovery_required", commit_records=[commit], last_error="post_publish_persistence_failed")
                persisted = True
            except Exception:
                persisted = False
            if persisted:
                try:
                    self.store.add_event({"transaction_id": transaction_id, "event_type": "task_commit_recovery_required", "status": "recovery_required", "input_hash": stable_hash(commit), "details": {"commit": commit, "cause": "post_publish_persistence_failed"}})
                except Exception:
                    pass
                try:
                    self.on_state_change(transaction_id, "recovery_required")
                except Exception:
                    pass
            raise DeliveryError("post_publish_recovery_required", "本地任务分支已发布，但交付记录未完整保存。", details={"published_commit": commit, "recovery_persisted": persisted}) from exc
        return {"state": "task_commit_created", "commit": commit, "idempotent": False, "repository_mutation_attempted": True, "remote_actions_blocked": True}

    def _validate_recorded_commit(self, project: Path, plan: Mapping[str, Any], expected_diff: str, record: Mapping[str, Any]) -> None:
        _audit_repository_automation(project)
        commit_id = str(record.get("commit") or "")
        branch = str(record.get("branch") or "")
        if (not commit_id or branch != plan["task_branch"] or record.get("parent") != plan["base_head"] or record.get("message") != plan["commit_message"] or record.get("patch_hash") != hashlib.sha256(expected_diff.encode()).hexdigest() or _required_git(project, ["rev-parse", "refs/heads/" + branch], "task_commit_drift") != commit_id):
            raise DeliveryError("task_commit_drift", "已记录的任务分支引用已变化。")
        parent = _required_git(project, ["rev-parse", commit_id + "^"], "task_commit_drift")
        subject = _required_git(project, ["log", "-1", "--format=%s", commit_id], "task_commit_drift")
        diff = _git(project, ["diff", "--no-textconv", "--binary", "--no-ext-diff", commit_id + "^", commit_id, "--", *list(plan["allowed_paths"])])
        expected_paths = sorted(set(_patch_paths(expected_diff)))
        if parent != plan["base_head"] or subject != plan["commit_message"] or diff.returncode != 0 or diff.stdout != expected_diff or _changed_paths_between(project, commit_id + "^", commit_id) != expected_paths or expected_paths != sorted(set(plan["allowed_paths"])):
            raise DeliveryError("task_commit_drift", "已记录任务 commit 的父提交、说明或 patch 已变化。")

    def _assert_prepublication_evidence(self, transaction_id: int, project: Path, plan: Mapping[str, Any], snapshot: Mapping[str, Any], expected_diff: str) -> None:
        """Close the final TOCTOU window immediately before publishing a ref."""
        _audit_repository_automation(project)
        current = self.store.get_transaction(transaction_id)
        if current is None:
            raise DeliveryError("repository_drift_detected", "交付事务在发布前不可读取。")
        context = self._context(current)
        acceptance = current.get("release_acceptance") or {}
        if (context["plan"].get("plan_hash") != plan.get("plan_hash") or current.get("state") != "release_runtime_accepted" or acceptance.get("status") != "passed" or acceptance.get("plan_hash") != plan.get("plan_hash") or snapshot.get("repository_identity") != _repository_identity(project)):
            raise DeliveryError("repository_drift_detected", "发布任务分支前仓库身份或验收计划已变化。")
        branch = _required_git(project, ["branch", "--show-current"], "current_branch_unreadable")
        head = _required_git(project, ["rev-parse", "HEAD"], "current_head_unreadable")
        current_patch = _worktree_diff(project, list(plan["allowed_paths"]))
        current_file_state = _file_state_hash(project, list(plan["allowed_paths"]))
        if (branch != plan["base_branch"] or head != plan["base_head"] or branch != acceptance.get("branch") or head != acceptance.get("head") or hashlib.sha256(current_patch.encode()).hexdigest() != plan["task_patch_hash"] or hashlib.sha256(current_patch.encode()).hexdigest() != acceptance.get("task_patch_hash") or current_file_state != plan["task_file_state_hash"] or current_file_state != acceptance.get("task_file_state_hash") or current_patch != expected_diff):
            raise DeliveryError("repository_drift_detected", "发布任务分支前工作区 patch 或文件状态已变化。")

    def _commit_in_temporary_worktree(self, transaction_id: int, project: Path, plan: Mapping[str, Any], snapshot: Mapping[str, Any], expected_diff: str) -> dict[str, Any]:
        _audit_repository_automation(project)
        if (project / ".gitmodules").exists():
            raise DeliveryError("repository_automation_unsupported", "仓库包含 submodule 配置。")
        if _required_git(project, ["rev-parse", "HEAD"], "current_head_unreadable") != plan["base_head"] or _required_git(project, ["branch", "--show-current"], "current_branch_unreadable") != plan["base_branch"]:
            raise DeliveryError("repository_drift_detected", "release 分支或 HEAD 已变化。")
        existing = _git(project, ["show-ref", "--verify", "--quiet", "refs/heads/" + str(plan["task_branch"])])
        if existing.returncode == 0:
            raise DeliveryError("task_branch_exists", "任务分支已存在，拒绝覆盖。")
        published_commit: Optional[dict[str, Any]] = None
        mutation_attempted = False
        try:
            # All checkout/index/add/commit construction has an independent
            # GIT_DIR/config.  Target .git/config is only audited, never read
            # by these commands; its object store is explicitly validated.
            with tempfile.TemporaryDirectory(
                prefix="his-engineering-isolated-worktree-",
                dir=_trusted_temporary_root(),
            ) as temporary:
                worktree = Path(temporary) / "worktree"
                worktree.mkdir(mode=0o700)
                with _isolated_git(project, work_tree=worktree) as (run, private_dir):
                    mutation_attempted = True
                    _audit_repository_automation(project)
                    if run(["read-tree", str(plan["base_head"])]).returncode != 0 or run(["checkout-index", "--all", "--prefix=" + str(worktree) + "/"]).returncode != 0:
                        raise DeliveryError("task_patch_apply_failed", "隔离 Git 环境无法检出基线文件。")
                    for args in (["apply", "--check", "--recount", "-"], ["apply", "--recount", "-"]):
                        _audit_repository_automation(project)
                        result = run(args, input_text=expected_diff)
                        if result.returncode != 0:
                            raise DeliveryError("task_patch_apply_failed", "隔离 Git 环境无法应用任务 patch。")
                    _audit_repository_automation(project)
                    staged = run(["add", "--", *list(plan["allowed_paths"])], include_alternates=False)
                    if staged.returncode != 0:
                        raise DeliveryError("task_stage_failed", "无法在隔离 Git index 暂存任务文件。")
                    diff = run(["diff", "--cached", "--binary", "--no-ext-diff", str(plan["base_head"]), "--", *list(plan["allowed_paths"])])
                    if diff.returncode != 0 or diff.stdout != expected_diff:
                        raise DeliveryError("staged_task_patch_mismatch", "隔离暂存 patch 与计划不一致。")
                    tree = run(["write-tree", "--missing-ok"], include_alternates=False)
                    commit = run(["-c", "user.name=HIS Engineering", "-c", "user.email=his-engineering@local.invalid", "commit-tree", tree.stdout.strip(), "-p", str(plan["base_head"]), "-m", str(plan["commit_message"])])
                    if tree.returncode != 0 or commit.returncode != 0 or not commit.stdout.strip():
                        raise DeliveryError("task_commit_failed", "无法在隔离 Git 环境创建本地任务 commit。")
                    commit_id = commit.stdout.strip()
                    published_commit = {"branch": plan["task_branch"], "commit": commit_id, "parent": str(plan["base_head"]), "message": plan["commit_message"], "patch_hash": hashlib.sha256(expected_diff.encode()).hexdigest(), "remote_pushed": False, "created_at": _now_iso()}
                    # No target object/ref mutation occurs before this final
                    # evidence check; private objects are disposable.
                    self._assert_prepublication_evidence(transaction_id, project, plan, snapshot, expected_diff)
                    _import_private_objects(project, private_dir, commit_id, str(plan["base_head"]), run)
        except DeliveryError as exc:
            if mutation_attempted:
                exc.details.setdefault("repository_mutation_attempted", True)
            raise
        except Exception as exc:
            raise DeliveryError("task_commit_failed", "隔离 Git 构造异常中断。", details={"repository_mutation_attempted": mutation_attempted}) from exc
        if published_commit is None:
            raise DeliveryError("task_commit_failed", "无法创建本地任务 commit。", details={"repository_mutation_attempted": mutation_attempted})
        # The disposable worktree is gone before the ref becomes visible; CAS
        # still prevents overwriting a concurrent task branch.
        try:
            self._assert_prepublication_evidence(transaction_id, project, plan, snapshot, expected_diff)
            published = _git(project, ["update-ref", "refs/heads/" + str(plan["task_branch"]), published_commit["commit"], _null_object_id(_repository_object_format(project))])
            if published.returncode != 0:
                observed_state, observed_value = _observe_task_ref(project, str(plan["task_branch"]))
                details = {"candidate_commit": published_commit, "repository_mutation_attempted": True, "repository_changed": True, "ref_observation": {"state": observed_state, "value": observed_value}}
                if observed_state == "present" and observed_value == published_commit["commit"]:
                    details["published_commit"] = published_commit
                    raise DeliveryError("task_branch_publish_uncertain", "本地任务分支发布结果不确定，已检测到目标引用。", details=details)
                if observed_state == "present":
                    raise DeliveryError("task_branch_publish_diverged", "本地任务分支引用已出现非预期值。", details=details)
                raise DeliveryError("task_branch_publish_failed", "无法安全发布本地任务分支，目标对象可能已导入。", details=details)
            return published_commit
        except DeliveryError as exc:
            exc.details.setdefault("repository_mutation_attempted", True)
            exc.details.setdefault("repository_changed", True)
            raise
        except Exception as exc:
            try:
                observed_state, observed_value = _observe_task_ref(project, str(plan["task_branch"]))
            except Exception:
                observed_state, observed_value = "unknown", ""
            details = {"repository_mutation_attempted": True, "repository_changed": True, "candidate_commit": published_commit, "ref_observation": {"state": observed_state, "value": observed_value}}
            if observed_state == "present" and observed_value == published_commit["commit"]:
                details["published_commit"] = published_commit
            raise DeliveryError("task_branch_publish_uncertain", "本地任务分支发布步骤异常中断。", details=details) from exc

    def execute_stage_two(
        self,
        transaction_id: int,
        *,
        approved_plan_hash: str,
        execute_gitlab_action: Callable[..., Mapping[str, Any]] | None = None,
        execute_github_action: Callable[..., Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        transaction = self.store.get_transaction(transaction_id)
        if transaction is None:
            raise DeliveryError("transaction_not_found", "未找到交付事务。")
        context = self._context(transaction)
        plan = context["plan"]
        if approved_plan_hash != plan["plan_hash"]:
            raise DeliveryError("plan_hash_not_approved", "确认的 plan hash 与当前交付计划不一致。")
        if transaction.get("state") == "completed":
            return {"state": "completed", "idempotent": True, "rc_push": {"pushed": bool((plan.get("actions") or {}).get("push_integration"))}}
        if transaction.get("state") != "rc_runtime_accepted":
            return {"state": str(transaction["state"]), "status": "blocked", "code": "rc_runtime_acceptance_pending", "changed": False, "external_write_attempted": False, "message": "RC 运行时验收未通过或已失效，未执行后续交付。"}
        actions = plan.get("actions") or {}
        rc_push: dict[str, Any] = {"pushed": False}
        project = Path(transaction["project_path"])
        try:
            if actions.get("push_integration"):
                parity = transaction.get("parity_result") or {}
                integration_head = str(parity.get("integration_head") or "")
                rc_ref = _delivery_ref("refs/heads/" + str(plan["integration_branch"]))
                remote_url = _approved_remote_url(project, str(plan["remote"]))
                if _required_git(project, ["rev-parse", rc_ref], "integration_ref_unreadable") != integration_head:
                    raise DeliveryError("rc_ref_drift", "RC 本地分支已变化，拒绝推送。")
                rc_pre_head = _required_git(project, ["rev-parse", integration_head + "^"], "integration_ref_unreadable")
                receipt = _push_verified_ref(project, remote_url=remote_url, source_ref=rc_ref, target_ref=rc_ref, expected_remote=rc_pre_head)
                rc_push = {"pushed": True, **receipt}
                self._append_remote_result(transaction_id, transaction, {"action": "rc_push", **rc_push})
            if actions.get("gitlab_write"):
                pending = {
                    "state": "gitlab_delivery_pending",
                    "rc_push": rc_push,
                    "gitlab_action": actions["gitlab_write"],
                }
                self.store.update_transaction(transaction_id, state="gitlab_delivery_pending", last_error="")
                self.store.add_event({"transaction_id": transaction_id, "event_type": "gitlab_delivery_pending", "status": "pending", "input_hash": stable_hash(pending), "details": pending})
                self.on_state_change(transaction_id, "gitlab_delivery_pending")
                if execute_gitlab_action is not None:
                    receipt = execute_gitlab_action(
                        transaction_id=transaction_id,
                        approved_plan_hash=approved_plan_hash,
                        gitlab_action=dict(actions["gitlab_write"]),
                        plan=dict(plan),
                    )
                    return self.complete_declared_gitlab_action(
                        transaction_id,
                        approved_plan_hash=approved_plan_hash,
                        receipt=receipt,
                    )
                return pending
            if actions.get("github_write"):
                pending = {
                    "state": "github_delivery_pending",
                    "rc_push": rc_push,
                    "github_action": actions["github_write"],
                }
                self.store.update_transaction(transaction_id, state="github_delivery_pending", last_error="")
                self.store.add_event({"transaction_id": transaction_id, "event_type": "github_delivery_pending", "status": "pending", "input_hash": stable_hash(pending), "details": pending})
                self.on_state_change(transaction_id, "github_delivery_pending")
                if execute_github_action is not None:
                    receipt = execute_github_action(
                        transaction_id=transaction_id,
                        approved_plan_hash=approved_plan_hash,
                        github_action=dict(actions["github_write"]),
                        plan=dict(plan),
                    )
                    return self.complete_declared_github_action(
                        transaction_id,
                        approved_plan_hash=approved_plan_hash,
                        receipt=receipt,
                    )
                return pending
            self.store.update_transaction(transaction_id, state="completed", last_error="")
            completed = {"state": "completed", "rc_push": rc_push, "completed_at": _now_iso()}
            self.store.add_event({"transaction_id": transaction_id, "event_type": "completed", "status": "success", "input_hash": stable_hash(completed), "details": completed})
            self.on_state_change(transaction_id, "completed")
            return completed
        except DeliveryError as exc:
            if exc.details.get("remote_dispatch_attempted"):
                self.store.update_transaction(transaction_id, state="recovery_required", last_error=exc.code)
                self.store.add_event({"transaction_id": transaction_id, "event_type": "remote_delivery_recovery_required", "status": "recovery_required", "input_hash": stable_hash({"code": exc.code}), "details": {"code": exc.code, **exc.details}})
                self.on_state_change(transaction_id, "recovery_required")
            raise

    def complete_declared_gitlab_action(self, transaction_id: int, *, approved_plan_hash: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Close a plan only after the bounded GitLab executor reports GET-verified success."""
        transaction = self.store.get_transaction(transaction_id)
        if transaction is None:
            raise DeliveryError("transaction_not_found", "未找到交付事务。")
        context = self._context(transaction)
        plan = context["plan"]
        action = (plan.get("actions") or {}).get("gitlab_write")
        if approved_plan_hash != plan["plan_hash"]:
            raise DeliveryError("plan_hash_not_approved", "确认的 plan hash 与当前交付计划不一致。")
        if transaction.get("state") != "gitlab_delivery_pending" or not isinstance(action, Mapping):
            raise DeliveryError("gitlab_completion_not_ready", "GitLab 写入不在可完成状态。")
        verified_target = _verified_gitlab_receipt_target(action, receipt) if isinstance(receipt, Mapping) else None
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("action") != action.get("action")
            or receipt.get("write_effect_status") != "verified_applied"
            or receipt.get("status") != "success"
            or verified_target is None
        ):
            raise DeliveryError(
                "gitlab_write_unverified",
                "GitLab 写入没有通过受控回读验证。",
                details={
                    "remote_dispatch_attempted": bool(
                        isinstance(receipt, Mapping)
                        and receipt.get("remote_dispatch_attempted")
                    ),
                },
            )
        safe_receipt = {
            "action": "gitlab_write",
            "declared_action": str(action["action"]),
            "target_alias": verified_target,
            "write_effect_status": "verified_applied",
            "completed_at": _now_iso(),
        }
        records = [dict(item) for item in list(transaction.get("remote_results") or [])]
        records.append(safe_receipt)
        completed = {"state": "completed", "gitlab_write": safe_receipt, "completed_at": _now_iso()}
        self.store.update_transaction(transaction_id, state="completed", remote_results=records, last_error="")
        self.store.add_event({"transaction_id": transaction_id, "event_type": "completed", "status": "success", "input_hash": stable_hash(completed), "details": completed})
        self.on_state_change(transaction_id, "completed")
        return completed

    def complete_declared_github_action(self, transaction_id: int, *, approved_plan_hash: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Close a plan only after the bounded GitHub executor reports GET-verified success."""
        transaction = self.store.get_transaction(transaction_id)
        if transaction is None:
            raise DeliveryError("transaction_not_found", "未找到交付事务。")
        context = self._context(transaction)
        plan = context["plan"]
        action = (plan.get("actions") or {}).get("github_write")
        if approved_plan_hash != plan["plan_hash"]:
            raise DeliveryError("plan_hash_not_approved", "确认的 plan hash 与当前交付计划不一致。")
        if transaction.get("state") != "github_delivery_pending" or not isinstance(action, Mapping):
            raise DeliveryError("github_completion_not_ready", "GitHub 写入不在可完成状态。")
        verified_target = _verified_github_receipt_target(action, receipt) if isinstance(receipt, Mapping) else None
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("action") != action.get("action")
            or receipt.get("write_effect_status") != "verified_applied"
            or receipt.get("status") != "success"
            or verified_target is None
        ):
            raise DeliveryError(
                "github_write_unverified",
                "GitHub 写入没有通过受控回读验证。",
                details={
                    "remote_dispatch_attempted": bool(
                        isinstance(receipt, Mapping)
                        and receipt.get("remote_dispatch_attempted")
                    ),
                },
            )
        safe_receipt = {
            "action": "github_write",
            "declared_action": str(action["action"]),
            "target_alias": verified_target,
            "write_effect_status": "verified_applied",
            "completed_at": _now_iso(),
        }
        records = [dict(item) for item in list(transaction.get("remote_results") or [])]
        records.append(safe_receipt)
        completed = {"state": "completed", "github_write": safe_receipt, "completed_at": _now_iso()}
        self.store.update_transaction(transaction_id, state="completed", remote_results=records, last_error="")
        self.store.add_event({"transaction_id": transaction_id, "event_type": "completed", "status": "success", "input_hash": stable_hash(completed), "details": completed})
        self.on_state_change(transaction_id, "completed")
        return completed
