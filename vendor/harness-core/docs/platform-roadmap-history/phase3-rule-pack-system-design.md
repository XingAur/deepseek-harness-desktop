# 阶段3：多规则包支持设计与实现文档

## 📋 文档信息

| 项目 | 内容 |
|------|------|
| **阶段** | 阶段3：多规则包支持 |
| **版本** | v1.0.0 |
| **创建日期** | 2026-08-15 |
| **状态** | 设计中 |
| **预计工期** | 2 周 |

## 🎯 阶段目标

设计和实现多规则包系统，支持规则包的继承、合并、版本控制和动态加载，为不同领域和团队提供灵活的规则配置。

## 🏗️ 架构设计

### 1. 规则包系统架构

```
rule-pack-system/
├── core/                              # 核心系统
│   ├── __init__.py
│   ├── loader.py                     # 规则包加载器
│   ├── merger.py                     # 规则合并器
│   ├── validator.py                  # 规则验证器
│   ├── cache.py                      # 规则包缓存
│   └── registry.py                   # 规则包注册表
│
├── schemas/                           # Schema 定义
│   ├── __init__.py
│   ├── rule_pack.v2.py              # 规则包 Schema
│   ├── rule.v2.py                   # 规则 Schema
│   ├── rule_template.v2.py          # 规则模板 Schema
│   └── validation_rules.py          # 验证规则
│
├── parsers/                           # 规则解析器
│   ├── __init__.py
│   ├── base_parser.py               # 基础解析器
│   ├── json_parser.py               # JSON 解析器
│   ├── yaml_parser.py               # YAML 解析器
│   └── custom_parser.py             # 自定义解析器
│
├── validators/                        # 规则验证器
│   ├── __init__.py
│   ├── schema_validator.py          # Schema 验证器
│   ├── semantic_validator.py        # 语义验证器
│   ├── security_validator.py        # 安全验证器
│   └── compatibility_validator.py    # 兼容性验证器
│
├── resolvers/                         # 解析器
│   ├── __init__.py
│   ├── dependency_resolver.py       # 依赖解析器
│   ├── conflict_resolver.py         # 冲突解决器
│   └── inheritance_resolver.py      # 继承解析器
│
├── builders/                          # 构建器
│   ├── __init__.py
│   ├── pack_builder.py              # 规则包构建器
│   ├── rule_builder.py              # 规则构建器
│   └── template_builder.py          # 模板构建器
│
├── cli/                               # CLI 工具
│   ├── __init__.py
│   ├── rule_pack.py                 # 主 CLI 入口
│   ├── commands/                    # 命令实现
│   │   ├── __init__.py
│   │   ├── create.py
│   │   ├── validate.py
│   │   ├── list.py
│   │   ├── diff.py
│   │   ├── merge.py
│   │   ├── export.py
│   │   └── import.py
│   └── utils/                       # 工具函数
│
├── packages/                          # 预定义规则包
│   ├── universal/
│   │   ├── default.json
│   │   ├── strict.json
│   │   └── minimal.json
│   │
│   ├── frontend/
│   │   ├── default.json
│   │   ├── react.json
│   │   ├── vue.json
│   │   └── angular.json
│   │
│   ├── backend/
│   │   ├── default.json
│   │   ├── api.json
│   │   └── database.json
│   │
│   ├── security/
│   │   ├── default.json
│   │   ├── owasp.json
│   │   └── compliance.json
│   │
│   └── legacy/
│       └── his-medical.json
│
├── templates/                         # 规则模板
│   ├── git/
│   ├── code_style/
│   ├── security/
│   └── testing/
│
├── cache/                             # 缓存目录
│   └── rule_packs/
│
├── config/                            # 配置文件
│   ├── rule_pack.config.json        # 规则包配置
│   └── validation.config.json       # 验证配置
│
└── tests/                             # 测试
    ├── test_loader.py
    ├── test_merger.py
    ├── test_validator.py
    ├── test_resolvers.py
    └── fixtures/                     # 测试固件
```

### 2. 核心数据结构

#### 2.1 规则包元数据

```python
# schemas/rule_pack.v2.py

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set
from enum import Enum
from datetime import datetime
from pathlib import Path

class RulePackStatus(Enum):
    """规则包状态"""
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"

class RulePackCategory(Enum):
    """规则包分类"""
    UNIVERSAL = "universal"
    FRONTEND = "frontend"
    BACKEND = "backend"
    DEVOPS = "devops"
    SECURITY = "security"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    PERFORMANCE = "performance"
    DOMAIN_SPECIFIC = "domain_specific"

class RulePackPriority(Enum):
    """规则包优先级"""
    CRITICAL = "critical"      # 最高优先级，不可覆盖
    HIGH = "high"              # 高优先级，需要明确覆盖声明
    MEDIUM = "medium"          # 中等优先级
    LOW = "low"                # 低优先级，容易覆盖

@dataclass
class RulePackMetadata:
    """规则包元数据"""
    schema_version: str = "rule-pack.v2"
    pack_id: str = ""
    name: str = ""
    version: str = "1.0.0"
    display_name: str = ""
    description: str = ""
    category: RulePackCategory = RulePackCategory.UNIVERSAL
    status: RulePackStatus = RulePackStatus.DRAFT
    priority: RulePackPriority = RulePackPriority.MEDIUM

    # 继承关系
    extends: Optional[str] = None      # 继承的基础规则包
    overrides: List[str] = field(default_factory=list)  # 明确覆盖的规则

    # 作者信息
    author: str = ""
    author_email: str = ""
    organization: str = ""
    license: str = "MIT"

    # 版本兼容性
    platform_version_min: str = "1.0.0"
    platform_version_max: str = "99.0.0"

    # 统计信息
    rule_count: int = 0
    usage_count: int = 0

    # 时间戳
    created_at: str = ""
    updated_at: str = ""
    published_at: str = ""

    # 校验和
    checksum_sha256: str = ""

    # 标签和关键词
    tags: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)

    # 兼容性信息
    compatible_domains: List[str] = field(default_factory=list)
    compatible_languages: List[str] = field(default_factory=list)

    # 其他元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_dependency_chain(self) -> List[str]:
        """获取依赖链"""
        chain = []
        if self.extends:
            chain.append(self.extends)
        return chain

    def is_critical(self) -> bool:
        """是否为关键规则包"""
        return self.priority == RulePackPriority.CRITICAL

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "schema_version": self.schema_version,
            "pack_id": self.pack_id,
            "name": self.name,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category.value,
            "status": self.status.value,
            "priority": self.priority.value,
            "extends": self.extends,
            "overrides": self.overrides,
            "author": self.author,
            "author_email": self.author_email,
            "organization": self.organization,
            "license": self.license,
            "platform_version_min": self.platform_version_min,
            "platform_version_max": self.platform_version_max,
            "rule_count": self.rule_count,
            "usage_count": self.usage_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "published_at": self.published_at,
            "checksum_sha256": self.checksum_sha256,
            "tags": self.tags,
            "keywords": self.keywords,
            "compatible_domains": self.compatible_domains,
            "compatible_languages": self.compatible_languages,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "RulePackMetadata":
        """从字典创建"""
        return cls(
            schema_version=data.get("schema_version", "rule-pack.v2"),
            pack_id=data.get("pack_id", ""),
            name=data.get("name", ""),
            version=data.get("version", "1.0.0"),
            display_name=data.get("display_name", ""),
            description=data.get("description", ""),
            category=RulePackCategory(data.get("category", "universal")),
            status=RulePackStatus(data.get("status", "draft")),
            priority=RulePackPriority(data.get("priority", "medium")),
            extends=data.get("extends"),
            overrides=data.get("overrides", []),
            author=data.get("author", ""),
            author_email=data.get("author_email", ""),
            organization=data.get("organization", ""),
            license=data.get("license", "MIT"),
            platform_version_min=data.get("platform_version_min", "1.0.0"),
            platform_version_max=data.get("platform_version_max", "99.0.0"),
            rule_count=data.get("rule_count", 0),
            usage_count=data.get("usage_count", 0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            published_at=data.get("published_at", ""),
            checksum_sha256=data.get("checksum_sha256", ""),
            tags=data.get("tags", []),
            keywords=data.get("keywords", []),
            compatible_domains=data.get("compatible_domains", []),
            compatible_languages=data.get("compatible_languages", []),
            metadata=data.get("metadata", {})
        )
```

