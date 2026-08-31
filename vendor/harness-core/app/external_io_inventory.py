from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shlex
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


INVENTORY_SCHEMA_VERSION = "his-external-io-inventory.v1"

_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "backups",
        "data",
        "dist",
        "node_modules",
        "outputs",
        "runtime",
        "tests",
        "venv",
        "work",
    }
)
_SUPPORTED_EXECUTABLE_SUFFIXES = frozenset(
    {".bash", ".cjs", ".js", ".mjs", ".py", ".sh", ".ts", ".zsh"}
)

_PYTHON_NETWORK_CALLS = frozenset(
    {
        "aiohttp.request",
        "http.client.HTTPConnection",
        "http.client.HTTPSConnection",
        "httpx.delete",
        "httpx.get",
        "httpx.head",
        "httpx.options",
        "httpx.patch",
        "httpx.post",
        "httpx.put",
        "httpx.request",
        "requests.delete",
        "requests.get",
        "requests.head",
        "requests.options",
        "requests.patch",
        "requests.post",
        "requests.put",
        "requests.request",
        "socket.create_connection",
        "urllib.request.build_opener",
        "urllib.request.urlopen",
    }
)
_PYTHON_DATABASE_CALLS = frozenset(
    {
        "asyncpg.connect",
        "mysql.connector.connect",
        "psycopg.connect",
        "psycopg2.connect",
        "pymongo.MongoClient",
        "pymysql.connect",
        "redis.Redis",
        "redis.from_url",
        "sqlalchemy.create_engine",
    }
)
_PYTHON_PROCESS_CALLS = frozenset(
    {
        "os.popen",
        "os.spawnl",
        "os.spawnle",
        "os.spawnlp",
        "os.spawnlpe",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.spawnvpe",
        "os.system",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.run",
    }
)
_PYTHON_CREDENTIAL_CALLS = frozenset(
    {
        "keyring.get_credential",
        "keyring.get_password",
        "keyring.set_password",
    }
)

_SHELL_COMMAND_CATEGORIES = {
    "curl": "network",
    "ftp": "network",
    "nc": "network",
    "netcat": "network",
    "scp": "network",
    "sftp": "network",
    "ssh": "network",
    "wget": "network",
    "mongosh": "database",
    "mysql": "database",
    "pg_dump": "database",
    "pg_restore": "database",
    "psql": "database",
    "redis-cli": "database",
    "security": "credential",
    "bash": "process",
    "docker": "process",
    "git": "process",
    "gradle": "process",
    "java": "process",
    "mvn": "process",
    "mvnw": "process",
    "node": "process",
    "npm": "process",
    "npx": "process",
    "pnpm": "process",
    "python": "process",
    "python3": "process",
    "sh": "process",
    "yarn": "process",
    "zsh": "process",
}
_SHELL_COMMAND_PREFIXES = frozenset(
    {
        "!",
        "case",
        "do",
        "done",
        "elif",
        "else",
        "esac",
        "fi",
        "for",
        "if",
        "then",
        "until",
        "while",
        "{",
    }
)
_SHELL_COMMAND_WRAPPERS = frozenset({"command", "env", "exec", "nohup", "sudo"})
_SHELL_SEPARATORS = frozenset({"&", "&&", ";", "|", "||"})
_SHELL_CREDENTIAL_VARIABLE_PATTERN = re.compile(
    r"(?:CREDENTIAL|KEY|PASSWORD|SECRET|TOKEN)", re.IGNORECASE
)

