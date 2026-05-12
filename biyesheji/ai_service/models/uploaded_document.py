# 文档上传表
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ai_service.db import Base


class UploadedDocument(Base):
    __tablename__ = "uploaded_documents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)        # ID
    doc_code: Mapped[str] = mapped_column(String(80), unique=True, index=True)   # 文档编号
    original_filename: Mapped[str] = mapped_column(String(255), default="")      # 上传的文档名
    stored_filename: Mapped[str] = mapped_column(String(255), default="")        # 保存的文档名
    content_type: Mapped[str] = mapped_column(String(120), default="")           # 文档类型
    file_path: Mapped[str] = mapped_column(String(500), default="")              # 文档保存路径
    text_content: Mapped[str] = mapped_column(Text, default="")                  # 文档内容正文
    extracted_json: Mapped[str] = mapped_column(Text, default="")                # 需求提取的原始json
    status: Mapped[str] = mapped_column(String(30), default="已解析")             # 文档状态

    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
