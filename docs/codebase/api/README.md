# api 模块

## 职责

`app/api` 提供 REST API 路由。路由层负责鉴权、project_id 校验、请求参数解析、调用 service/repository、返回统一响应。

## 全局约定

- 大多数业务路由需要 `Authorization: Bearer <JWT>`。
- 大多数业务路由需要 `X-Project-Id`。
- 成功响应使用 `success(data)`。
- 业务错误抛 `APIError(code, message, data=None)`。
- 分页字段对外使用 `pageSize`，内部变量常用 `page_size`。

例外响应：

- `POST /api/v1/alerts/webhook` 返回 HTTP 202。
- `GET /api/v1/k8s/pods/{name}/logs` 返回纯文本。
- `GET /api/v1/k8s/pods/{name}/logs/stream` 返回 SSE。
- SSE 日志流因浏览器 `EventSource` 不能设置自定义 Header，兼容 `?project_id=`；其他业务接口仍要求 `X-Project-Id`。
- `GET /api/v1/exports/{export_id}/download` 返回文件响应。
- `GET /metrics` 返回 Prometheus 文本。

前端契约兼容要点：

- `auth.py` 的 login user 和 `/me` 返回 `avatar`、`email`、`permissions`。
- `settings.py` 配置更新请求体支持 `reason`，真实 key 以前端从配置列表拿到的 key 为准。
- `settings.py` 单配置项历史走 `GET /api/v1/settings/configs/{name}/history`，返回 `{value, changedBy, changedAt, reason}` 分页结构；全局历史 `GET /api/v1/settings/history` 仍保留，返回审计标准结构。
- `alerts.py` 的 batch-suppress 接受字符串 ID 数组，非法 ID 返回参数错误。
- `users.py` 支持完整 CRUD：`POST /api/v1/users`（创建，id 项目内唯一，重复 40022）、`PUT /api/v1/users/{id}`（部分更新，仅传字段被改）、`DELETE /api/v1/users/{id}`；三个写操作均记审计（`USER_CREATE`/`USER_UPDATE`/`USER_DELETE`）。
- `logs.py` 已保存查询支持管理：`PUT /api/v1/logs/queries/{id}`（重命名，名称重复 40022）、`DELETE /api/v1/logs/queries/{id}`。
- `metrics.py` series 接受 `timeRange`、`step`；`traces.py` 列表接受 `timeRange=1h|6h|24h`。

## 路由域

| 文件 | 主要路由 | 说明 |
|---|---|---|
| `auth.py` | `/api/v1/auth/*` | 登录、登出、me、refresh |
| `projects.py` | `/api/v1/projects` | 启用项目列表 |
| `dashboard.py` | `/api/v1/dashboard/*` | 首页统计 |
| `metrics.py` | `/api/v1/metrics/*` | 指标分类和序列 |
| `alerts.py` | `/api/v1/alerts/*`、`/api/v1/alert-groups` | 告警接入、查询、状态操作 |
| `agent.py` | `/api/v1/agent/*` | Agent 触发、状态、建议 |
| `rca.py` | `/api/v1/rca/*` | RCA 详情、证据、执行修复、导出 |
| `healing.py` | `/api/v1/healing/*` | 自愈动作和审批 |
| `logs.py` | `/api/v1/logs/*` | 日志搜索、保存查询、导出 |
| `traces.py` | `/api/v1/traces/*` | Trace 列表、详情、span |
| `k8s.py` | `/api/v1/k8s/*` | K8s 查询、日志、重启；SSE 日志流兼容 `?project_id=` |
| `topology.py` | `/api/v1/topology/*` | 服务拓扑 |
| `users.py` | `/api/v1/users/*` | 本地 users 表驱动的用户管理 MVP |
| `settings.py` | `/api/v1/settings/*` | 运行时配置 |
| `audit.py` | `/api/v1/audit/*` | 审计查询和导出 |
| `notifications.py` | `/api/v1/notifications/*` | 通知列表和已读 |
| `exports.py` | `/api/v1/exports/*` | 导出下载 |
| `seeds.py` | `/api/v1/seeds/demo` | 开发种子数据 |
| `health.py` | `/health`、`/metrics` | 探针和自身指标 |

## 新增 API 检查项

- 是否需要 `get_current_user`。
- 是否需要 `require_project_id` 或等效项目依赖。
- 是否返回统一信封。
- 是否补跨项目隔离测试。
- 是否补 `tests/test_api_contract_parity.py`。
- 高风险写操作是否写审计。
- 是否有特殊响应类型需要在文档中标明。
