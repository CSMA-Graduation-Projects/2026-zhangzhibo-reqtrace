# 数据库表结构的初始化或补全
# 确保表存在，若不存在则补全
# uploaded_documents 上传文档表
# requirements 需求点表
# requirement_evidences 需求来源证据表
# change_events 变更事件表
# requirement_revisions 需求版本表
# document_evaluation_benchmarks 人工基准表
# document_evaluation_records AI评估记录表（Precision、Recall、F1）
from __future__ import annotations

from threading import Lock

from sqlalchemy import inspect, text

from ai_service.db import Base, engine

_SCHEMA_READY = False
_SCHEMA_LOCK = Lock()

_DOCUMENT_TABLE_COLUMNS = {
    "requirements": {
        "document_id": "INT NULL",
    },
    "uploaded_documents": {
        "doc_code": "VARCHAR(80) NULL",
        "original_filename": "VARCHAR(255) NULL",
        "stored_filename": "VARCHAR(255) NULL",
        "content_type": "VARCHAR(120) NULL",
        "file_path": "VARCHAR(500) NULL",
        "text_content": "LONGTEXT NULL",
        "extracted_json": "LONGTEXT NULL",
        "status": "VARCHAR(30) NULL DEFAULT '已解析'",
        "created_at": "DATETIME NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "requirement_revisions": {
        "req_code": "VARCHAR(50) NULL",
        "version_no": "INT NULL DEFAULT 1",
        "title": "VARCHAR(200) NULL",
        "description": "LONGTEXT NULL",
        "change_type": "VARCHAR(20) NULL DEFAULT '导入'",
        "source_type": "VARCHAR(30) NULL DEFAULT 'manual'",
        "document_id": "INT NULL",
        "event_id": "INT NULL",
        "old_snapshot": "LONGTEXT NULL",
        "new_snapshot": "LONGTEXT NULL",
        "relation_json": "LONGTEXT NULL",
        "created_at": "DATETIME NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "document_evaluation_benchmarks": {
        "document_id": "INT NULL",
        "benchmark_code": "VARCHAR(50) NULL",
        "title": "VARCHAR(200) NULL",
        "description": "LONGTEXT NULL",
        "created_at": "DATETIME NULL DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "DATETIME NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "document_evaluation_records": {
        "document_id": "INT NULL",
        "precision": "FLOAT NULL DEFAULT 0",
        "recall": "FLOAT NULL DEFAULT 0",
        "f1": "FLOAT NULL DEFAULT 0",
        "tp_count": "INT NULL DEFAULT 0",
        "fp_count": "INT NULL DEFAULT 0",
        "fn_count": "INT NULL DEFAULT 0",
        "ai_count": "INT NULL DEFAULT 0",
        "benchmark_count": "INT NULL DEFAULT 0",
        "matched_json": "LONGTEXT NULL",
        "false_positive_json": "LONGTEXT NULL",
        "false_negative_json": "LONGTEXT NULL",
        "ai_summary": "LONGTEXT NULL",
        "ai_report": "LONGTEXT NULL",
        "created_at": "DATETIME NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "requirement_evidences": {
        "document_id": "INT NULL",
        "req_code": "VARCHAR(50) NULL",
        "source_location": "VARCHAR(120) NULL",
        "source_excerpt": "LONGTEXT NULL",
        "evidence_type": "VARCHAR(30) NULL DEFAULT 'document'",
        "created_at": "DATETIME NULL DEFAULT CURRENT_TIMESTAMP",
    },
}

_DOCUMENT_TABLE_INDEXES = {
    "requirements": [("ix_requirements_document_id", "document_id")],
    "uploaded_documents": [("ix_uploaded_documents_doc_code", "doc_code")],
    "requirement_revisions": [
        ("ix_requirement_revisions_req_code", "req_code"),
        ("ix_requirement_revisions_version_no", "version_no"),
        ("ix_requirement_revisions_document_id", "document_id"),
        ("ix_requirement_revisions_event_id", "event_id"),
    ],
    "document_evaluation_benchmarks": [
        ("ix_doc_eval_benchmarks_document_id", "document_id"),
        ("ix_doc_eval_benchmarks_code", "benchmark_code"),
    ],
    "document_evaluation_records": [("ix_doc_eval_records_document_id", "document_id")],
    "requirement_evidences": [
        ("ix_requirement_evidences_document_id", "document_id"),
        ("ix_requirement_evidences_req_code", "req_code"),
    ],
}


def _quote_identifier(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _ensure_upgrade_columns() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table_name, required_columns in _DOCUMENT_TABLE_COLUMNS.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_ddl in required_columns.items():
                if column_name in existing_columns:
                    continue
                conn.execute(text(f"ALTER TABLE {_quote_identifier(table_name)} ADD COLUMN {_quote_identifier(column_name)} {column_ddl}"))

        inspector = inspect(engine)
        for table_name, indexes in _DOCUMENT_TABLE_INDEXES.items():
            if table_name not in existing_tables:
                continue
            existing_indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
            for index_name, column_name in indexes:
                if index_name in existing_indexes:
                    continue
                try:
                    conn.execute(text(f"CREATE INDEX {_quote_identifier(index_name)} ON {_quote_identifier(table_name)} ({_quote_identifier(column_name)})"))
                except Exception:
                    pass


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        Base.metadata.create_all(bind=engine)
        _ensure_upgrade_columns()
        _SCHEMA_READY = True
