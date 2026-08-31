---
name: harness-history
description: Use when the legacy harness-history skill path is selected.
---

正式实现位于 `/Users/lym/plugins/his-harness-core/skills/harness-history/SKILL.md`；插件未安装时必须停止并报告安装错误，不得静默执行旧实现。

# Compatibility

本目录不再维护历史管理实现。旧脚本只启动正式插件脚本；找不到插件时 fail closed，不允许回退到旧任务写入路径。
