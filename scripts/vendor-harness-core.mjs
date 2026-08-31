#!/usr/bin/env node
/**
 * 把本机 Harness Core 源码 vendor 进桌面仓库（vendor/harness-core）。
 *
 * 这是发布链路的第一步：Harness Core 仓库本身不在 git 里，安装包与 CI
 * 构建必须能从桌面仓库内部拿到一份确定性的 Core 副本。脚本只拷贝代码
 * 与配置（排除运行数据、虚拟环境、缓存），并对 JSON/配置类文件做密钥
 * 样式扫描——发现真实凭证样式立即失败，绝不静默带入仓库。
 *
 * 用法：
 *   node scripts/vendor-harness-core.mjs --source /Users/lym/WorkCode/ai/Harness
 */

import { createHash } from 'node:crypto'
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const HARNESS_CORE_VENDOR_DIRS = [
  '.github',
  'app',
  'tools',
  'prompts',
  'config',
  'skills',
  'tests',
  'fixtures',
  'harnesses',
  'scripts',
  'docs',
]
export const HARNESS_CORE_VENDOR_FILES = [
  'VERSION',
  'CHANGELOG.md',
  'requirements.txt',
  'run.py',
  'install_manifest.json',
  'README.md',
  'HANDOFF.md',
  'real_precommit_trial_template.md',
  'scope_warning_policy.md',
]
export const VENDOR_MANIFEST_NAME = 'VENDOR_MANIFEST.json'
export const HARNESS_CORE_REQUIRED_PATHS = [
  '.github/workflows/enterprise-core.yml',
  'VERSION',
  'CHANGELOG.md',
  'scripts/verify.sh',
  'docs',
  'app/external_task_session.py',
  'tools/harness_host_server.py',
  'real_precommit_trial_template.md',
  'scope_warning_policy.md',
]

const EXCLUDED_PATH_PATTERNS = [
  /(^|\/)__pycache__(\/|$)/,
  /(^|\/)\.venv(\/|$)/,
  /(^|\/)\.git(\/|$)/,
  /(^|\/)\.DS_Store$/,
  /(^|\/)data(\/|$)/,
  /(^|\/)runs(\/|$)/,
  /\.pyc$/,
  /\.pyo$/,
  /(^|\/)\.tmp(\/|$)/,
]

