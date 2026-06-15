"""代码内执行 Alembic upgrade head（启动时调用）。"""
from pathlib import Path

from alembic import command
from alembic.config import Config

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_upgrade_head() -> None:
    """绝对路径修正 script_location 后 upgrade 到最新。

    注意：env.py 在线模式用 asyncio.run() 建临时 loop 跑迁移。
    因此本函数必须在「没有运行中的事件循环」的线程里调用——
    在 FastAPI lifespan / async 测试中要用 `await asyncio.to_thread(run_upgrade_head)`，
    否则会触发 RuntimeError: asyncio.run() cannot be called from a running event loop。
    """
    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(_PROJECT_ROOT / "app" / "db" / "migrations")
    )
    command.upgrade(cfg, "head")
