# 阶段1：核心去医疗化架构设计文档

## 📋 文档信息

| 项目 | 内容 |
|------|------|
| **阶段** | 阶段1：核心去医疗化 |
| **版本** | v1.0.0 |
| **创建日期** | 2026-08-15 |
| **状态** | 设计中 |
| **预计工期** | 2-3 周 |

## 🎯 阶段目标

将现有的 HIS AI Harness 系统重构为通用的 AI Workflow 平台核心，移除所有医疗领域特定的硬编码，建立可复用的抽象层。

## 🏗️ 架构设计

### 1. 目录结构

```
harness-platform/
├── core/                              # 通用核心层
│   ├── __init__.py
│   ├── capability/                    # 能力管理
│   │   ├── __init__.py
│   │   ├── registry.py               # 能力注册表（现有）
│   │   ├── runtime.py                # 运行时（现有）
│   │   ├── service.py                # 服务层（现有）
│   │   └── contract.py               # 合约抽象层（新增）
│   │
│   ├── plugin/                        # 插件系统
│   │   ├── __init__.py
│   │   ├── manager.py                # 插件管理器（新增）
│   │   ├── inventory.py              # 插件清单（现有）
│   │   ├── loader.py                 # 插件加载器（新增）
│   │   └── validator.py              # 插件验证器（新增）
│   │
│   ├── security/                      # 安全模型
│   │   ├── __init__.py
│   │   ├── permission.py             # 权限模型（L0-L5）
│   │   ├── credential.py             # 凭证管理
│   │   └── audit.py                  # 审计日志
│   │
│   ├── schema/                        # Schema 定义
│   │   ├── __init__.py
│   │   ├── capability_manifest.py
│   │   ├── requirement_governance.py
│   │   ├── code_review.py
│   │   └── deployment.py
│   │
│   ├── workspace/                     # 工作空间管理
│   │   ├── __init__.py
│   │   ├── manager.py                # 工作空间管理器（新增）
│   │   ├── config.py                 # 工作空间配置
│   │   └── isolation.py              # 隔离策略
│   │
│   └── config/                        # 核心配置
│       ├── __init__.py
│       ├── settings.py               # 全局设置
│       └── constants.py              # 常量定义
│
├── legacy/                            # HIS 兼容层
│   ├── __init__.py
│   ├── his_adapter.py                # HIS 适配器
│   ├── dfhis_rules.py                # DFHIS 规则迁移
│   └── medical_contracts.py          # 医疗合约
│
└── tests/                             # 测试
    ├── test_core_capability.py
    ├── test_core_plugin.py
    ├── test_core_security.py
    └── test_core_workspace.py
```

### 2. 核心抽象层设计

#### 2.1 领域抽象模型

