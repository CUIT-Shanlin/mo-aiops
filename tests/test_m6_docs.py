from pathlib import Path


def test_api_docs_mark_m6_export_download_semantics():
    doc = Path("docs/AIOps_API_文档.md").read_text()
    assert "/rca/:id" not in doc
    for path in (
        "/api/v1/rca/{run_id}",
        "/api/v1/rca/{run_id}/evidences",
        "/api/v1/rca/{run_id}/timeline",
        "/api/v1/rca/{run_id}/full-evidence",
        "/api/v1/rca/{run_id}/related-alerts",
        "/api/v1/rca/{run_id}/execute-fix",
        "/api/v1/rca/{run_id}/export",
    ):
        assert path in doc
    assert "/api/v1/rca/{id}/export" not in doc
    assert "/api/v1/exports/{exportId}/download" in doc
    assert "导出文件保存在 Redis，默认 TTL 1 小时" in doc
    assert "M6 已实现" in doc
    assert "前端携带同一 Bearer token 与 X-Project-Id 下载" in doc


def test_api_docs_use_v1_paths_for_final_mvp_parity_routes():
    doc = Path("docs/AIOps_API_文档.md").read_text()
    for path in (
        "POST /api/v1/alerts/webhook",
        "GET /api/v1/alerts/{alert_id}/rca-id",
        "GET /api/v1/alerts/{alert_id}/logs",
        "GET /api/v1/agent/latest-analysis",
        "POST /api/v1/agent/suggestions/{suggestion_id}/accept",
        "POST /api/v1/agent/suggestions/{suggestion_id}/reject",
        "GET /api/v1/agent/suggestions/{suggestion_id}/evidence",
        "GET /api/v1/k8s/namespaces",
        "GET /api/v1/k8s/deployments?namespace=production",
        "PUT /api/v1/notifications/read-all",
        "PUT /api/v1/notifications/{notification_id}/read",
    ):
        assert path in doc

    for old_path in (
        "GET /alerts/:id/rca-id",
        "GET /alerts/:id/logs",
        "GET /agent/latest-analysis",
        "POST /agent/suggestions/:id/accept",
        "GET /k8s/namespaces",
        "PUT /notifications/read-all",
        "PUT /notifications/:id/read",
    ):
        assert old_path not in doc

    assert "MVP 已实现" in doc
    assert "告警关联日志从 Redis recent_errors 缓存读取" in doc
    assert "K8s 列表接口在 Provider 不可用时返回空列表" in doc
    assert "AI 建议接受/拒绝状态持久化到 agent_runs.node_states" in doc


def test_api_docs_mark_project_header_required_for_business_apis():
    doc = Path("docs/AIOps_API_文档.md").read_text()
    assert "### 1.1a 多项目上下文（业务接口必填）" in doc
    assert "业务接口必须携带 `X-Project-Id`" in doc
    assert "auth/projects/metric-profiles" in doc
    assert "未携带时返回项目错误" in doc
    assert "未携带时，后端使用配置文件中的默认项目" not in doc


def test_overview_m6_marks_synchronous_exports_and_metrics():
    doc = Path("docs/aiops_overview_design.md").read_text()
    assert "M6 同步导出" in doc
    assert "mo_chat_aiops_http_requests_total" in doc
    assert "POST /api/v1/seeds/demo" in doc
    assert "异步大文件导出/导出任务历史/对象存储归档" in doc
    assert "ARQ 任务可靠性增强、数据导出" not in doc


def test_overview_marks_project_context_required_for_business_apis():
    doc = Path("docs/aiops_overview_design.md").read_text()
    assert "业务 REST API 必须携带 `X-Project-Id`" in doc
    assert "auth/projects/metric-profiles" in doc
    assert "WebSocket 使用 `?project_id=`" in doc
    assert "缺失业务项目上下文返回项目错误" in doc
    assert "可选 `X-Project-Id`" not in doc
    assert "解析可选请求头 `X-Project-Id`" not in doc
    assert "缺失时回退到默认项目" not in doc
