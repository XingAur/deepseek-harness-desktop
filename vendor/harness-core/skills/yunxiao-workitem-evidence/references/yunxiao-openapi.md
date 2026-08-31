# 云效只读 OpenAPI

本 Skill 使用项目协作新版只读接口，默认服务接入点为 `https://openapi-rdc.aliyuncs.com`。

| 能力 | 中心版请求 |
| --- | --- |
| 工作项详情 | `GET /oapi/v1/projex/organizations/{organizationId}/workitems/{id}` |
| 评论 | `GET /oapi/v1/projex/organizations/{organizationId}/workitems/{id}/comments` |
| 附件 | `GET /oapi/v1/projex/organizations/{organizationId}/workitems/{id}/attachments` |
| 关系 | `GET /oapi/v1/projex/organizations/{organizationId}/workitems/{id}/relationRecords?relationType={type}` |
| 内联文件详情 | `GET /oapi/v1/projex/organizations/{organizationId}/workitems/{workitemId}/files/{id}` |

关系类型固定查询：`PARENT`、`SUB`、`ASSOCIATED`、`DEPEND_ON`、`DEPENDED_BY`。

认证头仅使用 `x-yunxiao-token`，且只允许发送到
`https://openapi-rdc.aliyuncs.com`。API 重定向、HTTP 地址和其他主机均拒绝。
附件临时下载地址不携带云效令牌，避免把令牌发送给文件存储域名。

官方文档：

- https://help.aliyun.com/zh/yunxiao/developer-reference/getworkitem
- https://help.aliyun.com/en/yunxiao/developer-reference/listworkitemrelationrecords
- https://help.aliyun.com/en/yunxiao/developer-reference/listworkitemcomments
- https://help.aliyun.com/en/yunxiao/developer-reference/listworkitemattachments
- https://help.aliyun.com/zh/yunxiao/developer-reference/getworkitemfile