```python
# core/schema/domain.py

from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Dict

class DomainType(Enum):
    """支持的领域类型"""
    UNIVERSAL = "universal"      # 通用
    FRONTEND = "frontend"        # 前端开发
    BACKEND = "backend"          # 后端开发
    DEVOPS = "devops"            # DevOps
    SECURITY = "security"        # 安全
    TESTING = "testing"          # 测试
    DOCUMENTATION = "documentation"  # 文档
    MEDICAL = "medical"          # 医疗（保留）
    CUSTOM = "custom"            # 自定义

class PermissionLevel(Enum):
    """权限等级"""
    L0_PREVIEW = "L0"           # 本地预览或纯计算
    L1_READONLY = "L1"          # 只读证据
    L2_LOCAL_PERSIST = "L2"     # 本地持久化
    L3_CONTROLLED_DELIVERY = "L3"  # 受控本地交付
    L4_EXTERNAL_WRITE = "L4"    # 外部系统写入
    L5_PROD_CHANGE = "L5"       # 生产级变更

@dataclass
class DomainConfig:
    """领域配置"""
    domain_type: DomainType
    display_name: str
    description: str
    default_rule_pack: str
    supported_skill_packs: List[str]
    required_plugins: List[str]
    metadata: Dict[str, any] = None

# 预定义领域配置
UNIVERSAL_DOMAIN = DomainConfig(
    domain_type=DomainType.UNIVERSAL,
    display_name="通用开发",
    description="适用于所有类型的基础开发工作流",
    default_rule_pack="universal.default",
    supported_skill_packs=["universal.core"],
    required_plugins=["core-workflow"]
)

FRONTEND_DOMAIN = DomainConfig(
    domain_type=DomainType.FRONTEND,
    display_name="前端开发",
    description="专注于前端开发的工作流和规则",
    default_rule_pack="frontend.default",
    supported_skill_packs=[
        "frontend.core",
        "frontend.react",
        "frontend.vue",
        "frontend.angular"
    ],
    required_plugins=["core-workflow", "git-provider"]
)

BACKEND_DOMAIN = DomainConfig(
    domain_type=DomainType.BACKEND,
    display_name="后端开发",
    description="专注于后端开发的工作流和规则",
    default_rule_pack="backend.default",
    supported_skill_packs=[
        "backend.core",
        "backend.nodejs",
        "backend.python",
        "backend.java",
        "backend.go"
    ],
    required_plugins=["core-workflow", "git-provider", "database-provider"]
)

MEDICAL_DOMAIN = DomainConfig(
    domain_type=DomainType.MEDICAL,
    display_name="医疗系统",
    description="医疗HIS系统专用工作流",
    default_rule_pack="his.medical",
    supported_skill_packs=[
        "his.core",
        "his.medical",
        "his.insurance",
        "his.billing"
    ],
    required_plugins=["his-harness-core", "yunxiao", "his-engineering", "his-knowledge"]
)

DOMAIN_REGISTRY = {
    DomainType.UNIVERSAL: UNIVERSAL_DOMAIN,
    DomainType.FRONTEND: FRONTEND_DOMAIN,
    DomainType.BACKEND: BACKEND_DOMAIN,
    DomainType.MEDICAL: MEDICAL_DOMAIN
}

def get_domain_config(domain_type: DomainType) -> DomainConfig:
    """获取领域配置"""
    return DOMAIN_REGISTRY.get(domain_type, UNIVERSAL_DOMAIN)
```

#### 2.2 通用合约抽象

```python
# core/schema/contract.py

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

class CapabilityContract(ABC):
    """能力合约基类"""

    @abstractmethod
    def validate_input(self, input_data: Dict) -> ValidationResult:
        """验证输入数据"""
        pass

    @abstractmethod
    def execute(self, input_data: Dict, context: ExecutionContext) -> ExecutionResult:
        """执行能力"""
        pass

    @abstractmethod
    def get_required_permissions(self) -> List[PermissionLevel]:
        """获取所需权限等级"""
        pass

@dataclass
class ValidationResult:
    """验证结果"""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

@dataclass
class ExecutionContext:
    """执行上下文"""
    workspace_id: str
    user_id: str
    permissions: List[PermissionLevel]
    environment: Dict[str, Any] = field(default_factory=dict)
    audit_trail: List[str] = field(default_factory=list)

@dataclass
class ExecutionResult:
    """执行结果"""
    success: bool
    output: Dict[str, Any]
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
```

#### 2.3 插件管理器

```python
# core/plugin/manager.py

from typing import Dict, List, Optional
from pathlib import Path
from core.schema.domain import DomainConfig, DomainType

class PluginManager:
    """通用插件管理器"""

    def __init__(self, plugin_roots: List[str]):
        self.plugin_roots = [Path(root) for root in plugin_roots]
        self.loaded_plugins: Dict[str, PluginInfo] = {}
        self.capability_map: Dict[str, str] = {}  # capability_name -> plugin_name

    def load_all(self) -> LoadResult:
        """加载所有插件"""
        pass

    def get_plugin_for_capability(self, capability_name: str) -> Optional[PluginInfo]:
        """获取提供指定能力的插件"""
        return self.loaded_plugins.get(self.capability_map.get(capability_name))

    def validate_plugin_compatibility(self, plugin_name: str, domain: DomainType) -> ValidationResult:
        """验证插件与域名的兼容性"""
        pass

    def get_domain_plugins(self, domain: DomainType) -> List[PluginInfo]:
        """获取指定域名所需的插件列表"""
        domain_config = get_domain_config(domain)
        return [self.loaded_plugins.get(name) for name in domain_config.required_plugins]

@dataclass
class PluginInfo:
    """插件信息"""
    name: str
    version: str
    path: Path
    capabilities: List[str]
    enabled: bool
    domain_support: List[DomainType]
    metadata: Dict[str, Any]

@dataclass
class LoadResult:
    """加载结果"""
    success: bool
    loaded_count: int
    failed_count: int
    errors: List[str]
```