#### 2.2 规则定义

```python
# schemas/rule.v2.py

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from enum import Enum

class RuleType(Enum):
    """规则类型"""
    HARD_GUARD = "hard_guard"          # 硬保护规则
    SOFT_CHECK = "soft_check"          # 软检查规则
    SUGGESTION = "suggestion"          # 建议规则
    SECURITY = "security"              # 安全规则
    PERFORMANCE = "performance"        # 性能规则
    CODE_STYLE = "code_style"          # 代码风格规则
    BUSINESS = "business"              # 业务规则

class RuleAction(Enum):
    """规则动作"""
    BLOCK = "block"                    # 阻断
    WARN = "warn"                      # 警告
    INFO = "info"                      # 信息
    LOG = "log"                        # 记录日志
    IGNORE = "ignore"                  # 忽略

class RuleScope(Enum):
    """规则作用域"""
    GLOBAL = "global"                  # 全局作用域
    FILE = "file"                      # 文件作用域
    FUNCTION = "function"              # 函数作用域
    LINE = "line"                      # 行作用域
    COMMIT = "commit"                  # 提交作用域
    PR = "pr"                          # PR作用域

@dataclass
class RuleDefinition:
    """规则定义"""
    rule_id: str = ""
    name: str = ""
    display_name: str = ""
    description: str = ""
    rule_type: RuleType = RuleType.SOFT_CHECK
    action: RuleAction = RuleAction.WARN
    scope: RuleScope = RuleScope.FILE

    # 规则条件
    condition_type: str = "pattern"    # pattern, custom, ai, composite
    condition_pattern: Optional[str] = None
    condition_script: Optional[str] = None
    condition_ai_prompt: Optional[str] = None
    composite_rules: List[str] = field(default_factory=list)

    # 作用范围限定
    file_patterns: List[str] = field(default_factory=list)
    language_patterns: List[str] = field(default_factory=list)
    directory_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)

    # 执行配置
    enabled: bool = True
    auto_fixable: bool = False
    auto_fix_script: Optional[str] = None
    timeout_seconds: int = 30

    # 优先级和覆盖
    priority: int = 100
    can_override: bool = True
    override_conditions: List[str] = field(default_factory=list)

    # 验证和测试
    test_cases: List[Dict[str, Any]] = field(default_factory=list)
    validation_rules: List[str] = field(default_factory=list)

    # 解释和文档
    explanation: str = ""
    examples: List[Dict[str, str]] = field(default_factory=list)
    references: List[str] = field(default_factory=list)
    best_practices: List[str] = field(default_factory=list)

    # 元数据
    tags: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    category: Optional[str] = None
    severity: str = "medium"           # info, low, medium, high, critical

    # 版本控制
    version: str = "1.0.0"
    deprecated: bool = False
    deprecated_message: Optional[str] = None
    replaced_by: Optional[str] = None

    def evaluate(self, context: Dict) -> RuleEvaluationResult:
        """评估规则"""
        # 实现规则评估逻辑
        pass

    def can_execute(self, context: Dict) -> bool:
        """检查是否可以执行"""
        # 检查文件匹配
        if not self._matches_scope(context):
            return False

        # 检查语言匹配
        if not self._matches_language(context):
            return False

        # 检查排除模式
        if self._is_excluded(context):
            return False

        return True

    def _matches_scope(self, context: Dict) -> bool:
        """检查作用域匹配"""
        file_path = context.get("file_path", "")
        return any(
            file_path.endswith(pattern.lstrip("*"))
            for pattern in self.file_patterns
        ) if self.file_patterns else True

    def _matches_language(self, context: Dict) -> bool:
        """检查语言匹配"""
        language = context.get("language", "")
        return (
            not self.language_patterns or
            language in self.language_patterns
        )

    def _is_excluded(self, context: Dict) -> bool:
        """检查是否被排除"""
        file_path = context.get("file_path", "")
        return any(
            file_path.endswith(pattern.lstrip("*"))
            for pattern in self.exclude_patterns
        )

    def get_severity_score(self) -> int:
        """获取严重性评分"""
        severity_map = {
            "info": 1,
            "low": 2,
            "medium": 3,
            "high": 4,
            "critical": 5
        }
        return severity_map.get(self.severity, 3)

@dataclass
class RuleEvaluationResult:
    """规则评估结果"""
    rule_id: str
    passed: bool
    action: RuleAction
    severity: str
    violations: List[RuleViolation] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    execution_time_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "rule_id": self.rule_id,
            "passed": self.passed,
            "action": self.action.value,
            "severity": self.severity,
            "violations": [v.to_dict() for v in self.violations],
            "suggestions": self.suggestions,
            "context": self.context,
            "execution_time_ms": self.execution_time_ms,
            "metadata": self.metadata
        }

@dataclass
class RuleViolation:
    """规则违规信息"""
    file_path: str
    line_number: int
    column_number: Optional[int] = None
    end_line_number: Optional[int] = None
    end_column_number: Optional[int] = None
    rule_id: str = ""
    severity: str = "medium"
    message: str = ""
    suggestion: Optional[str] = None
    code_snippet: Optional[str] = None
    context_lines: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "column_number": self.column_number,
            "end_line_number": self.end_line_number,
            "end_column_number": self.end_column_number,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
            "code_snippet": self.code_snippet,
            "context_lines": self.context_lines
        }
```

