# 阶段2：技能包系统设计与实现文档

## 📋 文档信息

| 项目 | 内容 |
|------|------|
| **阶段** | 阶段2：技能包系统 |
| **版本** | v1.0.0 |
| **创建日期** | 2026-08-15 |
| **状态** | 设计中 |
| **预计工期** | 3-4 周 |

## 🎯 阶段目标

设计和实现可扩展的技能包系统，支持技能包的发现、安装、管理、版本控制和依赖解析。

## 🏗️ 架构设计

### 1. 技能包系统架构

```
skill-pack-system/
├── core/                              # 核心系统
│   ├── __init__.py
│   ├── manager.py                    # 技能包管理器
│   ├── registry.py                   # 技能包注册表
│   ├── installer.py                  # 安装器
│   ├── loader.py                     # 加载器
│   ├── resolver.py                   # 依赖解析器
│   └── validator.py                  # 验证器
│
├── schemas/                           # Schema 定义
│   ├── __init__.py
│   ├── skill_pack.v1.py             # 技能包 Schema
│   ├── skill.v1.py                  # 技能 Schema
│   ├── rule_template.v1.py          # 规则模板 Schema
│   └── metadata.v1.py               # 元数据 Schema
│
├── marketplace/                       # 技能包市场
│   ├── __init__.py
│   ├── catalog.py                   # 目录服务
│   ├── search.py                    # 搜索服务
│   ├── rating.py                    # 评分系统
│   └── stats.py                     # 统计服务
│
├── repository/                        # 仓库管理
│   ├── __init__.py
│   ├── local.py                     # 本地仓库
│   ├── remote.py                    # 远程仓库
│   ├── cache.py                     # 缓存管理
│   └── index.py                     # 索引管理
│
├── packaging/                         # 打包工具
│   ├── __init__.py
│   ├── builder.py                   # 打包构建器
│   ├── packager.py                  # 打包工具
│   ├── signer.py                    # 签名工具
│   └── compressor.py                # 压缩工具
│
├── cli/                               # CLI 工具
│   ├── __init__.py
│   ├── skill_pack.py                # 主 CLI 入口
│   ├── commands/                    # 命令实现
│   │   ├── __init__.py
│   │   ├── install.py
│   │   ├── uninstall.py
│   │   ├── list.py
│   │   ├── search.py
│   │   ├── info.py
│   │   ├── update.py
│   │   └── publish.py
│   └── utils/                       # 工具函数
│
├── skills/                            # 技能包存储
│   ├── universal/
│   │   ├── core/
│   │   ├── analysis/
│   │   └── review/
│   │
│   ├── frontend/
│   │   ├── react/
│   │   ├── vue/
│   │   ├── angular/
│   │   └── typescript/
│   │
│   ├── backend/
│   │   ├── nodejs/
│   │   ├── python/
│   │   ├── java/
│   │   └── go/
│   │
│   ├── devops/
│   │   ├── kubernetes/
│   │   ├── docker/
│   │   └── ci-cd/
│   │
│   ├── security/
│   │   ├── sast/
│   │   ├── dependency/
│   │   └── secret/
│   │
│   └── testing/
│       ├── unit/
│       ├── integration/
│       └── e2e/
│
├── cache/                             # 缓存目录
│   ├── index/                        # 索引缓存
│   └── packages/                     # 包缓存
│
├── config/                            # 配置文件
│   ├── skill_pack.config.json       # 技能包配置
│   ├── marketplace.config.json      # 市场配置
│   └── registry.config.json         # 注册表配置
│
└── tests/                             # 测试
    ├── test_manager.py
    ├── test_installer.py
    ├── test_resolver.py
    ├── test_marketplace.py
    └── fixtures/                     # 测试固件
```

### 2. 核心数据结构

#### 2.1 技能包元数据

