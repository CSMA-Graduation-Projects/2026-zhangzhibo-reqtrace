# 人工基准表
from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ai_service.db import Base


class DocumentEvaluationBenchmark(Base):
    """人工确认后的文档需求基准。"""

    __tablename__ = "document_evaluation_benchmarks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)        # 记录ID
    document_id: Mapped[int] = mapped_column(Integer, index=True)                # 对应文档ID
    benchmark_code: Mapped[str] = mapped_column(String(50), index=True)          # 基准编号
    title: Mapped[str] = mapped_column(String(200), default="")                  # 需求标题
    description: Mapped[str] = mapped_column(Text, default="")                   # 需求描述
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now()) # 创建时间
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now()) # 更新时间


class DocumentEvaluationRecord(Base):
    """系统提取结果与人工确认基准的评估记录。"""

    __tablename__ = "document_evaluation_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)           # 记录ID
    document_id: Mapped[int] = mapped_column(Integer, index=True)                   # 对应文档ID
    precision: Mapped[float] = mapped_column(Float, default=0.0)                    # 精确率
    recall: Mapped[float] = mapped_column(Float, default=0.0)                       # 召回率
    f1: Mapped[float] = mapped_column(Float, default=0.0)                           # F1值
    tp_count: Mapped[int] = mapped_column(Integer, default=0)                       # 正确匹配数量
    fp_count: Mapped[int] = mapped_column(Integer, default=0)                       # 系统多提或误提数量
    fn_count: Mapped[int] = mapped_column(Integer, default=0)                       # 系统漏提数量
    ai_count: Mapped[int] = mapped_column(Integer, default=0)                       # 系统提取出的需求总数
    benchmark_count: Mapped[int] = mapped_column(Integer, default=0)                # 人工基准需求总数
    matched_json: Mapped[str] = mapped_column(Text, default="")                     # 匹配成功的详细
    false_positive_json: Mapped[str] = mapped_column(Text, default="")              # 系统误提或多提的详细
    false_negative_json: Mapped[str] = mapped_column(Text, default="")              # 系统漏提的明细
    ai_summary: Mapped[str] = mapped_column(Text, default="")                       # AI 评估摘要
    ai_report: Mapped[str] = mapped_column(Text, default="")                        # AI 生成的详细评估报告
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())    # 评估时间