#### 2.4 工作空间管理器

```python
# core/workspace/manager.py

from typing import Dict, Optional
from pathlib import Path
from core.schema.domain import DomainType, DomainConfig

class WorkspaceManager:
    """工作空间管理器"""

    def __init__(self, workspaces_root: Path):
        self.workspaces_root = workspaces_root
        self.active_workspaces: Dict[str, Workspace] = {}

    def create_workspace(self, config: WorkspaceConfig) -> Workspace:
        """创建工作空间"""
        pass

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        """获取工作空间"""
        return self.active_workspaces.get(workspace_id)

    def switch_workspace(self, workspace_id: str) -> bool:
        """切换工作空间"""
        pass

    def delete_workspace(self, workspace_id: str) -> bool:
        """删除工作空间"""
        pass

@dataclass
class WorkspaceConfig:
    """工作空间配置"""
    workspace_id: str
    name: str
    domain: DomainType
    rule_pack: str
    skill_packs: List[str]
    plugins: List[str]
    permissions: Dict[str, PermissionLevel]
    metadata: Dict[str, Any]

@dataclass
class Workspace:
    """工作空间"""
    config: WorkspaceConfig
    path: Path
    state: WorkspaceState
    is_active: bool = False

@dataclass
class WorkspaceState:
    """工作空间状态"""
    created_at: str
    last_modified: str
    active_runs: List[str]
    audit_log: List[str]
```

### 3. 配置文件去医疗化

#### 3.1 通用规则包

```json
// config/rule_packs/universal.default.json
{
  "schema_version": "rule-pack.v2",
  "pack_id": "universal.default",
  "version": "1.0.0",
  "display_name": "通用默认规则包",
  "description": "适用于所有类型开发的基础规则",
  "domain": "universal",
  "extends": null,
  "hard_guards": {
    "no_secret_printing": true,
    "external_writes_default": "off",
    "real_status_transition_requires_confirmation": true,
    "real_commit_push_requires_confirmation": true,
    "destructive_git_forbidden": true,
    "publish_forbidden_by_default": true
  },
  "providers": {
    "requirement_sources": ["manual", "file", "github_issue", "jira", "trello"],
    "normalized_schema": [
      "source_type",
      "source_url",
      "external_id",
      "title",
      "description_text",
      "comments",
      "attachments",
      "images",
      "status",
      "assignee",
      "fetched_at",
      "warnings"
    ]
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
    }
  },
  "risk_assessment": {
    "high_risk_keywords": ["production", "deploy", "database", "delete", "remove", "force"],
    "medium_risk_keywords": ["config", "migration", "api", "external"],
    "low_risk_keywords": ["ui", "style", "typo", "format"]
  }
}
```

#### 3.2 前端规则包

```json
// config/rule_packs/frontend.default.json
{
  "schema_version": "rule-pack.v2",
  "pack_id": "frontend.default",
  "version": "1.0.0",
  "display_name": "前端开发默认规则包",
  "description": "专注于前端开发的规则和最佳实践",
  "domain": "frontend",
  "extends": "universal.default",
  "rules": {
    "component_naming": {
      "style": "PascalCase",
      "enforce": true
    },
    "file_naming": {
      "style": "kebab-case",
      "enforce": true
    },
    "style_guides": [
      {
        "name": "eslint",
        "config": "recommended",
        "required": true
      },
      {
        "name": "prettier",
        "config": "default",
        "required": true
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
  },
  "verification": {
    "default_commands": [
      "npm run lint",
      "npm run type-check",
      "npm run test:unit",
      "npm run build"
    ]
  }
}
```

