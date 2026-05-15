from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ai_service.api.v1 import api_router
from ai_service.db.schema import ensure_schema

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(title="软件需求变更管理与可追溯平台")
app.include_router(api_router, prefix="/api/v1")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.on_event("startup")
def startup_init_schema():
    ensure_schema()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/ui/requirements", response_class=HTMLResponse)
def ui_requirements(request: Request):
    return templates.TemplateResponse(request, "requirements.html", {})


@app.get("/ui/change", response_class=HTMLResponse)
def ui_change(request: Request):
    return templates.TemplateResponse(request, "change.html", {})


@app.get("/ui/changes", response_class=HTMLResponse)
def ui_changes(request: Request):
    return templates.TemplateResponse(request, "change.html", {})


@app.get("/ui/suggest", response_class=HTMLResponse)
def ui_suggest(request: Request):
    return templates.TemplateResponse(request, "suggest.html", {})


@app.get("/ui/trace-maintenance", response_class=HTMLResponse)
def ui_trace_maintenance(request: Request):
    return templates.TemplateResponse(request, "suggest.html", {})


@app.get("/ui/impact-graph", response_class=HTMLResponse)
def ui_impact_graph(request: Request):
    return templates.TemplateResponse(request, "impact_graph.html", {})


@app.get("/ui/impact-graph/{req_code}", response_class=HTMLResponse)
def ui_impact_graph_with_req(request: Request, req_code: str):
    return templates.TemplateResponse(request, "impact_graph.html", {"req_code": req_code})


@app.get("/ui/evaluation", response_class=HTMLResponse)
def ui_evaluation(request: Request):
    return templates.TemplateResponse(request, "evaluation.html", {})

