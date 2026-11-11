"""Unit tests for the OSC 0/1/2 title extractor."""

from __future__ import annotations

from mobaide.core.osc_parser import (
    MAX_PAYLOAD_CHARS,
    MAX_PS_DIGITS,
    OscTitleExtractor,
)


def test_single_chunk_osc0_bel() -> None:
    ext = OscTitleExtractor()
    assert ext.feed(b"\x1b]0;hello\x07") == "hello"


def test_single_chunk_osc0_st_terminator() -> None:
    ext = OscTitleExtractor()
    assert ext.feed(b"\x1b]0;ST-terminated\x1b\\") == "ST-terminated"


def test_osc_split_at_every_interior_byte() -> None:
    """Whatever the chunk boundary, the parser must reconstruct the title."""
    payload = b"\x1b]2;rebuild me\x07"
    for cut in range(1, len(payload)):
        ext = OscTitleExtractor()
        first = ext.feed(payload[:cut])
        second = ext.feed(payload[cut:])
        # The title commits on the chunk that contains the terminator.
        assert (first, second) == (None, "rebuild me"), f"failed at cut={cut}"


def test_empty_title_payload_reset() -> None:
    """The documented Claude Code title-reset sequence (issue #16353)."""
    ext = OscTitleExtractor()
    assert ext.feed(b"\x1b]0;\x07") == ""


def test_mid_osc_abort_by_fresh_esc() -> None:
    """A new ESC mid-payload that isn't '\\' aborts the OSC."""
    ext = OscTitleExtractor()
    assert ext.feed(b"\x1b]0;abor") is None
    assert ext.feed(b"\x1bX") is None
    assert ext.feed(b"\x1b]0;clean\x07") == "clean"


def test_8bit_osc_entry_with_bel_terminator() -> None:
    """0x9D opens an OSC. 8-bit ST 0x9C deliberately NOT accepted as a
    terminator (it collides with UTF-8 continuation bytes — see the
    module docstring), so the OSC is closed via BEL instead."""
    ext = OscTitleExtractor()
    assert ext.feed(b"\x9d0;eightbit\x07") == "eightbit"


def test_8bit_st_inside_utf8_payload_is_not_a_terminator() -> None:
    """Regression: 0x9C inside a multi-byte UTF-8 rune must not commit early."""
    ext = OscTitleExtractor()
    # Bytes for ✨ are \xe2\x9c\xa8 — the middle byte equals 8-bit ST.
    assert ext.feed(b"\x1b]0;\xe2\x9c\xa8 ok\x07") == "✨ ok"


def test_csi_interleaved_passes_through() -> None:
    """CSI bytes around an OSC must not break extraction."""
    ext = OscTitleExtractor()
    chunk = b"\x1b[31mRED\x1b[0m\x1b]0;tab\x07\x1b[1mBOLD\x1b[0m"
    assert ext.feed(chunk) == "tab"


def test_no_osc_in_chunk_returns_none() -> None:
    ext = OscTitleExtractor()
    assert ext.feed(b"\x1b[31mjust SGR\x1b[0m no title here") is None


def test_osc4_palette_discarded() -> None:
    ext = OscTitleExtractor()
    assert ext.feed(b"\x1b]4;0;rgb:00/00/00\x07") is None


def test_non_title_osc_does_not_consume_subsequent_title() -> None:
    """After tracking a discarded OSC through its terminator, a real title still commits."""
    ext = OscTitleExtractor()
    chunk = b"\x1b]4;0;rgb:00/00/00\x07\x1b]0;real\x07"
    assert ext.feed(chunk) == "real"


def test_multiple_titles_in_one_chunk_returns_last() -> None:
    ext = OscTitleExtractor()
    chunk = b"\x1b]0;first\x07middle\x1b]2;second\x07"
    assert ext.feed(chunk) == "second"


def test_oversized_payload_truncated_and_no_growth() -> None:
    """Payload >MAX is silently truncated, but parser still commits cleanly."""
    payload_bytes = b"A" * (MAX_PAYLOAD_CHARS + 100)
    ext = OscTitleExtractor()
    result = ext.feed(b"\x1b]0;" + payload_bytes + b"\x07")
    assert result is not None
    assert len(result) == MAX_PAYLOAD_CHARS
    assert result == "A" * MAX_PAYLOAD_CHARS


def test_utf8_payload_decoded() -> None:
    ext = OscTitleExtractor()
    assert ext.feed(b"\x1b]0;\xe2\x9c\xa8 task\x07") == "✨ task"


def test_invalid_utf8_replaced_not_raised() -> None:
    ext = OscTitleExtractor()
    out = ext.feed(b"\x1b]0;bad\xff\xfeseq\x07")
    assert out is not None
    assert "bad" in out and "seq" in out


def test_subsequent_chunks_after_commit_continue_to_parse() -> None:
    ext = OscTitleExtractor()
    assert ext.feed(b"\x1b]0;one\x07") == "one"
    assert ext.feed(b"junk in between") is None
    assert ext.feed(b"\x1b]2;two\x07") == "two"


def test_lone_esc_at_chunk_boundary_does_not_emit() -> None:
    ext = OscTitleExtractor()
    assert ext.feed(b"\x1b") is None
    assert ext.feed(b"]0;continue\x07") == "continue"


def test_st_pending_completes_across_chunks() -> None:
    ext = OscTitleExtractor()
    assert ext.feed(b"\x1b]0;split-st\x1b") is None
    assert ext.feed(b"\\") == "split-st"


def test_unterminated_ps_digits_do_not_grow_unbounded() -> None:
    """Hardening: ``ESC ] 0000…`` with no terminator must not grow memory.

    Without the MAX_PS_DIGITS cap, a hostile or buggy PTY child can OOM
    the agent by emitting an unterminated OSC digit stream.
    """
    ext = OscTitleExtractor()
    # Drive past the cap. Result is None — no terminator arrived.
    assert ext.feed(b"\x1b]" + b"0" * (MAX_PS_DIGITS * 1000)) is None
    # Buffer is bounded — past the cap, digits are discarded.
    assert len(ext._ps_buf) <= MAX_PS_DIGITS  # type: ignore[attr-defined]
    # The OSC is now in DISCARD; once it terminates, no title commits.
    assert ext.feed(b";would-have-been-payload\x07") is None
    # Parser cleanly resumes for the next real OSC.
    assert ext.feed(b"\x1b]0;recovered\x07") == "recovered"


def test_fast_path_preserves_state_across_lone_esc_split() -> None:
    """Regression: fast-path must let a lone trailing ESC drive state."""
    ext = OscTitleExtractor()
    assert ext.feed(b"\x1b") is None
    assert ext.feed(b"]0;ok\x07") == "ok"


def test_fast_path_skips_pure_text_chunks() -> None:
    """Sanity: chunks with no ESC bytes return immediately, state unchanged."""
    ext = OscTitleExtractor()
    assert ext.feed(b"plain output, no escapes here" * 100) is None
    # Subsequent real OSC still parses correctly.
    assert ext.feed(b"\x1b]0;after-fast-path\x07") == "after-fast-path"
