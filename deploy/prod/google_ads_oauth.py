#!/usr/bin/env python3
"""Mint the Google Ads OAuth refresh token the central server uploads with.

Runs the OAuth 2.0 desktop flow (loopback redirect + PKCE) against a
*Desktop app* OAuth client and prints the three ``GOOGLE_ADS_OAUTH_*`` lines
for ``/opt/mobaide/.env``. Standard library only, so it runs from a bare
clone on any Python 3.12+.

Usage:
    python deploy/prod/google_ads_oauth.py --client-id <id>          # prompts for the secret
    python deploy/prod/google_ads_oauth.py --client-id <id> --verify --developer-token <token>
    python deploy/prod/google_ads_oauth.py --client-id <id> --no-browser   # print the URL instead

Only the ``.env`` lines go to stdout; everything else goes to stderr, so
``>> some-file`` captures exactly the lines to paste. The client secret is
never echoed.

Before running: the Google Cloud project's OAuth consent screen must be
**In production** (a *Testing* consent screen issues refresh tokens that
expire after 7 days), the Google Ads API must be enabled on it, and the
Google account you sign in with must reach the Ads account — directly, or
through the manager account whose id then goes in GOOGLE_ADS_LOGIN_CUSTOMER_ID.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Any, Callable

MIN_PYTHON = (3, 12)
SCOPE = "https://www.googleapis.com/auth/adwords"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
API_BASE = "https://googleads.googleapis.com"
DEFAULT_API_VERSION = "v25"
REVOKE_URL = "https://myaccount.google.com/permissions"

CAVEATS = """\
Before you rely on this token:
  * The OAuth consent screen must be "In production" (or "Internal" on a
    Workspace domain). A "Testing" consent screen issues refresh tokens that
    expire after 7 days — the server then logs
    pairing.conversion_upload_failed ... reason=oauth_refresh_failed.
  * The account you just signed in with must reach the Ads account. If it
    does so through a manager account, set GOOGLE_ADS_LOGIN_CUSTOMER_ID to
    that manager's id (digits only).
  * The developer token needs Basic access before real uploads succeed; a
    Test-access token only reaches test accounts.
  * Google keeps at most 100 live refresh tokens per account per client, and
    re-running this script does not revoke the previous one — revoke it at
    %s if you are rotating.
""" % REVOKE_URL


class OAuthFlowError(Exception):
    """The flow cannot continue; the message is operator-facing."""


def _info(message: str) -> None:
    print(message, file=sys.stderr)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_pkce_pair() -> tuple[str, str]:
    """RFC 7636 verifier (86 chars, inside 43..128) and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_auth_url(
    *, client_id: str, redirect_uri: str, state: str, code_challenge: str
) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPE,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            # Both are what makes Google return a refresh token at all.
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return f"{AUTH_ENDPOINT}?{query}"


