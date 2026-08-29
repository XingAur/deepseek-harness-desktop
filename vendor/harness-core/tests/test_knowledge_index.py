from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
from urllib.parse import quote_plus

from app.knowledge_index import (
    publish_approved_knowledge_markdown,
    query_knowledge_index,
    sync_obsidian_markdown_index,
)


class KnowledgeIndexTests(unittest.TestCase):
    def test_reviewed_markdown_publish_is_atomic_content_hash_deduplicated_and_indexed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "his-knowledge"
            content_hash = "sha256:" + hashlib.sha256(b"reviewed-candidate").hexdigest()

            first = publish_approved_knowledge_markdown(
                home,
                content_hash=content_hash,
                title="DFHIS-31333 审核知识",
                body="已审核证据可用于门诊收费金额汇总咨询。",
                allowed_base=Path(temp_dir),
            )
            second = publish_approved_knowledge_markdown(
                home,
                content_hash=content_hash,
                title="DFHIS-31333 审核知识",
                body="已审核证据可用于门诊收费金额汇总咨询。",
                allowed_base=Path(temp_dir),
            )
            result = query_knowledge_index(home, "门诊收费金额汇总")

            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertEqual(first["source_path"], second["source_path"])
            self.assertEqual(1, len(list((home / "vault" / "90-review").glob("*.md"))))
            self.assertTrue(Path(str(first["manifest_path"])).is_file())
            self.assertTrue(result["answerable"])
            self.assertEqual([first["source_path"]], result["citations"])

    def test_publish_rejects_tampered_manifest_path_that_escapes_vault(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "his-knowledge"
            vault = home / "vault"
            vault.mkdir(parents=True)
            outside = home.parent / "outside.md"
            outside.write_text("outside", encoding="utf-8")
            content_hash = "sha256:" + hashlib.sha256(b"tampered").hexdigest()
            (vault / ".harness-knowledge-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "his-knowledge-manifest.v1",
                        "entries": [
                            {
                                "content_hash": content_hash,
                                "source_path": "vault/../../outside.md",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "knowledge_manifest_invalid"):
                publish_approved_knowledge_markdown(
                    home,
                    content_hash=content_hash,
                    title="安全知识",
                    body="不应接受越界 manifest。",
                    allowed_base=Path(temp_dir),
                )

    def test_publish_rejects_root_and_child_symlink_before_external_write(self) -> None:
        with TemporaryDirectory() as temp_dir:
            allowed_base = Path(temp_dir) / "allowed"
            allowed_base.mkdir()
            outside = Path(temp_dir) / "outside"
            outside.mkdir()
            content_hash = "sha256:" + hashlib.sha256(b"symlink").hexdigest()

            root_link = allowed_base / "linked-home"
            root_link.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "knowledge_path_invalid"):
                publish_approved_knowledge_markdown(
                    root_link,
                    content_hash=content_hash,
                    title="根链接知识",
                    body="不得通过根链接写入。",
                    allowed_base=allowed_base,
                )
            self.assertEqual([], list(outside.iterdir()))

            home = allowed_base / "his-knowledge"
            home.mkdir()
            (home / "vault").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "knowledge_path_invalid"):
                publish_approved_knowledge_markdown(
                    home,
                    content_hash=content_hash,
                    title="子链接知识",
                    body="不得通过子目录链接写入。",
                    allowed_base=allowed_base,
                )
            self.assertEqual([], list(outside.iterdir()))

    def test_publication_keeps_timezone_expiry_and_same_day_expiry_is_not_answerable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "his-knowledge"
            content_hash = "sha256:" + hashlib.sha256(b"expiry").hexdigest()
            expired_same_day = "2026-08-10T00:00:00+00:00"

            published = publish_approved_knowledge_markdown(
                home,
                content_hash=content_hash,
                title="同日已过期知识",
                body="同一天内到期后不得直接回答。",
                valid_until=expired_same_day,
                allowed_base=Path(temp_dir),
            )

            markdown = Path(str(published["markdown_path"])).read_text(encoding="utf-8")
            manifest = json.loads(Path(str(published["manifest_path"])).read_text(encoding="utf-8"))
            result = query_knowledge_index(
                home, "同日已过期知识", allowed_base=Path(temp_dir)
            )
            self.assertIn(expired_same_day, markdown)
            self.assertEqual(expired_same_day, manifest["entries"][0]["valid_until"])
            self.assertFalse(result["answerable"])

    def test_obsidian_markdown_is_indexed_and_retrievable_with_citation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "his-knowledge"
            note = home / "vault" / "10-his-rules" / "refund.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                """---
status: approved
evidence_level: verified
valid_until: 2999-12-31
---
# 门诊退费规则

普通医保退费需要区分全退、部分退和移动医保来源。
""",
                encoding="utf-8",
            )

            sync = sync_obsidian_markdown_index(home)
            result = query_knowledge_index(home, "移动医保")

            self.assertTrue(sync["changed"])
            self.assertEqual(1, sync["indexed_count"])
            self.assertTrue(result["answerable"])
            self.assertEqual("vault/10-his-rules/refund.md", result["results"][0]["source_path"])
            self.assertIn("移动医保", result["results"][0]["snippet"])
            self.assertIn("vault/10-his-rules/refund.md", result["citations"])
            self.assertEqual("approved", result["results"][0]["status"])
            self.assertEqual("verified", result["results"][0]["evidence_level"])

    def test_only_approved_evidenced_unexpired_note_is_answerable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "his-knowledge"
            note_dir = home / "vault"
            note_dir.mkdir(parents=True)
            fixtures = {
                "candidate.md": ("candidate", "draft", "2999-12-31", "候选知识"),
                "conflicted.md": ("conflicted", "verified", "2999-12-31", "冲突知识"),
                "unknown.md": ("unknown", "verified", "2999-12-31", "未知知识"),
                "expired.md": ("approved", "verified", "2000-01-01", "过期知识"),
                "no-evidence.md": ("approved", "", "2999-12-31", "无证据知识"),
            }
            for name, (status, evidence_level, valid_until, title) in fixtures.items():
                (note_dir / name).write_text(
                    f"---\nstatus: {status}\nevidence_level: {evidence_level}\n"
                    f"valid_until: {valid_until}\n---\n# {title}\n\n{title}正文。\n",
                    encoding="utf-8",
                )
            sync_obsidian_markdown_index(home)

            for _, (_, _, _, title) in fixtures.items():
                with self.subTest(title=title):
                    result = query_knowledge_index(home, title)
                    self.assertFalse(result["answerable"])
                    self.assertEqual([], result["results"])
                    self.assertEqual("knowledge_insufficient", result["retrieval_status"])

    def test_query_without_indexed_knowledge_reports_gap_without_answering(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "his-knowledge"
            (home / "vault").mkdir(parents=True)
            sync_obsidian_markdown_index(home)

            result = query_knowledge_index(home, "结算清单")

            self.assertFalse(result["answerable"])
            self.assertEqual([], result["results"])
            self.assertIn("缺资料", result["message"])

    def test_quoted_colon_governance_scalar_remains_valid_at_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "his-knowledge"
            note = home / "vault" / "quoted-colon.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                "---\nstatus: approved\nevidence_level: 'verified:manual'\n"
                "valid_until: 2999-12-31\n---\n# 引用冒号知识\n\n正常根级字段。\n",
                encoding="utf-8",
            )

            sync = sync_obsidian_markdown_index(home)
            result = query_knowledge_index(home, "引用冒号知识")

            self.assertEqual(1, sync["indexed_count"])
            self.assertTrue(result["answerable"])
            self.assertEqual("verified:manual", result["results"][0]["evidence_level"])

    def test_sensitive_notes_are_skipped_with_shared_fail_closed_detector(self) -> None:
        deep_encoded = '{"client_secret":"SENTINEL_DEEP_ENCODED_NOTE"}'
        for _ in range(5):
            deep_encoded = quote_plus(deep_encoded)
        escaped_key = (
            "\\u0063\\u006c\\u0069\\u0065\\u006e\\u0074\\u005f"
            "\\u0073\\u0065\\u0063\\u0072\\u0065\\u0074"
        )
        fixtures = {
            "encoded-json": quote_plus(
                '{"nested":{"client_secret":"SENTINEL_ENCODED"}}'
            ),
            "unicode-json-key": (
                '{"outer":[{"\\u0063\\u006c\\u0069\\u0065\\u006e\\u0074\\u005f'
                '\\u0073\\u0065\\u0063\\u0072\\u0065\\u0074":'
                '"SENTINEL_UNICODE_JSON"}]}'
            ),
            "percent-u": "%u0063lient_secret%3DSENTINEL_PERCENT_U",
            "html-entity": "&#x63;lient_secret&#x3A;SENTINEL_HTML_ENTITY",
            "api-key": '{"api_key":"SENTINEL_API_KEY"}',
            "personal-token": '"personal_access_token":"SENTINEL_PERSONAL"',
            "independent-pat": "pat=SENTINEL_INDEPENDENT_PAT",
            "pat-key": '"gitlab_pat":"SENTINEL_PAT"',
            "aliyun-pat": '"aliyun_devops_pat":"SENTINEL_ALIYUN"',
            "authorization": "Authorization=Bearer SENTINEL_AUTH",
            "private-key": "-----BEGIN PRIVATE KEY-----\nSENTINEL_KEY",
            "encrypted-private-key": (
                "-----BEGIN ENCRYPTED PRIVATE KEY-----\n"
                "SENTINEL_ENCRYPTED_KEY\n"
                "-----END ENCRYPTED PRIVATE KEY-----"
            ),
            "mobile": "138.0013.8000",
            "country-code-mobile": "+8613800138000",
            "identity": "110105-19491231-002X",
            "deep-encoded": deep_encoded,
            "prefixed-unicode-json": (
                f'前缀 {{"outer":[{{"{escaped_key}":'
                '"SENTINEL_PREFIX_NOTE"}}]} 后缀'
            ),
            "malformed-unicode-json": (
                f'prefix {{"{escaped_key}":"SENTINEL_MALFORMED_NOTE",}} suffix'
            ),
            "deep-json": "[" * 70 + '"SENTINEL_DEEP_NOTE"' + "]" * 70,
            "over-char-json": (
                '{"message":"SENTINEL_OVER_CHAR_NOTE_' + "a" * 33_000 + '"}'
            ),
            "over-byte-json": (
                '{"message":"SENTINEL_OVER_BYTE_NOTE_' + "汉" * 22_000 + '"}'
            ),
            "over-node-json": (
                '["SENTINEL_OVER_NODE_NOTE",'
                + ",".join("0" for _ in range(10_100))
                + "]"
            ),
        }
        for name, sensitive_value in fixtures.items():
            with self.subTest(name=name), TemporaryDirectory() as temp_dir:
                home = Path(temp_dir) / "his-knowledge"
                note = home / "vault" / f"{name}.md"
                note.parent.mkdir(parents=True)
                note.write_text(
                    "---\nstatus: approved\nevidence_level: verified\n"
                    "valid_until: 2999-12-31\n---\n"
                    f"# 敏感知识\n\n{sensitive_value}\n",
                    encoding="utf-8",
                )

                sync = sync_obsidian_markdown_index(home)
                result = query_knowledge_index(home, "敏感知识")
                with closing(sqlite3.connect(home / "knowledge.sqlite")) as connection:
                    stored_count = connection.execute(
                        "select count(*) from obsidian_markdown_index"
                    ).fetchone()[0]
                raw_index_storage = b"".join(
                    path.read_bytes()
                    for path in home.glob("knowledge.sqlite*")
                    if path.is_file()
                )
                rendered = json.dumps([sync, result], ensure_ascii=False)

                self.assertEqual(0, sync["indexed_count"])
                self.assertEqual(1, sync["skipped_sensitive_count"])
                self.assertEqual(0, stored_count)
                self.assertFalse(result["answerable"])
                self.assertNotIn("SENTINEL", rendered)
                self.assertFalse(b"SENTINEL" in raw_index_storage)
                self.assertNotIn(sensitive_value.encode("utf-8"), raw_index_storage)

    def test_query_treats_percent_underscore_and_backslash_as_literal_text(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "his-knowledge"
            note = home / "vault" / "literal.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                "---\nstatus: approved\nevidence_level: verified\n"
                "valid_until: 2999-12-31\n---\n# 普通知识\n\n普通字面量正文。\n",
                encoding="utf-8",
            )
            sync_obsidian_markdown_index(home)

            self.assertTrue(query_knowledge_index(home, "普通字面量")["answerable"])
            for special_query in ("%", "_", "\\"):
                with self.subTest(query=special_query):
                    result = query_knowledge_index(home, special_query)
                    self.assertFalse(result["answerable"])
                    self.assertEqual("knowledge_gap", result["retrieval_status"])

    def test_duplicate_or_malformed_governance_frontmatter_is_skipped(self) -> None:
        fixtures = {
            "duplicate": (
                "status: approved\nstatus: candidate\n"
                "evidence_level: verified\nvalid_until: 2999-12-31"
            ),
            "duplicate-case": (
                "Status: approved\nstatus: approved\n"
                "evidence_level: verified\nvalid_until: 2999-12-31"
            ),
            "duplicate-quoted-key": (
                '"status": approved\nstatus: approved\n'
                "evidence_level: verified\nvalid_until: 2999-12-31"
            ),
            "malformed-line": (
                "status approved\nevidence_level: verified\nvalid_until: 2999-12-31"
            ),
            "invalid-date": (
                "status: approved\nevidence_level: verified\nvalid_until: 2999-02-30"
            ),
            "invalid-date-format": (
                "status: approved\nevidence_level: verified\nvalid_until: 2999/12/31"
            ),
            "mismatched-quote": (
                "status: 'approved\"\nevidence_level: verified\nvalid_until: 2999-12-31"
            ),
            "structured-evidence": (
                "status: approved\nevidence_level: [verified]\nvalid_until: 2999-12-31"
            ),
            "null-evidence": (
                "status: approved\nevidence_level: null\nvalid_until: 2999-12-31"
            ),
            "mismatched-key-quote": (
                '"status\': approved\nevidence_level: verified\nvalid_until: 2999-12-31'
            ),
            "indented-governance": (
                "  status: approved\nevidence_level: verified\nvalid_until: 2999-12-31"
            ),
            "nested-governance": (
                "governance:\n  status: approved\n  evidence_level: verified\n"
                "  valid_until: 2999-12-31"
            ),
            "nested-status-block": (
                "status:\n  value: approved\nevidence_level: verified\n"
                "valid_until: 2999-12-31"
            ),
            "unquoted-colon-value": (
                "status: approved\nevidence_level: verified:manual\n"
                "valid_until: 2999-12-31"
            ),
            "unterminated": (
                "status: approved\nevidence_level: verified\nvalid_until: 2999-12-31"
            ),
        }
        for name, frontmatter in fixtures.items():
            with self.subTest(name=name), TemporaryDirectory() as temp_dir:
                home = Path(temp_dir) / "his-knowledge"
                note = home / "vault" / f"{name}.md"
                note.parent.mkdir(parents=True)
                closing = "" if name == "unterminated" else "\n---"
                note.write_text(
                    f"---\n{frontmatter}{closing}\n# 非法治理知识\n\n不应入索引。\n",
                    encoding="utf-8",
                )

                sync = sync_obsidian_markdown_index(home)
                result = query_knowledge_index(home, "非法治理知识")

                self.assertEqual(0, sync["indexed_count"])
                self.assertEqual(1, sync["skipped_invalid_metadata_count"])
                self.assertFalse(result["answerable"])

    def test_approved_match_after_more_than_limit_ineligible_matches_is_returned(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "his-knowledge"
            note_dir = home / "vault"
            note_dir.mkdir(parents=True)
            for index in range(6):
                (note_dir / f"a-candidate-{index}.md").write_text(
                    "---\nstatus: candidate\nevidence_level: draft\n"
                    "valid_until: 2999-12-31\n---\n# 共同检索词\n\n候选内容。\n",
                    encoding="utf-8",
                )
            (note_dir / "z-approved.md").write_text(
                "---\nstatus: approved\nevidence_level: verified\n"
                "valid_until: 2999-12-31\n---\n# 共同检索词\n\n已批准内容。\n",
                encoding="utf-8",
            )
            sync_obsidian_markdown_index(home)

            result = query_knowledge_index(home, "共同检索词", limit=5)

            self.assertTrue(result["answerable"])
            self.assertEqual(["vault/z-approved.md"], result["citations"])

    def test_v1_index_fails_closed_until_explicit_sync_upgrades_it(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "his-knowledge"
            home.mkdir(parents=True)
            with closing(sqlite3.connect(home / "knowledge.sqlite")) as connection:
                connection.execute(
                    """
                    create table obsidian_markdown_index (
                        source_path text primary key,
                        title text not null,
                        content text not null,
                        content_hash text not null
                    )
                    """
                )
                connection.execute(
                    "insert into obsidian_markdown_index values (?, ?, ?, ?)",
                    ("vault/legacy.md", "迁移知识", "迁移知识正文", "sha256:legacy"),
                )
                connection.commit()

            before = query_knowledge_index(home, "迁移知识")
            note = home / "vault" / "approved.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                "---\nstatus: approved\nevidence_level: verified\n"
                "valid_until: 2999-12-31\n---\n# 迁移知识\n\n迁移后正文。\n",
                encoding="utf-8",
            )
            sync_obsidian_markdown_index(home)
            after = query_knowledge_index(home, "迁移知识")

            self.assertFalse(before["answerable"])
            self.assertEqual("knowledge_gap", before["retrieval_status"])
            self.assertTrue(after["answerable"])
            self.assertEqual(["vault/approved.md"], after["citations"])

    def test_sync_failure_rolls_back_v1_schema_and_existing_rows(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "his-knowledge"
            home.mkdir(parents=True)
            database_path = home / "knowledge.sqlite"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    create table obsidian_markdown_index (
                        source_path text primary key,
                        title text not null,
                        content text not null,
                        content_hash text not null
                    )
                    """
                )
                connection.execute(
                    "insert into obsidian_markdown_index values (?, ?, ?, ?)",
                    ("vault/legacy.md", "旧知识", "旧知识正文", "sha256:legacy"),
                )
                connection.commit()
            note = home / "vault" / "approved.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                "---\nstatus: approved\nevidence_level: verified\n"
                "valid_until: 2999-12-31\n---\n# 新知识\n\n新知识正文。\n",
                encoding="utf-8",
            )

            with mock.patch(
                "app.knowledge_index._replace_index_rows",
                side_effect=RuntimeError("injected migration failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                    sync_obsidian_markdown_index(home)

            with closing(sqlite3.connect(database_path)) as connection:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "pragma table_info(obsidian_markdown_index)"
                    )
                }
                rows = connection.execute(
                    "select source_path, title from obsidian_markdown_index"
                ).fetchall()
            self.assertEqual(
                {"source_path", "title", "content", "content_hash"},
                columns,
            )
            self.assertEqual([("vault/legacy.md", "旧知识")], rows)


if __name__ == "__main__":
    unittest.main()
