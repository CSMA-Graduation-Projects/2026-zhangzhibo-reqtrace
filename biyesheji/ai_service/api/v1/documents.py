from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_service.api.deps import get_db
from ai_service.db.schema import ensure_schema
from ai_service.models.uploaded_document import UploadedDocument
from ai_service.services.document_export_service import export_latest_changed_document
from ai_service.services.document_service import (
    ai_extract_requirements,
    build_requirement_version_graph,
    create_uploaded_document_record,
    extract_text_from_file,
    import_requirements_from_document,
    get_document_ai_analysis,
    list_document_requirements,
    list_documents,
    save_upload_file,
    delete_uploaded_document,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload")
async def upload_requirement_document(file: UploadFile = File(...), db: Session = Depends(get_db)):
    ensure_schema()
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择要上传的需求文档")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")

    stored_name, path = save_upload_file(file.filename, raw)
    text = extract_text_from_file(path, file.filename)
    if not text.strip():
        raise HTTPException(status_code=400, detail="文档文本解析失败，请优先上传 docx/txt/md 格式的需求文档")

    extracted = ai_extract_requirements(text)
    doc = create_uploaded_document_record(
        db,
        original_filename=file.filename,
        stored_filename=stored_name,
        content_type=file.content_type or "application/octet-stream",
        file_path=str(path),
        text_content=text,
        extracted=extracted,
    )
    result = import_requirements_from_document(db, doc=doc, extracted=extracted)
    return {
        "ok": True,
        **result,
        "original_filename": file.filename,
        "text_length": len(text),
        "preview": text[:500],
        "ai_error": extracted.get("ai_error"),
    }


@router.get("")
def get_documents(db: Session = Depends(get_db)):
    ensure_schema()
    return list_documents(db)




@router.get("/graph/versions")
def get_document_requirement_graph(req_code: str | None = None, document_id: int | None = None, db: Session = Depends(get_db)):
    ensure_schema()
    return build_requirement_version_graph(db, req_code=req_code, document_id=document_id)





@router.get("/{document_id}/ai-analysis")
def get_document_ai_analysis_endpoint(document_id: int, force: bool = False, db: Session = Depends(get_db)):
    ensure_schema()
    try:
        return get_document_ai_analysis(db, document_id, force=force)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

@router.get("/{document_id}/requirements")
def get_document_requirements(document_id: int, db: Session = Depends(get_db)):
    ensure_schema()
    doc = db.execute(select(UploadedDocument).where(UploadedDocument.id == document_id)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return list_document_requirements(db, document_id)


@router.delete("/{document_id}")
def delete_document_endpoint(document_id: int, db: Session = Depends(get_db)):
    ensure_schema()
    try:
        return delete_uploaded_document(db, document_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"文档删除失败：{e}") from e


@router.get("/{document_id}/changed-document/latest")
def download_latest_changed_document(document_id: int, db: Session = Depends(get_db)):
    ensure_schema()
    try:
        result = export_latest_changed_document(db, document_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"最新变更后文档导出失败：{e}") from e
    return FileResponse(
        path=str(result.path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=result.filename,
    )


@router.get("/{document_id}")
def get_document_detail(document_id: int, db: Session = Depends(get_db)):
    ensure_schema()
    doc = db.execute(select(UploadedDocument).where(UploadedDocument.id == document_id)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    try:
        extracted = json.loads(doc.extracted_json or "{}")
    except Exception:
        extracted = {}
    return {
        "id": doc.id,
        "doc_code": doc.doc_code,
        "original_filename": doc.original_filename,
        "content_type": doc.content_type,
        "status": doc.status,
        "created_at": str(doc.created_at),
        "text_length": len(doc.text_content or ""),
        "preview": (doc.text_content or "")[:1200],
        "extracted": extracted,
    }