```python
# schemas/skill_pack.v1.py

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum
from datetime import datetime

class SkillPackStatus(Enum):
    """技能包状态"""
    DRAFT = "draft"                # 草稿
    PUBLISHED = "published"        # 已发布
    DEPRECATED = "deprecated"      # 已弃用
    ARCHIVED = "archived"          # 已归档

class SkillPackType(Enum):
    """技能包类型"""
    CORE = "core"                  # 核心技能包
    DOMAIN = "domain"              # 领域技能包
    FRAMEWORK = "framework"        # 框架技能包
    TOOL = "tool"                  # 工具技能包
    CUSTOM = "custom"              # 自定义技能包

class SkillPackCategory(Enum):
    """技能包分类"""
    FRONTEND = "frontend"
    BACKEND = "backend"
    DEVOPS = "devops"
    SECURITY = "security"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    PERFORMANCE = "performance"
    ACCESSIBILITY = "accessibility"
    CUSTOM = "custom"

@dataclass
class SkillPackMetadata:
    """技能包元数据"""
    schema_version: str = "skill-pack.v1"
    pack_id: str = ""
    name: str = ""
    version: str = "1.0.0"
    display_name: str = ""
    description: str = ""
    pack_type: SkillPackType = SkillPackType.CUSTOM
    category: SkillPackCategory = SkillPackCategory.CUSTOM
    status: SkillPackStatus = SkillPackStatus.DRAFT

    # 作者信息
    author: str = ""
    author_email: str = ""
    organization: str = ""
    license: str = "MIT"

    # 兼容性
    platform_version_min: str = "1.0.0"
    platform_version_max: str = "99.0.0"
    python_version_min: str = "3.8"

    # 依赖
    dependencies: Dict[str, str] = field(default_factory=dict)  # pack_id: version
    skill_dependencies: List[str] = field(default_factory=list)
    plugin_dependencies: List[str] = field(default_factory=list)

    # 统计信息
    downloads: int = 0
    rating_average: float = 0.0
    rating_count: int = 0

    # 时间戳
    created_at: str = ""
    updated_at: str = ""
    published_at: str = ""

    # 校验和
    checksum_sha256: str = ""

    # 标签和关键词
    tags: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)

    # 其他元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "schema_version": self.schema_version,
            "pack_id": self.pack_id,
            "name": self.name,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "pack_type": self.pack_type.value,
            "category": self.category.value,
            "status": self.status.value,
            "author": self.author,
            "author_email": self.author_email,
            "organization": self.organization,
            "license": self.license,
            "platform_version_min": self.platform_version_min,
            "platform_version_max": self.platform_version_max,
            "python_version_min": self.python_version_min,
            "dependencies": self.dependencies,
            "skill_dependencies": self.skill_dependencies,
            "plugin_dependencies": self.plugin_dependencies,
            "downloads": self.downloads,
            "rating_average": self.rating_average,
            "rating_count": self.rating_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "published_at": self.published_at,
            "checksum_sha256": self.checksum_sha256,
            "tags": self.tags,
            "keywords": self.keywords,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "SkillPackMetadata":
        """从字典创建"""
        return cls(
            schema_version=data.get("schema_version", "skill-pack.v1"),
            pack_id=data.get("pack_id", ""),
            name=data.get("name", ""),
            version=data.get("version", "1.0.0"),
            display_name=data.get("display_name", ""),
            description=data.get("description", ""),
            pack_type=SkillPackType(data.get("pack_type", "custom")),
            category=SkillPackCategory(data.get("category", "custom")),
            status=SkillPackStatus(data.get("status", "draft")),
            author=data.get("author", ""),
            author_email=data.get("author_email", ""),
            organization=data.get("organization", ""),
            license=data.get("license", "MIT"),
            platform_version_min=data.get("platform_version_min", "1.0.0"),
            platform_version_max=data.get("platform_version_max", "99.0.0"),
            python_version_min=data.get("python_version_min", "3.8"),
            dependencies=data.get("dependencies", {}),
            skill_dependencies=data.get("skill_dependencies", []),
            plugin_dependencies=data.get("plugin_dependencies", []),
            downloads=data.get("downloads", 0),
            rating_average=data.get("rating_average", 0.0),
            rating_count=data.get("rating_count", 0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            published_at=data.get("published_at", ""),
            checksum_sha256=data.get("checksum_sha256", ""),
            tags=data.get("tags", []),
            keywords=data.get("keywords", []),
            metadata=data.get("metadata", {})
        )
```

#### 2.2 技能定义

```python
# schemas/skill.v1.py

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Callable
from enum import Enum

class SkillTriggerType(Enum):
    """技能触发类型"""
    AUTOMATIC = "automatic"      # 自动触发
    USER_INITIATED = "user"     # 用户发起
    CONTEXTUAL = "contextual"   # 上下文触发
    SCHEDULED = "scheduled"     # 定时触发

class SkillPermission(Enum):
    """技能权限要求"""
    NONE = "none"                # 无需权限
    L0 = "L0"                    # 预览权限
    L1 = "L1"                    # 只读权限
    L2 = "L2"                    # 本地持久化
    L3 = "L3"                    # 受控交付
    L4 = "L4"                    # 外部写入
    L5 = "L5"                    # 生产变更

@dataclass
class SkillDefinition:
    """技能定义"""
    skill_id: str = ""
    name: str = ""
    display_name: str = ""
    description: str = ""
    version: str = "1.0.0"

    # 触发条件
    trigger_type: SkillTriggerType = SkillTriggerType.USER_INITIATED
    trigger_patterns: List[str] = field(default_factory=list)
    trigger_keywords: List[str] = field(default_factory=list)

    # 权限
    required_permission: SkillPermission = SkillPermission.NONE
    capability_requirements: List[str] = field(default_factory=list)

    # 执行
    entrypoint: str = ""
    execution_mode: str = "sync"  # sync, async, streaming
    timeout_seconds: int = 300

    # 输入输出
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    examples: List[Dict[str, Any]] = field(default_factory=list)

    # 配置
    config_schema: Dict[str, Any] = field(default_factory=dict)
    default_config: Dict[str, Any] = field(default_factory=dict)

    # 依赖
    depends_on: List[str] = field(default_factory=list)  # 其他技能ID
    provides: List[str] = field(default_factory=list)     # 提供的能力

    # 验证
    test_cases: List[Dict[str, Any]] = field(default_factory=list)
    validation_rules: List[Dict[str, Any]] = field(default_factory=list)

    # 文档
    documentation: str = ""
    references: List[str] = field(default_factory=list)
    examples_urls: List[str] = field(default_factory=list)

    # 兼容性
    compatible_platforms: List[str] = field(default_factory=list)
    compatible_languages: List[str] = field(default_factory=list)

    # 元数据
    tags: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate_input(self, input_data: Dict) -> ValidationResult:
        """验证输入数据"""
        pass

    def validate_config(self, config: Dict) -> ValidationResult:
        """验证配置"""
        pass

@dataclass
class ValidationResult:
    """验证结果"""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

@dataclass
class SkillExecutionResult:
    """技能执行结果"""
    success: bool
    skill_id: str
    output: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    execution_time_seconds: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
```

#### 2.3 规则模板

