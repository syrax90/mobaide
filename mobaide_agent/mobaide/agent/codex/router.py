from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request, status

from mobaide.core import CliBinaryNotFoundError
from mobaide.core.cli_launcher import prepend_path_dir
from mobaide.core.codex_auth import codex_login_with_api_key

from ..auth.dependencies import get_current_user
from ..logging import audit_event, get_logger
from .models import CodexLoginRequest, CodexLoginResponse

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/codex",
    tags=["codex"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/login", response_model=CodexLoginResponse)
async def codex_login(body: CodexLoginRequest, request: Request) -> CodexLoginResponse:
    """Authenticate the host's Codex CLI with an OpenAI API key.

    Runs ``codex login --with-api-key`` as a one-shot subprocess with the key on
    stdin (never argv, never persisted by us). The same configured binary and
    ``CODEX_HOME`` the launcher uses are applied, so the credential lands where
    the create-session auth guard reads it.
    """
    launcher = getattr(request.app.state, "codex_launcher", None)
    if launcher is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Codex is not configured on this agent.",
        )
    try:
        codex_bin = launcher.resolve_binary()
    except CliBinaryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{exc.cli_name.capitalize()} CLI not found: {exc}",
        )

    env = os.environ.copy()
    prepend_path_dir(env, os.path.dirname(codex_bin))
    ok, detail = await codex_login_with_api_key(
        body.api_key.get_secret_value(),
        codex_bin=codex_bin,
        env=env,
    )

    audit_event(
        "audit.codex.login",
        actor=getattr(request.state, "username", None),
        outcome="ok" if ok else "failure",
    )
    if not ok:
        logger.warning("codex.login_failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Codex login failed: {detail}",
        )
    return CodexLoginResponse(authenticated=True)
