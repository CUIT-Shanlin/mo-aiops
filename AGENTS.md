# AGENTS.md — mo-chat-aiops 开发指南

> 本文件是 AI agent 在本仓库工作的权威指南。开始任何编码前必读。
> 配套文档：后端需求 `docs/aiops_requirements.md`、前端规范 `docs/aiops前端规范.md`、Keep 架构借鉴 `docs/keep_architecture.md`。

---

## 1. 项目定位

- **项目名**：`mo-chat-aiops`
- **类型**：独立 Python 后端服务（FastAPI），mo-chat IM 平台的运维智能体。
- **职责**：采集 Prometheus/Loki/Tempo/K8s 数据 → LangGraph Agent 异常检测/根因分析 → 告警去重与持久化 → K8s 自愈（含风险审批/重试/恢复验证）→ 审计 → 通过 REST + WebSocket 向前端 AIOps Console 提供数据。
- **核心闭环**：见 `aiops_requirements.md` §1。
- **非职责（不要在本仓库做）**：
  - 不实现前端 UI（前端由他人开发，本仓库只提供对接契约与联调支持）。
  - 不实现 IM 客户端 / IM 主业务（属 Java 主服务）。
  - 不管理 Grafana Dashboard / Alert Rules（运维独立维护）。
  - 不展示私聊消息明文（端到端加密，物理上无法解密，且不提供该接口）。

---

## 2. 两大架构能力（v1.1 新增，贯穿全局）

### 2.1 多项目隔离
- 一套 AIOps 控制台**管理多个被监控项目（project）**，每个 project 是一套独立部署，有自己的数据源连接 + 告警/自愈/审计数据，互相隔离。
- **隔离机制：单库 + `project_id` 行级隔离**。所有业务表带 `project_id NOT NULL`，每次查询强制 `WHERE project_id = ?`；Redis key 一律 `aiops:{project_id}:*`；采集/Agent 循环按 project 遍历。
- **API 维度**：除 `/api/projects*`、`/api/metric-profiles` 外，所有业务接口必须带请求头 `X-Project-Id`；WebSocket 带 `?project_id=`。
- project 是「被监控目标」，**不是** SaaS 多租户；用户/管理员全局。详见 `aiops_requirements.md` §2.1、§4.10。

### 2.2 指标 Profile 抽象（语言无关）
- 采集/分析/评分**只引用 canonical 指标名**（如 `runtime.memory_used_ratio`、`msg.p99_latency`、`mq.backlog`），由 **Metric Profile** 映射到具体 PromQL。
- **代码内置 profile 注册表**；MVP 只实现 `java` profile；新增 `go`/`nodejs` = 加一个注册项，不改采集/分析逻辑。
- 每个 project 在 `projects.metric_profile` 绑定一个 profile。详见 `aiops_requirements.md` §2.2、§3.1。
- **禁止**在采集器/评分/Agent 代码里硬编码 `jvm_*` 等具体指标名——必须经 profile 解析。

---

## 3. 技术栈与依赖管理

### 3.1 项目管理：uv（强制）
- 本项目由 **uv** 管理，已 `uv init`，使用项目 `.venv`。
- **添加依赖**：`uv add <pkg>`（开发依赖 `uv add --dev <pkg>`）。**禁止** `pip install`。
- **运行**：`uv run <cmd>`（如 `uv run python -m app`、`uv run pytest`、`uv run uvicorn app.main:app --reload`）。
- **同步环境**：`uv sync`。
- Python 版本以项目 `.python-version`（**3.13**）+ `pyproject.toml` 的 `requires-python` 为准；需求文档基线写 3.11+，本项目实际锁 3.13。

### 3.2 核心依赖（已安装；新增按 `uv add`）

> 首批依赖已装好，见 `pyproject.toml`。下表为映射；版本以 `pyproject.toml`/`uv.lock` 为准。

