from __future__ import annotations

import unittest

from app.database_read_policy import validate_readonly_sql


class DatabaseReadPolicyTests(unittest.TestCase):
    def test_accepts_only_select_explain_select_and_readonly_cte(self) -> None:
        self.assertEqual("select", validate_readonly_sql("select * from patient limit 1").statement_kind)
        self.assertEqual(
            "explain_select",
            validate_readonly_sql("EXPLAIN SELECT * FROM patient").statement_kind,
        )
        self.assertEqual(
            "with_select",
            validate_readonly_sql(
                "WITH active AS (SELECT id FROM patient WHERE active = 1) SELECT * FROM active"
            ).statement_kind,
        )

    def test_rejects_every_change_and_session_statement(self) -> None:
        statements = (
            "insert into t values (1)",
            "update patient set name='x'",
            "delete from patient",
            "merge into t using s on t.id=s.id when matched then update set id=s.id",
            "create table t(id int)",
            "alter table t add column name text",
            "drop table t",
            "truncate table t",
            "grant select on t to u",
            "revoke select on t from u",
            "call do_work()",
            "exec do_work",
            "vacuum",
            "attach database 'other.db' as other",
            "detach database other",
            "pragma table_info(t)",
            "copy t to '/tmp/t.csv'",
            "load data infile '/tmp/t.csv' into table t",
            "select * from t into outfile '/tmp/t.csv'",
            "select * into temp_table from t",
            "with changed as (delete from t returning id) select * from changed",
        )
        for sql in statements:
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(ValueError, "database_readonly_policy"):
                    validate_readonly_sql(sql)

    def test_comments_and_string_literals_do_not_create_false_write_tokens(self) -> None:
        result = validate_readonly_sql(
            "/* DELETE FROM audit */ SELECT 'drop table x; update y' AS note -- INSERT\nFROM patient"
        )
        self.assertEqual("select", result.statement_kind)

    def test_rejects_multiple_or_non_read_statements_and_unclosed_literals(self) -> None:
        statements = (
            "select 1; select 2",
            "values (1)",
            "show tables",
            "begin transaction",
            "commit",
            "rollback",
            "start transaction",
            "set transaction read only",
            "lock table patient in share mode",
            "prepare work from 'select 1'",
            "do $$ begin perform 1; end $$",
            "select 'unterminated",
            "select 1 /* unterminated",
            "",
        )
        for sql in statements:
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(ValueError, "database_readonly_policy"):
                    validate_readonly_sql(sql)

    def test_allows_one_optional_trailing_semicolon(self) -> None:
        self.assertEqual("select", validate_readonly_sql("select 1;").statement_kind)

    def test_rejects_row_locking_clauses_even_when_the_statement_starts_with_select(self) -> None:
        for statement in (
            "select * from patient for update",
            "select * from (select * from patient for share) candidate",
        ):
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(ValueError, "locking_clause_not_allowed"):
                    validate_readonly_sql(statement)

    def test_backslash_does_not_hide_a_following_change_statement(self) -> None:
        with self.assertRaisesRegex(ValueError, "database_readonly_policy"):
            validate_readonly_sql("SELECT '\\'; DELETE FROM patient --'")

    def test_rejects_mysql_and_mariadb_executable_comments(self) -> None:
        for sql in (
            "SELECT 1 /*!50000 DELETE FROM patient */",
            "SELECT 1 /*M!100100 DELETE FROM patient */",
            "SELECT 1 /*m!100100 UPDATE patient SET name='x' */",
        ):
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(ValueError, "database_readonly_policy"):
                    validate_readonly_sql(sql)

    def test_executable_comment_text_inside_string_is_not_executed(self) -> None:
        result = validate_readonly_sql(
            "SELECT '/*!50000 DELETE FROM patient */' AS mysql_note, "
            "'/*M!100100 UPDATE patient SET name=''x'' */' AS mariadb_note"
        )

        self.assertEqual("select", result.statement_kind)


if __name__ == "__main__":
    unittest.main()