```python
# schemas/rule_template.v1.py

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum

class RuleSeverity(Enum):
    """规则严重级别"""
    INFO = "info"          # 信息
    SUGGESTION = "suggestion"  # 建议
    WARNING = "warning"    # 警告
    ERROR = "error"        # 错误
    CRITICAL = "critical"  # 严重

class RuleCategory(Enum):
    """规则分类"""
    CODE_STYLE = "code_style"
    CODE_QUALITY = "code_quality"
    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTENABILITY = "maintainability"
    ACCESSIBILITY = "accessibility"
    TEST_COVERAGE = "test_coverage"
    DOCUMENTATION = "documentation"
    DOMAIN_SPECIFIC = "domain_specific"

@dataclass
class RuleTemplate:
    """规则模板"""
    rule_id: str = ""
    name: str = ""
    display_name: str = ""
    description: str = ""
    category: RuleCategory = RuleCategory.CODE_QUALITY
    severity: RuleSeverity = RuleSeverity.WARNING

    # 规则定义
    rule_type: str = "pattern"  # pattern, custom, ai, composite
    pattern: Optional[str] = None
    custom_check: Optional[str] = None
    ai_prompt: Optional[str] = None
    composite_rules: List[str] = field(default_factory=list)

    # 作用范围
    file_patterns: List[str] = field(default_factory=list)
    language_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)

    # 自动修复
    auto_fixable: bool = False
    auto_fix_command: Optional[str] = None
    auto_fix_script: Optional[str] = None

    # 解释和文档
    explanation: str = ""
    examples: List[Dict[str, str]] = field(default_factory=list)
    references: List[str] = field(default_factory=list)

    # 配置
    config_schema: Optional[Dict] = None
    default_config: Dict[str, Any] = field(default_factory=dict)

    # 兼容性
    compatible_languages: List[str] = field(default_factory=list)
    compatible_frameworks: List[str] = field(default_factory=list)

    # 元数据
    tags: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def evaluate(self, content: str, context: Dict) -> RuleEvaluationResult:
        """评估规则"""
        pass

@dataclass
class RuleEvaluationResult:
    """规则评估结果"""
    rule_id: str
    passed: bool
    violations: List[RuleViolation] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class RuleViolation:
    """规则违规信息"""
    file_path: str
    line_number: int
    column_number: Optional[int]
    severity: RuleSeverity
    message: str
    suggestion: Optional[str]
    context: Optional[str]
```

### 3. 技能包管理器

