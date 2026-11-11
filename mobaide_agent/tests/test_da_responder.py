"""Tests for the PTY-side Device Attributes interceptor (CLA-15 Part B)."""

from __future__ import annotations

from mobaide.core.da_responder import (
    DA1_REPLY,
    DA2_REPLY,
    DA3_REPLY,
    DaResponder,
)


def test_da1_stripped_and_reply_queued() -> None:
    r = DaResponder()
    out = r.process(b"hello\x1b[cworld")
    assert out == b"helloworld"
    assert r.pending_replies() == DA1_REPLY
    # Replies are drained on read.
    assert r.pending_replies() == b""


def test_da1_with_question_mark_prefix() -> None:
    r = DaResponder()
    out = r.process(b"\x1b[?c")
    assert out == b""
    assert r.pending_replies() == DA1_REPLY




def test_da2() -> None:
    r = DaResponder()
    out = r.process(b"\x1b[>c")
    assert out == b""
    assert r.pending_replies() == DA2_REPLY


def test_da3() -> None:
    r = DaResponder()
    out = r.process(b"\x1b[=c")
    assert out == b""
    assert r.pending_replies() == DA3_REPLY


def test_split_at_esc_boundary() -> None:
    r = DaResponder()
    a = r.process(b"foo\x1b")
    b = r.process(b"[cbar")
    assert a + b == b"foobar"
    assert r.pending_replies() == DA1_REPLY




def test_split_with_param_across_chunks() -> None:
    r = DaResponder()
    a = r.process(b"\x1b[?")
    b = r.process(b"0c done")
    assert a + b == b" done"
    assert r.pending_replies() == DA1_REPLY


def test_non_da_csi_passes_through() -> None:
    r = DaResponder()
    out = r.process(b"\x1b[31mred\x1b[0m")
    assert out == b"\x1b[31mred\x1b[0m"
    assert r.pending_replies() == b""




def test_long_sgr_not_carried_forever() -> None:
    # A long SGR sequence (24-bit colour) must not be over-buffered just
    # because it starts with ESC[.
    r = DaResponder()
    out = r.process(b"\x1b[38;2;255;0;0mred")
    assert out == b"\x1b[38;2;255;0;0mred"
    assert r.pending_replies() == b""




def test_multiple_da_in_one_chunk() -> None:
    r = DaResponder()
    out = r.process(b"\x1b[c\x1b[c")
    assert out == b""
    assert r.pending_replies() == DA1_REPLY + DA1_REPLY
