from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

from app.llm_client import is_high_risk_demand


EXPECTED_STEP_COUNT = 9
REQUIRED_TERMS = ["结论", "依据", "风险", "待确认", "下一步"]
EVIDENCE_TERMS = ["工程证据", "Evidence ID", "证据包", "只读扫描", "疑似影响", "Review ID", "提交 diff", "变更文件"]
HIGH_RISK_TERMS = ["高风险", "保守", "人工确认", "待确认"]
TEST_TERMS = ["测试", "验收", "回归"]
PROHIBITED_HIGH_RISK_TERMS = ["无需人工", "直接上线", "自动发布", "无需确认"]
NEGATION_TERMS = ["不", "未", "无", "不得", "不能", "禁止", "严禁", "不可", "不允许", "不建议", "拒绝", "阻断", "避免"]
DANGEROUS_INSTRUCTION_TERMS = [
    "不测试",
    "不用测试",
    "无需测试",
    "跳过测试",
    "不用验证",
    "无需验证",
    "没测直接",
    "直接流转",
    "自动流转",
    "直接关闭",
    "自动关闭",
    "直接上线",
    "自动发布",
]


@dataclass
class EvaluationIssue:
    step_order: int
    step_key: str
    severity: str
    message: str


@dataclass
class EvaluationResult:
    status: str
    summary: str
    first_bad_step_order: int | None = None
    issues: list[EvaluationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["issues"] = [asdict(issue) for issue in self.issues]
        return data


class Evaluator:
    def __init__(self, *, require_real_model: bool = True, expected_steps: int = EXPECTED_STEP_COUNT) -> None:
        self.require_real_model = require_real_model
        self.expected_steps = expected_steps

    def evaluate(
        self,
        *,
        demand_text: str,
        steps: list[dict],
        llm_mode: str,
        evidence_bundle: dict | None = None,
        acceptance_matrix: dict | None = None,
    ) -> EvaluationResult:
        issues: list[EvaluationIssue] = []

        if self.require_real_model and llm_mode == "mock":
            issues.append(
                EvaluationIssue(
                    step_order=0,
                    step_key="run",
                    severity="failed",
                    message="正式自测要求真实模型，当前为 mock 模式",
                )
            )

        if len(steps) < self.expected_steps:
            issues.append(
                EvaluationIssue(
                    step_order=len(steps) + 1,
                    step_key="workflow",
                    severity="needs_retry",
                    message=f"Workflow 未完整执行，期望 {self.expected_steps} 步，实际 {len(steps)} 步",
                )
            )

        seen_orders = {step["step_order"] for step in steps}
        for order in range(1, self.expected_steps + 1):
            if order not in seen_orders:
                issues.append(
                    EvaluationIssue(
                        step_order=order,
                        step_key="workflow",
                        severity="needs_retry",
                        message=f"缺少第 {order} 步专家输出",
                    )
                )

        high_risk = is_high_risk_demand(demand_text) or evidence_risk_level(evidence_bundle) in {"high", "critical"}
        issues.extend(self._evaluate_acceptance_matrix(demand_text=demand_text, acceptance_matrix=acceptance_matrix, high_risk=high_risk))
        for step in steps:
            issues.extend(
                self._evaluate_step(
                    step=step,
                    demand_text=demand_text,
                    high_risk=high_risk,
                    evidence_bundle=evidence_bundle,
                )
            )

        final_text = "\n".join(step.get("output_text", "") for step in steps if step.get("step_key") in {"test_plan", "final_review"})
        if final_text and not any(term in final_text for term in TEST_TERMS):
            issues.append(
                EvaluationIssue(
                    step_order=8,
                    step_key="test_plan",
                    severity="needs_retry",
                    message="最终测试验收信息不足，缺少测试/验收/回归口径",
                )
            )

        status = "pass"
        if any(issue.severity == "failed" for issue in issues):
            status = "failed"
        elif issues:
            status = "needs_retry"

        first_bad = min((issue.step_order for issue in issues if issue.step_order > 0), default=None)
        summary = self._build_summary(status=status, issues=issues)
        return EvaluationResult(status=status, summary=summary, first_bad_step_order=first_bad, issues=issues)

    def _evaluate_step(
        self,
        *,
        step: dict,
        demand_text: str,
        high_risk: bool,
        evidence_bundle: dict | None,
    ) -> list[EvaluationIssue]:
        issues: list[EvaluationIssue] = []
        text = step.get("output_text", "") or ""
        step_order = int(step.get("step_order", 0))
        step_key = str(step.get("step_key", ""))
        evidence_id = str((evidence_bundle or {}).get("evidence_id", ""))

        if step.get("status") != "success":
            issues.append(
                EvaluationIssue(
                    step_order=step_order,
                    step_key=step_key,
                    severity="needs_retry",
                    message=f"{step.get('step_name', step_key)} 执行失败：{step.get('error') or '未知错误'}",
                )
            )
            return issues

        if len(text.strip()) < 260:
            issues.append(
                EvaluationIssue(
                    step_order=step_order,
                    step_key=step_key,
                    severity="needs_retry",
                    message="专家输出过短，无法支撑审查",
                )
            )

        for term in REQUIRED_TERMS:
            if term not in text:
                issues.append(
                    EvaluationIssue(
                        step_order=step_order,
                        step_key=step_key,
                        severity="needs_retry",
                        message=f"专家输出缺少“{term}”部分",
                    )
                )

        if self._looks_generic(text=text, demand_text=demand_text):
            issues.append(
                EvaluationIssue(
                    step_order=step_order,
                    step_key=step_key,
                    severity="needs_retry",
                    message="专家输出过于泛化，缺少需求关键词或业务对象引用",
                )
            )

        if high_risk:
            if not any(term in text for term in HIGH_RISK_TERMS):
                issues.append(
                    EvaluationIssue(
                        step_order=step_order,
                        step_key=step_key,
                        severity="failed" if step_key in {"test_plan", "final_review"} else "needs_retry",
                        message="高敏感 HIS 需求缺少高风险/保守/人工确认表述",
                    )
                )
            if contains_prohibited_high_risk_claim(text):
                issues.append(
                    EvaluationIssue(
                        step_order=step_order,
                        step_key=step_key,
                        severity="failed",
                        message="高敏感需求出现不允许的直接上线或无需确认表述",
                    )
                )

        if evidence_bundle:
            if not (evidence_id and evidence_id in text) and not any(term in text for term in EVIDENCE_TERMS):
                issues.append(
                    EvaluationIssue(
                        step_order=step_order,
                        step_key=step_key,
                        severity="needs_retry",
                        message="专家输出缺少工程证据包引用，必须引用 Evidence ID、疑似模块或只读扫描依据",
                    )
                )
            if mentions_code_location(text) and not any(term in text for term in EVIDENCE_TERMS) and evidence_id not in text:
                issues.append(
                    EvaluationIssue(
                        step_order=step_order,
                        step_key=step_key,
                        severity="needs_retry",
                        message="专家输出给出了代码位置判断，但没有绑定工程证据",
                    )
                )
        elif mentions_code_location(text) and "不足以下结论" not in text and "补充项目上下文" not in text:
            issues.append(
                EvaluationIssue(
                    step_order=step_order,
                    step_key=step_key,
                    severity="needs_retry",
                    message="未提供项目证据时，不能给出确定代码文件结论",
                )
            )

        if step_key in {"test_plan", "final_review"} and not any(term in text for term in TEST_TERMS):
            issues.append(
                EvaluationIssue(
                    step_order=step_order,
                    step_key=step_key,
                    severity="needs_retry",
                    message="测试或最终评审缺少测试、验收或回归内容",
                )
            )

        return issues

    def _evaluate_acceptance_matrix(
        self,
        *,
        demand_text: str,
        acceptance_matrix: dict | None,
        high_risk: bool,
    ) -> list[EvaluationIssue]:
        if not acceptance_matrix:
            return []
        issues: list[EvaluationIssue] = []
        requirement_items = acceptance_matrix.get("requirement_acceptance") or []
        manual_items = acceptance_matrix.get("manual_acceptance") or []
        challenge_reviews = acceptance_matrix.get("challenge_reviews") or []
        decisions = acceptance_matrix.get("decisions") or {}

        if not requirement_items:
            issues.append(
                EvaluationIssue(
                    step_order=8,
                    step_key="acceptance_matrix",
                    severity="failed",
                    message="v0.8.7 验收矩阵缺少需求验收项",
                )
            )
        if not manual_items:
            issues.append(
                EvaluationIssue(
                    step_order=8,
                    step_key="acceptance_matrix",
                    severity="needs_retry",
                    message="v0.8.7 验收矩阵缺少人工验收项",
                )
            )
        if high_risk and not any("人工" in jsonish(item) or "高风险" in jsonish(item) for item in manual_items):
            issues.append(
                EvaluationIssue(
                    step_order=8,
                    step_key="acceptance_matrix",
                    severity="failed",
                    message="高风险需求验收矩阵缺少人工验收或高风险确认项",
                )
            )
        if dangerous_instruction_present(demand_text) and not challenge_reviews:
            issues.append(
                EvaluationIssue(
                    step_order=8,
                    step_key="acceptance_matrix",
                    severity="failed",
                    message="需求存在危险或不合理指令，但验收矩阵没有输出反驳/纠偏",
                )
            )
        yunxiao_transition = (decisions.get("can_yunxiao_transition") or {}).get("status")
        if yunxiao_transition and yunxiao_transition != "blocked":
            issues.append(
                EvaluationIssue(
                    step_order=8,
                    step_key="acceptance_matrix",
                    severity="failed",
                    message="v0.8.7 不允许真实云效状态流转，验收矩阵闸口必须保持 blocked",
                )
            )
        auto_commit = (decisions.get("can_auto_commit") or {}).get("status")
        if auto_commit and auto_commit != "blocked":
            issues.append(
                EvaluationIssue(
                    step_order=8,
                    step_key="acceptance_matrix",
                    severity="failed",
                    message="v0.8.7 不允许自动提交，验收矩阵提交闸口必须保持 blocked",
                )
            )
        return issues

    def _looks_generic(self, *, text: str, demand_text: str) -> bool:
        keywords = extract_keywords(demand_text)
        if not keywords:
            return False
        hits = sum(1 for keyword in keywords if keyword in text)
        return hits == 0

    def _build_summary(self, *, status: str, issues: Iterable[EvaluationIssue]) -> str:
        issue_list = list(issues)
        if status == "pass":
            return "自动审核通过：阶段完整、结构满足要求，报告可进入人工审查。"
        if status == "failed":
            return "自动审核失败：存在不可自动返工的高风险问题，需要人工介入。"
        return f"自动审核要求返工：发现 {len(issue_list)} 个问题，需从最早问题步骤开始重跑。"


def extract_keywords(text: str) -> list[str]:
    candidates: list[str] = []
    for token in [
        "医保",
        "结算",
        "收费",
        "报表",
        "对账",
        "发票",
        "库存",
        "门诊",
        "住院",
        "处方",
        "接口",
        "字段",
        "权限",
        "患者",
        "医生",
        "护士",
        "优惠",
        "优惠项目",
        "不限时",
        "限时",
        "有效期",
        "核算",
        "减免",
    ]:
        if token in text:
            candidates.append(token)
    return candidates[:6]


def evidence_risk_level(evidence_bundle: dict | None) -> str:
    if not evidence_bundle:
        return ""
    risk = evidence_bundle.get("risk")
    if not isinstance(risk, dict):
        return ""
    return str(risk.get("level", ""))


def mentions_code_location(text: str) -> bool:
    markers = [
        ".vue",
        ".java",
        ".py",
        ".js",
        ".ts",
        ".sql",
        ".xml",
        "src/",
        "components/",
        "controller",
        "service",
        "mapper",
        "dao",
    ]
    if any(marker in text for marker in markers):
        return True
    return bool(re.search(r"`[A-Za-z][A-Za-z0-9_./-]{3,}`", text))


def contains_prohibited_high_risk_claim(text: str) -> bool:
    for term in PROHIBITED_HIGH_RISK_TERMS:
        start = 0
        while True:
            index = text.find(term, start)
            if index == -1:
                break
            context = text[max(0, index - 24) : index] + text[index + len(term) : index + len(term) + 12]
            if not any(negation in context for negation in NEGATION_TERMS):
                return True
            start = index + len(term)
    return False


def dangerous_instruction_present(text: str) -> bool:
    return any(term in text for term in DANGEROUS_INSTRUCTION_TERMS)


def jsonish(value: object) -> str:
    return str(value)
