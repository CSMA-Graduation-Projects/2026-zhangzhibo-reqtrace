# 变更事件
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from ai_service.api.deps import get_db
from ai_service.models.change_event import ChangeEvent

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
def list_events(
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=50),
        db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size

    rows = db.execute(
        select(ChangeEvent)
        .order_by(desc(ChangeEvent.id))
        .offset(offset)
        .limit(page_size)
    ).scalars().all()

    return [
        {
            "id": e.id,
            "req_code": e.req_code,
            "change_type": getattr(e, "change_type", ""),
            "old_text": (getattr(e, "old_text", "") or "")[:120],
            "new_text": (getattr(e, "new_text", "") or "")[:120],
            "created_at": (e.created_at.isoformat() if getattr(e, "created_at", None) else None),
        }
        for e in rows
    ]