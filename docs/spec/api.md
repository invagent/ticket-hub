# API 规格（v0.5.6 草案）

> 决策 10：所有业务能力先以 FastAPI endpoint 暴露；OpenAPI 是唯一对外契约。
> 前端类型 / CLI / MCP 由 OpenAPI 自动生成（参见 scripts/gen_*）。

## 阶段递进

| 阶段 | API 命名空间 |
|------|------------|
| D0 | `/health` `/api/auth/feishu/*` `/api/admin/{sources,product-lines,users}` |
| D1 | `/api/admin/{users,scopes}` 完整 CRUD ; `/api/tickets` 接收 webhook ; `/webhook/*` 复用现有契约 |
| D2 | `/api/supervisor/{inbox,relink,revert}` ; `/api/customers/{search,merge,split}` ; `/webhook/zammad` |
| D3 | `/api/agents/{runs,run}` ; `/api/admin/llm-{config,cost}` |
| D4 | `/api/hub-issues/{,id}/{supersede,link-linear,update-reply}` ; `/api/admin/knowledge` ; `/webhook/linear` |
| D5 | `/api/internal-tasks/*` ; `/mcp/*` |
| D6 | 性能压测面 + 主管聚合看板 + PII 审计页 |

## 鉴权

- 浏览器：JWT cookie（HttpOnly + SameSite=Strict）+ 飞书 SSO callback 落 cookie
- API / CLI / MCP：`Authorization: Bearer <jwt>`
- Webhook：query `?access_token=<token>`（兼容 feishu-python 老契约）

## 错误模型

```json
{
  "detail": "human readable message",
  "code": "MACHINE_CODE",         // optional
  "trace_id": "abc1234567890def"
}
```

HTTP 状态码遵循语义；4xx 客户端、5xx 服务端；429 用于路由限速。

## 待评审

- MCP 鉴权方式（复用 Bearer vs OAuth Device Flow）— 阶段 5 再定（议题 11.2-2）
- `/webhook/linear` 反向同步是否需要拉取版本号 / 防重放
