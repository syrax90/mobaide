"""Pins the pure pieces of ``deploy/prod/google_ads_oauth.py`` — the PKCE
pair, the authorization URL, redirect parsing, the token exchange, the
``.env`` output and the loopback listener — without touching Google."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "deploy" / "prod"))

import google_ads_oauth as oauth  # noqa: E402


class _Response:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def _opener_returning(payload: dict):
    def opener(request, timeout=None):
        return _Response(payload)

    return opener


def _opener_raising(status: int, body: dict):
    def opener(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, status, "Bad Request", {}, io.BytesIO(json.dumps(body).encode())
        )

    return opener


def test_pkce_pair_is_s256_of_verifier():
    verifier, challenge = oauth.make_pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_-]+", verifier)
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert challenge == expected
    assert "=" not in challenge


def test_pkce_pairs_are_unique():
    assert oauth.make_pkce_pair() != oauth.make_pkce_pair()


def test_auth_url_carries_offline_consent_and_pkce():
    url = oauth.build_auth_url(
        client_id="cid", redirect_uri="http://127.0.0.1:43123/", state="st", code_challenge="ch"
    )
    parts = urllib.parse.urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == oauth.AUTH_ENDPOINT
    query = urllib.parse.parse_qs(parts.query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["cid"]
    assert query["scope"] == [oauth.SCOPE]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == ["ch"]
    assert query["state"] == ["st"]
    assert query["redirect_uri"] == ["http://127.0.0.1:43123/"]


def test_parse_redirect_returns_the_code():
    assert oauth.parse_redirect_query("/?code=abc&state=s", "s") == "abc"


def test_parse_redirect_rejects_state_mismatch():
    with pytest.raises(oauth.OAuthFlowError, match="state"):
        oauth.parse_redirect_query("/?code=abc&state=other", "s")


def test_parse_redirect_surfaces_google_error():
    with pytest.raises(oauth.OAuthFlowError, match="access_denied"):
        oauth.parse_redirect_query("/?error=access_denied&state=s", "s")


def test_parse_redirect_rejects_missing_code():
    with pytest.raises(oauth.OAuthFlowError, match="code"):
        oauth.parse_redirect_query("/?state=s", "s")


def test_token_request_is_form_encoded_with_verifier():
    request = oauth.build_token_request(
        client_id="cid",
        client_secret="sec",
        code="abc",
        redirect_uri="http://127.0.0.1:43123/",
        code_verifier="ver",
    )
    assert request.get_method() == "POST"
    assert request.full_url == oauth.TOKEN_ENDPOINT
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    body = urllib.parse.parse_qs(request.data.decode("ascii"))
    assert body["grant_type"] == ["authorization_code"]
    assert body["code_verifier"] == ["ver"]
    assert body["client_secret"] == ["sec"]
    assert "sec" not in request.full_url


def test_exchange_code_returns_tokens():
    request = oauth.build_token_request(
        client_id="c", client_secret="s", code="a", redirect_uri="r", code_verifier="v"
    )
    tokens = oauth.exchange_code(
        request, opener=_opener_returning({"access_token": "at", "refresh_token": "rt"})
    )
    assert tokens["refresh_token"] == "rt"


def test_exchange_code_requires_refresh_token():
    request = oauth.build_token_request(
        client_id="c", client_secret="s", code="a", redirect_uri="r", code_verifier="v"
    )
    with pytest.raises(oauth.OAuthFlowError, match="myaccount.google.com/permissions"):
        oauth.exchange_code(request, opener=_opener_returning({"access_token": "at"}))


def test_exchange_code_translates_http_error():
    request = oauth.build_token_request(
        client_id="c", client_secret="s", code="a", redirect_uri="r", code_verifier="v"
    )
    with pytest.raises(oauth.OAuthFlowError, match="invalid_grant"):
        oauth.exchange_code(
            request,
            opener=_opener_raising(400, {"error": "invalid_grant", "error_description": "Bad"}),
        )


def test_env_lines_exact_text():
    assert oauth.env_lines(client_id="cid", client_secret="sec", refresh_token="rt") == (
        "GOOGLE_ADS_OAUTH_CLIENT_ID=cid\n"
        "GOOGLE_ADS_OAUTH_CLIENT_SECRET=sec\n"
        "GOOGLE_ADS_OAUTH_REFRESH_TOKEN=rt\n"
    )


def test_verify_request_has_both_headers_and_no_login_customer_id():
    request = oauth.build_verify_request(access_token="at", developer_token="dt")
    assert request.full_url == (
        "https://googleads.googleapis.com/v25/customers:listAccessibleCustomers"
    )
    assert request.get_header("Authorization") == "Bearer at"
    assert request.get_header("Developer-token") == "dt"
    assert request.get_header("Login-customer-id") is None


def test_verify_token_returns_resource_names():
    request = oauth.build_verify_request(access_token="at", developer_token="dt")
    names = oauth.verify_token(
        request, opener=_opener_returning({"resourceNames": ["customers/1234567890"]})
    )
    assert names == ["customers/1234567890"]


def test_verify_token_translates_ads_api_error():
    request = oauth.build_verify_request(access_token="at", developer_token="dt")
    with pytest.raises(oauth.OAuthFlowError, match="developer token"):
        oauth.verify_token(
            request,
            opener=_opener_raising(
                401, {"error": {"code": 401, "message": "The developer token is not valid."}}
            ),
        )


def test_listener_captures_the_redirect_and_ignores_favicon():
    server = oauth.start_listener(0)
    port = server.server_address[1]

    def browser():
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/favicon.ico", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/?code=abc&state=s", timeout=5
        ) as response:
            assert b"close this tab" in response.read()

    thread = threading.Thread(target=browser)
    thread.start()
    try:
        assert oauth.wait_for_code(server, "s", timeout=10) == "abc"
    finally:
        thread.join(timeout=5)
        server.server_close()


def test_main_prints_env_lines_and_never_the_secret(monkeypatch, capsys):
    class FakeServer:
        server_address = ("127.0.0.1", 43123)

        def server_close(self):
            pass

    monkeypatch.setattr(oauth, "start_listener", lambda port: FakeServer())
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(oauth, "wait_for_code", lambda server, state, timeout: "code-1")
    monkeypatch.setattr(
        oauth,
        "exchange_code",
        lambda request, opener=None: {"access_token": "at", "refresh_token": "rt"},
    )

    rc = oauth.main(["--client-id", "cid", "--client-secret", "sec"])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == oauth.env_lines(client_id="cid", client_secret="sec", refresh_token="rt")
    assert "sec" not in captured.err
    assert "In production" in captured.err


def test_main_reports_flow_errors_on_stderr(monkeypatch, capsys):
    class FakeServer:
        server_address = ("127.0.0.1", 43123)

        def server_close(self):
            pass

    monkeypatch.setattr(oauth, "start_listener", lambda port: FakeServer())
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url: True)

    def _deny(server, state, timeout):
        raise oauth.OAuthFlowError("google returned error=access_denied")

    monkeypatch.setattr(oauth, "wait_for_code", _deny)

    rc = oauth.main(["--client-id", "cid", "--client-secret", "sec"])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "access_denied" in captured.err
