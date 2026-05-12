# 影响波及图
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ai_service.api.deps import get_db
from ai_service.services.document_service import build_requirement_version_graph

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/document-impact")
def get_document_impact_graph(
    req_code: str | None = None,
    document_id: int | None = None,
    db: Session = Depends(get_db),
):
    """基于 MySQL 中的文档、需求和版本记录生成影响波及图。"""
    graph = build_requirement_version_graph(db, req_code=req_code, document_id=document_id)
    graph["data_source"] = "mysql"
    graph["summary"] = (
        "当前影响波及图直接由 MySQL 中的上传文档、需求点、来源证据和版本链动态生成。"
        + (graph.get("summary") or "")
    )
    return graph
