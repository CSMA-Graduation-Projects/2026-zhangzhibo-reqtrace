# 处理人工基准、AI评估、Precision/Recall/F1 指标
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ai_service.api.deps import get_db
from ai_service.services.document_evaluation_service import (
    create_benchmark_from_ai,
    get_document_evaluation_detail,
    list_evaluation_documents,
    run_document_evaluation,
    save_benchmark_items,
)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


class BenchmarkItemIn(BaseModel):
    benchmark_code: str = ""
    req_code: str = ""
    title: str = ""
    description: str = ""


class BenchmarkSaveIn(BaseModel):
    items: list[BenchmarkItemIn] = Field(default_factory=list)


class BenchmarkFromAiIn(BaseModel):
    overwrite: bool = False


@router.get("/documents")
def get_evaluation_documents(db: Session = Depends(get_db)):
    return list_evaluation_documents(db)


@router.get("/documents/{document_id}")
def get_evaluation_document_detail(document_id: int, db: Session = Depends(get_db)):
    try:
        return get_document_evaluation_detail(db, document_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/documents/{document_id}/benchmark/from-ai")
def build_benchmark_from_ai(document_id: int, data: BenchmarkFromAiIn, db: Session = Depends(get_db)):
    try:
        return create_benchmark_from_ai(db, document_id, overwrite=data.overwrite)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/documents/{document_id}/benchmark")
def save_document_benchmark(document_id: int, data: BenchmarkSaveIn, db: Session = Depends(get_db)):
    try:
        return save_benchmark_items(db, document_id, [x.model_dump() for x in data.items])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/documents/{document_id}/run")
def run_document_ai_evaluation(document_id: int, db: Session = Depends(get_db)):
    try:
        return run_document_evaluation(db, document_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
