# data 模块

## 职责

数据层包括 `app/models`、`app/repositories` 和 `app/db/migrations`。当前没有 SQLAlchemy relationship 和 DB 外键，关系由逻辑字段和 project-scoped repository 维护。

## 主要表

| 表 | 模型 | 说明 |
|---|---|---|
| `agent_runs` | `AgentRun` | Agent 执行记录、根因、证据链、自愈建议 |
| `alert_events` | `AlertEvent` | 告警事件和活跃告警去重 |
| `heal_actions` | `HealAction` | 自愈动作 |
| `audit_logs` | `AuditLog` | 审计日志，`project_id` 可空 |
| `notifications` | `Notification` | 项目通知 |
| `saved_queries` | `SavedQuery` | 保存的日志查询 |
| `users` | `User` | 项目级本地用户快照，支撑前端用户管理 |

## project_id 隔离

核心基类：

```python
ProjectScopedRepository
```

规则：

- 构造时绑定 `session + project_id`。
- 子类声明 `project_column`。
- SELECT 通过 `self.scope(stmt)` 注入 `WHERE project_id = self.project_id`。
- 写入时使用 repository 自身的 `project_id`，不要从请求体接收。

已继承该基类：

- `AgentRunRepository`
- `AlertEventRepository`
- `HealActionRepository`
- `NotificationRepository`
- `SavedQueryRepository`
- `UserRepository`

例外：

- `AuditLogRepository` 不继承基类，因为 `audit_logs.project_id` 可空；它用 `_scope()` 区分全局审计和项目审计。

## 关键约束

- `alert_events` 使用部分唯一索引实现活跃告警更新式去重。
- `heal_actions` 对非空告警动作使用唯一约束防止重复自愈。
- `saved_queries` 使用 `project_id + name` 唯一。
- `users` 使用 `(project_id, id)` 唯一约束，同一用户 ID 可存在于不同项目。
- 跨表 join 必须两侧都按同一 `project_id` 过滤。

## Migration 链

| revision | 说明 |
|---|---|
| `0001_create_projects` | 创建旧 projects 表 |
| `0002_drop_projects` | 删除 projects 表，项目配置改为 YAML |
| `0003_create_agent_runs` | 创建 agent_runs |
| `0004_create_m4_alert_healing_audit` | 创建 alert_events、heal_actions、audit_logs、notifications |
| `0005_create_m5_saved_queries` | 创建 saved_queries |
| `0006_create_users` | 创建 users |

## 新增表要求

- 项目级表必须包含 `project_id NOT NULL`。
- 必须建立按 `project_id` 查询的索引。
- 必须补 migration 测试和 repository 隔离测试。
- 如存在逻辑关联 id，必须在 service/repository 层校验同项目。
