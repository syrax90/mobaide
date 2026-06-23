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
  * scans PTY output for OSC 10/11/12 color controls,
  * for each ``?`` (query) spec, strips it from the byte stream (so the client
    emulator never sees a query and never loops a reply back as input) and queues
    a synthetic reply to the PTY master, so the child gets a color report
    immediately regardless of client-attach state,
  * for a mixed query+set control (``ESC]10;?;rgb:…``), re-emits each value spec
    re-targeted to its absolute color code, so the set still applies while the
    query is removed,
  * passes a pure color *set* through unchanged.

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

# A complete OSC dynamic-color control: ESC ] (10|11|12) (; spec)+ (BEL | ST),
# where each spec is ``?`` (query that color) or a value (set it). xterm/Termux
# walk the specs left-to-right from the base slot (10=fg, 11=bg, 12=cursor),
# advancing one slot per spec and stopping after the cursor; a ``?`` makes the
# emulator reply ``ESC]<base>;rgb:…<term>`` (Ps is always the base, the color is
# the slot's current value), a value spec sets that slot. Matching the whole
# control — not just the all-``?`` form — lets us answer queries, re-target value
# specs, and keep mixed query+set controls (``ESC]10;?;rgb:…``) from leaking on
# replay. ratatui sends separate single queries in practice; the broader forms
# are defensive.
_CONTROL_RE = re.compile(rb"\x1b\](10|11|12)((?:;[^;\x07\x1b]*)+)(\x07|\x1b\\)")

# Longest *incomplete* control we hold across a read boundary so a split control
# reassembles. A full three-slot control with value specs (e.g.
# ``ESC]10;rgb:…/…/…;rgb:…/…/…;rgb:…/…/…ST``) is ~60 bytes; 64 covers it while
# bounding the carry so a control that never terminates cannot grow it forever.
_MAX_CARRY = 64

# A tail that is the *start* of a color control but not yet complete — the carry
# rule holds such a tail across a read boundary so a split control reassembles.
# It matches the introducer byte-by-byte (``ESC``, ``ESC]``, ``ESC]1``,
# ``ESC]1[012]``) then any run of ``;<spec>`` groups with the optional lone ``ESC``
# of a half-written ST. A *complete* control is matched by ``_CONTROL_RE`` first,
# so this only classifies genuinely incomplete tails; anything that cannot
# continue into a color control — a title (``ESC]0``), OSC 4, the ``ESC]110``
# reset — fails to full-match and falls through to pass-through.
_PARTIAL_CONTROL_RE = re.compile(
    rb"\x1b(?:\](?:1(?:[012](?:;[^;\x07\x1b]*)*\x1b?)?)?)?"
)


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
        """Strip OSC color *queries* from ``chunk`` and queue replies for them,
        while preserving color *sets* (re-targeting them inside mixed controls).

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

            m = _CONTROL_RE.match(data, esc)
            if m:
                specs = m.group(2).split(b";")[1:]
                if b"?" in specs:
                    # A query or mixed query+set control. Answer each ``?`` slot
                    # from the default palette (so the child gets a report and the
                    # query never reaches the client to be looped back as input),
                    # and re-emit each value slot re-targeted to its absolute color
                    # code so the set still takes effect. Mirrors TerminalEmulator's
                    # left-to-right slot walk, stopping after the cursor (code > 12);
                    # the reply's Ps is always the base, matching the emulator.
                    base_bytes = m.group(1)
                    base = int(base_bytes)
                    term = m.group(3)
                    for offset, spec in enumerate(specs):
                        code = base + offset
                        if code > 12:
                            break
                        if spec == b"?":
                            self._pending += (
                                b"\x1b]" + base_bytes + b";" + _COLOR_BY_CODE[code] + term
                            )
                        elif spec:
                            out += b"\x1b]" + b"%d" % code + b";" + spec + term
                    i = m.end()
                    continue
                # A pure color *set* (no ``?``): legitimate output, pass verbatim.
                out += data[esc : m.end()]
                i = m.end()
                continue

            # Not a complete control at ``esc``. If the tail is a still-completable
            # partial control within the carry bound, it is split across reads —
            # carry it and resume next chunk. Otherwise it is other OSC (a title,
            # OSC 4/52): emit the introducer and advance so the rest passes through.
            tail = data[esc:]
            if len(tail) <= _MAX_CARRY and _PARTIAL_CONTROL_RE.fullmatch(tail):
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
