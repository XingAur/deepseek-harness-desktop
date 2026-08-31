#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGED_PYTHON="$ROOT_DIR/runtime/bin/python3"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

if [ -x "$PACKAGED_PYTHON" ]; then
  PYTHON="$PACKAGED_PYTHON"
elif [ -x "$VENV_PYTHON" ]; then
  PYTHON="$VENV_PYTHON"
else
  echo "Harness interpreter is missing: $PACKAGED_PYTHON or $VENV_PYTHON" >&2
  exit 2
fi

export PYTHONDONTWRITEBYTECODE="1"
COMMAND="${1:-offline}"

prepare_isolated_test_runtime() {
  HARNESS_VERIFY_RUNTIME_DIR="$(mktemp -d /private/tmp/his-harness-verify.XXXXXX)"
  mkdir -p "$HARNESS_VERIFY_RUNTIME_DIR/knowledge"
  export HARNESS_DB_PATH="$HARNESS_VERIFY_RUNTIME_DIR/harness.sqlite"
  export HIS_KNOWLEDGE_HOME="$HARNESS_VERIFY_RUNTIME_DIR/knowledge"
}

case "$COMMAND" in
  unit)
    prepare_isolated_test_runtime
    exec "$PYTHON" -m unittest discover -s "$ROOT_DIR/tests" -p 'test_*.py'
    ;;
  offline)
    prepare_isolated_test_runtime
    if [ -n "${HARNESS_GATE_OUTPUT_DIR:-}" ]; then
      OUTPUT_DIR="$HARNESS_GATE_OUTPUT_DIR"
    else
      OUTPUT_DIR="$(mktemp -d "${TMPDIR:-/private/tmp}/his-harness-enterprise-gate.XXXXXX")"
    fi
    exec "$PYTHON" "$ROOT_DIR/tools/enterprise_gate.py" --output-dir "$OUTPUT_DIR"
    ;;
  manager-static)
    prepare_isolated_test_runtime
    if [ -z "${HIS_HARNESS_ROOT:-}" ]; then
      echo "HIS_HARNESS_ROOT must be set for manager-static" >&2
      exit 2
    fi
    MANAGER_ROOT="${HARNESS_MANAGER_ROOT:-$ROOT_DIR/../HarnessManager}"
    if [ ! -d "$MANAGER_ROOT" ]; then
      echo "HarnessManager directory is missing: $MANAGER_ROOT" >&2
      exit 2
    fi
    cd "$MANAGER_ROOT"
    exec env HIS_HARNESS_ROOT="$HIS_HARNESS_ROOT" "$PYTHON" -m unittest \
      tests.test_core_adapter \
      tests.test_evidence_catalog \
      tests.test_static_behavior \
      tests.test_static_contract
    ;;
  architecture)
    prepare_isolated_test_runtime
    cd "$ROOT_DIR"
    "$PYTHON" tools/external_io_inventory.py validate \
      --policy config/external_io_boundaries.v1.json \
      --matrix config/role_capability_skill_matrix.json \
      --format summary
    exec "$PYTHON" -m unittest \
      tests.test_external_io_inventory \
      tests.test_external_io_policy \
      tests.test_external_io_inventory_cli \
      tests.test_role_capability_skill_registry \
      tests.test_mcp_contracts \
      tests.test_mcp_schema_validation \
      tests.test_mcp_capability_registry \
      tests.test_mcp_capability_check_cli \
      tests.test_mcp_gateway \
      tests.test_mcp_capability_runtime \
      tests.test_mcp_phase_1a_acceptance \
      tests.test_mcp_stdio_transport \
      tests.test_mcp_persistence \
      tests.test_mcp_runtime_factory \
      tests.test_mcp_phase_1b_runtime_acceptance \
      tests.test_mcp_phase_1d_primary_activation \
      tests.test_mcp_primary_provider_adapter \
      tests.test_mcp_connector_server_contracts \
      tests.test_provider_authority_policy \
      tests.test_provider_action_authorization \
      tests.test_provider_authority_acceptance \
      tests.test_change_context_external_collectors \
      tests.test_change_context_database_collector \
      tests.test_change_context_prompt_boundary \
      tests.test_change_context_worker_binding \
      tests.test_pg_evidence_mcp_boundary -v
    ;;
  *)
    echo "unknown verification command: $COMMAND (expected unit, offline, manager-static, or architecture)" >&2
    exit 2
    ;;
esac
