# AI Skills

这里保留历史 Skill 的兼容入口。四个正式插件中的 Skill 才是当前
`canonical` 实现；本目录不得维护第二套 provider 或治理逻辑。

## 目录约定

- 每个兼容 Skill 使用一个独立目录，目录名保持稳定。
- 兼容脚本只能代理正式插件；插件缺失时必须 fail closed。
- 凭证、需求证据、附件、worktree 和运行日志不得放进本目录。
- 新实现、脚本、测试和协议只进入对应插件，不再新增根目录 Skill。
- Harness 只依赖 capability contract，不直接耦合 provider 返回结构。

## 兼容入口

| 旧 Skill | 状态 | 正式入口 |
| --- | --- | --- |
| `his-harness` | `compatibility` | `his-harness-core/skills/his-harness` |
| `harness-workitem-intake` | `compatibility` | `his-harness-core/skills/harness-workitem-intake` |
| `harness-history` | `compatibility` | `his-harness-core/skills/harness-history` |
| `yunxiao-workitem-evidence` | `compatibility` | `yunxiao/skills/yunxiao-workitem-read` |

正式默认链路：

```text
DFHIS 编号或云效链接
  -> his-harness-core 治理编排
  -> yunxiao 只读证据
  -> requirement.govern + 一次修改合同
  -> his-engineering 本地 Git / 只读工程证据
  -> review + verification
  -> harness-history 审计
```

知识问答走 `his-knowledge` 的 `question` 模式，不进入改码链路。云效写、Git
远端写、GitLab 写、数据库真实变更、部署和生产操作不属于默认链路。

## 退场规则

本版本不得删除上述兼容入口。只能在下一版本提出删除计划，并且删除前必须同时
满足：已有真实调用/无调用的使用证据、所有调用方已迁移、回退方案已验证、取得
用户确认。缺少任一项时继续保留 `compatibility`。
