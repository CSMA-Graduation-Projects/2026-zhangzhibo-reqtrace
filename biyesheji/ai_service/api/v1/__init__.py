# RESTful接口总路由
from fastapi import APIRouter

from ai_service.api.v1.documents import router as documents_router
from ai_service.api.v1.requirements import router as requirements_router
from ai_service.api.v1.events import router as events_router
from ai_service.api.v1.graph import router as graph_router
from ai_service.api.v1.trace_versions import router as trace_versions_router
from ai_service.api.v1.evaluation import router as evaluation_router

api_router = APIRouter()

api_router.include_router(documents_router)
api_router.include_router(requirements_router)
api_router.include_router(events_router)
api_router.include_router(graph_router)
api_router.include_router(trace_versions_router)
api_router.include_router(evaluation_router)

__all__ = ["api_router"]