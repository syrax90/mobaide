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

# The two legal OSC string terminators: BEL or ST (ESC \).
_BEL = b"\x07"
_ST = b"\x1b\\"

# Color reported per OSC code, matching Termux's DEFAULT_COLORSCHEME (foreground
# and cursor white, background black). The 16-bit-per-channel ``rgb:`` form is
# what xterm and the Termux emulator emit (an 8-bit 0xff scales to 0xffff). OSC
# 12 (cursor) uses the shipped default; a user-applied *light* theme could flip
# the emulator's real cursor reply to black, but Codex probes only 10/11, so the
# divergence is accepted.
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

# Every full *single*-query target, used by the carry rule: a trailing fragment
# is carried across reads only while it is a strict prefix of one of these, so a
# split query reassembles, but long titles and other OSC are never over-buffered.
_TARGETS = tuple(
    b"\x1b]" + code + b";?" + term
    for code in (b"10", b"11", b"12")
    for term in (_BEL, _ST)
)


def _is_strict_prefix_of_target(tail: bytes) -> bool:
    """True if ``tail`` is a non-empty strict prefix of some single-query target.

    Strictness (``len(tail) < len(t)``) bounds the carry to ~8 bytes and ensures
    a tail that already equals a full query was matched by ``_QUERY_RE`` instead.
    """
    return any(len(tail) < len(t) and t.startswith(tail) for t in _TARGETS)


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
            # strict prefix of a target, the query is split across reads — carry
            # it and resume next chunk. Otherwise it is some other OSC (a title,
            # OSC 4/52, or a color *set* like ESC]11;rgb:…): emit the introducer
            # and advance so the rest passes through untouched.
            tail = data[esc:]
            if _is_strict_prefix_of_target(tail):
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
