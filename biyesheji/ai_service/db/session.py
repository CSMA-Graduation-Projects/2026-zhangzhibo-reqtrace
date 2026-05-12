# 创建数据库连接，读取 core/config.py 里的 MySQL 配置
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ai_service.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=True,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)