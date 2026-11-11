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
from datetime import datetime, timezone
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


@pytest.mark.parametrize(
    "query, match",
    [("/?code=abc&state=other", "state"), ("/?error=access_denied&state=s", "access_denied")],
    ids=["state-mismatch", "google-error"],
)
def test_parse_redirect_rejects_a_bad_redirect(query, match):
    with pytest.raises(oauth.OAuthFlowError, match=match):
        oauth.parse_redirect_query(query, "s")


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


def _body(request):
    return json.loads(request.data.decode("utf-8"))


def test_verify_request_is_a_validate_only_ingest_for_the_destination():
    now = datetime(2026, 9, 2, 12, 18, 0, 123456, tzinfo=timezone.utc)
    request = oauth.build_verify_request(
        access_token="at",
        customer_id="1234567890",
        conversion_action_id="987654321",
        login_customer_id="5555",
        click_id="Cj0K-placeholder",
        now=now,
    )
    assert request.get_method() == "POST"
    assert request.full_url == oauth.INGEST_ENDPOINT
    assert request.get_header("Authorization") == "Bearer at"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Developer-token") is None
    body = _body(request)
    assert body["validateOnly"] is True
    assert body["destinations"] == [
        {
            "operatingAccount": {"accountType": "GOOGLE_ADS", "accountId": "1234567890"},
            "productDestinationId": "987654321",
            "loginAccount": {"accountType": "GOOGLE_ADS", "accountId": "5555"},
        }
    ]
    event = body["events"][0]
    assert event["adIdentifiers"] == {"gclid": "Cj0K-placeholder"}
    assert event["eventSource"] == "WEB"
    assert event["eventTimestamp"] == "2026-09-02T12:18:00Z"
    # The relay's deduplication key (identifier + microsecond instant), so
    # validate-only covers the field too.
    material = "\n".join(("gclid", "Cj0K-placeholder", "2026-09-02T12:18:00.123456+00:00"))
    assert event["transactionId"] == hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    assert event["consent"] == {
        "adUserData": "CONSENT_GRANTED",
        "adPersonalization": "CONSENT_DENIED",
    }
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", event["eventTimestamp"])


def test_verify_destination_returns_field_warnings():
    request = oauth.build_verify_request(
        access_token="at", customer_id="1", conversion_action_id="2"
    )
    assert oauth.verify_destination(request, opener=_opener_returning({})) == []
    warnings = oauth.verify_destination(
        request,
        opener=_opener_returning(
            {"fieldWarnings": [{"field": "events[0]", "description": "odd click id"}]}
        ),
    )
    assert warnings == ["odd click id"]


def test_verify_destination_translates_permission_error():
    request = oauth.build_verify_request(
        access_token="at", customer_id="1", conversion_action_id="2"
    )
    with pytest.raises(oauth.OAuthFlowError, match="PERMISSION_DENIED: The caller"):
        oauth.verify_destination(
            request,
            opener=_opener_raising(
                403,
                {
                    "error": {
                        "code": 403,
                        "message": "The caller does not have permission",
                        "status": "PERMISSION_DENIED",
                    }
                },
            ),
        )


@pytest.mark.parametrize("raw, expected", [("123-456-7890", "1234567890"), ("12ab", None), (None, None)])
def test_digits_tolerates_dashes_and_rejects_garbage(raw, expected):
    assert oauth._digits(raw) == expected


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
    _fake_flow(monkeypatch)

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


def test_main_requires_destination_ids_for_verify(monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_ADS_CUSTOMER_ID", raising=False)
    monkeypatch.delenv("GOOGLE_ADS_CONVERSION_ACTION_ID", raising=False)

    def _never(port):
        raise AssertionError("listener must not start")

    monkeypatch.setattr(oauth, "start_listener", _never)

    rc = oauth.main(["--client-id", "cid", "--client-secret", "sec", "--verify"])

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert "--customer-id" in captured.err


def _fake_flow(monkeypatch):
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


def test_main_verify_reports_the_validated_destination(monkeypatch, capsys):
    _fake_flow(monkeypatch)
    seen = {}

    def fake_verify(request, opener=None):
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return ["odd click id"]

    monkeypatch.setattr(oauth, "verify_destination", fake_verify)

    rc = oauth.main(
        [
            "--client-id", "cid", "--client-secret", "sec", "--verify",
            "--customer-id", "123-456-7890", "--conversion-action-id", "987654321",
            "--login-customer-id", "555-555-5555",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == oauth.env_lines(client_id="cid", client_secret="sec", refresh_token="rt")
    assert seen["body"]["destinations"][0]["operatingAccount"]["accountId"] == "1234567890"
    assert seen["body"]["destinations"][0]["loginAccount"]["accountId"] == "5555555555"
    assert "Validated" in captured.err and "via manager 5555555555" in captured.err
    assert "warning: odd click id" in captured.err


def test_main_verify_failure_keeps_the_token_and_returns_1(monkeypatch, capsys):
    _fake_flow(monkeypatch)

    def refuse(request, opener=None):
        raise oauth.OAuthFlowError("HTTP 403 from x: PERMISSION_DENIED: nope")

    monkeypatch.setattr(oauth, "verify_destination", refuse)

    rc = oauth.main(
        [
            "--client-id", "cid", "--client-secret", "sec", "--verify",
            "--customer-id", "1234567890", "--conversion-action-id", "987654321",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == oauth.env_lines(client_id="cid", client_secret="sec", refresh_token="rt")
    assert "PERMISSION_DENIED" in captured.err
    assert "GOOGLE_ADS_LOGIN_CUSTOMER_ID" in captured.err
