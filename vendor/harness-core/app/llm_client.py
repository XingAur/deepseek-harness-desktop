from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from socket import timeout as SocketTimeout

from app.runtime_policy import assert_runtime_mode_allowed


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    mode: str
    model: str


SECRET_ENV_KEYS = [
    "OPENAI_API_KEY",
    "openai_api_key",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ALIYUN_DEVOPS_PAT",
    "aliyun_devops_pat",
    "ALIYUN_DEVOPS_WRITE_PAT",
    "aliyun_devops_write_pat",
]

DEFAULT_CREDENTIALS_FILE = "/Users/lym/WorkCode/ai/apiKey/credentials.json"


class BaseLLMClient:
    mode = "base"
    model_name = ""
    is_mock = False

    def complete(self, *, system_prompt: str, user_prompt: str, step_key: str, expert_name: str) -> LLMResponse:
        raise NotImplementedError


class MockLLMClient(BaseLLMClient):
    mode = "mock"
    model_name = "mock-harness-local"
    is_mock = True

    def complete(self, *, system_prompt: str, user_prompt: str, step_key: str, expert_name: str) -> LLMResponse:
        prompt_tokens = max(1, len(system_prompt + user_prompt) // 4)
        content = build_mock_report(step_key=step_key, expert_name=expert_name, user_prompt=user_prompt)
        completion_tokens = max(1, len(content) // 4)
        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            mode=self.mode,
            model=self.model_name,
        )


class OpenAICompatibleClient(BaseLLMClient):
    mode = "openai"
    is_mock = False

    def __init__(self) -> None:
        assert_runtime_mode_allowed(self.mode)
        self.api_key = os.environ["OPENAI_API_KEY"]
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = os.environ.get("OPENAI_MODEL", "deepseek-v4-flash")
        self.timeout_seconds = llm_timeout_seconds()
        self.model_name = self.model

    def complete(self, *, system_prompt: str, user_prompt: str, step_key: str, expert_name: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Connection": "close",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {exc.code}: {redact_secrets(detail)}") from exc
        except (urllib.error.URLError, SocketTimeout, TimeoutError) as exc:
            raise RuntimeError(f"LLM request failed or timed out after {self.timeout_seconds}s: {redact_secrets(str(exc))}") from exc
        content = body["choices"][0]["message"]["content"]
        if not content or not content.strip():
            raise RuntimeError("LLM returned empty content")
        usage = body.get("usage", {})
        return LLMResponse(
            content=content,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            mode=self.mode,
            model=self.model,
        )


class AnthropicCompatibleClient(BaseLLMClient):
    mode = "anthropic"
    is_mock = False

    def __init__(self) -> None:
        assert_runtime_mode_allowed(self.mode)
        self.api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("anthropic mode requires ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY")
        self.base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic").rstrip("/")
        self.model = (
            os.environ.get("ANTHROPIC_MODEL")
            or os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL")
            or "glm-5.1"
        )
        try:
            self.max_tokens = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "4096"))
        except ValueError as exc:
            raise RuntimeError("ANTHROPIC_MAX_TOKENS must be an integer") from exc
        self.timeout_seconds = llm_timeout_seconds()
        self.model_name = self.model

    def complete(self, *, system_prompt: str, user_prompt: str, step_key: str, expert_name: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0.2,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_prompt},
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        endpoint = build_anthropic_messages_url(self.base_url)
        request = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
                "Connection": "close",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {exc.code}: {redact_secrets(detail)}") from exc
        except (urllib.error.URLError, SocketTimeout, TimeoutError) as exc:
            raise RuntimeError(f"LLM request failed or timed out after {self.timeout_seconds}s: {redact_secrets(str(exc))}") from exc

        content = extract_anthropic_text(body)
        if not content or not content.strip():
            raise RuntimeError("LLM returned empty content")
        usage = body.get("usage", {})
        return LLMResponse(
            content=content,
            prompt_tokens=int(usage.get("input_tokens", 0)),
            completion_tokens=int(usage.get("output_tokens", 0)),
            mode=self.mode,
            model=self.model,
        )


def get_llm_client(mode: str | None = None, *, allow_mock: bool = False) -> BaseLLMClient:
    selected = (mode or os.environ.get("HARNESS_LLM_MODE") or "mock").strip().lower()
    assert_runtime_mode_allowed(selected)
    if selected == "mock":
        if not allow_mock:
            raise RuntimeError("mock mode is only allowed for demo/development runs; pass allow_mock=True explicitly")
        return MockLLMClient()
    load_claude_settings_env_if_requested()
    load_local_llm_credentials_env_if_available()
    if selected in {"openai", "real"}:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("real model mode requires OPENAI_API_KEY")
        return OpenAICompatibleClient()
    if selected in {"anthropic", "claude", "zhipu"}:
        return AnthropicCompatibleClient()
    raise RuntimeError(f"unsupported HARNESS_LLM_MODE: {selected}")


