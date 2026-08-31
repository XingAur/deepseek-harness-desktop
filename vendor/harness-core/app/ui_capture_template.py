from __future__ import annotations

import json
from pathlib import Path


UI_CAPTURE_TEMPLATE_VERSION = "0.10.3C"


def write_playwright_capture_template(
    *,
    output_dir: str | Path,
    entity_id: str = "",
    title: str = "",
    route: str = "",
    scenario_name: str = "",
) -> dict:
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    script_path = target / "playwright_capture.mjs"
    env_example_path = target / "playwright_capture.env.example"
    manual_record_path = target / "manual_acceptance_record.md"
    readme_path = target / "README.md"
    script_path.write_text(
        build_playwright_capture_script(entity_id=entity_id, title=title, route=route, scenario_name=scenario_name),
        encoding="utf-8",
    )
    env_example_path.write_text(
        build_env_example(entity_id=entity_id, route=route),
        encoding="utf-8",
    )
    manual_record_path.write_text(
        build_manual_acceptance_record(entity_id=entity_id, title=title, route=route, scenario_name=scenario_name),
        encoding="utf-8",
    )
    readme_path.write_text(
        build_template_readme(entity_id=entity_id, title=title),
        encoding="utf-8",
    )
    return {
        "version": UI_CAPTURE_TEMPLATE_VERSION,
        "status": "pass",
        "summary": "已生成 Playwright/Chrome UI 证据采集模板。",
        "output_dir": str(target),
        "script_path": str(script_path),
        "env_example_path": str(env_example_path),
        "manual_record_path": str(manual_record_path),
        "readme_path": str(readme_path),
        "recommended_command": f"node {script_path}",
    }


