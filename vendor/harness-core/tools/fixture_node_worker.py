from __future__ import annotations

import json
import os
import sys
import time


INPUT_SCHEMA_VERSION = "1.0-sandbox-worker-input"
RESULT_SCHEMA_VERSION = "1.0-sandbox-node-result"


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("schema_version") != INPUT_SCHEMA_VERSION:
            raise ValueError("worker input schema invalid")
        fixture = payload.get("fixture")
        if not isinstance(fixture, dict) or fixture.get("fixture_only") is not True:
            raise ValueError("fixture-only input required")
        behavior = str(fixture.get("worker_behavior") or "success")
        environment_key = str(fixture.get("assert_env_absent") or "").strip()
        if environment_key and os.environ.get(environment_key) is not None:
            write_result("failure", error_code="forbidden_environment_inherited")
            return
        if behavior == "failure":
            write_result("failure", error_code="fixture_worker_failure")
            return
        if behavior == "protocol_error":
            sys.stdout.write("invalid-worker-protocol")
            return
        if behavior == "sleep":
            sleep_seconds = max(0.0, min(float(fixture.get("sleep_seconds") or 0), 10.0))
            time.sleep(sleep_seconds)
        elif behavior != "success":
            write_result("failure", error_code="fixture_worker_behavior_invalid")
            return
        write_result(
            "success",
            contract_content=fixture.get("contract_content") or {},
            usage=fixture.get("usage") or {"input_tokens": 0, "output_tokens": 0},
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        write_result("failure", error_code="worker_input_invalid")


def write_result(
    status: str,
    *,
    contract_content: dict | None = None,
    usage: dict | None = None,
    error_code: str = "",
) -> None:
    json.dump(
        {
            "schema_version": RESULT_SCHEMA_VERSION,
            "status": status,
            "contract_content": contract_content or {},
            "usage": usage or {"input_tokens": 0, "output_tokens": 0},
            "error_code": error_code,
        },
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