```python
# core/manager.py

from typing import Dict, List, Optional, Tuple
from pathlib import Path
from datetime import datetime
from schemas.skill_pack.v1 import SkillPackMetadata, SkillPackStatus, SkillPackType
from schemas.skill.v1 import SkillDefinition
from schemas.rule_template.v1 import RuleTemplate
from .registry import SkillPackRegistry
from .installer import SkillPackInstaller
from .resolver import DependencyResolver
from .validator import SkillPackValidator

class SkillPackManager:
    """技能包管理器"""

    def __init__(
        self,
        registry: SkillPackRegistry,
        installer: SkillPackInstaller,
        resolver: DependencyResolver,
        validator: SkillPackValidator,
        skills_root: Path
    ):
        self.registry = registry
        self.installer = installer
        self.resolver = resolver
        self.validator = validator
        self.skills_root = skills_root
        self.installed_packs: Dict[str, SkillPackMetadata] = {}
        self._load_installed_packs()

    def _load_installed_packs(self):
        """加载已安装的技能包"""
        pass

    def install(
        self,
        pack_id: str,
        version: Optional[str] = None,
        force: bool = False,
        skip_dependencies: bool = False
    ) -> InstallationResult:
        """安装技能包"""
        # 1. 获取技能包元数据
        metadata = self.registry.get_metadata(pack_id, version)
        if not metadata:
            return InstallationResult(
                success=False,
                pack_id=pack_id,
                errors=[f"Skill pack {pack_id} not found"]
            )

        # 2. 验证技能包
        validation = self.validator.validate(metadata)
        if not validation.is_valid:
            return InstallationResult(
                success=False,
                pack_id=pack_id,
                errors=validation.errors,
                warnings=validation.warnings
            )

        # 3. 解析依赖
        if not skip_dependencies:
            resolution = self.resolver.resolve(metadata)
            if not resolution.is_resolvable:
                return InstallationResult(
                    success=False,
                    pack_id=pack_id,
                    errors=resolution.errors,
                    dependencies=resolution.resolved_dependencies
                )

            # 安装依赖
            for dep_pack_id, dep_version in resolution.resolved_dependencies.items():
                dep_result = self.install(dep_pack_id, dep_version, force)
                if not dep_result.success:
                    return dep_result

        # 4. 检查是否已安装
        if pack_id in self.installed_packs and not force:
            return InstallationResult(
                success=False,
                pack_id=pack_id,
                errors=["Skill pack already installed"],
                warnings=["Use --force to reinstall"]
            )

        # 5. 执行安装
        install_result = self.installer.install(metadata)
        if install_result.success:
            self.installed_packs[pack_id] = metadata
            self.registry.update_stats(pack_id, downloads_increment=1)

        return install_result

    def uninstall(
        self,
        pack_id: str,
        force: bool = False,
        skip_dependency_check: bool = False
    ) -> UninstallationResult:
        """卸载技能包"""
        if pack_id not in self.installed_packs:
            return UninstallationResult(
                success=False,
                pack_id=pack_id,
                errors=["Skill pack not installed"]
            )

        # 检查依赖关系
        if not skip_dependency_check:
            dependents = self.resolver.get_dependents(pack_id)
            if dependents and not force:
                return UninstallationResult(
                    success=False,
                    pack_id=pack_id,
                    errors=[
                        "Cannot uninstall skill pack with dependencies",
                        f"Required by: {', '.join(dependents)}"
                    ],
                    warnings=["Use --force to force uninstall"]
                )

        # 执行卸载
        uninstall_result = self.installer.uninstall(pack_id)
        if uninstall_result.success:
            del self.installed_packs[pack_id]

        return uninstall_result

    def list_installed(
        self,
        category: Optional[str] = None,
        status: Optional[SkillPackStatus] = None
    ) -> List[SkillPackMetadata]:
        """列出已安装的技能包"""
        result = list(self.installed_packs.values())

        if category:
            result = [p for p in result if p.category.value == category]

        if status:
            result = [p for p in result if p.status == status]

        return result

    def list_available(
        self,
        category: Optional[str] = None,
        search_query: Optional[str] = None,
        tags: Optional[List[str]] = None,
        min_rating: Optional[float] = None,
        sort_by: str = "downloads"
    ) -> List[SkillPackMetadata]:
        """列出可用的技能包"""
        return self.registry.list_available(
            category=category,
            search_query=search_query,
            tags=tags,
            min_rating=min_rating,
            sort_by=sort_by
        )

    def get_info(self, pack_id: str) -> Optional[SkillPackInfo]:
        """获取技能包详细信息"""
        metadata = self.installed_packs.get(pack_id)
        if not metadata:
            metadata = self.registry.get_metadata(pack_id)
            if not metadata:
                return None

        skills = self.installer.get_installed_skills(pack_id)
        rules = self.installer.get_installed_rules(pack_id)

        return SkillPackInfo(
            metadata=metadata,
            skills=skills,
            rules=rules,
            dependencies=metadata.dependencies,
            dependents=self.resolver.get_dependents(pack_id)
        )

    def update(
        self,
        pack_id: str,
        to_version: Optional[str] = None,
        dry_run: bool = False
    ) -> UpdateResult:
        """更新技能包"""
        current_metadata = self.installed_packs.get(pack_id)
        if not current_metadata:
            return UpdateResult(
                success=False,
                pack_id=pack_id,
                errors=["Skill pack not installed"]
            )

        # 获取最新版本
        latest_metadata = self.registry.get_latest_metadata(pack_id)
        if not latest_metadata:
            return UpdateResult(
                success=False,
                pack_id=pack_id,
                errors=["Latest version not found"]
            )

        if to_version:
            latest_metadata = self.registry.get_metadata(pack_id, to_version)

        # 检查版本差异
        if latest_metadata.version == current_metadata.version:
            return UpdateResult(
                success=True,
                pack_id=pack_id,
                current_version=current_metadata.version,
                new_version=latest_metadata.version,
                changes=[],
                message="Already up to date"
            )

        # Dry run
        if dry_run:
            changes = self._get_version_changes(current_metadata, latest_metadata)
            return UpdateResult(
                success=True,
                pack_id=pack_id,
                current_version=current_metadata.version,
                new_version=latest_metadata.version,
                changes=changes,
                message="Dry run successful"
            )

        # 卸载旧版本
        uninstall_result = self.uninstall(pack_id, force=True)
        if not uninstall_result.success:
            return UpdateResult(
                success=False,
                pack_id=pack_id,
                errors=uninstall_result.errors
            )

        # 安装新版本
        install_result = self.install(pack_id, latest_metadata.version)
        if not install_result.success:
            return UpdateResult(
                success=False,
                pack_id=pack_id,
                errors=install_result.errors
            )

        changes = self._get_version_changes(current_metadata, latest_metadata)
        return UpdateResult(
            success=True,
            pack_id=pack_id,
            current_version=current_metadata.version,
            new_version=latest_metadata.version,
            changes=changes,
            message="Update successful"
        )

    def _get_version_changes(
        self,
        current: SkillPackMetadata,
        latest: SkillPackMetadata
    ) -> List[ChangeInfo]:
        """获取版本变更信息"""
        changes = []

        if current.version != latest.version:
            changes.append(ChangeInfo(
                type="version",
                description=f"Version {current.version} -> {latest.version}"
            ))

        # 比较技能
        current_skills = set(current.skill_dependencies)
        latest_skills = set(latest.skill_dependencies)

        new_skills = latest_skills - current_skills
        removed_skills = current_skills - latest_skills

        if new_skills:
            changes.append(ChangeInfo(
                type="skill_addition",
                description=f"Added skills: {', '.join(new_skills)}"
            ))

        if removed_skills:
            changes.append(ChangeInfo(
                type="skill_removal",
                description=f"Removed skills: {', '.join(removed_skills)}"
            ))

        return changes

    def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None
    ) -> SearchResult:
        """搜索技能包"""
        return self.registry.search(query, filters)

    def get_compatible_skills(
        self,
        domain: str,
        framework: Optional[str] = None,
        language: Optional[str] = None
    ) -> List[SkillDefinition]:
        """获取兼容的技能列表"""
        compatible_packs = []
        for pack in self.installed_packs.values():
            if self._is_pack_compatible(pack, domain, framework, language):
                compatible_packs.extend(self.installer.get_installed_skills(pack.pack_id))

        return compatible_packs

    def _is_pack_compatible(
        self,
        pack: SkillPackMetadata,
        domain: str,
        framework: Optional[str],
        language: Optional[str]
    ) -> bool:
        """检查技能包兼容性"""
        # 实现兼容性检查逻辑
        return True

@dataclass
class InstallationResult:
    """安装结果"""
    success: bool
    pack_id: str
    installed_version: Optional[str] = None
    installed_path: Optional[Path] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    dependencies: Dict[str, str] = field(default_factory=dict)
    install_time: Optional[str] = None

@dataclass
class UninstallationResult:
    """卸载结果"""
    success: bool
    pack_id: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    uninstall_time: Optional[str] = None

@dataclass
class UpdateResult:
    """更新结果"""
    success: bool
    pack_id: str
    current_version: Optional[str] = None
    new_version: Optional[str] = None
    changes: List["ChangeInfo"] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    message: str = ""

@dataclass
class ChangeInfo:
    """变更信息"""
    type: str
    description: str
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class SkillPackInfo:
    """技能包详细信息"""
    metadata: SkillPackMetadata
    skills: List[SkillDefinition]
    rules: List[RuleTemplate]
    dependencies: Dict[str, str]
    dependents: List[str]

@dataclass
class SearchResult:
    """搜索结果"""
    query: str
    total_count: int
    results: List[SkillPackMetadata]
    facets: Dict[str, Any] = field(default_factory=dict)
```

### 4. 技能包注册表

