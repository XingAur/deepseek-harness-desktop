from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit
from collections.abc import Sequence


_ALIAS = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


@dataclass(frozen=True)
class RepositoryScope:
    """An explicit local repository boundary, resolved before Git is invoked."""

    alias: str
    root: Path
    allowed_paths: tuple[Path, ...]
    protected_branches: tuple[str, ...]
    protected_prefixes: tuple[str, ...]
    remotes: tuple[tuple[str, str], ...]
    root_identity: tuple[int, int]
    git_identity: tuple[int, int] | None

    def __init__(
        self,
        alias: str,
        root: str | Path,
        *,
        allowed_paths: Sequence[str] = (".",),
        protected_branches: Sequence[str] = ("main", "master", "develop"),
        protected_prefixes: Sequence[str] = ("release/", "hotfix/"),
        remotes: Sequence[tuple[str, str]] = (),
    ) -> None:
        if not isinstance(alias, str) or _ALIAS.fullmatch(alias) is None:
            raise ValueError("repository_scope_alias_invalid")
        configured_root = Path(root)
        if not configured_root.is_absolute() or not configured_root.is_dir():
            raise ValueError("repository_scope_root_invalid")
        resolved_root = configured_root.resolve(strict=True)
        git_dir = resolved_root / ".git"
        if git_dir.exists() and (not git_dir.is_dir() or git_dir.is_symlink()):
            raise ValueError("repository_scope_root_invalid")
        normalized_allowed = tuple(
            self._allowed_path(value, resolved_root) for value in allowed_paths
        )
        if not normalized_allowed:
            raise ValueError("repository_scope_allowed_paths_invalid")
        protected_exact = tuple(_branch_name(value) for value in protected_branches)
        protected_start = tuple(_branch_prefix(value) for value in protected_prefixes)
        safe_remotes = tuple((self._remote_alias(alias), self._remote_url(url)) for alias, url in remotes)
        if len({alias for alias, _url in safe_remotes}) != len(safe_remotes):
            raise ValueError("repository_scope_remote_invalid")
        object.__setattr__(self, "alias", alias)
        object.__setattr__(self, "root", resolved_root)
        object.__setattr__(self, "allowed_paths", normalized_allowed)
        object.__setattr__(self, "protected_branches", protected_exact)
        object.__setattr__(self, "protected_prefixes", protected_start)
        object.__setattr__(self, "remotes", safe_remotes)
        object.__setattr__(self, "root_identity", (resolved_root.stat().st_dev, resolved_root.stat().st_ino))
        object.__setattr__(self, "git_identity", (git_dir.stat().st_dev, git_dir.stat().st_ino) if git_dir.is_dir() else None)

    def assert_identity(self) -> None:
        try:
            root_stat = self.root.stat(follow_symlinks=False)
            git_stat = (self.root / ".git").stat(follow_symlinks=False)
        except OSError:
            raise ValueError("repository_scope_identity_changed") from None
        if self.git_identity is None or (root_stat.st_dev, root_stat.st_ino) != self.root_identity or (git_stat.st_dev, git_stat.st_ino) != self.git_identity:
            raise ValueError("repository_scope_identity_changed")

    def open_root_fd(self) -> int:
        """Open the configured root without following a replacement symlink."""

        try:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stat = os.fstat(fd)
        except OSError:
            raise ValueError("repository_scope_identity_changed") from None
        if (stat.st_dev, stat.st_ino) != self.root_identity:
            os.close(fd)
            raise ValueError("repository_scope_identity_changed")
        return fd

    def open_git_fd(self) -> int:
        """Return an anchored descriptor for the in-tree standalone Git dir."""

        root_fd = self.open_root_fd()
        try:
            git_fd = os.open(".git", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            stat = os.fstat(git_fd)
        except OSError:
            raise ValueError("repository_scope_identity_changed") from None
        finally:
            os.close(root_fd)
        if self.git_identity is None or (stat.st_dev, stat.st_ino) != self.git_identity:
            os.close(git_fd)
            raise ValueError("repository_scope_identity_changed")
        return git_fd

    @staticmethod
    def _allowed_path(value: str, root: Path) -> Path:
        if not isinstance(value, str) or not value or "\\" in value:
            raise ValueError("repository_scope_allowed_paths_invalid")
        candidate = Path(value)
        if candidate.is_absolute() or any(part in {"", ".."} for part in candidate.parts):
            raise ValueError("repository_scope_allowed_paths_invalid")
        normalized = root if value == "." else (root / candidate)
        resolved = normalized.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            raise ValueError("repository_scope_allowed_paths_invalid") from None
        return resolved

    def resolve_path(self, relative_path: str) -> Path:
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or relative_path == "."
            or "\\" in relative_path
        ):
            raise ValueError("repository_scope_path_invalid")
        candidate_path = Path(relative_path)
        if candidate_path.is_absolute() or any(part in {"", ".", ".."} for part in candidate_path.parts):
            raise ValueError("repository_scope_path_invalid")
        candidate = self.root / candidate_path
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise ValueError("repository_scope_symlink_escape") from None
        if not any(_within(resolved, allowed) for allowed in self.allowed_paths):
            raise ValueError("repository_scope_path_not_allowed")
        return resolved

    def relative_path(self, relative_path: str) -> str:
        return self.resolve_path(relative_path).relative_to(self.root).as_posix()

    def branch_allowed(self, branch: str) -> bool:
        return branch not in self.protected_branches and not branch.startswith(self.protected_prefixes)

    def remote_url(self, alias: str) -> str:
        for configured_alias, url in self.remotes:
            if configured_alias == alias:
                return url
        raise ValueError("repository_scope_remote_not_allowed")

    @staticmethod
    def _remote_alias(value: object) -> str:
        if not isinstance(value, str) or _ALIAS.fullmatch(value) is None:
            raise ValueError("repository_scope_remote_invalid")
        return value

    @staticmethod
    def _remote_url(value: object) -> str:
        if not isinstance(value, str) or value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("repository_scope_remote_invalid")
        parsed = urlsplit(value)
        try: port = parsed.port
        except ValueError: raise ValueError("repository_scope_remote_invalid") from None
        hostname = parsed.hostname
        if (parsed.scheme != "https" or not hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or value[len("https://"):].split("/", 1)[0] != value[len("https://"):].split("/", 1)[0].lower() or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in hostname.split("."))
                or port is not None and not 1 <= port <= 65535 or not parsed.path.startswith("/") or "//" in parsed.path or any(part in {".", ".."} for part in parsed.path.split("/"))
                or re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path)):
            raise ValueError("repository_scope_remote_invalid")
        decoded_parts = [unquote(part) for part in parsed.path.split("/")]
        if any(part in {"", ".", ".."} or "/" in part for part in decoded_parts[1:]):
            raise ValueError("repository_scope_remote_invalid")
        authority = hostname + (f":{port}" if port not in {None, 443} else "")
        return "https://" + authority + "/" + "/".join(quote(part, safe="._-~") for part in decoded_parts[1:])


def _within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _branch_name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}", value):
        raise ValueError("repository_scope_branch_policy_invalid")
    return value


def _branch_prefix(value: object) -> str:
    if not isinstance(value, str) or not value.endswith("/"):
        raise ValueError("repository_scope_branch_policy_invalid")
    return _branch_name(value[:-1]) + "/"