def parse_redirect_query(path: str, expected_state: str) -> str:
    """The authorization code out of the loopback redirect, or an error."""
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    if "error" in query:
        raise OAuthFlowError(f"google returned error={query['error'][0]}")
    if query.get("state", [None])[0] != expected_state:
        raise OAuthFlowError("state mismatch on the redirect — rerun the flow")
    code = query.get("code", [None])[0]
    if not code:
        raise OAuthFlowError("the redirect carried no authorization code")
    return code


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — http.server API
        if urllib.parse.urlsplit(self.path).path != "/":
            # Browsers probe /favicon.ico; that must not consume the wait.
            self.send_response(404)
            self.end_headers()
            return
        self.server.captured_path = self.path  # type: ignore[attr-defined]
        body = (
            b"<!doctype html><title>Mobaide</title>"
            b"<p>Signed in. You can close this tab.</p>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


def start_listener(port: int) -> http.server.HTTPServer:
    """Bind the loopback redirect target; ``port=0`` picks a free one."""
    server = http.server.HTTPServer(("127.0.0.1", port), _RedirectHandler)
    server.captured_path = None  # type: ignore[attr-defined]
    return server


def wait_for_code(
    server: http.server.HTTPServer, expected_state: str, timeout: float
) -> str:
    deadline = time.monotonic() + timeout
    while server.captured_path is None:  # type: ignore[attr-defined]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OAuthFlowError("timed out waiting for the browser redirect")
        server.timeout = remaining
        server.handle_request()
    return parse_redirect_query(server.captured_path, expected_state)  # type: ignore[attr-defined]


def build_token_request(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> urllib.request.Request:
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": code_verifier,
        }
    ).encode("ascii")
    return urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def _describe_error(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except ValueError:
        return raw[:300]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    description = payload.get("error_description", "") if isinstance(payload, dict) else ""
    return f"{error or ''}: {description}".strip(": ")[:300] or raw[:300]


def _read_json(opener: Callable[..., Any], request: urllib.request.Request) -> dict:
    try:
        with opener(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = _describe_error(exc.read().decode("utf-8", "replace"))
        raise OAuthFlowError(f"HTTP {exc.code} from {request.full_url}: {detail}") from None
    except urllib.error.URLError as exc:
        raise OAuthFlowError(f"could not reach {request.full_url}: {exc.reason}") from None


def exchange_code(
    request: urllib.request.Request, opener: Callable[..., Any] = urllib.request.urlopen
) -> dict:
    payload = _read_json(opener, request)
    if not payload.get("refresh_token"):
        raise OAuthFlowError(
            "the token response carried no refresh_token. prompt=consent was "
            f"sent, so Google only omits it when a live grant already exists: "
            f"revoke this app at {REVOKE_URL} and rerun"
        )
    return payload


def env_lines(*, client_id: str, client_secret: str, refresh_token: str) -> str:
    return (
        f"GOOGLE_ADS_OAUTH_CLIENT_ID={client_id}\n"
        f"GOOGLE_ADS_OAUTH_CLIENT_SECRET={client_secret}\n"
        f"GOOGLE_ADS_OAUTH_REFRESH_TOKEN={refresh_token}\n"
    )


def build_verify_request(
    *, access_token: str, developer_token: str, api_version: str = DEFAULT_API_VERSION
) -> urllib.request.Request:
    # User-scoped: lists every customer the signed-in account can reach, so
    # no login-customer-id is needed here.
    return urllib.request.Request(
        f"{API_BASE}/{api_version}/customers:listAccessibleCustomers",
        headers={
            "Authorization": f"Bearer {access_token}",
            "developer-token": developer_token,
        },
    )


def verify_token(
    request: urllib.request.Request, opener: Callable[..., Any] = urllib.request.urlopen
) -> list[str]:
    payload = _read_json(opener, request)
    return [str(name) for name in payload.get("resourceNames", [])]


def run(args: argparse.Namespace) -> int:
    client_id = args.client_id or os.environ.get("GOOGLE_ADS_OAUTH_CLIENT_ID")
    if not client_id:
        _info("error: --client-id (or GOOGLE_ADS_OAUTH_CLIENT_ID) is required")
        return 2
    client_secret = (
        args.client_secret
        or os.environ.get("GOOGLE_ADS_OAUTH_CLIENT_SECRET")
        or getpass.getpass("OAuth client secret: ")
    )
    if not client_secret:
        _info("error: a client secret is required")
        return 2
    developer_token = args.developer_token or os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")
    if args.verify and not developer_token:
        _info("error: --verify needs --developer-token (or GOOGLE_ADS_DEVELOPER_TOKEN)")
        return 2

    verifier, challenge = make_pkce_pair()
    state = secrets.token_urlsafe(16)
    server = start_listener(args.port)
    try:
        redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/"
        url = build_auth_url(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=challenge,
        )
        if args.no_browser or not webbrowser.open(url):
            _info(
                "Open this URL in a browser signed in as the Ads administrator:\n\n"
                f"{url}\n"
            )
        else:
            _info(
                "Browser opened — sign in with the Google account that administers "
                "the Ads account and approve the request."
            )
        code = wait_for_code(server, state, args.timeout)
    finally:
        server.server_close()

    tokens = exchange_code(
        build_token_request(
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=verifier,
        )
    )
    sys.stdout.write(
        env_lines(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=tokens["refresh_token"],
        )
    )
    sys.stdout.flush()

    if args.verify:
        names = verify_token(
            build_verify_request(
                access_token=tokens["access_token"],
                developer_token=developer_token,
                api_version=args.api_version,
            )
        )
        _info("Accessible customers: " + (", ".join(names) or "(none)"))
        _info(
            "If GOOGLE_ADS_CUSTOMER_ID is not listed, set GOOGLE_ADS_LOGIN_CUSTOMER_ID "
            "to the manager account that is."
        )
    _info("")
    _info(CAVEATS)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--client-id", help="Desktop-app OAuth client id (env GOOGLE_ADS_OAUTH_CLIENT_ID)")
    parser.add_argument(
        "--client-secret",
        help="OAuth client secret (env GOOGLE_ADS_OAUTH_CLIENT_SECRET; prompted when absent)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="after minting, list the Ads customers the token can reach",
    )
    parser.add_argument(
        "--developer-token", help="needed with --verify (env GOOGLE_ADS_DEVELOPER_TOKEN)"
    )
    parser.add_argument("--api-version", default=DEFAULT_API_VERSION)
    parser.add_argument("--port", type=int, default=0, help="loopback port (default: random)")
    parser.add_argument(
        "--no-browser", action="store_true", help="print the authorization URL instead of opening it"
    )
    parser.add_argument(
        "--timeout", type=float, default=300.0, help="seconds to wait for the redirect"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < MIN_PYTHON:
        _info(f"error: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required")
        return 2
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except OAuthFlowError as exc:
        _info(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        _info("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