#### 2.3 规则包完整结构

```python
# schemas/rule_pack_structure.py

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from .rule_pack.v2 import RulePackMetadata, RulePackCategory
from .rule.v2 import RuleDefinition

@dataclass
class HardGuards:
    """硬保护配置"""
    no_secret_printing: bool = True
    external_writes_default: bool = False
    real_status_transition_requires_confirmation: bool = True
    real_commit_push_requires_confirmation: bool = True
    destructive_git_forbidden: bool = True
    publish_forbidden_by_default: bool = True

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "no_secret_printing": self.no_secret_printing,
            "external_writes_default": self.external_writes_default,
            "real_status_transition_requires_confirmation": self.real_status_transition_requires_confirmation,
            "real_commit_push_requires_confirmation": self.real_commit_push_requires_confirmation,
            "destructive_git_forbidden": self.destructive_git_forbidden,
            "publish_forbidden_by_default": self.publish_forbidden_by_default
        }

@dataclass
class GitRules:
    """Git规则配置"""
    remote: str = "origin"
    integration_branch: str = "main"
    branch_name: Dict[str, str] = field(default_factory=dict)
    commit_message: Dict[str, Any] = field(default_factory=dict)
    base_branches: Dict[str, str] = field(default_factory=dict)
    permissions: Dict[str, bool] = field(default_factory=dict)
    required_before_commit: List[str] = field(default_factory=list)

@dataclass
class VerificationRules:
    """验证规则配置"""
    default_commands: Dict[str, List[str]] = field(default_factory=dict)
    test_coverage_minimum: float = 0.8
    lint_required: bool = True
    type_check_required: bool = True
    security_scan_required: bool = False

@dataclass
class RiskAssessment:
    """风险评估配置"""
    high_risk_keywords: List[str] = field(default_factory=list)
    medium_risk_keywords: List[str] = field(default_factory=list)
    low_risk_keywords: List[str] = field(default_factory=list)
    risk_thresholds: Dict[str, int] = field(default_factory=dict)

@dataclass
class RulePack:
    """完整的规则包"""
    metadata: RulePackMetadata
    hard_guards: HardGuards
    git: Optional[GitRules] = None
    verification: Optional[VerificationRules] = None
    risk_assessment: Optional[RiskAssessment] = None
    custom_rules: List[RuleDefinition] = field(default_factory=list)
    rule_templates: Dict[str, Any] = field(default_factory=dict)
    domain_specific: Dict[str, Any] = field(default_factory=dict)

    def get_all_rules(self) -> List[RuleDefinition]:
        """获取所有规则"""
        rules = []

        # 添加硬保护规则
        for guard_name, guard_value in self.hard_guards.to_dict().items():
            if guard_value:
                rules.append(self._create_hard_guard_rule(guard_name))

        # 添加自定义规则
        rules.extend(self.custom_rules)

        return rules

    def _create_hard_guard_rule(self, guard_name: str) -> RuleDefinition:
        """创建硬保护规则"""
        return RuleDefinition(
            rule_id=f"hard_guard.{guard_name}",
            name=f"Hard Guard: {guard_name}",
            display_name=f"{guard_name} 保护",
            description=f"硬保护规则：{guard_name}",
            rule_type=RuleType.HARD_GUARD,
            action=RuleAction.BLOCK,
            scope=RuleScope.GLOBAL,
            priority=1000,
            can_override=False,
            enabled=True
        )

    def merge_with(self, other: "RulePack") -> "RulePack":
        """合并另一个规则包"""
        # 实现合并逻辑
        pass

    def validate(self) -> ValidationResult:
        """验证规则包"""
        # 实现验证逻辑
        pass

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "metadata": self.metadata.to_dict(),
            "hard_guards": self.hard_guards.to_dict(),
            "git": self.git.to_dict() if self.git else None,
            "verification": self.verification.to_dict() if self.verification else None,
            "risk_assessment": self.risk_assessment.to_dict() if self.risk_assessment else None,
            "custom_rules": [rule.to_dict() for rule in self.custom_rules],
            "rule_templates": self.rule_templates,
            "domain_specific": self.domain_specific
        }

@dataclass
class ValidationResult:
    """验证结果"""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
```

### 3. 规则包加载器