```python
# core/registry.py

from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime
from schemas.skill_pack.v1 import SkillPackMetadata, SkillPackStatus, SkillPackCategory
from .repository import LocalRepository, RemoteRepository

class SkillPackRegistry:
    """技能包注册表"""

    def __init__(
        self,
        local_repo: LocalRepository,
        remote_repos: List[RemoteRepository],
        cache_enabled: bool = True
    ):
        self.local_repo = local_repo
        self.remote_repos = remote_repos
        self.cache_enabled = cache_enabled
        self.metadata_cache: Dict[str, Dict[str, SkillPackMetadata]] = {}

    def get_metadata(
        self,
        pack_id: str,
        version: Optional[str] = None
    ) -> Optional[SkillPackMetadata]:
        """获取技能包元数据"""
        # 检查缓存
        cache_key = f"{pack_id}:{version or 'latest'}"
        if cache_key in self.metadata_cache:
            return self.metadata_cache[cache_key]

        # 尝试本地仓库
        metadata = self.local_repo.get_metadata(pack_id, version)
        if metadata:
            if self.cache_enabled:
                self.metadata_cache[cache_key] = metadata
            return metadata

        # 尝试远程仓库
        for repo in self.remote_repos:
            metadata = repo.get_metadata(pack_id, version)
            if metadata:
                if self.cache_enabled:
                    self.metadata_cache[cache_key] = metadata
                return metadata

        return None

    def get_latest_metadata(self, pack_id: str) -> Optional[SkillPackMetadata]:
        """获取最新版本的技能包元数据"""
        return self.get_metadata(pack_id, version=None)

    def list_available(
        self,
        category: Optional[str] = None,
        search_query: Optional[str] = None,
        tags: Optional[List[str]] = None,
        min_rating: Optional[float] = None,
        sort_by: str = "downloads"
    ) -> List[SkillPackMetadata]:
        """列出可用的技能包"""
        all_metadata = []

        # 收集所有仓库的元数据
        for repo in self.remote_repos:
            all_metadata.extend(repo.list_all())

        # 过滤
        if category:
            all_metadata = [
                m for m in all_metadata
                if m.category.value == category
            ]

        if search_query:
            all_metadata = [
                m for m in all_metadata
                if self._matches_search_query(m, search_query)
            ]

        if tags:
            all_metadata = [
                m for m in all_metadata
                if any(tag in m.tags for tag in tags)
            ]

        if min_rating:
            all_metadata = [
                m for m in all_metadata
                if m.rating_average >= min_rating
            ]

        # 排序
        if sort_by == "downloads":
            all_metadata.sort(key=lambda m: m.downloads, reverse=True)
        elif sort_by == "rating":
            all_metadata.sort(key=lambda m: m.rating_average, reverse=True)
        elif sort_by == "updated":
            all_metadata.sort(key=lambda m: m.updated_at, reverse=True)
        elif sort_by == "name":
            all_metadata.sort(key=lambda m: m.display_name)

        return all_metadata

    def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None
    ) -> SearchResult:
        """搜索技能包"""
        results = []
        facets = {
            "categories": {},
            "types": {},
            "tags": {}
        }

        for repo in self.remote_repos:
            repo_results = repo.search(query)
            for metadata in repo_results:
                # 构建分类统计
                cat = metadata.category.value
                facets["categories"][cat] = facets["categories"].get(cat, 0) + 1

                # 构建类型统计
                ptype = metadata.pack_type.value
                facets["types"][ptype] = facets["types"].get(ptype, 0) + 1

                # 构建标签统计
                for tag in metadata.tags:
                    facets["tags"][tag] = facets["tags"].get(tag, 0) + 1

                results.append(metadata)

        return SearchResult(
            query=query,
            total_count=len(results),
            results=results,
            facets=facets
        )

    def update_stats(
        self,
        pack_id: str,
        downloads_increment: int = 0,
        rating_increment: Optional[float] = None,
        rating_count_increment: int = 0
    ):
        """更新统计信息"""
        metadata = self.get_metadata(pack_id)
        if metadata:
            metadata.downloads += downloads_increment
            if rating_increment:
                metadata.rating_average = (
                    (metadata.rating_average * metadata.rating_count + rating_increment) /
                    (metadata.rating_count + rating_count_increment)
                )
                metadata.rating_count += rating_count_increment

            # 清除缓存
            self._clear_cache(pack_id)

    def publish(
        self,
        pack_path: Path,
        dry_run: bool = False
    ) -> PublishResult:
        """发布技能包"""
        # 验证技能包
        metadata = self._validate_pack_for_publish(pack_path)
        if not metadata:
            return PublishResult(
                success=False,
                errors=["Invalid skill pack"]
            )

        if dry_run:
            return PublishResult(
                success=True,
                dry_run=True,
                message="Validation passed, ready to publish"
            )

        # 发布到远程仓库
        result = self.remote_repos[0].publish(pack_path)
        if result.success:
            metadata.status = SkillPackStatus.PUBLISHED
            metadata.published_at = datetime.now().isoformat()

        return result

    def _validate_pack_for_publish(self, pack_path: Path) -> Optional[SkillPackMetadata]:
        """验证技能包是否可以发布"""
        # 实现验证逻辑
        return None

    def _matches_search_query(self, metadata: SkillPackMetadata, query: str) -> bool:
        """检查元数据是否匹配搜索查询"""
        query_lower = query.lower()

        searchable_text = [
            metadata.display_name,
            metadata.description,
            metadata.author,
            metadata.organization,
            " ".join(metadata.tags),
            " ".join(metadata.keywords),
            " ".join(metadata.skill_dependencies)
        ]

        return any(query_lower in text.lower() for text in searchable_text)

    def _clear_cache(self, pack_id: str):
        """清除指定技能包的缓存"""
        keys_to_remove = [
            key for key in self.metadata_cache.keys()
            if key.startswith(f"{pack_id}:")
        ]
        for key in keys_to_remove:
            del self.metadata_cache[key]

@dataclass
class PublishResult:
    """发布结果"""
    success: bool
    dry_run: bool = False
    published_url: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    message: str = ""

@dataclass
class SearchResult:
    """搜索结果"""
    query: str
    total_count: int
    results: List[SkillPackMetadata]
    facets: Dict[str, Any] = field(default_factory=dict)
```

### 5. 依赖解析器

