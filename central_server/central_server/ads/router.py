"""Anonymous endpoint behind the landing page's consent banner."""
from __future__ import annotations

import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, model_validator

from ..auth.models import AD_CLICK_ID_PATTERN, AdClickKind
from .consent import COOKIE_MAX_AGE, COOKIE_NAME, cookie_domain, cookie_value

router = APIRouter(prefix="/api/ads", tags=["ads"])


class AdsConsentRequest(BaseModel):
    """What the landing page knows when the visitor accepts the banner."""

    click_id: str | None = Field(default=None, pattern=AD_CLICK_ID_PATTERN)
    click_id_kind: AdClickKind | None = None
    arrived_at: int | None = Field(default=None, ge=0, le=4_102_444_800)

    @model_validator(mode="after")
    def _click_fields_travel_together(self) -> "AdsConsentRequest":
        if (self.click_id is None) != (self.click_id_kind is None):
            raise ValueError("click_id and click_id_kind must be set together")
        return self


def _is_same_origin(request: Request) -> bool:
    """Only the landing page itself may mint a consent cookie.

    Both headers are browser-controlled, so a page on a sibling host (or any
    other site) cannot obtain a validly signed cookie for a visitor through a
    cross-site POST. ``Sec-Fetch-Site`` is authoritative where present; older
    browsers fall back to comparing the ``Origin`` host with ``Host``.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site == "same-origin"
    origin = request.headers.get("origin")
    if not origin:
        return False
    return urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()


@router.post(
    "/consent", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def record_ads_consent(request: Request, body: AdsConsentRequest) -> Response:
    """Issue the signed ``mobaide_ads`` cookie after the visitor accepts the
    landing page's banner. ``t`` is the arrival time the page reports, capped
    at the server clock so a skewed browser cannot post-date a click."""
    if not _is_same_origin(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent is recorded from the landing page only",
        )
    config = request.app.state.config
    now = int(time.time())
    clicked_at = now if body.arrived_at is None else min(body.arrived_at, now)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.set_cookie(
        key=COOKIE_NAME,
        value=cookie_value(
            config.jwt_secret_key,
            click_id=body.click_id,
            click_id_kind=body.click_id_kind,
            clicked_at=clicked_at,
        ),
        max_age=COOKIE_MAX_AGE,
        path="/",
        domain=cookie_domain(request.url.hostname),
        secure=config.user_session_cookie_secure,
        httponly=False,
        samesite="lax",
    )
    return response