#### 3.3 后端规则包

```json
// config/rule_packs/backend.default.json
{
  "schema_version": "rule-pack.v2",
  "pack_id": "backend.default",
  "version": "1.0.0",
  "display_name": "后端开发默认规则包",
  "description": "专注于后端开发的规则和最佳实践",
  "domain": "backend",
  "extends": "universal.default",
  "rules": {
    "api_design": {
      "style": "REST",
      "versioning": true,
      "documentation_required": true
    },
    "error_handling": {
      "style": "structured",
      "logging_required": true
    },
    "database": {
      "migration_required": true,
      "transaction_safety": true
    },
    "logging": {
      "style": "structured",
      "level_configured": true
    },
    "security": {
      "authentication_required": true,
      "authorization_checked": true,
      "input_validation": true
    }
  },
  "verification": {
    "default_commands": [
      "make lint",
      "make type-check",
      "make test",
      "make integration-test",
      "make security-scan"
    ]
  }
}
```

#### 3.4 HIS 医疗规则包（保留）

```json
// config/rule_packs/his.medical.json
{
  "schema_version": "rule-pack.v2",
  "pack_id": "his.medical",
  "version": "1.0.0",
  "display_name": "HIS 医疗系统规则包",
  "description": "医疗HIS系统专用规则，继承后端规则并添加医疗特定规则",
  "domain": "medical",
  "extends": "backend.default",
  "rules": {
    "patient_data_protection": {
      "level": "strict",
      "encryption_required": true,
      "audit_trail_required": true
    },
    "medical_billing_compliance": {
      "enabled": true,
      "audit_level": "detailed"
    },
    "insurance_integration": {
      "validation_required": true,
      "error_handling": "strict"
    },
    "pharmacy_rules": {
      "dosage_validation": true,
      "interaction_check": true
    }
  },
  "verification": {
    "default_commands": [
      "make medical-lint",
      "make patient-data-validation",
      "make insurance-protocol-test"
    ]
  },
  "hard_guards": {
    "patient_data_export_blocked": true,
    "production_access_2fa_required": true
  }
}
```

### 4. Schema 标准化

#### 4.1 通用需求治理 Schema

```json
// config/schemas/requirement_governance.v2.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "requirement-governance.v2.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "domain",
    "status",
    "can_modify",
    "can_complete_in_single_pass",
    "risk_level",
    "checks",
    "blockers",
    "missing_information",
    "unsupported_reasons",
    "required_capabilities",
    "evidence_refs"
  ],
  "properties": {
    "schema_version": {"const": "requirement-governance.v2"},
    "domain": {
      "type": "string",
      "enum": ["universal", "frontend", "backend", "devops", "security", "testing", "documentation", "medical", "custom"]
    },
    "status": {
      "enum": ["ready_for_local_change", "review_only", "blocked_needs_requirement", "blocked_needs_business_decision", "blocked_unsupported"]
    },
    "can_modify": {"type": "boolean"},
    "can_complete_in_single_pass": {"type": "boolean"},
    "risk_level": {
      "enum": ["low", "medium", "high", "critical", "unknown"]
    },
    "estimated_effort": {
      "type": "object",
      "properties": {
        "hours": {"type": "number"},
        "complexity": {"enum": ["trivial", "simple", "moderate", "complex", "very_complex"]}
      }
    },
    "checks": {
      "type": "array",
      "minItems": 8,
      "items": {
        "type": "object",
        "properties": {
          "name": {"type": "string"},
          "status": {"enum": ["pass", "warning", "blocked", "not_applicable"]},
          "summary": {"type": "string"},
          "evidence_refs": {"type": "array"},
          "blockers": {"type": "array"},
          "warnings": {"type": "array"}
        }
      }
    },
    "blockers": {"type": "array", "items": {"type": "string"}},
    "missing_information": {"type": "array", "items": {"type": "string"}},
    "unsupported_reasons": {"type": "array", "items": {"type": "string"}},
    "required_capabilities": {"type": "array", "items": {"type": "string"}},
    "evidence_refs": {"type": "array", "items": {"type": "object"}},
    "metadata": {
      "type": "object",
      "additionalProperties": true
    }
  }
}
```

