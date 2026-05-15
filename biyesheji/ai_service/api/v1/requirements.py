#需求点的增删改查
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ai_service.api.deps import get_db
from ai_service.db.schema import ensure_schema
from ai_service.models.change_event import ChangeEvent
from ai_service.models.requirement import Requirement
from ai_service.models.uploaded_document import UploadedDocument
from ai_service.services.document_service import record_requirement_revision, upsert_requirement_evidence

router = APIRouter(prefix="/requirements", tags=["requirements"])


class RequirementCreate(BaseModel):
    req_code: str
    title: str
    description: str = ""
    document_id: Optional[int] = None


class RequirementUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


def _snapshot(title: str, description: str) -> str:
    return f"标题：{(title or '').strip()}\n描述：{(description or '').strip()}"


def _ensure_document_exists(db: Session, document_id: Optional[int]) -> UploadedDocument | None:
    if not document_id:
        return None
    doc = db.execute(select(UploadedDocument).where(UploadedDocument.id == int(document_id))).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在，不能在该文档下维护需求")
    return doc


def _make_unique_req_code_for_document(db: Session, req_code: str, document_id: Optional[int]) -> str:
    code = (req_code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="需求编码不能为空")

    exists = db.execute(select(Requirement).where(Requirement.req_code == code)).scalar_one_or_none()
    if not exists:
        return code

    if document_id and getattr(exists, "document_id", None) == document_id:
        raise HTTPException(status_code=400, detail="该文档下需求编码已存在")
    if not document_id:
        raise HTTPException(status_code=400, detail="需求编码已存在")

    candidate = f"D{document_id}_{code}"
    seq = 2
    while db.execute(select(Requirement).where(Requirement.req_code == candidate)).scalar_one_or_none():
        candidate = f"D{document_id}_{code}_{seq}"
        seq += 1
    return candidate


def _dt(value) -> str | None:
    if not value:
        return None
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _natural_req_no(req_code: str | None) -> int:
    """提取需求编号中的数字，用于 R1、R2、D12-R3 等编号自然排序。"""
    text = (req_code or "").upper()
    match = re.search(r"R\s*(\d+)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else 999999


def _requirement_sort_key(req: Requirement) -> tuple[int, int, int, str]:
    """最新文档排在前面，同一文档内按需求编号从小到大。"""
    doc_id = int(getattr(req, "document_id", None) or 0)
    return (-doc_id, _natural_req_no(req.req_code), int(req.id or 0), req.req_code or "")


def _requirement_out(req: Requirement) -> dict:
    return {
        "id": req.id,
        "req_code": req.req_code,
        "title": req.title,
        "description": req.description,
        "document_id": getattr(req, "document_id", None),
        "created_at": _dt(getattr(req, "created_at", None)),
        "updated_at": _dt(getattr(req, "updated_at", None)),
    }


@router.get("/")
def list_requirements(
    q: Optional[str] = Query(default=None),
    document_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    ensure_schema()
    stmt = select(Requirement)
    if document_id:
        stmt = stmt.where(Requirement.document_id == int(document_id))
    rows = db.execute(stmt).scalars().all()

    if q:
        keyword = q.strip().lower()
        rows = [
            r for r in rows
            if keyword in (r.req_code or "").lower()
            or keyword in (r.title or "").lower()
            or keyword in (r.description or "").lower()
        ]
    rows = sorted(rows, key=_requirement_sort_key)
    return [_requirement_out(r) for r in rows]


@router.get("/{req_code}")
def get_requirement(req_code: str, db: Session = Depends(get_db)):
    ensure_schema()
    req = db.execute(select(Requirement).where(Requirement.req_code == req_code.strip())).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="需求不存在")
    return _requirement_out(req)


@router.post("/")
def create_requirement(payload: RequirementCreate, db: Session = Depends(get_db)):
    ensure_schema()
    title = (payload.title or "").strip()
    description = (payload.description or "").strip()
    document_id = payload.document_id

    if not title:
        raise HTTPException(status_code=400, detail="需求标题不能为空")

    _ensure_document_exists(db, document_id)
    req_code = _make_unique_req_code_for_document(db, payload.req_code, document_id)

    try:
        req = Requirement(req_code=req_code, title=title, description=description, document_id=document_id)
        db.add(req)
        db.flush()

        event = ChangeEvent(req_code=req_code, change_type="新增", old_text="", new_text=_snapshot(title, description))
        db.add(event)
        db.flush()

        record_requirement_revision(
            db,
            req_code=req_code,
            title=title,
            description=description,
            change_type="新增",
            source_type="manual",
            document_id=document_id,
            event_id=event.id,
            old_snapshot="",
        )
        upsert_requirement_evidence(
            db,
            document_id=document_id,
            req_code=req_code,
            source_excerpt=description or title,
            source_location="用户在当前文档下手动补充",
            evidence_type="manual",
        )

        db.commit()
        db.refresh(req)
        return {"msg": "created", **_requirement_out(req)}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建需求失败（数据库错误）：{str(e)}") from e
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建需求失败：{str(e)}") from e


@router.put("/{req_code}")
def update_requirement(req_code: str, payload: RequirementUpdate, db: Session = Depends(get_db)):
    ensure_schema()
    req_code = req_code.strip()
    req = db.execute(select(Requirement).where(Requirement.req_code == req_code)).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="需求不存在")

    old_title = req.title or ""
    old_description = req.description or ""
    document_id = getattr(req, "document_id", None)

    try:
        if payload.title is not None:
            title = payload.title.strip()
            if not title:
                raise HTTPException(status_code=400, detail="需求标题不能为空")
            req.title = title
        if payload.description is not None:
            req.description = payload.description.strip()

        new_title = req.title or ""
        new_description = req.description or ""
        event = ChangeEvent(
            req_code=req_code,
            change_type="修改",
            old_text=_snapshot(old_title, old_description),
            new_text=_snapshot(new_title, new_description),
        )
        db.add(event)
        db.flush()

        record_requirement_revision(
            db,
            req_code=req_code,
            title=new_title,
            description=new_description,
            change_type="修改",
            source_type="manual",
            document_id=document_id,
            event_id=event.id,
            old_snapshot=_snapshot(old_title, old_description),
        )

        db.commit()
        db.refresh(req)
        return {"msg": "updated", **_requirement_out(req)}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新需求失败（数据库错误）：{str(e)}") from e
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新需求失败：{str(e)}") from e


@router.delete("/{req_code}")
def delete_requirement(req_code: str, db: Session = Depends(get_db)):
    ensure_schema()
    req_code = req_code.strip()
    req = db.execute(select(Requirement).where(Requirement.req_code == req_code)).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="需求不存在")

    document_id = getattr(req, "document_id", None)
    old_snapshot = _snapshot(req.title or "", req.description or "")

    try:
        event = ChangeEvent(req_code=req_code, change_type="删除", old_text=old_snapshot, new_text="")
        db.add(event)
        db.flush()

        record_requirement_revision(
            db,
            req_code=req_code,
            title=req.title or req_code,
            description="需求已删除，仅保留历史版本记录。",
            change_type="删除",
            source_type="manual",
            document_id=document_id,
            event_id=event.id,
            old_snapshot=old_snapshot,
        )

        db.execute(delete(Requirement).where(Requirement.req_code == req_code))
        db.commit()
        return {"msg": "deleted", "req_code": req_code}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除需求失败（数据库错误）：{str(e)}") from e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除需求失败：{str(e)}") from e
