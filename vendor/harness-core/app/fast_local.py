from __future__ import annotations

from pathlib import Path

from app.requirement_calibration import find_high_risk_terms
from app.technical_decision import requires_service_contract


FRONTEND_LOCAL_SUFFIXES = {".vue", ".js", ".ts", ".css", ".scss", ".less"}
MAX_ALLOWED_PATHS = 3


def build_fast_local_decision(
    *,
    title: str,
    demand_text: str,
    project_paths: list[str],
    allowed_paths: list[str],
    project_path_is_explicit: bool = True,
    allowed_paths_are_explicit: bool = True,
) -> dict:
    """Decide whether auto-local may skip the broad project-context scan."""
    normalized_projects = [str(Path(item).expanduser().resolve()) for item in project_paths if str(item).strip()]
    normalized_paths = [str(item).strip() for item in allowed_paths if str(item).strip()]
    combined_text = f"{title}\n{demand_text}"
    blockers: list[str] = []

    if not project_path_is_explicit:
        blockers.append("需要调用方显式指定业务项目路径。")
    elif len(normalized_projects) != 1:
        blockers.append("需要一个显式业务项目路径。")
    if not allowed_paths_are_explicit:
        blockers.append("需要调用方显式指定白名单路径。")
    elif not normalized_paths:
        blockers.append("需要显式白名单路径。")
    elif len(normalized_paths) > MAX_ALLOWED_PATHS:
        blockers.append(f"白名单路径超过 {MAX_ALLOWED_PATHS} 个，需走完整工程扫描。")
    if requires_service_contract(combined_text):
        blockers.append("需求命中跨层接口或排序契约，需走完整工程扫描。")
    if find_high_risk_terms(title=title, demand_text=demand_text):
        blockers.append("需求命中医保、收费、结算等高风险词，需走完整工程扫描。")
    if normalized_paths and any(Path(path).suffix.lower() not in FRONTEND_LOCAL_SUFFIXES for path in normalized_paths):
        blockers.append("白名单包含非前端局部文件，需走完整工程扫描。")
    if len(normalized_projects) == 1:
        project_root = Path(normalized_projects[0])
        missing_paths = [path for path in normalized_paths if not (project_root / path).is_file()]
        if missing_paths:
            blockers.append("白名单路径不存在：" + ", ".join(missing_paths))

    return {
        "version": "0.43-fast-local",
        "eligible": not blockers,
        "route": "fast_local" if not blockers else "core_closure_trial",
        "project_paths": normalized_projects,
        "allowed_paths": normalized_paths,
        "skip_project_context_scan": not blockers,
        "blockers": blockers,
        "policy": (
            "仅跳过全仓工程上下文扫描；需求校准、技术路径存在性、专项验证、worktree、独立 diff 审查和本地应用门禁不变。"
            if not blockers
            else "不满足快车道条件，继续执行完整 auto-local 核心闭环。"
        ),
    }
