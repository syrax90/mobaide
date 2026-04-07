from __future__ import annotations

import pytest

from claude_mobile.core.projects_config import ProjectConfig, ProjectsConfig
from claude_mobile.server.app import create_app
from claude_mobile.server.config import ServerConfig

from .conftest import TEST_JWT_SECRET, TEST_PASSWORD, TEST_USERNAME


@pytest.fixture
async def app_with_projects(tmp_path):
    pc = ProjectsConfig(
        projects={
            "alpha": ProjectConfig(name="alpha", path="C:/proj/alpha", linear_api_key="lin_secret"),
            "beta": ProjectConfig(name="beta", path="C:/proj/beta"),
        }
    )
    cfg = ServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        user_store_path=str(tmp_path / "users.json"),
        max_sessions=4,
        log_level="DEBUG",
        projects_config=pc,
    )
    application = create_app(config=cfg)
    application.state.user_store.add_user(TEST_USERNAME, TEST_PASSWORD)
    yield application
    await application.state.session_manager.shutdown()


@pytest.fixture
async def client_with_projects(app_with_projects):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app_with_projects)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers_p(client_with_projects):
    resp = await client_with_projects.post(
        "/api/auth/token",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_list_projects_returns_configured(client_with_projects, auth_headers_p):
    resp = await client_with_projects.get("/api/projects", headers=auth_headers_p)
    assert resp.status_code == 200
    body = resp.json()
    names = {p["name"] for p in body}
    assert names == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_list_projects_omits_secrets(client_with_projects, auth_headers_p):
    resp = await client_with_projects.get("/api/projects", headers=auth_headers_p)
    raw = resp.text
    assert "lin_secret" not in raw
    body = resp.json()
    by_name = {p["name"]: p for p in body}
    assert by_name["alpha"]["has_linear_key"] is True
    assert by_name["beta"]["has_linear_key"] is False


@pytest.mark.asyncio
async def test_list_projects_requires_auth(client_with_projects):
    resp = await client_with_projects.get("/api/projects")
    assert resp.status_code in (401, 403)
