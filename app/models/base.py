"""SQLAlchemy 2.0 声明式基类（Alembic metadata 源）。"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    pass
