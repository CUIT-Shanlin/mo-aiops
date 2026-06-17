"""Project-scoped runtime settings service backed by Redis hash overrides."""
from __future__ import annotations

from dataclasses import dataclass
from redis.asyncio import Redis

from app.core.constants import RedisKey

DEFAULT_CONFIGS = [
    {
        "category": "AIOps 配置",
        "key": "aiops.detect_interval_sec",
        "name": "异常检测周期",
        "value": "60",
        "unit": "秒",
        "riskLevel": "medium",
    },
    {
        "category": "AIOps 配置",
        "key": "aiops.auto_heal_enabled",
        "name": "自动自愈开关",
        "value": "开启",
        "unit": "",
        "riskLevel": "high",
    },
    {
        "category": "AIOps 配置",
        "key": "aiops.dedup_window_sec",
        "name": "告警去重窗口",
        "value": "300",
        "unit": "秒",
        "riskLevel": "medium",
    },
]

API_DOC_CATEGORIES = ["连接层配置", "限流配置", "缓存配置", "AIOps 配置"]


@dataclass(slots=True)
class ConfigItem:
    category: str
    key: str
    name: str
    value: str
    unit: str
    risk_level: str

    def to_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "key": self.key,
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "riskLevel": self.risk_level,
        }


class SettingsService:
    """Reads and writes project-scoped runtime settings."""

    def __init__(self, redis: Redis, project_id: str) -> None:
        self.redis = redis
        self.project_id = project_id
        self._defaults = {
            item["key"]: ConfigItem(
                category=item["category"],
                key=item["key"],
                name=item["name"],
                value=item["value"],
                unit=item["unit"],
                risk_level=item["riskLevel"],
            )
            for item in DEFAULT_CONFIGS
        }

    def categories(self) -> list[str]:
        return list(API_DOC_CATEGORIES)

    async def list_configs(self, *, category: str | None = None) -> list[dict[str, str]]:
        overrides = await self.redis.hgetall(RedisKey.of(self.project_id, RedisKey.CONFIG))
        items: list[dict[str, str]] = []
        for item in DEFAULT_CONFIGS:
            if category is not None and item["category"] != category:
                continue
            merged = dict(item)
            override = overrides.get(item["key"])
            if override is not None:
                merged["value"] = _to_text(override)
            items.append(merged)
        return items

    def get_definition(self, key: str) -> ConfigItem | None:
        return self._defaults.get(key)

    async def get_effective_value(self, key: str) -> str | None:
        definition = self.get_definition(key)
        if definition is None:
            return None
        override = await self.redis.hget(RedisKey.of(self.project_id, RedisKey.CONFIG), key)
        return _to_text(override) if override is not None else definition.value

    async def write_override(self, key: str, value: str) -> None:
        await self.redis.hset(RedisKey.of(self.project_id, RedisKey.CONFIG), key, value)


def _to_text(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value
