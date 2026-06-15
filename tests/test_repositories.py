import uuid

from sqlalchemy import Column, String, select
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base
from app.repositories.base import ProjectScopedRepository


class _Dummy(Base):
    """仅测试用的带 project_id 的表。"""

    __tablename__ = "dummy_scoped"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String(64), nullable=False)
    name = Column(String(64))


class _DummyRepo(ProjectScopedRepository):
    project_column = _Dummy.project_id


def test_scope_injects_project_filter():
    repo = _DummyRepo(session=None, project_id="p-123")  # session 不参与本断言
    stmt = repo.scope(select(_Dummy))
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    # 注入了 WHERE ... project_id = 'p-123'
    assert "project_id" in compiled
    assert "p-123" in compiled


def test_repo_binds_project_id():
    repo = _DummyRepo(session=None, project_id="p-xyz")
    assert repo.project_id == "p-xyz"
