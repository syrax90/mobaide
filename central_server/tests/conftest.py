from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from central_server.agents.service import AgentService
from central_server.app import create_app
from central_server.auth.service import AuthService
from central_server.config import CentralServerConfig
from central_server.db.database import Database
from central_server.manifest.store import ManifestStore
from central_server.pairing.service import PairingService
from central_server.relay.routing import RoutingTable

TEST_JWT_SECRET = "test-secret-key-for-central-server-do-not-use-in-prod"
TEST_ADMIN_EMAIL = "admin@test.local"
TEST_ADMIN_PASSWORD = "testpass123"
TEST_VIEWER_EMAIL = "viewer@test.local"
TEST_VIEWER_PASSWORD = "viewerpass123"


@pytest.fixture
def config(tmp_path) -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        admin_seed_email=TEST_ADMIN_EMAIL,
        admin_seed_password=TEST_ADMIN_PASSWORD,
        db_path=str(tmp_path / "test.db"),
        log_level="DEBUG",
        log_format="console",
        pairing_token_ttl_seconds=300,
        ws_ping_interval=20,
        agent_disconnect_timeout=30,
        # Bypass OTP flow for legacy tests — covered by the dedicated
        # test_email_verification.py suite with this flag flipped back on.
        email_verification_required=False,
    )


@pytest.fixture
async def db(config) -> Database:
    database = Database(config.db_path)
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
async def app(config, db):
    """Create a FastAPI app with manually initialized state (bypasses lifespan).

    The ASGI lifespan is not reliably triggered by httpx.ASGITransport,
    so we initialize app.state directly — mirroring what create_app's
    lifespan context manager does at startup.
    """
    application = create_app(config=config)
    # Manually wire up state that the lifespan would normally set.
    application.state.db = db
    application.state.routing = RoutingTable()
    application.state.manifest_store = ManifestStore(config.manifest_store_path)
    application.state.auth_service = AuthService(config, db)
    application.state.pairing_service = PairingService(db)
    application.state.agent_service = AgentService(db, application.state.routing)
    # Seed admin user (mirrors lifespan)
    await application.state.auth_service.seed_admin_user()
    yield application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    # base_url uses https:// so httpx's cookie jar will return cookies
    # marked with the Secure attribute on follow-up requests. Dashboard
    # login sets the cookie with ``secure=True`` (the production code
    # path); under ``http://`` the jar accepts the cookie but refuses
    # to send it back, breaking end-to-end round-trip tests. The ASGI
    # transport itself is scheme-agnostic for routing.
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


@pytest.fixture
async def dashboard_headers(app) -> dict[str, str]:
    """Return dashboard auth cookie for the admin user (web dashboard)."""
    auth_service = app.state.auth_service
    _user_id, _role, token = await auth_service.login_user(
        TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD
    )
    return {"Cookie": f"dashboard_token={token}"}


@pytest.fixture
async def viewer_user(app) -> dict:
    """Create (once per app) and return the viewer test user row."""
    existing = await app.state.db.get_user_by_email(TEST_VIEWER_EMAIL)
    if existing is not None:
        return existing
    await app.state.auth_service.register_user(
        TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD
    )
    user = await app.state.db.get_user_by_email(TEST_VIEWER_EMAIL)
    assert user is not None
    return user


@pytest.fixture
async def api_auth_headers(app, viewer_user) -> dict[str, str]:
    """Bearer header for a non-admin (viewer) user.

    Agent-ownership endpoints reject admin tokens with 403, so the default
    "authenticated API user" fixture is a viewer. Use
    ``admin_api_auth_headers`` to verify the admin-blocking behaviour.
    """
    auth_service = app.state.auth_service
    _user_id, _role, token = await auth_service.login_user(
        TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def admin_api_auth_headers(app) -> dict[str, str]:
    """Bearer header for the seeded admin user (for admin-blocked tests)."""
    auth_service = app.state.auth_service
    _user_id, _role, token = await auth_service.login_user(
        TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def registered_agent(db, viewer_user) -> tuple[str, str]:
    """Register a test agent owned by the viewer user. Returns (agent_id, raw_token).

    Admins never own agents (enforced by router + service + migration 0003);
    ``viewer_user`` is the default agent owner for tests.
    """
    return await db.register_agent("test-agent", viewer_user["id"])