```python
# core/loader.py

from typing import Dict, List, Optional, Any
from pathlib import Path
import json
import hashlib
from schemas.rule_pack_structure import RulePack, RulePackMetadata, HardGuards, GitRules, VerificationRules, RiskAssessment
from schemas.rule.v2 import RuleDefinition
from .cache import RulePackCache
from .registry import RulePackRegistry

class RulePackLoader:
    """规则包加载器"""

    def __init__(
        self,
        packages_root: Path,
        cache: Optional[RulePackCache] = None,
        registry: Optional[RulePackRegistry] = None
    ):
        self.packages_root = packages_root
        self.cache = cache
        self.registry = registry
        self.loaded_packs: Dict[str, RulePack] = {}
        self.load_order: List[str] = []

    def load(self, pack_id: str, version: Optional[str] = None) -> Optional[RulePack]:
        """加载规则包"""
        cache_key = f"{pack_id}:{version or 'latest'}"

        # 检查缓存
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        # 构建文件路径
        pack_path = self._find_pack_path(pack_id, version)
        if not pack_path or not pack_path.exists():
            return None

        # 加载规则包
        try:
            rule_pack = self._load_from_file(pack_path)

            # 缓存结果
            if self.cache:
                self.cache.set(cache_key, rule_pack)

            self.loaded_packs[pack_id] = rule_pack
            return rule_pack

        except Exception as e:
            return None

    def load_with_extends(self, pack_id: str) -> Optional[RulePack]:
        """加载规则包及其继承链"""
        pack = self.load(pack_id)
        if not pack:
            return None

        # 加载继承的规则包
        if pack.metadata.extends:
            base_pack = self.load_with_extends(pack.metadata.extends)
            if base_pack:
                pack = self._merge_with_base(pack, base_pack)

        return pack

    def load_all(self, category: Optional[str] = None) -> List[RulePack]:
        """加载所有规则包"""
        all_packs = []

        for category_path in self.packages_root.iterdir():
            if not category_path.is_dir():
                continue

            if category and category_path.name != category:
                continue

            for pack_file in category_path.glob("*.json"):
                try:
                    pack = self._load_from_file(pack_file)
                    if pack:
                        all_packs.append(pack)
                except Exception as e:
                    continue

        return all_packs

    def _find_pack_path(self, pack_id: str, version: Optional[str]) -> Optional[Path]:
        """查找规则包文件路径"""
        # 尝试从注册表查找
        if self.registry:
            metadata = self.registry.get_metadata(pack_id)
            if metadata:
                return self.packages_root / metadata.category.value / f"{metadata.name}.json"

        # 尝试直接查找
        pack_file = self.packages_root / f"{pack_id}.json"
        if pack_file.exists():
            return pack_file

        # 尝试按分类查找
        for category_path in self.packages_root.iterdir():
            if category_path.is_dir():
                pack_file = category_path / f"{pack_id}.json"
                if pack_file.exists():
                    return pack_file

        return None

    def _load_from_file(self, file_path: Path) -> RulePack:
        """从文件加载规则包"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 解析元数据
        metadata = RulePackMetadata.from_dict(data.get("metadata", {}))

        # 解析硬保护配置
        hard_guards_data = data.get("hard_guards", {})
        hard_guards = HardGuards(**hard_guards_data)

        # 解析Git规则
        git_rules = None
        if "git" in data:
            git_data = data["git"]
            git_rules = GitRules(
                remote=git_data.get("remote", "origin"),
                integration_branch=git_data.get("integration_branch", "main"),
                branch_name=git_data.get("branch_name", {}),
                commit_message=git_data.get("commit_message", {}),
                base_branches=git_data.get("base_branches", {}),
                permissions=git_data.get("permissions", {}),
                required_before_commit=git_data.get("required_before_commit", [])
            )

        # 解析验证规则
        verification_rules = None
        if "verification" in data:
            verification_data = data["verification"]
            verification_rules = VerificationRules(
                default_commands=verification_data.get("default_commands", {}),
                test_coverage_minimum=verification_data.get("test_coverage_minimum", 0.8),
                lint_required=verification_data.get("lint_required", True),
                type_check_required=verification_data.get("type_check_required", True),
                security_scan_required=verification_data.get("security_scan_required", False)
            )

        # 解析风险评估
        risk_assessment = None
        if "risk_assessment" in data:
            risk_data = data["risk_assessment"]
            risk_assessment = RiskAssessment(
                high_risk_keywords=risk_data.get("high_risk_keywords", []),
                medium_risk_keywords=risk_data.get("medium_risk_keywords", []),
                low_risk_keywords=risk_data.get("low_risk_keywords", []),
                risk_thresholds=risk_data.get("risk_thresholds", {})
            )

        # 解析自定义规则
        custom_rules = []
        if "custom_rules" in data:
            for rule_data in data["custom_rules"]:
                custom_rules.append(RuleDefinition(**rule_data))

        # 创建规则包
        rule_pack = RulePack(
            metadata=metadata,
            hard_guards=hard_guards,
            git=git_rules,
            verification=verification_rules,
            risk_assessment=risk_assessment,
            custom_rules=custom_rules,
            rule_templates=data.get("rule_templates", {}),
            domain_specific=data.get("domain_specific", {})
        )

        # 计算校验和
        metadata.checksum_sha256 = self._calculate_checksum(file_path)
        metadata.rule_count = len(rule_pack.get_all_rules())

        return rule_pack

    def _merge_with_base(self, pack: RulePack, base_pack: RulePack) -> RulePack:
        """与基础规则包合并"""
        # 合并硬保护（基础包的硬保护优先级更高）
        merged_hard_guards = HardGuards()
        for field in HardGuards.__dataclass_fields__:
            base_value = getattr(base_pack.hard_guards, field)
            pack_value = getattr(pack.hard_guards, field)
            setattr(merged_hard_guards, field, base_value if base_value is not None else pack_value)

        # 合并Git规则
        merged_git = self._merge_git_rules(pack.git, base_pack.git) if pack.git or base_pack.git else None

        # 合并验证规则
        merged_verification = self._merge_verification_rules(
            pack.verification, base_pack.verification
        ) if pack.verification or base_pack.verification else None

        # 合并风险评估
        merged_risk = self._merge_risk_assessment(
            pack.risk_assessment, base_pack.risk_assessment
        ) if pack.risk_assessment or base_pack.risk_assessment else None

        # 合并自定义规则（派生包规则优先）
        merged_custom_rules = self._merge_custom_rules(
            pack.custom_rules, base_pack.custom_rules, pack.metadata.overrides
        )

        # 合并模板
        merged_templates = {**base_pack.rule_templates, **pack.rule_templates}

        # 合并域特定配置
        merged_domain_specific = {
            **base_pack.domain_specific,
            **pack.domain_specific
        }

        return RulePack(
            metadata=pack.metadata,
            hard_guards=merged_hard_guards,
            git=merged_git,
            verification=merged_verification,
            risk_assessment=merged_risk,
            custom_rules=merged_custom_rules,
            rule_templates=merged_templates,
            domain_specific=merged_domain_specific
        )

    def _merge_git_rules(
        self,
        git1: Optional[GitRules],
        git2: Optional[GitRules]
    ) -> Optional[GitRules]:
        """合并Git规则"""
        if not git1:
            return git2
        if not git2:
            return git1

        merged = GitRules()
        for field in GitRules.__dataclass_fields__:
            value1 = getattr(git1, field)
            value2 = getattr(git2, field)

            if isinstance(value1, dict):
                merged_value = {**value2, **value1}
            else:
                merged_value = value1 if value1 is not None else value2

            setattr(merged, field, merged_value)

        return merged

    def _merge_verification_rules(
        self,
        verification1: Optional[VerificationRules],
        verification2: Optional[VerificationRules]
    ) -> Optional[VerificationRules]:
        """合并验证规则"""
        if not verification1:
            return verification2
        if not verification2:
            return verification1

        return VerificationRules(
            default_commands={
                **verification2.default_commands,
                **verification1.default_commands
            },
            test_coverage_minimum=max(
                verification1.test_coverage_minimum,
                verification2.test_coverage_minimum
            ),
            lint_required=verification1.lint_required or verification2.lint_required,
            type_check_required=verification1.type_check_required or verification2.type_check_required,
            security_scan_required=verification1.security_scan_required or verification2.security_scan_required
        )

    def _merge_risk_assessment(
        self,
        risk1: Optional[RiskAssessment],
        risk2: Optional[RiskAssessment]
    ) -> Optional[RiskAssessment]:
        """合并风险评估"""
        if not risk1:
            return risk2
        if not risk2:
            return risk1

        return RiskAssessment(
            high_risk_keywords=risk1.high_risk_keywords + risk2.high_risk_keywords,
            medium_risk_keywords=risk1.medium_risk_keywords + risk2.medium_risk_keywords,
            low_risk_keywords=risk1.low_risk_keywords + risk2.low_risk_keywords,
            risk_thresholds={**risk2.risk_thresholds, **risk1.risk_thresholds}
        )

    def _merge_custom_rules(
        self,
        rules1: List[RuleDefinition],
        rules2: List[RuleDefinition],
        overrides: List[str]
    ) -> List[RuleDefinition]:
        """合并自定义规则"""
        # 创建规则映射（派生包规则优先）
        rule_map = {}

        # 添加基础规则
        for rule in rules2:
            rule_map[rule.rule_id] = rule

        # 添加派生规则（覆盖基础规则）
        for rule in rules1:
            if rule.rule_id in rule_map:
                # 检查是否允许覆盖
                base_rule = rule_map[rule.rule_id]
                if not base_rule.can_override and rule.rule_id not in overrides:
                    continue  # 不允许覆盖，跳过
            rule_map[rule.rule_id] = rule

        return list(rule_map.values())

    def _calculate_checksum(self, file_path: Path) -> str:
        """计算文件校验和"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()

    def reload(self, pack_id: str) -> Optional[RulePack]:
        """重新加载规则包"""
        if pack_id in self.loaded_packs:
            del self.loaded_packs[pack_id]

        if self.cache:
            cache_keys = [key for key in self.cache.cache.keys() if key.startswith(f"{pack_id}:")]
            for key in cache_keys:
                del self.cache.cache[key]

        return self.load(pack_id)
```

