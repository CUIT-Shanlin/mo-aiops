"""CLI 入口：在 pod 内直连 DB/Redis 注入演示数据，绕开 prod 禁用的 HTTP 接口。

用法:
    python -m app.seeds.run --project mochat-prod --reset
"""
from __future__ import annotations

import argparse
import asyncio
import json

from app.core.config import get_settings
from app.core.db import create_engine, make_sessionmaker
from app.core.redis import create_redis
from app.seeds.service import SeedService


async def run_seed(project_id: str, reset: bool) -> dict[str, int]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    redis = create_redis(settings.redis_url, db=settings.redis_db)
    try:
        async with sessionmaker() as session:
            counts = await SeedService(session, redis).seed_demo(project_id, reset=reset)
        return counts
    finally:
        await engine.dispose()
        await redis.aclose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Inject demo data into mo-chat-aiops")
    parser.add_argument("--project", default="mochat-prod", help="目标 project_id")
    parser.add_argument("--reset", action="store_true", help="注入前清空该项目已有数据")
    args = parser.parse_args(argv)
    counts = asyncio.run(run_seed(args.project, args.reset))
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
