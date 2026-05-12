# 需求点表
from sqlalchemy import Integer, String, Text, DateTime, func

from sqlalchemy.orm import Mapped, mapped_column
from ai_service.db import Base

class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)       # 记录ID
    req_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # 需求编号
    title: Mapped[str] = mapped_column(String(200))                             # 需求标题
    description: Mapped[str] = mapped_column(Text, default="")                  # 需求详细描述
    document_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)  # 所属的文档ID
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())         # 需求创建时间
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now()) # 需求更新时间