### 4. 规则合并器

```python
# core/merger.py

from typing import Dict, List, Optional, Any, Tuple
from schemas.rule_pack_structure import RulePack, RulePackMetadata, RuleDefinition
from schemas.rule.v2 import RuleAction, RuleType

class RulePackMerger:
    """规则包合并器"""

    def __init__(self, loader):
        self.loader = loader

    def merge_packs(
        self,
        pack_ids: List[str],
        resolve_conflicts: str = "latest"  # latest, strict, manual
    ) -> MergeResult:
        """合并多个规则包"""
        if not pack_ids:
            return MergeResult(
                success=False,
                errors=["No rule packs specified"]
            )

        # 加载所有规则包
        packs = []
        for pack_id in pack_ids:
            pack = self.loader.load_with_extends(pack_id)
            if not pack:
                return MergeResult(
                    success=False,
                    errors=[f"Failed to load rule pack: {pack_id}"]
                )
            packs.append(pack)

        # 检查冲突
        conflicts = self._detect_conflicts(packs)
        if conflicts and resolve_conflicts == "strict":
            return MergeResult(
                success=False,
                conflicts=conflicts,
                errors=["Conflicts detected in strict mode"]
            )

        # 解决冲突
        resolved_rules = self._resolve_conflicts(packs, conflicts, resolve_conflicts)

        # 创建合并后的规则包
        merged_pack = self._create_merged_pack(packs, resolved_rules)

        return MergeResult(
            success=True,
            merged_pack=merged_pack,
            conflicts_resolved=len(conflicts),
            warnings=[f"Merged {len(packs)} rule packs"]
        )

    def _detect_conflicts(self, packs: List[RulePack]) -> List[ConflictInfo]:
        """检测规则冲突"""
        conflicts = []
        rule_sources: Dict[str, List[str]] = {}

        # 收集所有规则的来源
        for pack in packs:
            for rule in pack.get_all_rules():
                if rule.rule_id not in rule_sources:
                    rule_sources[rule.rule_id] = []
                rule_sources[rule.rule_id].append(pack.metadata.pack_id)

        # 检测同一规则来自不同包的情况
        for rule_id, sources in rule_sources.items():
            if len(sources) > 1:
                conflicts.append(ConflictInfo(
                    type="duplicate_rule",
                    rule_id=rule_id,
                    sources=sources,
                    description=f"Rule {rule_id} appears in multiple packs"
                ))

        # 检测硬保护冲突
        hard_guard_conflicts = self._detect_hard_guard_conflicts(packs)
        conflicts.extend(hard_guard_conflicts)

        return conflicts

    def _detect_hard_guard_conflicts(self, packs: List[RulePack]) -> List[ConflictInfo]:
        """检测硬保护冲突"""
        conflicts = []

        hard_guard_states: Dict[str, Dict[str, bool]] = {}

        for pack in packs:
            guards = pack.hard_guards.to_dict()
            for guard_name, guard_value in guards.items():
                if guard_name not in hard_guard_states:
                    hard_guard_states[guard_name] = {}

                if guard_value in hard_guard_states[guard_name]:
                    if hard_guard_states[guard_name][guard_value] != pack.metadata.pack_id:
                        conflicts.append(ConflictInfo(
                            type="hard_guard_conflict",
                            rule_id=guard_name,
                            sources=[
                                hard_guard_states[guard_name][guard_value],
                                pack.metadata.pack_id
                            ],
                            description=f"Hard guard {guard_name} has conflicting values"
                        ))
                else:
                    hard_guard_states[guard_name][guard_value] = pack.metadata.pack_id

        return conflicts

    def _resolve_conflicts(
        self,
        packs: List[RulePack],
        conflicts: List[ConflictInfo],
        strategy: str
    ) -> List[RuleDefinition]:
        """解决冲突"""
        resolved_rules: Dict[str, RuleDefinition] = {}

        for pack in packs:
            for rule in pack.get_all_rules():
                if rule.rule_id not in resolved_rules:
                    resolved_rules[rule.rule_id] = rule
                else:
                    # 根据策略解决冲突
                    existing_rule = resolved_rules[rule.rule_id]

                    if strategy == "latest":
                        # 使用最新版本的规则
                        if self._is_newer_version(rule.version, existing_rule.version):
                            resolved_rules[rule.rule_id] = rule
                    elif strategy == "manual":
                        # 需要手动解决（记录冲突）
                        pass

        return list(resolved_rules.values())

    def _is_newer_version(self, version1: str, version2: str) -> bool:
        """比较版本号"""
        v1_parts = [int(x) for x in version1.split('.')]
        v2_parts = [int(x) for x in version2.split('.')]

        for v1, v2 in zip(v1_parts, v2_parts):
            if v1 > v2:
                return True
            elif v1 < v2:
                return False

        return len(v1_parts) > len(v2_parts)

    def _create_merged_pack(
        self,
        packs: List[RulePack],
        resolved_rules: List[RuleDefinition]
    ) -> RulePack:
        """创建合并后的规则包"""
        # 使用第一个包的基础元数据
        base_pack = packs[0]

        # 合并硬保护（使用最严格的）
        merged_hard_guards = self._merge_hard_guards([p.hard_guards for p in packs])

        # 合并Git规则
        merged_git = self._merge_git_rules([p.git for p in packs if p.git])

        # 合并验证规则（使用最严格的）
        merged_verification = self._merge_verification_rules([
            p.verification for p in packs if p.verification
        ])

        # 合并风险评估（累积）
        merged_risk = self._merge_risk_assessments([
            p.risk_assessment for p in packs if p.risk_assessment
        ])

        # 创建合并后的元数据
        merged_metadata = RulePackMetadata(
            schema_version="rule-pack.v2",
            pack_id=f"merged:{'+'.join(p.metadata.pack_id for p in packs)}",
            name="merged",
            version="1.0.0",
            display_name=f"Merged Rule Pack ({len(packs)} packs)",
            description=f"Merged from: {', '.join(p.metadata.pack_id for p in packs)}",
            category=base_pack.metadata.category,
            status=RulePackStatus.ACTIVE
        )

        return RulePack(
            metadata=merged_metadata,
            hard_guards=merged_hard_guards,
            git=merged_git,
            verification=merged_verification,
            risk_assessment=merged_risk,
            custom_rules=resolved_rules,
            rule_templates={},
            domain_specific={}
        )

    def _merge_hard_guards(self, guards_list: List[Any]) -> Any:
        """合并硬保护（使用最严格的）"""
        from schemas.rule_pack_structure import HardGuards
        merged = HardGuards()

        for guards in guards_list:
            for field in HardGuards.__dataclass_fields__:
                current_value = getattr(merged, field)
                new_value = getattr(guards, field)

                # 对于硬保护，True（启用）优先于 False（禁用）
                if new_value and not current_value:
                    setattr(merged, field, True)

        return merged

    def _merge_git_rules(self, git_rules_list: List[Any]) -> Optional[Any]:
        """合并Git规则"""
        if not git_rules_list:
            return None

        # 使用第一个非空的Git规则作为基础
        base_git = next((g for g in git_rules_list if g), None)
        if not base_git:
            return None

        # 合并其他Git规则
        for git_rules in git_rules_list:
            if git_rules != base_git:
                for field in type(base_git).__dataclass_fields__:
                    base_value = getattr(base_git, field)
                    new_value = getattr(git_rules, field)

                    if isinstance(base_value, dict):
                        merged_value = {**base_value, **new_value}
                        setattr(base_git, field, merged_value)
                    elif base_value is None:
                        setattr(base_git, field, new_value)

        return base_git

    def _merge_verification_rules(self, verification_list: List[Any]) -> Optional[Any]:
        """合并验证规则"""
        if not verification_list:
            return None

        from schemas.rule_pack_structure import VerificationRules
        merged = VerificationRules()

        for verification in verification_list:
            # 使用最严格的测试覆盖率要求
            merged.test_coverage_minimum = max(
                merged.test_coverage_minimum,
                verification.test_coverage_minimum
            )

            # 任何要求都为True
            merged.lint_required = merged.lint_required or verification.lint_required
            merged.type_check_required = merged.type_check_required or verification.type_check_required
            merged.security_scan_required = merged.security_scan_required or verification.security_scan_required

            # 合并默认命令
            merged.default_commands.update(verification.default_commands)

        return merged

    def _merge_risk_assessments(self, risk_list: List[Any]) -> Optional[Any]:
        """合并风险评估"""
        if not risk_list:
            return None

        from schemas.rule_pack_structure import RiskAssessment
        merged = RiskAssessment()

        for risk in risk_list:
            # 累积关键词
            merged.high_risk_keywords.extend(risk.high_risk_keywords)
            merged.medium_risk_keywords.extend(risk.medium_risk_keywords)
            merged.low_risk_keywords.extend(risk.low_risk_keywords)

            # 合并阈值
            merged.risk_thresholds.update(risk.risk_thresholds)

        return merged

@dataclass
class MergeResult:
    """合并结果"""
    success: bool
    merged_pack: Optional[RulePack] = None
    conflicts_resolved: int = 0
    conflicts: List["ConflictInfo"] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

@dataclass
class ConflictInfo:
    """冲突信息"""
    type: str
    rule_id: str
    sources: List[str]
    description: str
    resolution_suggestion: Optional[str] = None
```

