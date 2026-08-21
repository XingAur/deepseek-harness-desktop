# DeepSeek Harness Desktop Plugin

DeepSeek Harness Desktop 内置插件，为官方工作台提供桌面布局、Profile 切换和本地项目入口。该插件不包含社区插件市场。

## 本地项目

- 单击项目卡片进行选择，并在下方对话框中快捷修改项目。
- 双击卡片或选中后按 Enter 启动项目。
- 右键卡片可修改名称、选择内置渐变或颜色封面、置顶和删除项目。
- 删除时默认仅从当前 Profile 的 Workspace 列表移除；也可在二次确认后将项目目录移到 Windows 回收站。
- 空列表可直接描述项目需求，通过 DeepSeek Harness 对话构建第一个本地项目。项目路径、Profile 和权限由桌面端自动处理：项目目录会安全创建在“文档\DeepSeek Harness\Projects”，并自动避免重名。

本地项目始终来自当前 Profile 的官方 Workspace registry。Profile 正在切换时，项目操作会暂时停用，切换失败则恢复 last-known-good Profile。

## 桌面更新

应用启动后的自动更新检查在后台进行；网络或更新服务不可用不会阻塞 Runtime 与工作台启动。用户通过原生入口手动检查更新时，失败会显示可重试的诊断提示。