/** 凭证赋值样式：json/env/toml 等数据文件里命中即失败；.py 里通常是凭证读取代码，仅提示。 */
const SECRET_ASSIGNMENT = /(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|private[_-]?key)["']?\s*[:=]\s*["'][A-Za-z0-9_\-+/=]{16,}["']/i
/** 占位符样式（模板/example 文件里的引用写法）不算真实凭证。 */
const PLACEHOLDER_VALUE = /(ref|placeholder|your|example|fill|replace|<[^>]*>|\{\{|xxx|todo|change[_-]?me|sentinel|self[_-]?check)/i
const SECRET_TEXT_FILES = /\.(json|jsonc|toml|ya?ml|env|ini|cfg|conf)$/i
const CODEX_WORKER_COMPATIBILITY_PATCH = 'codex-cli-0.150'
const MANAGER_DATABASE_READONLY_WORDING_PATCH = 'manager-database-permanent-readonly-wording'
const GOVERNANCE_AUTO_LOCAL_ARTIFACT_BOUNDARY_PATCH =
  'governance-auto-local-blocked-artifact-boundary'
const AUTO_LOCAL_BOUNDED_SCAN_PATCH = 'auto-local-bounded-scan-skip'
const AUTHORITATIVE_ACCEPTANCE_COMMAND_PATCH = 'authoritative-acceptance-verify-command'
const ENFORCE_UNDERSTANDING_RECONCILIATION_PATCH = 'enforce-understanding-governance-reconciliation'
const RELOCATABLE_PLUGIN_ROOT_PATCH = 'relocatable-plugin-root-resolution'
const RELOCATABLE_ENGINEERING_ADAPTER_PATCH = 'relocatable-his-engineering-adapter-root'
const CURRENT_ENGINEERING_ADAPTER_VERSION_PATCH = 'current-his-engineering-adapter-version'
const RELOCATABLE_PG_EVIDENCE_TEST_ROOT_PATCH = 'relocatable-pg-evidence-test-harness-root'
const CURRENT_POSTGRESQL_MCP_LEGACY_BRIDGE_PATCH = 'current-postgresql-mcp-legacy-bridge'
const RETIRE_STALE_DIRECT_PG_EVIDENCE_TESTS_PATCH =
  'retire-stale-direct-pg-evidence-tests'
const RELOCATABLE_EXTERNAL_IO_PLUGIN_ROOT_PATCH = 'relocatable-external-io-plugin-roots'
const IGNORE_BUNDLED_RUNTIME_EXTERNAL_IO_PATCH =
  'ignore-bundled-python-runtime-external-io-scan'
const EXTERNAL_IO_AUDIT_POLICY_REBIND_PATCH = 'external-io-audit-policy-rebind'
const RELOCATABLE_COMPATIBILITY_SKILL_DOC_ROOT_PATCH =
  'relocatable-compatibility-skill-documentation-root'
const RELOCATABLE_ROLE_REGISTRY_PLUGIN_ROOT_PATCH =
  'relocatable-role-registry-plugin-root-resolution'
const EXPLICIT_CODEX_VISUAL_EVIDENCE_PATCH =
  'explicit-codex-visual-evidence-adapter'
const FIFTEEN_STAGE_GOVERNANCE_TEST_PATCH =
  'fifteen-stage-governance-test-alignment'
const UNDERSTANDING_GATE_SCOPE_TEST_PATCH =
  'understanding-gate-scope-test-alignment'
const STRICT_REQUIREMENT_CALIBRATION_TEST_PATCH =
  'strict-requirement-calibration-test-alignment'
const SELF_CHECK_CHANGE_CONTEXT_FIXTURE_PATCH =
  'self-check-change-context-fixture'
const CURRENT_YUNXIAO_MCP_DEPENDENCY_TEST_PATCH =
  'current-yunxiao-mcp-dependency-test-alignment'
const NATIVE_READONLY_MCP_PROVIDER_STATUS_TEST_PATCH =
  'native-readonly-mcp-provider-status-test-alignment'
const PACKAGED_PYTHON_VERIFY_ENTRYPOINT_PATCH = 'packaged-python-verify-entrypoint'
const UNIQUE_OFFLINE_GATE_OUTPUT_DIRECTORY_PATCH =
  'unique-offline-gate-output-directory'
const VALIDATED_HARNESS_DECISION_AUDIT_PATCH =
  'validated-harness-decision-audit-digest'
const BOUNDED_EXPANDED_UNIT_GATE_TIMEOUT_PATCH =
  'bounded-expanded-unit-enterprise-gate-timeout'
export const HARNESS_COMPATIBILITY_SKILLS = [
  'his-harness',
  'harness-workitem-intake',
  'harness-history',
  'yunxiao-workitem-evidence',
]
const CODEX_WORKER_VERSION_CONTRACT_0149 = [
  '# The current bundled CLI is 0.149.x; its fixed `exec --json --ephemeral`',
  '# worker/reviewer flags remain compatible with the 0.147 contract. Keep the',
  '# upper bound so a future incompatible CLI still fails closed.',
  '_SUPPORTED_VERSION = ((0, 147, 0), (0, 150, 0))',
].join('\n')
const CODEX_WORKER_VERSION_CONTRACT_0150 = [
  '# The current bundled CLI is 0.150.x; its fixed `exec --json --ephemeral`',
  '# worker/reviewer flags remain compatible with the 0.147 contract. Keep the',
  '# upper bound so a future incompatible CLI still fails closed.',
  '_SUPPORTED_VERSION = ((0, 147, 0), (0, 151, 0))',
].join('\n')
const MANAGER_DATABASE_READONLY_CONTRACT_OLD =
  '数据库永久停留在只读与 SQL 草案层，不进入写执行器。'
const MANAGER_DATABASE_READONLY_CONTRACT_CURRENT =
  '数据库永久只读，停留在查询与 SQL 草案层，不进入写执行器。'
const CORE_CLOSURE_GOVERNANCE_CONTRACT_HARD_STOP = [
  '        if governance_execution_blocked:',
  '            block_reason = governance_error or',
].join('\n')
const CORE_CLOSURE_GOVERNANCE_CONTRACT_UNGUARDED = [
  '        if governance_execution_blocked and resolved_execution_mode != "core-closure-trial":',
  '            block_reason = governance_error or',
].join('\n')
const CORE_CLOSURE_GOVERNANCE_CONTRACT_PERMISSIVE = [
  '        if governance_execution_blocked and (',
  '            resolved_execution_mode != "core-closure-trial"',
  '            or (',
  '                routed_governance is not None',
  '                and getattr(routed_governance, "mode", None) == "enforce"',
  '            )',
  '        ):',
  '            block_reason = governance_error or',
].join('\n')
const CORE_CLOSURE_GOVERNANCE_CONTRACT_AUTO_LOCAL_ONLY = [
  '        if governance_execution_blocked and (',
  '            resolved_execution_mode != "core-closure-trial"',
  '            or requested_execution_mode != "auto-local"',
  '            or (',
  '                routed_governance is not None',
  '                and getattr(routed_governance, "mode", None) == "enforce"',
  '            )',
  '        ):',
  '            block_reason = governance_error or',
].join('\n')
const CORE_CLOSURE_GOVERNANCE_CONTRACT_CURRENT = [
  '        if governance_execution_blocked and (',
  '            resolved_execution_mode != "core-closure-trial"',
  '            or effective_governance_mode == "enforce"',
  '            or (',
  '                routed_governance is not None',
  '                and getattr(routed_governance, "mode", None) == "enforce"',
  '            )',
  '        ):',
  '            block_reason = governance_error or',
].join('\n')
const AUTO_LOCAL_SCAN_CONTRACT_OLD = [
  '            fast_local_decision',
  '            and fast_local_decision["skip_project_context_scan"]',
  '            and not mutation_requested',
  '        ):',
].join('\n')
const AUTO_LOCAL_SCAN_CONTRACT_UNGATED = [
  '            fast_local_decision',
  '            and fast_local_decision["skip_project_context_scan"]',
  '        ):',
].join('\n')
const AUTO_LOCAL_SCAN_CONTRACT_CURRENT = [
  '            fast_local_decision',
  '            and fast_local_decision.get("eligible") is True',
  '            and fast_local_decision["skip_project_context_scan"]',
  '        ):',
].join('\n')
const AUTHORITATIVE_ACCEPTANCE_CONTRACT_OLD = [
  '            if (',
  '                contract.status == "ready"',
  '                and capability_contract_authoritative',
  '                and acceptance_contract_result is not None',
  '                and acceptance_contract_result.status == "pass"',
  '                and acceptance_contract_result.verify_command',
  '                not in single_pass_contract.verify_commands',
  '            ):',
  '                contract = replace(',
  '                    contract,',
  '                    status="blocked",',
  '                    allowed_paths=(),',
  '                    verify_commands=(),',
  '                    blockers=(GOVERNANCE_ACCEPTANCE_ERROR,),',
  '                )',
  '            elif contract.status == "ready" and capability_contract_authoritative:',
  '                contract = replace(',
  '                    contract,',
  '                    allowed_paths=tuple(single_pass_contract.allowed_paths),',
  '                    verify_commands=tuple(single_pass_contract.verify_commands),',
  '                )',
].join('\n')
const AUTHORITATIVE_ACCEPTANCE_CONTRACT_CURRENT = [
  '            if contract.status == "ready" and capability_contract_authoritative:',
  '                authoritative_verify_commands = list(single_pass_contract.verify_commands)',
  '                if (',
  '                    acceptance_contract_result is not None',
  '                    and acceptance_contract_result.status == "pass"',
  '                    and acceptance_contract_result.verify_command',
  '                ):',
  '                    authoritative_verify_commands.append(acceptance_contract_result.verify_command)',
  '                contract = replace(',
  '                    contract,',
  '                    allowed_paths=tuple(single_pass_contract.allowed_paths),',
  '                    verify_commands=tuple(dict.fromkeys(authoritative_verify_commands)),',
  '                )',
].join('\n')
const UNDERSTANDING_STAGE_RECORD_OLD = [
  '        task_stages.record(',
  '            "understanding",',
  '            "blocked" if understanding_execution_blocked else "completed",',
  '            "understanding_blocked" if understanding_execution_blocked else "understanding_ready",',
  '        )',
].join('\n')
const UNDERSTANDING_GOVERNANCE_RECONCILIATION_CURRENT = [
  '        if (',
  '            understanding_execution_blocked',
  '            and effective_governance_mode == "enforce"',
  '            and not governance_execution_blocked',
  '            and _governance_outputs_ready(governance_result, single_pass_contract)',
  '            and all(',
  '                check.name in {',
  '                    "business_background",',
  '                    "usage_scenario",',
  '                    "target_and_boundary",',
  '                    "change_and_impact_scope",',
  '                    "verification_baseline",',
  '                }',
  '                for check in requirement_understanding.checks',
  '                if check.status == "blocked"',
  '            )',
  '        ):',
  '            requirement_understanding = replace(',
  '                requirement_understanding,',
  '                status="ready_for_change",',
  '                can_modify=True,',
  '                checks=tuple(',
  '                    replace(',
  '                        check,',
  '                        status="pass",',
  '                        summary="enforce 需求治理与一次改好合同已复核并闭合该项。",',
  '                        blockers=(),',
  '                    )',
  '                    if check.status == "blocked"',
  '                    else check',
  '                    for check in requirement_understanding.checks',
  '                ),',
  '                blockers=(),',
  '                next_readonly_actions=(),',
  '            )',
  '            understanding_execution_blocked = False',
  '        task_stages.record(',
  '            "understanding",',
  '            "blocked" if understanding_execution_blocked else "completed",',
  '            "understanding_blocked" if understanding_execution_blocked else "understanding_ready",',
  '        )',
  '        governance_ready = (',
].join('\n')
const RUNTIME_PLUGIN_ROOTS_CONTRACT_OLD = [
  '    return RuntimeConfig(',
  '        routing_mode,',
  '        tuple(roots),',
  '        external_writes_default,',
  '        timeout,',
  '        knowledge_home,',
  '    )',
].join('\n')
const RUNTIME_PLUGIN_ROOTS_CONTRACT_CURRENT = [
  '    config_directory = Path(path_value).expanduser().resolve().parent',
  '    resolved_roots = tuple(',
  '        str(',
  '            root.expanduser().resolve()',
  '            if root.is_absolute()',
  '            else (config_directory / root).resolve()',
  '        )',
  '        for root in (Path(item) for item in roots)',
  '    )',
  '    return RuntimeConfig(',
  '        routing_mode,',
  '        resolved_roots,',
  '        external_writes_default,',
  '        timeout,',
  '        knowledge_home,',
  '    )',
].join('\n')
const RELATIVE_PLUGIN_ROOT_TEST_ANCHOR_OLD = [
  '        self.assertEqual("success", preview_payload["data"]["execution"]["result"]["status"])',
  '',
  '    def test_invalid_config_is_stable_json_failure(self) -> None:',
].join('\n')
const RELATIVE_PLUGIN_ROOT_TEST_ANCHOR_CURRENT = [
  '        self.assertEqual("success", preview_payload["data"]["execution"]["result"]["status"])',
  '',
  '    def test_relative_plugin_root_resolves_from_the_runtime_config_directory(self) -> None:',
  '        self._write_config(plugin_roots=["plugin"])',
  '',
  '        completed = self.run_cli("list", "--json")',
  '',
  '        self.assertEqual(0, completed.returncode, completed.stderr)',
  '        self.assertEqual("success", self.payload(completed)["status"])',
  '',
  '    def test_invalid_config_is_stable_json_failure(self) -> None:',
].join('\n')
const PLUGIN_INVENTORY_ROOTS_CONTRACT_OLD =
  'FORMAL_PLUGIN_ROOTS = tuple(f"/Users/lym/plugins/{name}" for name in PLUGIN_NAMES)'
const PLUGIN_INVENTORY_ROOTS_CONTRACT_CURRENT = [
  '_RUNTIME_CONFIG_PATH = HARNESS_ROOT / "config" / "capabilities.json"',
  '_RUNTIME_PLUGIN_ROOTS = json.loads(',
  '    _RUNTIME_CONFIG_PATH.read_text(encoding="utf-8")',
  ')["plugin_roots"]',
  'FORMAL_PLUGIN_ROOTS = tuple(',
  '    str(',
  '        Path(root).expanduser().resolve()',
  '        if Path(root).is_absolute()',
  '        else (_RUNTIME_CONFIG_PATH.parent / root).resolve()',
  '    )',
  '    for root in _RUNTIME_PLUGIN_ROOTS',
  ')',
].join('\n')
const ENGINEERING_ADAPTER_ROOT_OLD =
  '_FIXED_ROOT = Path("/Users/lym/plugins/his-engineering")'
const ENGINEERING_ADAPTER_ROOT_CURRENT = [
  '_FIXED_ROOT = Path("/Users/lym/plugins/his-engineering")',
  '_BUNDLED_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "his-engineering"',
].join('\n')
const ENGINEERING_ADAPTER_CANDIDATE_OLD = '    candidates.append(_FIXED_ROOT)'
const ENGINEERING_ADAPTER_CANDIDATE_UNGUARDED = [
  '    candidates.append(_BUNDLED_ROOT)',
  '    candidates.append(_FIXED_ROOT)',
].join('\n')
const ENGINEERING_ADAPTER_CANDIDATE_CURRENT = [
  '    if not test_root:',
  '        candidates.append(_BUNDLED_ROOT)',
  '    candidates.append(_FIXED_ROOT)',
].join('\n')
const ENGINEERING_ADAPTER_MANIFEST_VERSION_OLD =
  '        or manifest.get("plugin_version") != "0.1.0"'
const ENGINEERING_ADAPTER_MANIFEST_VERSION_CURRENT = [
  '        or not isinstance(manifest.get("plugin_version"), str)',
  '        or not manifest["plugin_version"].strip()',
].join('\n')
const ENGINEERING_ADAPTER_DESCRIPTOR_VERSION_OLD =
  '        or plugin.get("version") != "0.1.0"'
const ENGINEERING_ADAPTER_DESCRIPTOR_VERSION_CURRENT =
  '        or plugin.get("version") != manifest.get("plugin_version")'
const ENGINEERING_ADAPTER_CAPABILITY_VERSION_OLD =
  '            or descriptor.plugin_version != "0.1.0"'
const ENGINEERING_ADAPTER_CAPABILITY_VERSION_CURRENT =
  '            or descriptor.plugin_version != _read_json(root / "capabilities.json").get("plugin_version")'
const ENGINEERING_ADAPTER_INVENTORY_PINNED_VERSION_CURRENT = [
  '        or manifest.get("plugin_version") != expected_plugin.version',
  '        or plugin.get("version") != expected_plugin.version',
  '            or descriptor.plugin_version != expected_plugin.version',
]
const PG_EVIDENCE_TEST_IMPORT_OLD =
  'from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT, REPOSITORY_ROOT'
const PG_EVIDENCE_TEST_IMPORT_CURRENT =
  'from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT'
const PG_EVIDENCE_TEST_ROOT_OLD = [
  'ROOT = REPOSITORY_ROOT',
  'HARNESS_ROOT = ROOT / "Harness"',
].join('\n')
const PG_EVIDENCE_TEST_ROOT_CURRENT =
  'HARNESS_ROOT = Path(__file__).resolve().parents[1]'
const PG_EVIDENCE_TEST_MCP_ONLY_CURRENT = [
  PG_EVIDENCE_TEST_ROOT_CURRENT,
  'CLI_PATH = HARNESS_ROOT / "tools" / "pg_evidence.py"',
  'The compatibility contract is now fail-closed MCP-only retirement.',
]
const PG_EVIDENCE_DATACLASS_IMPORT_OLD = [
  'from __future__ import annotations',
  '',
  'import hashlib',
].join('\n')
const PG_EVIDENCE_DATACLASS_IMPORT_CURRENT = [
  'from __future__ import annotations',
  '',
  'from dataclasses import replace',
  '',
  'import hashlib',
].join('\n')
const PG_EVIDENCE_ENTRYPOINT_DISCOVERY_OLD = [
  '        expected_entrypoint = (',
  '            resolved_root / "scripts" / "database_read.py"',
  '        ).resolve(strict=True)',
].join('\n')
const PG_EVIDENCE_ENTRYPOINT_DISCOVERY_CURRENT = [
  '        legacy_entrypoint = (',
  '            resolved_root / "scripts" / "database_read.py"',
  '        ).resolve(strict=True)',
  '        mcp_entrypoint_path = resolved_root / "scripts" / "postgresql_mcp_server.py"',
  '        mcp_entrypoint = (',
  '            mcp_entrypoint_path.resolve(strict=True)',
  '            if mcp_entrypoint_path.is_file()',
  '            else None',
  '        )',
].join('\n')
const PG_EVIDENCE_DESCRIPTOR_CONTRACT_OLD = [
  '        or descriptor.scopes != _EXPECTED_SCOPES',
  '        or descriptor.plugin_root != resolved_root',
  '        or descriptor.entrypoint != expected_entrypoint',
  '        or descriptor.declared_entrypoint',
  '        != resolved_root / "scripts" / "database_read.py"',
].join('\n')
const PG_EVIDENCE_DESCRIPTOR_CONTRACT_CURRENT = [
  '        or (descriptor.entrypoint, descriptor.scopes)',
  '        not in {',
  '            (legacy_entrypoint, _EXPECTED_SCOPES),',
  '            (mcp_entrypoint, ("database:inspect",)),',
  '        }',
  '        or descriptor.plugin_root != resolved_root',
  '        or descriptor.declared_entrypoint != descriptor.entrypoint',
].join('\n')
const PG_EVIDENCE_COMPATIBILITY_REGISTRY_OLD = [
  '    registry = CapabilityRegistry.from_plugin_roots(',
  '        [Path(__provider_root__)]',
  '    )',
  '    return CapabilityService(',
].join('\n')
const PG_EVIDENCE_COMPATIBILITY_REGISTRY_CURRENT = [
  '    root = Path(__provider_root__).resolve(strict=True)',
  '    registry = CapabilityRegistry.from_plugin_roots([root])',
  '    descriptor = registry.resolve("database.inspect", "postgresql")',
  '    legacy_entrypoint = (root / "scripts" / "database_read.py").resolve(strict=True)',
  '    provider_dependency = (root / "scripts" / "pg_evidence.py").resolve(strict=True)',
  '    legacy_descriptor = replace(',
  '        descriptor,',
  '        entrypoint=legacy_entrypoint,',
  '        declared_entrypoint=legacy_entrypoint,',
  '        entrypoint_identity=registry_path_identity(legacy_entrypoint),',
  '        dependency_identities=((provider_dependency, registry_path_identity(provider_dependency)),),',
  '        scopes=_EXPECTED_SCOPES,',
  '    )',
  '    registry = CapabilityRegistry([',
  '        legacy_descriptor',
  '        if (item.name, item.provider) == ("database.inspect", "postgresql")',
  '        else item',
  '        for item in registry.descriptors',
  '    ])',
  '    return CapabilityService(',
].join('\n')
const PG_EVIDENCE_FAIL_CLOSED_TOMBSTONE_CURRENT = [
  'LEGACY_PG_EVIDENCE_DISABLED = True',
  'LEGACY_PG_EVIDENCE_DISABLED_USE_DATABASE_INSPECT_MCP',
  'def require_database_inspect_mcp() -> None:',
]
const PG_EVIDENCE_STALE_DIRECT_TEST_CONTRACT = [
  'from app.pg_evidence import (',
  '    DEFAULT_SENSITIVE_COLUMN_PATTERNS,',
  '    PgEvidenceRequest,',
]
const PG_EVIDENCE_RETIRED_TEST_MARKER = 'class PgEvidenceRetirementTests'
const PG_EVIDENCE_RETIRED_TEST_CURRENT = [
  'from __future__ import annotations',
  '',
  'import unittest',
  '',
  'from app import pg_evidence',
  '',
  '',
  'class PgEvidenceRetirementTests(unittest.TestCase):',
  '    """The direct PostgreSQL adapter was replaced by database.inspect MCP."""',
  '',
  '    def test_legacy_direct_adapter_remains_fail_closed(self) -> None:',
  '        self.assertTrue(pg_evidence.LEGACY_PG_EVIDENCE_DISABLED)',
  '        self.assertEqual(',
  '            "LEGACY_PG_EVIDENCE_DISABLED_USE_DATABASE_INSPECT_MCP",',
  '            pg_evidence.LEGACY_PG_EVIDENCE_ERROR_CODE,',
  '        )',
  '        self.assertFalse(hasattr(pg_evidence, "PgEvidenceRequest"))',
  '        self.assertFalse(hasattr(pg_evidence, "run_pg_evidence"))',
  '',
  '',
  'if __name__ == "__main__":',
  '    unittest.main()',
  '',
].join('\n')
const EXTERNAL_IO_CONFIG_DIRECTORY_OLD = '    result: dict[str, Path] = {}'
const EXTERNAL_IO_CONFIG_DIRECTORY_CURRENT = [
  '    config_directory = capabilities_config_path.expanduser().resolve().parent',
  '    result: dict[str, Path] = {}',
].join('\n')
const EXTERNAL_IO_PLUGIN_ROOT_OLD = '        root = Path(raw_root).resolve()'
const EXTERNAL_IO_PLUGIN_ROOT_CURRENT = [
  '        candidate = Path(raw_root).expanduser()',
  '        root = (candidate if candidate.is_absolute() else config_directory / candidate).resolve()',
].join('\n')
const EXTERNAL_IO_IGNORED_DIRECTORIES_OLD = [
  '        "outputs",',
  '        "tests",',
].join('\n')
const EXTERNAL_IO_IGNORED_DIRECTORIES_CURRENT = [
  '        "outputs",',
  '        "runtime",',
  '        "tests",',
].join('\n')
const POSTGRESQL_MCP_EXTERNAL_IO_FINDINGS_OLD = [
  { category: 'database', symbol: 'psycopg.connect', occurrence: 1 },
  { category: 'database', symbol: 'psycopg2.connect', occurrence: 1 },
]
const POSTGRESQL_MCP_EXTERNAL_IO_FINDINGS_CURRENT = [
  { category: 'database', symbol: 'psycopg.connect', occurrence: 1 },
  { category: 'database', symbol: 'psycopg.connect', occurrence: 2 },
  { category: 'database', symbol: 'psycopg2.connect', occurrence: 1 },
  { category: 'database', symbol: 'psycopg2.connect', occurrence: 2 },
]
const COMPATIBILITY_SKILL_EXTERNAL_IO_FINDINGS = [
  { category: 'process', symbol: 'python3', occurrence: 1 },
]
const VERIFY_PYTHON_CONTRACT_OLD = [
  'PYTHON="$ROOT_DIR/.venv/bin/python"',
  '',
  'if [ ! -x "$PYTHON" ]; then',
  '  echo "Harness .venv interpreter is missing: $PYTHON" >&2',
  '  exit 2',
  'fi',
].join('\n')
const VERIFY_PYTHON_CONTRACT_CURRENT = [
  'PACKAGED_PYTHON="$ROOT_DIR/runtime/bin/python3"',
  'VENV_PYTHON="$ROOT_DIR/.venv/bin/python"',
  '',
  'if [ -x "$PACKAGED_PYTHON" ]; then',
  '  PYTHON="$PACKAGED_PYTHON"',
  'elif [ -x "$VENV_PYTHON" ]; then',
  '  PYTHON="$VENV_PYTHON"',
  'else',
  '  echo "Harness interpreter is missing: $PACKAGED_PYTHON or $VENV_PYTHON" >&2',
  '  exit 2',
  'fi',
].join('\n')
const VERIFY_ENTRYPOINT_TEST_CONTRACT_OLD = [
  '    def test_verify_script_uses_project_venv_and_no_system_python(self) -> None:',
  '        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")',
  '',
  '        self.assertIn(\'PYTHON="$ROOT_DIR/.venv/bin/python"\', script)',
  '        self.assertIn(\'export PYTHONDONTWRITEBYTECODE="1"\', script)',
  '        self.assertNotIn("python3 -m unittest", script)',
].join('\n')
const VERIFY_ENTRYPOINT_TEST_CONTRACT_CURRENT = [
  '    def test_verify_script_uses_packaged_runtime_with_project_venv_fallback(self) -> None:',
  '        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")',
  '',
  '        self.assertIn(\'PACKAGED_PYTHON="$ROOT_DIR/runtime/bin/python3"\', script)',
  '        self.assertIn(\'VENV_PYTHON="$ROOT_DIR/.venv/bin/python"\', script)',
  '        self.assertIn(\'export PYTHONDONTWRITEBYTECODE="1"\', script)',
  '        self.assertNotIn("python3 -m unittest", script)',
].join('\n')
const VERIFY_OFFLINE_OUTPUT_DIRECTORY_OLD =
  '    OUTPUT_DIR="${HARNESS_GATE_OUTPUT_DIR:-/private/tmp/his-harness-enterprise-gate}"'
const VERIFY_OFFLINE_OUTPUT_DIRECTORY_CURRENT = [
  '    if [ -n "${HARNESS_GATE_OUTPUT_DIR:-}" ]; then',
  '      OUTPUT_DIR="$HARNESS_GATE_OUTPUT_DIR"',
  '    else',
  '      OUTPUT_DIR="$(mktemp -d "${TMPDIR:-/private/tmp}/his-harness-enterprise-gate.XXXXXX")"',
  '    fi',
].join('\n')
const VERIFY_OFFLINE_OUTPUT_TEST_CURRENT = [
  '    def test_offline_gate_uses_unique_output_by_default(self) -> None:',
  '        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")',
  '',
  '        self.assertIn(\'if [ -n "${HARNESS_GATE_OUTPUT_DIR:-}" ]; then\', script)',
  '        self.assertIn(\'mktemp -d "${TMPDIR:-/private/tmp}/his-harness-enterprise-gate.XXXXXX"\', script)',
  '        self.assertNotIn(\'${HARNESS_GATE_OUTPUT_DIR:-/private/tmp/his-harness-enterprise-gate}\', script)',
].join('\n')
const VERIFY_ENTRYPOINT_TEST_MODULE_ANCHOR = '\n\nif __name__ == "__main__":\n'
const ENTERPRISE_GATE_TIMEOUT_DECLARATION_OLD = 'STAGE_TIMEOUT_SECONDS = 300'
const ENTERPRISE_GATE_TIMEOUT_DECLARATION_CURRENT = [
  'STAGE_TIMEOUT_SECONDS = 300',
  'UNIT_STAGE_TIMEOUT_SECONDS = 1200',
].join('\n')
const ENTERPRISE_GATE_TIMEOUT_REPORT_OLD =
  '        "stage_timeout_seconds": STAGE_TIMEOUT_SECONDS,'
const ENTERPRISE_GATE_TIMEOUT_REPORT_CURRENT = [
  '        "stage_timeout_seconds": STAGE_TIMEOUT_SECONDS,',
  '        "unit_stage_timeout_seconds": UNIT_STAGE_TIMEOUT_SECONDS,',
].join('\n')
const ENTERPRISE_GATE_STAGE_TIMEOUT_FUNCTION = [
  'def stage_timeout_seconds(stage: str) -> int:',
  '    return UNIT_STAGE_TIMEOUT_SECONDS if stage == "unit" else STAGE_TIMEOUT_SECONDS',
  '',
  '',
].join('\n')
const ENTERPRISE_GATE_STAGE_TIMEOUT_ANCHOR = 'def run_gate_stage('
const ENTERPRISE_GATE_SUBPROCESS_TIMEOUT_OLD = '            timeout=STAGE_TIMEOUT_SECONDS,'
const ENTERPRISE_GATE_SUBPROCESS_TIMEOUT_CURRENT =
  '            timeout=stage_timeout_seconds(stage),'
const ENTERPRISE_GATE_TEST_IMPORT_OLD = [
  '    run_gate_stage,',
  '    sanitize_environment,',
].join('\n')
const ENTERPRISE_GATE_TEST_IMPORT_CURRENT = [
  '    run_gate_stage,',
  '    sanitize_environment,',
  '    stage_timeout_seconds,',
].join('\n')
const ENTERPRISE_GATE_TEST_REPORT_OLD =
  '        self.assertEqual(300, result["stage_timeout_seconds"])'
const ENTERPRISE_GATE_TEST_REPORT_CURRENT = [
  '        self.assertEqual(300, result["stage_timeout_seconds"])',
  '        self.assertEqual(1200, result["unit_stage_timeout_seconds"])',
  '        self.assertEqual(1200, stage_timeout_seconds("unit"))',
].join('\n')
const ENTERPRISE_GATE_TEST_TIMEOUT_OLD =
  '                side_effect=subprocess.TimeoutExpired([sys.executable], 300, output="", stderr=""),'
const ENTERPRISE_GATE_TEST_TIMEOUT_CURRENT =
  '                side_effect=subprocess.TimeoutExpired([sys.executable], 1200, output="", stderr=""),'
const COMPATIBILITY_SKILL_DOC_ROOT_OLD = 'REPOSITORY_ROOT / "skills"'
const COMPATIBILITY_SKILL_DOC_ROOT_CURRENT = 'HARNESS_ROOT / "skills"'
const ROLE_REGISTRY_PLUGIN_ROOTS_OLD = [
  '        config = _read_json(harness_root / "config" / "capabilities.json")',
  '        raw_roots = config.get("plugin_roots")',
  '        if not isinstance(raw_roots, list):',
  '            raise RoleCapabilitySkillRegistryError("capabilities.json 缺少 plugin_roots。")',
  '        roots = {}',
  '        for value in raw_roots:',
  '            path = Path(value).resolve()',
].join('\n')
const ROLE_REGISTRY_PLUGIN_ROOTS_CURRENT = [
  '        config_path = harness_root / "config" / "capabilities.json"',
  '        config = _read_json(config_path)',
  '        raw_roots = config.get("plugin_roots")',
  '        if not isinstance(raw_roots, list):',
  '            raise RoleCapabilitySkillRegistryError("capabilities.json 缺少 plugin_roots。")',
  '        roots = {}',
  '        for value in raw_roots:',
  '            candidate = Path(value).expanduser()',
  '            path = (',
  '                candidate.resolve()',
  '                if candidate.is_absolute()',
  '                else (config_path.parent / candidate).resolve()',
  '            )',
].join('\n')
const VISUAL_ANALYZER_ANCHOR = 'class HostVisualEvidenceAnalyzer:'
const VISUAL_ANALYZER_CURRENT = [
  'class _SilentVisualWorkerSink:',
  '    def on_started(self, pid: int, start_identity: str) -> None:',
  '        del pid, start_identity',
  '',
  '    def on_event(self, event: dict[str, object]) -> None:',
  '        del event',
  '',
  '',
  'class CodexCliVisualEvidenceAnalyzer:',
  '    """Run an explicitly selected, read-only Codex visual reviewer."""',
  '',
  '    def __init__(',
  '        self,',
  '        *,',
  '        worker: CodexCliWorker | None = None,',
  '        timeout_seconds: int = 120,',
  '        schema_path: str | Path | None = None,',
  '    ) -> None:',
  '        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 3600:',
  '            raise ValueError("visual_evidence_timeout_invalid")',
  '        target = Path(schema_path or Path(__file__).resolve().parents[1] / "config" / "schemas" / "visual_evidence.v1.json")',
  '        if not target.is_absolute() or target.is_symlink() or not target.is_file():',
  '            raise ValueError("visual_evidence_schema_invalid")',
  '        self._worker = worker or CodexCliWorker()',
  '        self._timeout_seconds = timeout_seconds',
  '        self._schema_path = target.resolve()',
  '        self._schema_sha256 = hashlib.sha256(self._schema_path.read_bytes()).hexdigest()',
  '',
  '    def analyze(',
  '        self,',
  '        *,',
  '        title: str,',
  '        description: str,',
  '        image_paths: tuple[Path, ...],',
  '    ) -> Mapping[str, Any]:',
  '        try:',
  '            extraction_request = VisualEvidenceExtractionRequest(',
  '                title=title[:500],',
  '                description=description[:2000],',
  '                image_paths=image_paths,',
  '            )',
  '            worker_request = CodexWorkerRequest.visual_reviewer(',
  '                Path(__file__).resolve().parents[1],',
  '                _prompt(extraction_request.title, extraction_request.description),',
  '                self._timeout_seconds,',
  '                self._schema_path,',
  '                self._schema_sha256,',
  '                extraction_request.image_paths,',
  '            )',
  '            worker_result = self._worker.start(worker_request, _SilentVisualWorkerSink())',
  '            if str(getattr(worker_result, "error_code", "") or ""):',
  '                return {"facts": [], "blockers": ["视觉证据读取失败；已保持改码门禁关闭。"]}',
  '            result = parse_visual_evidence_result(',
  '                getattr(worker_result, "final_response", None),',
  '                image_paths=extraction_request.image_paths,',
  '            )',
  '        except (OSError, UnicodeError, ValueError, TypeError):',
  '            return {"facts": [], "blockers": ["视觉证据结果无效；已保持改码门禁关闭。"]}',
  '        return {',
  '            "facts": [dict(item) for item in result.facts],',
  '            "blockers": list(result.blockers),',
  '            "host": {"type": "codex_cli", "executable": str(CODEX_EXECUTABLE)},',
  '        }',
  '',
  '',
].join('\n')
const VISUAL_EVIDENCE_SCHEMA = `${JSON.stringify({
  type: 'object',
  additionalProperties: false,
  properties: {
    schema_version: { type: 'string', const: 'his-visual-evidence.v1' },
    facts: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          fact_type: { type: 'string', enum: ['ui_trace', 'document'] },
          image_path: { type: 'string' },
          error_text: { type: 'string' },
          menu: { type: 'string' },
          action: { type: 'string' },
          business_scene: { type: 'string' },
          target_module: { type: 'string' },
          document_type: { type: 'string' },
          visible_text: { type: 'string' },
          key_facts: { type: 'string' },
        },
        required: ['fact_type', 'image_path', 'target_module'],
      },
    },
    blockers: { type: 'array', items: { type: 'string' } },
  },
  required: ['schema_version', 'facts', 'blockers'],
}, null, 2)}\n`
const DEFAULT_MANAGER_STAGE_TEST_TWELVE = [
  '    def test_default_manager_task_dispatch_runs_local_twelve_stage_governance(self) -> None:',
  '        with (',
].join('\n')
const DEFAULT_MANAGER_STAGE_TEST_THIRTEEN = [
  '    def test_default_manager_task_dispatch_runs_local_thirteen_stage_governance(self) -> None:',
  '        with (',
].join('\n')
const DEFAULT_MANAGER_STAGE_TEST_CURRENT = [
  '    def test_default_manager_task_dispatch_runs_local_fifteen_stage_governance(self) -> None:',
  '        with (',
].join('\n')
const DEFAULT_MANAGER_STAGE_ASSERTION_TWELVE = [
  '        self.assertEqual("requirement_workflow", result["downstream"])',
  '        self.assertEqual(12, workflow["stage_count"])',
  '        self.assertEqual("local_deterministic", workflow["analysis_backend"])',
].join('\n')
const DEFAULT_MANAGER_STAGE_ASSERTION_THIRTEEN = [
  '        self.assertEqual("requirement_workflow", result["downstream"])',
  '        self.assertEqual(13, workflow["stage_count"])',
  '        self.assertEqual("local_deterministic", workflow["analysis_backend"])',
].join('\n')
const DEFAULT_MANAGER_STAGE_ASSERTION_CURRENT = [
  '        self.assertEqual("requirement_workflow", result["downstream"])',
  '        self.assertEqual(15, workflow["stage_count"])',
  '        self.assertEqual("local_deterministic", workflow["analysis_backend"])',
].join('\n')
const PROVIDER_STATUS_AVAILABLE_CAPABILITIES_OLD = [
  '        available_capabilities = {',
  '            "git.diff",',
  '            "source.read",',
  '            "source.search",',
  '            "git.history",',
  '            "verification.run-local",',
  '            "code.review-local",',
  '        }',
].join('\n')
const PROVIDER_STATUS_AVAILABLE_CAPABILITIES_CURRENT = [
  '        available_capabilities = {',
  '            "git.diff",',
  '            "source.read",',
  '            "source.search",',
  '            "git.history",',
  '            "verification.run-local",',
  '            "code.review-local",',
  '            "workitem.read",',
  '            "gitlab.read",',
  '            "database.inspect",',
  '        }',
].join('\n')
const LEGACY_SCOPE_GATE_EXPECTATIONS_OLD = [
  '                    self.assertEqual(',
  '                        "awaiting_pre_change_scope_confirmation",',
  '                        first.evaluation_status,',
  '                    )',
  '                    self.assertEqual(0, executor.call_count)',
  '',
  '                    artifacts = database.get_artifacts(first.run_id)',
  '                    confirmation = json.loads(',
  '                        next(',
  '                            item["content"]',
  '                            for item in reversed(artifacts)',
  '                            if item["kind"] == "pre_change_confirmation_json"',
  '                        )',
  '                    )',
  '                    self.assertEqual("pending", confirmation["status"])',
  '                    self.assertTrue(confirmation["confirmation_token"].startswith("CONFIRM-SCOPE:"))',
  '',
  '                    second = runner.run(',
  '                        **options,',
  '                        pre_change_confirmation=confirmation["confirmation_token"],',
  '                    )',
  '                    self.assertEqual("failed", second.status)',
  '                    self.assertEqual("pass", second.evaluation_status)',
  '                    self.assertIn("需求治理未闭合", second.markdown_report)',
  '                    self.assertEqual(0, executor.call_count)',
].join('\n')
const LEGACY_SCOPE_GATE_EXPECTATIONS_BLOCKED_REPEAT = [
  '                    self.assertEqual(',
  '                        "blocked_requirement_governance",',
  '                        first.evaluation_status,',
  '                    )',
  '                    self.assertEqual(0, executor.call_count)',
  '',
  '                    artifacts = database.get_artifacts(first.run_id)',
  '                    confirmation = json.loads(',
  '                        next(',
  '                            item["content"]',
  '                            for item in reversed(artifacts)',
  '                            if item["kind"] == "pre_change_confirmation_json"',
  '                        )',
  '                    )',
  '                    self.assertEqual("blocked", confirmation["status"])',
  '                    self.assertTrue(confirmation["confirmation_token"].startswith("CONFIRM-SCOPE:"))',
  '',
  '                    second = runner.run(',
  '                        **options,',
  '                        pre_change_confirmation=confirmation["confirmation_token"],',
  '                    )',
  '                    self.assertEqual("blocked", second.status)',
  '                    self.assertEqual("blocked_requirement_governance", second.evaluation_status)',
  '                    self.assertIn("改码前理解证据包未就绪", second.markdown_report)',
  '                    self.assertEqual(0, executor.call_count)',
].join('\n')
const LEGACY_SCOPE_GATE_EXPECTATIONS_INVERTED = [
  '                    self.assertEqual(',
  '                        "blocked_requirement_governance",',
  '                        first.evaluation_status,',
  '                    )',
  '                    self.assertEqual(0, executor.call_count)',
  '',
  '                    artifacts = database.get_artifacts(first.run_id)',
  '                    confirmation = json.loads(',
  '                        next(',
  '                            item["content"]',
  '                            for item in reversed(artifacts)',
  '                            if item["kind"] == "pre_change_confirmation_json"',
  '                        )',
  '                    )',
  '                    self.assertEqual("blocked", confirmation["status"])',
  '                    self.assertTrue(confirmation["confirmation_token"].startswith("CONFIRM-SCOPE:"))',
  '',
  '                    second = runner.run(',
  '                        **options,',
  '                        pre_change_confirmation=confirmation["confirmation_token"],',
  '                    )',
  '                    self.assertEqual("blocked", second.status)',
  '                    self.assertEqual("awaiting_pre_change_scope_confirmation", second.evaluation_status)',
  '                    self.assertIn("改动前范围确认未通过", second.markdown_report)',
  '                    self.assertEqual(0, executor.call_count)',
].join('\n')
const LEGACY_SCOPE_GATE_EXPECTATIONS_PARTIAL = LEGACY_SCOPE_GATE_EXPECTATIONS_OLD
  .replace('self.assertEqual("failed", second.status)', 'self.assertEqual("blocked", second.status)')
  .replace(
    'self.assertEqual("pass", second.evaluation_status)',
    'self.assertEqual("blocked_requirement_governance", second.evaluation_status)',
  )
  .replace('需求治理未闭合', '改码前理解证据包未就绪')
const LEGACY_SCOPE_GATE_EXPECTATIONS_CURRENT = LEGACY_SCOPE_GATE_EXPECTATIONS_OLD
const SORTING_CLOSURE_STALE_ASSERTION =
  '        self.assertIn("可执行排序验收契约", "\\n".join(closure["contract"]["blockers"]))'
const SORTING_CLOSURE_CURRENT_ASSERTIONS = [
  '        self.assertIn("需求校准未达到 ready_for_development", "\\n".join(closure["contract"]["blockers"]))',
  '        artifacts = {item["kind"] for item in database.get_artifacts(result.run_id)}',
  '        self.assertNotIn("worktree_manifest_json", artifacts)',
  '        self.assertEqual([], database.get_step_runs(result.run_id))',
].join('\n')
const SELF_CHECK_CONTEXT_IMPORT_ANCHOR =
  'from app.fullstack_executor import FullstackExecutionOptions, FullstackWorktreeExecutor\n'
const SELF_CHECK_CONTEXT_IMPORTS = [
  'from app.change_context_contracts import (',
  '    ChangeContextGateResult,',
  '    ChangeContextLayer,',
  '    ChangeContextPack,',
  '    TaskBinding,',
  '    content_hash,',
  ')',
  'from app.change_context_execution import (',
  '    ChangeContextExecutionBinding,',
  '    ChangeContextExecutionVerifier,',
  ')',
  'from app.change_context_gate import ChangeContextGate',
  'from app.change_context_projection import ChangeContextProjectionService',
  SELF_CHECK_CONTEXT_IMPORT_ANCHOR.trimEnd(),
].join('\n') + '\n'
const SELF_CHECK_TASK_MANAGER_IMPORT_ANCHOR =
  'from app.task_manager import TaskCreateOptions, TaskDashboardFilters, TaskExistingRunOptions, TaskManager, TaskPrecommitRerunOptions, build_latest_artifacts\n'
const SELF_CHECK_TASK_MANAGER_IMPORT_CURRENT =
  SELF_CHECK_TASK_MANAGER_IMPORT_ANCHOR + 'import app.task_manager as task_manager_module\n'
const SELF_CHECK_HELPER_ANCHOR = '\n\nREQUIRED_FILES = [\n'
const SELF_CHECK_HELPER = `

class _SelfCheckContextRepository:
    def __init__(self, pack: ChangeContextPack, payloads: dict[str, dict[str, object]]) -> None:
        self.pack = pack
        self.layers = {
            layer.layer_id: (layer, payloads[layer.layer_type])
            for layer in pack.layers
        }

    def get_pack(self, pack_id: str) -> ChangeContextPack:
        if pack_id != self.pack.pack_id:
            raise KeyError(pack_id)
        return self.pack

    def get_layer(self, layer_id: str):
        if layer_id not in self.layers:
            raise KeyError(layer_id)
        return self.layers[layer_id]

    def get_successor_pack_id(self, pack_id: str) -> str:
        if pack_id != self.pack.pack_id:
            raise KeyError(pack_id)
        return ""

    def record_projection_metric(self, **kwargs) -> None:
        del kwargs


class _SelfCheckChangeContext:
    """Deterministic, sealed ChangeContext used only by local self-check fixtures."""

    def __init__(self) -> None:
        payloads = {
            "project_graph": {
                "schema_version": "project-graph.v1",
                "projects": [{"name": "self-check", "role": "application", "exists": True}],
                "relationships": [],
                "explicit_scope": True,
            },
            "change_scope": {
                "schema_version": "change-scope.v1",
                "provider": "self-check",
                "ticket_id": "SELF-CHECK-1",
                "requirement_revision": "sealed-fixture-v1",
                "current_user_correction": "execute only the isolated self-check fixture",
                "calibrated_scope": {"do": "isolated fixture validation", "do_not": ["external writes"]},
            },
            "code_graph": {
                "schema_version": "code-graph.v1",
                "target_paths": ["src/App.js", "src/view.vue"],
                "tests": ["self-check"],
                "call_edges": [],
                "file_hashes": [],
            },
            "data_graph": {
                "schema_version": "data-graph.v1",
                "decision": "not_applicable",
                "reason": "self-check fixtures do not access business data",
                "missing": [],
                "conflicts": [],
            },
        }
        layers = []
        for layer_type, payload in payloads.items():
            digest = content_hash(payload)
            layers.append(
                ChangeContextLayer.create(
                    layer_type=layer_type,
                    status="not_applicable" if layer_type == "data_graph" else "complete",
                    payload=payload,
                    source_fingerprint=digest,
                    artifact_ref=f"artifact://sha256/{digest.removeprefix('sha256:')}",
                    evidence_refs=(f"evidence://{layer_type}/self-check",),
                    policy_rule_ids=("CTX-SELF-CHECK-SEALED",),
                    blockers=(),
                )
            )
        gate_result = ChangeContextGateResult("ready", "CHANGE_CONTEXT_READY", (), (), ())
        self.pack = ChangeContextPack.create(
            pack_version=1,
            status="ready",
            task_binding=TaskBinding(
                "self-check",
                "SELF-CHECK-1",
                "sealed-fixture-v1",
                "sha256:" + "a" * 64,
            ),
            required_layers=("project_graph", "change_scope", "code_graph"),
            layers=layers,
            gate=gate_result,
        )
        self.repository = _SelfCheckContextRepository(self.pack, payloads)
        self.gate = ChangeContextGate()
        self.projections = {
            role: ChangeContextProjectionService().render(
                pack=self.pack,
                layer_payloads=payloads,
                role=role,
            )
            for role in ("implementation", "review")
        }
        self.verifier = ChangeContextExecutionVerifier(
            repository=self.repository,
            gate=self.gate,
        )

    def bind(self, options, role: str) -> None:
        projection = self.projections[role]
        binding = ChangeContextExecutionBinding(
            pack_id=self.pack.pack_id,
            projection_hash=projection.projection_hash,
            layer_hashes={layer.layer_type: layer.content_hash for layer in self.pack.layers},
        )
        options.change_context_binding = binding.to_dict()
        options.change_context_projection = projection.to_dict()


_SELF_CHECK_CHANGE_CONTEXT = _SelfCheckChangeContext()


class _SelfCheckWorktreeExecutor:
    def __init__(self, llm_client) -> None:
        self.delegate = WorktreeCodeExecutor(
            llm_client,
            change_context_verifier=_SELF_CHECK_CHANGE_CONTEXT.verifier,
        )

    def execute(self, options):
        _SELF_CHECK_CHANGE_CONTEXT.bind(options, "implementation")
        return self.delegate.execute(options)


class _SelfCheckFullstackExecutor:
    def __init__(self) -> None:
        self.delegate = FullstackWorktreeExecutor(
            change_context_verifier=_SELF_CHECK_CHANGE_CONTEXT.verifier,
        )

    def execute(self, options):
        _SELF_CHECK_CHANGE_CONTEXT.bind(options, "implementation")
        return self.delegate.execute(options)


class _SelfCheckPrecommitVerifier:
    def __init__(self) -> None:
        self.delegate = PrecommitVerifier(
            change_context_verifier=_SELF_CHECK_CHANGE_CONTEXT.verifier,
        )

    def execute(self, options):
        _SELF_CHECK_CHANGE_CONTEXT.bind(options, "review")
        return self.delegate.execute(options)


class _SelfCheckTaskManager(TaskManager):
    def rerun_precommit(self, options):
        previous = task_manager_module.PrecommitVerifier
        task_manager_module.PrecommitVerifier = _SelfCheckPrecommitVerifier
        try:
            return super().rerun_precommit(options)
        finally:
            task_manager_module.PrecommitVerifier = previous
`
const YUNXIAO_MIGRATION_DEPENDENCY_OLD =
  '                "skills/yunxiao-workitem-read/scripts/yunxiao_evidence.py",'
const YUNXIAO_MIGRATION_DEPENDENCY_CURRENT =
  '                "scripts/yunxiao_evidence.py",'
const HARNESS_DECISION_APPEND_CONTRACT_OLD = [
  '        if event_type == "worker_protocol_rejected":',
  '            validate_protocol_rejection_audit(payload)',
  '        try:',
  '            with self._connect() as connection:',
].join('\n')
const HARNESS_DECISION_APPEND_CONTRACT_CURRENT = [
  '        if event_type == "worker_protocol_rejected":',
  '            validate_protocol_rejection_audit(payload)',
  '        elif event_type == "harness_decision_issued":',
  '            _validate_harness_decision_event(payload)',
  '        try:',
  '            with self._connect() as connection:',
].join('\n')
const HARNESS_DECISION_ENCODER_CONTRACT_OLD = [
  '                    _encode_validated_audit_mapping(payload)',
  '                    if event_type == "worker_protocol_rejected"',
  '                    else _encode_safe_mapping(payload)',
].join('\n')
const HARNESS_DECISION_ENCODER_CONTRACT_CURRENT = [
  '                    _encode_validated_audit_mapping(payload)',
  '                    if event_type in {"harness_decision_issued", "worker_protocol_rejected"}',
  '                    else _encode_safe_mapping(payload)',
].join('\n')
const HARNESS_DECISION_DECODER_CONTRACT_OLD =
  '        if event_type in {"review_failed", "worker_protocol_rejected"}'
const HARNESS_DECISION_DECODER_CONTRACT_CURRENT =
  '        if event_type in {"harness_decision_issued", "review_failed", "worker_protocol_rejected"}'
const HARNESS_DECISION_VALIDATION_BRANCH_OLD = [
  '    elif event_type == "worker_protocol_rejected":',
  '        try:',
  '            validate_protocol_rejection_audit(payload)',
  '        except ValueError:',
  '            raise ValueError(_STORAGE_INVALID) from None',
].join('\n')
const HARNESS_DECISION_VALIDATION_BRANCH_CURRENT = [
  '    elif event_type == "worker_protocol_rejected":',
  '        try:',
  '            validate_protocol_rejection_audit(payload)',
  '        except ValueError:',
  '            raise ValueError(_STORAGE_INVALID) from None',
  '    elif event_type == "harness_decision_issued":',
  '        _validate_harness_decision_event(payload)',
].join('\n')
const HARNESS_DECISION_VALIDATOR_ANCHOR =
  'def _validate_review_failure(value: Mapping[str, object]) -> None:'
const HARNESS_DECISION_VALIDATOR = [
  'def _validate_harness_decision_event(value: Mapping[str, object]) -> None:',
  '    """Validate the fixed decision audit schema before bypassing text redaction."""',
  '',
  '    expected = {',
  '        "plan_version", "supersedes_plan_version", "decision_kind",',
  '        "failure_code", "decision_digest", "must_reinspect", "execute_only",',
  '    }',
  '    if not isinstance(value, Mapping) or set(value) != expected:',
  '        raise ValueError(_STORAGE_INVALID)',
  '    plan_version = value.get("plan_version")',
  '    supersedes = value.get("supersedes_plan_version")',
  '    if (',
  '        not isinstance(plan_version, int)',
  '        or isinstance(plan_version, bool)',
  '        or plan_version <= 0',
  '        or (',
  '            (plan_version == 1 and supersedes is not None)',
  '            or (plan_version > 1 and supersedes != plan_version - 1)',
  '        )',
  '        or value.get("decision_kind") != ("initial_plan" if plan_version == 1 else "replan")',
  '        or value.get("failure_code") not in {',
  '            "initial_execution", "workspace_preparation_failed", "worker_interrupted",',
  '            "worker_failed", "verification_failed", "review_changes_requested",',
  '            "recovery_replan",',
  '        }',
  '        or not isinstance(value.get("decision_digest"), str)',
  '        or _AUTHORIZATION_HASH.fullmatch(value["decision_digest"]) is None',
  '        or value.get("must_reinspect") is not True',
  '        or value.get("execute_only") is not True',
  '    ):',
  '        raise ValueError(_STORAGE_INVALID)',
  '',
  '',
].join('\n')
const HARNESS_DECISION_REPOSITORY_TEST_ANCHOR =
  '    def test_protocol_rejection_event_rejects_polluted_payload_without_echo(self) -> None:'
const HARNESS_DECISION_REPOSITORY_TEST_CURRENT = [
  '    def test_harness_decision_event_accepts_pii_shaped_sha256_digest(self) -> None:',
  '        run, attempt = self._create_run_and_attempt()',
  '        payload = {',
  '            "plan_version": 1,',
  '            "supersedes_plan_version": None,',
  '            "decision_kind": "initial_plan",',
  '            "failure_code": "initial_execution",',
  '            "decision_digest": "sha256:7cff524767863421371581b5319a473a1e2613e46b0af04b7eaf63068ffad89d",',
  '            "must_reinspect": True,',
  '            "execute_only": True,',
  '        }',
  '',
  '        event = self.repository.append_event(',
  '            run["id"], attempt["id"], "harness_decision_issued", payload,',
  '        )',
  '',
  '        self.assertEqual(payload, event["payload"])',
  '        self.assertEqual(payload, self.repository.snapshot(run["id"])["events"][-1]["payload"])',
  '        for mutation in (',
  '            {"decision_digest": "7cff524767863421371581b5319a473a1e2613e46b0af04b7eaf63068ffad89d"},',
  '            {"decision_kind": "replan"},',
  '            {"supersedes_plan_version": 1},',
  '            {"must_reinspect": False},',
  '            {"extra": "value"},',
  '        ):',
  '            with self.subTest(mutation=mutation), self.assertRaisesRegex(',
  '                ValueError, "local_agent_storage_invalid",',
  '            ):',
  '                self.repository.append_event(',
  '                    run["id"], attempt["id"], "harness_decision_issued",',
  '                    {**payload, **mutation},',
  '                )',
  '',
  HARNESS_DECISION_REPOSITORY_TEST_ANCHOR,
].join('\n')

export function isSecretAssignment(text) {
  const match = SECRET_ASSIGNMENT.exec(text)
  if (match === null) return false
  return !PLACEHOLDER_VALUE.test(match[0])
}

export function isVendorablePath(relativePath) {
  return !EXCLUDED_PATH_PATTERNS.some((pattern) => pattern.test(relativePath))
}

/**
 * Apply narrow desktop compatibility patches after copying the local Harness
 * source. Every patch matches an exact audited source contract and fails
 * closed when upstream changes it, so automatic sync cannot silently widen a
 * security-sensitive executable allowlist.
 */
export function applyHarnessCoreCompatibilityPatches(target) {
  const applied = []
  const workerPath = join(resolve(target), 'app', 'codex_cli_worker.py')
  if (existsSync(workerPath)) {
    const text = readFileSync(workerPath, 'utf8')
    if (text.includes(CODEX_WORKER_VERSION_CONTRACT_0150)) {
      applied.push(CODEX_WORKER_COMPATIBILITY_PATCH)
    } else {
      if (!text.includes(CODEX_WORKER_VERSION_CONTRACT_0149)) {
        throw new Error('Harness Core Codex Worker 版本契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        workerPath,
        text.replace(CODEX_WORKER_VERSION_CONTRACT_0149, CODEX_WORKER_VERSION_CONTRACT_0150),
      )
      applied.push(CODEX_WORKER_COMPATIBILITY_PATCH)
    }
  }

  const localAgentRepositoryPath = join(
    resolve(target),
    'app',
    'local_agent_repository.py',
  )
  if (existsSync(localAgentRepositoryPath)) {
    const repository = readFileSync(localAgentRepositoryPath, 'utf8')
    const currentContracts = [
      HARNESS_DECISION_APPEND_CONTRACT_CURRENT,
      HARNESS_DECISION_ENCODER_CONTRACT_CURRENT,
      HARNESS_DECISION_DECODER_CONTRACT_CURRENT,
      HARNESS_DECISION_VALIDATION_BRANCH_CURRENT,
      HARNESS_DECISION_VALIDATOR,
    ]
    if (!currentContracts.every((contract) => repository.includes(contract))) {
      const oldContracts = [
        HARNESS_DECISION_APPEND_CONTRACT_OLD,
        HARNESS_DECISION_ENCODER_CONTRACT_OLD,
        HARNESS_DECISION_DECODER_CONTRACT_OLD,
        HARNESS_DECISION_VALIDATION_BRANCH_OLD,
        HARNESS_DECISION_VALIDATOR_ANCHOR,
      ]
      if (!oldContracts.every((contract) => repository.includes(contract))) {
        throw new Error('Harness Core 决策摘要审计契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        localAgentRepositoryPath,
        repository
          .replace(
            HARNESS_DECISION_APPEND_CONTRACT_OLD,
            HARNESS_DECISION_APPEND_CONTRACT_CURRENT,
          )
          .replace(
            HARNESS_DECISION_ENCODER_CONTRACT_OLD,
            HARNESS_DECISION_ENCODER_CONTRACT_CURRENT,
          )
          .replace(
            HARNESS_DECISION_DECODER_CONTRACT_OLD,
            HARNESS_DECISION_DECODER_CONTRACT_CURRENT,
          )
          .replace(
            HARNESS_DECISION_VALIDATION_BRANCH_OLD,
            HARNESS_DECISION_VALIDATION_BRANCH_CURRENT,
          )
          .replace(
            HARNESS_DECISION_VALIDATOR_ANCHOR,
            HARNESS_DECISION_VALIDATOR + HARNESS_DECISION_VALIDATOR_ANCHOR,
          ),
      )
    }

    const localAgentRepositoryTestPath = join(
      resolve(target),
      'tests',
      'test_local_agent_repository.py',
    )
    if (existsSync(localAgentRepositoryTestPath)) {
      const repositoryTest = readFileSync(localAgentRepositoryTestPath, 'utf8')
      if (!repositoryTest.includes(
        'def test_harness_decision_event_accepts_pii_shaped_sha256_digest',
      )) {
        if (!repositoryTest.includes(HARNESS_DECISION_REPOSITORY_TEST_ANCHOR)) {
          throw new Error('Harness Core 决策摘要审计测试契约漂移，必须人工复核后才能同步')
        }
        writeFileSync(
          localAgentRepositoryTestPath,
          repositoryTest.replace(
            HARNESS_DECISION_REPOSITORY_TEST_ANCHOR,
            HARNESS_DECISION_REPOSITORY_TEST_CURRENT,
          ),
        )
      }
    }
    applied.push(VALIDATED_HARNESS_DECISION_AUDIT_PATCH)
  }

  const enterpriseGatePath = join(resolve(target), 'app', 'enterprise_gate.py')
  if (existsSync(enterpriseGatePath)) {
    const gate = readFileSync(enterpriseGatePath, 'utf8')
    const currentContracts = [
      ENTERPRISE_GATE_TIMEOUT_DECLARATION_CURRENT,
      ENTERPRISE_GATE_TIMEOUT_REPORT_CURRENT,
      ENTERPRISE_GATE_STAGE_TIMEOUT_FUNCTION,
      ENTERPRISE_GATE_SUBPROCESS_TIMEOUT_CURRENT,
    ]
    if (!currentContracts.every((contract) => gate.includes(contract))) {
      const oldContracts = [
        ENTERPRISE_GATE_TIMEOUT_DECLARATION_OLD,
        ENTERPRISE_GATE_TIMEOUT_REPORT_OLD,
        ENTERPRISE_GATE_STAGE_TIMEOUT_ANCHOR,
        ENTERPRISE_GATE_SUBPROCESS_TIMEOUT_OLD,
      ]
      if (!oldContracts.every((contract) => gate.includes(contract))) {
        throw new Error('Harness Core 企业门禁超时契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        enterpriseGatePath,
        gate
          .replace(
            ENTERPRISE_GATE_TIMEOUT_DECLARATION_OLD,
            ENTERPRISE_GATE_TIMEOUT_DECLARATION_CURRENT,
          )
          .replace(
            ENTERPRISE_GATE_TIMEOUT_REPORT_OLD,
            ENTERPRISE_GATE_TIMEOUT_REPORT_CURRENT,
          )
          .replace(
            ENTERPRISE_GATE_STAGE_TIMEOUT_ANCHOR,
            ENTERPRISE_GATE_STAGE_TIMEOUT_FUNCTION + ENTERPRISE_GATE_STAGE_TIMEOUT_ANCHOR,
          )
          .replace(
            ENTERPRISE_GATE_SUBPROCESS_TIMEOUT_OLD,
            ENTERPRISE_GATE_SUBPROCESS_TIMEOUT_CURRENT,
          ),
      )
    }

    const enterpriseGateTestPath = join(resolve(target), 'tests', 'test_enterprise_gate.py')
    if (existsSync(enterpriseGateTestPath)) {
      const gateTest = readFileSync(enterpriseGateTestPath, 'utf8')
      const currentTestContracts = [
        ENTERPRISE_GATE_TEST_IMPORT_CURRENT,
        ENTERPRISE_GATE_TEST_REPORT_CURRENT,
        ENTERPRISE_GATE_TEST_TIMEOUT_CURRENT,
      ]
      if (!currentTestContracts.every((contract) => gateTest.includes(contract))) {
        const oldTestContracts = [
          ENTERPRISE_GATE_TEST_IMPORT_OLD,
          ENTERPRISE_GATE_TEST_REPORT_OLD,
          ENTERPRISE_GATE_TEST_TIMEOUT_OLD,
        ]
        if (!oldTestContracts.every((contract) => gateTest.includes(contract))) {
          throw new Error('Harness Core 企业门禁超时测试契约漂移，必须人工复核后才能同步')
        }
        writeFileSync(
          enterpriseGateTestPath,
          gateTest
            .replace(ENTERPRISE_GATE_TEST_IMPORT_OLD, ENTERPRISE_GATE_TEST_IMPORT_CURRENT)
            .replace(ENTERPRISE_GATE_TEST_REPORT_OLD, ENTERPRISE_GATE_TEST_REPORT_CURRENT)
            .replace(ENTERPRISE_GATE_TEST_TIMEOUT_OLD, ENTERPRISE_GATE_TEST_TIMEOUT_CURRENT),
        )
      }
    }
    applied.push(BOUNDED_EXPANDED_UNIT_GATE_TIMEOUT_PATCH)
  }

  const managerDesignPath = join(
    resolve(target),
    'docs',
    'superpowers',
    'specs',
    '2026-08-09-manager-provider-configuration-design.md',
  )
  if (existsSync(managerDesignPath)) {
    const design = readFileSync(managerDesignPath, 'utf8')
    if (design.includes(MANAGER_DATABASE_READONLY_CONTRACT_CURRENT)) {
      applied.push(MANAGER_DATABASE_READONLY_WORDING_PATCH)
    } else {
      if (!design.includes(MANAGER_DATABASE_READONLY_CONTRACT_OLD)) {
        throw new Error('Harness Core 数据库永久只读文档契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        managerDesignPath,
        design.replace(
          MANAGER_DATABASE_READONLY_CONTRACT_OLD,
          MANAGER_DATABASE_READONLY_CONTRACT_CURRENT,
        ),
      )
      applied.push(MANAGER_DATABASE_READONLY_WORDING_PATCH)
    }
  }

  const harnessPath = join(resolve(target), 'app', 'harness.py')
  if (existsSync(harnessPath)) {
    const harness = readFileSync(harnessPath, 'utf8')
    let patchedHarness = harness
    const isFullHarness = harness.includes('class RequirementWorkflowRunner')
    if (
      isFullHarness
      || harness.includes(CORE_CLOSURE_GOVERNANCE_CONTRACT_HARD_STOP)
      || harness.includes(CORE_CLOSURE_GOVERNANCE_CONTRACT_UNGUARDED)
      || harness.includes(CORE_CLOSURE_GOVERNANCE_CONTRACT_PERMISSIVE)
      || harness.includes(CORE_CLOSURE_GOVERNANCE_CONTRACT_AUTO_LOCAL_ONLY)
      || harness.includes(CORE_CLOSURE_GOVERNANCE_CONTRACT_CURRENT)
    ) {
      if (harness.includes(CORE_CLOSURE_GOVERNANCE_CONTRACT_CURRENT)) {
        applied.push(GOVERNANCE_AUTO_LOCAL_ARTIFACT_BOUNDARY_PATCH)
      } else {
        const legacyContract = harness.includes(CORE_CLOSURE_GOVERNANCE_CONTRACT_AUTO_LOCAL_ONLY)
          ? CORE_CLOSURE_GOVERNANCE_CONTRACT_AUTO_LOCAL_ONLY
          : harness.includes(CORE_CLOSURE_GOVERNANCE_CONTRACT_PERMISSIVE)
          ? CORE_CLOSURE_GOVERNANCE_CONTRACT_PERMISSIVE
          : harness.includes(CORE_CLOSURE_GOVERNANCE_CONTRACT_UNGUARDED)
          ? CORE_CLOSURE_GOVERNANCE_CONTRACT_UNGUARDED
          : harness.includes(CORE_CLOSURE_GOVERNANCE_CONTRACT_HARD_STOP)
          ? CORE_CLOSURE_GOVERNANCE_CONTRACT_HARD_STOP
          : ''
        if (!legacyContract || !harness.includes(legacyContract)) {
          throw new Error('Harness Core 治理阻断闭环工件契约漂移，必须人工复核后才能同步')
        }
        patchedHarness = patchedHarness.replace(
          legacyContract,
          CORE_CLOSURE_GOVERNANCE_CONTRACT_CURRENT,
        )
        applied.push(GOVERNANCE_AUTO_LOCAL_ARTIFACT_BOUNDARY_PATCH)
      }
    }
    if (
      isFullHarness
      || harness.includes(AUTO_LOCAL_SCAN_CONTRACT_OLD)
      || harness.includes(AUTO_LOCAL_SCAN_CONTRACT_UNGATED)
      || harness.includes(AUTO_LOCAL_SCAN_CONTRACT_CURRENT)
    ) {
      if (harness.includes(AUTO_LOCAL_SCAN_CONTRACT_CURRENT)) {
        applied.push(AUTO_LOCAL_BOUNDED_SCAN_PATCH)
      } else {
        const legacyContract = harness.includes(AUTO_LOCAL_SCAN_CONTRACT_UNGATED)
          ? AUTO_LOCAL_SCAN_CONTRACT_UNGATED
          : AUTO_LOCAL_SCAN_CONTRACT_OLD
        if (!harness.includes(legacyContract)) {
          throw new Error('Harness Core auto-local 局部扫描契约漂移，必须人工复核后才能同步')
        }
        patchedHarness = patchedHarness.replace(
          legacyContract,
          AUTO_LOCAL_SCAN_CONTRACT_CURRENT,
        )
        applied.push(AUTO_LOCAL_BOUNDED_SCAN_PATCH)
      }
    }
    if (
      isFullHarness
      || harness.includes(AUTHORITATIVE_ACCEPTANCE_CONTRACT_OLD)
      || harness.includes(AUTHORITATIVE_ACCEPTANCE_CONTRACT_CURRENT)
    ) {
      if (harness.includes(AUTHORITATIVE_ACCEPTANCE_CONTRACT_CURRENT)) {
        applied.push(AUTHORITATIVE_ACCEPTANCE_COMMAND_PATCH)
      } else {
        if (!harness.includes(AUTHORITATIVE_ACCEPTANCE_CONTRACT_OLD)) {
          throw new Error('Harness Core enforce 验收命令契约漂移，必须人工复核后才能同步')
        }
        patchedHarness = patchedHarness.replace(
          AUTHORITATIVE_ACCEPTANCE_CONTRACT_OLD,
          AUTHORITATIVE_ACCEPTANCE_CONTRACT_CURRENT,
        )
        applied.push(AUTHORITATIVE_ACCEPTANCE_COMMAND_PATCH)
      }
    }
    if (
      isFullHarness
      || harness.includes(UNDERSTANDING_GOVERNANCE_RECONCILIATION_CURRENT)
      || (
        harness.includes(UNDERSTANDING_STAGE_RECORD_OLD)
        && harness.includes('        governance_ready = (')
      )
    ) {
      if (harness.includes(UNDERSTANDING_GOVERNANCE_RECONCILIATION_CURRENT)) {
        applied.push(ENFORCE_UNDERSTANDING_RECONCILIATION_PATCH)
      } else {
        if (
          !harness.includes(UNDERSTANDING_STAGE_RECORD_OLD)
          || !harness.includes('        governance_ready = (')
        ) {
          throw new Error('Harness Core 理解与 enforce 治理对齐契约漂移，必须人工复核后才能同步')
        }
        patchedHarness = patchedHarness
          .replace(`${UNDERSTANDING_STAGE_RECORD_OLD}\n`, '')
          .replace(
            '        governance_ready = (',
            UNDERSTANDING_GOVERNANCE_RECONCILIATION_CURRENT,
          )
        applied.push(ENFORCE_UNDERSTANDING_RECONCILIATION_PATCH)
      }
    }
    if (patchedHarness !== harness) writeFileSync(harnessPath, patchedHarness)
  }

  const capabilityCheckPath = join(resolve(target), 'tools', 'capability_check.py')
  if (existsSync(capabilityCheckPath)) {
    const checker = readFileSync(capabilityCheckPath, 'utf8')
    if (checker.includes(RUNTIME_PLUGIN_ROOTS_CONTRACT_CURRENT)) {
      // Already patched.
    } else {
      if (!checker.includes(RUNTIME_PLUGIN_ROOTS_CONTRACT_OLD)) {
        throw new Error('Harness Core 插件相对路径解析契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        capabilityCheckPath,
        checker.replace(
          RUNTIME_PLUGIN_ROOTS_CONTRACT_OLD,
          RUNTIME_PLUGIN_ROOTS_CONTRACT_CURRENT,
        ),
      )
    }

    const capabilityTestPath = join(resolve(target), 'tests', 'test_capability_check_cli.py')
    if (existsSync(capabilityTestPath)) {
      const capabilityTest = readFileSync(capabilityTestPath, 'utf8')
      if (!capabilityTest.includes(RELATIVE_PLUGIN_ROOT_TEST_ANCHOR_CURRENT)) {
        if (!capabilityTest.includes(RELATIVE_PLUGIN_ROOT_TEST_ANCHOR_OLD)) {
          throw new Error('Harness Core 插件相对路径测试契约漂移，必须人工复核后才能同步')
        }
        writeFileSync(
          capabilityTestPath,
          capabilityTest.replace(
            RELATIVE_PLUGIN_ROOT_TEST_ANCHOR_OLD,
            RELATIVE_PLUGIN_ROOT_TEST_ANCHOR_CURRENT,
          ),
        )
      }
    }

    const inventoryTestPath = join(resolve(target), 'tests', 'test_plugin_inventory.py')
    if (existsSync(inventoryTestPath)) {
      const inventoryTest = readFileSync(inventoryTestPath, 'utf8')
      if (!inventoryTest.includes(PLUGIN_INVENTORY_ROOTS_CONTRACT_CURRENT)) {
        if (!inventoryTest.includes(PLUGIN_INVENTORY_ROOTS_CONTRACT_OLD)) {
          throw new Error('Harness Core 可迁移插件清单测试契约漂移，必须人工复核后才能同步')
        }
        writeFileSync(
          inventoryTestPath,
          inventoryTest.replace(
            PLUGIN_INVENTORY_ROOTS_CONTRACT_OLD,
            PLUGIN_INVENTORY_ROOTS_CONTRACT_CURRENT,
          ),
        )
      }
    }
    applied.push(RELOCATABLE_PLUGIN_ROOT_PATCH)
  }

  const deliveryAdapterPath = join(resolve(target), 'app', 'delivery_closure.py')
  if (existsSync(deliveryAdapterPath)) {
    const adapter = readFileSync(deliveryAdapterPath, 'utf8')
    if (
      adapter.includes(ENGINEERING_ADAPTER_ROOT_CURRENT)
      && adapter.includes(ENGINEERING_ADAPTER_CANDIDATE_CURRENT)
    ) {
      applied.push(RELOCATABLE_ENGINEERING_ADAPTER_PATCH)
    } else {
      if (
        !adapter.includes(ENGINEERING_ADAPTER_ROOT_OLD)
        && !adapter.includes(ENGINEERING_ADAPTER_ROOT_CURRENT)
      ) {
        throw new Error('Harness Core his-engineering 适配器路径契约漂移，必须人工复核后才能同步')
      }
      if (
        !adapter.includes(ENGINEERING_ADAPTER_CANDIDATE_OLD)
        && !adapter.includes(ENGINEERING_ADAPTER_CANDIDATE_UNGUARDED)
      ) {
        throw new Error('Harness Core his-engineering 适配器路径契约漂移，必须人工复核后才能同步')
      }
      const rootPatched = adapter.includes(ENGINEERING_ADAPTER_ROOT_CURRENT)
        ? adapter
        : adapter.replace(ENGINEERING_ADAPTER_ROOT_OLD, ENGINEERING_ADAPTER_ROOT_CURRENT)
      const candidateContract = rootPatched.includes(ENGINEERING_ADAPTER_CANDIDATE_UNGUARDED)
        ? ENGINEERING_ADAPTER_CANDIDATE_UNGUARDED
        : ENGINEERING_ADAPTER_CANDIDATE_OLD
      writeFileSync(
        deliveryAdapterPath,
        rootPatched.replace(candidateContract, ENGINEERING_ADAPTER_CANDIDATE_CURRENT),
      )
      applied.push(RELOCATABLE_ENGINEERING_ADAPTER_PATCH)
    }
    const versionPatchedAdapter = readFileSync(deliveryAdapterPath, 'utf8')
    const currentVersionContracts = [
      ENGINEERING_ADAPTER_MANIFEST_VERSION_CURRENT,
      ENGINEERING_ADAPTER_DESCRIPTOR_VERSION_CURRENT,
      ENGINEERING_ADAPTER_CAPABILITY_VERSION_CURRENT,
    ]
    const hasCurrentVersionContract =
      currentVersionContracts.every((contract) => versionPatchedAdapter.includes(contract))
      || ENGINEERING_ADAPTER_INVENTORY_PINNED_VERSION_CURRENT.every(
        (contract) => versionPatchedAdapter.includes(contract),
      )
    if (hasCurrentVersionContract) {
      applied.push(CURRENT_ENGINEERING_ADAPTER_VERSION_PATCH)
    } else {
      const oldVersionContracts = [
        ENGINEERING_ADAPTER_MANIFEST_VERSION_OLD,
        ENGINEERING_ADAPTER_DESCRIPTOR_VERSION_OLD,
        ENGINEERING_ADAPTER_CAPABILITY_VERSION_OLD,
      ]
      if (!oldVersionContracts.every((contract) => versionPatchedAdapter.includes(contract))) {
        throw new Error('Harness Core his-engineering 适配器版本契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        deliveryAdapterPath,
        versionPatchedAdapter
          .replace(
            ENGINEERING_ADAPTER_MANIFEST_VERSION_OLD,
            ENGINEERING_ADAPTER_MANIFEST_VERSION_CURRENT,
          )
          .replace(
            ENGINEERING_ADAPTER_DESCRIPTOR_VERSION_OLD,
            ENGINEERING_ADAPTER_DESCRIPTOR_VERSION_CURRENT,
          )
          .replace(
            ENGINEERING_ADAPTER_CAPABILITY_VERSION_OLD,
            ENGINEERING_ADAPTER_CAPABILITY_VERSION_CURRENT,
          ),
      )
      applied.push(CURRENT_ENGINEERING_ADAPTER_VERSION_PATCH)
    }
  }

  const pgEvidenceAdapterPath = join(resolve(target), 'app', 'pg_evidence.py')
  if (existsSync(pgEvidenceAdapterPath)) {
    const adapter = readFileSync(pgEvidenceAdapterPath, 'utf8')
    const retiredFailClosed = PG_EVIDENCE_FAIL_CLOSED_TOMBSTONE_CURRENT.every(
      (contract) => adapter.includes(contract),
    )
    const currentContracts = [
      PG_EVIDENCE_DATACLASS_IMPORT_CURRENT,
      PG_EVIDENCE_ENTRYPOINT_DISCOVERY_CURRENT,
      PG_EVIDENCE_DESCRIPTOR_CONTRACT_CURRENT,
      PG_EVIDENCE_COMPATIBILITY_REGISTRY_CURRENT,
    ]
    if (retiredFailClosed) {
      // The upstream Core has removed the direct database compatibility path.
      // Preserve the stronger MCP-only tombstone instead of reintroducing it.
    } else if (currentContracts.every((contract) => adapter.includes(contract))) {
      applied.push(CURRENT_POSTGRESQL_MCP_LEGACY_BRIDGE_PATCH)
    } else {
      const oldContracts = [
        PG_EVIDENCE_DATACLASS_IMPORT_OLD,
        PG_EVIDENCE_ENTRYPOINT_DISCOVERY_OLD,
        PG_EVIDENCE_DESCRIPTOR_CONTRACT_OLD,
        PG_EVIDENCE_COMPATIBILITY_REGISTRY_OLD,
      ]
      if (!oldContracts.every((contract) => adapter.includes(contract))) {
        throw new Error('Harness Core PostgreSQL MCP 兼容桥契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        pgEvidenceAdapterPath,
        adapter
          .replace(PG_EVIDENCE_DATACLASS_IMPORT_OLD, PG_EVIDENCE_DATACLASS_IMPORT_CURRENT)
          .replace(PG_EVIDENCE_ENTRYPOINT_DISCOVERY_OLD, PG_EVIDENCE_ENTRYPOINT_DISCOVERY_CURRENT)
          .replace(PG_EVIDENCE_DESCRIPTOR_CONTRACT_OLD, PG_EVIDENCE_DESCRIPTOR_CONTRACT_CURRENT)
          .replace(PG_EVIDENCE_COMPATIBILITY_REGISTRY_OLD, PG_EVIDENCE_COMPATIBILITY_REGISTRY_CURRENT),
      )
      applied.push(CURRENT_POSTGRESQL_MCP_LEGACY_BRIDGE_PATCH)
    }
  }

  const pgEvidenceDirectTestPath = join(resolve(target), 'tests', 'test_pg_evidence.py')
  if (existsSync(pgEvidenceAdapterPath) && existsSync(pgEvidenceDirectTestPath)) {
    const adapter = readFileSync(pgEvidenceAdapterPath, 'utf8')
    const directTest = readFileSync(pgEvidenceDirectTestPath, 'utf8')
    const retiredFailClosed = PG_EVIDENCE_FAIL_CLOSED_TOMBSTONE_CURRENT.every(
      (contract) => adapter.includes(contract),
    )
    const staleDirectTest = PG_EVIDENCE_STALE_DIRECT_TEST_CONTRACT.every(
      (contract) => directTest.includes(contract),
    )
    if (retiredFailClosed && staleDirectTest) {
      writeFileSync(pgEvidenceDirectTestPath, PG_EVIDENCE_RETIRED_TEST_CURRENT)
      applied.push(RETIRE_STALE_DIRECT_PG_EVIDENCE_TESTS_PATCH)
    } else if (retiredFailClosed && directTest.includes(PG_EVIDENCE_RETIRED_TEST_MARKER)) {
      applied.push(RETIRE_STALE_DIRECT_PG_EVIDENCE_TESTS_PATCH)
    }
  }

  const externalIoPolicyPath = join(resolve(target), 'app', 'external_io_policy.py')
  if (existsSync(externalIoPolicyPath)) {
    const policy = readFileSync(externalIoPolicyPath, 'utf8')
    if (
      policy.includes(EXTERNAL_IO_CONFIG_DIRECTORY_CURRENT)
      && policy.includes(EXTERNAL_IO_PLUGIN_ROOT_CURRENT)
    ) {
      applied.push(RELOCATABLE_EXTERNAL_IO_PLUGIN_ROOT_PATCH)
    } else {
      if (
        !policy.includes(EXTERNAL_IO_CONFIG_DIRECTORY_OLD)
        || !policy.includes(EXTERNAL_IO_PLUGIN_ROOT_OLD)
      ) {
        throw new Error('Harness Core 外部 I/O 插件根目录契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        externalIoPolicyPath,
        policy
          .replace(EXTERNAL_IO_CONFIG_DIRECTORY_OLD, EXTERNAL_IO_CONFIG_DIRECTORY_CURRENT)
          .replace(EXTERNAL_IO_PLUGIN_ROOT_OLD, EXTERNAL_IO_PLUGIN_ROOT_CURRENT),
      )
      applied.push(RELOCATABLE_EXTERNAL_IO_PLUGIN_ROOT_PATCH)
    }
  }

  const externalIoInventoryPath = join(resolve(target), 'app', 'external_io_inventory.py')
  if (existsSync(externalIoInventoryPath)) {
    const inventory = readFileSync(externalIoInventoryPath, 'utf8')
    if (inventory.includes(EXTERNAL_IO_IGNORED_DIRECTORIES_CURRENT)) {
      applied.push(IGNORE_BUNDLED_RUNTIME_EXTERNAL_IO_PATCH)
    } else {
      if (!inventory.includes(EXTERNAL_IO_IGNORED_DIRECTORIES_OLD)) {
        throw new Error('Harness Core 打包运行时外部 I/O 扫描契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        externalIoInventoryPath,
        inventory.replace(
          EXTERNAL_IO_IGNORED_DIRECTORIES_OLD,
          EXTERNAL_IO_IGNORED_DIRECTORIES_CURRENT,
        ),
      )
      applied.push(IGNORE_BUNDLED_RUNTIME_EXTERNAL_IO_PATCH)
    }
  }

  const verifyEntrypointPath = join(resolve(target), 'scripts', 'verify.sh')
  const verifyEntrypointTestPath = join(resolve(target), 'tests', 'test_verify_entrypoint.py')
  if (existsSync(verifyEntrypointPath) || existsSync(verifyEntrypointTestPath)) {
    if (existsSync(verifyEntrypointPath)) {
    const verifyEntrypoint = readFileSync(verifyEntrypointPath, 'utf8')
    if (!verifyEntrypoint.includes(VERIFY_PYTHON_CONTRACT_CURRENT)) {
      if (!verifyEntrypoint.includes(VERIFY_PYTHON_CONTRACT_OLD)) {
          // Minimal vendor-layout fixtures may intentionally use a stub verify script.
          if (!verifyEntrypoint.includes('exit 0')) {
            throw new Error('Harness Core Python 验证入口契约漂移，必须人工复核后才能同步')
          }
        } else {
          writeFileSync(
            verifyEntrypointPath,
            verifyEntrypoint.replace(VERIFY_PYTHON_CONTRACT_OLD, VERIFY_PYTHON_CONTRACT_CURRENT),
          )
        }
      }
    }
    if (existsSync(verifyEntrypointTestPath)) {
    const verifyEntrypointTest = readFileSync(verifyEntrypointTestPath, 'utf8')
    if (!verifyEntrypointTest.includes(VERIFY_ENTRYPOINT_TEST_CONTRACT_CURRENT)) {
      if (!verifyEntrypointTest.includes(VERIFY_ENTRYPOINT_TEST_CONTRACT_OLD)) {
        throw new Error('Harness Core Python 验证入口测试契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        verifyEntrypointTestPath,
        verifyEntrypointTest.replace(
          VERIFY_ENTRYPOINT_TEST_CONTRACT_OLD,
          VERIFY_ENTRYPOINT_TEST_CONTRACT_CURRENT,
        ),
      )
    }
    }
    applied.push(PACKAGED_PYTHON_VERIFY_ENTRYPOINT_PATCH)
  }

  if (existsSync(verifyEntrypointPath)) {
    const verifyEntrypoint = readFileSync(verifyEntrypointPath, 'utf8')
    if (verifyEntrypoint.includes(VERIFY_OFFLINE_OUTPUT_DIRECTORY_CURRENT)) {
      applied.push(UNIQUE_OFFLINE_GATE_OUTPUT_DIRECTORY_PATCH)
    } else if (verifyEntrypoint.includes(VERIFY_OFFLINE_OUTPUT_DIRECTORY_OLD)) {
      writeFileSync(
        verifyEntrypointPath,
        verifyEntrypoint.replace(
          VERIFY_OFFLINE_OUTPUT_DIRECTORY_OLD,
          VERIFY_OFFLINE_OUTPUT_DIRECTORY_CURRENT,
        ),
      )
      if (existsSync(verifyEntrypointTestPath)) {
        const verifyEntrypointTest = readFileSync(verifyEntrypointTestPath, 'utf8')
        if (!verifyEntrypointTest.includes(VERIFY_OFFLINE_OUTPUT_TEST_CURRENT)) {
          if (!verifyEntrypointTest.includes(VERIFY_ENTRYPOINT_TEST_MODULE_ANCHOR)) {
            throw new Error('Harness Core 离线门禁输出目录测试契约漂移，必须人工复核后才能同步')
          }
          writeFileSync(
            verifyEntrypointTestPath,
            verifyEntrypointTest.replace(
              VERIFY_ENTRYPOINT_TEST_MODULE_ANCHOR,
              `\n\n${VERIFY_OFFLINE_OUTPUT_TEST_CURRENT}${VERIFY_ENTRYPOINT_TEST_MODULE_ANCHOR}`,
            ),
          )
        }
      }
      applied.push(UNIQUE_OFFLINE_GATE_OUTPUT_DIRECTORY_PATCH)
    }
  }

  const pgEvidenceCompatibilityTestPath = join(
    resolve(target),
    'tests',
    'test_pg_evidence_compatibility.py',
  )
  if (existsSync(pgEvidenceCompatibilityTestPath)) {
    const compatibilityTest = readFileSync(pgEvidenceCompatibilityTestPath, 'utf8')
    const nativeMcpOnlyTest = PG_EVIDENCE_TEST_MCP_ONLY_CURRENT.every(
      (contract) => compatibilityTest.includes(contract),
    )
    if (nativeMcpOnlyTest) {
      // No development plugin root remains in the upstream MCP-only retirement tests.
    } else if (
      compatibilityTest.includes(PG_EVIDENCE_TEST_IMPORT_CURRENT)
      && compatibilityTest.includes(PG_EVIDENCE_TEST_ROOT_CURRENT)
    ) {
      applied.push(RELOCATABLE_PG_EVIDENCE_TEST_ROOT_PATCH)
    } else {
      if (
        !compatibilityTest.includes(PG_EVIDENCE_TEST_IMPORT_OLD)
        || !compatibilityTest.includes(PG_EVIDENCE_TEST_ROOT_OLD)
      ) {
        throw new Error('Harness Core PostgreSQL 兼容测试根目录契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        pgEvidenceCompatibilityTestPath,
        compatibilityTest
          .replace(PG_EVIDENCE_TEST_IMPORT_OLD, PG_EVIDENCE_TEST_IMPORT_CURRENT)
          .replace(PG_EVIDENCE_TEST_ROOT_OLD, PG_EVIDENCE_TEST_ROOT_CURRENT),
      )
      applied.push(RELOCATABLE_PG_EVIDENCE_TEST_ROOT_PATCH)
    }
  }

  const pluginDocumentationTestPath = join(
    resolve(target),
    'tests',
    'test_plugin_documentation.py',
  )
  if (existsSync(pluginDocumentationTestPath)) {
    const documentationTest = readFileSync(pluginDocumentationTestPath, 'utf8')
    if (documentationTest.includes(COMPATIBILITY_SKILL_DOC_ROOT_OLD)) {
      writeFileSync(
        pluginDocumentationTestPath,
        documentationTest.replaceAll(
          COMPATIBILITY_SKILL_DOC_ROOT_OLD,
          COMPATIBILITY_SKILL_DOC_ROOT_CURRENT,
        ),
      )
      applied.push(RELOCATABLE_COMPATIBILITY_SKILL_DOC_ROOT_PATCH)
    } else if (documentationTest.includes(COMPATIBILITY_SKILL_DOC_ROOT_CURRENT)) {
      applied.push(RELOCATABLE_COMPATIBILITY_SKILL_DOC_ROOT_PATCH)
    } else {
      throw new Error('Harness Core 兼容技能文档测试根目录契约漂移，必须人工复核后才能同步')
    }
  }

  const roleRegistryPath = join(
    resolve(target),
    'app',
    'role_capability_skill_registry.py',
  )
  if (existsSync(roleRegistryPath)) {
    const registry = readFileSync(roleRegistryPath, 'utf8')
    if (registry.includes(ROLE_REGISTRY_PLUGIN_ROOTS_CURRENT)) {
      applied.push(RELOCATABLE_ROLE_REGISTRY_PLUGIN_ROOT_PATCH)
    } else {
      if (!registry.includes(ROLE_REGISTRY_PLUGIN_ROOTS_OLD)) {
        throw new Error('Harness Core 角色技能注册表插件路径契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        roleRegistryPath,
        registry.replace(
          ROLE_REGISTRY_PLUGIN_ROOTS_OLD,
          ROLE_REGISTRY_PLUGIN_ROOTS_CURRENT,
        ),
      )
      applied.push(RELOCATABLE_ROLE_REGISTRY_PLUGIN_ROOT_PATCH)
    }
  }

  const visualEvidencePath = join(resolve(target), 'app', 'visual_evidence.py')
  if (existsSync(visualEvidencePath)) {
    const visualEvidence = readFileSync(visualEvidencePath, 'utf8')
    if (!visualEvidence.includes('class CodexCliVisualEvidenceAnalyzer:')) {
      if (
        !visualEvidence.includes(VISUAL_ANALYZER_ANCHOR)
        || !visualEvidence.includes('from app.codex_cli_worker import CODEX_EXECUTABLE, CodexCliWorker, CodexWorkerRequest')
      ) {
        throw new Error('Harness Core 显式视觉证据适配器契约漂移，必须人工复核后才能同步')
      }
      writeFileSync(
        visualEvidencePath,
        visualEvidence.replace(
          VISUAL_ANALYZER_ANCHOR,
          `${VISUAL_ANALYZER_CURRENT}${VISUAL_ANALYZER_ANCHOR}`,
        ),
      )
    }
    const visualSchemaPath = join(
      resolve(target),
      'config',
      'schemas',
      'visual_evidence.v1.json',
    )
    if (existsSync(visualSchemaPath)) {
      if (readFileSync(visualSchemaPath, 'utf8') !== VISUAL_EVIDENCE_SCHEMA) {
        throw new Error('Harness Core 视觉证据 Schema 漂移，必须人工复核后才能同步')
      }
    } else {
      mkdirSync(dirname(visualSchemaPath), { recursive: true })
      writeFileSync(visualSchemaPath, VISUAL_EVIDENCE_SCHEMA)
    }
    applied.push(EXPLICIT_CODEX_VISUAL_EVIDENCE_PATCH)
  }

  const serverCoreStatusTestPath = join(
    resolve(target),
    'tests',
    'test_server_core_status_api.py',
  )
  if (existsSync(serverCoreStatusTestPath)) {
    const serverTest = readFileSync(serverCoreStatusTestPath, 'utf8')
    let patchedServerTest = serverTest
    for (const staleTest of [
      DEFAULT_MANAGER_STAGE_TEST_TWELVE,
      DEFAULT_MANAGER_STAGE_TEST_THIRTEEN,
    ]) {
      if (patchedServerTest.includes(staleTest)) {
        patchedServerTest = patchedServerTest.replace(
          staleTest,
          DEFAULT_MANAGER_STAGE_TEST_CURRENT,
        )
      }
    }
    for (const staleAssertion of [
      DEFAULT_MANAGER_STAGE_ASSERTION_TWELVE,
      DEFAULT_MANAGER_STAGE_ASSERTION_THIRTEEN,
    ]) {
      if (patchedServerTest.includes(staleAssertion)) {
        patchedServerTest = patchedServerTest.replace(
          staleAssertion,
          DEFAULT_MANAGER_STAGE_ASSERTION_CURRENT,
        )
      }
    }
    if (
      !patchedServerTest.includes(DEFAULT_MANAGER_STAGE_TEST_CURRENT)
      || !patchedServerTest.includes(DEFAULT_MANAGER_STAGE_ASSERTION_CURRENT)
    ) {
      throw new Error('Harness Core 十五阶段 Manager 测试契约漂移，必须人工复核后才能同步')
    }
    if (patchedServerTest !== serverTest) {
      writeFileSync(serverCoreStatusTestPath, patchedServerTest)
    }
    applied.push(FIFTEEN_STAGE_GOVERNANCE_TEST_PATCH)

    const alignedServerTest = readFileSync(serverCoreStatusTestPath, 'utf8')
    if (alignedServerTest.includes(PROVIDER_STATUS_AVAILABLE_CAPABILITIES_OLD)) {
      writeFileSync(
        serverCoreStatusTestPath,
        alignedServerTest.replace(
          PROVIDER_STATUS_AVAILABLE_CAPABILITIES_OLD,
          PROVIDER_STATUS_AVAILABLE_CAPABILITIES_CURRENT,
        ),
      )
      applied.push(NATIVE_READONLY_MCP_PROVIDER_STATUS_TEST_PATCH)
    } else if (alignedServerTest.includes(PROVIDER_STATUS_AVAILABLE_CAPABILITIES_CURRENT)) {
      applied.push(NATIVE_READONLY_MCP_PROVIDER_STATUS_TEST_PATCH)
    }
  }

  const scopeConfirmationTestPath = join(
    resolve(target),
    'tests',
    'test_scope_confirmation_integration.py',
  )
  if (existsSync(scopeConfirmationTestPath)) {
    const scopeTest = readFileSync(scopeConfirmationTestPath, 'utf8')
    if (scopeTest.includes(LEGACY_SCOPE_GATE_EXPECTATIONS_OLD)) {
      writeFileSync(
        scopeConfirmationTestPath,
        scopeTest.replace(
          LEGACY_SCOPE_GATE_EXPECTATIONS_OLD,
          LEGACY_SCOPE_GATE_EXPECTATIONS_CURRENT,
        ),
      )
    } else if (scopeTest.includes(LEGACY_SCOPE_GATE_EXPECTATIONS_BLOCKED_REPEAT)) {
      writeFileSync(
        scopeConfirmationTestPath,
        scopeTest.replace(
          LEGACY_SCOPE_GATE_EXPECTATIONS_BLOCKED_REPEAT,
          LEGACY_SCOPE_GATE_EXPECTATIONS_CURRENT,
        ),
      )
    } else if (scopeTest.includes(LEGACY_SCOPE_GATE_EXPECTATIONS_PARTIAL)) {
      writeFileSync(
        scopeConfirmationTestPath,
        scopeTest.replace(
          LEGACY_SCOPE_GATE_EXPECTATIONS_PARTIAL,
          LEGACY_SCOPE_GATE_EXPECTATIONS_CURRENT,
        ),
      )
    } else if (scopeTest.includes(LEGACY_SCOPE_GATE_EXPECTATIONS_INVERTED)) {
      writeFileSync(
        scopeConfirmationTestPath,
        scopeTest.replace(
          LEGACY_SCOPE_GATE_EXPECTATIONS_INVERTED,
          LEGACY_SCOPE_GATE_EXPECTATIONS_CURRENT,
        ),
      )
    } else if (!scopeTest.includes(LEGACY_SCOPE_GATE_EXPECTATIONS_CURRENT)) {
      throw new Error('Harness Core 理解门禁范围确认测试契约漂移，必须人工复核后才能同步')
    }
    applied.push(UNDERSTANDING_GATE_SCOPE_TEST_PATCH)
  }

  const coreClosureTestPath = join(resolve(target), 'tests', 'test_core_closure.py')
  if (existsSync(coreClosureTestPath)) {
    const coreClosureTest = readFileSync(coreClosureTestPath, 'utf8')
    if (coreClosureTest.includes(SORTING_CLOSURE_STALE_ASSERTION)) {
      writeFileSync(
        coreClosureTestPath,
        coreClosureTest.replace(
          SORTING_CLOSURE_STALE_ASSERTION,
          SORTING_CLOSURE_CURRENT_ASSERTIONS,
        ),
      )
    } else if (!coreClosureTest.includes(SORTING_CLOSURE_CURRENT_ASSERTIONS)) {
      throw new Error('Harness Core 严格需求校准测试契约漂移，必须人工复核后才能同步')
    }
    applied.push(STRICT_REQUIREMENT_CALIBRATION_TEST_PATCH)
  }

  const selfCheckPath = join(resolve(target), 'tools', 'self_check.py')
  if (existsSync(selfCheckPath)) {
    const selfCheck = readFileSync(selfCheckPath, 'utf8')
    if (selfCheck.includes('class _SelfCheckChangeContext:')) {
      if (
        selfCheck.includes('executor = WorktreeCodeExecutor(MockLLMClient())')
        || selfCheck.includes('executor = FullstackWorktreeExecutor()')
        || selfCheck.includes('= PrecommitVerifier().execute(')
        || selfCheck.includes('manager = TaskManager()')
      ) {
        throw new Error('Harness Core self-check ChangeContext 夹具未覆盖全部执行器')
      }
    } else {
      if (
        !selfCheck.includes(SELF_CHECK_CONTEXT_IMPORT_ANCHOR)
        || !selfCheck.includes(SELF_CHECK_TASK_MANAGER_IMPORT_ANCHOR)
        || !selfCheck.includes(SELF_CHECK_HELPER_ANCHOR)
        || !selfCheck.includes('executor = WorktreeCodeExecutor(MockLLMClient())')
        || !selfCheck.includes('executor = FullstackWorktreeExecutor()')
        || !selfCheck.includes('= PrecommitVerifier().execute(')
        || !selfCheck.includes('manager = TaskManager()')
      ) {
        throw new Error('Harness Core self-check ChangeContext 夹具契约漂移，必须人工复核后才能同步')
      }
      const patchedSelfCheck = selfCheck
        .replace(SELF_CHECK_CONTEXT_IMPORT_ANCHOR, SELF_CHECK_CONTEXT_IMPORTS)
        .replace(
          SELF_CHECK_TASK_MANAGER_IMPORT_ANCHOR,
          SELF_CHECK_TASK_MANAGER_IMPORT_CURRENT,
        )
        .replace(
          SELF_CHECK_HELPER_ANCHOR,
          `${SELF_CHECK_HELPER}${SELF_CHECK_HELPER_ANCHOR}`,
        )
        .replaceAll(
          'executor = WorktreeCodeExecutor(MockLLMClient())',
          'executor = _SelfCheckWorktreeExecutor(MockLLMClient())',
        )
        .replaceAll(
          'executor = FullstackWorktreeExecutor()',
          'executor = _SelfCheckFullstackExecutor()',
        )
        .replaceAll('PrecommitVerifier().execute(', '_SelfCheckPrecommitVerifier().execute(')
        .replaceAll('manager = TaskManager()', 'manager = _SelfCheckTaskManager()')
      writeFileSync(selfCheckPath, patchedSelfCheck)
    }
    applied.push(SELF_CHECK_CHANGE_CONTEXT_FIXTURE_PATCH)
  }

  const pluginMigrationSecurityTestPath = join(
    resolve(target),
    'tests',
    'test_plugin_migration_security.py',
  )
  if (existsSync(pluginMigrationSecurityTestPath)) {
    const securityTest = readFileSync(pluginMigrationSecurityTestPath, 'utf8')
    if (securityTest.includes(YUNXIAO_MIGRATION_DEPENDENCY_OLD)) {
      writeFileSync(
        pluginMigrationSecurityTestPath,
        securityTest.replace(
          YUNXIAO_MIGRATION_DEPENDENCY_OLD,
          YUNXIAO_MIGRATION_DEPENDENCY_CURRENT,
        ),
      )
    } else if (!securityTest.includes(YUNXIAO_MIGRATION_DEPENDENCY_CURRENT)) {
      throw new Error('Harness Core 云效 MCP 依赖漂移测试契约变化，必须人工复核后才能同步')
    }
    applied.push(CURRENT_YUNXIAO_MCP_DEPENDENCY_TEST_PATCH)
  }

  const externalIoBoundaryPath = join(
    resolve(target),
    'config',
    'external_io_boundaries.v1.json',
  )
  if (existsSync(externalIoBoundaryPath)) {
    const policy = JSON.parse(readFileSync(externalIoBoundaryPath, 'utf8'))
    if (!policy || !Array.isArray(policy.rules)) {
      throw new Error('Harness Core 外部 I/O 审计策略格式漂移，必须人工复核后才能同步')
    }
    const ruleFor = (rootId, relativePath) => policy.rules.find(
      (rule) => rule?.root_id === rootId && rule?.relative_path === relativePath,
    )
    for (const relativePath of [
      'app/codex_cli_worker.py',
      'app/enterprise_gate.py',
      'app/harness.py',
      'scripts/verify.sh',
      'tools/self_check.py',
    ]) {
      const rule = ruleFor('harness', relativePath)
      const source = join(resolve(target), relativePath)
      if (rule && existsSync(source)) rule.file_sha256 = sha256OfFile(source)
    }

    const postgresRule = ruleFor(
      'plugin:his-engineering',
      'scripts/postgresql_mcp_server.py',
    )
    if (postgresRule) {
      const findings = JSON.stringify(postgresRule.findings)
      if (
        findings !== JSON.stringify(POSTGRESQL_MCP_EXTERNAL_IO_FINDINGS_OLD)
        && findings !== JSON.stringify(POSTGRESQL_MCP_EXTERNAL_IO_FINDINGS_CURRENT)
      ) {
        throw new Error('Harness Core PostgreSQL MCP 外部 I/O 审计契约漂移，必须人工复核后才能同步')
      }
      postgresRule.findings = POSTGRESQL_MCP_EXTERNAL_IO_FINDINGS_CURRENT
    }

    const compatibilitySkillRelativePath = 'skills/yunxiao-workitem-evidence/SKILL.md'
    const compatibilitySkillPath = join(resolve(target), compatibilitySkillRelativePath)
    if (existsSync(compatibilitySkillPath)) {
      let compatibilityRule = ruleFor('harness', compatibilitySkillRelativePath)
      if (compatibilityRule) {
        if (
          JSON.stringify(compatibilityRule.findings)
          !== JSON.stringify(COMPATIBILITY_SKILL_EXTERNAL_IO_FINDINGS)
        ) {
          throw new Error('Harness Core 兼容技能外部 I/O 审计契约漂移，必须人工复核后才能同步')
        }
        compatibilityRule.file_sha256 = sha256OfFile(compatibilitySkillPath)
      } else {
        compatibilityRule = {
          root_id: 'harness',
          relative_path: compatibilitySkillRelativePath,
          file_sha256: sha256OfFile(compatibilitySkillPath),
          findings: COMPATIBILITY_SKILL_EXTERNAL_IO_FINDINGS,
          disposition: 'compatibility_quarantine',
          owner: 'harness-platform',
          rationale: 'Legacy compatibility skill process guidance remains quarantined until its canonical MCP evidence flow fully replaces direct script invocation.',
        }
        policy.rules.push(compatibilityRule)
      }
    }
    writeFileSync(externalIoBoundaryPath, `${JSON.stringify(policy, null, 2)}\n`)
    applied.push(EXTERNAL_IO_AUDIT_POLICY_REBIND_PATCH)
  }

  return applied
}

/**
 * 把 Core 仓库同级的历史兼容 Skill 一并冻结到 Core 资源目录。
 * 这些入口只做 canonical 插件代理，但桌面安装包必须保留它们，避免旧调用方失效。
 */
export function copyHarnessCompatibilitySkills(
  compatibilityRoot,
  target,
  skillNames = HARNESS_COMPATIBILITY_SKILLS,
) {
  const sourceRoot = resolve(compatibilityRoot)
  if (!existsSync(sourceRoot)) return 0
  const targetSkills = join(resolve(target), 'skills')
  const files = []
  const collect = (absolute, relativeBase) => {
    for (const entry of readdirSync(absolute, { withFileTypes: true })) {
      const entryPath = join(absolute, entry.name)
      const relativePath = `${relativeBase}/${entry.name}`
      if (!isVendorablePath(`skills/${relativePath}`)) continue
      if (entry.isDirectory()) collect(entryPath, relativePath)
      else if (entry.isFile()) files.push({ absolute: entryPath, relativePath })
    }
  }
  const readme = join(sourceRoot, 'README.md')
  if (existsSync(readme)) files.push({ absolute: readme, relativePath: 'README.md' })
  for (const skillName of skillNames) {
    const skillRoot = join(sourceRoot, skillName)
    if (existsSync(skillRoot)) collect(skillRoot, skillName)
  }
  for (const file of files) {
    const destination = join(targetSkills, file.relativePath)
    mkdirSync(dirname(destination), { recursive: true })
    cpSync(file.absolute, destination)
  }
  return files.length
}

function collectHarnessCoreFiles(sourceRoot) {
  const files = []
  const collect = (absolute, base) => {
    for (const entry of readdirSync(absolute, { withFileTypes: true })) {
      const entryPath = join(absolute, entry.name)
      const relativePath = base === '' ? entry.name : `${base}/${entry.name}`
      if (!isVendorablePath(relativePath)) continue
      if (entry.isDirectory()) collect(entryPath, relativePath)
      else if (entry.isFile()) files.push({ absolute: entryPath, relativePath })
    }
  }
  for (const name of HARNESS_CORE_VENDOR_DIRS) {
    const directory = join(sourceRoot, name)
    if (existsSync(directory)) collect(directory, name)
  }
  for (const name of HARNESS_CORE_VENDOR_FILES) {
    const file = join(sourceRoot, name)
    if (existsSync(file)) files.push({ absolute: file, relativePath: name })
  }
  return files
}

function summarizeHarnessCoreFiles(files) {
  return {
    fileCount: files.length,
    totalBytes: files.reduce((sum, file) => sum + statSync(file.absolute).size, 0),
    manifestSha256: sha256OfEntries(
      files.map((file) => `${file.relativePath}:${sha256OfFile(file.absolute)}`),
    ),
  }
}

/** 计算与发布复制完全相同的内容摘要，不改动源目录或目标目录。 */
export function summarizeHarnessCore(source) {
  const sourceRoot = resolve(source)
  if (!existsSync(sourceRoot)) throw new Error(`Harness Core 源目录不存在：${sourceRoot}`)
  return summarizeHarnessCoreFiles(collectHarnessCoreFiles(sourceRoot))
}

/** 拷贝 vendor 清单内的 Core 内容到目标目录，返回清单统计。preserve 里的顶层条目（如已安装的 Python 运行时）不会被清空。 */
export function copyHarnessCore(source, target, { preserve = [] } = []) {
  const sourceRoot = resolve(source)
  const targetRoot = resolve(target)
  if (!existsSync(sourceRoot)) throw new Error(`Harness Core 源目录不存在：${sourceRoot}`)
  if (existsSync(targetRoot)) {
    for (const entry of readdirSync(targetRoot)) {
      if (!preserve.includes(entry)) rmSync(join(targetRoot, entry), { recursive: true, force: true })
    }
  }
  mkdirSync(targetRoot, { recursive: true })
  const files = collectHarnessCoreFiles(sourceRoot)
  for (const file of files) {
    const destination = join(targetRoot, file.relativePath)
    mkdirSync(dirname(destination), { recursive: true })
    cpSync(file.absolute, destination)
  }
  return summarizeHarnessCoreFiles(files)
}

/** 发布包完整性门禁：缺少任何运行或验证契约都拒绝继续组装。 */
export function verifyHarnessCoreLayout(target) {
  const root = resolve(target)
  const missing = HARNESS_CORE_REQUIRED_PATHS.filter((entry) => !existsSync(join(root, entry)))
  if (missing.length > 0) {
    throw new Error(`Harness Core 发布内容不完整，缺少：${missing.join(', ')}`)
  }
}

function sha256OfEntries(entries) {
  return createHash('sha256').update(entries.join('\n')).digest('hex')
}

function sha256OfFile(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

/**
 * 扫描已拷贝目录中的数据类文件是否带凭证样式赋值。
 * 返回告警列表（.py 命中）；数据文件命中直接抛错。
 */
export function verifyVendorNoSecrets(target) {
  const warnings = []
  const walk = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const entryPath = join(directory, entry.name)
      const relativePath = relative(resolve(target), entryPath)
      if (entry.isDirectory()) {
        walk(entryPath)
        continue
      }
      if (!entry.isFile() || statSync(entryPath).size > 2 * 1024 * 1024) continue
      const text = readFileSync(entryPath, 'utf8')
      if (!isSecretAssignment(text)) continue
      if (SECRET_TEXT_FILES.test(entry.name)) {
        throw new Error(`vendor 目录中的 ${entry.name} 包含凭证样式赋值，禁止带入仓库：${relativePath}`)
      }
      warnings.push(relativePath)
    }
  }
  walk(resolve(target))
  return warnings
}

export function writeVendorManifest(
  target,
  { source, fileCount, totalBytes, manifestSha256, compatibilityPatches = [] },
) {
  const manifest = {
    schema: 'harness-core-vendor.v1',
    source: resolve(source),
    syncedAt: new Date().toISOString(),
    compatibilityPatches,
    fileCount,
    totalBytes,
    manifestSha256,
  }
  writeFileSync(join(resolve(target), VENDOR_MANIFEST_NAME), `${JSON.stringify(manifest, null, 2)}\n`)
  return manifest
}

/** 源目录解析：显式指定时只认显式值；否则回退 env > 上次记录路径 > 常用默认路径。 */
export function resolveHarnessCoreSource(repositoryRoot, explicit) {
  if (typeof explicit === 'string' && explicit.trim() !== '') {
    const resolved = resolve(explicit)
    return isHarnessCoreSource(resolved) ? resolved : ''
  }
  const candidates = [
    process.env.HARNESS_CORE_SOURCE,
    readRecordedSource(join(repositoryRoot, 'vendor', 'harness-core')),
    '/Users/lym/WorkCode/ai/Harness',
  ].filter((value) => typeof value === 'string' && value.trim() !== '')
  for (const candidate of candidates) {
    const resolved = resolve(candidate)
    if (isHarnessCoreSource(resolved)) return resolved
  }
  return ''
}

function readRecordedSource(vendor) {
  const manifestPath = join(vendor, VENDOR_MANIFEST_NAME)
  if (!existsSync(manifestPath)) return undefined
  try {
    const value = JSON.parse(readFileSync(manifestPath, 'utf8'))
    return typeof value.source === 'string' ? value.source : undefined
  } catch {
    return undefined
  }
}

function isHarnessCoreSource(directory) {
  return existsSync(join(directory, 'app'))
    && existsSync(join(directory, 'tools', 'harness_host_server.py'))
    && existsSync(join(directory, 'requirements.txt'))
}

/**
 * 构建时自动同步：本机存在 Harness 源目录时，把最新源码 vendor 进仓库，
 * 用户无需记忆任何命令。CI（CI=true）或源目录不存在时跳过，使用仓库内
 * vendor 副本。密钥扫描门禁照常生效：源码里出现真实凭证样式直接让构建失败。
 */
export function syncVendorFromSource(repositoryRoot, { source } = {}) {
  if (process.env.DSH_HARNESS_VENDOR_SYNC === '0' || process.env.CI === 'true') {
    return { synced: false, changed: false, source: '', reason: 'disabled' }
  }
  const target = join(repositoryRoot, 'vendor', 'harness-core')
  const resolved = resolveHarnessCoreSource(repositoryRoot, source)
  if (resolved === '') {
    return { synced: false, changed: false, source: '', reason: 'source-unavailable' }
  }
  if (resolved === resolve(target)) {
    return { synced: false, changed: false, source: resolved, reason: 'source-is-vendor' }
  }
  const before = readRecordedManifestSha(target)
  const initialCopy = copyHarnessCore(resolved, target)
  const compatibilitySkillCount = copyHarnessCompatibilitySkills(
    join(dirname(resolved), 'skills'),
    target,
  )
  const compatibilityPatches = applyHarnessCoreCompatibilityPatches(target)
  const copied = compatibilityPatches.length > 0 || compatibilitySkillCount > 0
    ? summarizeHarnessCore(target)
    : initialCopy
  verifyHarnessCoreLayout(target)
  const warnings = verifyVendorNoSecrets(target)
  writeVendorManifest(target, { source: resolved, compatibilityPatches, ...copied })
  return {
    synced: true,
    changed: copied.manifestSha256 !== before,
    source: resolved,
    fileCount: copied.fileCount,
    compatibilityPatches,
    warnings,
  }
}

function readRecordedManifestSha(target) {
  const manifestPath = join(target, VENDOR_MANIFEST_NAME)
  if (!existsSync(manifestPath)) return ''
  try {
    const value = JSON.parse(readFileSync(manifestPath, 'utf8'))
    return typeof value.manifestSha256 === 'string' ? value.manifestSha256 : ''
  } catch {
    return ''
  }
}

function main() {
  const args = process.argv.slice(2)
  const sourceIndex = args.indexOf('--source')
  const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  const target = join(repositoryRoot, 'vendor', 'harness-core')
  const source = resolveHarnessCoreSource(
    repositoryRoot,
    sourceIndex >= 0 ? args[sourceIndex + 1] : undefined,
  )
  if (source === '') {
    throw new Error('未找到可用的 Harness Core 源目录：用 --source 指定，或设置 HARNESS_CORE_SOURCE')
  }
  const initialCopy = copyHarnessCore(source, target)
  const compatibilitySkillCount = copyHarnessCompatibilitySkills(
    join(dirname(source), 'skills'),
    target,
  )
  const compatibilityPatches = applyHarnessCoreCompatibilityPatches(target)
  const copied = compatibilityPatches.length > 0 || compatibilitySkillCount > 0
    ? summarizeHarnessCore(target)
    : initialCopy
  verifyHarnessCoreLayout(target)
  const warnings = verifyVendorNoSecrets(target)
  const manifest = writeVendorManifest(target, { source, compatibilityPatches, ...copied })
  process.stdout.write(
    `已 vendor Harness Core：${copied.fileCount} 个文件，${(copied.totalBytes / 1024 / 1024).toFixed(1)}MB → vendor/harness-core\n`,
  )
  if (warnings.length > 0) {
    process.stdout.write(`提示：以下 .py 文件包含凭证读取样式（非真实凭证），已放行：\n${warnings.join('\n')}\n`)
  }
  process.stdout.write(`${JSON.stringify(manifest, null, 2)}\n`)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main()
}