```python
# core/resolver.py

from typing import Dict, List, Set, Optional, Tuple
from schemas.skill_pack.v1 import SkillPackMetadata

class DependencyResolver:
    """依赖解析器"""

    def __init__(self, registry: SkillPackRegistry):
        self.registry = registry

    def resolve(
        self,
        metadata: SkillPackMetadata
    ) -> DependencyResolution:
        """解析依赖关系"""
        resolved = {}
        errors = []
        warnings = []

        # 收集所有依赖
        all_dependencies = self._collect_all_dependencies(
            metadata,
            resolved,
            errors,
            warnings,
            visited=set()
        )

        if errors:
            return DependencyResolution(
                is_resolvable=False,
                errors=errors,
                warnings=warnings,
                resolved_dependencies=resolved
            )

        # 检查版本冲突
        conflicts = self._check_version_conflicts(all_dependencies)
        if conflicts:
            return DependencyResolution(
                is_resolvable=False,
                errors=[
                    f"Version conflicts: {', '.join(conflicts)}"
                ],
                warnings=warnings,
                resolved_dependencies=resolved
            )

        return DependencyResolution(
            is_resolvable=True,
            errors=[],
            warnings=warnings,
            resolved_dependencies=all_dependencies
        )

    def _collect_all_dependencies(
        self,
        metadata: SkillPackMetadata,
        resolved: Dict[str, str],
        errors: List[str],
        warnings: List[str],
        visited: Set[str]
    ) -> Dict[str, str]:
        """收集所有依赖（递归）"""
        pack_id = metadata.pack_id

        # 检查循环依赖
        if pack_id in visited:
            errors.append(f"Circular dependency detected: {pack_id}")
            return {}

        visited.add(pack_id)

        # 处理直接依赖
        for dep_id, dep_version in metadata.dependencies.items():
            if dep_id in resolved:
                # 检查版本冲突
                if resolved[dep_id] != dep_version:
                    warnings.append(
                        f"Dependency version conflict for {dep_id}: "
                        f"requested {dep_version} but already have {resolved[dep_id]}"
                    )
                continue

            # 获取依赖包元数据
            dep_metadata = self.registry.get_metadata(dep_id, dep_version)
            if not dep_metadata:
                errors.append(f"Dependency not found: {dep_id}@{dep_version}")
                continue

            # 添加到已解析
            resolved[dep_id] = dep_version

            # 递归处理依赖的依赖
            self._collect_all_dependencies(
                dep_metadata,
                resolved,
                errors,
                warnings,
                visited.copy()  # 使用副本避免影响当前路径的循环检测
            )

        return resolved

    def check_version_conflicts(
        self,
        metadata: SkillPackMetadata
    ) -> List[str]:
        """检查版本冲突"""
        resolution = self.resolve(metadata)
        return self._check_version_conflicts(resolution.resolved_dependencies)

    def _check_version_conflicts(
        self,
        dependencies: Dict[str, str]
    ) -> List[str]:
        """检查版本冲突"""
        # 简单实现：检查同一依赖是否有不同版本要求
        # 实际实现可能需要支持版本范围语义
        return []

    def get_dependents(self, pack_id: str) -> List[str]:
        """获取依赖指定技能包的其他技能包"""
        dependents = []

        # 扫描所有已安装的技能包
        for pack_id, pack_version in dependencies.items():
            metadata = self.registry.get_metadata(pack_id, pack_version)
            if metadata and pack_id in metadata.dependencies:
                dependents.append(pack_id)

        return dependents

    def get_installation_order(
        self,
        pack_ids: List[str]
    ) -> List[str]:
        """获取安装顺序（拓扑排序）"""
        # 构建依赖图
        graph = {}
        in_degree = {}

        for pack_id in pack_ids:
            metadata = self.registry.get_metadata(pack_id)
            if metadata:
                graph[pack_id] = list(metadata.dependencies.keys())
                in_degree[pack_id] = 0

        # 计算入度
        for pack_id in graph:
            for dep_id in graph[pack_id]:
                if dep_id in in_degree:
                    in_degree[dep_id] += 1

        # 拓扑排序
        result = []
        queue = [pack_id for pack_id, degree in in_degree.items() if degree == 0]

        while queue:
            current = queue.pop(0)
            result.append(current)

            for dep_id in graph.get(current, []):
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0:
                    queue.append(dep_id)

        return result

@dataclass
class DependencyResolution:
    """依赖解析结果"""
    is_resolvable: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    resolved_dependencies: Dict[str, str] = field(default_factory=dict)
    missing_dependencies: List[str] = field(default_factory=list)
    circular_dependencies: List[str] = field(default_factory=list)
```

### 6. CLI 工具