def build_playwright_capture_script(*, entity_id: str, title: str, route: str, scenario_name: str) -> str:
    default_entity = json.dumps(entity_id or "DFHIS-UNKNOWN", ensure_ascii=False)
    default_route = json.dumps(route or "/", ensure_ascii=False)
    default_scenario = json.dumps(scenario_name or title or "HIS UI capture", ensure_ascii=False)
    return f"""import fs from 'node:fs';
import path from 'node:path';
import {{ chromium }} from 'playwright';

const DEFAULT_ENTITY_ID = {default_entity};
const DEFAULT_ROUTE = {default_route};
const DEFAULT_SCENARIO = {default_scenario};

const evidenceDir = path.resolve(process.env.HARNESS_UI_EVIDENCE_DIR || path.join(process.cwd(), 'ui-evidence'));
fs.mkdirSync(evidenceDir, {{ recursive: true }});

const baseUrl = requiredEnv('HIS_UI_BASE_URL');
const route = process.env.HIS_UI_ROUTE || DEFAULT_ROUTE;
const storageState = process.env.HIS_UI_STORAGE_STATE || '';
const entityId = process.env.HIS_UI_ENTITY_ID || DEFAULT_ENTITY_ID;
const scenarioName = process.env.HIS_UI_SCENARIO || DEFAULT_SCENARIO;
const timeoutMs = Number(process.env.HIS_UI_TIMEOUT_MS || 60000);
const waitUntil = process.env.HIS_UI_WAIT_UNTIL || 'networkidle';
const headed = process.env.HIS_UI_HEADED === '1';

  const browser = await chromium.launch({{ headless: !headed }});
try {{
  const contextOptions = {{}};
  if (storageState) {{
    const storageStatePath = path.resolve(storageState);
    if (!fs.existsSync(storageStatePath)) {{
      throw new Error(`HIS_UI_STORAGE_STATE file not found: ${{storageStatePath}}`);
    }}
    contextOptions.storageState = storageStatePath;
  }}
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  const targetUrl = new URL(route, baseUrl).toString();
  await page.goto(targetUrl, {{ waitUntil, timeout: timeoutMs }});
  if (process.env.HIS_UI_READY_SELECTOR) {{
    await page.waitForSelector(process.env.HIS_UI_READY_SELECTOR, {{ timeout: timeoutMs }});
  }}

  const safeEntity = safeFileName(entityId);
  const screenshotFile = `${{safeEntity}}-ui-capture.png`;
  const stateFile = `${{safeEntity}}-ui-state.json`;
  const screenshotPath = path.join(evidenceDir, screenshotFile);
  const statePath = path.join(evidenceDir, stateFile);
  await page.screenshot({{ path: screenshotPath, fullPage: true }});

  const dialogSelector = process.env.HIS_UI_DIALOG_SELECTOR || '.el-message-box__wrapper, .el-dialog__wrapper, [role=\"dialog\"]';
  const loadingSelector = process.env.HIS_UI_LOADING_SELECTOR || '.el-loading-mask';
  const progressSelector = process.env.HIS_UI_PROGRESS_SELECTOR || '';
  const dialogCount = await visibleCount(page, dialogSelector);
  const loadingCount = await visibleCount(page, loadingSelector);
  const progressCount = progressSelector ? await visibleCount(page, progressSelector) : null;
  const dialogTexts = await visibleTexts(page, dialogSelector);

  const state = {{
    entityId,
    scenarioName,
    url: page.url(),
    title: await page.title(),
    dialogSelector,
    dialogCount,
    dialogTexts,
    loadingSelector,
    loadingCount,
    progressSelector,
    progressCount,
    capturedAt: new Date().toISOString()
  }};
  fs.writeFileSync(statePath, JSON.stringify(state, null, 2), 'utf8');

  const assertions = [
    {{
      name: 'page_loaded',
      status: page.url() ? 'pass' : 'failed',
      evidence: `当前页面：${{page.url() || '-'}}`
    }}
  ];
  if (process.env.HIS_UI_EXPECT_DIALOG_COUNT) {{
    const expected = Number(process.env.HIS_UI_EXPECT_DIALOG_COUNT);
    assertions.push({{
      name: 'dialog_count',
      status: dialogCount === expected ? 'pass' : 'failed',
      evidence: `期望弹框数量=${{expected}}，实际=${{dialogCount}}`
    }});
  }} else {{
    assertions.push({{
      name: 'dialog_count_observed',
      status: 'pass',
      evidence: `已采集弹框数量=${{dialogCount}}`
    }});
  }}
  if (process.env.HIS_UI_EXPECT_NO_LOADING === '1') {{
    assertions.push({{
      name: 'loading_closed',
      status: loadingCount === 0 ? 'pass' : 'failed',
      evidence: `loading 可见数量=${{loadingCount}}`
    }});
  }}
  if (process.env.HIS_UI_EXPECT_PROGRESS_CLOSED === '1' && progressCount !== null) {{
    assertions.push({{
      name: 'progress_closed',
      status: progressCount === 0 ? 'pass' : 'failed',
      evidence: `进度弹框可见数量=${{progressCount}}`
    }});
  }}

  console.log(JSON.stringify({{
    "artifacts": [
      {{ path: screenshotFile, kind: 'screenshot', label: `${{scenarioName}} 截图` }},
      {{ path: stateFile, kind: 'manual_record', label: `${{scenarioName}} UI 状态 JSON` }}
    ],
    "assertions": assertions
  }}, null, 2));
}} finally {{
  await browser.close();
}}

function requiredEnv(name) {{
  const value = process.env[name];
  if (!value) {{
    throw new Error(`${{name}} is required. Set it in your shell or copy playwright_capture.env.example.`);
  }}
  return value;
}}

async function visibleCount(page, selector) {{
  return await page.locator(selector).evaluateAll(nodes => nodes.filter(node => {{
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  }}).length);
}}

async function visibleTexts(page, selector) {{
  return await page.locator(selector).evaluateAll(nodes => nodes
    .filter(node => {{
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    }})
    .map(node => node.textContent.trim())
    .filter(Boolean)
  );
}}

function safeFileName(value) {{
  return String(value || 'ui-capture').replace(/[^a-zA-Z0-9._-]+/g, '_').slice(0, 80);
}}
"""