| 用途 | 包 |
|---|---|
| Web/ASGI | `fastapi`、`uvicorn[standard]` |
| 数据校验/配置 | `pydantic`（随 fastapi）、`pydantic-settings` |
| 异步 HTTP | `aiohttp`（调 Prometheus/Loki/Tempo） |
| ORM/PostgreSQL | `sqlalchemy[asyncio]`、`asyncpg`（异步连接池，min 5/max 20） |
| 迁移 | `alembic`（启动时自动 upgrade） |
| Redis | `redis`（async 客户端，固定用 DB 1） |
| K8s | `kubernetes-asyncio`（**异步**客户端；官方 `kubernetes` 是同步的，与全异步约束冲突，故不用） |
| Agent | `langgraph`、`langchain`、`langchain-openai`（Qwen 走 OpenAI 兼容接口） |
| JWT | `pyjwt`（`python-jose` 已停维护，不用） |
| 凭证加密 | `cryptography`（数据源凭证加密存储） |
| 测试/质量（dev） | `pytest`、`pytest-asyncio`、`httpx`、`ruff`、`mypy` |

> LLM 接入用 LangChain `BaseChatModel` 抽象，**不硬编码厂商**；Qwen 首选，OpenAI 可选。见 `aiops_requirements.md` §3.9。

### 3.3 环境变量与配置约定

- **启动配置（env，pydantic-settings）**：`DATABASE_URL`（AIOps 持久化库 DSN）、`REDIS_URL`、**`REDIS_DB=1`**（AIOps 固定用 Redis DB 1，与 Java 主服务 DB 0 隔离，见需求 §3.8/§7）、`JWT_SECRET`（与 Java 主服务共享，见需求 §4.5）、`JAVA_SERVICE_INTERNAL_TOKEN`（服务间调用，见需求 §4.9）、LLM 接入（`LLM_PROVIDER`/`LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`、全局非 per-project）、`CORS_ORIGINS`。
- **运行时热更新配置（不是 env）**：检测周期、Z-score 阈值、自愈开关等存 Redis Hash `aiops:{project_id}:config`，通过 `PUT /api/config/{key}` 改，**按项目独立**（需求 §4.8）。代码里把这类放在配置服务/仓库，不要塞进 pydantic-settings 静态配置。
- **项目数据源连接**：不走 env，存 `projects.datasource_config`（DB，凭证加密），运行时按项目读取（需求 §3.0/§4.10）。
- **自身可观测性**：服务必须暴露自己的 `/metrics`（Prometheus 格式：API 延迟、Agent 运行成功率、LLM 调用次数/延迟）+ 结构化 JSON 日志（`service=mo-chat-aiops`），见需求 §5。

---

## 4. 建议的代码结构

> 尚未建立 `app/` 包；首次实现时按下列结构搭建，保持单一职责、模块边界清晰。每个文件聚焦一件事，文件变大即拆分。

```
app/
  main.py                  # FastAPI app 入口（lifespan 起采集/Agent 后台任务）
  core/
    config.py              # pydantic-settings 配置（env）
    security.py            # JWT 校验（与 Java 主服务共享 Secret）
    logging.py             # 结构化 JSON 日志（service=mo-chat-aiops）
    db.py                  # asyncpg 连接池
    redis.py               # redis async 客户端
    project_context.py     # X-Project-Id 解析 + 项目上下文依赖
  providers/               # 数据源 Provider 抽象（借鉴 Keep）
    base.py                # BaseProvider：async _query/validate_config/validate_scopes
    factory.py             # 按 datasource_type 约定式实例化
    prometheus.py loki.py tempo.py kubernetes.py
  metrics_profile/         # 指标 Profile 抽象（语言无关）
    base.py registry.py    # canonical 指标 → PromQL 映射 + 注册表
    java.py                # 内置 java profile（MVP 唯一实现）
  collectors/              # 三路采集器（按 project 运行）
    metrics.py logs.py traces.py
  agent/                   # LangGraph StateGraph（8 节点：3 采集+异常检测+RAG+根因+自愈决策+证据汇编）
    state.py graph.py nodes.py llm.py rag.py runs.py
  alerts/                  # 告警状态机 + fingerprint 更新式去重 + 聚合分组（查询期）
  healing/                 # K8s 自愈：动作/风险审批/重试/恢复验证
  k8s/                     # 集群资源查询
  topology/                # 服务拓扑动态计算（Tempo 边 + Prom 指标叠加 + K8s 元数据）
  rca/                     # RCA 证据链/时间线汇编 + 查询
  users/                   # 被监控项目主服务用户管理代理
  projects/                # 项目 CRUD + 数据源连接管理（多项目核心）
  metrics/                 # Prometheus 代理 + 健康评分
  schemas/                 # Pydantic 请求/响应模型（统一响应包装）
  api/                     # REST 路由
  ws/                      # WebSocket（alerts/agent/heal）+ Redis 消费/PubSub
  repositories/            # PostgreSQL 数据访问（全部带 project_id）
  db/migrations/           # Alembic
tests/                     # 单元 + 集成 + API 测试
```