```python
# cli/skill_pack.py

import argparse
from typing import Optional
from pathlib import Path
from core.manager import SkillPackManager
from core.registry import SkillPackRegistry
from core.installer import SkillPackInstaller
from core.resolver import DependencyResolver
from core.validator import SkillPackValidator
from cli.commands import install, uninstall, list, search, info, update, publish

def create_parser() -> argparse.ArgumentParser:
    """创建命令行解析器"""
    parser = argparse.ArgumentParser(
        description="Harness Skill Pack Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # 全局选项
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细输出"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="静默模式"
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="指定配置文件路径"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="指定缓存目录"
    )

    # 子命令
    subparsers = parser.add_subparsers(
        dest="command",
        help="可用命令",
        required=True
    )

    # install 命令
    install_parser = subparsers.add_parser(
        "install",
        help="安装技能包"
    )
    install_parser.add_argument(
        "pack_id",
        help="技能包ID"
    )
    install_parser.add_argument(
        "--version", "-v",
        help="指定版本"
    )
    install_parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="强制重新安装"
    )
    install_parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="跳过依赖检查"
    )
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="模拟安装"
    )

    # uninstall 命令
    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="卸载技能包"
    )
    uninstall_parser.add_argument(
        "pack_id",
        help="技能包ID"
    )
    uninstall_parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="强制卸载"
    )
    uninstall_parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="跳过依赖检查"
    )

    # list 命令
    list_parser = subparsers.add_parser(
        "list",
        help="列出技能包"
    )
    list_parser.add_argument(
        "--installed",
        action="store_true",
        help="只显示已安装的技能包"
    )
    list_parser.add_argument(
        "--category", "-c",
        help="按分类筛选"
    )
    list_parser.add_argument(
        "--status", "-s",
        help="按状态筛选"
    )
    list_parser.add_argument(
        "--sort-by",
        choices=["downloads", "rating", "updated", "name"],
        default="downloads",
        help="排序方式"
    )
    list_parser.add_argument(
        "--limit", "-l",
        type=int,
        default=20,
        help="限制结果数量"
    )
    list_parser.add_argument(
        "--format",
        choices=["table", "json", "yaml"],
        default="table",
        help="输出格式"
    )

    # search 命令
    search_parser = subparsers.add_parser(
        "search",
        help="搜索技能包"
    )
    search_parser.add_argument(
        "query",
        help="搜索查询"
    )
    search_parser.add_argument(
        "--category", "-c",
        help="按分类筛选"
    )
    search_parser.add_argument(
        "--tags", "-t",
        nargs="+",
        help="按标签筛选"
    )
    search_parser.add_argument(
        "--min-rating",
        type=float,
        help="最小评分"
    )
    search_parser.add_argument(
        "--sort-by",
        choices=["relevance", "downloads", "rating", "updated"],
        default="relevance",
        help="排序方式"
    )
    search_parser.add_argument(
        "--limit", "-l",
        type=int,
        default=20,
        help="限制结果数量"
    )
    search_parser.add_argument(
        "--format",
        choices=["table", "json", "yaml"],
        default="table",
        help="输出格式"
    )

    # info 命令
    info_parser = subparsers.add_parser(
        "info",
        help="显示技能包详细信息"
    )
    info_parser.add_argument(
        "pack_id",
        help="技能包ID"
    )
    info_parser.add_argument(
        "--version", "-v",
        help="指定版本"
    )
    info_parser.add_argument(
        "--format",
        choices=["text", "json", "yaml", "markdown"],
        default="text",
        help="输出格式"
    )

    # update 命令
    update_parser = subparsers.add_parser(
        "update",
        help="更新技能包"
    )
    update_parser.add_argument(
        "pack_id",
        nargs="?",
        help="技能包ID（不指定则更新所有）"
    )
    update_parser.add_argument(
        "--to-version",
        help="更新到指定版本"
    )
    update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="模拟更新"
    )
    update_parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="强制更新"
    )

    # publish 命令
    publish_parser = subparsers.add_parser(
        "publish",
        help="发布技能包"
    )
    publish_parser.add_argument(
        "pack_path",
        type=Path,
        help="技能包路径"
    )
    publish_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="模拟发布"
    )
    publish_parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="跳过验证"
    )

    return parser

def main():
    parser = create_parser()
    args = parser.parse_args()

    # 初始化服务
    config = load_config(args.config)
    manager = initialize_skill_pack_manager(config, args)

    # 处理命令
    command_map = {
        "install": install.handle,
        "uninstall": uninstall.handle,
        "list": list.handle,
        "search": search.handle,
        "info": info.handle,
        "update": update.handle,
        "publish": publish.handle
    }

    command_handler = command_map.get(args.command)
    if command_handler:
        result = command_handler(args, manager)
        output_result(result, args.format, args.verbose)
    else:
        parser.print_help()

def load_config(config_path: Optional[Path]) -> Dict:
    """加载配置"""
    # 实现配置加载逻辑
    return {}

def initialize_skill_pack_manager(config: Dict, args) -> SkillPackManager:
    """初始化技能包管理器"""
    # 实现管理器初始化逻辑
    pass

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
    else:  # text/table
        output_table(result)

if __name__ == "__main__":
    main()
```

### 7. 预定义技能包示例

#### 7.1 React 技能包

```json
{
  "schema_version": "skill-pack.v1",
  "pack_id": "frontend.react",
  "name": "react",
  "version": "1.0.0",
  "display_name": "React 开发技能包",
  "description": "React 项目专用的开发、审查、测试技能集合",
  "pack_type": "framework",
  "category": "frontend",
  "status": "published",

  "author": "Harness Team",
  "author_email": "team@harness.dev",
  "organization": "Harness",
  "license": "MIT",

  "platform_version_min": "1.0.0",
  "platform_version_max": "99.0.0",
  "python_version_min": "3.8",

  "dependencies": {
    "frontend.core": ">=1.0.0"
  },
  "skill_dependencies": [
    "react.component.create",
    "react.hooks.best-practices",
    "react.performance.optimize",
    "react.accessibility.check",
    "react.state.management",
    "react.testing.unit",
    "react.testing.e2e"
  ],
  "plugin_dependencies": [
    "core-workflow",
    "git-provider"
  ],

  "downloads": 1250,
  "rating_average": 4.8,
  "rating_count": 85,

  "created_at": "2026-01-15T00:00:00Z",
  "updated_at": "2026-08-10T00:00:00Z",
  "published_at": "2026-01-20T00:00:00Z",

  "checksum_sha256": "abc123...",

  "tags": ["react", "frontend", "javascript", "typescript", "ui"],
  "keywords": ["react", "hooks", "jsx", "tsx", "component"],

  "metadata": {
    "react_versions_supported": ["16", "17", "18"],
    "nextjs_support": true,
    "gatsby_support": true,
    "typescript_support": true,
    "testing_frameworks": ["jest", "react-testing-library", "cypress", "playwright"]
  }
}
```

#### 7.2 代码审查技能

