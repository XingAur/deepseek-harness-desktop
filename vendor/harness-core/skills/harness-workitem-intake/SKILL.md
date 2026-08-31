---
name: harness-workitem-intake
description: Use when the legacy harness-workitem-intake skill path is selected.
---

正式实现位于 `/Users/lym/plugins/his-harness-core/skills/harness-workitem-intake/SKILL.md`；插件未安装时必须停止并报告安装错误，不得静默执行旧实现。

# Compatibility

本目录不再包含云效 provider 实现或写入路径。旧脚本仅启动正式插件脚本；找不到插件时 fail closed。