def smoke_test(client: BaseLLMClient) -> LLMResponse:
    assert_runtime_mode_allowed(client.mode)
    return client.complete(
        system_prompt="你是模型连通性检查器。请严格按要求输出。",
        user_prompt="请只回复一行：SMOKE_OK",
        step_key="preflight",
        expert_name="模型预检",
    )


def is_smoke_response_ok(content: str) -> bool:
    return "SMOKE_OK" in (content or "")


def is_high_risk_demand(text: str) -> bool:
    return any(word in text for word in ["医保", "结算", "收费", "报表", "对账", "政策", "发票", "药品", "库存"])


def describe_mode(client: BaseLLMClient) -> str:
    if client.is_mock:
        return "mock（仅演示，不可用于业务判断）"
    return f"{client.mode}:{client.model_name}"


def llm_timeout_seconds() -> int:
    raw = os.environ.get("HARNESS_LLM_TIMEOUT_SECONDS", "45").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("HARNESS_LLM_TIMEOUT_SECONDS must be an integer") from exc
    if value < 5:
        raise RuntimeError("HARNESS_LLM_TIMEOUT_SECONDS must be at least 5")
    return value


def get_legacy_llm_client() -> BaseLLMClient:
    mode = os.environ.get("HARNESS_LLM_MODE", "mock").strip().lower()
    assert_runtime_mode_allowed(mode)
    if mode == "mock":
        return MockLLMClient()
    load_claude_settings_env_if_requested()
    load_local_llm_credentials_env_if_available()
    if mode == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("HARNESS_LLM_MODE=openai requires OPENAI_API_KEY")
        return OpenAICompatibleClient()
    if mode in {"anthropic", "claude", "zhipu"}:
        return AnthropicCompatibleClient()
    return MockLLMClient()


def extract_anthropic_text(body: dict) -> str:
    content = body.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and (item.get("type") in {None, "text"}):
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    return ""


def build_anthropic_messages_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1/messages") or normalized.endswith("/messages"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/messages"
    return f"{normalized}/v1/messages"


def redact_secrets(text: str) -> str:
    redacted = text
    for key in SECRET_ENV_KEYS:
        value = os.environ.get(key)
        if value and len(value) >= 8:
            redacted = redacted.replace(value, f"<redacted:{key}>")
    return redacted