#### 4.2 通用代码审查 Schema

```json
// config/schemas/code_review.v2.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "code-review.v2.json",
  "type": "object",
  "required": [
    "schema_version",
    "domain",
    "review_type",
    "files_reviewed",
    "findings",
    "overall_rating",
    "recommendation"
  ],
  "properties": {
    "schema_version": {"const": "code-review.v2"},
    "domain": {
      "type": "string",
      "enum": ["universal", "frontend", "backend", "devops", "security", "testing", "documentation", "medical"]
    },
    "review_type": {
      "enum": ["incremental", "full", "security", "performance", "accessibility"]
    },
    "files_reviewed": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "path": {"type": "string"},
          "lines_added": {"type": "number"},
          "lines_removed": {"type": "number"},
          "language": {"type": "string"}
        }
      }
    },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "severity": {"enum": ["critical", "high", "medium", "low", "info"]},
          "category": {"type": "string"},
          "file": {"type": "string"},
          "line": {"type": "number"},
          "description": {"type": "string"},
          "suggestion": {"type": "string"},
          "references": {"type": "array"}
        }
      }
    },
    "overall_rating": {
      "type": "number",
      "minimum": 1,
      "maximum": 5
    },
    "recommendation": {
      "enum": ["approve", "approve_with_suggestions", "request_changes", "block"]
    },
    "metrics": {
      "type": "object",
      "properties": {
        "code_coverage": {"type": "number"},
        "complexity_score": {"type": "number"},
        "duplication_percentage": {"type": "number"}
      }
    }
  }
}
```

#### 4.3 通用部署 Schema

```json
// config/schemas/deployment.v2.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "deployment.v2.json",
  "type": "object",
  "required": [
    "schema_version",
    "deployment_type",
    "environment",
    "components",
    "pre_checks",
    "post_checks",
    "rollback_plan"
  ],
  "properties": {
    "schema_version": {"const": "deployment.v2"},
    "deployment_type": {
      "enum": ["standard", "blue_green", "canary", "rolling", "custom"]
    },
    "environment": {
      "enum": ["development", "staging", "production"]
    },
    "components": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": {"type": "string"},
          "version": {"type": "string"},
          "type": {"type": "string"},
          "dependencies": {"type": "array"}
        }
      }
    },
    "pre_checks": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "check_type": {"type": "string"},
          "command": {"type": "string"},
          "timeout": {"type": "number"},
          "required": {"type": "boolean"}
        }
      }
    },
    "post_checks": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "check_type": {"type": "string"},
          "endpoint": {"type": "string"},
          "expected_status": {"type": "number"},
          "timeout": {"type": "number"}
        }
      }
    },
    "rollback_plan": {
      "type": "object",
      "properties": {
        "strategy": {"type": "string"},
        "triggers": {"type": "array"},
        "steps": {"type": "array"}
      }
    },
    "metadata": {
      "type": "object",
      "additionalProperties": true
    }
  }
}
```

### 5. 配置系统设计

#### 5.1 分层配置加载