def build_env_example(*, entity_id: str, route: str) -> str:
    return "\n".join(
        [
            "# Copy this file to a local .env or export these variables in your shell.",
            "# Do not commit real storageState files, cookies, passwords, or tokens.",
            "HIS_UI_BASE_URL=http://127.0.0.1:8080",
            f"HIS_UI_ROUTE={route or '/'}",
            f"HIS_UI_ENTITY_ID={entity_id or 'DFHIS-UNKNOWN'}",
            "HIS_UI_STORAGE_STATE=/absolute/path/to/playwright-storage-state.json",
            "HIS_UI_SCENARIO=HIS UI capture",
            "HIS_UI_READY_SELECTOR=#app",
            "HIS_UI_DIALOG_SELECTOR=.el-message-box__wrapper,.el-dialog__wrapper,[role=\"dialog\"]",
            "HIS_UI_LOADING_SELECTOR=.el-loading-mask",
            "HIS_UI_PROGRESS_SELECTOR=.settlement-progress-dialog",
            "HIS_UI_EXPECT_DIALOG_COUNT=0",
            "HIS_UI_EXPECT_NO_LOADING=1",
            "HIS_UI_EXPECT_PROGRESS_CLOSED=1",
            "HIS_UI_TIMEOUT_MS=60000",
            "HIS_UI_WAIT_UNTIL=networkidle",
            "HIS_UI_HEADED=0",
            "",
        ]
    )


def build_manual_acceptance_record(*, entity_id: str, title: str, route: str, scenario_name: str) -> str:
    lines = [
        f"# {entity_id or 'DFHIS-UNKNOWN'} UI 人工验收记录",
        "",
        f"- 标题：{title or '-'}",
        f"- 场景：{scenario_name or '-'}",
        f"- 页面路由：{route or '-'}",
        "- 登录态：使用 `HIS_UI_STORAGE_STATE` 指向的本地 Playwright storageState 文件。",
        "- 云效边界：本记录只作为本地证据，不写评论、不流转状态、不关闭任务。",
        "",
        "## 验收项",
        "",
        "- 页面能正常进入目标功能。",
        "- 弹框数量、弹框文案和截图与需求目标一致。",
        "- loading、进度条或结算进度详情按预期关闭。",
        "- 空数据、失败提示和关闭动作不触发重复提示或泛化错误文案。",
        "- 收费、结算、医保、退费状态边界未因 UI 关闭动作改变。",
        "",
        "## 记录",
        "",
        "- 验收人：",
        "- 验收时间：",
        "- 测试环境：",
        "- 测试数据：",
        "- 结论：",
        "- 残余风险：",
        "",
    ]
    return "\n".join(lines)


def build_template_readme(*, entity_id: str, title: str) -> str:
    return "\n".join(
        [
            f"# {entity_id or 'DFHIS'} Playwright UI Capture Template",
            "",
            f"- 需求：{title or '-'}",
            "- 先准备本地 HIS 前端服务和 Playwright storageState。",
            "- 根据 `playwright_capture.env.example` 设置环境变量。",
            "- 使用 v0.10.3B runner 时，把命令传给 `--ui-capture-command 'node <template-dir>/playwright_capture.mjs'`。",
            "- 脚本会向 stdout 输出 `artifacts` 和 `assertions` JSON，供 Harness 自动纳入 `ui_evidence_manifest`。",
            "- 不要提交真实 storageState、cookie、密码或 token。",
            "",
        ]
    )


def template_result_to_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)


def template_result_to_markdown(result: dict) -> str:
    return "\n".join(
        [
            "## v0.10.3C Playwright/Chrome UI 采集模板",
            "",
            f"- 状态：{result.get('status') or '-'}",
            f"- 结论：{result.get('summary') or '-'}",
            f"- 输出目录：{result.get('output_dir') or '-'}",
            f"- 脚本：{result.get('script_path') or '-'}",
            f"- 环境变量示例：{result.get('env_example_path') or '-'}",
            f"- 人工验收记录：{result.get('manual_record_path') or '-'}",
            "",
            "### 推荐命令",
            "",
            "```bash",
            str(result.get("recommended_command") or "-"),
            "```",
        ]
    )
