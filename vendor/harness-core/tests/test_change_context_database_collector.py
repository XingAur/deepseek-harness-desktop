from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.capability_contracts import CapabilityResult, MutationLevel
from app.change_context_collectors import DataGraphCollector


CATALOG = {
    "tables": (
        ["table_schema", "table_name", "table_type"],
        [["public", "orders", "BASE TABLE"]],
    ),
    "columns": (
        ["table_schema", "table_name", "ordinal_position", "column_name", "data_type", "is_nullable", "column_default"],
        [["public", "orders", 1, "id", "bigint", "NO", "nextval('orders_id_seq')"]],
    ),
    "constraints": (
        ["constraint_name", "constraint_type", "column_name", "ordinal_position"],
        [["orders_pkey", "PRIMARY KEY", "id", 1]],
    ),
    "indexes": (
        ["schemaname", "tablename", "indexname", "indexdef"],
        [["public", "orders", "orders_pkey", "CREATE UNIQUE INDEX orders_pkey ON public.orders USING btree (id)"]],
    ),
    "foreign_keys": (
        ["constraint_name", "table_schema", "table_name", "column_name", "foreign_table_schema", "foreign_table_name", "foreign_column_name"],
        [],
    ),
}


class FakeDatabaseRuntime:
    def __init__(self, *, fail_operation: str = "", stale: bool = False, changed: bool = False, catalog=None) -> None:
        self.calls = []
        self.fail_operation = fail_operation
        self.stale = stale
        self.changed = changed
        self.catalog = CATALOG if catalog is None else catalog

    def execute(self, request):
        self.calls.append(request)
        operation = request.input["operation"]
        status = "failed" if operation == self.fail_operation else "success"
        columns, rows = self.catalog.get(operation, ([], []))
        result = CapabilityResult(
            request_id=request.request_id,
            capability=request.capability,
            provider=request.provider,
            status=status,
            mutation_level=MutationLevel.L1,
            changed=self.changed,
            summary="fixture",
            data={
                "connection_alias": request.input["connection_alias"],
                "operation": operation,
                "columns": columns,
                "rows": rows,
            } if status == "success" else {},
            evidence=({"ref": f"mcp-evidence:{request.request_id}:abc"},) if status == "success" else (),
            warnings=(),
            blockers=() if status == "success" else ("failed",),
            audit={
                "error_code": "" if status == "success" else "DATABASE_TIMEOUT",
                "execution_kind": "mcp",
                "source_identity": f"postgresql:{request.input['connection_alias']}:{operation}",
                "source_version": f"{operation}-v1",
                "freshness_status": "stale" if self.stale else "fresh",
                "freshness_expires_at": "2026-08-30T01:00:00Z",
                "collected_at": "2026-08-30T00:00:00Z",
            },
        )
        return SimpleNamespace(result=result)


class DataGraphCollectorTests(unittest.TestCase):
    def collect(self, runtime):
        return DataGraphCollector(runtime=runtime).collect(
            connection_alias="his_test_readonly",
            schema="public",
            tables=("orders",),
            task_id="task-1",
            run_id="run-1",
        )

    def test_exact_mcp_catalog_requests_and_normalized_metadata_only_payload(self) -> None:
        runtime = FakeDatabaseRuntime()
        collected = self.collect(runtime)
        self.assertEqual("complete", collected.status)
        self.assertEqual(["tables", "columns", "constraints", "indexes", "foreign_keys"], [item.input["operation"] for item in runtime.calls])
        for request in runtime.calls:
            self.assertEqual(("database.inspect", "postgresql", "preview", MutationLevel.L1), (request.capability, request.provider, request.mode, request.mutation_level))
            self.assertEqual(["database:inspect"], request.to_dict()["authorization"]["scope"])
            self.assertEqual({"task_id": "task-1", "run_id": "run-1"}, dict(request.context))
        serialized = str(collected.payload).lower()
        for forbidden in ("password", "username", "dsn", "endpoint", "'sql':", "raw_envelope", "business_rows"):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("rows", collected.payload)
        self.assertEqual("expression", collected.payload["tables"][0]["columns"][0]["default_class"])

    def test_readonly_alias_missing_table_and_source_failures_block_without_fallback(self) -> None:
        runtime = FakeDatabaseRuntime()
        invalid = DataGraphCollector(runtime=runtime).collect(
            connection_alias="his_test", schema="public", tables=("orders",), task_id="task-1", run_id="run-1",
        )
        self.assertEqual("incomplete", invalid.status)
        self.assertEqual([], runtime.calls)
        missing_catalog = dict(CATALOG)
        missing_catalog["tables"] = CATALOG["tables"][0], []
        missing_runtime = FakeDatabaseRuntime(catalog=missing_catalog)
        missing = self.collect(missing_runtime)
        self.assertEqual("incomplete", missing.status)
        self.assertEqual(1, len(missing_runtime.calls))
        for runtime_case in (FakeDatabaseRuntime(fail_operation="columns"), FakeDatabaseRuntime(stale=True), FakeDatabaseRuntime(changed=True)):
            with self.subTest(case=vars(runtime_case)):
                blocked = self.collect(runtime_case)
                self.assertEqual("incomplete", blocked.status)
                self.assertIn("BLOCKED_CONTEXT_SOURCE_UNAVAILABLE", "\n".join(blocked.blockers))

    def test_partial_metadata_and_contradictory_foreign_keys_block(self) -> None:
        partial = dict(CATALOG)
        partial["columns"] = (["column_name"], [["id"]])
        self.assertEqual("incomplete", self.collect(FakeDatabaseRuntime(catalog=partial)).status)
        contradictory = dict(CATALOG)
        contradictory["foreign_keys"] = (
            CATALOG["foreign_keys"][0],
            [
                ["orders_customer_fk", "public", "orders", "customer_id", "public", "customer", "id"],
                ["orders_customer_fk", "public", "orders", "customer_id", "public", "patient", "id"],
            ],
        )
        result = self.collect(FakeDatabaseRuntime(catalog=contradictory))
        self.assertEqual("incomplete", result.status)
        self.assertIn("contradictory", "\n".join(result.blockers).lower())


if __name__ == "__main__":
    unittest.main()
