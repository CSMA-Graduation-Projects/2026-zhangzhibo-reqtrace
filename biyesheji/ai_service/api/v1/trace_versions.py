# 追踪矩阵和版本链
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ai_service.api.deps import get_db
from ai_service.models.document_evaluation import DocumentEvaluationBenchmark, DocumentEvaluationRecord
from ai_service.models.requirement import Requirement
from ai_service.models.requirement_evidence import RequirementEvidence
from ai_service.models.requirement_revision import RequirementRevision
from ai_service.models.uploaded_document import UploadedDocument
from ai_service.services.document_service import ensure_document_evidences

router = APIRouter(prefix="/trace-versions", tags=["trace_versions"])


def _dt(value) -> str | None:
    if not value:
        return None
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _natural_req_no(req_code: str | None) -> int:
    """提取 R1、R2、D12-R3 等编号中的 R 数字，用于自然排序。"""
    text = (req_code or "").upper()
    match = re.search(r"R\s*(\d+)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else 999999


def _requirement_sort_key(req: Requirement) -> tuple[int, int, int, str]:
    doc_id = int(getattr(req, "document_id", None) or 0)
    return (-doc_id, _natural_req_no(req.req_code), int(req.id or 0), req.req_code or "")


def _latest_document_id(db: Session) -> int | None:
    latest = db.execute(
        select(UploadedDocument.id)
        .order_by(desc(UploadedDocument.id))
        .limit(1)
    ).scalar_one_or_none()
    return int(latest) if latest else None


def _parse_snapshot(text: str | None) -> dict[str, str]:
    raw = (text or "").strip()
    if not raw:
        return {"title": "", "description": "", "raw": ""}
    title = ""
    desc_parts: list[str] = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if line.startswith("标题："):
            title = line.replace("标题：", "", 1).strip()
        elif line.startswith("标题:"):
            title = line.replace("标题:", "", 1).strip()
        elif line.startswith("描述："):
            desc_parts.append(line.replace("描述：", "", 1).strip())
        elif line.startswith("描述:"):
            desc_parts.append(line.replace("描述:", "", 1).strip())
        elif desc_parts:
            desc_parts.append(line)
    description = "\n".join(x for x in desc_parts if x).strip()
    if not title and not description:
        description = raw
    return {"title": title, "description": description, "raw": raw}


def _diff_fields(old_snapshot: str | None, new_snapshot: str | None, change_type: str | None) -> list[dict[str, str]]:
    old = _parse_snapshot(old_snapshot)
    new = _parse_snapshot(new_snapshot)
    change_type = (change_type or "").strip()
    if change_type in {"新增", "文档导入"} or not (old_snapshot or "").strip():
        return [{"field": "需求内容", "old_value": "无（新建/文档导入）", "new_value": new["raw"] or "无"}]
    if change_type == "删除":
        return [{"field": "需求内容", "old_value": old["raw"] or "无", "new_value": "已删除"}]
    fields: list[dict[str, str]] = []
    if old["title"] != new["title"]:
        fields.append({"field": "标题", "old_value": old["title"] or "无", "new_value": new["title"] or "无"})
    if old["description"] != new["description"]:
        fields.append({"field": "需求描述", "old_value": old["description"] or "无", "new_value": new["description"] or "无"})
    return fields or [{"field": "需求内容", "old_value": old["raw"] or "无", "new_value": new["raw"] or "无"}]


def _resolve_document(db: Session, rev: RequirementRevision) -> UploadedDocument | None:
    doc_id = getattr(rev, "document_id", None)
    if doc_id:
        doc = db.get(UploadedDocument, doc_id)
        if doc:
            return doc
    req = db.execute(select(Requirement).where(Requirement.req_code == rev.req_code)).scalar_one_or_none()
    req_doc_id = getattr(req, "document_id", None) if req else None
    if req_doc_id:
        return db.get(UploadedDocument, req_doc_id)
    return None


def _revision_summary(db: Session, rev: RequirementRevision) -> dict[str, Any]:
    doc = _resolve_document(db, rev)
    fields = _diff_fields(getattr(rev, "old_snapshot", "") or "", getattr(rev, "new_snapshot", "") or "", getattr(rev, "change_type", "") or "")
    return {
        "id": rev.id,
        "req_code": rev.req_code,
        "version_no": rev.version_no,
        "change_type": rev.change_type,
        "source_type": rev.source_type,
        "document_id": getattr(doc, "id", None) or getattr(rev, "document_id", None),
        "doc_code": getattr(doc, "doc_code", "") if doc else "",
        "document_name": getattr(doc, "original_filename", "") if doc else "未绑定文档",
        "event_id": rev.event_id,
        "title": rev.title,
        "description": rev.description,
        "created_at": _dt(rev.created_at),
        "changed_fields": [x["field"] for x in fields],
        "change_summary": "、".join(x["field"] for x in fields) or "需求内容",
    }


@router.get("/trace-matrix")
def list_light_trace_matrix(
    document_id: int | None = Query(default=None, description="可选：按上传文档 ID 过滤"),
    req_code: str | None = Query(default=None, description="可选：按需求编号过滤，例如 R1"),
    default_latest: bool = Query(default=True, description="未传文档 ID 时默认只展示最新文档"),
    db: Session = Depends(get_db),
):
    """追踪矩阵：默认展示最新文档下的需求追踪，避免一次性列出全部历史文档。"""
    if document_id is None and not req_code and default_latest:
        document_id = _latest_document_id(db)
        if document_id is None:
            return []

    req_stmt = select(Requirement)
    if document_id:
        req_stmt = req_stmt.where(Requirement.document_id == int(document_id))
    if req_code:
        req_stmt = req_stmt.where(Requirement.req_code == req_code.strip())
    reqs = db.execute(req_stmt).scalars().all()
    reqs = sorted(reqs, key=_requirement_sort_key)

    doc_ids = sorted({int(r.document_id) for r in reqs if getattr(r, "document_id", None)})
    for did in doc_ids:
        ensure_document_evidences(db, did)
    docs = {}
    if doc_ids:
        docs = {int(d.id): d for d in db.execute(select(UploadedDocument).where(UploadedDocument.id.in_(doc_ids))).scalars().all()}

    rows: list[dict[str, Any]] = []
    for req in reqs:
        doc_id = int(req.document_id or 0) if getattr(req, "document_id", None) else None
        version_count = db.execute(
            select(func.count())
            .select_from(RequirementRevision)
            .where(RequirementRevision.req_code == req.req_code)
        ).scalar() or 0
        latest = db.execute(
            select(RequirementRevision)
            .where(RequirementRevision.req_code == req.req_code)
            .order_by(desc(RequirementRevision.version_no), desc(RequirementRevision.id))
            .limit(1)
        ).scalar_one_or_none()
        evidence = None
        if doc_id:
            evidence = db.execute(
                select(RequirementEvidence)
                .where(RequirementEvidence.document_id == doc_id)
                .where(RequirementEvidence.req_code == req.req_code)
                .limit(1)
            ).scalar_one_or_none()
        benchmark_count = 0
        latest_record = None
        if doc_id:
            benchmark_count = db.execute(
                select(func.count())
                .select_from(DocumentEvaluationBenchmark)
                .where(DocumentEvaluationBenchmark.document_id == doc_id)
            ).scalar() or 0
            latest_record = db.execute(
                select(DocumentEvaluationRecord)
                .where(DocumentEvaluationRecord.document_id == doc_id)
                .order_by(desc(DocumentEvaluationRecord.id))
                .limit(1)
            ).scalar_one_or_none()

        rows.append({
            "document_id": doc_id,
            "document_name": getattr(docs.get(doc_id), "original_filename", "未绑定文档") if doc_id else "未绑定文档",
            "doc_code": getattr(docs.get(doc_id), "doc_code", "") if doc_id else "",
            "req_code": req.req_code,
            "title": req.title or "",
            "current_version": int(getattr(latest, "version_no", None) or version_count or 1),
            "version_count": int(version_count or 0),
            "latest_change_type": getattr(latest, "change_type", "当前需求") if latest else "当前需求",
            "version_status": "有版本链" if version_count else "仅当前需求",
            "has_evidence": bool(evidence and (evidence.source_excerpt or evidence.source_location)),
            "source_location": getattr(evidence, "source_location", "") if evidence else "",
            "benchmark_status": "已建立基准" if benchmark_count else "未建立基准",
            "evaluation_status": "已评估" if latest_record else "未评估",
            "trace_status": "完整" if version_count and evidence and benchmark_count else "待补充",
        })
    return rows


@router.get("/requirement-versions")
def list_requirement_versions(
    document_id: int | None = Query(default=None, description="可选：按上传文档 ID 过滤"),
    req_code: str | None = Query(default=None, description="可选：按需求编号过滤，例如 R1"),
    default_latest: bool = Query(default=True, description="未传文档 ID 和需求编号时默认只展示最新文档"),
    db: Session = Depends(get_db),
):
    if document_id is None and not req_code and default_latest:
        document_id = _latest_document_id(db)
        if document_id is None:
            return []

    stmt = select(RequirementRevision)
    if document_id:
        req_codes = db.execute(select(Requirement.req_code).where(Requirement.document_id == document_id)).scalars().all()
        if req_codes:
            stmt = stmt.where((RequirementRevision.document_id == document_id) | (RequirementRevision.req_code.in_(list(req_codes))))
        else:
            stmt = stmt.where(RequirementRevision.document_id == document_id)
    if req_code:
        stmt = stmt.where(RequirementRevision.req_code == req_code.strip())
    rows = db.execute(stmt.order_by(desc(RequirementRevision.id))).scalars().all()
    return [_revision_summary(db, rev) for rev in rows]


@router.get("/requirement-versions/{revision_id}")
def get_requirement_version_detail(revision_id: int, db: Session = Depends(get_db)):
    rev = db.get(RequirementRevision, revision_id)
    if not rev:
        raise HTTPException(status_code=404, detail="追溯版本不存在")
    doc = _resolve_document(db, rev)
    old_snapshot = getattr(rev, "old_snapshot", "") or ""
    new_snapshot = getattr(rev, "new_snapshot", "") or ""
    return {
        "version": _revision_summary(db, rev),
        "document": {
            "id": getattr(doc, "id", None) or getattr(rev, "document_id", None),
            "doc_code": getattr(doc, "doc_code", "") if doc else "",
            "name": getattr(doc, "original_filename", "") if doc else "未绑定文档",
        },
        "requirement": {
            "req_code": rev.req_code,
            "version_no": rev.version_no,
            "change_type": rev.change_type,
            "source_type": rev.source_type,
            "event_id": rev.event_id,
            "created_at": _dt(rev.created_at),
        },
        "changes": _diff_fields(old_snapshot, new_snapshot, getattr(rev, "change_type", "") or ""),
        "old_snapshot": old_snapshot,
        "new_snapshot": new_snapshot,
    }