---

## 5. 从 Keep 借鉴什么（详见 docs/keep_architecture.md）

**直接照搬**：
1. **双哈希思想**：区分 `fingerprint`（实体身份）与"内容是否变化"——用于判断"同一问题 vs 内容变了"。**注意**：我们落地为**更新式去重**（每 `(project_id, fingerprint)` 活跃告警一行，重复仅更新 `last_seen_at`），**不照搬 Keep 的 append-only 双表**。详见 `keep_architecture.md` §2.3、需求 §4.3。
2. **Provider 抽象**：薄基类 + 能力按方法覆盖探测 + `<Name>AuthConfig` + 约定式工厂。
3. **K8s 单参数分派**：`_query(command_type, ...)` / `_notify(action, ...)`，一个类覆盖几十个操作。
4. **enqueue-and-202**：告警接入快速入队（Redis List `aiops:{project_id}:alerts`），异步处理。
5. **LLM-as-provider**：每模型一个薄 `_query`，结构化 JSON 输出。
6. **硬指标 Python 算 / LLM 只做模糊总结**：低温度 + 固定 seed + 限制输入大小。
7. **DB 唯一约束做幂等**：防止对同一告警重复触发 K8s 自愈。
8. **拓扑从 trace 动态算**：Tempo span 父子聚合成依赖边（比 Keep 静态拉取更强，见需求 §3.10）。
9. **启动时自动 Alembic 迁移**。

> **不照搬 append-only**：Keep 用不可变 `Alert` 历史 + `LastAlert` 指针；我们用单表 upsert（更新式）。若未来要告警变更历史，另建审计/快照表，不要把 `alert_events` 改成 append-only。Keep 也没有 `alert_hash` 内容哈希——我们只需 `fingerprint`。

**明确不抄（Keep 的过度设计）**：132 个 Provider（只做 4 个）、几十个通知渠道、多租户（改 project_id 行级隔离）、三方言 DB（只 PostgreSQL/asyncpg）、同步 ORM+线程池（全异步）、Pusher（用原生 WebSocket）、完整 YAML 工作流引擎（用 LangGraph）、Elasticsearch 双搜索、CEL 规则引擎（MVP 不要）。

**我们比 Keep 多做**：内生 LangGraph Agent（8 节点，含 RAG + 证据汇编）、K8s 自愈闭环（风险审批/重试/恢复验证）、更丰富的告警状态机、`agent_runs` 持久化（结构化证据链 + 时间线 + 工具调用 + RAG）+ 节点级 WebSocket 推送、从 trace 动态算服务拓扑。

---

## 6. 关键开发约束（硬规则）

1. **全异步**：所有 I/O 用 async（aiohttp/asyncpg/redis async）；async 路径里禁止阻塞调用。
2. **多项目隔离**：每个仓库/查询/Redis key 必须带 `project_id`；新写数据访问层强制带 `WHERE project_id`。漏掉 = bug。
3. **指标经 profile 解析**：禁止硬编码 `jvm_*` 等具体指标名；引用 canonical 名，由 profile 翻译。
4. **统一响应格式**（前端依赖，见需求 `aiops_requirements.md` §9 前端对接约定）：
   - 成功 `{"code": 0, "data": ...}`；失败 `{"code": int, "message": str, "detail": any}`
   - 分页 `{"total": int, "page": int, "size": int, "items": [...]}`
   - 时间一律 ISO 8601 UTC（`2026-06-11T10:00:00Z`）
