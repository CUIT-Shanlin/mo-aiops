from app.models.base import Base
from app.models.project import Project


def test_project_table_registered():
    assert "projects" in Base.metadata.tables


def test_project_columns():
    cols = Base.metadata.tables["projects"].columns.keys()
    for c in ["id", "name", "slug", "enabled", "metric_profile",
              "datasource_config", "created_at", "updated_at"]:
        assert c in cols
