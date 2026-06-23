"""Tests for the PTY-side OSC dynamic-color (OSC 10/11/12) query interceptor.

Sibling of ``test_da_responder.py``. Verifies the agent answers OSC 10/11/12
color queries (so Codex / any ratatui TUI gets a color report) and strips them
from the byte stream so the client emulator never re-answers a replayed query
and loops the reply back into the input field.
"""

from __future__ import annotations

from mobaide.core.osc_color_responder import OscColorResponder

BEL = b"\x07"
ST = b"\x1b\\"

# Expected replies, matching Termux's default palette (fg/cursor white, bg black).
FG_BEL = b"\x1b]10;rgb:ffff/ffff/ffff\x07"
BG_BEL = b"\x1b]11;rgb:0000/0000/0000\x07"
CURSOR_BEL = b"\x1b]12;rgb:ffff/ffff/ffff\x07"
BG_ST = b"\x1b]11;rgb:0000/0000/0000\x1b\\"


def test_osc10_foreground_bel() -> None:
    r = OscColorResponder()
    out = r.process(b"hi\x1b]10;?\x07bye")
    assert out == b"hibye"
    assert r.pending_replies() == FG_BEL
    # Replies are drained on read.
    assert r.pending_replies() == b""


def test_osc11_background_bel() -> None:
    r = OscColorResponder()
    out = r.process(b"\x1b]11;?\x07")
    assert out == b""
    assert r.pending_replies() == BG_BEL


def test_osc12_cursor_bel() -> None:
    r = OscColorResponder()
    out = r.process(b"\x1b]12;?\x07")
    assert out == b""
    assert r.pending_replies() == CURSOR_BEL


def test_st_terminator_is_echoed() -> None:
    # An ST-terminated query gets an ST-terminated reply.
    r = OscColorResponder()
    out = r.process(b"\x1b]11;?\x1b\\")
    assert out == b""
    assert r.pending_replies() == BG_ST


def test_query_between_output() -> None:
    r = OscColorResponder()
    out = r.process(b"prompt> \x1b]11;?\x1b\\ rest")
    assert out == b"prompt>  rest"
    assert r.pending_replies() == BG_ST


def test_color_set_passes_through() -> None:
    # A color *set* (no '?') is legitimate output: never strip it, never reply.
    r = OscColorResponder()
    payload = b"\x1b]11;rgb:1234/5678/9abc\x07"
    assert r.process(payload) == payload
    assert r.pending_replies() == b""


def test_color_set_hash_name_passes_through() -> None:
    r = OscColorResponder()
    payload = b"\x1b]10;#ffffff\x07"
    assert r.process(payload) == payload
    assert r.pending_replies() == b""


def test_titles_pass_through() -> None:
    # OSC 0/1/2 (window/icon titles) are not color queries.
    r = OscColorResponder()
    payload = b"\x1b]0;my title\x07\x1b]2;other\x1b\\"
    assert r.process(payload) == payload
    assert r.pending_replies() == b""


def test_long_title_without_terminator_not_overbuffered() -> None:
    # A title whose terminator falls in a later read must still flow out now —
    # the carry rule only buffers fragments that are a prefix of a color query.
    r = OscColorResponder()
    payload = b"\x1b]0;" + b"x" * 500
    assert r.process(payload) == payload
    assert r.pending_replies() == b""


def test_osc4_palette_query_passes_through() -> None:
    # OSC 4 (palette) is a different shape and intentionally not handled here.
    r = OscColorResponder()
    payload = b"\x1b]4;1;?\x07"
    assert r.process(payload) == payload
    assert r.pending_replies() == b""


def test_osc52_clipboard_passes_through() -> None:
    r = OscColorResponder()
    payload = b"\x1b]52;c;SGVsbG8=\x07"
    assert r.process(payload) == payload
    assert r.pending_replies() == b""


def test_non_osc_csi_passes_through() -> None:
    r = OscColorResponder()
    payload = b"\x1b[31mred\x1b[0m"
    assert r.process(payload) == payload
    assert r.pending_replies() == b""


def test_plain_text_passes_through() -> None:
    r = OscColorResponder()
    payload = b"just some normal text\nwith a newline"
    assert r.process(payload) == payload
    assert r.pending_replies() == b""


