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