### 5. CLI 工具

```python
# cli/rule_pack.py

import argparse
from typing import Optional
from pathlib import Path
from core.loader import RulePackLoader
from core.merger import RulePackMerger
from core.validator import RulePackValidator
from core.cache import RulePackCache
from cli.commands import create, validate, list, diff, merge, export, import_pkg

def create_parser() -> argparse.ArgumentParser:
    """创建命令行解析器"""
    parser = argparse.ArgumentParser(
        description="Harness Rule Pack Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # 全局选项
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细输出"
    )
    parser.add_argument(
        "--packages-dir",
        type=Path,
        default=Path("./packages"),
        help="规则包目录"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("./cache/rule_packs"),
        help="缓存目录"
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "yaml", "markdown"],
        default="table",
        help="输出格式"
    )

    # 子命令
    subparsers = parser.add_subparsers(
        dest="command",
        help="可用命令",
        required=True
    )

    # create 命令
    create_parser = subparsers.add_parser(
        "create",
        help="创建新规则包"
    )
    create_parser.add_argument(
        "--id", "-i",
        required=True,
        help="规则包ID"
    )
    create_parser.add_argument(
        "--name", "-n",
        required=True,
        help="规则包名称"
    )
    create_parser.add_argument(
        "--display-name",
        help="显示名称"
    )
    create_parser.add_argument(
        "--description", "-d",
        help="描述"
    )
    create_parser.add_argument(
        "--category", "-c",
        choices=["universal", "frontend", "backend", "devops", "security"],
        default="universal",
        help="分类"
    )
    create_parser.add_argument(
        "--extends",
        help="继承的基础规则包"
    )
    create_parser.add_argument(
        "--output", "-o",
        type=Path,
        help="输出目录"
    )

    # validate 命令
    validate_parser = subparsers.add_parser(
        "validate",
        help="验证规则包"
    )
    validate_parser.add_argument(
        "pack_id",
        help="规则包ID"
    )
    validate_parser.add_argument(
        "--file", "-f",
        type=Path,
        help="直接验证指定文件"
    )
    validate_parser.add_argument(
        "--strict",
        action="store_true",
        help="严格验证模式"
    )

    # list 命令
    list_parser = subparsers.add_parser(
        "list",
        help="列出规则包"
    )
    list_parser.add_argument(
        "--category", "-c",
        help="按分类筛选"
    )
    list_parser.add_argument(
        "--status", "-s",
        choices=["draft", "active", "deprecated"],
        help="按状态筛选"
    )
    list_parser.add_argument(
        "--show-rules",
        action="store_true",
        help="显示规则详情"
    )

    # diff 命令
    diff_parser = subparsers.add_parser(
        "diff",
        help="比较规则包差异"
    )
    diff_parser.add_argument(
        "pack1",
        help="第一个规则包ID"
    )
    diff_parser.add_argument(
        "pack2",
        help="第二个规则包ID"
    )
    diff_parser.add_argument(
        "--version1",
        help="第一个包的版本"
    )
    diff_parser.add_argument(
        "--version2",
        help="第二个包的版本"
    )

    # merge 命令
    merge_parser = subparsers.add_parser(
        "merge",
        help="合并多个规则包"
    )
    merge_parser.add_argument(
        "packs",
        nargs="+",
        help="要合并的规则包ID列表"
    )
    merge_parser.add_argument(
        "--conflict-resolution",
        choices=["latest", "strict", "manual"],
        default="latest",
        help="冲突解决策略"
    )
    merge_parser.add_argument(
        "--output", "-o",
        type=Path,
        help="输出合并后的规则包文件"
    )

    # export 命令
    export_parser = subparsers.add_parser(
        "export",
        help="导出规则包"
    )
    export_parser.add_argument(
        "pack_id",
        help="规则包ID"
    )
    export_parser.add_argument(
        "--output", "-o",
        type=Path,
        required=True,
        help="输出文件路径"
    )
    export_parser.add_argument(
        "--format",
        choices=["json", "yaml"],
        default="json",
        help="输出格式"
    )

    # import 命令
    import_parser = subparsers.add_parser(
        "import",
        help="导入规则包"
    )
    import_parser.add_argument(
        "file",
        type=Path,
        help="规则包文件路径"
    )
    import_parser.add_argument(
        "--category", "-c",
        help="指定分类"
    )
    import_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖现有规则包"
    )

    return parser

def main():
    parser = create_parser()
    args = parser.parse_args()

    # 初始化服务
    cache = RulePackCache(args.cache_dir)
    loader = RulePackLoader(
        packages_root=args.packages_dir,
        cache=cache
    )
    merger = RulePackMerger(loader)
    validator = RulePackValidator(loader)

    # 处理命令
    command_map = {
        "create": create.handle,
        "validate": validate.handle,
        "list": list.handle,
        "diff": diff.handle,
        "merge": merge.handle,
        "export": export.handle,
        "import": import_pkg.handle
    }

    command_handler = command_map.get(args.command)
    if command_handler:
        result = command_handler(args, loader, merger, validator)
        output_result(result, args.format, args.verbose)
    else:
        parser.print_help()

def output_result(result: Any, format: str, verbose: bool):
    """输出结果"""
    if format == "json":
        import json
        print(json.dumps(result, indent=2, default=str))
    elif format == "yaml":
        import yaml
        print(yaml.dump(result, default_flow_style=False))
    elif format == "markdown":
        output_markdown(result)
    else:  # table
        output_table(result)

if __name__ == "__main__":
    main()
```

