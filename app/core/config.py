"""启动期静态配置（pydantic-settings 单一来源）。"""
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """从 .env / 环境变量加载的启动配置。敏感字段禁止进日志。"""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 数据库与缓存
    database_url: str
    redis_url: str
    redis_db: int = 1

    # 鉴权（AIOps 自签发 JWT）
    jwt_secret: str
    jwt_algorithm: str = "HS256"

    # 管理员（MVP 单 admin，AIOps 自签发）
    admin_username: str = "admin"
    admin_password_hash: str = ""  # bcrypt hash，必须通过 env 设置

    # 项目配置文件路径
    projects_config_path: str = "config/projects.yaml"
    startup_provider_validation: bool = True

    # 服务间调用（M0 留位）
    java_service_internal_token: str | None = None

    # LLM 接入（M0 留位，全局非 per-project）
    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None

    # 横切
    cors_origins: Annotated[list[str], NoDecode] = ["*"]
    log_format: Literal["dev", "json"] = "json"
    environment: Literal["dev", "prod"] = "dev"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v: object) -> object:
        """允许 env 用逗号分隔字符串（如 CORS_ORIGINS=*,http://x）。
        NoDecode 关掉 pydantic-settings 对复杂类型的 JSON 预解码，
        否则 `*` 会在 source 层就报错（validator 根本来不及跑）。"""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    """进程内单例配置。"""
    return Settings()
