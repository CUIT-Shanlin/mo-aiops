# tests 模块

## 测试基础

配置来自 `pyproject.toml`：

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

主要测试依赖：

- `pytest`
- `pytest-asyncio`
- `httpx`
- `ruff`
- `mypy`

## 关键夹具

`tests/conftest.py` 提供：

- 每个测试清理 settings cache 和 projects config。
- `app_instance` 运行 FastAPI lifespan。
- `client` 使用 `httpx.AsyncClient + ASGITransport`。
- `make_jwt` 生成 admin JWT。
- Redis 测试后清理 `aiops:*` key。

## 关键测试文件

| 文件 | 覆盖内容 |
|---|---|
| `test_repositories.py` | `ProjectScopedRepository.scope()` |
| `test_alerts_repository.py` | 告警、自愈、审计、通知仓库隔离和 upsert |
| `test_agent_runs_repository.py` | AgentRun 创建、完成、跨项目隔离 |
| `test_users_repository.py` | 本地用户快照、筛选、封禁状态和跨项目隔离 |
| `test_migrations.py` | Alembic head 和 projects 表移除 |
| `test_m5_migrations.py` | saved_queries migration |
| `test_m6_users_migration.py` | users migration 和唯一约束 |
| `test_db.py` | engine/session/ping |
| `test_alerts_api.py` | 告警 API 和状态操作 |
| `test_agent_api.py` | Agent API 和建议状态 |
| `test_healing_api.py` | 自愈 API |
| `test_audit_notifications_api.py` | 审计和通知 |
| `test_rca_api.py` | RCA |
| `test_exports_api.py` | 导出 |
| `test_logs_api.py` | 日志 |
| `test_ws_realtime.py` / `test_metrics_ws.py` | WebSocket |
| `test_collectors_*` | 采集器 |
| `test_providers_*` | Provider |
| `test_api_contract_parity.py` | 关键 API 路由契约 |
| `test_m6_docs.py` | 文档契约检查 |

## 新增功能测试要求

新增 repository/table：

- migration 文件测试。
- `upgrade head` 后实际表/约束存在性测试。
- 写入自动绑定 `project_id`。
- 跨项目不可见。
- 唯一约束同项目冲突、跨项目不冲突。

新增 API：

- 未认证。
- 缺失 `X-Project-Id`。
- 未知/disabled project。
- 跨项目不可读不可改。
- 响应信封。
- 审计写入。
- 前端契约兼容测试需覆盖可选 query 参数、字符串 ID 兼容、字段命名与统一响应信封。

新增 Redis/WS/collector：

- Redis key 必须带 `aiops:{project_id}`。
- WebSocket 必须要求 token 和 project_id。
- 一个项目失败不能阻塞其他项目。