```python
# core/config/settings.py

from typing import Dict, Optional
from pathlib import Path
from core.schema.domain import DomainType

class ConfigurationLoader:
    """分层配置加载器"""

    CONFIG_LAYERS = [
        "system",      # 系统级配置
        "workspace",   # 工作空间配置
        "project",     # 项目配置
        "user",        # 用户配置
        "run"          # 运行时配置
    ]

    def __init__(self, base_config_path: Path):
        self.base_config_path = base_config_path
        self.config_cache: Dict[str, Dict] = {}

    def load_config(self, domain: DomainType, workspace_id: Optional[str] = None) -> ResolvedConfig:
        """加载配置并解析层级"""
        configs = {}

        # 按优先级加载各层配置
        for layer in self.CONFIG_LAYERS:
            layer_config = self._load_layer(layer, domain, workspace_id)
            if layer_config:
                configs[layer] = layer_config

        return self._resolve_configs(configs)

    def _load_layer(self, layer: str, domain: DomainType, workspace_id: Optional[str]) -> Optional[Dict]:
        """加载单层配置"""
        pass

    def _resolve_configs(self, configs: Dict[str, Dict]) -> ResolvedConfig:
        """解析并合并配置"""
        pass

@dataclass
class ResolvedConfig:
    """解析后的配置"""
    domain: DomainType
    rule_pack: Dict
    capabilities: Dict
    permissions: Dict
    verification: Dict
    metadata: Dict[str, Any]
    source_provenance: Dict[str, str]
```

### 6. API 设计

#### 6.1 核心服务 API

```python
# core/services/workflow_service.py

class WorkflowService:
    """工作流服务"""

    def __init__(self, plugin_manager: PluginManager, workspace_manager: WorkspaceManager):
        self.plugin_manager = plugin_manager
        self.workspace_manager = workspace_manager

    def create_workflow(self, request: WorkflowRequest) -> Workflow:
        """创建工作流"""
        pass

    def execute_workflow(self, workflow_id: str, context: ExecutionContext) -> WorkflowResult:
        """执行工作流"""
        pass

    def get_workflow_status(self, workflow_id: str) -> WorkflowStatus:
        """获取工作流状态"""
        pass

    def cancel_workflow(self, workflow_id: str) -> bool:
        """取消工作流"""
        pass

@dataclass
class WorkflowRequest:
    """工作流请求"""
    domain: DomainType
    task_type: str
    input_data: Dict[str, Any]
    requested_capabilities: List[str]
    workspace_id: Optional[str] = None
    metadata: Dict[str, Any] = None

@dataclass
class Workflow:
    """工作流"""
    workflow_id: str
    request: WorkflowRequest
    status: WorkflowStatus
    steps: List[WorkflowStep]
    created_at: str
    updated_at: str

@dataclass
class WorkflowStep:
    """工作流步骤"""
    step_id: str
    name: str
    capability_name: str
    status: str
    input_data: Dict[str, Any]
    output_data: Optional[Dict[str, Any]]
    errors: List[str]
    started_at: Optional[str]
    completed_at: Optional[str]
```

#### 6.2 CLI 入口设计

