# 后端修复联调说明

> 面向前端联调，逐条对应 `docs/backend-fixes-required.md`。

## 1. SSE Pod 日志流项目参数

`GET /api/v1/k8s/pods/{name}/logs/stream` 已兼容 query 参数 `project_id`：

```text
GET /api/v1/k8s/pods/{podName}/logs/stream?project_id={projectId}
```

浏览器原生 `EventSource` 不能设置自定义 Header，所以只有 SSE 日志流允许用 `?project_id=`。其他业务接口仍必须通过 `X-Project-Id` Header 传项目 ID。

联调注意事项：

- SSE 仍需要鉴权 token。当前后端鉴权仍按既有 REST 约定读取 `Authorization: Bearer <token>` Header；原生 `EventSource` 不能设置该 Header，因此前端如需直接带 JWT，请使用支持自定义 Header 的 SSE polyfill 或 `fetch + ReadableStream` 封装。`project_id` 是本轮新增的 query 兼容项，不代表该接口放宽鉴权。
- 非 SSE 的 K8s 查询、日志纯文本接口、其他业务接口不要迁移到 query project_id。
- 若前端封装了统一项目 Header，请只在 EventSource 创建处单独追加 `project_id`。

## 2. Metrics Series 时间参数

`GET /api/v1/metrics/{name}/series` 已接受可选 query 参数 `timeRange` 和 `step`：

```text
GET /api/v1/metrics/{name}/series?timeRange=1h&step=30s
```

调用要求：

- 继续携带 `X-Project-Id`。
- `timeRange`、`step` 是可选参数；不传时后端使用默认范围。
- 前端实时监控页可恢复传入 `MetricSeriesQuery.timeRange` 和 `MetricSeriesQuery.step`。

## 3. Traces 时间范围

`GET /api/v1/traces` 已接受 `timeRange=1h|6h|24h`：

```text
GET /api/v1/traces?timeRange=6h
```

调用要求：

- 继续携带 `X-Project-Id`。
- 仅使用 `1h`、`6h`、`24h`；其他值会按参数错误处理。

## 4. Users 列表与统计

用户管理改为读取后端本地 `users` 表。每个项目会兜底提供一个 `admin` 用户，避免 MVP 环境用户列表为空。

`GET /api/v1/users` 支持：

```text
GET /api/v1/users?keyword=adm&isBanned=false&onlineStatus=offline&riskLevel=normal&page=1&pageSize=20
```

参数说明：

- `keyword`：按用户名等关键字筛选。
- `isBanned`：按封禁状态筛选。
- `onlineStatus`：按 `online` / `offline` 筛选。
- `riskLevel`：按 `normal` / `warning` / `high` 筛选。
- `page`、`pageSize`：分页。

`GET /api/v1/users/stats` 返回统计包含：

- `total`
- `online`
- `banned`
- `highRisk`
- `riskUsers`

其中 `highRisk` 与 `riskUsers` 当前都可用于高风险用户数量展示，前端优先使用类型中已有字段即可。

`POST /api/v1/users/{user_id}/ban` 和 `POST /api/v1/users/{user_id}/unban` 更新本地 `is_banned`，并写入审计日志。封禁状态是 AIOps 本地快照状态，不等同于直接修改 IM 主服务真实账号状态。

## 5. User 列表与详情字段

用户列表和详情按前端 `User` 结构返回，字段包括：

```json
{
  "id": "admin",
  "username": "admin",
  "isBanned": false,
  "lastLogin": null,
  "onlineStatus": "offline",
  "registeredAt": "2026-06-24",
  "riskLevel": "normal",
  "todayMessages": 0
}
```

联调注意事项：

- `id` 按字符串处理。
- `lastLogin` 可能为 `null`。
- `registeredAt` 是日期字符串；如前端需要 Date 对象，请在客户端转换。
- 私聊消息明文仍不会提供。

## 6. Auth Login 与 Me 字段

`POST /api/v1/auth/login` 的 `user` 与 `GET /api/v1/auth/me` 已补齐：

- `avatar`
- `email`
- `permissions`

这些字段可能为空值或空数组，前端应按可选字段处理。

## 7. Settings Config Update Reason

`PUT /api/v1/settings/configs/{key}` 请求体已支持 `reason`：

```json
{
  "value": 60,
  "confirm": true,
  "reason": "降低检测频率以配合压测窗口"
}
```

当前真实 key 示例：

```text
PUT /api/v1/settings/configs/aiops.detect_interval_sec
```

不同环境可用配置 key 可能不同，前端应以配置列表接口返回的 key 为准，不要写死示例 key。

## 8. Alerts Batch Suppress 字符串 ID

`POST /api/v1/alerts/batch-suppress` 已兼容字符串 ID 数组：

```json
{
  "ids": ["1", "2", "3"],
  "reason": "同一维护窗口批量抑制"
}
```

后端会转换合法字符串 ID；非法 ID 会返回参数错误，不再表现为框架级 `422`。

## 9. 已实现但前端未接入接口

以下属于“已实现接口未接入项”，非本轮后端修复范围：

- `POST /api/v1/agent/trigger`
- `PUT /api/v1/alerts/{id}/resolve`
- `GET /api/v1/alert-groups`
- `GET /api/v1/exports/{id}/download`

前端可在后续迭代按页面需求接入。

## 验证命令

本轮后端修复计划建议验证：

```bash
rtk uv run pytest tests/test_api_contract_parity.py tests/test_users_api.py tests/test_users_repository.py tests/test_m6_users_migration.py
rtk uv run pytest
rtk uv run ruff check .
rtk uv run mypy app
```

文档任务本身的验收命令：

```bash
rtk rg "backend-fixes-solution|SSE 日志流兼容|UserRepository|test_users_repository|字符串 ID" docs/backend-fixes-solution.md docs/codebase/api/README.md docs/codebase/data/README.md docs/codebase/tests/README.md
```