### 6. 预定义规则包示例

#### 6.1 通用默认规则包

```json
{
  "metadata": {
    "schema_version": "rule-pack.v2",
    "pack_id": "universal.default",
    "name": "default",
    "version": "1.0.0",
    "display_name": "通用默认规则包",
    "description": "适用于所有类型开发的基础规则",
    "category": "universal",
    "status": "active",
    "priority": "high",
    "extends": null,
    "author": "Harness Team",
    "license": "MIT",
    "tags": ["universal", "default", "basic"],
    "keywords": ["基础规则", "通用规则"]
  },
  "hard_guards": {
    "no_secret_printing": true,
    "external_writes_default": false,
    "real_status_transition_requires_confirmation": true,
    "real_commit_push_requires_confirmation": true,
    "destructive_git_forbidden": true,
    "publish_forbidden_by_default": true
  },
  "git": {
    "remote": "origin",
    "integration_branch": "main",
    "branch_name": {
      "feature": "feature/{id}",
      "bugfix": "bugfix/{id}",
      "hotfix": "hotfix/{id}",
      "chore": "chore/{id}",
      "refactor": "refactor/{id}"
    },
    "commit_message": {
      "style": "conventional",
      "format": "{type}(scope): {subject}",
      "types": ["feat", "fix", "docs", "style", "refactor", "perf", "test", "chore"]
    },
    "base_branches": {
      "default": "main",
      "develop": "develop"
    },
    "permissions": {
      "auto_create_branch": false,
      "auto_commit": false,
      "auto_push": false,
      "auto_merge": false,
      "auto_push_feature_branch": false,
      "auto_integrate_main": false,
      "auto_push_main": false
    },
    "required_before_commit": [
      "lint_check",
      "unit_tests",
      "code_review"
    ]
  },
  "verification": {
    "default_commands": {
      "frontend": ["npm run lint", "npm run type-check", "npm test"],
      "backend": ["make lint", "make test", "make type-check"]
    },
    "test_coverage_minimum": 0.8,
    "lint_required": true,
    "type_check_required": true,
    "security_scan_required": false
  },
  "risk_assessment": {
    "high_risk_keywords": ["production", "deploy", "database", "delete", "remove", "force"],
    "medium_risk_keywords": ["config", "migration", "api", "external"],
    "low_risk_keywords": ["ui", "style", "typo", "format"],
    "risk_thresholds": {
      "high": 3,
      "medium": 2,
      "low": 1
    }
  },
  "custom_rules": [
    {
      "rule_id": "universal.secret-detection",
      "name": "secret-detection",
      "display_name": "密钥检测",
      "description": "检测代码中可能泄露的密钥和敏感信息",
      "rule_type": "security",
      "action": "block",
      "scope": "file",
      "enabled": true,
      "priority": 1000,
      "file_patterns": ["*.py", "*.js", "*.ts", "*.json", "*.yaml", "*.yml"],
      "condition_type": "pattern",
      "condition_pattern": "(password|secret|token|api_key|private_key|access_token)[\\s]*[:=][\\s]*['\"]\\w+['\"]",
      "explanation": "敏感信息不应硬编码在代码中",
      "suggestions": [
        "使用环境变量存储敏感信息",
        "使用密钥管理服务"
      ]
    },
    {
      "rule_id": "universal.console-logging",
      "name": "console-logging",
      "display_name": "控制台日志检测",
      "description": "检测不恰当的控制台日志输出",
      "rule_type": "code_style",
      "action": "warn",
      "scope": "line",
      "enabled": true,
      "priority": 100,
      "file_patterns": ["*.js", "*.ts"],
      "condition_type": "pattern",
      "condition_pattern": "console\\.(log|debug|info|warn|error)\\(",
      "explanation": "生产环境代码不应包含控制台日志",
      "suggestions": [
        "使用适当的日志库",
        "移除调试代码"
      ]
    }
  ]
}
```

