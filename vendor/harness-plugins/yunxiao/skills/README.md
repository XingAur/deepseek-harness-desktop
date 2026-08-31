# yunxiao skills

本目录状态为 `canonical`，属于 `yunxiao` 正式插件。

- `yunxiao-workitem-read`：使用独立只读凭证等级读取工作项、评论、内联文件和附件，
  只生成已脱敏证据，不授予后续修改权限。
- `yunxiao-workitem-write`：只描述写入请求的独立授权边界；当前
  `workitem.write` 为 L4、`enabled=false`，不能执行评论、流转、分配或上传。

根目录 `yunxiao-workitem-evidence` 仅为 `compatibility` 入口。插件未安装时旧入口
必须 fail closed，不能回退到旧实现或写凭证。
