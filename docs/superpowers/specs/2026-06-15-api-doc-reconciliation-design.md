# 后端文档对齐 API 文档 · 设计方案

> **日期**：2026-06-15
> **主题**：以 `AIOps_API_文档.md`（前端逆向接口契约）为准，反向更新后端三份文档（需求 / 概要设计 / 前端规范）与 M0 已实现代码；保留多项目能力但改为**配置文件定义、无 CRUD**。
> **方案**：A —— API 文档升格为唯一「对外接口契约」真相源，需求/概要设计退回「为什么 / 内部怎么实现」层，互相引用。
> **配套文档**：`docs/AIOps_API_文档.md`、`docs/aiops_requirements.md`、`docs/aiops_overview_design.md`、`docs/aiops前端规范.md`

---

## 0. 背景与问题

仓库内存在两套互相冲突的接口契约口径：

- `AIOps_API_文档.md`：**从前端页面代码逐行逆向**得到，83 个 REST + 6 路实时连接，信息最全（含 TS 类型、枚举速查），但**无多项目概念**、自带 `/auth/*` 登录、全局约定（`/api/v1`、`pageSize`、5 位错误码、`{code,message,data}`、`YYYY-MM-DD HH:mm:ss` 时间）。
- `aiops_requirements.md` (v1.1) / `aiops_overview_design.md` (v1.0)：后端权威设计，强调**多项目 `project_id` 行级隔离 + 项目 CRUD + projects 表**、`/api` 前缀、ISO 8601 UTC、`size` 分页、`{code,message,detail}` 错误体、`X-Project-Id` 强制头、**JWT 由主服务签发 AIOps 仅校验**。
- M0 已实现代码（`app/core/*`、`app/models/project.py`、`migrations/0001`）跟随后端设计，与 API 文档底座约定冲突。

四份文档各自维护接口表 → 系统性漂移。本方案统一收敛为一套契约。

---

## 1. 决策汇总（已与用户确认）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 全局约定冲突 | **全面采纳 API 文档**：`/api/v1` 前缀、`pageSize`、5 位错误码、错误体 `{code,message,data}`、时间 `YYYY-MM-DD HH:mm:ss`、project id 用字符串 |
| 2 | 多项目表达 | **保留多项目**，但项目改**配置文件定义、无 CRUD**；**可选 `X-Project-Id` 头 + 默认回退**，后端统一一处处理 |
| 3 | 认证 | **AIOps 自己做登录**（login/logout/refresh/me），管理员账号放配置/env，AIOps 用自己的 secret 签发 JWT |
| 4 | 额外模块范围 | API 文档全部模块**纳入**；需求/概要设计/Roadmap/前端规范**全部同步更新**；**每个新增模块都要「后端语义研究→概要设计→文档同步」三步**，不止补接口形状 |
| 5 | 文档分工 | **方案 A**：API 文档为唯一接口契约，其余三份引用它 |
| 6 | 前端项目切换器取数 | **新增只读 `GET /api/v1/projects`**（仅列配置项目，无增删改） |

---

## 2. 文档分工（方案 A）

```
AIOps_API_文档.md  ← 唯一「对外接口契约」真相源
  路径 / 请求体 / 响应体 / 枚举 / TS 类型 / WS / SSE
  （仅补：可选 X-Project-Id 头说明 + 只读 GET /api/v1/projects）
        ▲ 引用                          ▲ 引用
aiops_requirements.md            aiops_overview_design.md
 「为什么 / 后端语义」              「内部模块怎么实现」
 去重·状态机·风险审批·             分层·Provider·Profile·
 profile·采集·权限·新模块语义       调度·Roadmap·新模块设计
 §4.5 接口表 → 删，改引用          §3.6 接口表 → 删，改引用
```

- 接口形状只在 API 文档定义一次；需求/概要设计删掉各自接口清单表，改「接口契约见 `AIOps_API_文档.md`，本节只补后端语义/实现」。
- 根治「四份文档四张接口表互相漂移」。

---

## 3. M0 代码回改清单（全面采纳 API 文档约定）

