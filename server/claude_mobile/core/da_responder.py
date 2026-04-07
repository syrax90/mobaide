"""Device Attributes (DA) query interceptor for the PTY read path.

The ``claude`` CLI (via Ink/ansi-escapes) emits a CSI Primary Device
Attributes query (``ESC[c``) at startup. Nothing on the server side answers
it, so the bytes sit in the session ring buffer and surface as user-visible
text on client attach (the literal ``?64;1;2;6;9;15;18;21;22c`` would
otherwise leak into the input field).

This module provides a small stateful filter that:
  * scans PTY output for DA1 / DA2 / DA3 queries,
  * strips them from the byte stream,
  * queues a synthetic reply to be written back to the PTY master.

Reply payloads match Termux's ``TerminalEmulator.doDeviceAttributes()`` so
the client emulator stays consistent.

References:
  * xterm ctlseqs: https://invisible-island.net/xterm/ctlseqs/ctlseqs.html
  * VT510 DA1: https://vt100.net/docs/vt510-rm/DA1.html
"""

from __future__ import annotations

import re

# DA1 — Primary Device Attributes (xterm advertising what Termux reports).
DA1_REPLY = b"\x1b[?64;1;2;6;9;15;18;21;22c"
# DA2 — Secondary Device Attributes (xterm: VT420, version 276, ROM 0).
DA2_REPLY = b"\x1b[>0;276;0c"
# DA3 — Tertiary Device Attributes (DECRPTUI, eight zero hex digits).
DA3_REPLY = b"\x1bP!|00000000\x1b\\"

# CSI form for DA queries: ESC [ (?|>|=)? digits? c
_DA_RE = re.compile(rb"\x1b\[([?>=]?)(\d*)c")

# Maximum bytes of partial-CSI tail we will carry across chunks. The longest
# DA query we recognise is 5 bytes (e.g. ``ESC [ ? 0 c``); 8 leaves headroom.
_MAX_CARRY = 8

# CSI final byte range (per ECMA-48): 0x40-0x7E.
_FINAL_LO = 0x40
_FINAL_HI = 0x7E


def _csi_has_final(buf: bytes) -> bool:
    """True if ``buf`` (which starts with ESC[) already contains a CSI
    final byte. Used to decide whether a tail is "complete enough" to
    process now or should be carried into the next chunk."""
    for ch in buf[2:]:
        if _FINAL_LO <= ch <= _FINAL_HI:
            return True
    return False


class DaResponder:
    """Stateful filter that intercepts DA queries on a PTY byte stream.

    Usage::

        resp = DaResponder()
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
        """Strip DA queries from ``chunk`` and queue replies for them.

        Returns the sanitized chunk. Call :meth:`pending_replies` after
        each ``process`` to retrieve any reply bytes that should be
        written back to the PTY master.
        """
        if not chunk and not self._carry:
            return b""

        data = bytes(self._carry) + chunk
        self._carry.clear()

        out = bytearray()
        i = 0
        n = len(data)
        while i < n:
            esc = data.find(b"\x1b[", i)
            if esc < 0:
                # No CSI introducer found. If the buffer ends in a lone
                # ESC byte, carry it — the next chunk may begin with ``[``
                # and complete a DA query we'd otherwise miss.
                if data.endswith(b"\x1b"):
                    out += data[i:-1]
                    self._carry.append(0x1B)
                else:
                    out += data[i:]
                break

            # Pass through everything before the ESC[ we found.
            out += data[i:esc]

            tail = data[esc:]
            # If the CSI hasn't finished yet and the tail is short, carry
            # it into the next chunk so we don't split a DA query in two.
            if not _csi_has_final(tail) and len(tail) < _MAX_CARRY:
                self._carry.extend(tail)
                return bytes(out)

            m = _DA_RE.match(data, esc)
            if m:
                prefix = m.group(1)
                if prefix in (b"", b"?"):
                    self._pending += DA1_REPLY
                elif prefix == b">":
                    self._pending += DA2_REPLY
                elif prefix == b"=":
                    self._pending += DA3_REPLY
                i = m.end()
                continue

            # Non-DA CSI (e.g. ``\x1b[31m``): emit the ESC[ introducer
            # verbatim and resume scanning at esc+2. The remaining bytes
            # of the CSI (params + final byte) are not clipped — they fall
            # into the next iteration's ``data[i:esc]`` slice and pass
            # through unchanged on the way to the next ESC[ (or end).
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
