"""The server-issued, signed ``mobaide_ads`` cookie: signing helpers and the
``POST /api/ads/consent`` endpoint behind the landing page's banner."""
from __future__ import annotations

import base64
import re
import time
from urllib.parse import parse_qs

import pytest

from central_server.ads.consent import (
    COOKIE_NAME,
    canonical_payload,
    cookie_domain,
    cookie_value,
    network_fingerprint,
    sign_attribution,
    verify_attribution,
)
from tests.conftest import TEST_JWT_SECRET

SECRET = "unit-secret"
NET = network_fingerprint(SECRET, "203.0.113.7")
CLICK = {
    "click_id": "Cj0K_abc-123",
    "click_id_kind": "gclid",
    "clicked_at": 1_756_800_000,
    "network": NET,
}
CONSENT_ONLY = {"click_id": None, "click_id_kind": None, "clicked_at": 1_756_800_000, "network": NET}
# The ASGI transport's default client address, i.e. what the endpoint sees.
TEST_CLIENT_NET = network_fingerprint(TEST_JWT_SECRET, "127.0.0.1")


def _decode(value: str) -> tuple[dict, str]:
    encoded, _, signature = value.rpartition(".")
    payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    return parse_qs(payload, keep_blank_values=True), signature


class TestSigning:
    def test_round_trips_a_click(self):
        signature = sign_attribution(SECRET, **CLICK)
        assert verify_attribution(SECRET, **CLICK, signature=signature)

    def test_round_trips_consent_only(self):
        signature = sign_attribution(SECRET, **CONSENT_ONLY)
        assert verify_attribution(SECRET, **CONSENT_ONLY, signature=signature)

    @pytest.mark.parametrize(
        "mutation",
        [
            {"click_id": "Cj0K_abc-124"},
            {"click_id_kind": "wbraid"},
            {"clicked_at": 1_756_800_001},
            {"click_id": None, "click_id_kind": None},
            {"network": network_fingerprint(SECRET, "203.0.113.8")},
        ],
    )
    def test_tampered_fields_fail(self, mutation):
        signature = sign_attribution(SECRET, **CLICK)
        assert not verify_attribution(SECRET, **{**CLICK, **mutation}, signature=signature)

    def test_wrong_secret_fails(self):
        signature = sign_attribution(SECRET, **CLICK)
        assert not verify_attribution("other", **CLICK, signature=signature)

    def test_missing_pieces_fail(self):
        signature = sign_attribution(SECRET, **CLICK)
        assert not verify_attribution(SECRET, **{**CLICK, "clicked_at": None}, signature=signature)
        assert not verify_attribution(SECRET, **CLICK, signature=None)
        assert not verify_attribution(SECRET, **CLICK, signature="")
        assert not verify_attribution(SECRET, **{**CLICK, "network": None}, signature=signature)
        unpaired = {"click_id": "Cj0K_abc-123", "click_id_kind": None, "clicked_at": 1, "network": NET}
        assert not verify_attribution(SECRET, **unpaired, signature=signature)

    @pytest.mark.parametrize("hostile", ["sig☃", "\ud800", "x" * 10_000])
    def test_hostile_signatures_are_false_not_errors(self, hostile):
        assert verify_attribution(SECRET, **CLICK, signature=hostile) is False

    def test_canonical_payload_shapes(self):
        assert canonical_payload(**CLICK) == f"v=1&k=gclid&t=1756800000&id=Cj0K_abc-123&n={NET}"
        assert canonical_payload(**CONSENT_ONLY) == f"v=1&t=1756800000&n={NET}"

    def test_cookie_value_is_cookie_safe_and_verifies(self):
        value = cookie_value(SECRET, **CLICK)
        # No `=`, `;`, quotes or spaces: Starlette would otherwise quote it
        # and the SPA would read the quotes back as part of the value.
        assert re.fullmatch(r"[A-Za-z0-9_.-]+", value)
        params, signature = _decode(value)
        assert params == {
            "v": ["1"], "k": ["gclid"], "t": ["1756800000"], "id": ["Cj0K_abc-123"], "n": [NET],
        }
        assert verify_attribution(SECRET, **CLICK, signature=signature)


class TestNetworkFingerprint:
    def test_is_stable_and_cookie_safe(self):
        first = network_fingerprint(SECRET, "203.0.113.7")
        assert first == network_fingerprint(SECRET, "203.0.113.7")
        assert re.fullmatch(r"[A-Za-z0-9_-]{16}", first)

    def test_differs_by_address_and_by_secret(self):
        assert network_fingerprint(SECRET, "203.0.113.7") != network_fingerprint(SECRET, "203.0.113.8")
        assert network_fingerprint(SECRET, "203.0.113.7") != network_fingerprint("other", "203.0.113.7")

    def test_ipv6_binds_the_64_prefix(self):
        """Privacy extensions rotate the host half within hours; the /64 is
        what stays with the visitor."""
        same = network_fingerprint(SECRET, "2001:db8:1:2:aaaa:bbbb:cccc:dddd")
        assert same == network_fingerprint(SECRET, "2001:db8:1:2:1111:2222:3333:4444")
        assert same != network_fingerprint(SECRET, "2001:db8:1:3:aaaa:bbbb:cccc:dddd")

    def test_ipv4_mapped_ipv6_collapses_to_ipv4(self):
        assert network_fingerprint(SECRET, "::ffff:203.0.113.7") == network_fingerprint(SECRET, "203.0.113.7")

    def test_unknown_addresses_never_raise(self):
        assert network_fingerprint(SECRET, None) == network_fingerprint(SECRET, "")
        assert network_fingerprint(SECRET, "not an ip") == network_fingerprint(SECRET, " NOT AN IP ")


