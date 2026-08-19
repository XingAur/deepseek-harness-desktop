# Runtime manifests

发布流水线会在这里写入两个经 Ed25519 签名的清单：

- `runtime-windows-x86_64.json`
- `runtime-darwin-aarch64.json`

仓库不提交指向不存在制品的伪清单。开发时可设置
`DSH_DESKTOP_RUNTIME_MANIFEST_URL=https://.../{target}.json`，或先运行运行时组装与签名脚本。
