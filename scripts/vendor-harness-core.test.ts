import { createHash } from 'node:crypto'
import { existsSync, mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterAll, describe, expect, it } from 'vitest'
import {
  HARNESS_CORE_VENDOR_DIRS,
  HARNESS_CORE_VENDOR_FILES,
  HARNESS_CORE_REQUIRED_PATHS,
  VENDOR_MANIFEST_NAME,
  applyHarnessCoreCompatibilityPatches,
  copyHarnessCompatibilitySkills,
  copyHarnessCore,
  isSecretAssignment,
  isVendorablePath,
  resolveHarnessCoreSource,
  summarizeHarnessCore,
  syncVendorFromSource,
  verifyHarnessCoreLayout,
  verifyVendorNoSecrets,
} from './vendor-harness-core.mjs'

const directories: string[] = []
function temporary() {
  const directory = mkdtempSync(join(tmpdir(), 'vendor-core-'))
  directories.push(directory)
  return directory
}

afterAll(() => {
  for (const directory of directories) rmSync(directory, { recursive: true, force: true })
})

describe('harness core vendoring', () => {
  it('applies the audited Codex 0.150 compatibility patch idempotently', () => {
    const target = temporary()
    mkdirSync(join(target, 'app'), { recursive: true })
    const worker = join(target, 'app', 'codex_cli_worker.py')
    writeFileSync(worker, [
      '# The current bundled CLI is 0.149.x; its fixed `exec --json --ephemeral`',
      '# worker/reviewer flags remain compatible with the 0.147 contract. Keep the',
      '# upper bound so a future incompatible CLI still fails closed.',
      '_SUPPORTED_VERSION = ((0, 147, 0), (0, 150, 0))',
      '',
    ].join('\n'))

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual(['codex-cli-0.150'])
    expect(readFileSync(worker, 'utf8')).toContain(
      '_SUPPORTED_VERSION = ((0, 147, 0), (0, 151, 0))',
    )
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual(['codex-cli-0.150'])
  })

  it('fails closed when the Codex worker version contract drifts unexpectedly', () => {
    const target = temporary()
    mkdirSync(join(target, 'app'), { recursive: true })
    writeFileSync(
      join(target, 'app', 'codex_cli_worker.py'),
      '_SUPPORTED_VERSION = ((0, 148, 0), (0, 999, 0))\n',
    )

    expect(() => applyHarnessCoreCompatibilityPatches(target)).toThrow(/契约漂移/)
  })

  it('publishes the canonical permanent database read-only boundary in the manager design', () => {
    const target = temporary()
    const specs = join(target, 'docs', 'superpowers', 'specs')
    mkdirSync(specs, { recursive: true })
    const design = join(specs, '2026-08-09-manager-provider-configuration-design.md')
    writeFileSync(
      design,
      '阶段 C 继续受控接入。数据库永久停留在只读与 SQL 草案层，不进入写执行器。\n',
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'manager-database-permanent-readonly-wording',
    ])
    expect(readFileSync(design, 'utf8')).toContain(
      '数据库永久只读，停留在查询与 SQL 草案层，不进入写执行器。',
    )
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'manager-database-permanent-readonly-wording',
    ])
  })

  it('keeps observe-mode governance blocks on the non-executing closure artifact path', () => {
    const target = temporary()
    const app = join(target, 'app')
    mkdirSync(app, { recursive: true })
    const harness = join(app, 'harness.py')
    writeFileSync(
      harness,
      [
        '        if governance_execution_blocked and (',
        '            resolved_execution_mode != "core-closure-trial"',
        '            or (',
        '                routed_governance is not None',
        '                and getattr(routed_governance, "mode", None) == "enforce"',
        '            )',
        '        ):',
        '            block_reason = governance_error or "blocked"',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'governance-auto-local-blocked-artifact-boundary',
    ])
    expect(readFileSync(harness, 'utf8')).toContain(
      'or effective_governance_mode == "enforce"',
    )
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'governance-auto-local-blocked-artifact-boundary',
    ])
  })

  it('lets an eligible bounded auto-local mutation skip only the broad repository scan', () => {
    const target = temporary()
    const app = join(target, 'app')
    mkdirSync(app, { recursive: true })
    const harness = join(app, 'harness.py')
    writeFileSync(
      harness,
      [
        '        if (',
        '            fast_local_decision',
        '            and fast_local_decision["skip_project_context_scan"]',
        '            and not mutation_requested',
        '        ):',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'auto-local-bounded-scan-skip',
    ])
    expect(readFileSync(harness, 'utf8')).not.toContain('and not mutation_requested')
    expect(readFileSync(harness, 'utf8')).toContain('and fast_local_decision.get("eligible") is True')
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'auto-local-bounded-scan-skip',
    ])
  })

  it('keeps a deterministic acceptance command inside the authoritative verification scope', () => {
    const target = temporary()
    const app = join(target, 'app')
    mkdirSync(app, { recursive: true })
    const harness = join(app, 'harness.py')
    writeFileSync(
      harness,
      [
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
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'authoritative-acceptance-verify-command',
    ])
    const patched = readFileSync(harness, 'utf8')
    expect(patched).toContain('authoritative_verify_commands = list(single_pass_contract.verify_commands)')
    expect(patched).toContain('authoritative_verify_commands.append(acceptance_contract_result.verify_command)')
    expect(patched).not.toContain('blockers=(GOVERNANCE_ACCEPTANCE_ERROR,)')
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'authoritative-acceptance-verify-command',
    ])
  })

  it('reconciles an older understanding result only after enforce governance is fully ready', () => {
    const target = temporary()
    const app = join(target, 'app')
    mkdirSync(app, { recursive: true })
    const harness = join(app, 'harness.py')
    writeFileSync(
      harness,
      [
        '        understanding_execution_blocked = (',
        '            mutation_requested and not requirement_understanding.can_modify',
        '        )',
        '        task_stages.record(',
        '            "understanding",',
        '            "blocked" if understanding_execution_blocked else "completed",',
        '            "understanding_blocked" if understanding_execution_blocked else "understanding_ready",',
        '        )',
        '        legacy_governance_result = None',
        '        legacy_single_pass_contract = None',
        '        legacy_governance_error = ""',
        '        routed_governance = None',
        '        # governance body',
        '        ) = _resolve_governance_execution(',
        '            requested_mode=requirement_governance,',
        '        )',
        '        governance_ready = (',
        '            not governance_execution_blocked',
        '            and _governance_outputs_ready(governance_result, single_pass_contract)',
        '        )',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'enforce-understanding-governance-reconciliation',
    ])
    const patched = readFileSync(harness, 'utf8')
    expect(patched).toContain('effective_governance_mode == "enforce"')
    expect(patched).toContain('requirement_understanding = replace(')
    expect(patched.indexOf('task_stages.record(')).toBeGreaterThan(
      patched.indexOf(') = _resolve_governance_execution('),
    )
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'enforce-understanding-governance-reconciliation',
    ])
  })

  it('resolves relocatable plugin roots from the copied runtime config directory', () => {
    const target = temporary()
    const tools = join(target, 'tools')
    mkdirSync(tools, { recursive: true })
    const checker = join(tools, 'capability_check.py')
    writeFileSync(
      checker,
      [
        '    return RuntimeConfig(',
        '        routing_mode,',
        '        tuple(roots),',
        '        external_writes_default,',
        '        timeout,',
        '        knowledge_home,',
        '    )',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'relocatable-plugin-root-resolution',
    ])
    expect(readFileSync(checker, 'utf8')).toContain(
      'config_directory = Path(path_value).expanduser().resolve().parent',
    )
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'relocatable-plugin-root-resolution',
    ])
  })

  it('discovers the frozen his-engineering plugin from the relocatable desktop bundle', () => {
    const target = temporary()
    const app = join(target, 'app')
    mkdirSync(app, { recursive: true })
    const adapter = join(app, 'delivery_closure.py')
    writeFileSync(
      adapter,
      [
        '_FIXED_ROOT = Path("/Users/lym/plugins/his-engineering")',
        '    candidates.append(_FIXED_ROOT)',
        '        or manifest.get("plugin_version") != "0.1.0"',
        '        or plugin.get("version") != "0.1.0"',
        '            or descriptor.plugin_version != "0.1.0"',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'relocatable-his-engineering-adapter-root',
      'current-his-engineering-adapter-version',
    ])
    const patched = readFileSync(adapter, 'utf8')
    expect(patched).toContain('_BUNDLED_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "his-engineering"')
    expect(patched).toContain(
      '    if not test_root:\n        candidates.append(_BUNDLED_ROOT)',
    )
    expect(patched).toContain('or not isinstance(manifest.get("plugin_version"), str)')
    expect(patched).toContain('or plugin.get("version") != manifest.get("plugin_version")')
    expect(patched).toContain(
      'or descriptor.plugin_version != _read_json(root / "capabilities.json").get("plugin_version")',
    )
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'relocatable-his-engineering-adapter-root',
      'current-his-engineering-adapter-version',
    ])
  })

  it('preserves the newer inventory-pinned his-engineering adapter version contract', () => {
    const target = temporary()
    const app = join(target, 'app')
    mkdirSync(app, { recursive: true })
    const adapter = join(app, 'delivery_closure.py')
    writeFileSync(adapter, [
      '_FIXED_ROOT = Path("/Users/lym/plugins/his-engineering")',
      '    candidates.append(_FIXED_ROOT)',
      '        or manifest.get("plugin_version") != expected_plugin.version',
      '        or plugin.get("version") != expected_plugin.version',
      '            or descriptor.plugin_version != expected_plugin.version',
      '',
    ].join('\n'))

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'relocatable-his-engineering-adapter-root',
      'current-his-engineering-adapter-version',
    ])
    const patched = readFileSync(adapter, 'utf8')
    expect(patched).toContain('manifest.get("plugin_version") != expected_plugin.version')
    expect(patched).toContain('plugin.get("version") != expected_plugin.version')
    expect(patched).toContain('descriptor.plugin_version != expected_plugin.version')
  })

  it('runs PostgreSQL compatibility CLI checks from the packaged Core root', () => {
    const target = temporary()
    const tests = join(target, 'tests')
    mkdirSync(tests, { recursive: true })
    const compatibilityTest = join(tests, 'test_pg_evidence_compatibility.py')
    writeFileSync(
      compatibilityTest,
      [
        'from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT, REPOSITORY_ROOT',
        'ROOT = REPOSITORY_ROOT',
        'HARNESS_ROOT = ROOT / "Harness"',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'relocatable-pg-evidence-test-harness-root',
    ])
    const patched = readFileSync(compatibilityTest, 'utf8')
    expect(patched).toContain('from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT')
    expect(patched).toContain('HARNESS_ROOT = Path(__file__).resolve().parents[1]')
    expect(patched).not.toContain('ROOT / "Harness"')
  })

  it('accepts the native MCP-only PostgreSQL compatibility test without plugin-root imports', () => {
    const target = temporary()
    const tests = join(target, 'tests')
    mkdirSync(tests, { recursive: true })
    const compatibilityTest = join(tests, 'test_pg_evidence_compatibility.py')
    writeFileSync(compatibilityTest, [
      'from pathlib import Path',
      '',
      'HARNESS_ROOT = Path(__file__).resolve().parents[1]',
      'CLI_PATH = HARNESS_ROOT / "tools" / "pg_evidence.py"',
      '',
      'class PgEvidenceCompatibilityTests(unittest.TestCase):',
      '    """The compatibility contract is now fail-closed MCP-only retirement."""',
      '',
    ].join('\n'))

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([])
    expect(readFileSync(compatibilityTest, 'utf8')).not.toContain('PLUGIN_SOURCE_ROOT')
  })

  it('bridges the current PostgreSQL MCP descriptor to the frozen legacy evidence CLI', () => {
    const target = temporary()
    const app = join(target, 'app')
    mkdirSync(app, { recursive: true })
    const adapter = join(app, 'pg_evidence.py')
    writeFileSync(
      adapter,
      [
        'from __future__ import annotations',
        '',
        'import hashlib',
        '        expected_entrypoint = (',
        '            resolved_root / "scripts" / "database_read.py"',
        '        ).resolve(strict=True)',
        '        or descriptor.scopes != _EXPECTED_SCOPES',
        '        or descriptor.plugin_root != resolved_root',
        '        or descriptor.entrypoint != expected_entrypoint',
        '        or descriptor.declared_entrypoint',
        '        != resolved_root / "scripts" / "database_read.py"',
        '    registry = CapabilityRegistry.from_plugin_roots(',
        '        [Path(__provider_root__)]',
        '    )',
        '    return CapabilityService(',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'current-postgresql-mcp-legacy-bridge',
    ])
    const patched = readFileSync(adapter, 'utf8')
    expect(patched).toContain('from dataclasses import replace')
    expect(patched).toContain('mcp_entrypoint_path = resolved_root / "scripts" / "postgresql_mcp_server.py"')
    expect(patched).toContain('legacy_descriptor = replace(')
    expect(patched).toContain('dependency_identities=((provider_dependency, registry_path_identity(provider_dependency)),)')
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'current-postgresql-mcp-legacy-bridge',
    ])
  })

  it('resolves external I/O policy plugin roots relative to the runtime config', () => {
    const target = temporary()
    const app = join(target, 'app')
    mkdirSync(app, { recursive: true })
    const policy = join(app, 'external_io_policy.py')
    writeFileSync(
      policy,
      [
        'def _plugin_roots(capabilities_config_path: Path) -> dict[str, Path]:',
        '    payload = _read_json(capabilities_config_path, "capability config")',
        '    raw_roots = payload.get("plugin_roots")',
        '    result: dict[str, Path] = {}',
        '        root = Path(raw_root).resolve()',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'relocatable-external-io-plugin-roots',
    ])
    const patched = readFileSync(policy, 'utf8')
    expect(patched).toContain(
      'config_directory = capabilities_config_path.expanduser().resolve().parent',
    )
    expect(patched).toContain(
      'root = (candidate if candidate.is_absolute() else config_directory / candidate).resolve()',
    )
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'relocatable-external-io-plugin-roots',
    ])
  })

  it('excludes the sealed bundled Python runtime from application source I/O scanning', () => {
    const target = temporary()
    const app = join(target, 'app')
    mkdirSync(app, { recursive: true })
    const inventory = join(app, 'external_io_inventory.py')
    writeFileSync(
      inventory,
      [
        '        "outputs",',
        '        "tests",',
        '        "venv",',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'ignore-bundled-python-runtime-external-io-scan',
    ])
    expect(readFileSync(inventory, 'utf8')).toContain('        "runtime",')
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'ignore-bundled-python-runtime-external-io-scan',
    ])
  })

  it('rebinds the external I/O audit to exact compatibility-patched sources', () => {
    const target = temporary()
    const app = join(target, 'app')
    const config = join(target, 'config')
    const tools = join(target, 'tools')
    const compatibilitySkill = join(target, 'skills', 'yunxiao-workitem-evidence', 'SKILL.md')
    mkdirSync(app, { recursive: true })
    mkdirSync(config, { recursive: true })
    mkdirSync(tools, { recursive: true })
    mkdirSync(join(target, 'skills', 'yunxiao-workitem-evidence'), { recursive: true })
    const worker = join(app, 'codex_cli_worker.py')
    writeFileSync(
      worker,
      [
        '# The current bundled CLI is 0.150.x; its fixed `exec --json --ephemeral`',
        '# worker/reviewer flags remain compatible with the 0.147 contract. Keep the',
        '# upper bound so a future incompatible CLI still fails closed.',
        '_SUPPORTED_VERSION = ((0, 147, 0), (0, 151, 0))',
        'subprocess.run(["codex"])',
        '',
      ].join('\n'),
    )
    writeFileSync(compatibilitySkill, 'Run `python3 scripts/evidence.py`.\n')
    const selfCheck = join(tools, 'self_check.py')
    writeFileSync(selfCheck, 'class _SelfCheckChangeContext:\n    pass\n')
    const policyPath = join(config, 'external_io_boundaries.v1.json')
    writeFileSync(
      policyPath,
      `${JSON.stringify({
        schema_version: 'his-external-io-boundaries.v1',
        roots: [],
        rules: [
          {
            root_id: 'harness',
            relative_path: 'app/codex_cli_worker.py',
            file_sha256: '0'.repeat(64),
            findings: [{ category: 'process', symbol: 'subprocess.run', occurrence: 1 }],
            disposition: 'worker_allowed',
            owner: 'harness-platform',
            rationale: 'test',
          },
          {
            root_id: 'plugin:his-engineering',
            relative_path: 'scripts/postgresql_mcp_server.py',
            file_sha256: '160e2c7da3817d6f169ccfb1d8e11c1f5693fe22af91b4a42efa046b0df40b66',
            findings: [
              { category: 'database', symbol: 'psycopg.connect', occurrence: 1 },
              { category: 'database', symbol: 'psycopg2.connect', occurrence: 1 },
            ],
            disposition: 'mcp_required',
            owner: 'his-engineering',
            rationale: 'test',
          },
          {
            root_id: 'harness',
            relative_path: 'tools/self_check.py',
            file_sha256: '0'.repeat(64),
            findings: [{ category: 'process', symbol: 'subprocess.run', occurrence: 1 }],
            disposition: 'worker_allowed',
            owner: 'harness-platform',
            rationale: 'test',
          },
        ],
      }, null, 2)}\n`,
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'codex-cli-0.150',
      'self-check-change-context-fixture',
      'external-io-audit-policy-rebind',
    ])
    const policy = JSON.parse(readFileSync(policyPath, 'utf8'))
    const workerRule = policy.rules.find(
      (rule: { relative_path: string }) => rule.relative_path === 'app/codex_cli_worker.py',
    )
    expect(workerRule.file_sha256).toBe(
      createHash('sha256').update(readFileSync(worker)).digest('hex'),
    )
    const selfCheckRule = policy.rules.find(
      (rule: { relative_path: string }) => rule.relative_path === 'tools/self_check.py',
    )
    expect(selfCheckRule.file_sha256).toBe(
      createHash('sha256').update(readFileSync(selfCheck)).digest('hex'),
    )
    const postgresRule = policy.rules.find(
      (rule: { relative_path: string }) => rule.relative_path === 'scripts/postgresql_mcp_server.py',
    )
    expect(postgresRule.findings).toEqual([
      { category: 'database', symbol: 'psycopg.connect', occurrence: 1 },
      { category: 'database', symbol: 'psycopg.connect', occurrence: 2 },
      { category: 'database', symbol: 'psycopg2.connect', occurrence: 1 },
      { category: 'database', symbol: 'psycopg2.connect', occurrence: 2 },
    ])
    const compatibilityRule = policy.rules.find(
      (rule: { relative_path: string }) =>
        rule.relative_path === 'skills/yunxiao-workitem-evidence/SKILL.md',
    )
    expect(compatibilityRule.file_sha256).toBe(
      createHash('sha256').update(readFileSync(compatibilitySkill)).digest('hex'),
    )
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'codex-cli-0.150',
      'self-check-change-context-fixture',
      'external-io-audit-policy-rebind',
    ])
  })

  it('aligns the migration security drift fixture with the frozen Yunxiao MCP dependency', () => {
    const target = temporary()
    const tests = join(target, 'tests')
    mkdirSync(tests, { recursive: true })
    const securityTest = join(tests, 'test_plugin_migration_security.py')
    writeFileSync(
      securityTest,
      '                "skills/yunxiao-workitem-read/scripts/yunxiao_evidence.py",\n',
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'current-yunxiao-mcp-dependency-test-alignment',
    ])
    expect(readFileSync(securityTest, 'utf8')).toContain(
      '                "scripts/yunxiao_evidence.py",',
    )
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'current-yunxiao-mcp-dependency-test-alignment',
    ])
  })

  it('aligns provider status expectations with the three native readonly MCP capabilities', () => {
    const target = temporary()
    const tests = join(target, 'tests')
    mkdirSync(tests, { recursive: true })
    const serverTest = join(tests, 'test_server_core_status_api.py')
    writeFileSync(
      serverTest,
      [
        '    def test_default_manager_task_dispatch_runs_local_thirteen_stage_governance(self) -> None:',
        '        with (',
        '            fixture,',
        '        ):',
        '            pass',
        '        self.assertEqual("requirement_workflow", result["downstream"])',
        '        self.assertEqual(13, workflow["stage_count"])',
        '        self.assertEqual("local_deterministic", workflow["analysis_backend"])',
        '        available_capabilities = {',
        '            "git.diff",',
        '            "source.read",',
        '            "source.search",',
        '            "git.history",',
        '            "verification.run-local",',
        '            "code.review-local",',
        '        }',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'fifteen-stage-governance-test-alignment',
      'native-readonly-mcp-provider-status-test-alignment',
    ])
    const patched = readFileSync(serverTest, 'utf8')
    expect(patched).toContain('            "workitem.read",')
    expect(patched).toContain('            "gitlab.read",')
    expect(patched).toContain('            "database.inspect",')
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'fifteen-stage-governance-test-alignment',
      'native-readonly-mcp-provider-status-test-alignment',
    ])
  })

  it('uses the sealed packaged Python runtime while preserving the source venv fallback', () => {
    const target = temporary()
    const scripts = join(target, 'scripts')
    const tests = join(target, 'tests')
    mkdirSync(scripts, { recursive: true })
    mkdirSync(tests, { recursive: true })
    const verify = join(scripts, 'verify.sh')
    writeFileSync(
      verify,
      [
        'ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"',
        'PYTHON="$ROOT_DIR/.venv/bin/python"',
        '',
        'if [ ! -x "$PYTHON" ]; then',
        '  echo "Harness .venv interpreter is missing: $PYTHON" >&2',
        '  exit 2',
        'fi',
        '',
      ].join('\n'),
    )
    const verifyTest = join(tests, 'test_verify_entrypoint.py')
    writeFileSync(
      verifyTest,
      [
        '    def test_verify_script_uses_project_venv_and_no_system_python(self) -> None:',
        '        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")',
        '',
        '        self.assertIn(\'PYTHON="$ROOT_DIR/.venv/bin/python"\', script)',
        '        self.assertIn(\'export PYTHONDONTWRITEBYTECODE="1"\', script)',
        '        self.assertNotIn("python3 -m unittest", script)',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'packaged-python-verify-entrypoint',
    ])
    const patched = readFileSync(verify, 'utf8')
    expect(patched).toContain('PACKAGED_PYTHON="$ROOT_DIR/runtime/bin/python3"')
    expect(patched).toContain('VENV_PYTHON="$ROOT_DIR/.venv/bin/python"')
    const patchedTest = readFileSync(verifyTest, 'utf8')
    expect(patchedTest).toContain('uses_packaged_runtime_with_project_venv_fallback')
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'packaged-python-verify-entrypoint',
    ])
  })

  it('gives the expanded Core unit suite a separate bounded enterprise-gate timeout', () => {
    const target = temporary()
    const app = join(target, 'app')
    const tests = join(target, 'tests')
    mkdirSync(app, { recursive: true })
    mkdirSync(tests, { recursive: true })
    const gate = join(app, 'enterprise_gate.py')
    writeFileSync(gate, [
      'STAGE_TIMEOUT_SECONDS = 300',
      '        "stage_timeout_seconds": STAGE_TIMEOUT_SECONDS,',
      '',
      'def validate_stages(stages: Sequence[str]) -> tuple[str, ...]:',
      '    return tuple(stages)',
      '',
      'def run_gate_stage(',
      '    stage: str,',
      '            timeout=STAGE_TIMEOUT_SECONDS,',
      '            check=False,',
      '',
    ].join('\n'))
    const gateTest = join(tests, 'test_enterprise_gate.py')
    writeFileSync(gateTest, [
      '    run_gate_stage,',
      '    sanitize_environment,',
      '        self.assertEqual(300, result["stage_timeout_seconds"])',
      '                side_effect=subprocess.TimeoutExpired([sys.executable], 300, output="", stderr=""),',
      '        self.assertEqual("timeout", result["reason"])',
      '',
    ].join('\n'))

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'bounded-expanded-unit-enterprise-gate-timeout',
    ])
    const patched = readFileSync(gate, 'utf8')
    expect(patched).toContain('UNIT_STAGE_TIMEOUT_SECONDS = 1200')
    expect(patched).toContain('timeout=stage_timeout_seconds(stage)')
    expect(patched).toContain('def stage_timeout_seconds(stage: str) -> int:')
    const patchedTest = readFileSync(gateTest, 'utf8')
    expect(patchedTest).toContain('side_effect=subprocess.TimeoutExpired([sys.executable], 1200')
    expect(patchedTest).toContain('self.assertEqual(1200, result["unit_stage_timeout_seconds"])')
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'bounded-expanded-unit-enterprise-gate-timeout',
    ])
  })

  it('creates a fresh offline-gate evidence directory unless an explicit path is supplied', () => {
    const target = temporary()
    const scripts = join(target, 'scripts')
    const tests = join(target, 'tests')
    mkdirSync(scripts, { recursive: true })
    mkdirSync(tests, { recursive: true })
    const verify = join(scripts, 'verify.sh')
    writeFileSync(verify, [
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
      '',
      '  offline)',
      '    OUTPUT_DIR="${HARNESS_GATE_OUTPUT_DIR:-/private/tmp/his-harness-enterprise-gate}"',
      '    exec "$PYTHON" "$ROOT_DIR/tools/enterprise_gate.py" --output-dir "$OUTPUT_DIR"',
      '    ;;',
      '',
    ].join('\n'))
    const verifyTest = join(tests, 'test_verify_entrypoint.py')
    writeFileSync(verifyTest, [
      'class VerifyEntrypointTests(unittest.TestCase):',
      '    def test_verify_script_uses_packaged_runtime_with_project_venv_fallback(self) -> None:',
      '        script = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")',
      '',
      '        self.assertIn(\'PACKAGED_PYTHON="$ROOT_DIR/runtime/bin/python3"\', script)',
      '        self.assertIn(\'VENV_PYTHON="$ROOT_DIR/.venv/bin/python"\', script)',
      '        self.assertIn(\'export PYTHONDONTWRITEBYTECODE="1"\', script)',
      '        self.assertNotIn("python3 -m unittest", script)',
      '',
      '    def test_verify_script_exposes_supported_gates(self) -> None:',
      '        self.assertTrue(True)',
      '',
      '',
      'if __name__ == "__main__":',
      '    unittest.main()',
      '',
    ].join('\n'))

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'packaged-python-verify-entrypoint',
      'unique-offline-gate-output-directory',
    ])
    const patched = readFileSync(verify, 'utf8')
    expect(patched).toContain('if [ -n "${HARNESS_GATE_OUTPUT_DIR:-}" ]; then')
    expect(patched).toContain('mktemp -d "${TMPDIR:-/private/tmp}/his-harness-enterprise-gate.XXXXXX"')
    expect(patched).not.toContain('/private/tmp/his-harness-enterprise-gate}')
    const patchedTest = readFileSync(verifyTest, 'utf8')
    expect(patchedTest).toContain('test_offline_gate_uses_unique_output_by_default')
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'packaged-python-verify-entrypoint',
      'unique-offline-gate-output-directory',
    ])
  })

  it('persists validated Harness decision digests even when their hex resembles PII', () => {
    const target = temporary()
    const app = join(target, 'app')
    const tests = join(target, 'tests')
    mkdirSync(app, { recursive: true })
    mkdirSync(tests, { recursive: true })
    const repository = join(app, 'local_agent_repository.py')
    writeFileSync(repository, [
      '        if event_type == "worker_protocol_rejected":',
      '            validate_protocol_rejection_audit(payload)',
      '        try:',
      '            with self._connect() as connection:',
      '        payload_json = (',
      '                    _encode_validated_audit_mapping(payload)',
      '                    if event_type == "worker_protocol_rejected"',
      '                    else _encode_safe_mapping(payload)',
      '        )',
      '    payload = (',
      '        _decode_validated_audit_mapping(row["payload_json"])',
      '        if event_type in {"review_failed", "worker_protocol_rejected"}',
      '        else _decode_safe_mapping(row["payload_json"])',
      '    )',
      '    if event_type == "review_failed":',
      '        _validate_review_failure(payload)',
      '    elif event_type == "worker_protocol_rejected":',
      '        try:',
      '            validate_protocol_rejection_audit(payload)',
      '        except ValueError:',
      '            raise ValueError(_STORAGE_INVALID) from None',
      '',
      'def _validate_review_failure(value: Mapping[str, object]) -> None:',
      '    pass',
      '',
    ].join('\n'))
    const repositoryTest = join(tests, 'test_local_agent_repository.py')
    writeFileSync(repositoryTest, [
      '    def test_exact_digest_fields_accept_deterministic_hex_values(self) -> None:',
      '        self.assertTrue(True)',
      '',
      '    def test_protocol_rejection_event_rejects_polluted_payload_without_echo(self) -> None:',
      '        self.assertTrue(True)',
      '',
    ].join('\n'))

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'validated-harness-decision-audit-digest',
    ])
    const patched = readFileSync(repository, 'utf8')
    expect(patched).toContain('def _validate_harness_decision_event(')
    expect(patched).toContain('"harness_decision_issued", "worker_protocol_rejected"')
    const patchedTest = readFileSync(repositoryTest, 'utf8')
    expect(patchedTest).toContain('test_harness_decision_event_accepts_pii_shaped_sha256_digest')
    expect(patchedTest).toContain('7cff524767863421371581b5319a473a1e2613e46b0af04b7eaf63068ffad89d')
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'validated-harness-decision-audit-digest',
    ])
  })

  it('vendors legacy compatibility skills into the packaged Core without caches', () => {
    const compatibilityRoot = temporary()
    const target = temporary()
    mkdirSync(join(compatibilityRoot, 'his-harness', '__pycache__'), { recursive: true })
    writeFileSync(join(compatibilityRoot, 'README.md'), '# compatibility\n')
    writeFileSync(join(compatibilityRoot, 'his-harness', 'SKILL.md'), 'status: compatibility\n')
    writeFileSync(join(compatibilityRoot, 'his-harness', '__pycache__', 'skill.pyc'), 'cache')

    const copied = copyHarnessCompatibilitySkills(compatibilityRoot, target, ['his-harness'])

    expect(copied).toBe(2)
    expect(readFileSync(join(target, 'skills', 'README.md'), 'utf8')).toContain('compatibility')
    expect(readFileSync(join(target, 'skills', 'his-harness', 'SKILL.md'), 'utf8')).toContain('compatibility')
    expect(existsSync(join(target, 'skills', 'his-harness', '__pycache__'))).toBe(false)
  })

  it('checks compatibility skill documentation from the packaged Core root', () => {
    const target = temporary()
    const tests = join(target, 'tests')
    mkdirSync(tests, { recursive: true })
    const documentationTest = join(tests, 'test_plugin_documentation.py')
    writeFileSync(
      documentationTest,
      [
        'readme = (REPOSITORY_ROOT / "skills" / "README.md").read_text()',
        'content = (REPOSITORY_ROOT / "skills" / skill / "SKILL.md").read_text()',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'relocatable-compatibility-skill-documentation-root',
    ])
    const patched = readFileSync(documentationTest, 'utf8')
    expect(patched).toContain('HARNESS_ROOT / "skills" / "README.md"')
    expect(patched).toContain('HARNESS_ROOT / "skills" / skill / "SKILL.md"')
  })

  it('resolves role registry plugin roots from the packaged config directory', () => {
    const target = temporary()
    const app = join(target, 'app')
    mkdirSync(app, { recursive: true })
    const registry = join(app, 'role_capability_skill_registry.py')
    writeFileSync(
      registry,
      [
        '        config = _read_json(harness_root / "config" / "capabilities.json")',
        '        raw_roots = config.get("plugin_roots")',
        '        if not isinstance(raw_roots, list):',
        '            raise RoleCapabilitySkillRegistryError("capabilities.json 缺少 plugin_roots。")',
        '        roots = {}',
        '        for value in raw_roots:',
        '            path = Path(value).resolve()',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'relocatable-role-registry-plugin-root-resolution',
    ])
    expect(readFileSync(registry, 'utf8')).toContain(
      'else (config_path.parent / candidate).resolve()',
    )
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'relocatable-role-registry-plugin-root-resolution',
    ])
  })

  it('adds the explicit read-only Codex visual adapter and its sealed schema', () => {
    const target = temporary()
    const app = join(target, 'app')
    mkdirSync(app, { recursive: true })
    const visual = join(app, 'visual_evidence.py')
    writeFileSync(
      visual,
      [
        'from app.codex_cli_worker import CODEX_EXECUTABLE, CodexCliWorker, CodexWorkerRequest',
        '',
        'class HostVisualEvidenceAnalyzer:',
        '    pass',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'explicit-codex-visual-evidence-adapter',
    ])
    expect(readFileSync(visual, 'utf8')).toContain('class CodexCliVisualEvidenceAnalyzer:')
    expect(readFileSync(join(target, 'config', 'schemas', 'visual_evidence.v1.json'), 'utf8')).toContain(
      'his-visual-evidence.v1',
    )
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'explicit-codex-visual-evidence-adapter',
    ])
  })

  it('aligns stale Manager, scope and closure tests with the stricter fifteen-stage gates', () => {
    const target = temporary()
    const tests = join(target, 'tests')
    mkdirSync(tests, { recursive: true })
    const serverTest = join(tests, 'test_server_core_status_api.py')
    writeFileSync(
      serverTest,
      [
        '    def test_default_manager_task_dispatch_runs_local_twelve_stage_governance(self) -> None:',
        '        with (',
        '            fixture,',
        '        ):',
        '            pass',
        '        self.assertEqual("requirement_workflow", result["downstream"])',
        '        self.assertEqual(12, workflow["stage_count"])',
        '        self.assertEqual("local_deterministic", workflow["analysis_backend"])',
        '',
      ].join('\n'),
    )
    const scopeTest = join(tests, 'test_scope_confirmation_integration.py')
    const sourceScopeTest = readFileSync(
      join(process.cwd(), 'vendor', 'harness-core', 'tests', 'test_scope_confirmation_integration.py'),
      'utf8',
    )
    writeFileSync(scopeTest, sourceScopeTest)
    const coreClosureTest = join(tests, 'test_core_closure.py')
    writeFileSync(
      coreClosureTest,
      [
        '        self.assertEqual("blocked", result.status)',
        '        self.assertIn("可执行排序验收契约", "\\n".join(closure["contract"]["blockers"]))',
        '',
      ].join('\n'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'fifteen-stage-governance-test-alignment',
      'understanding-gate-scope-test-alignment',
      'strict-requirement-calibration-test-alignment',
    ])
    expect(readFileSync(serverTest, 'utf8')).toContain('fifteen_stage_governance')
    expect(readFileSync(serverTest, 'utf8')).toContain('assertEqual(15, workflow["stage_count"])')
    expect(readFileSync(scopeTest, 'utf8')).toContain(
      'self.assertEqual("pass", second.evaluation_status)',
    )
    expect(readFileSync(scopeTest, 'utf8')).toContain('self.assertEqual("pending", confirmation["status"])')
    expect(readFileSync(scopeTest, 'utf8')).toContain('self.assertEqual("failed", second.status)')
    expect(readFileSync(scopeTest, 'utf8')).toContain('self.assertIn("需求治理未闭合", second.markdown_report)')
    const patchedClosureTest = readFileSync(coreClosureTest, 'utf8')
    expect(patchedClosureTest).toContain('需求校准未达到 ready_for_development')
    expect(patchedClosureTest).toContain('self.assertNotIn("worktree_manifest_json", artifacts)')
    expect(patchedClosureTest).toContain('self.assertEqual([], database.get_step_runs(result.run_id))')
  })

  it('binds deterministic ChangeContext fixtures into every self-check mutating executor', () => {
    const target = temporary()
    const tools = join(target, 'tools')
    mkdirSync(tools, { recursive: true })
    const selfCheck = join(tools, 'self_check.py')
    writeFileSync(
      selfCheck,
      readFileSync(join(process.cwd(), 'vendor', 'harness-core', 'tools', 'self_check.py'), 'utf8'),
    )

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'self-check-change-context-fixture',
    ])
    const patched = readFileSync(selfCheck, 'utf8')
    expect(patched).toContain('class _SelfCheckChangeContext:')
    expect(patched).toContain('class _SelfCheckWorktreeExecutor:')
    expect(patched).toContain('class _SelfCheckFullstackExecutor:')
    expect(patched).toContain('class _SelfCheckPrecommitVerifier:')
    expect(patched).toContain('class _SelfCheckTaskManager(TaskManager):')
    expect(patched).not.toContain('executor = WorktreeCodeExecutor(MockLLMClient())')
    expect(patched).not.toContain('executor = FullstackWorktreeExecutor()')
    expect(patched).not.toContain('= PrecommitVerifier().execute(')
    expect(patched).not.toContain('manager = TaskManager()')
  })

  it('includes the release gate, version and documentation contract in the vendored core', () => {
    const source = temporary()
    mkdirSync(join(source, 'app'), { recursive: true })
    mkdirSync(join(source, 'tools'), { recursive: true })
    mkdirSync(join(source, 'scripts'), { recursive: true })
    mkdirSync(join(source, 'docs', 'superpowers', 'specs'), { recursive: true })
    mkdirSync(join(source, '.github', 'workflows'), { recursive: true })
    writeFileSync(join(source, 'app', 'core.py'), 'print("ok")\n')
    writeFileSync(join(source, 'app', 'external_task_session.py'), 'class ExternalTaskSession: pass\n')
    writeFileSync(join(source, 'tools', 'harness_host_server.py'), 'entry = 1\n')
    writeFileSync(join(source, 'scripts', 'verify.sh'), '#!/bin/sh\nexit 0\n')
    writeFileSync(join(source, 'docs', 'superpowers', 'specs', 'design.md'), '# Design\n')
    writeFileSync(join(source, 'requirements.txt'), 'cryptography>=42\n')
    writeFileSync(join(source, 'VERSION'), '0.66.0\n')
    writeFileSync(join(source, 'CHANGELOG.md'), '# Changelog\n')
    writeFileSync(join(source, 'real_precommit_trial_template.md'), '# Trial\n')
    writeFileSync(join(source, 'scope_warning_policy.md'), '# Scope policy\n')
    writeFileSync(
      join(source, '.github', 'workflows', 'enterprise-core.yml'),
      'run: python tools/build_release_bundle.py --output-dir dist\n',
    )

    const target = temporary()
    copyHarnessCore(source, target)

    expect(readFileSync(join(target, 'VERSION'), 'utf8')).toBe('0.66.0\n')
    expect(readFileSync(join(target, 'scripts', 'verify.sh'), 'utf8')).toContain('exit 0')
    expect(readFileSync(join(target, 'docs', 'superpowers', 'specs', 'design.md'), 'utf8')).toContain('Design')
    expect(readFileSync(join(target, 'CHANGELOG.md'), 'utf8')).toContain('Changelog')
    expect(readFileSync(join(target, 'real_precommit_trial_template.md'), 'utf8')).toContain('Trial')
    expect(readFileSync(join(target, 'scope_warning_policy.md'), 'utf8')).toContain('Scope policy')
    expect(readFileSync(join(target, '.github', 'workflows', 'enterprise-core.yml'), 'utf8')).toContain('build_release_bundle.py')
    expect(HARNESS_CORE_VENDOR_DIRS).toEqual(expect.arrayContaining(['scripts', 'docs', '.github']))
    expect(HARNESS_CORE_VENDOR_FILES).toEqual(expect.arrayContaining(['VERSION', 'CHANGELOG.md']))
    expect(HARNESS_CORE_REQUIRED_PATHS).toEqual(expect.arrayContaining([
      'real_precommit_trial_template.md',
      'scope_warning_policy.md',
      '.github/workflows/enterprise-core.yml',
    ]))
    expect(() => verifyHarnessCoreLayout(target)).not.toThrow()

    rmSync(join(target, 'scripts', 'verify.sh'))
    expect(() => verifyHarnessCoreLayout(target)).toThrow(/scripts\/verify\.sh/)
  })

  it('keeps code and config but drops caches, virtual environments and runtime data', () => {
    const source = temporary()
    for (const name of ['app', 'tools']) {
      mkdirSync(join(source, name), { recursive: true })
      writeFileSync(join(source, name, 'core.py'), 'print("ok")\n')
    }
    mkdirSync(join(source, 'app', '__pycache__'), { recursive: true })
    writeFileSync(join(source, 'app', '__pycache__', 'core.pyc'), 'binary')
    mkdirSync(join(source, '.venv'), { recursive: true })
    writeFileSync(join(source, '.venv', 'bin'), 'not-a-dir')
    mkdirSync(join(source, 'data'), { recursive: true })
    writeFileSync(join(source, 'data', 'big.db'), 'x')
    mkdirSync(join(source, 'unrelated'), { recursive: true })
    writeFileSync(join(source, 'requirements.txt'), 'cryptography>=42\n')

    const target = temporary()
    const copied = copyHarnessCore(source, target)

    expect(copied.fileCount).toBe(3)
    expect(isVendorablePath('app/core.py')).toBe(true)
    expect(isVendorablePath('app/__pycache__/core.pyc')).toBe(false)
    expect(isVendorablePath('.venv/bin/python')).toBe(false)
    expect(isVendorablePath('data/big.db')).toBe(false)
    expect(HARNESS_CORE_VENDOR_DIRS).toContain('app')
  })

  it('preserves installed runtime directories across re-copies', () => {
    const source = temporary()
    mkdirSync(join(source, 'app'), { recursive: true })
    writeFileSync(join(source, 'app', 'core.py'), 'print("v1")\n')
    const target = temporary()
    copyHarnessCore(source, target)
    const runtime = join(target, 'runtime')
    mkdirSync(join(runtime, 'bin'), { recursive: true })
    writeFileSync(join(runtime, 'bin', 'python3'), 'kept')

    writeFileSync(join(source, 'app', 'core.py'), 'print("v2")\n')
    copyHarnessCore(source, target, { preserve: ['runtime'] })

    expect(readFileSync(join(target, 'app', 'core.py'), 'utf8')).toContain('v2')
    expect(existsSync(join(runtime, 'bin', 'python3'))).toBe(true)
  })

  it('refuses real credential assignments in data files but allows documented placeholders', () => {
    const target = temporary()
    mkdirSync(join(target, 'config'), { recursive: true })
    writeFileSync(
      join(target, 'config', 'template.json'),
      '{"api_key": "fill-model-api-key-ref"}\n',
    )
    expect(verifyVendorNoSecrets(target)).toEqual([])

    writeFileSync(
      join(target, 'config', 'leaked.json'),
      '{"password": "hunter2hunter2hunter2"}\n',
    )
    expect(() => verifyVendorNoSecrets(target)).toThrow(/凭证样式/)
    expect(isSecretAssignment('{"password": "hunter2hunter2hunter2"}')).toBe(true)
    expect(isSecretAssignment('{"api_key": "fill-model-api-key-ref"}')).toBe(false)
    expect(isSecretAssignment('{"note": "不要在配置里写 token"}')).toBe(false)
  })

  it('auto-syncs vendor from a local Harness source without any manual command', () => {
    const repositoryRoot = temporary()
    const source = temporary()
    const originalCiFlag = process.env.CI
    try {
      // 该用例验证本机开发态同步；CI 行为由下一个用例单独覆盖。
      delete process.env.CI
      for (const name of ['app', 'tools']) mkdirSync(join(source, name), { recursive: true })
      writeFileSync(join(source, 'app', 'core.py'), 'print("v1")\n')
      writeFileSync(join(source, 'app', 'external_task_session.py'), 'class ExternalTaskSession: pass\n')
      mkdirSync(join(source, 'tools'), { recursive: true })
      writeFileSync(join(source, 'tools', 'harness_host_server.py'), 'entry = 1\n')
      mkdirSync(join(source, 'scripts'), { recursive: true })
      mkdirSync(join(source, 'docs'), { recursive: true })
      mkdirSync(join(source, '.github', 'workflows'), { recursive: true })
      writeFileSync(join(source, 'scripts', 'verify.sh'), '#!/bin/sh\nexit 0\n')
      writeFileSync(join(source, 'docs', 'README.md'), '# Docs\n')
      writeFileSync(join(source, 'requirements.txt'), 'cryptography>=42\n')
      writeFileSync(join(source, 'VERSION'), '0.66.0\n')
      writeFileSync(join(source, 'CHANGELOG.md'), '# Changelog\n')
      writeFileSync(join(source, 'real_precommit_trial_template.md'), '# Trial\n')
      writeFileSync(join(source, 'scope_warning_policy.md'), '# Scope policy\n')
      writeFileSync(
        join(source, '.github', 'workflows', 'enterprise-core.yml'),
        'run: python tools/build_release_bundle.py --output-dir dist\n',
      )

      const first = syncVendorFromSource(repositoryRoot, { source })
      expect(first.synced).toBe(true)
      expect(first.changed).toBe(true)
      const vendor = join(repositoryRoot, 'vendor', 'harness-core')
      expect(readFileSync(join(vendor, 'app', 'core.py'), 'utf8')).toContain('v1')

      // 源码未变：再次同步报告一致；源码变了：自动带入。
      const second = syncVendorFromSource(repositoryRoot, { source })
      expect(second.synced).toBe(true)
      expect(second.changed).toBe(false)
      writeFileSync(join(source, 'app', 'core.py'), 'print("v2")\n')
      const third = syncVendorFromSource(repositoryRoot, { source })
      expect(third.synced).toBe(true)
      expect(third.changed).toBe(true)
      expect(readFileSync(join(vendor, 'app', 'core.py'), 'utf8')).toContain('v2')
      // 记录的源路径成为后续解析的默认来源（无需再次显式传参）
      expect(resolveHarnessCoreSource(repositoryRoot)).toBe(source)
    } finally {
      if (originalCiFlag === undefined) delete process.env.CI
      else process.env.CI = originalCiFlag
    }
  })

  it('skips auto-sync in CI, without a source, or when the source is the vendor itself', () => {
    const repositoryRoot = temporary()
    const originalCiFlag = process.env.CI
    try {
      process.env.CI = 'true'
      expect(syncVendorFromSource(repositoryRoot).synced).toBe(false)
      delete process.env.CI
      expect(syncVendorFromSource(repositoryRoot, { source: '/definitely-not-a-harness-source' }).reason).toBe('source-unavailable')
      const vendor = join(repositoryRoot, 'vendor', 'harness-core')
      mkdirSync(join(vendor, 'app'), { recursive: true })
      mkdirSync(join(vendor, 'tools'), { recursive: true })
      writeFileSync(join(vendor, 'tools', 'harness_host_server.py'), 'x = 1\n')
      writeFileSync(join(vendor, 'requirements.txt'), '')
      expect(syncVendorFromSource(repositoryRoot, { source: vendor }).reason).toBe('source-is-vendor')
    } finally {
      if (originalCiFlag === undefined) delete process.env.CI
      else process.env.CI = originalCiFlag
    }
  })

  it('preserves the upstream fail-closed retirement of the legacy PostgreSQL adapter', () => {
    const target = temporary()
    const app = join(target, 'app')
    mkdirSync(app, { recursive: true })
    const adapter = join(app, 'pg_evidence.py')
    writeFileSync(adapter, [
      'LEGACY_PG_EVIDENCE_DISABLED = True',
      'LEGACY_PG_EVIDENCE_ERROR_CODE = (',
      '    "LEGACY_PG_EVIDENCE_DISABLED_USE_DATABASE_INSPECT_MCP"',
      ')',
      '',
      'def require_database_inspect_mcp() -> None:',
      '    raise LegacyPgEvidenceDisabled(LEGACY_PG_EVIDENCE_ERROR_CODE)',
      '',
    ].join('\n'))

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([])
    const preserved = readFileSync(adapter, 'utf8')
    expect(preserved).toContain('LEGACY_PG_EVIDENCE_DISABLED = True')
    expect(preserved).not.toContain('CapabilityRegistry')
  })

  it('retires stale direct PostgreSQL tests when the upstream adapter is MCP-only', () => {
    const target = temporary()
    const app = join(target, 'app')
    const tests = join(target, 'tests')
    mkdirSync(app, { recursive: true })
    mkdirSync(tests, { recursive: true })
    writeFileSync(join(app, 'pg_evidence.py'), [
      'LEGACY_PG_EVIDENCE_DISABLED = True',
      'LEGACY_PG_EVIDENCE_ERROR_CODE = (',
      '    "LEGACY_PG_EVIDENCE_DISABLED_USE_DATABASE_INSPECT_MCP"',
      ')',
      '',
      'def require_database_inspect_mcp() -> None:',
      '    raise LegacyPgEvidenceDisabled(LEGACY_PG_EVIDENCE_ERROR_CODE)',
      '',
    ].join('\n'))
    const staleTest = join(tests, 'test_pg_evidence.py')
    writeFileSync(staleTest, [
      'from app.pg_evidence import (',
      '    DEFAULT_SENSITIVE_COLUMN_PATTERNS,',
      '    PgEvidenceRequest,',
      ')',
      '',
      'class PgEvidenceProfileTests(unittest.TestCase):',
      '    pass',
      '',
    ].join('\n'))

    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'retire-stale-direct-pg-evidence-tests',
    ])
    const patched = readFileSync(staleTest, 'utf8')
    expect(patched).toContain('class PgEvidenceRetirementTests')
    expect(patched).toContain('LEGACY_PG_EVIDENCE_DISABLED_USE_DATABASE_INSPECT_MCP')
    expect(patched).not.toContain('DEFAULT_SENSITIVE_COLUMN_PATTERNS')
    expect(patched).not.toContain('from app.pg_evidence import')
    expect(applyHarnessCoreCompatibilityPatches(target)).toEqual([
      'retire-stale-direct-pg-evidence-tests',
    ])
  })

  it('keeps the checked-in vendor manifest consistent with the packaged source set', () => {
    const vendor = join(process.cwd(), 'vendor', 'harness-core')
    const manifest = JSON.parse(readFileSync(join(vendor, VENDOR_MANIFEST_NAME), 'utf8'))

    expect(summarizeHarnessCore(vendor)).toEqual({
      fileCount: manifest.fileCount,
      totalBytes: manifest.totalBytes,
      manifestSha256: manifest.manifestSha256,
    })
  })
})
