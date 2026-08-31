# his-engineering skills

本目录状态为 `canonical`，属于 `his-engineering` 正式插件。

- `his-git-local`：本地仓库检查和精确白名单 patch；不包含 branch、commit 或远端。
- `his-code-evidence`：源码、搜索、diff、history、本地验证和 review 的 canonical 只读证据；不包含 patch、commit、push 或数据库写入。
- `his-git-delivery`：独立、可审计的本地交付事务；commit 属于 L3，远端 push
  仍为 L4 disabled。
- `his-gitlab`：项目、MR 和 pipeline job 的只读证据；写能力 disabled。
- `his-database-read`：参数化、只读数据库证据；不得执行 DDL/DML。
- `his-database-change`：只生成静态变更、验证与回退计划，不建立真实写连接。

本插件不因持有凭证或已经完成本地改动而自动扩大授权。Git 远端、GitLab 写和
数据库真实写入必须由独立 capability 开放；当前 manifest 均禁止。
