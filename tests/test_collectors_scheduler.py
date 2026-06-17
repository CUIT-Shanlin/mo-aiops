import json
import asyncio

from app.collectors.models import MetricSample
from app.collectors.scheduler import CollectorScheduler
from app.core.projects import ProjectConfig, ProjectsConfig


class FakeRedis:
    def __init__(self):
        self.published = []

    async def publish(self, channel, message):
        self.published.append((channel, json.loads(message)))
        return 1


class PublishFailingRedis(FakeRedis):
    async def publish(self, channel, message):
        if channel == "aiops:prod:ws:metrics":
            raise RuntimeError("publish down")
        return await super().publish(channel, message)


class FakeProvider:
    def __init__(self, datasource_type):
        self.datasource_type = datasource_type
        self.closed = False

    async def close(self):
        self.closed = True


def fake_provider_factory(project_id, project):
    return {
        "prometheus": FakeProvider("prometheus"),
        "loki": FakeProvider("loki"),
        "tempo": FakeProvider("tempo"),
    }


async def fake_metrics_collector(**kwargs):
    return [
        MetricSample(
            canonicalName=f"{kwargs['project_id']}.conn.active",
            value=1.0,
        )
    ]


async def fake_logs_collector(**kwargs):
    return [], set()


async def fake_traces_collector(**kwargs):
    return {}


async def test_scheduler_collect_once_iterates_enabled_projects_and_publishes_metrics():
    redis = FakeRedis()
    config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "disabled": ProjectConfig(
                name="Disabled",
                metric_profile="java",
                enabled=False,
            ),
            "stage": ProjectConfig(name="Stage", metric_profile="java"),
        },
    )
    scheduler = CollectorScheduler(
        redis=redis,
        projects_config=config,
        provider_factory=fake_provider_factory,
        metrics_collector=fake_metrics_collector,
        logs_collector=fake_logs_collector,
        traces_collector=fake_traces_collector,
    )

    results = await scheduler.collect_once()

    assert [result.project_id for result in results] == ["prod", "stage"]
    assert [call[0] for call in redis.published] == [
        "aiops:prod:ws:metrics",
        "aiops:stage:ws:metrics",
    ]
    assert redis.published[0][1]["metrics"][0]["canonicalName"] == "prod.conn.active"


async def test_scheduler_keeps_other_projects_when_one_fails():
    async def failing_metrics_collector(**kwargs):
        if kwargs["project_id"] == "prod":
            raise RuntimeError("boom")
        return [MetricSample(canonicalName="conn.active", value=2.0)]

    redis = FakeRedis()
    config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "stage": ProjectConfig(name="Stage", metric_profile="java"),
        },
    )
    scheduler = CollectorScheduler(
        redis=redis,
        projects_config=config,
        provider_factory=fake_provider_factory,
        metrics_collector=failing_metrics_collector,
        logs_collector=fake_logs_collector,
        traces_collector=fake_traces_collector,
    )

    results = await scheduler.collect_once()

    assert results[0].project_id == "prod"
    assert results[0].errors == ["metrics: RuntimeError"]
    assert results[1].project_id == "stage"
    assert results[1].metrics[0].value == 2.0
    assert [call[0] for call in redis.published] == ["aiops:stage:ws:metrics"]


async def test_scheduler_keeps_other_projects_when_provider_factory_fails():
    def partially_failing_provider_factory(project_id, project):
        if project_id == "prod":
            raise RuntimeError("bad datasource")
        return fake_provider_factory(project_id, project)

    redis = FakeRedis()
    config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "stage": ProjectConfig(name="Stage", metric_profile="java"),
        },
    )
    scheduler = CollectorScheduler(
        redis=redis,
        projects_config=config,
        provider_factory=partially_failing_provider_factory,
        metrics_collector=fake_metrics_collector,
        logs_collector=fake_logs_collector,
        traces_collector=fake_traces_collector,
    )

    results = await scheduler.collect_once()

    assert [result.project_id for result in results] == ["prod", "stage"]
    assert results[0].errors == ["providers: RuntimeError"]
    assert results[1].metrics[0].canonicalName == "stage.conn.active"
    assert [call[0] for call in redis.published] == ["aiops:stage:ws:metrics"]


async def test_scheduler_records_publish_failure_and_keeps_other_projects():
    redis = PublishFailingRedis()
    config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "stage": ProjectConfig(name="Stage", metric_profile="java"),
        },
    )
    scheduler = CollectorScheduler(
        redis=redis,
        projects_config=config,
        provider_factory=fake_provider_factory,
        metrics_collector=fake_metrics_collector,
        logs_collector=fake_logs_collector,
        traces_collector=fake_traces_collector,
    )

    results = await scheduler.collect_once()

    assert [result.project_id for result in results] == ["prod", "stage"]
    assert results[0].errors == ["publish: RuntimeError"]
    assert results[1].metrics[0].canonicalName == "stage.conn.active"
    assert [call[0] for call in redis.published] == ["aiops:stage:ws:metrics"]


async def test_scheduler_run_forever_exits_after_stop():
    redis = FakeRedis()
    config = ProjectsConfig(
        default_project="prod",
        projects={"prod": ProjectConfig(name="Prod", metric_profile="java")},
    )
    scheduler = CollectorScheduler(
        redis=redis,
        projects_config=config,
        provider_factory=fake_provider_factory,
        interval_seconds=3600,
    )
    collected = asyncio.Event()
    calls = 0

    async def fake_collect_once():
        nonlocal calls
        calls += 1
        collected.set()
        return []

    scheduler.collect_once = fake_collect_once
    task = asyncio.create_task(scheduler.run_forever())

    await asyncio.wait_for(collected.wait(), timeout=1)
    await scheduler.stop()
    await asyncio.wait_for(task, timeout=1)

    assert calls == 1
    assert task.done()
    assert task.cancelled() is False


async def test_scheduler_run_forever_continues_after_collect_once_failure():
    redis = FakeRedis()
    config = ProjectsConfig(
        default_project="prod",
        projects={"prod": ProjectConfig(name="Prod", metric_profile="java")},
    )
    scheduler = CollectorScheduler(
        redis=redis,
        projects_config=config,
        provider_factory=fake_provider_factory,
        interval_seconds=0.01,
    )
    recovered = asyncio.Event()
    calls = 0

    async def flaky_collect_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        recovered.set()
        return []

    scheduler.collect_once = flaky_collect_once
    task = asyncio.create_task(scheduler.run_forever())

    await asyncio.wait_for(recovered.wait(), timeout=1)
    await scheduler.stop()
    await asyncio.wait_for(task, timeout=1)

    assert calls >= 2
    assert task.done()
    assert task.cancelled() is False


async def test_scheduler_stop_ignores_non_callable_close_and_awaits_async_close():
    redis = FakeRedis()
    config = ProjectsConfig(
        default_project="prod",
        projects={"prod": ProjectConfig(name="Prod", metric_profile="java")},
    )
    scheduler = CollectorScheduler(
        redis=redis,
        projects_config=config,
        provider_factory=fake_provider_factory,
    )
    async_provider = FakeProvider("prometheus")
    scheduler._providers["prod"] = {
        "prometheus": async_provider,
        "loki": object(),
    }

    await scheduler.stop()

    assert async_provider.closed is True