```json
{
  "skill_id": "react.code-review",
  "name": "code-review",
  "display_name": "React 代码审查技能",
  "description": "专门针对 React 代码的自动化审查能力",
  "version": "1.0.0",

  "trigger_type": "user",
  "trigger_patterns": [
    "review.*react",
    "check.*react.*code",
    "analyze.*react.*component"
  ],
  "trigger_keywords": [
    "review", "check", "analyze", "lint", "quality"
  ],

  "required_permission": "L1",
  "capability_requirements": [
    "code.analyze",
    "code.review"
  ],

  "entrypoint": "skills/react_code_review.py",
  "execution_mode": "sync",
  "timeout_seconds": 300,

  "input_schema": {
    "type": "object",
    "properties": {
      "code_path": {
        "type": "string",
        "description": "代码文件或目录路径"
      },
      "focus_areas": {
        "type": "array",
        "items": {
          "type": "string",
          "enum": ["performance", "security", "accessibility", "best-practices", "testing"]
        }
      },
      "severity_threshold": {
        "type": "string",
        "enum": ["info", "warning", "error", "critical"]
      }
    }
  },

  "output_schema": {
    "type": "object",
    "properties": {
      "overall_rating": {
        "type": "number",
        "minimum": 1,
        "maximum": 5
      },
      "findings": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "severity": {
              "type": "string",
              "enum": ["info", "warning", "error", "critical"]
            },
            "category": {
              "type": "string"
            },
            "file": {
              "type": "string"
            },
            "line": {
              "type": "number"
            },
            "description": {
              "type": "string"
            },
            "suggestion": {
              "type": "string"
            }
          }
        }
      },
      "metrics": {
        "type": "object",
        "properties": {
          "component_count": {"type": "number"},
          "hooks_usage_score": {"type": "number"},
          "accessibility_score": {"type": "number"},
          "test_coverage": {"type": "number"}
        }
      }
    }
  },

  "examples": [
    {
      "input": {
        "code_path": "./src/components",
        "focus_areas": ["performance", "best-practices"]
      },
      "output": {
        "overall_rating": 4.2,
        "findings": [...],
        "metrics": {...}
      }
    }
  ],

  "config_schema": {
    "type": "object",
    "properties": {
      "check_jsx_no_inline_styles": {
        "type": "boolean",
        "default": true
      },
      "check_use_effect_dependencies": {
        "type": "boolean",
        "default": true
      },
      "check_component_naming": {
        "type": "boolean",
        "default": true
      }
    }
  },

  "default_config": {
    "check_jsx_no_inline_styles": true,
    "check_use_effect_dependencies": true,
    "check_component_naming": true
  },

  "depends_on": [],
  "provides": ["react.code-review"],

  "documentation": "# React 代码审查技能\n\n该技能专门用于审查 React 代码...",
  "references": [
    "https://react.dev/learn/thinking-in-react",
    "https://react.dev/learn/referencing-values-with-refs"
  ],

  "compatible_platforms": ["macos", "linux", "windows"],
  "compatible_languages": ["javascript", "typescript"],

  "tags": ["react", "code-review", "quality"],
  "keywords": ["review", "lint", "check", "analyze"]
}
```

### 8. 测试策略

#### 8.1 单元测试

```python
# tests/test_skill_pack_manager.py

import pytest
from pathlib import Path
from core.manager import SkillPackManager
from schemas.skill_pack.v1 import SkillPackMetadata, SkillPackStatus

@pytest.fixture
def skill_pack_manager():
    """创建技能包管理器实例"""
    # 模拟依赖
    registry = MockSkillPackRegistry()
    installer = MockSkillPackInstaller()
    resolver = MockDependencyResolver()
    validator = MockSkillPackValidator()

    return SkillPackManager(
        registry=registry,
        installer=installer,
        resolver=resolver,
        validator=validator,
        skills_root=Path("./test_skills")
    )

def test_install_skill_pack(skill_pack_manager):
    """测试安装技能包"""
    result = skill_pack_manager.install("frontend.react")

    assert result.success
    assert result.pack_id == "frontend.react"
    assert "frontend.react" in skill_pack_manager.installed_packs

def test_install_with_dependencies(skill_pack_manager):
    """测试带依赖的安装"""
    result = skill_pack_manager.install("frontend.react", skip_dependencies=False)

    assert result.success
    assert len(result.dependencies) > 0

def test_uninstall_with_dependents(skill_pack_manager):
    """测试有依赖项时的卸载"""
    # 先安装
    skill_pack_manager.install("frontend.core")
    skill_pack_manager.install("frontend.react")

    # 尝试卸载被依赖的包
    result = skill_pack_manager.uninstall("frontend.core")

    assert not result.success
    assert "Required by" in " ".join(result.errors)

def test_list_installed(skill_pack_manager):
    """测试列出已安装的技能包"""
    skill_pack_manager.install("frontend.react")
    skill_pack_manager.install("backend.python")

    all_packs = skill_pack_manager.list_installed()
    assert len(all_packs) == 2

    frontend_packs = skill_pack_manager.list_installed(category="frontend")
    assert len(frontend_packs) == 1
    assert frontend_packs[0].pack_id == "frontend.react"

def test_search_skill_packs(skill_pack_manager):
    """测试搜索技能包"""
    result = skill_pack_manager.search("react")

    assert len(result.results) > 0
    assert any("react" in p.tags for p in result.results)
```

### 9. 交付物清单

| 交付物 | 描述 | 状态 |
|--------|------|------|
| 核心管理器代码 | manager.py | ⬜ |
| 注册表实现 | registry.py | ⬜ |
| 安装器实现 | installer.py | ⬜ |
| 依赖解析器 | resolver.py | ⬜ |
| 验证器实现 | validator.py | ⬜ |
| Schema 定义 | schemas/*.py | ⬜ |
| CLI 工具 | cli/skill_pack.py | ⬜ |
| 仓库管理 | repository/*.py | ⬜ |
| 打包工具 | packaging/*.py | ⬜ |
| 预定义技能包 | skills/**/*.json | ⬜ |
| 单元测试 | tests/test_*.py | ⬜ |
| 集成测试 | tests/integration/*.py | ⬜ |
| 架构文档 | 本文档 | ✅ |

---

**下一步行动**：进入阶段2的代码实现阶段