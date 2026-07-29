"""ENABLE_API_DOCS exposure policy for the agent's OpenAPI routes.

The agent binds ``0.0.0.0`` so DIRECT mode stays reachable over the LAN,
which made FastAPI's stock ``/docs`` publish the whole session / project /
git / codex surface to anything on the network. There is no admin role here,
so the flag itself is the gate: off (the default) leaves the three routes
unregistered, on serves them.
"""
from __future__ import annotations

import pytest

from mobaide.agent.config import AgentConfig

DOC_PATHS = ("/docs", "/redoc", "/openapi.json")

# The flag is consumed by ``create_app``, so it has to be pinned on the
# config before the conftest ``app`` fixture builds the application.
docs_enabled = pytest.mark.parametrize("agent_config", [True], indirect=True)


@pytest.fixture
def agent_config(agent_config, request) -> AgentConfig:
    """The shared agent config with ``enable_api_docs`` pinned; off unless
    asked. ``AgentConfig`` is frozen, hence ``model_copy``."""
    return agent_config.model_copy(
        update={"enable_api_docs": getattr(request, "param", False)}
    )


class TestDocsDisabledByDefault:
    @pytest.mark.parametrize("path", DOC_PATHS)
    async def test_doc_route_is_not_registered(self, client, path) -> None:
        response = await client.get(path)
        assert response.status_code == 404

    async def test_app_exposes_no_doc_urls(self, app) -> None:
        assert (app.docs_url, app.redoc_url, app.openapi_url) == (None, None, None)


@docs_enabled
class TestDocsEnabled:
    @pytest.mark.parametrize("path", DOC_PATHS)
    async def test_doc_route_is_served(self, client, path) -> None:
        response = await client.get(path)
        assert response.status_code == 200, response.text

    async def test_schema_documents_the_session_api(self, client) -> None:
        response = await client.get("/openapi.json")
        assert "/api/sessions" in response.json()["paths"]


class TestPublicSurfaceStillRoutes:
    """Turning the doc routes off changed no other routing."""

    @pytest.mark.parametrize("path", ["/api/health", "/api/version"])
    async def test_unauthenticated_endpoint_still_answers(self, client, path) -> None:
        response = await client.get(path)
        assert response.status_code == 200, path
