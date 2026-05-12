# 需求来源证据表
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ai_service.db import Base


class RequirementEvidence(Base):
    """文档证据来源：记录每条需求从上传文档中的哪个片段提取而来。"""

    __tablename__ = "requirement_evidences"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)         # 记录ID
    document_id: Mapped[int] = mapped_column(Integer, index=True)                 # 来源文档ID
    req_code: Mapped[str] = mapped_column(String(50), index=True)                 # 需求编号
    source_location: Mapped[str] = mapped_column(String(120), default="")         # 来源片段位置
    source_excerpt: Mapped[str] = mapped_column(Text, default="")                 # 来源片段内容
    evidence_type: Mapped[str] = mapped_column(String(30), default="document")    # 证据类型
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())  # 证据创建时间