5. **外部系统容错**：Prometheus/Loki/Tempo/K8s 不可达不能让服务崩溃，记 WARN 跳过；单个项目的连接失败不得阻塞其他项目的采集/分析循环。
6. **LLM 输出必须校验**：用 Pydantic 按 schema 解析，不信任自由文本；失败按需求重试（指数退避，最多 3 次）。
7. **高风险操作必须审计 + 二次确认**：node_evict / rolling_restart / 用户封禁 / 开自动自愈 / 删除项目等写 `audit_logs`；高风险自愈走 `waiting_approval`。（限流等配置项属延后范围，见 §8）
8. **私聊明文永不提供接口**。
9. **K8s ServiceAccount 最小权限**（见需求 §3.6）。
10. **网络暴露端点必须鉴权**：REST 用 Bearer JWT，WebSocket 用 `?token=`；不要创建无鉴权端点。

---

## 7. 测试与完成标准

- 新增功能/修 bug **必须写测试**（`pytest` + `pytest-asyncio`，API 用 `httpx.AsyncClient`）。
- 重点测试：fingerprint 计算、告警状态机转换、自愈风险审批/重试、统一响应包装、WebSocket 消息结构、**project_id 隔离**（跨项目不串数据）、metric profile 解析。
- 完成前运行 `uv run pytest`；若引入 ruff/mypy，则运行 `uv run ruff check .` / `uv run mypy app`。
- 报告结果要忠实：测试失败就说失败并贴输出；跳过的步骤要讲明。
- 清理临时文件，不留调试产物。

---

## 8. MVP 范围与预留

**MVP 实现**：
- 多项目（project_id 隔离 + 项目 CRUD + 数据源连接管理 + 连通性校验 + 生命周期）
- Java metric profile（唯一）
- 四数据源 Provider（Prometheus/Loki/Tempo/K8s）
- LangGraph 8 节点 Agent（3 采集 + 异常检测 + RAG 检索 + 根因 + 自愈决策 + 证据汇编）、三路采集器
- 告警更新式去重 / 状态机 / 聚合分组（查询期）、四种 K8s 自愈、审计
- RCA 结构化证据链 + 故障时间线 + 告警↔agent_run 双向关联（§4.2a）
- 服务拓扑动态计算（Tempo 边 + Prom 指标 + K8s 元数据，§3.10）
- REST + 三个 WebSocket
- **仅 admin 角色**（JWT 只校验 `role=admin`，其他角色预留不实现）

**预留扩展点（不在 MVP 实现，但设计上留口）**：
- `go`/`nodejs` 等 metric profile（注册表加项即可）
- 运维/观察员/AI Agent 等 RBAC 角色 + per-project 权限
- 持久化 incident 实体 + CEL 规则引擎（MVP 用查询期聚合 + Agent 根因归并替代）
- 手动修正/补充服务拓扑（Keep 的 `is_manual`）
- 配置中心里连接层/限流/缓存等来自被监控项目主服务的配置项
- 数据导出、告警策略/自愈策略编辑页（前端 P1）

> 增量开发：解决被要求的问题即可，不要超范围加抽象或防御性代码。较大特性若需改动既有设计，先说明再做。

---

## 9. 文档与协作

- 三份文档发生需求变化时同步更新：`aiops_requirements.md`（后端）、`aiops前端规范.md`（前端）、本文件。
- **Codebase 文档维护（强制）**：`docs/codebase/` 是新会话理解仓库全貌的模块级文档。任何代码变更只要改变模块职责、数据流、接口、配置、数据模型、Redis key、测试策略或重要约束，必须同步更新对应 `docs/codebase/<module>/README.md`；跨模块变更还要更新 `docs/codebase/README.md`。如果代码变更确认不影响 codebase 文档，最终汇报中要明确说明“无需更新 codebase 文档”的理由。
- 前端由他人开发：**契约即文档**——优先维护 OpenAPI（FastAPI 自动生成 `/docs`）+ WebSocket 消息格式说明，作为前端对接与联调的权威来源。
- 联调：开发环境 CORS 放开；提供种子数据/Mock 项目便于前端在无真实数据源时联调。
- `docs/` 已被 `.gitignore` 忽略；需求文档不进版本库，改动需主动告知协作者。