| 文件 | 现状 | 改为（API 文档口径） |
|---|---|---|
| `core/constants.py` | 4 位错误码 4001/4010/4030/4220/5000 | 5 位：`40001` 未登录/Token 失效、`40003` 权限不足、`40004` 资源不存在、`40022` 参数校验、`50000` 内部、`50001` K8s、`50002` 外部服务 |
| `schemas/response.py` | 成功 `{code,data}`；错误 `{code,message,detail}`；`Page.size` | 统一 `{code,message,data}`（成功 `message="success"`）；分页 `{total,page,pageSize,items}`；`APIError` 序列化字段 `detail`→`data` |
| `main.py` | 异常处理器吐 `detail`、4 位码 | 吐 `data`；错误码换 5 位（校验失败 `40022`、兜底 `50000`） |
| `core/project_context.py` | 强制 `X-Project-Id` + UUID 校验，缺失抛 4001 | **可选**头；缺失→配置默认项目；有值→校验是否在配置项目集合内，未知→`40004`；id 改字符串（去 UUID 校验） |
| `api/health.py` | `/health` `/metrics` `/api/whoami` | `/health` `/metrics` 留根路径（探针不进版本前缀）；`whoami` 由真实 `/api/v1/auth/me` 取代 |
| 业务路由前缀 | `/api` | `/api/v1` |
| `models/project.py` + `migrations/0001_create_projects` | projects 表（UUID PK + JSONB） | **删表**：项目改配置文件定义（见 §4） |
| `core/config.py` | 无项目/管理员配置 | 新增：项目配置加载、默认项目 id、管理员账号（见 §4、§5） |

**取舍后果（已确认接受）**：API 文档时间格式 `YYYY-MM-DD HH:mm:ss` 无时区，弱于原 ISO 8601 UTC（丢时区信息）。需求 §9 留说明，前端统一按本地时区理解。

---

## 4. 多项目改为配置文件定义（无 CRUD）

项目定义搬到配置文件（如 `config/projects.yaml`），启动时加载：

```yaml
default_project: mochat-prod      # 缺省 X-Project-Id 时用它
projects:
  mochat-prod:
    name: "mo-chat 生产"
    metric_profile: java
    datasources:
      prometheus: { base_url: "...", username: "...", password: "${PROM_PWD}" }
      loki:       { base_url: "...", auth_type: NoAuth }
      tempo:      { base_url: "..." }
      kubernetes: { mode: token, api_server: "...", token: "${K8S_TOKEN}" }
  mochat-staging:
    name: "mo-chat 预发"
    # ...
```

随之变化：

- **删 `projects` 表**：项目不入库；`project_id` 在其余业务表里是 **VARCHAR 字符串**（= 配置 key），无外键。
- **凭证**：不再 cryptography 加密入库，改配置文件 + **env 变量插值**（`${PROM_PWD}`），靠文件权限/密管保护；`datasource_secret_key` 配置项移除。
- **连通性校验**：从「创建项目时」改为「**启动时**对每项目各数据源探活，失败记 WARN 不阻塞启动」。
- **删** 需求 §4.10 项目 CRUD、概要设计 §3.5 `projects/` CRUD 模块 → 改 `项目配置加载器`。
- **项目选择**：可选 `X-Project-Id` 头（WS 用 `?project_id=`）；缺失→默认项目；统一在 `project_context` 一处处理（有头校验、无头回退）。
- **前端切换器取数**：新增只读 `GET /api/v1/projects`，仅列配置项目（id/name/健康点），无增删改。API 文档补此接口。

---

## 5. 认证模块（AIOps 自签发 JWT）

API 文档 `/auth/login`、`/logout`、`/refresh`、`/me` 由 AIOps 自实现：

- **账号来源**：配置/env（MVP 单 admin）——`admin_username` + `admin_password_hash`（bcrypt 或 sha256）。
- **`POST /auth/login`**：校验账号密码 → AIOps 用自己 `jwt_secret` 签发 `{token, refreshToken, expiresIn}`；`rememberMe` 决定时效（如 8h / 7d）。
- **`POST /auth/refresh`**：校验 refreshToken → 换发新 token 对。
- **`GET /auth/me`**：解析当前 token → `{userId, username, role:"admin", permissions}`。
- **`POST /auth/logout`**：MVP 无状态，前端丢 token 即可，返回成功（黑名单留扩展）。
- `core/security.py` 现有「仅校验」逻辑保留；新增 `auth/` 模块负责签发。需求/概要设计中「JWT 由主服务签发」改写为「**AIOps 自签发，与主服务 JWT 体系解耦**」。

---

## 6. 新增模块（每个都要：研究 → 概要设计 → 文档同步）

| 新增模块 | 要研究/设计的后端语义 | 落到文档 |
|---|---|---|
| 认证 `/auth/*` | 账号来源、JWT 签发/刷新、权限模型 | 需求新增「认证」节 + 概要设计 `auth/` |
| 通知 `/notifications` + `WS /ws/notifications` | 通知来源（告警/自愈/系统）、未读计数、已读状态存哪（Redis/PG）、推送触发点 | 需求新增「通知」节 + 概要设计 `notifications/` |
| `WS /ws/metrics` 指标流 | 推送频率、数据来自采集窗口、按项目命名空间 | 概要设计 ws 节补一路 |
| `SSE /k8s/pods/:name/logs/stream` | K8s watch/follow log 流、背压、断连 | 需求 K8s 节 + 概要设计 k8s 模块 |
| K8s Pod 操作 `/logs` `/restart` `/restart/manual` | 与自愈 executor 复用、审计 executor 区分（AI/管理员） | 需求 K8s + 审计联动 |
| RCA `execute-fix` `full-evidence` `related-alerts` `export` | 执行修复→生成 heal_action、证据下钻、报告导出（pdf/md） | 需求 §4.2a 扩展 |
| 日志 `save query` `/queries` `export` | 已存查询存哪、导出文件生成（csv/json） | 需求日志节 |
| 审计 `export`、告警 `batch-suppress` | 批量操作、导出 | 需求对应节 |
| RAG `similar-cases` | 复用 §4.2 `rag_retrieve`，暴露独立查询接口 | 需求 §3.9/§4.2 |

