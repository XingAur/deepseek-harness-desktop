from __future__ import annotations

import re
from dataclasses import dataclass


_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
_FORBIDDEN_TOKENS = frozenset(
    (
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "CREATE",
        "ALTER",
        "DROP",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
        "CALL",
        "EXEC",
        "EXECUTE",
        "VACUUM",
        "ATTACH",
        "DETACH",
        "PRAGMA",
        "COPY",
        "LOAD",
        "INTO",
        "OUTFILE",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "START",
        "SET",
        "USE",
        "LOCK",
        "UNLOCK",
        "PREPARE",
        "DEALLOCATE",
        "DECLARE",
        "OPEN",
        "CLOSE",
        "HANDLER",
        "DO",
        "LOAD_EXTENSION",
        "XP_CMDSHELL",
    )
)
_LOCKING_CLAUSE = frozenset((("FOR", "UPDATE"), ("FOR", "SHARE")))


@dataclass(frozen=True)
class ReadonlySqlValidation:
    statement_kind: str
    readonly_account_required: bool = True


def validate_readonly_sql(sql: str) -> ReadonlySqlValidation:
    if not isinstance(sql, str) or not sql.strip():
        _reject("empty_statement")
    masked = _mask_comments_literals_and_identifiers(sql)
    body = masked.strip()
    semicolons = [index for index, character in enumerate(body) if character == ";"]
    if semicolons:
        if len(semicolons) != 1 or body[semicolons[0] + 1 :].strip():
            _reject("multiple_statements")
        body = body[: semicolons[0]].rstrip()
    if not body:
        _reject("empty_statement")

    tokens_with_depth = _tokens_with_depth(body)
    tokens = [token for token, _depth in tokens_with_depth]
    _reject_locking_clause(tokens)
    forbidden = next((token for token in tokens if token in _FORBIDDEN_TOKENS), None)
    if forbidden is not None:
        _reject(f"forbidden_token:{forbidden.lower()}")
    top_level = [token for token, depth in tokens_with_depth if depth == 0]
    if not top_level:
        _reject("statement_kind_not_allowed")
    if top_level[0] == "SELECT":
        return ReadonlySqlValidation("select")
    if top_level[:2] == ["EXPLAIN", "SELECT"]:
        return ReadonlySqlValidation("explain_select")
    if top_level[0] == "WITH" and "SELECT" in top_level[1:]:
        return ReadonlySqlValidation("with_select")
    _reject("statement_kind_not_allowed")


def _reject_locking_clause(top_level: list[str]) -> None:
    """Reject row-lock syntax structurally instead of accepting every SELECT prefix.

    The parser deliberately accepts a very small read grammar.  DML-like words
    are rejected at every nesting depth above, while this handles the one
    mutation-adjacent construction that can legally appear after a SELECT.
    """

    for index in range(len(top_level) - 1):
        if (top_level[index], top_level[index + 1]) in _LOCKING_CLAUSE:
            _reject("locking_clause_not_allowed")


def _tokens_with_depth(sql: str) -> list[tuple[str, int]]:
    tokens: list[tuple[str, int]] = []
    depth = 0
    token_at = {match.start(): match for match in _TOKEN.finditer(sql)}
    index = 0
    while index < len(sql):
        character = sql[index]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                _reject("unbalanced_parentheses")
        match = token_at.get(index)
        if match is not None:
            tokens.append((match.group(0).upper(), depth))
            index = match.end()
            continue
        index += 1
    if depth != 0:
        _reject("unbalanced_parentheses")
    return tokens


def _mask_comments_literals_and_identifiers(sql: str) -> str:
    masked = list(sql)
    index = 0
    while index < len(sql):
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            end = len(sql) if end < 0 else end
            _blank(masked, index, end)
            index = end
            continue
        if sql.startswith("/*", index):
            executable_prefix = sql[index : index + 4].lower()
            if sql.startswith("/*!", index) or executable_prefix == "/*m!":
                _reject("executable_comment")
            end = sql.find("*/", index + 2)
            if end < 0:
                _reject("unterminated_comment")
            _blank(masked, index, end + 2)
            index = end + 2
            continue
        dollar = _DOLLAR_QUOTE.match(sql, index)
        if dollar is not None:
            delimiter = dollar.group(0)
            end = sql.find(delimiter, dollar.end())
            if end < 0:
                _reject("unterminated_literal")
            _blank(masked, index, end + len(delimiter))
            index = end + len(delimiter)
            continue
        if sql[index] in ("'", '"', "`"):
            index = _mask_quoted(sql, masked, index, sql[index])
            continue
        if sql[index] == "[":
            end = sql.find("]", index + 1)
            if end < 0:
                _reject("unterminated_identifier")
            _blank(masked, index, end + 1)
            index = end + 1
            continue
        index += 1
    return "".join(masked)


def _mask_quoted(sql: str, masked: list[str], start: int, quote: str) -> int:
    index = start + 1
    while index < len(sql):
        if sql[index] == quote:
            if index + 1 < len(sql) and sql[index + 1] == quote:
                index += 2
                continue
            _blank(masked, start, index + 1)
            return index + 1
        index += 1
    _reject("unterminated_literal")


def _blank(masked: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if masked[index] not in ("\r", "\n"):
            masked[index] = " "


def _reject(reason: str) -> None:
    raise ValueError(f"database_readonly_policy:{reason}")