```python
# tools/universal_cli.py

import argparse
from core.services.workflow_service import WorkflowService
from core.config.settings import ConfigurationLoader

def create_parser() -> argparse.ArgumentParser:
    """创建命令行解析器"""
    parser = argparse.ArgumentParser(description="Universal AI Workflow CLI")

    # 全局选项
    parser.add_argument("--domain", help="指定领域 (universal/frontend/backend/devops/medical)")
    parser.add_argument("--workspace", help="指定工作空间")
    parser.add_argument("--config", help="指定配置文件路径")
    parser.add_argument("--verbose", action="store_true", help="详细输出")

    # 子命令
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # workflow 命令
    workflow_parser = subparsers.add_parser("workflow", help="工作流操作")
    workflow_subparsers = workflow_parser.add_subparsers(dest="workflow_action")

    # workflow create
    create_parser = workflow_subparsers.add_parser("create", help="创建工作流")
    create_parser.add_argument("--task", required=True, help="任务描述")
    create_parser.add_argument("--type", required=True, help="任务类型")
    create_parser.add_argument("--input", help="输入数据文件")
    create_parser.add_argument("--capabilities", nargs="+", help="需要的能力")

    # workflow execute
    execute_parser = workflow_subparsers.add_parser("execute", help="执行工作流")
    execute_parser.add_argument("--workflow-id", required=True, help="工作流ID")

    # workspace 命令
    workspace_parser = subparsers.add_parser("workspace", help="工作空间操作")
    workspace_subparsers = workspace_parser.add_subparsers(dest="workspace_action")

    # workspace create
    ws_create_parser = workspace_subparsers.add_parser("create", help="创建工作空间")
    ws_create_parser.add_argument("--name", required=True, help="工作空间名称")
    ws_create_parser.add_argument("--domain", required=True, help="领域类型")
    ws_create_parser.add_argument("--rule-pack", help="规则包")
    ws_create_parser.add_argument("--skill-packs", nargs="+", help="技能包列表")

    # workspace list
    ws_subparsers.add_parser("list", help="列出工作空间")

    # workspace switch
    ws_switch_parser = workspace_subparsers.add_parser("switch", help="切换工作空间")
    ws_switch_parser.add_argument("--workspace-id", required=True, help="工作空间ID")

    return parser

def main():
    parser = create_parser()
    args = parser.parse_args()

    # 初始化服务
    config_loader = ConfigurationLoader(args.config if args.config else Path("config"))
    plugin_manager = PluginManager(["/path/to/plugins"])
    workspace_manager = WorkspaceManager(Path("workspaces"))
    workflow_service = WorkflowService(plugin_manager, workspace_manager)

    # 处理命令
    if args.command == "workflow":
        handle_workflow_command(args, workflow_service)
    elif args.command == "workspace":
        handle_workspace_command(args, workspace_manager)
    else:
        parser.print_help()

def handle_workflow_command(args: argparse.Namespace, service: WorkflowService):
    """处理工作流命令"""
    if args.workflow_action == "create":
        # 创建工作流逻辑
        pass
    elif args.workflow_action == "execute":
        # 执行工作流逻辑
        pass

def handle_workspace_command(args: argparse.Namespace, manager: WorkspaceManager):
    """处理工作空间命令"""
    if args.workspace_action == "create":
        # 创建工作空间逻辑
        pass
    elif args.workspace_action == "list":
        # 列出工作空间逻辑
        pass
    elif args.workspace_action == "switch":
        # 切换工作空间逻辑
        pass

if __name__ == "__main__":
    main()
```

### 7. 测试策略

#### 7.1 单元测试

```python
# tests/test_core_domain.py

import pytest
from core.schema.domain import DomainType, get_domain_config, UNIVERSAL_DOMAIN, FRONTEND_DOMAIN

def test_domain_config_universal():
    """测试通用领域配置"""
    config = get_domain_config(DomainType.UNIVERSAL)
    assert config.domain_type == DomainType.UNIVERSAL
    assert config.default_rule_pack == "universal.default"
    assert "universal.core" in config.supported_skill_packs

def test_domain_config_frontend():
    """测试前端领域配置"""
    config = get_domain_config(DomainType.FRONTEND)
    assert config.domain_type == DomainType.FRONTEND
    assert config.default_rule_pack == "frontend.default"
    assert "frontend.react" in config.supported_skill_packs
    assert "frontend.vue" in config.supported_skill_packs

def test_domain_config_medical():
    """测试医疗领域配置（保留）"""
    config = get_domain_config(DomainType.MEDICAL)
    assert config.domain_type == DomainType.MEDICAL
    assert "his-harness-core" in config.required_plugins
    assert "yunxiao" in config.required_plugins

def test_domain_registry_consistency():
    """测试领域注册表一致性"""
    from core.schema.domain import DOMAIN_REGISTRY
    assert DomainType.UNIVERSAL in DOMAIN_REGISTRY
    assert DomainType.FRONTEND in DOMAIN_REGISTRY
    assert DomainType.BACKEND in DOMAIN_REGISTRY
    assert DomainType.MEDICAL in DOMAIN_REGISTRY
```

#### 7.2 集成测试

