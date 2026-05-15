# 需求版本表
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ai_service.db import Base


class RequirementRevision(Base):
    __tablename__ = "requirement_revisions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)          # 记录ID
    req_code: Mapped[str] = mapped_column(String(50), index=True)                  # 需求编号
    version_no: Mapped[int] = mapped_column(Integer, index=True, default=1)        # 版本号
    title: Mapped[str] = mapped_column(String(200), default="")                    # 需求标题
    description: Mapped[str] = mapped_column(Text, default="")                     # 需求描述
    change_type: Mapped[str] = mapped_column(String(20), default="导入")            # 变更类型
    source_type: Mapped[str] = mapped_column(String(30), default="manual")         # 来源类型
    document_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)   # 所属文档的ID
    event_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)      # 变更事件ID
    old_snapshot: Mapped[str] = mapped_column(Text, default="")                    # 变更前快照
    new_snapshot: Mapped[str] = mapped_column(Text, default="")                    # 变更后快照
    relation_json: Mapped[str] = mapped_column(Text, default="")                   # 版本关系或关系图数据

    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())   # 版本创建时间
