from app.collectors.metric_store import load_metric_values, save_metric_snapshot
from app.collectors.windows import MetricWindowStore
from app.core.constants import RedisKey


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


async def test_save_and_load_from_redis():
    redis = FakeRedis()
    await save_metric_snapshot(redis, "mochat-prod", {"msg.throughput": 12600.0})
    assert RedisKey.of("mochat-prod", RedisKey.METRICS_SNAPSHOT) in redis.store
    values = await load_metric_values(redis, None, "mochat-prod")
    assert values["msg.throughput"] == 12600.0


async def test_load_prefers_memory():
    redis = FakeRedis()
    await save_metric_snapshot(redis, "mochat-prod", {"msg.throughput": 1.0})
    store = MetricWindowStore()
    store.add("mochat-prod", "msg.throughput", 999.0)
    values = await load_metric_values(redis, store, "mochat-prod")
    assert values["msg.throughput"] == 999.0


async def test_load_bad_value_returns_empty():
    redis = FakeRedis()
    redis.store[RedisKey.of("mochat-prod", RedisKey.METRICS_SNAPSHOT)] = "not-json"
    assert await load_metric_values(redis, None, "mochat-prod") == {}