@pytest.mark.parametrize(
    "hostname, expected",
    [
        ("mobaide.com", "mobaide.com"),
        ("test", "test"),
        ("localhost", None),
        ("LOCALHOST", None),
        ("127.0.0.1", None),
        ("::1", None),
        ("[::1]", None),
        (None, None),
        ("", None),
    ],
)
def test_cookie_domain(hostname, expected):
    assert cookie_domain(hostname) == expected


SAME_ORIGIN = {"Origin": "https://test"}


def _cookie_from(resp) -> tuple[str, str]:
    header = resp.headers["set-cookie"]
    name, _, rest = header.partition("=")
    return header, rest.split(";", 1)[0]


class TestConsentEndpoint:
    async def test_sets_a_signed_cookie_with_the_documented_attributes(self, client):
        resp = await client.post(
            "/api/ads/consent",
            json={"click_id": "Cj0K_abc-123", "click_id_kind": "gclid", "arrived_at": 1_756_800_000},
            headers=SAME_ORIGIN,
        )
        assert resp.status_code == 204, resp.text
        header, value = _cookie_from(resp)
        assert header.startswith(f"{COOKIE_NAME}=")
        assert '"' not in header
        lowered = header.lower()
        for attribute in ("max-age=7776000", "path=/", "domain=test", "samesite=lax", "secure"):
            assert attribute in lowered
        assert "httponly" not in lowered
        params, signature = _decode(value)
        assert params == {
            "v": ["1"],
            "k": ["gclid"],
            "t": ["1756800000"],
            "id": ["Cj0K_abc-123"],
            "n": [TEST_CLIENT_NET],
        }
        assert verify_attribution(
            TEST_JWT_SECRET,
            click_id="Cj0K_abc-123",
            click_id_kind="gclid",
            clicked_at=1_756_800_000,
            network=TEST_CLIENT_NET,
            signature=signature,
        )

    async def test_arrival_time_is_capped_at_the_server_clock(self, client):
        resp = await client.post(
            "/api/ads/consent", json={"arrived_at": 4_102_444_800}, headers=SAME_ORIGIN
        )
        assert resp.status_code == 204
        params, _ = _decode(_cookie_from(resp)[1])
        assert int(params["t"][0]) <= int(time.time())

    async def test_missing_arrival_time_uses_the_server_clock(self, client):
        before = int(time.time())
        resp = await client.post("/api/ads/consent", json={}, headers=SAME_ORIGIN)
        params, signature = _decode(_cookie_from(resp)[1])
        assert before <= int(params["t"][0]) <= int(time.time())
        assert "k" not in params and "id" not in params
        assert verify_attribution(
            TEST_JWT_SECRET,
            click_id=None,
            click_id_kind=None,
            clicked_at=int(params["t"][0]),
            network=TEST_CLIENT_NET,
            signature=signature,
        )

    @pytest.mark.parametrize(
        "bad",
        [
            {"click_id": "Cj0K_abc-123"},
            {"click_id_kind": "gclid"},
            {"click_id": "has space", "click_id_kind": "gclid"},
            {"click_id": "Cj0K_abc-123", "click_id_kind": "fbclid"},
            {"arrived_at": -1},
        ],
    )
    async def test_rejects_malformed_bodies(self, client, bad):
        resp = await client.post("/api/ads/consent", json=bad, headers=SAME_ORIGIN)
        assert resp.status_code == 422
        assert "set-cookie" not in resp.headers

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Origin": "https://get.mobaide.com"},
            {"Origin": "https://evil.example"},
            {"Sec-Fetch-Site": "cross-site", "Origin": "https://test"},
            {"Sec-Fetch-Site": "same-site"},
        ],
    )
    async def test_refuses_anything_but_the_landing_page_itself(self, client, headers):
        """A sibling host must not be able to mint a validly signed cookie for
        a visitor through a cross-site POST; both headers are browser-set."""
        resp = await client.post("/api/ads/consent", json={}, headers=headers)
        assert resp.status_code == 403, resp.text
        assert "set-cookie" not in resp.headers

    async def test_sec_fetch_site_same_origin_is_enough(self, client):
        resp = await client.post(
            "/api/ads/consent", json={}, headers={"Sec-Fetch-Site": "same-origin"}
        )
        assert resp.status_code == 204
