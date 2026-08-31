# HIS Harness v0.63 可复现离线发布包设计

## 目标

为个人本地分发提供不含数据库、凭证、运行产物和缓存的确定性源码包。同一源码和版本号重复构建时，归档字节与 SHA-256 必须完全一致。

## 边界

- 只包含显式白名单中的源码、测试、fixture、示例配置、文档和 CI。
- 不包含 `data/`、个人配置、运行记录、工作台快照、临时 worktree、缓存或密钥。
- 打包前执行高置信度源码密钥扫描，命中即阻断。
- 统一 tar 路径、文件顺序、mtime、uid、gid、用户名、组名和权限；gzip header 不记录构建时间或本机文件名。
- 发布包是本地产物，不执行 Git、上传、部署或外部写入。

## 产物

- `his-harness-<version>.tar.gz`
- `his-harness-<version>.manifest.json`
- `his-harness-<version>.sha256`
