from ai_service.db.base import Base
from ai_service.db.session import engine, SessionLocal

# 导入所有模型，确保 Base.metadata 能收集到表
import ai_service.models  # noqa: F401

__all__ = ["Base", "engine", "SessionLocal"]