from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.requirement_calibration import normalize_business_risk_text, remove_negated_scope_clauses


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "projects.json"

ACTION_LEVELS = ["read", "test", "write", "git", "ci", "deploy"]
DEFAULT_ALLOWED_ACTION = "read"
DISABLED_ACTIONS = ["test", "write", "git", "ci", "deploy"]

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "dist",
    "build",
    "target",
    ".venv",
    "venv",
    "data",
    "runs",
    "self_check_runs",
}

DEFAULT_SENSITIVE_KEYWORDS = [
    "医保",
    "结算",
    "收费",
    "报表",
    "对账",
    "政策",
    "发票",
    "基金支付",
    "统筹支付",
    "自费金额",
    "回写",
    "冲正",
]

MEDIUM_RISK_KEYWORDS = ["接口", "流程", "权限", "事务", "兼容", "日志", "异常", "审核", "状态"]
LOW_RISK_KEYWORDS = ["字段", "展示", "样式", "文案", "提示", "列表", "页面", "界面", "页签", "标签页", "刷新"]
PREFERENTIAL_RISK_KEYWORDS = ["优惠", "优惠项目", "优惠类别", "优惠明细", "减免", "折扣", "费用配置", "费用项目"]
FINANCIAL_HIGH_RISK_KEYWORDS = ["医保", "结算", "收费", "报表", "对账", "核算", "发票", "基金", "统筹"]
BUSINESS_TRIGGER_TERMS = ["优惠", "限时", "不限时", "有效期", "生效", "失效", "时间"]
BUSINESS_SYNONYMS = {
    "优惠项目": [
        "优惠项目",
        "优惠",
        "优惠类别",
        "优惠明细",
        "优惠比例",
        "优惠金额",
        "youHui",
        "youhui",
        "YouHui",
        "youHuiXm",
        "youHuiLb",
        "youHuiJe",
        "youHuiBl",
        "discount",
    ],
    "优惠": ["优惠", "youHui", "youhui", "YouHui", "youHuiLb", "youHuiXm", "youHuiJe", "youHuiBl", "discount"],
    "不限时": [
        "不限时",
        "限时",
        "有效期",
        "有效期限",
        "生效时间",
        "失效时间",
        "开始时间",
        "结束时间",
        "startTime",
        "endTime",
        "validTime",
        "youXiao",
    ],
    "限时": ["限时", "时间", "有效期", "生效时间", "失效时间", "startTime", "endTime", "validTime"],
    "添加": ["添加", "新增", "保存"],
}

TEXT_EXTENSIONS = {
    ".py",
    ".java",
    ".kt",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".vue",
    ".html",
    ".css",
    ".scss",
    ".less",
    ".json",
    ".xml",
    ".graphql",
    ".graphqls",
    ".yml",
    ".yaml",
    ".properties",
    ".sql",
    ".md",
    ".txt",
}


@dataclass
class ProjectProfile:
    key: str
    name: str
    repo_path: str
    frontend_dirs: list[str] = field(default_factory=list)
    backend_dirs: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)
    build_commands: list[str] = field(default_factory=list)
    sensitive_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_SENSITIVE_KEYWORDS))
    exclude_dirs: list[str] = field(default_factory=list)
    max_files: int = 2500
    max_file_bytes: int = 180_000
    max_snippet_chars: int = 220

    @classmethod
    def from_path(cls, repo_path: str | Path, *, key: str = "manual_project", name: str = "手工指定项目") -> "ProjectProfile":
        return cls(key=key, name=name, repo_path=str(Path(repo_path).expanduser()))


