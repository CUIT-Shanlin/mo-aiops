from __future__ import annotations


def test_mvp_api_contract_routes_are_registered(app_instance):
    route_keys = {
        (method, getattr(route, "path", ""))
        for route in app_instance.routes
        for method in getattr(route, "methods", set())
    }

    expected = {
        ("POST", "/api/v1/alerts/webhook"),
        ("GET", "/api/v1/alerts/{alert_id}/rca-id"),
        ("GET", "/api/v1/alerts/{alert_id}/logs"),
        ("GET", "/api/v1/dashboard/stats"),
        ("GET", "/api/v1/dashboard/health-score"),
        ("GET", "/api/v1/dashboard/metrics/series"),
        ("GET", "/api/v1/dashboard/alerts/recent"),
        ("GET", "/api/v1/dashboard/events"),
        ("GET", "/api/v1/dashboard/ai-agent/status"),
        ("GET", "/api/v1/dashboard/service-chain"),
        ("GET", "/api/v1/metrics/categories"),
        ("GET", "/api/v1/metrics/{name}/series"),
        ("GET", "/api/v1/traces"),
        ("GET", "/api/v1/traces/{trace_id}"),
        ("GET", "/api/v1/traces/{trace_id}/spans"),
        ("GET", "/api/v1/traces/services"),
        ("GET", "/api/v1/agent/stats"),
        ("GET", "/api/v1/agent/current-task"),
        ("GET", "/api/v1/agent/workflow/steps"),
        ("GET", "/api/v1/agent/latest-analysis"),
        ("POST", "/api/v1/agent/suggestions/{suggestion_id}/accept"),
        ("POST", "/api/v1/agent/suggestions/{suggestion_id}/reject"),
        ("GET", "/api/v1/agent/suggestions/{suggestion_id}/evidence"),
        ("GET", "/api/v1/k8s/overview"),
        ("GET", "/api/v1/k8s/pods"),
        ("GET", "/api/v1/k8s/nodes"),
        ("GET", "/api/v1/k8s/namespaces"),
        ("GET", "/api/v1/k8s/deployments"),
        ("GET", "/api/v1/notifications"),
        ("PUT", "/api/v1/notifications/read-all"),
        ("POST", "/api/v1/notifications/{notification_id}/read"),
        ("PUT", "/api/v1/notifications/{notification_id}/read"),
        ("GET", "/api/v1/users/stats"),
        ("GET", "/api/v1/users"),
        ("POST", "/api/v1/users"),
        ("GET", "/api/v1/users/{user_id}"),
        ("PUT", "/api/v1/users/{user_id}"),
        ("DELETE", "/api/v1/users/{user_id}"),
        ("POST", "/api/v1/users/{user_id}/ban"),
        ("POST", "/api/v1/users/{user_id}/unban"),
        ("PUT", "/api/v1/logs/queries/{query_id}"),
        ("DELETE", "/api/v1/logs/queries/{query_id}"),
        ("GET", "/api/v1/settings/configs/{name}/history"),
    }

    assert expected <= route_keys
