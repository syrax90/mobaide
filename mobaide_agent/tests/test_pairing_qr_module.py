"""Tests for ``mobaide.agent.pairing_qr``.

Pure-function tests for ``compute_trust_key_fp``, ``encode_qr_payload``,
``parse_qr_payload``, plus the ``PendingPairing`` JSON persistence
lifecycle (``save`` / ``load`` / ``clear``). The agent's QR-rendering
``segno`` integration is exercised for the encoding-dispatch behaviour
(half-block path on UTF-8 streams, ASCII fallback on cp1252 and on
streams with no declared encoding) using in-memory ``io.TextIOWrapper``
stand-ins so the assertions hold without a real terminal.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from mobaide.agent.pairing_qr import (
    DEFAULT_PAIRING_WEB_ORIGIN,
    PAIRING_WEB_ORIGIN_ENV,
    ParsedQrPayload,
    PendingPairing,
    clear_pending_pairing,
    compute_trust_key_fp,
    encode_qr_payload,
    format_pairing_link,
    load_pending_pairing,
    pairing_web_origin,
    parse_qr_payload,
    pending_pairing_path,
    read_pinned_trust_key_fp,
    render_qr_to_terminal,
    save_pending_pairing,
    write_pairing_hints,
)

# Resolved from this file, never the CWD: pytest is run from ``mobaide_agent/``
# so ``import mobaide`` picks up these sources rather than the installed
# checkout, which leaves the repo root two levels up.
_QR_PAYLOAD_FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "protocol_fixtures"
        / "pairing"
        / "qr_payload.json"
    ).read_text(encoding="utf-8")
)


def _fixture_cases(key: str) -> list[dict]:
    cases = _QR_PAYLOAD_FIXTURE[key]
    assert cases, f"{key} is empty — the golden would assert nothing"
    return cases


def _case_ids(cases: list[dict]) -> list[str]:
    return [case["name"] for case in cases]


_ENCODE_CASES = _fixture_cases("encode_cases")
_PARSE_CASES = _fixture_cases("parse_cases")
_LINK_CASES = _fixture_cases("link_cases")


@dataclass
class _StubLayout:
    """Minimal stand-in for ``mobaide.agent.updater.InstallLayout``.

    The pairing_qr module only reads ``layout.prefix`` and
    ``layout.trust_public_key``. Tests pin a per-test tmp_path so
    nothing leaks between tests.
    """

    prefix: Path

    @property
    def trust_public_key(self) -> Path:
        return self.prefix / "trust" / "central_server.pub"


@pytest.fixture
def layout(tmp_path) -> _StubLayout:
    (tmp_path / "trust").mkdir()
    return _StubLayout(prefix=tmp_path)


def _pending_for_now(qr_payload: str) -> PendingPairing:
    return PendingPairing(
        nonce="nonce-abc",
        expires_at="2099-01-01T00:00:00+00:00",
        trust_key_fp="ff" * 32,
        ws_url="wss://relay.test/ws/pairing/nonce-abc",
        relay_url="https://relay.test",
        qr_payload=qr_payload,
    )


def _far_future_payload() -> str:
    # exp_unix far enough in the future that ``load_pending_pairing``
    # never auto-expires it during the test run.
    far = int(time.time()) + 10_000
    return encode_qr_payload(
        "https://relay.test",
        "nonce-abc",
        "ff" * 32,
        far,
    )


class TestComputeTrustKeyFp:
    def test_known_vector_matches_pinned_algorithm(self):
        # Pinned vector — if either side of the agent/server/Android
        # triplet drifts, this test catches it before integration.
        # Vector: SHA-256 of 32 zero bytes is fixed by RFC 6234.
        zero_b64 = base64.b64encode(b"\x00" * 32).decode()
        # echo -n -e '\x00...x32' | sha256sum
        expected = (
            "66687aadf862bd776c8fc18b8e9f8e20"
            "089714856ee233b3902a591d0d5f2925"
        )

        assert compute_trust_key_fp(zero_b64) == expected


class TestEncodeQrPayload:
    @pytest.mark.parametrize(
        "case", _ENCODE_CASES, ids=_case_ids(_ENCODE_CASES)
    )
    def test_matches_shared_golden(self, case):
        out = encode_qr_payload(
            case["relay_url"],
            case["nonce"],
            case["trust_key_fp"],
            case["exp_unix"],
        )

        assert out == case["expect"]


class TestParseQrPayload:
    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "MOBA0|a|b|c|1",                 # wrong version
            "MOBA1|a|b|c",                   # 4 fields
            "MOBA1|a|b|c|1|extra",           # 6 fields
            "MOBA1||b|c|1",                  # blank relay_url
            "MOBA1|a||c|1",                  # blank nonce
            "MOBA1|a|b||1",                  # blank trust_key_fp
            "MOBA1|a|b|c|notanumber",        # non-numeric exp
        ],
    )
    def test_returns_none_on_malformed_input(self, bad):
        assert parse_qr_payload(bad) is None

    def test_absurd_digit_count_returns_none_rather_than_raising(self):
        """Past ``sys.get_int_max_str_digits()`` the shape check passes but
        ``int()`` raises; the guard in ``parse_qr_payload`` explains why that
        must not escape."""
        assert parse_qr_payload("MOBA1|https://relay.test|n|fp|" + "9" * 5000) is None

    @pytest.mark.parametrize("case", _PARSE_CASES, ids=_case_ids(_PARSE_CASES))
    def test_matches_shared_golden(self, case):
        parsed = parse_qr_payload(case["raw"])
        expected = case["expect"]

        if expected is None:
            assert parsed is None
            return
        assert parsed == ParsedQrPayload(
            version=expected["version"],
            relay_url=expected["relay_url"],
            nonce=expected["nonce"],
            trust_key_fp=expected["trust_key_fp"],
            exp_unix=expected["exp_unix"],
        )


class TestPairingWebOrigin:
    @pytest.mark.parametrize(
        "override,expected",
        [
            # A dev server on loopback has no TLS; refusing plain http would
            # make the override useless for exactly the case it exists for.
            ("http://localhost:5173", "http://localhost:5173"),
            # Every trailing slash, so the link never grows a `//pair`.
            ("https://pair.example.com///", "https://pair.example.com"),
            ("  https://pair.example.com  ", "https://pair.example.com"),
        ],
    )
    def test_honours_usable_override(self, monkeypatch, override, expected):
        monkeypatch.setenv(PAIRING_WEB_ORIGIN_ENV, override)

        assert pairing_web_origin() == expected

    @pytest.mark.parametrize(
        "override",
        [
            "",
            "app.mobaide.com",           # no scheme — not clickable
            "javascript:alert(1)",       # would render an executable "link"
            "https://",                  # scheme only: rstrip leaves "https:"
        ],
    )
    def test_ignores_unusable_override(self, monkeypatch, override):
        # Falling back beats printing a link nobody can open: the payload on
        # the line above still pastes, so a wrong host is recoverable and a
        # broken URL is not.
        monkeypatch.setenv(PAIRING_WEB_ORIGIN_ENV, override)

        assert pairing_web_origin() == DEFAULT_PAIRING_WEB_ORIGIN


class TestFormatPairingLink:
    @pytest.mark.parametrize("case", _LINK_CASES, ids=_case_ids(_LINK_CASES))
    def test_matches_shared_golden(self, monkeypatch, case):
        monkeypatch.setenv(PAIRING_WEB_ORIGIN_ENV, case["origin"])

        assert format_pairing_link(case["payload"]) == case["expect"]

    @pytest.mark.parametrize("case", _LINK_CASES, ids=_case_ids(_LINK_CASES))
    def test_fragment_round_trips_back_to_the_payload(self, monkeypatch, case):
        monkeypatch.setenv(PAIRING_WEB_ORIGIN_ENV, case["origin"])
        fragment = format_pairing_link(case["payload"]).split("#", 1)[1]

        # What the web decoder does: restore the stripped padding, then decode.
        padded = fragment + "=" * (-len(fragment) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")

        assert decoded == case["payload"]


class TestWritePairingHints:
    _PAYLOAD = f"MOBA1|https://relay.test|nonce-abc|{'ff' * 32}|1700000000"

    def _render(self, monkeypatch, payload: str | None = None) -> str:
        monkeypatch.delenv(PAIRING_WEB_ORIGIN_ENV, raising=False)
        stream = io.StringIO()

        write_pairing_hints(payload or self._PAYLOAD, stream=stream)

        return stream.getvalue()

    def test_link_occupies_its_own_line_after_the_payload(self, monkeypatch):
        out = self._render(monkeypatch)
        lines = out.split("\n")
        link = f"{DEFAULT_PAIRING_WEB_ORIGIN}/pair#"

        assert any(line.startswith(link) for line in lines)
        # Pasteable artifact first, link second — `gh auth login`'s order.
        assert lines.index(self._PAYLOAD) < next(
            index for index, line in enumerate(lines) if line.startswith(link)
        )

    def test_output_is_pure_ascii(self, monkeypatch):
        # A Windows service redirects stdout to a cp437/cp1252 log file, and a
        # UnicodeEncodeError raised here tears down the pairing WebSocket
        # before ``wait_for_claim`` — the incident this module's
        # ``_stream_supports_block_glyphs`` exists for. So no em dash, no
        # arrows, no box glyphs in the framing this writer adds.
        out = self._render(monkeypatch)

        assert out.isascii(), f"non-ASCII byte in pairing hints: {out!r}"

    def test_substitutes_a_notice_when_the_stream_cannot_encode_the_payload(
        self, monkeypatch
    ):
        """An internationalized ``relay_url`` must not raise on a cp1252 log.

        ``PUBLIC_BASE_URL`` is operator config and may be an IDN host, so the
        payload is not ASCII by construction the way the framing is. The
        sibling ``render_qr_to_terminal`` probes the stream's codec for exactly
        this reason; without the same probe here, ``show-qr`` would exit
        non-zero under ``install.ps1`` (a hard failure) and the pairing
        monitor's render would abort its WebSocket.
        """
        monkeypatch.delenv(PAIRING_WEB_ORIGIN_ENV, raising=False)
        stream, buf = _cp1252_stream()
        # Cyrillic, deliberately: cp1252 *can* carry `é`, so a Latin-1 accent
        # would prove nothing about the probe.
        payload = f"MOBA1|https://релэй.test|nonce-abc|{'ff' * 32}|1700000000"

        write_pairing_hints(payload, stream=stream)
        stream.flush()
        out = buf.getvalue().decode("cp1252")

        assert "cannot render" in out
        assert "nonce-abc" not in out

    def test_still_prints_an_ascii_payload_on_a_cp1252_stream(self, monkeypatch):
        monkeypatch.delenv(PAIRING_WEB_ORIGIN_ENV, raising=False)
        stream, buf = _cp1252_stream()

        write_pairing_hints(self._PAYLOAD, stream=stream)
        stream.flush()

        assert self._PAYLOAD in buf.getvalue().decode("cp1252").split("\n")

class TestReadPinnedTrustKeyFp:
    def test_returns_none_when_unpinned(self, layout):
        # Trust file does not exist yet.
        assert read_pinned_trust_key_fp(layout) is None

    def test_returns_fingerprint_when_pinned(self, layout):
        raw = b"\xab" * 32
        b64 = base64.b64encode(raw).decode()
        layout.trust_public_key.write_text(b64 + "\n", encoding="utf-8")

        fp = read_pinned_trust_key_fp(layout)

        assert fp == hashlib.sha256(raw).hexdigest()

    def test_returns_none_on_invalid_base64(self, layout):
        layout.trust_public_key.write_text("not~valid~b64@@@", encoding="utf-8")

        # Returns None rather than raising — the agent treats this as
        # "unpinned" and refuses to render a QR.
        assert read_pinned_trust_key_fp(layout) is None


class TestPendingPairingLifecycle:
    def test_save_then_load_round_trips(self, layout):
        pending = _pending_for_now(_far_future_payload())

        save_pending_pairing(layout, pending)
        loaded = load_pending_pairing(layout)

        assert loaded == pending

    def test_load_returns_none_on_corrupt_json(self, layout):
        target = pending_pairing_path(layout)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("not-json{{{", encoding="utf-8")

        assert load_pending_pairing(layout) is None

    def test_load_returns_none_when_payload_expired(self, layout):
        # exp_unix in the past → load returns None even though the file
        # is structurally fine.
        past = int(time.time()) - 10
        expired_qr = encode_qr_payload(
            "https://relay.test", "n1", "ff" * 32, past,
        )
        pending = _pending_for_now(expired_qr)

        save_pending_pairing(layout, pending)

        assert load_pending_pairing(layout) is None

    def test_load_returns_none_when_qr_payload_malformed(self, layout):
        pending = _pending_for_now("MOBA0|garbage")

        save_pending_pairing(layout, pending)

        assert load_pending_pairing(layout) is None

    def test_clear_is_idempotent(self, layout):
        clear_pending_pairing(layout)  # no-op when absent
        save_pending_pairing(layout, _pending_for_now(_far_future_payload()))
        clear_pending_pairing(layout)
        clear_pending_pairing(layout)  # second call must not raise

        assert not pending_pairing_path(layout).exists()

_SAMPLE_QR_PAYLOAD = (
    f"MOBA1|wss://relay.test|abcdef0123456789|{'ff' * 32}|9999999999"
)
_BLOCK_GLYPH_RANGE = range(0x2580, 0x25A0)


def _utf8_stream() -> tuple[io.TextIOWrapper, io.BytesIO]:
    buf = io.BytesIO()
    return io.TextIOWrapper(
        buf, encoding="utf-8", newline="", write_through=True
    ), buf


def _cp1252_stream() -> tuple[io.TextIOWrapper, io.BytesIO]:
    buf = io.BytesIO()
    return io.TextIOWrapper(
        buf, encoding="cp1252", newline="", write_through=True
    ), buf


class TestRenderQrToTerminal:
    """Encoding-dispatch behaviour of ``render_qr_to_terminal``.

    Pins the contract that:

    * a UTF-8-capable stream gets segno's compact half-block rendering
      (at least one glyph in U+2580–U+259F appears);
    * a cp1252 stream (and any stream whose codec cannot carry those
      glyphs) silently falls back to the ASCII ``##`` rendering — no
      ``UnicodeEncodeError`` propagates, no half-block glyphs leak into
      the output.

    The Windows-service crash in ``agent.out.log`` came from the second
    contract being absent: segno wrote half-blocks straight to a
    cp1252-redirected stdout, the write raised, and the pairing flow
    aborted before ``wait_for_claim``. These tests would have caught it.
    """

    def test_compact_block_glyphs_on_utf8_stream(self):
        stream, buf = _utf8_stream()

        render_qr_to_terminal(_SAMPLE_QR_PAYLOAD, stream=stream)
        stream.flush()
        output = buf.getvalue().decode("utf-8")

        assert any(
            ord(c) in _BLOCK_GLYPH_RANGE for c in output
        ), "expected at least one half-block glyph on a UTF-8 stream"

    def test_ascii_fallback_on_cp1252_stream(self):
        stream, buf = _cp1252_stream()

        render_qr_to_terminal(_SAMPLE_QR_PAYLOAD, stream=stream)
        stream.flush()
        output = buf.getvalue().decode("cp1252")

        assert "##" in output, "ASCII fallback must emit '##' dark cells"
        assert not any(
            ord(c) in _BLOCK_GLYPH_RANGE for c in output
        ), "ASCII fallback must not emit half-block glyphs"

    def test_ascii_fallback_when_stream_encoding_is_unknown_codec(self):
        # A misconfigured ``PYTHONIOENCODING`` (or a custom stream wrapper
        # that advertises a bogus codec name) makes the encoding probe
        # raise ``LookupError`` rather than ``UnicodeEncodeError``. The
        # ``except (UnicodeEncodeError, LookupError)`` tuple in
        # ``_stream_supports_block_glyphs`` exists specifically for this
        # case — pin it so a future refactor that narrows the except to
        # only ``UnicodeEncodeError`` would crash the pairing flow on
        # bogus-codec stdout instead of falling back cleanly.
        class _BogusEncodingStream:
            encoding = "definitely-not-a-real-codec-name"

            def __init__(self) -> None:
                self.buffer: list[str] = []

            def write(self, s: str) -> int:
                self.buffer.append(s)
                return len(s)

            def flush(self) -> None:
                pass

        stream = _BogusEncodingStream()

        render_qr_to_terminal(_SAMPLE_QR_PAYLOAD, stream=stream)
        output = "".join(stream.buffer)

        assert "##" in output, (
            "unknown-codec stream must fall back to ASCII; if this "
            "fires, ``LookupError`` was likely dropped from the "
            "encoding-probe except tuple"
        )
        assert not any(
            ord(c) in _BLOCK_GLYPH_RANGE for c in output
        ), "unknown-codec fallback must not emit half-block glyphs"