> 导出类（RCA/日志/审计 export）、SSE、RAG 等较重的排到靠后里程碑，但**文档现在就补全语义**（契约先定，实现分期）。

---

## 7. 逐文档改动

**`AIOps_API_文档.md`**（契约，基本保留）：补 ① 可选 `X-Project-Id` 头说明 ② 只读 `GET /api/v1/projects`。其余不动。

**`aiops_requirements.md`**：
- §2.1 多项目 → 「配置文件定义，无 CRUD，project_id 为字符串配置 key，可选头+默认回退」
- §3.0 数据源 → 配置文件 + env 插值 + 启动时校验
- §3.7 / §6 → 删 projects 表；`project_id` 改 VARCHAR 无外键
- §4.5 接口表 → 删，改引用 API 文档
- §4.10 项目 CRUD → 删，改「配置加载 + 只读列表」
- 新增节：认证、通知、SSE、RCA 扩展、日志/审计导出、RAG 查询
- §8 角色 → admin 不变，补「AIOps 自签发 JWT」
- §9 前端对接 → 时间 `YYYY-MM-DD HH:mm:ss`、分页 `pageSize`、错误体 `{code,message,data}`、`/api/v1`、可选 `X-Project-Id`

**`aiops_overview_design.md`**：
- §1.2 / §2 → 多项目配置化、project_context 可选+默认
- §3.5 `projects/` CRUD → `项目配置加载器`
- §3.6 接口分组表 → 删，改引用 API 文档 + 补新模块设计（`auth/`、`notifications/`、ws metrics、sse）
- §6 数据模型 → 删 projects 表框，project_id 字符串
- §5 Roadmap → 重排（见 §8）

**`aiops前端规范.md`**（轻改）：
- §3.0a → 项目来自只读 `GET /api/v1/projects`，配置驱动
- §3.0b → 原「⚠️ 预留」中已纳入的（导出/通知/策略等）上调为 MVP；认证行改「AIOps 提供登录」

---

## 8. Roadmap 重排

M0 已建但需按本设计回改约定。

| 里程碑 | 内容 |
|---|---|
| **M0+** | 回改全局约定（错误码/响应体/前缀/分页）+ 项目配置化 + 删 projects 表 + 认证模块 |
| M1 | Provider 四件套 + Profile + 配置加载 + 启动连通性校验 + LLM |
| M2 | 三路采集 + 窗口 + Z-score + 按项目遍历 + `WS /ws/metrics` |
| M3 | LangGraph 8 节点 + RAG（含 similar-cases）+ agent_runs |
| M4 | 告警去重/状态机/聚合/batch-suppress + 四自愈 + 审批/重试/验证 + 审计 + 通知系统 |
| M5 | 全量 REST + 三 WS + 通知 WS + SSE 日志流 + 拓扑 + RCA（含 execute-fix/full-evidence/export） |
| M6 | 导出类（日志/审计/RCA report）+ 自身可观测 + 联调种子 + 文档校对 |

---

## 9. 影响与风险

- **时间格式降级**（去时区）是已确认的取舍；跨时区部署时需前端约定统一基准。
- **删 projects 表**意味着已合并的 `migrations/0001_create_projects` 需回退/替换；M0+ 里程碑要处理迁移。
- **认证自签发**与原「主服务签发」解耦，意味着 AIOps 与主服务的 JWT secret 不再强制共享；需在配置中明确 AIOps 自己的 secret。
- 新增模块体量不小（通知/SSE/导出/RAG），契约先行、实现分期，避免前端对接空接口——沿用前端规范 §3.0b「双向标注」习惯，对未实现接口标注里程碑。

---

## 10. 验收标准

- 四份文档不再各自维护接口表；需求/概要设计的接口章节改为引用 API 文档。
- API 文档补齐可选 `X-Project-Id` 头与只读 `GET /api/v1/projects` 两处。
- 所有全局约定（前缀/分页/错误码/错误体/时间）在四份文档中表述一致，且与回改后的 M0 代码一致。
- 多项目「配置文件 + 无 CRUD + 可选头+默认」在需求、概要设计、前端规范三处表述一致。
- 每个新增模块在需求（语义）与概要设计（模块）中均有对应章节，并在 Roadmap 排期。