```python
# tests/integration/test_workflow_integration.py

import pytest
from pathlib import Path
from core.services.workflow_service import WorkflowService
from core.plugin.manager import PluginManager
from core.workspace.manager import WorkspaceManager

@pytest.fixture
def workflow_service():
    """创建工作流服务实例"""
    plugin_manager = PluginManager(["./test_plugins"])
    workspace_manager = WorkspaceManager(Path("./test_workspaces"))
    return WorkflowService(plugin_manager, workspace_manager)

def test_create_and_execute_simple_workflow(workflow_service):
    """测试创建和执行简单工作流"""
    from core.schema.domain import DomainType
    from core.services.workflow_service import WorkflowRequest

    request = WorkflowRequest(
        domain=DomainType.UNIVERSAL,
        task_type="code_review",
        input_data={"code_path": "./test_code"},
        requested_capabilities=["code.analyze", "code.review"]
    )

    workflow = workflow_service.create_workflow(request)
    assert workflow.status == "created"

    # 执行工作流
    from core.services.workflow_service import ExecutionContext
    context = ExecutionContext(
        workspace_id="test",
        user_id="test_user",
        permissions=["L0", "L1"]
    )

    result = workflow_service.execute_workflow(workflow.workflow_id, context)
    assert result.success or len(result.errors) > 0
```

### 8. 迁移路径

#### 8.1 HIS 兼容层

```python
# legacy/his_adapter.py

from typing import Dict, Optional
from core.schema.domain import DomainType
from core.services.workflow_service import WorkflowService

class HISAdapter:
    """HIS 系统兼容适配器"""

    def __init__(self, workflow_service: WorkflowService):
        self.workflow_service = workflow_service
        self.his_domain = DomainType.MEDICAL

    def migrate_requirement(self, his_requirement: Dict) -> Dict:
        """迁移 HIS 需求格式到通用格式"""
        return {
            "domain": self.his_domain,
            "task_type": "requirement_analysis",
            "input_data": {
                "source_type": his_requirement.get("source", "yunxiao"),
                "external_id": his_requirement.get("dfhis_id"),
                "title": his_requirement.get("title"),
                "description": his_requirement.get("demand_text"),
                "comments": his_requirement.get("comments", []),
                "attachments": his_requirement.get("attachments", [])
            },
            "requested_capabilities": ["requirement.govern", "code.analyze"],
            "metadata": {
                "his_original_data": his_requirement,
                "is_his_migration": True
            }
        }

    def adapt_response(self, universal_response: Dict) -> Dict:
        """将通用响应适配回 HIS 格式"""
        if universal_response.get("success"):
            return {
                "his_governance_result": {
                    "status": "ready_for_local_change",
                    "can_modify": True,
                    "can_complete_in_single_pass": True,
                    "checks": universal_response.get("checks", []),
                    "blockers": [],
                    "risk_level": universal_response.get("risk_level", "low")
                }
            }
        else:
            return {
                "his_governance_result": {
                    "status": "blocked_needs_requirement",
                    "can_modify": False,
                    "blockers": universal_response.get("errors", [])
                }
            }
```

### 9. 交付物清单

| 交付物 | 描述 | 状态 |
|--------|------|------|
| 核心架构代码 | domain.py, contract.py, plugin/ 目录 | ⬜ |
| 配置系统 | settings.py, config/ 目录 | ⬜ |
| 工作空间管理器 | workspace/ 目录 | ⬜ |
| 通用规则包 | universal.default.json, frontend.default.json | ⬜ |
| Schema 定义 | requirement_governance.v2.json 等 | ⬜ |
| CLI 工具 | universal_cli.py | ⬜ |
| HIS 兼容层 | legacy/his_adapter.py | ⬜ |
| 单元测试 | tests/ 目录 | ⬜ |
| 集成测试 | tests/integration/ 目录 | ⬜ |
| 架构文档 | 本文档 | ✅ |

### 10. 风险与缓解措施

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 破坏现有 HIS 功能 | 高 | 保留完整兼容层，渐进式迁移 |
| 配置迁移复杂 | 中 | 提供自动化迁移脚本 |
| 向后兼容性 | 高 | 保持所有现有 API 接口 |
| 测试覆盖不足 | 中 | 增加集成测试和回归测试 |

---

**下一步行动**：进入阶段1的代码实现阶段