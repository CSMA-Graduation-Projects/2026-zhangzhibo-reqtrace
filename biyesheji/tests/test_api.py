from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ai_service.main import app
from ai_service.api.deps import get_db
from ai_service.db.base import Base
import ai_service.models  # noqa: F401  确保 SQLAlchemy 收集全部模型

from ai_service.models.requirement_revision import RequirementRevision
from ai_service.models.uploaded_document import UploadedDocument
from ai_service.services.document_service import (
    extract_text_from_file,
    fallback_extract_requirements,
)


def _make_docx(path: Path, paragraphs: list[str]) -> Path:
    """生成一个最小可解析 docx，用于测试文本解析、上传和导出。"""
    path.parent.mkdir(parents=True, exist_ok=True)

    body = "".join(
        f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"
        for text in paragraphs
    )

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {body}
  </w:body>
</w:document>
"""

    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>
"""

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml)

    return path


@pytest.fixture()
def db_session():
    """使用临时 SQLite 数据库跑测试，避免依赖本地 MySQL 数据。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,
    )

    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session, monkeypatch, tmp_path):
    """覆盖数据库依赖与 schema 初始化，使接口测试走临时数据库。"""
    import ai_service.main as main_module
    import ai_service.api.v1.documents as documents_api
    import ai_service.api.v1.requirements as requirements_api
    import ai_service.services.document_service as document_service
    import ai_service.services.document_export_service as export_service
    import ai_service.services.document_evaluation_service as evaluation_service

    upload_dir = tmp_path / "uploaded_docs"
    export_dir = upload_dir / "exports"
    upload_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)

    # 跑测试时不连接真实 MySQL，不执行真实建表补全逻辑。
    monkeypatch.setattr(main_module, "ensure_schema", lambda: None)
    monkeypatch.setattr(documents_api, "ensure_schema", lambda: None)
    monkeypatch.setattr(requirements_api, "ensure_schema", lambda: None)

    # 上传文件和导出文件都写入 pytest 临时目录。
    monkeypatch.setattr(document_service, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(export_service, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(export_service, "EXPORT_DIR", export_dir)

    # 避免测试时调用真实大模型服务。
    monkeypatch.setattr(
        evaluation_service,
        "chat_text",
        lambda *args, **kwargs: "一、评估对象\n测试环境下生成的AI评估报告。\n\n二、指标结果\n系统已完成TP、FP、FN、Precision、Recall和F1计算。",
    )

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_app_and_basic_pages_ok(client):
    """检查应用初始化和主要页面路由。"""
    assert app.title == "软件需求变更管理与可追溯平台"

    for url in [
        "/",
        "/ui/requirements",
        "/ui/change",
        "/ui/suggest",
        "/ui/impact-graph",
        "/ui/evaluation",
    ]:
        response = client.get(url)
        assert response.status_code == 200


def test_docx_text_parsing_and_rule_requirement_extraction(tmp_path):
    """检查 docx 文本解析与规则需求提取函数。"""
    docx_path = _make_docx(
        tmp_path / "需求测试文档.docx",
        [
            "1 功能需求",
            "系统应当支持上传docx需求文档。",
            "系统应当支持查看需求版本链。",
            "系统应当支持导出最新变更说明文档。",
        ],
        )

    text = extract_text_from_file(docx_path, "需求测试文档.docx")
    assert "系统应当支持上传docx需求文档" in text
    assert "系统应当支持查看需求版本链" in text

    extracted = fallback_extract_requirements(text)
    requirements = extracted.get("requirements", [])

    assert extracted.get("extract_mode") == "rule-section-table-bullet-role"
    assert len(requirements) >= 3

    first = requirements[0]
    assert first.get("req_code", "").startswith("R")
    assert first.get("title")
    assert first.get("description")
    assert first.get("source_excerpt")
    assert first.get("source_location")


def test_document_upload_crud_versions_evaluation_and_export(client, db_session, tmp_path):
    """检查上传、规则提取、需求维护、版本记录、评估指标和文档导出闭环。"""
    docx_path = _make_docx(
        tmp_path / "需求闭环测试文档.docx",
        [
            "1 功能需求",
            "系统应当支持上传docx需求文档。",
            "系统应当支持查看需求版本链。",
        ],
        )

    with docx_path.open("rb") as f:
        upload_response = client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "需求闭环测试文档.docx",
                    f,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert upload_response.status_code == 200
    upload_data = upload_response.json()

    document_id = upload_data["document_id"]
    imported_requirements = upload_data.get("requirements", [])

    assert document_id > 0
    assert upload_data["imported_count"] >= 2
    assert len(imported_requirements) >= 2

    doc_requirements_response = client.get(f"/api/v1/documents/{document_id}/requirements")
    assert doc_requirements_response.status_code == 200

    doc_requirements = doc_requirements_response.json()
    assert len(doc_requirements) >= 2

    first_req_code = doc_requirements[0]["req_code"]

    update_response = client.put(
        f"/api/v1/requirements/{first_req_code}",
        json={
            "title": "上传docx需求文档",
            "description": "系统应当支持上传docx需求文档，并保存来源片段和上传记录。",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["msg"] == "updated"

    create_response = client.post(
        "/api/v1/requirements/",
        json={
            "req_code": "R99",
            "title": "人工补充导出需求",
            "description": "系统应当支持导出带有当前变更说明的docx文档。",
            "document_id": document_id,
        },
    )
    assert create_response.status_code == 200
    manual_req_code = create_response.json()["req_code"]

    delete_response = client.delete(f"/api/v1/requirements/{manual_req_code}")
    assert delete_response.status_code == 200
    assert delete_response.json()["msg"] == "deleted"

    revisions = db_session.execute(
        select(RequirementRevision).order_by(
            RequirementRevision.req_code.asc(),
            RequirementRevision.version_no.asc(),
        )
    ).scalars().all()

    first_req_revisions = [r for r in revisions if r.req_code == first_req_code]
    manual_req_revisions = [r for r in revisions if r.req_code == manual_req_code]

    assert [r.version_no for r in first_req_revisions] == [1, 2]
    assert first_req_revisions[-1].change_type == "修改"

    assert [r.version_no for r in manual_req_revisions] == [1, 2]
    assert [r.change_type for r in manual_req_revisions] == ["新增", "删除"]

    graph_response = client.get(
        "/api/v1/graph/document-impact",
        params={"document_id": document_id},
    )
    assert graph_response.status_code == 200
    graph_data = graph_response.json()
    assert graph_data.get("data_source") == "mysql"
    assert len(graph_data.get("nodes", [])) >= 1

    doc = db_session.get(UploadedDocument, document_id)
    assert doc is not None

    extracted_json = json.loads(doc.extracted_json or "{}")
    extracted_items = extracted_json.get("requirements", [])
    assert len(extracted_items) >= 2

    first_extracted = extracted_items[0]

    benchmark_response = client.post(
        f"/api/v1/evaluation/documents/{document_id}/benchmark",
        json={
            "items": [
                {
                    "benchmark_code": first_extracted.get("req_code", "R1"),
                    "title": first_extracted.get("title", "上传docx需求文档"),
                    "description": first_extracted.get("description", "系统应当支持上传docx需求文档。"),
                },
                {
                    "benchmark_code": "R100",
                    "title": "权限审批流程",
                    "description": "系统应当支持权限审批流程，该条用于测试漏提项。",
                },
            ]
        },
    )
    assert benchmark_response.status_code == 200

    evaluation_response = client.post(f"/api/v1/evaluation/documents/{document_id}/run")
    assert evaluation_response.status_code == 200

    record = evaluation_response.json()["record"]

    assert record["ai_count"] == len(extracted_items)
    assert record["benchmark_count"] == 2
    assert record["tp_count"] >= 1
    assert record["fp_count"] >= 0
    assert record["fn_count"] >= 1

    tp = record["tp_count"]
    fp = record["fp_count"]
    fn = record["fn_count"]

    expected_precision = round((tp / (tp + fp) * 100) if (tp + fp) else 0.0, 2)
    expected_recall = round((tp / (tp + fn) * 100) if (tp + fn) else 0.0, 2)
    expected_f1 = round(
        (2 * expected_precision * expected_recall / (expected_precision + expected_recall))
        if (expected_precision + expected_recall)
        else 0.0,
        2,
    )

    assert record["precision"] == expected_precision
    assert record["recall"] == expected_recall
    assert record["f1"] == expected_f1
    assert evaluation_response.json()["ai_report"]

    export_response = client.get(f"/api/v1/documents/{document_id}/changed-document/latest")
    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert export_response.content.startswith(b"PK")