# 变更事件表
from sqlalchemy import String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from ai_service.db import Base

class ChangeEvent(Base):
    __tablename__ = "change_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)  # 记录ID
    req_code: Mapped[str] = mapped_column(String(50), index=True)          # 发生变更的需求编号
    change_type: Mapped[str] = mapped_column(String(20))                   # 变更类型
    old_text: Mapped[str] = mapped_column(Text, default="")                # 变更前内容
    new_text: Mapped[str] = mapped_column(Text, default="")                # 变更后内容

    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now()) # 变更发生时间