@dataclass
class EvidenceBundle:
    evidence_id: str
    project: dict
    action_policy: dict
    risk: dict
    scan: dict
    impact: dict
    evidence_files: list[dict]
    suggested_commands: list[str]
    human_confirmations: list[str]
    acceptance_checklist: list[str]
    unknowns: list[str]
    review: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        lines = [
            "## 只读工程证据包",
            "",
            f"- Evidence ID：{self.evidence_id}",
            f"- 项目：{self.project.get('name')} ({self.project.get('key')})",
            f"- 路径：{self.project.get('repo_path')}",
            f"- 动作权限：仅允许 {self.action_policy.get('allowed')}；禁用 {', '.join(self.action_policy.get('disabled', []))}",
            f"- 风险等级：{self.risk.get('level')}",
            f"- 风险原因：{'; '.join(self.risk.get('reasons', [])) or '-'}",
            f"- 扫描文件数：{self.scan.get('scanned_files')} / 跳过：{self.scan.get('skipped_files')}",
            f"- Git 状态：{self.scan.get('git_status_summary') or '未检测到 Git 状态'}",
        ]
        business_keywords = self.scan.get("business_keywords", [])
        if business_keywords:
            lines.append(f"- 业务定位关键词：{', '.join(business_keywords[:24])}")

        if self.review:
            lines.extend(["", "### 已提交 Diff 审查证据", ""])
            lines.append(f"- Review ID：{self.review.get('review_id') or '-'}")
            lines.append(f"- 模式：{self.review.get('mode') or 'review-worktree'}")
            commit = self.review.get("review_commit", {})
            base = self.review.get("review_base", {})
            lines.append(f"- Commit：{commit.get('sha') or commit.get('input') or '-'}")
            if commit.get("subject"):
                lines.append(f"- Commit 标题：{commit.get('subject')}")
            lines.append(f"- Base：{base.get('sha') or base.get('input') or '-'}")
            changed_paths = self.review.get("changed_paths", [])
            allowed_paths = self.review.get("allowed_paths", [])
            lines.append(f"- 变更文件：{', '.join(changed_paths) if changed_paths else '-'}")
            lines.append(f"- 允许审查路径：{', '.join(allowed_paths) if allowed_paths else '-'}")
            diff_stat = str(self.review.get("diff_stat") or "").strip()
            if diff_stat:
                lines.extend(["", "```text", diff_stat[:2000], "```"])
            diff_excerpt = str(self.review.get("diff_excerpt") or "").strip()
            if diff_excerpt:
                lines.extend(["", "#### Diff 摘要", "", "```diff", diff_excerpt[:8000], "```"])

        lines.extend(["", "### 疑似影响范围", ""])
        for category, files in self.impact.get("categories", {}).items():
            if files:
                lines.append(f"- {category}：{', '.join(files[:8])}")
        if not any(self.impact.get("categories", {}).values()):
            lines.append("- 暂未命中明确文件，只能基于目录和需求文本做保守判断。")

        lines.extend(["", "### 命中证据", ""])
        if not self.evidence_files:
            lines.append("- 未读取到直接命中片段。")
        for item in self.evidence_files[:12]:
            keywords = ", ".join(item.get("matched_keywords", [])) or "-"
            lines.append(f"- `{item['path']}` [{item['category']}]，关键词：{keywords}")
            for snippet in item.get("snippets", [])[:2]:
                lines.append(f"  - {snippet}")

        lines.extend(["", "### 建议验证命令", ""])
        if self.suggested_commands:
            lines.extend(f"- `{command}`" for command in self.suggested_commands)
        else:
            lines.append("- 未识别到可建议的本地验证命令。")

        lines.extend(["", "### 必须人工确认项", ""])
        lines.extend(f"- {item}" for item in self.human_confirmations)

        lines.extend(["", "### 测试验收清单", ""])
        lines.extend(f"- {item}" for item in self.acceptance_checklist)

        lines.extend(["", "### 不可自动判断项", ""])
        lines.extend(f"- {item}" for item in self.unknowns)
        return "\n".join(lines)

    def to_prompt_context(self, *, limit: int = 9000) -> str:
        markdown = self.to_markdown()
        if len(markdown) <= limit:
            return markdown
        head = markdown[: limit // 2]
        tail = markdown[-limit // 2 :]
        return f"{head}\n\n...（工程证据包过长，已压缩，仅保留摘要和关键命中）...\n\n{tail}"


def load_project_profile(
    *,
    project_key: str | None = None,
    project_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> ProjectProfile | None:
    if project_path:
        return ProjectProfile.from_path(project_path, key=project_key or "manual_project")
    if not project_key:
        return None

    path = Path(config_path).expanduser() if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"项目画像配置不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    projects = data.get("projects", [])
    for item in projects:
        if item.get("key") == project_key:
            if not item.get("repo_path"):
                raise ValueError(f"项目画像 {project_key} 缺少 repo_path")
            merged = {
                "sensitive_keywords": list(DEFAULT_SENSITIVE_KEYWORDS),
                "exclude_dirs": [],
                "frontend_dirs": [],
                "backend_dirs": [],
                "test_commands": [],
                "build_commands": [],
            }
            merged.update(item)
            merged["repo_path"] = str(Path(str(merged["repo_path"])).expanduser())
            return ProjectProfile(**merged)
    raise KeyError(f"项目画像不存在：{project_key}")


class ProjectContextScanner:
    def __init__(self, profile: ProjectProfile) -> None:
        self.profile = profile
        self.repo_path = Path(profile.repo_path).expanduser().resolve()
        self.exclude_dirs = set(DEFAULT_EXCLUDE_DIRS) | set(profile.exclude_dirs)
        self.sensitive_keywords = unique_keep_order(DEFAULT_SENSITIVE_KEYWORDS + profile.sensitive_keywords)

    def scan(self, *, demand_text: str) -> EvidenceBundle:
        if not self.repo_path.exists():
            raise FileNotFoundError(f"项目路径不存在：{self.repo_path}")
        if not self.repo_path.is_dir():
            raise NotADirectoryError(f"项目路径不是目录：{self.repo_path}")

        classified_demand_text = normalize_business_risk_text(demand_text)
        demand_keywords = extract_demand_keywords(classified_demand_text)
        demand_has_sensitive = any(keyword in classified_demand_text for keyword in self.sensitive_keywords)
        search_keywords = unique_keep_order(
            demand_keywords + MEDIUM_RISK_KEYWORDS + LOW_RISK_KEYWORDS + (self.sensitive_keywords if demand_has_sensitive else [])
        )
        files, skipped_files = self._collect_files()
        git_status = self._git_status()
        evidence_files: list[dict] = []
        keyword_hits: dict[str, int] = {}

        for path in files:
            rel = safe_relative(path, self.repo_path)
            category = categorize_path(rel, self.profile)
            item = self._inspect_file(path=path, rel=rel, category=category, keywords=search_keywords)
            include_as_relevant = bool(item["matched_keywords"])
            if not include_as_relevant:
                continue
            for keyword in item.get("matched_keywords", []):
                keyword_hits[keyword] = keyword_hits.get(keyword, 0) + 1
            evidence_files.append(item)

        evidence_files = sort_evidence_files(evidence_files)[:30]
        categories = build_categories_from_evidence(evidence_files)
        risk = classify_risk(demand_text=classified_demand_text, keyword_hits=keyword_hits)
        suggested_commands = suggest_commands(profile=self.profile, repo_path=self.repo_path, categories=categories)
        human_confirmations = build_human_confirmations(risk=risk, demand_text=classified_demand_text)
        acceptance_checklist = build_acceptance_checklist(categories=categories, risk=risk)
        unknowns = build_unknowns(evidence_files=evidence_files, risk=risk)
        evidence_id = build_evidence_id(
            project_key=self.profile.key,
            repo_path=str(self.repo_path),
            demand_text=demand_text,
            keyword_hits=keyword_hits,
            git_status=git_status,
        )

        return EvidenceBundle(
            evidence_id=evidence_id,
            project={
                "key": self.profile.key,
                "name": self.profile.name,
                "repo_path": str(self.repo_path),
            },
            action_policy={
                "allowed": DEFAULT_ALLOWED_ACTION,
                "disabled": list(DISABLED_ACTIONS),
                "levels": list(ACTION_LEVELS),
                "note": "默认只读工程分析；worktree/review-worktree 仅在显式开启时使用临时 Git worktree，不提交、不推送、不发布或写云效事务。",
            },
            risk=risk,
            scan={
                "scanned_files": len(files),
                "skipped_files": skipped_files,
                "business_keywords": demand_keywords[:80],
                "git_status_summary": git_status,
                "context_compression": "只读取文本文件的小片段；大文件、二进制和排除目录不会送入模型。",
            },
            impact={
                "categories": categories,
                "keyword_hits": keyword_hits,
            },
            evidence_files=evidence_files,
            suggested_commands=suggested_commands,
            human_confirmations=human_confirmations,
            acceptance_checklist=acceptance_checklist,
            unknowns=unknowns,
        )

    def _collect_files(self) -> tuple[list[Path], int]:
        collected: list[Path] = []
        skipped = 0
        for root, dirs, filenames in os.walk(self.repo_path):
            dirs[:] = [name for name in dirs if name not in self.exclude_dirs]
            root_path = Path(root)
            for filename in filenames:
                path = root_path / filename
                if path.suffix.lower() not in TEXT_EXTENSIONS:
                    skipped += 1
                    continue
                if is_noisy_generated_file(path):
                    skipped += 1
                    continue
                collected.append(path)
                if len(collected) >= self.profile.max_files:
                    return collected, skipped
        return collected, skipped

    def _git_status(self) -> str:
        result = run_readonly_command(["git", "status", "--short", "--branch"], cwd=self.repo_path)
        if result["returncode"] != 0:
            return "未检测到 Git 仓库或无法读取 git status"
        text = result["stdout"].strip()
        if not text:
            return "clean"
        lines = text.splitlines()
        return "; ".join(lines[:12])

    def _inspect_file(self, *, path: Path, rel: str, category: str, keywords: list[str]) -> dict:
        size = path.stat().st_size
        if size > self.profile.max_file_bytes:
            path_matches = [keyword for keyword in keywords if keyword_matches(text="", rel=rel, keyword=keyword)]
            return {
                "path": rel,
                "category": category,
                "size": size,
                "read_status": "skipped_large_file",
                "matched_keywords": unique_keep_order(path_matches),
                "snippets": [],
                "match_score": len(path_matches) * 3,
                "path_score": len(path_matches),
            }
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="gb18030")
            except UnicodeDecodeError:
                return {
                    "path": rel,
                    "category": category,
                    "size": size,
                    "read_status": "skipped_non_text",
                    "matched_keywords": [],
                    "snippets": [],
                }
        except OSError as exc:
            return {
                "path": rel,
                "category": category,
                "size": size,
                "read_status": f"read_failed:{exc}",
                "matched_keywords": [],
                "snippets": [],
            }
        matched = [keyword for keyword in keywords if keyword_matches(text=text, rel=rel, keyword=keyword)]
        snippets = [extract_snippet(text, keyword, self.profile.max_snippet_chars) for keyword in matched[:4]]
        path_score = sum(1 for keyword in matched if keyword_matches(text="", rel=rel, keyword=keyword))
        content_score = sum(1 for keyword in matched if keyword_matches(text=text, rel="", keyword=keyword))
        role_score = path_role_score(rel)
        return {
            "path": rel,
            "category": category,
            "size": size,
            "read_status": "read_snippet",
            "matched_keywords": unique_keep_order(matched),
            "snippets": [snippet for snippet in snippets if snippet],
            "match_score": path_score * 3 + content_score + role_score,
            "path_score": path_score,
            "role_score": role_score,
        }


def run_readonly_command(command: list[str], *, cwd: Path) -> dict:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}
    return {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def categorize_path(rel: str, profile: ProjectProfile) -> str:
    normalized = rel.replace("\\", "/")
    lower = normalized.lower()
    if any(lower.startswith(prefix.strip("/").lower() + "/") for prefix in profile.frontend_dirs if prefix):
        return "frontend"
    if any(lower.startswith(prefix.strip("/").lower() + "/") for prefix in profile.backend_dirs if prefix):
        return "backend"
    if any(part in lower for part in ["/test/", "/tests/", ".spec.", ".test."]):
        return "test"
    if lower.endswith((".md", ".txt")):
        return "docs"
    if lower.endswith("pom.xml"):
        return "config"
    if lower.endswith((".sql", ".xml")) or any(word in lower for word in ["mapper", "report", "baobiao", "statement"]):
        return "database_report"
    if lower.endswith((".vue", ".tsx", ".jsx", ".css", ".scss", ".less", ".html")) or any(
        word in lower for word in ["frontend", "web", "pages", "components", "views", "router", "apis", "api/"]
    ):
        return "frontend"
    if lower.endswith((".java", ".kt", ".py")) or any(word in lower for word in ["backend", "controller", "service", "dao"]):
        return "backend"
    if lower.endswith((".json", ".yml", ".yaml", ".properties")):
        return "config"
    return "unknown"


def classify_risk(*, demand_text: str, keyword_hits: dict[str, int]) -> dict:
    demand_text = normalize_business_risk_text(demand_text)
    reasons: list[str] = []
    sensitive_hits = [keyword for keyword in DEFAULT_SENSITIVE_KEYWORDS if keyword in demand_text]
    preferential_hits = [keyword for keyword in PREFERENTIAL_RISK_KEYWORDS if keyword in demand_text]
    financial_hits = [keyword for keyword in FINANCIAL_HIGH_RISK_KEYWORDS if keyword in demand_text]
    if {"医保", "结算"} & set(sensitive_hits) and any(word in demand_text for word in ["报表", "对账", "回写", "基金", "统筹", "收费"]):
        level = "critical"
        reasons.append("需求同时涉及医保/结算与报表、对账、回写或收费口径。")
    elif preferential_hits and financial_hits:
        level = "high"
        reasons.append(
            "需求同时涉及优惠/减免费用配置和收费、结算、报表、对账或核算上下文："
            + ", ".join(unique_keep_order(preferential_hits + financial_hits)[:10])
        )
    elif sensitive_hits:
        level = "high"
        reasons.append("需求命中高敏感 HIS 关键词：" + ", ".join(sensitive_hits[:8]))
    elif preferential_hits:
        level = "medium"
        reasons.append("需求涉及优惠、减免、折扣或费用配置，至少按中风险处理：" + ", ".join(preferential_hits[:8]))
    elif any(keyword in demand_text for keyword in MEDIUM_RISK_KEYWORDS):
        level = "medium"
        reasons.append("需求涉及接口、流程、权限、事务、兼容或异常路径。")
    elif any(keyword in demand_text for keyword in LOW_RISK_KEYWORDS):
        level = "low"
        reasons.append("需求主要为字段、展示、样式或文案调整。")
    else:
        level = "medium"
        reasons.append("需求风险信息不足，按中风险保守处理。")
    return {"level": level, "reasons": reasons}


def suggest_commands(*, profile: ProjectProfile, repo_path: Path, categories: dict[str, list[str]]) -> list[str]:
    commands = list(profile.test_commands)
    if categories.get("frontend"):
        frontend_dirs = profile.frontend_dirs or find_dirs_with_file(repo_path, "package.json")
        for directory in frontend_dirs[:2]:
            commands.append(f"cd {directory or '.'} && npm test")
            commands.append(f"cd {directory or '.'} && npm run build")
    if categories.get("backend"):
        backend_dirs = profile.backend_dirs or find_dirs_with_file(repo_path, "pom.xml")
        for directory in backend_dirs[:2]:
            commands.append(f"cd {directory or '.'} && mvn test")
    if categories.get("database_report"):
        commands.append("人工核对 SQL/报表口径，并使用真实样例数据验证。")
    commands.extend(profile.build_commands)
    return unique_keep_order(commands)[:10]


def build_human_confirmations(*, risk: dict, demand_text: str) -> list[str]:
    items = [
        "确认需求边界、角色权限、入口页面和验收样例。",
        "确认扫描命中的疑似模块是否确实属于本需求范围。",
    ]
    if risk.get("level") in {"high", "critical"}:
        items.extend(
            [
                "医保、结算、收费、报表、对账或政策口径必须由业务负责人确认。",
                "异常回写、失败补偿、历史数据兼容和人工核对流程必须明确。",
                "上线前需要人工复核测试证据，不允许自动提交或自动发布。",
            ]
        )
    if any(word in demand_text for word in PREFERENTIAL_RISK_KEYWORDS):
        items.append("确认优惠/减免项目的适用范围、有效期或不限时规则，以及是否影响收费、结算或核算口径。")
    if any(word in demand_text for word in ["接口", "兼容", "旧版本"]):
        items.append("确认接口入参、出参和旧版本客户端兼容策略。")
    return unique_keep_order(items)


def build_acceptance_checklist(*, categories: dict[str, list[str]], risk: dict) -> list[str]:
    items = ["需求描述中的核心场景可以被手工复现和验收。"]
    if categories.get("frontend"):
        items.append("前端覆盖 loading、空数据、错误态、权限态、字段展示和回归页面。")
    if categories.get("backend"):
        items.append("后端覆盖接口兼容、判空、异常处理、事务边界、日志和幂等。")
    if categories.get("database_report"):
        items.append("数据库/报表覆盖字段来源、SQL 口径、历史数据、性能和真实样例数据。")
    if risk.get("level") in {"high", "critical"}:
        items.append("高敏感需求必须保留人工确认记录和对账/回写异常验收证据。")
    return unique_keep_order(items)


def build_unknowns(*, evidence_files: list[dict], risk: dict) -> list[str]:
    items = [
        "只读扫描不能证明运行时配置、数据库真实数据和外部接口状态。",
        "未执行测试、构建、Git、CI 或发布动作。",
    ]
    if not evidence_files:
        items.append("未找到直接命中需求关键词的工程片段，不能给出确定改动文件结论。")
    if risk.get("level") in {"high", "critical"}:
        items.append("政策、医保中心、结算平台或院内对账规则不能由模型自行下结论。")
    return items


def extract_demand_keywords(text: str) -> list[str]:
    text = remove_negated_scope_clauses(text)
    candidates = DEFAULT_SENSITIVE_KEYWORDS + MEDIUM_RISK_KEYWORDS + LOW_RISK_KEYWORDS + PREFERENTIAL_RISK_KEYWORDS
    domain_terms = ["门诊", "住院", "处方", "医嘱", "护士", "医生", "患者", "药品", "库存", "病历", "发票"]
    matched_terms = [keyword for keyword in candidates + domain_terms if keyword in text]
    business_terms = extract_business_terms(text)
    return expand_business_keywords(unique_keep_order(matched_terms + business_terms))[:120]


def extract_business_terms(text: str) -> list[str]:
    terms: list[str] = []
    quoted_segments = re.findall(r"[《“”\"']([^《》“”\"']{2,50})[》“”\"']", text)
    text_segments = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,40}", text)
    for segment in quoted_segments + text_segments:
        if not any(trigger in segment for trigger in BUSINESS_TRIGGER_TERMS):
            continue
        terms.extend(extract_terms_from_segment(segment))
    return unique_keep_order(terms)


def extract_terms_from_segment(segment: str) -> list[str]:
    terms: list[str] = []
    if "优惠项目" in segment:
        terms.append("优惠项目")
    if "优惠" in segment:
        terms.append("优惠")
    if "不限时" in segment:
        terms.extend(["不限时", "限时"])
    elif "限时" in segment:
        terms.append("限时")
    if "有效期" in segment or "有效" in segment:
        terms.extend(["有效期", "有效时间"])
    if "生效" in segment:
        terms.append("生效时间")
    if "失效" in segment:
        terms.append("失效时间")
    if "添加" in segment:
        terms.append("添加")
    for pattern in [r"[\u4e00-\u9fff]{1,8}项目", r"[\u4e00-\u9fff]{1,8}类别", r"[\u4e00-\u9fff]{1,8}界面"]:
        terms.extend(re.findall(pattern, segment))
    return unique_keep_order(terms)


def expand_business_keywords(terms: list[str]) -> list[str]:
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        for key, aliases in BUSINESS_SYNONYMS.items():
            if key in term or term in key:
                expanded.extend(aliases)
    return unique_keep_order(expanded)


def infer_relevant_categories(text: str) -> set[str]:
    text = remove_negated_scope_clauses(text)
    categories: set[str] = set()
    if any(keyword in text for keyword in ["页面", "界面", "列表", "字段", "展示", "样式", "文案", "提示", "前端"]):
        categories.add("frontend")
    if any(keyword in text for keyword in ["接口", "流程", "状态", "事务", "兼容", "日志", "异常", "审核", "后端"]):
        categories.add("backend")
    if any(keyword in text for keyword in ["数据库", "SQL", "报表", "统计", "字段来源", "口径", "对账"]):
        categories.add("database_report")
    if any(keyword in text for keyword in ["测试", "验收", "回归"]):
        categories.add("test")
    if any(keyword in text for keyword in DEFAULT_SENSITIVE_KEYWORDS):
        categories.update({"backend", "database_report"})
    if any(keyword in text for keyword in BUSINESS_TRIGGER_TERMS):
        categories.update({"frontend", "backend"})
    if any(keyword in text for keyword in PREFERENTIAL_RISK_KEYWORDS + FINANCIAL_HIGH_RISK_KEYWORDS):
        categories.update({"frontend", "backend", "database_report"})
    if not categories:
        categories.update({"frontend", "backend"})
    return categories


def extract_snippet(text: str, keyword: str, limit: int) -> str:
    index = find_keyword_index(text, keyword)
    if index == -1:
        return ""
    start = max(0, index - limit // 2)
    end = min(len(text), index + len(keyword) + limit // 2)
    snippet = text[start:end].replace("\n", " ").strip()
    return snippet


def find_keyword_index(text: str, keyword: str) -> int:
    index = text.find(keyword)
    if index != -1:
        return index
    return text.lower().find(keyword.lower())


def keyword_matches(*, text: str, rel: str, keyword: str) -> bool:
    if not keyword:
        return False
    return find_keyword_index(text, keyword) != -1 or find_keyword_index(rel, keyword) != -1


def is_noisy_generated_file(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith((".min.css", ".min.js", ".map"))


def path_role_score(rel: str) -> int:
    lower = rel.replace("\\", "/").lower()
    if "/router/" in lower or lower.startswith("src/router/"):
        return 8
    if "/apis/" in lower or "/api/" in lower or lower.startswith("src/apis/"):
        return 6
    if "/pages/" in lower or "/views/" in lower or lower.startswith("src/pages/"):
        return 4
    if "/store/" in lower or lower.startswith("src/store/"):
        return 3
    if "/components/" in lower or lower.startswith("src/components/"):
        return 2
    return 0


def find_dirs_with_file(repo_path: Path, filename: str) -> list[str]:
    matches: list[str] = []
    for path in repo_path.rglob(filename):
        if any(part in DEFAULT_EXCLUDE_DIRS for part in path.parts):
            continue
        matches.append(safe_relative(path.parent, repo_path))
        if len(matches) >= 3:
            break
    return matches


def build_categories_from_evidence(evidence_files: list[dict]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {
        "frontend": [],
        "backend": [],
        "database_report": [],
        "config": [],
        "test": [],
        "docs": [],
        "unknown": [],
    }
    for item in evidence_files:
        category = item.get("category", "unknown")
        if category not in categories:
            category = "unknown"
        path = item.get("path", "")
        if path and path not in categories[category]:
            categories[category].append(path)
    return {key: values[:30] for key, values in categories.items()}


def sort_evidence_files(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda item: (
            0 if item.get("matched_keywords") else 1,
            -int(item.get("match_score", 0)),
            -int(item.get("path_score", 0)),
            category_rank(item.get("category", "")),
            item.get("path", ""),
        ),
    )


def category_rank(category: str) -> int:
    order = {"database_report": 0, "backend": 1, "frontend": 2, "test": 3, "config": 4, "docs": 5}
    return order.get(category, 9)


def build_evidence_id(*, project_key: str, repo_path: str, demand_text: str, keyword_hits: dict[str, int], git_status: str) -> str:
    raw = json.dumps(
        {
            "project_key": project_key,
            "repo_path": repo_path,
            "demand": demand_text,
            "keyword_hits": keyword_hits,
            "git_status": git_status,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return "ev-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
