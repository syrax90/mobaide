"""OSC dynamic-color (OSC 10/11/12) query interceptor for the PTY read path.

Codex (a ratatui/crossterm Rust TUI) emits OSC 10/11 *color queries*
(``ESC ] 10 ; ? ST`` / ``ESC ] 11 ; ? ST``) at startup to detect the terminal's
foreground/background colors (light vs dark theme). Nothing on the agent side
answered them, so the query bytes sat in the session ring buffer. On client
(re)attach the ring is replayed verbatim; the Android Termux emulator parses the
replayed query and writes its own color *reply* back up the input channel
(``mSession.write`` -> ``'i'`` frame), which lands in the CLI's input field as
literal text (``]10;rgb:ffff/ffff/ffff\\]11;rgb:0000/0000/0000\\``). Because the
query is replayed on every reconnect, the leak doubles each time.

This is the OSC analogue of :mod:`da_responder`. The filter:
  * scans PTY output for OSC 10/11/12 color queries (spec == ``?``),
  * strips them from the byte stream (so the client emulator never sees a query
    and so never generates a reply that loops back as input),
  * queues a synthetic reply to be written back to the PTY master so the child
    still gets a color report immediately, regardless of client-attach state.

Reply colors and the echoed BEL/ST terminator match Termux's
``TerminalEmulator.doOscSetTextParameters`` and the shipped
``TerminalColorScheme`` defaults (foreground/cursor ``ffff/ffff/ffff``,
background ``0000/0000/0000``) so a live client and the agent stay byte-consistent.

References:
  * xterm ctlseqs (OSC 10-12 dynamic colors):
    https://invisible-island.net/xterm/ctlseqs/ctlseqs.html
"""

from __future__ import annotations

import re

# Color reported per OSC code, matching Termux's DEFAULT_COLORSCHEME (foreground
# and cursor white, background black). The 16-bit-per-channel ``rgb:`` form is
# what xterm and the Termux emulator emit (an 8-bit 0xff scales to 0xffff). These
# are fixed defaults: the responder answers every query from the shipped palette
# rather than tracking runtime color *set*/reset (``OSC 10/11/12;rgb``, ``OSC
# 110-112``) state. That is correct for the case that matters — a child probing
# the ambient theme at startup, before it changes anything — and keeps a reply
# available even when no client is attached. A child that sets a color then
# re-queries it (or OSC 12 under a user-applied *light* theme) gets the default
# rather than the live value; that divergence is accepted, as no CLI in use does
# this and Codex probes only 10/11.
_COLOR_BY_CODE = {
    10: b"rgb:ffff/ffff/ffff",  # foreground
    11: b"rgb:0000/0000/0000",  # background
    12: b"rgb:ffff/ffff/ffff",  # cursor
}

# A complete color query: ESC ] (10|11|12) (;?)+ (BEL | ST). The repeated ``;?``
# group captures xterm's compound form (e.g. ``ESC]10;?;?ST`` asks fg then bg);
# ratatui sends two separate single queries in practice, but matching the
# compound form keeps it from slipping through to the client.
_QUERY_RE = re.compile(rb"\x1b\](10|11|12)((?:;\?)+)(\x07|\x1b\\)")

# Longest *incomplete* color query we hold across a read boundary. A query
# reports at most three colors (fg, bg, cursor — specs past code 12 are ignored),
# i.e. ``ESC]10;?;?;?ST`` = 13 bytes; 16 leaves headroom while bounding the carry
# so a query that never terminates cannot grow it without limit.
_MAX_CARRY = 16

# A tail that is the *start* of a (single or compound) color query but is not yet
# complete — the carry rule holds such a tail across a read boundary so a split
# query reassembles. It matches the introducer byte-by-byte (``ESC``, ``ESC]``,
# ``ESC]1``, ``ESC]1[012]``) then any run of ``;?`` units with an optional
# dangling ``;`` or the lone ``ESC`` of a half-written ST terminator. A *complete*
# query is matched by ``_QUERY_RE`` first, so this only classifies genuinely
# incomplete tails; anything that cannot continue into a color query — a title
# (``ESC]0``), OSC 4, the ``ESC]110`` reset, or a color *set* (``ESC]10;rgb``) —
# fails to full-match and falls through to pass-through.
_PARTIAL_QUERY_RE = re.compile(rb"\x1b(?:\](?:1(?:[012](?:;\??)*\x1b?)?)?)?")


class OscColorResponder:
    """Stateful filter that intercepts OSC 10/11/12 color queries on a PTY stream.

    Usage mirrors :class:`da_responder.DaResponder`::

        resp = OscColorResponder()
        for chunk in pty_reads:
            sanitized = resp.process(chunk)
            replies = resp.pending_replies()
            if replies:
                pty.write(replies)
            ring.append(sanitized)
    """

    def __init__(self) -> None:
        self._carry = bytearray()
        self._pending = bytearray()

    def process(self, chunk: bytes) -> bytes:
        """Strip OSC color queries from ``chunk`` and queue replies for them.

        Returns the sanitized chunk. Call :meth:`pending_replies` afterward to
        retrieve any reply bytes to write back to the PTY master. Sequences split
        across chunk boundaries are reassembled via a bounded internal carry.
        """
        if not chunk and not self._carry:
            return b""

        data = bytes(self._carry) + chunk
        self._carry.clear()

        out = bytearray()
        i = 0
        n = len(data)
        while i < n:
            esc = data.find(b"\x1b]", i)
            if esc < 0:
                # No OSC introducer left. Carry a lone trailing ESC — the next
                # chunk may begin with ']' and complete a query we'd otherwise
                # miss (mirrors DaResponder's lone-ESC carry).
                if data.endswith(b"\x1b"):
                    out += data[i:-1]
                    self._carry.append(0x1B)
                else:
                    out += data[i:]
                break

            # Pass through everything before the OSC introducer.
            out += data[i:esc]

            m = _QUERY_RE.match(data, esc)
            if m:
                code_bytes = m.group(1)
                term = m.group(3)
                base = int(code_bytes)
                # One reply per ``?`` spec, with the reported color advancing
                # (fg -> bg -> cursor) while the reply's Ps echoes the request's
                # original code — exactly what TerminalEmulator does. Stop once
                # the advancing slot passes the cursor (code > 12).
                for j in range(m.group(2).count(b"?")):
                    code = base + j
                    if code > 12:
                        break
                    self._pending += (
                        b"\x1b]" + code_bytes + b";" + _COLOR_BY_CODE[code] + term
                    )
                i = m.end()
                continue

            # Not a complete color query at ``esc``. If the remaining tail is a
            # viable (still-completable) color-query prefix within the carry
            # bound, the query is split across reads — carry it and resume next
            # chunk. Otherwise it is some other OSC (a title, OSC 4/52, or a color
            # *set* like ESC]11;rgb:…): emit the introducer and advance so the
            # rest passes through untouched.
            tail = data[esc:]
            if len(tail) <= _MAX_CARRY and _PARTIAL_QUERY_RE.fullmatch(tail):
                self._carry.extend(tail)
                return bytes(out)

            out += data[esc : esc + 2]
            i = esc + 2

        return bytes(out)

    def pending_replies(self) -> bytes:
        """Return any queued reply bytes and clear the queue."""
        if not self._pending:
            return b""
        replies = bytes(self._pending)
        self._pending.clear()
        return replies