#### 6.2 前端默认规则包

```json
{
  "metadata": {
    "schema_version": "rule-pack.v2",
    "pack_id": "frontend.default",
    "name": "default",
    "version": "1.0.0",
    "display_name": "前端开发默认规则包",
    "description": "专注于前端开发的规则和最佳实践",
    "category": "frontend",
    "status": "active",
    "priority": "medium",
    "extends": "universal.default",
    "overrides": [],
    "author": "Harness Team",
    "license": "MIT",
    "tags": ["frontend", "javascript", "typescript"],
    "keywords": ["前端", "JS", "TS", "组件"]
  },
  "hard_guards": {
    "no_secret_printing": true,
    "external_writes_default": false,
    "real_status_transition_requires_confirmation": true,
    "real_commit_push_requires_confirmation": true,
    "destructive_git_forbidden": true,
    "publish_forbidden_by_default": true
  },
  "git": {
    "remote": "origin",
    "integration_branch": "main",
    "branch_name": {
      "feature": "feature/ui/{id}",
      "bugfix": "bugfix/ui/{id}",
      "hotfix": "hotfix/ui/{id}",
      "chore": "chore/ui/{id}"
    },
    "commit_message": {
      "style": "conventional",
      "format": "{type}(ui): {subject}",
      "types": ["feat", "fix", "docs", "style", "refactor", "perf", "test", "chore"]
    },
    "base_branches": {
      "default": "main",
      "develop": "develop"
    },
    "permissions": {
      "auto_create_branch": false,
      "auto_commit": false,
      "auto_push": false,
      "auto_merge": false,
      "auto_push_feature_branch": false,
      "auto_integrate_main": false,
      "auto_push_main": false
    },
    "required_before_commit": [
      "frontend_lint",
      "frontend_type_check",
      "frontend_build",
      "component_test"
    ]
  },
  "verification": {
    "default_commands": {
      "react": ["npm run lint", "npm run type-check", "npm test", "npm run build"],
      "vue": ["npm run lint", "npm run type-check", "npm test", "npm run build"],
      "angular": ["ng lint", "ng test", "ng build"],
      "vanilla": ["npm run lint", "npm test"]
    },
    "test_coverage_minimum": 0.8,
    "lint_required": true,
    "type_check_required": true,
    "security_scan_required": true
  },
  "risk_assessment": {
    "high_risk_keywords": ["mutation", "global-state", "eval", "innerHTML", "document.write"],
    "medium_risk_keywords": ["component", "hook", "state", "prop"],
    "low_risk_keywords": ["style", "class", "layout", "animation"],
    "risk_thresholds": {
      "high": 3,
      "medium": 2,
      "low": 1
    }
  },
  "custom_rules": [
    {
      "rule_id": "frontend.component-naming",
      "name": "component-naming",
      "display_name": "组件命名规范",
      "description": "确保组件使用正确的命名约定",
      "rule_type": "code_style",
      "action": "warn",
      "scope": "file",
      "enabled": true,
      "priority": 200,
      "file_patterns": ["*.jsx", "*.tsx", "*.vue"],
      "condition_type": "pattern",
      "condition_pattern": "(class|function) [a-z]",
      "explanation": "React组件应该使用PascalCase命名",
      "suggestions": [
        "将组件名改为PascalCase",
        "使用PascalCase作为文件名"
      ]
    },
    {
      "rule_id": "frontend.hooks-dependencies",
      "name": "hooks-dependencies",
      "display_name": "Hooks依赖检查",
      "description": "确保useEffect等Hooks正确声明依赖项",
      "rule_type": "security",
      "action": "block",
      "scope": "function",
      "enabled": true,
      "priority": 300,
      "file_patterns": ["*.jsx", "*.tsx"],
      "condition_type": "pattern",
      "condition_pattern": "useEffect\\(\\([^\\]]*\\)[^\\)]*\\)(?!\\s*,\\s*\\[)",
      "explanation": "useEffect必须声明完整的依赖项数组",
      "suggestions": [
        "添加所有使用的依赖项到useEffect的第二个参数",
        "使用ESLint的react-hooks/exhaustive-deps规则"
      ]
    },
    {
      "rule_id": "frontend.inline-styles",
      "name": "inline-styles",
      "display_name": "内联样式检查",
      "description": "避免使用内联样式，应该使用CSS类或样式模块",
      "rule_type": "code_style",
      "action": "warn",
      "scope": "line",
      "enabled": true,
      "priority": 150,
      "file_patterns": ["*.jsx", "*.tsx"],
      "condition_type": "pattern",
      "condition_pattern": "style=\\{\\{[^}]+\\}\\}",
      "explanation": "内联样式会影响性能和可维护性",
      "suggestions": [
        "使用CSS类或样式模块",
        "使用CSS-in-JS解决方案如styled-components"
      ]
    }
  ],
  "framework_specific": {
    "react": {
      "rules": ["jsx-no-inline-styles", "use-effect-dependencies"],
      "hooks_check": true
    },
    "vue": {
      "rules": ["vue-name-prop-casing", "vue-component-definition-name-casing"],
      "composition_api": true
    }
  }
}
```

### 7. 交付物清单

| 交付物 | 描述 | 状态 |
|--------|------|------|
| 规则包加载器 | loader.py | ⬜ |
| 规则合并器 | merger.py | ⬜ |
| 规则验证器 | validator.py | ⬜ |
| 规则包缓存 | cache.py | ⬜ |
| 规则包注册表 | registry.py | ⬜ |
| Schema 定义 | schemas/*.py | ⬜ |
| CLI 工具 | cli/rule_pack.py | ⬜ |
| 解析器实现 | parsers/*.py | ⬜ |
| 冲突解决器 | resolvers/*.py | ⬜ |
| 构建器实现 | builders/*.py | ⬜ |
| 预定义规则包 | packages/**/*.json | ⬜ |
| 单元测试 | tests/test_*.py | ⬜ |
| 集成测试 | tests/integration/*.py | ⬜ |
| 架构文档 | 本文档 | ✅ |

---

**下一步行动**：进入阶段3的代码实现阶段