_JAVASCRIPT_CALL_CATEGORIES = {
    "axios.delete": "network",
    "axios.get": "network",
    "axios.head": "network",
    "axios.options": "network",
    "axios.patch": "network",
    "axios.post": "network",
    "axios.put": "network",
    "axios.request": "network",
    "fetch": "network",
    "http.get": "network",
    "http.request": "network",
    "https.get": "network",
    "https.request": "network",
    "childProcess.exec": "process",
    "childProcess.execFile": "process",
    "childProcess.fork": "process",
    "childProcess.spawn": "process",
    "child_process.exec": "process",
    "child_process.execFile": "process",
    "child_process.fork": "process",
    "child_process.spawn": "process",
    "pg.connect": "database",
    "redis.createClient": "database",
    "keytar.getPassword": "credential",
    "keytar.setPassword": "credential",
}
_JAVASCRIPT_CALL_PATTERN = re.compile(
    r"(?<![\w$])([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\("
)
_SKILL_FENCE_PATTERN = re.compile(
    r"^```(?P<language>[A-Za-z0-9_-]*)[^\n]*\n(?P<body>.*?)^```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_SHELL_LANGUAGES = frozenset({"bash", "sh", "shell", "zsh"})
_JAVASCRIPT_LANGUAGES = frozenset({"javascript", "js", "mjs", "node", "ts", "typescript"})


class ExternalIoScanError(ValueError):
    """Raised when an inventory cannot be produced deterministically and safely."""


@dataclass(frozen=True)
class ScanRoot:
    root_id: str
    path: Path


@dataclass(frozen=True)
class ExternalIoFinding:
    root_id: str
    relative_path: str
    line: int
    category: str
    symbol: str
    occurrence: int
    file_sha256: str
    fingerprint: str


@dataclass(frozen=True)
class ExternalIoInventory:
    schema_version: str
    generated_at: str
    roots: tuple[dict[str, str], ...]
    findings: tuple[ExternalIoFinding, ...]


def _fingerprint(
    *,
    root_id: str,
    relative_path: str,
    category: str,
    symbol: str,
    occurrence: int,
) -> str:
    identity = {
        "category": category,
        "occurrence": occurrence,
        "relative_path": relative_path,
        "root_id": root_id,
        "symbol": symbol,
    }
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_findings(
    raw_findings: Iterable[tuple[int, str, str]],
    *,
    root_id: str,
    relative_path: str,
    file_sha256: str,
) -> tuple[ExternalIoFinding, ...]:
    occurrence_by_symbol: dict[tuple[str, str], int] = defaultdict(int)
    findings: list[ExternalIoFinding] = []
    for line, category, symbol in sorted(raw_findings, key=lambda item: (item[0], item[1], item[2])):
        key = (category, symbol)
        occurrence_by_symbol[key] += 1
        occurrence = occurrence_by_symbol[key]
        findings.append(
            ExternalIoFinding(
                root_id=root_id,
                relative_path=relative_path,
                line=line,
                category=category,
                symbol=symbol,
                occurrence=occurrence,
                file_sha256=file_sha256,
                fingerprint=_fingerprint(
                    root_id=root_id,
                    relative_path=relative_path,
                    category=category,
                    symbol=symbol,
                    occurrence=occurrence,
                ),
            )
        )
    return tuple(
        sorted(
            findings,
            key=lambda item: (
                item.category,
                item.symbol,
                item.occurrence,
                item.line,
            ),
        )
    )


def _resolve_python_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _resolve_python_name(node.value, aliases)
        if parent:
            return f"{parent}.{node.attr}"
    return None


def _classify_python_call(symbol: str) -> str | None:
    if symbol in _PYTHON_CREDENTIAL_CALLS:
        return "credential"
    if symbol in _PYTHON_DATABASE_CALLS:
        return "database"
    if symbol.endswith(".connect") and "psycopg" in symbol.lower():
        return "database"
    if symbol in _PYTHON_NETWORK_CALLS:
        return "network"
    if symbol.endswith(".open") and "opener" in symbol.lower():
        return "network"
    if symbol.endswith("opener") and symbol.startswith("self."):
        return "network"
    if symbol in _PYTHON_PROCESS_CALLS:
        return "process"
    return None


def scan_python_source(
    source: str,
    *,
    root_id: str,
    relative_path: str,
    file_sha256: str,
) -> tuple[ExternalIoFinding, ...]:
    try:
        tree = ast.parse(source, filename=relative_path)
    except SyntaxError as exc:
        raise ExternalIoScanError(
            f"cannot parse Python executable {relative_path}:{exc.lineno or 0}"
        ) from exc

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                local_name = imported.asname or imported.name.split(".", 1)[0]
                canonical_name = imported.name if imported.asname else local_name
                aliases[local_name] = canonical_name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for imported in node.names:
                if imported.name == "*":
                    continue
                aliases[imported.asname or imported.name] = f"{node.module}.{imported.name}"

    raw_findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        symbol = _resolve_python_name(node.func, aliases)
        if not symbol:
            continue
        category = _classify_python_call(symbol)
        if category:
            raw_findings.append((node.lineno, category, symbol))
    return _build_findings(
        raw_findings,
        root_id=root_id,
        relative_path=relative_path,
        file_sha256=file_sha256,
    )


def _shell_tokens(line: str) -> list[str] | None:
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
        lexer.commenters = "#"
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def scan_shell_source(
    source: str,
    *,
    root_id: str,
    relative_path: str,
    file_sha256: str,
) -> tuple[ExternalIoFinding, ...]:
    raw_findings: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        candidate_line = line.rstrip()
        if candidate_line.endswith("\\"):
            candidate_line = candidate_line[:-1]
        tokens = _shell_tokens(candidate_line)
        if tokens is None:
            raw_findings.append((line_number, "process", "dynamic-command"))
            continue
        expecting_command = True
        for token in tokens:
            if token in _SHELL_SEPARATORS:
                expecting_command = True
                continue
            if not expecting_command:
                continue
            if token in _SHELL_COMMAND_PREFIXES or token in _SHELL_COMMAND_WRAPPERS:
                continue
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
                continue
            if token.startswith("$") or "${" in token:
                category = (
                    "credential"
                    if _SHELL_CREDENTIAL_VARIABLE_PATTERN.search(token)
                    else "process"
                )
                raw_findings.append((line_number, category, "dynamic-command"))
                expecting_command = False
                continue
            command = Path(token).name
            category = _SHELL_COMMAND_CATEGORIES.get(command)
            if category:
                raw_findings.append((line_number, category, command))
            expecting_command = False
    return _build_findings(
        raw_findings,
        root_id=root_id,
        relative_path=relative_path,
        file_sha256=file_sha256,
    )


def _mask_javascript_non_code(source: str) -> str:
    output = list(source)
    index = 0
    state = "code"
    quote = ""
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if current == "/" and following == "/":
                output[index] = output[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if current == "/" and following == "*":
                output[index] = output[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if current in {"'", '"', "`"}:
                quote = current
                output[index] = " "
                index += 1
                state = "string"
                continue
        elif state == "line_comment":
            if current == "\n":
                state = "code"
            else:
                output[index] = " "
        elif state == "block_comment":
            if current == "*" and following == "/":
                output[index] = output[index + 1] = " "
                index += 2
                state = "code"
                continue
            if current != "\n":
                output[index] = " "
        else:
            if current == "\\":
                output[index] = " "
                if index + 1 < len(source):
                    if source[index + 1] != "\n":
                        output[index + 1] = " "
                    index += 2
                    continue
            elif current == quote:
                output[index] = " "
                index += 1
                state = "code"
                continue
            elif current != "\n":
                output[index] = " "
        index += 1
    return "".join(output)


def scan_javascript_source(
    source: str,
    *,
    root_id: str,
    relative_path: str,
    file_sha256: str,
) -> tuple[ExternalIoFinding, ...]:
    executable = _mask_javascript_non_code(source)
    raw_findings: list[tuple[int, str, str]] = []
    for match in _JAVASCRIPT_CALL_PATTERN.finditer(executable):
        symbol = match.group(1)
        category = _JAVASCRIPT_CALL_CATEGORIES.get(symbol)
        if category:
            raw_findings.append((executable.count("\n", 0, match.start()) + 1, category, symbol))
    return _build_findings(
        raw_findings,
        root_id=root_id,
        relative_path=relative_path,
        file_sha256=file_sha256,
    )


def scan_skill_markdown(
    source: str,
    *,
    root_id: str,
    relative_path: str,
    file_sha256: str,
) -> tuple[ExternalIoFinding, ...]:
    raw_findings: list[tuple[int, str, str]] = []
    for match in _SKILL_FENCE_PATTERN.finditer(source):
        language = match.group("language").lower()
        body = match.group("body")
        body_line = source.count("\n", 0, match.start("body")) + 1
        if language in {"py", "python"}:
            block_findings = scan_python_source(
                body,
                root_id=root_id,
                relative_path=relative_path,
                file_sha256=file_sha256,
            )
        elif language in _SHELL_LANGUAGES:
            block_findings = scan_shell_source(
                body,
                root_id=root_id,
                relative_path=relative_path,
                file_sha256=file_sha256,
            )
        elif language in _JAVASCRIPT_LANGUAGES:
            block_findings = scan_javascript_source(
                body,
                root_id=root_id,
                relative_path=relative_path,
                file_sha256=file_sha256,
            )
        else:
            continue
        raw_findings.extend(
            (body_line + finding.line - 1, finding.category, finding.symbol)
            for finding in block_findings
        )
    return _build_findings(
        raw_findings,
        root_id=root_id,
        relative_path=relative_path,
        file_sha256=file_sha256,
    )


def _validate_plugin_entrypoints(root: Path) -> None:
    capabilities_path = root / "capabilities.json"
    if not capabilities_path.is_file():
        return
    try:
        payload = json.loads(capabilities_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExternalIoScanError("cannot read plugin capabilities manifest") from exc
    capabilities = payload.get("capabilities", []) if isinstance(payload, dict) else []
    if not isinstance(capabilities, list):
        raise ExternalIoScanError("invalid plugin capabilities manifest")
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        entrypoint = item.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            continue
        suffix = Path(entrypoint).suffix.lower()
        if suffix not in _SUPPORTED_EXECUTABLE_SUFFIXES:
            raise ExternalIoScanError(f"unsupported executable entrypoint: {entrypoint}")


def _iter_scannable_files(root: Path) -> Iterable[Path]:
    for current_directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in _IGNORED_DIRECTORY_NAMES
            and not (Path(current_directory) / name).is_symlink()
        )
        current_path = Path(current_directory)
        for file_name in sorted(file_names):
            path = current_path / file_name
            if path.is_symlink():
                continue
            if file_name == "SKILL.md" or path.suffix.lower() in _SUPPORTED_EXECUTABLE_SUFFIXES:
                yield path


def _scan_file(path: Path, *, root_id: str, relative_path: str) -> tuple[ExternalIoFinding, ...]:
    try:
        content = path.read_bytes()
        source = content.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ExternalIoScanError(f"cannot read executable {relative_path}") from exc
    file_sha256 = hashlib.sha256(content).hexdigest()
    if path.name == "SKILL.md":
        return scan_skill_markdown(
            source,
            root_id=root_id,
            relative_path=relative_path,
            file_sha256=file_sha256,
        )
    suffix = path.suffix.lower()
    if suffix == ".py":
        scanner = scan_python_source
    elif suffix in {".bash", ".sh", ".zsh"}:
        scanner = scan_shell_source
    elif suffix in {".cjs", ".js", ".mjs", ".ts"}:
        scanner = scan_javascript_source
    else:
        raise ExternalIoScanError(f"unsupported executable: {relative_path}")
    return scanner(
        source,
        root_id=root_id,
        relative_path=relative_path,
        file_sha256=file_sha256,
    )


def scan_roots(
    roots: Sequence[ScanRoot],
    *,
    generated_at: str | None = None,
) -> ExternalIoInventory:
    seen_root_ids: set[str] = set()
    root_records: list[dict[str, str]] = []
    findings: list[ExternalIoFinding] = []
    for scan_root in roots:
        root_id = scan_root.root_id.strip()
        if not root_id:
            raise ExternalIoScanError("scan root id must not be empty")
        if root_id in seen_root_ids:
            raise ExternalIoScanError(f"duplicate scan root id: {root_id}")
        seen_root_ids.add(root_id)
        root = scan_root.path.resolve()
        if not root.is_dir():
            raise ExternalIoScanError(f"scan root is not a directory: {root_id}")
        _validate_plugin_entrypoints(root)
        root_records.append({"path": str(root), "root_id": root_id})
        for path in _iter_scannable_files(root):
            relative_path = path.relative_to(root).as_posix()
            findings.extend(_scan_file(path, root_id=root_id, relative_path=relative_path))

    inventory_timestamp = generated_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    return ExternalIoInventory(
        schema_version=INVENTORY_SCHEMA_VERSION,
        generated_at=inventory_timestamp,
        roots=tuple(sorted(root_records, key=lambda item: item["root_id"])),
        findings=tuple(
            sorted(
                findings,
                key=lambda item: (
                    item.root_id,
                    item.relative_path,
                    item.category,
                    item.symbol,
                    item.occurrence,
                    item.line,
                ),
            )
        ),
    )


def inventory_to_dict(inventory: ExternalIoInventory) -> dict[str, object]:
    return {
        "schema_version": inventory.schema_version,
        "generated_at": inventory.generated_at,
        "roots": [dict(item) for item in inventory.roots],
        "findings": [asdict(item) for item in inventory.findings],
    }
