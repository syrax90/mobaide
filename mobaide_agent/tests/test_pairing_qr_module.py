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
    ParsedQrPayload,
    PendingPairing,
    clear_pending_pairing,
    compute_trust_key_fp,
    encode_qr_payload,
    load_pending_pairing,
    parse_qr_payload,
    pending_pairing_path,
    read_pinned_trust_key_fp,
    render_qr_to_terminal,
    save_pending_pairing,
)


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
    def test_matches_sha256_hex_of_decoded_bytes(self):
        raw = b"\x42" * 32
        b64 = base64.b64encode(raw).decode()

        assert compute_trust_key_fp(b64) == hashlib.sha256(raw).hexdigest()

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
    def test_canonical_format(self):
        out = encode_qr_payload(
            "https://relay.test", "n1", "ff" * 32, 1700000000
        )

        assert out == f"MOBA1|https://relay.test|n1|{'ff' * 32}|1700000000"


class TestParseQrPayload:
    def test_round_trip_with_encode(self):
        text = encode_qr_payload(
            "https://relay.test", "n1", "ff" * 32, 1700000000
        )

        parsed = parse_qr_payload(text)

        assert parsed == ParsedQrPayload(
            version="MOBA1",
            relay_url="https://relay.test",
            nonce="n1",
            trust_key_fp="ff" * 32,
            exp_unix=1700000000,
        )

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

    def test_load_returns_none_when_file_absent(self, layout):
        assert load_pending_pairing(layout) is None

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

    def test_save_writes_atomic_and_readable_json(self, layout):
        # The file must be valid JSON parseable into PendingPairing's fields.
        pending = _pending_for_now(_far_future_payload())

        save_pending_pairing(layout, pending)
        text = pending_pairing_path(layout).read_text(encoding="utf-8")
        decoded = json.loads(text)

        assert decoded["nonce"] == pending.nonce
        assert decoded["qr_payload"] == pending.qr_payload


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

    def test_ascii_fallback_when_stream_has_no_encoding(self):
        # io.StringIO has encoding=None — exercises the no-encoding branch
        # of _stream_supports_block_glyphs without needing a real codec.
        stream = io.StringIO()

        render_qr_to_terminal(_SAMPLE_QR_PAYLOAD, stream=stream)
        output = stream.getvalue()

        assert "##" in output
        assert all(
            ord(c) < 128 for c in output
        ), "encoding=None must use the pure-ASCII fallback"

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