def load_claude_settings_env_if_requested() -> list[str]:
    flag = os.environ.get("HARNESS_LOAD_CLAUDE_SETTINGS", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return []

    configured = os.environ.get("CLAUDE_SETTINGS_PATH")
    candidates = [Path(configured).expanduser()] if configured else [Path.home() / ".claude" / "settings.json"]
    allowed_keys = {
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_MAX_TOKENS",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    }
    loaded: list[str] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"failed to read Claude settings: {path}: {exc}") from exc
        env = data.get("env") if isinstance(data, dict) and isinstance(data.get("env"), dict) else {}
        for key in allowed_keys:
            value = env.get(key)
            if value and key not in os.environ:
                os.environ[key] = str(value)
                loaded.append(key)
        return sorted(loaded)
    return []


def load_local_llm_credentials_env_if_available() -> list[str]:
    """Load OpenAI-compatible model settings from the local credentials file.

    Environment variables remain authoritative. The credentials file may contain
    openai_api_key/openai_base_url/openai_model for DeepSeek or other
    OpenAI-compatible providers.
    """
    path = Path(os.environ.get("HARNESS_CREDENTIALS_FILE") or DEFAULT_CREDENTIALS_FILE).expanduser()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to read Harness credentials file: {path}: {exc}") from exc
    if not isinstance(data, dict):
        return []

    loaded: list[str] = []
    key = first_local_credential(
        data,
        [
            "openai_api_key",
            "OPENAI_API_KEY",
            "deepseek_api_key",
            "DEEPSEEK_API_KEY",
            # Backward compatibility for an existing local credentials typo.
            "deppseek_api_key",
        ],
    )
    if key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = key
        loaded.append("OPENAI_API_KEY")

    base_url = first_local_credential(data, ["openai_base_url", "OPENAI_BASE_URL", "deepseek_base_url", "DEEPSEEK_BASE_URL"])
    if not base_url and key:
        base_url = "https://api.deepseek.com/v1"
    if base_url and not os.environ.get("OPENAI_BASE_URL"):
        os.environ["OPENAI_BASE_URL"] = base_url.rstrip("/")
        loaded.append("OPENAI_BASE_URL")

    model = first_local_credential(data, ["openai_model", "OPENAI_MODEL", "deepseek_model", "DEEPSEEK_MODEL"])
    if not model and key:
        model = "deepseek-v4-flash"
    if model and not os.environ.get("OPENAI_MODEL"):
        os.environ["OPENAI_MODEL"] = model
        loaded.append("OPENAI_MODEL")
    return loaded


def first_local_credential(data: dict, keys: list[str]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def build_mock_report(*, step_key: str, expert_name: str, user_prompt: str) -> str:
    demand_excerpt = extract_between(user_prompt, "【原始需求】", "【上游专家输出】").strip()
    if not demand_excerpt:
        demand_excerpt = user_prompt[:500].strip()
    demand_excerpt = demand_excerpt.replace("\n", " ")[:220]
    evidence_excerpt = extract_between(user_prompt, "【只读工程证据包】", "【上游专家输出】").strip()
    evidence_id = extract_evidence_id(evidence_excerpt)
    evidence_line = (
        f"Evidence ID：{evidence_id}；已基于只读扫描证据包判断疑似影响模块，未执行测试、Git、CI 或发布。"
        if evidence_id
        else "未提供项目证据包；不足以下结论给出确定代码文件，需补充真实项目上下文。"
    )

    risk_line = "风险等级：高；必须人工确认，不得擅自推断医保、结算、收费、报表或政策口径。" if is_high_risk_demand(demand_excerpt) else "风险等级：中；按最小改动和明确验收口径推进。"
    templates = {
        "demand_analysis": [
            "业务目标：把输入需求转成可研发、可验证的需求条目。",
            "影响模块：需结合 HIS 菜单、接口、字段和权限进一步确认。",
            "待确认：触发条件、涉及角色、历史数据处理、验收样例。",
        ],
        "requirement_clarification_gate": [
            "是否建议继续：可以进入方案设计，但必须保留待确认清单。",
            "澄清重点：字段口径、异常路径、是否影响收费/医保/报表。",
            "默认假设：未明确内容不新增业务规则，只按最小改动推进。",
        ],
        "architecture_plan": [
            "前端：确认页面入口、字段展示、状态和错误提示。",
            "后端：确认接口契约、判空、事务、兼容和异常处理。",
            "数据库/报表：确认字段来源、SQL 口径和历史数据兼容。",
        ],
        "frontend_quality": [
            "检查 loading、空数据、错误态、权限禁用和字段一致性。",
            "避免只改展示不改接口适配，避免字段缺失导致运行时报错。",
            "建议补充关键页面回归用例。",
        ],
        "backend_quality": [
            "检查接口入参出参兼容、事务边界、幂等和异常路径。",
            "医保、结算、对账相关逻辑默认不扩展隐式规则。",
            "建议对 SQL 风险和历史数据兼容做专项验证。",
        ],
        "database_report_quality": [
            "检查字段来源、统计口径、SQL 性能、索引和报表展示一致性。",
            "历史数据不可默认重算，需明确补偿或兼容策略。",
            "建议提供一组真实样例数据验证口径。",
        ],
        "execution_plan": [
            "执行顺序：先确认需求边界，再改最小模块，最后补验证。",
            "第一版只输出执行计划，不生成补丁、不改业务代码。",
            "高风险点需要人工确认后再进入代码执行阶段。",
        ],
        "test_plan": [
            "测试覆盖：正常路径、异常路径、权限、边界值和回归范围。",
            "验收标准：需求字段、接口行为、数据落库/查询和报表口径一致。",
            "上线前检查：编译、关键接口、核心页面和日志异常。",
        ],
        "final_review": [
            "最终结论：可以作为研发输入，但高风险业务需人工确认。",
            "建议下一步：进入代码执行前补齐待确认问题和验收样例。",
            "残余风险：需求描述不足时，AI 方案只能作为辅助判断。",
        ],
    }
    bullets = templates.get(step_key, ["已完成本阶段分析。"])
    bullet_text = "\n".join(f"- {item}" for item in bullets)
    return (
        f"> MOCK 模式输出，仅用于演示流程，不可用于真实业务判断。\n\n"
        f"## {expert_name}报告\n\n"
        f"### 工程证据引用\n"
        f"- {evidence_line}\n\n"
        f"### 结论\n"
        f"本阶段已基于需求完成结构化分析。需求摘要：{demand_excerpt or '未提供明确摘要'}\n\n"
        f"### 事实依据\n"
        f"- 依据原始需求、上游专家输出和 Harness 只读工程证据；不补充证据外事实。\n\n"
        f"### 关键判断\n{bullet_text}\n\n"
        f"### 待确认\n"
        f"- 业务口径、字段来源、角色权限、验收样例需要人工确认。\n\n"
        f"### 风险与边界\n"
        f"- {risk_line}\n"
        f"- 不编造需求中未出现的业务规则。\n"
        f"- 涉及医保、结算、对账、报表时默认按高敏感逻辑处理。\n"
        f"- 第一版 Harness 只生成报告，不执行代码修改。\n\n"
        f"### 测试验收\n"
        f"- 覆盖正常路径、异常路径、权限、数据一致性和回归范围。\n\n"
        f"### 下一步输入\n"
        f"将本阶段结论交给下游专家继续审查和汇总。\n"
    )


def extract_between(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    if start_index == -1:
        return ""
    start_index += len(start)
    end_index = text.find(end, start_index)
    if end_index == -1:
        end_index = len(text)
    return text[start_index:end_index]


def extract_evidence_id(text: str) -> str:
    marker = "Evidence ID："
    index = text.find(marker)
    if index == -1:
        marker = "Evidence ID:"
        index = text.find(marker)
    if index == -1:
        return ""
    start = index + len(marker)
    tail = text[start:].strip()
    return tail.split()[0].strip("；;，,。")
