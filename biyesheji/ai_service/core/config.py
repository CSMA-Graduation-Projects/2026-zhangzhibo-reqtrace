# 统一读取并管理项目的配置
import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)


def _get_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else value


def _get_env_first(names: List[str], default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default.strip() if isinstance(default, str) else default


def _get_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


class Settings:
    APP_NAME: str = _get_env("APP_NAME", "需求变更影响分析平台")
    APP_ENV: str = _get_env("APP_ENV", "dev")
    DEBUG: bool = _get_bool_env("DEBUG", default=(APP_ENV.lower() == "dev"))

    MYSQL_HOST: str = _get_env("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT: str = _get_env("MYSQL_PORT", "3306")
    MYSQL_USER: str = _get_env("MYSQL_USER", "root")
    MYSQL_PASSWORD: str = _get_env("MYSQL_PASSWORD", "123456")
    MYSQL_DB: str = _get_env("MYSQL_DB", "trace_platform")
    MYSQL_CHARSET: str = _get_env("MYSQL_CHARSET", "utf8mb4")

    LLM_API_KEY: str = _get_env_first(["LLM_API_KEY", "OPENAI_API_KEY"], default="")
    LLM_BASE_URL: str = _get_env_first(["LLM_BASE_URL", "OPENAI_BASE_URL"], default="")
    LLM_MODEL: str = _get_env_first(["LLM_MODEL", "OPENAI_MODEL"], default="gpt-4o-mini")


    @property
    def DATABASE_URL(self) -> str:
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DB}"
            f"?charset={self.MYSQL_CHARSET}"
        )


settings = Settings()

DATABASE_URL = settings.DATABASE_URL
LLM_API_KEY = settings.LLM_API_KEY
LLM_BASE_URL = settings.LLM_BASE_URL
LLM_MODEL = settings.LLM_MODEL
MYSQL_HOST = settings.MYSQL_HOST
MYSQL_PORT = settings.MYSQL_PORT
MYSQL_USER = settings.MYSQL_USER
MYSQL_PASSWORD = settings.MYSQL_PASSWORD
MYSQL_DB = settings.MYSQL_DB
MYSQL_CHARSET = settings.MYSQL_CHARSET
DEBUG = settings.DEBUG
APP_ENV = settings.APP_ENV
APP_NAME = settings.APP_NAME
