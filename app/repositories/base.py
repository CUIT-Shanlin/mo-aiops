"""业务仓库基类：构造即绑定 project_id，查询强制注入过滤，杜绝漏写。

M1+ 所有带 project_id 的业务表仓库继承本类，通过 self.scope(stmt) 查询。
projects 表本身无 project_id，是唯一例外，不继承此基类。
"""
from typing import Any

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement


class ProjectScopedRepository:
    """业务表仓库基类。session + project_id 在构造期绑定。"""

    #: 子类指定本表的 project_id 列（如 AlertEvent.project_id）
    project_column: ColumnElement[Any]

    def __init__(self, session: AsyncSession | None, project_id: str) -> None:
        self.session = session
        self.project_id = project_id

    def scope(self, stmt: Select) -> Select:
        """给任意 SELECT 注入 WHERE project_id = self.project_id。"""
        col = type(self).project_column
        return stmt.where(col == self.project_id)
