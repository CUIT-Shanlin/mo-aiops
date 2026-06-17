async def test_metrics_exposes_prometheus_text_after_api_request(client, make_jwt):
    headers = {"Authorization": f"Bearer {make_jwt(role='admin')}"}

    api_response = await client.get("/api/v1/projects", headers=headers)
    assert api_response.status_code == 200

    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "mo_chat_aiops_http_requests_total" in response.text
    assert 'method="GET"' in response.text
    assert 'path="/api/v1/projects"' in response.text
    assert 'status="200"' in response.text
    assert "mo_chat_aiops_http_request_duration_seconds_sum" in response.text
    assert "mo_chat_aiops_http_request_duration_seconds_count" in response.text


async def test_metrics_endpoint_is_not_counted_by_metrics(client):
    first = await client.get("/metrics")
    second = await client.get("/metrics")

    assert first.status_code == 200
    assert second.status_code == 200
    assert 'path="/metrics"' not in second.text
    assert first.text == second.text


async def test_metrics_uses_route_template_for_dynamic_paths(client, make_jwt):
    headers = {
        "Authorization": f"Bearer {make_jwt(role='admin')}",
        "X-Project-Id": "proj-1",
    }

    await client.get("/api/v1/rca/1", headers=headers)
    await client.get("/api/v1/rca/2", headers=headers)

    response = await client.get("/metrics")

    assert response.status_code == 200
    assert 'path="/api/v1/rca/{run_id}"' in response.text
    assert 'path="/api/v1/rca/1"' not in response.text
    assert 'path="/api/v1/rca/2"' not in response.text


async def test_metrics_uses_fixed_label_for_unmatched_routes(client):
    await client.get("/api/v1/does-not-exist/1")
    await client.get("/api/v1/does-not-exist/2")

    response = await client.get("/metrics")

    assert response.status_code == 200
    assert 'path="__unmatched__"' in response.text
    assert 'path="/api/v1/does-not-exist/1"' not in response.text
    assert 'path="/api/v1/does-not-exist/2"' not in response.text