def test_multiple_queries_in_one_chunk() -> None:
    r = OscColorResponder()
    out = r.process(b"\x1b]10;?\x07\x1b]11;?\x07")
    assert out == b""
    assert r.pending_replies() == FG_BEL + BG_BEL


def test_compound_query_fg_then_bg() -> None:
    # ESC]10;?;? asks fg then bg; the emulator emits two replies, both with the
    # request's Ps (10) but advancing colors. We match that byte-for-byte.
    r = OscColorResponder()
    out = r.process(b"\x1b]10;?;?\x07")
    assert out == b""
    assert r.pending_replies() == (
        b"\x1b]10;rgb:ffff/ffff/ffff\x07" + b"\x1b]10;rgb:0000/0000/0000\x07"
    )


def test_compound_query_capped_at_cursor() -> None:
    # Starting at cursor (12), a second spec would advance past the cursor slot,
    # so only one reply is produced.
    r = OscColorResponder()
    out = r.process(b"\x1b]12;?;?\x07")
    assert out == b""
    assert r.pending_replies() == CURSOR_BEL


def test_split_at_esc_boundary() -> None:
    r = OscColorResponder()
    a = r.process(b"foo\x1b")
    b = r.process(b"]10;?\x07bar")
    assert a + b == b"foobar"
    assert r.pending_replies() == FG_BEL


def test_split_after_introducer() -> None:
    r = OscColorResponder()
    a = r.process(b"x\x1b]")
    b = r.process(b"11;?\x07y")
    assert a + b == b"xy"
    assert r.pending_replies() == BG_BEL


def test_split_mid_params() -> None:
    r = OscColorResponder()
    a = r.process(b"\x1b]10;")
    b = r.process(b"?\x07done")
    assert a + b == b"done"
    assert r.pending_replies() == FG_BEL


def test_split_inside_st_terminator() -> None:
    # The hardest split: the ESC of an ST terminator lands at the chunk edge.
    r = OscColorResponder()
    a = r.process(b"\x1b]11;?\x1b")
    b = r.process(b"\\rest")
    assert a + b == b"rest"
    assert r.pending_replies() == BG_ST


def test_split_compound_query_after_first_spec() -> None:
    # Compound query ESC]10;?;? split right after the first ;? — the boundary the
    # old single-query carry list missed. ESC]10;?; is still a completable query
    # prefix, so it is carried and the compound reassembles on the next read.
    r = OscColorResponder()
    a = r.process(b"\x1b]10;?;")
    assert a == b""
    assert r.pending_replies() == b""  # incomplete: nothing emitted yet
    b = r.process(b"?\x07")
    assert b == b""
    assert r.pending_replies() == (
        b"\x1b]10;rgb:ffff/ffff/ffff\x07" + b"\x1b]10;rgb:0000/0000/0000\x07"
    )


def test_split_compound_query_before_second_spec() -> None:
    # Same compound query, split one byte earlier (after a full first ;?).
    r = OscColorResponder()
    a = r.process(b"\x1b]10;?")
    b = r.process(b";?\x07")
    assert a + b == b""
    assert r.pending_replies() == (
        b"\x1b]10;rgb:ffff/ffff/ffff\x07" + b"\x1b]10;rgb:0000/0000/0000\x07"
    )


def test_incomplete_query_past_carry_bound_passes_through() -> None:
    # A never-terminated query longer than the carry bound is not buffered without
    # limit — it flows straight through rather than growing the carry unboundedly.
    r = OscColorResponder()
    payload = b"\x1b]10" + b";?" * 10  # 25 bytes, no terminator
    assert r.process(payload) == payload
    assert r.pending_replies() == b""


def test_osc11_prefix_resolves_to_osc110_passthrough() -> None:
    # ESC]11 is an ambiguous prefix (OSC 11 query vs OSC 110 reset). When the
    # next read reveals OSC 110, the bytes pass through with no reply.
    r = OscColorResponder()
    a = r.process(b"\x1b]11")
    b = r.process(b"0\x07")
    assert a + b == b"\x1b]110\x07"
    assert r.pending_replies() == b""


def test_osc1_icon_title_passes_through() -> None:
    r = OscColorResponder()
    payload = b"\x1b]1;icon name\x07"
    assert r.process(payload) == payload
    assert r.pending_replies() == b""


def test_empty_chunk() -> None:
    r = OscColorResponder()
    assert r.process(b"") == b""
    assert r.pending_replies() == b""
