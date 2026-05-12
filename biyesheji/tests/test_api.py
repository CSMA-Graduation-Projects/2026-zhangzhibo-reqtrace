from fastapi.testclient import TestClient

from ai_service.main import app


client = TestClient(app)


def test_smoke_import_app():
    """检查 FastAPI 应用能否正常导入和初始化。"""
    assert app.title == "需求变更影响分析平台"


def test_home_page_ok():
    """检查首页是否能正常访问。"""
    response = client.get("/")
    assert response.status_code == 200


def test_requirements_page_ok():
    """检查需求管理页面是否能正常访问。"""
    response = client.get("/ui/requirements")
    assert response.status_code == 200


def test_change_page_ok():
    """检查变更分析页面是否能正常访问。"""
    response = client.get("/ui/change")
    assert response.status_code == 200


def test_trace_page_ok():
    """检查追溯维护页面是否能正常访问。"""
    response = client.get("/ui/suggest")
    assert response.status_code == 200


def test_evaluation_page_ok():
    """检查 AI评估页面是否能正常访问。"""
    response = client.get("/ui/evaluation")
    assert response.status_code == 200
