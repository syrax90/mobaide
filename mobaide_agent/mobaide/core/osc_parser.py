"""OSC 0/1/2 title extractor for the PTY read path.

Claude Code (Ink/React TUI) sets the terminal-tab title via ``ESC ] Ps ;
text BEL`` (or ``ESC \\`` / 8-bit ST). Those bytes already flow through
``TerminalSession._reader_loop`` — this module captures them into a
human-readable string so the mobile app can show a live, friendly session
label instead of the opaque 12-hex ``session_id``.

The extractor is a passive tap: ``feed(bytes)`` observes a chunk and
returns the most recent complete OSC 0/1/2 payload seen, or ``None``.
It NEVER mutates or consumes bytes — the chunk passed to the reader loop
flows downstream unchanged (in contrast to :class:`DaResponder` which
rewrites the stream to strip DA queries).

State machine follows the canonical xterm/DEC ANSI parser:

  GROUND ──ESC(0x1B)─→ ESC_SEEN ──']' (0x5D)─→ OSC_PS
                       │                       │
                       │                       ↓ digits+';' (Ps=0/1/2 → capture)
                       │                       OSC_PAYLOAD
                       │                       │
                       │  ESC(0x1B)            ↓ BEL(0x07) or ST(0x9C)
                       └────────────────────── │ or ESC(0x1B)→ST_PENDING──'\\'(0x5C)→COMMIT
                                               │
                                       (mid-OSC ESC: abort, re-enter ESC_SEEN)

8-bit entry ``0x9D`` is recognized as an OSC introducer. 8-bit ST ``0x9C``
is intentionally NOT treated as a terminator: it collides with valid UTF-8
continuation bytes (e.g. the second byte of ``\xe2\x9c\xa8`` for ✨) and
would chop a real title mid-rune. xterm sidesteps this with its
``c1Print`` / 8-bit-mode resource — modern terminals run UTF-8 by default,
so accept only the 7-bit ST form (``ESC \\``) and BEL.

References:
  * vt100.net DEC ANSI parser: https://vt100.net/emu/dec_ansi_parser
  * xterm ctlseqs (OSC): https://invisible-island.net/xterm/ctlseqs/ctlseqs.html
  * WezTerm escape sequences (BEL/ST equivalence):
    https://wezterm.org/escape-sequences.html
"""

from __future__ import annotations

import enum

ESC = 0x1B
BEL = 0x07
OSC_8BIT = 0x9D
OSC_INTRO = 0x5D  # ']'
ST_FINAL = 0x5C  # '\\' that completes ESC \\

# OSC payloads beyond this are truncated. xterm typically caps around 1024;
# 512 chars is enough for "* Some Long Task Name (3 tools, 12 files)" and
# bounds memory if a malformed stream forgets the terminator.
MAX_PAYLOAD_CHARS = 512

# Every documented OSC ``Ps`` is at most three or four decimal digits
# (xterm goes up to 119). Capping the digit accumulator bounds memory
# against a child that emits an unterminated ``ESC ] 000000…`` and
# never sends ``;``/BEL/ESC — without it the buffer grows indefinitely.
# Past this cap we keep parsing through the terminator but discard.
MAX_PS_DIGITS = 8


class _State(enum.IntEnum):
    GROUND = 0
    ESC_SEEN = 1  # after ESC, awaiting ']' (or other final)
    OSC_PS = 2  # accumulating digit prefix Ps before ';'
    OSC_PAYLOAD = 3  # accumulating payload bytes (only if Ps in {0,1,2})
    OSC_DISCARD = 4  # accumulating payload bytes but discarding (Ps not in {0,1,2})
    OSC_ST_PENDING = 5  # saw ESC inside OSC, awaiting '\\' to complete ST


class OscTitleExtractor:
    """Stateful tap that captures OSC 0/1/2 titles from a PTY byte stream.

    Usage::

        ext = OscTitleExtractor()
        for chunk in pty_reads:
            title = ext.feed(chunk)
            if title is not None:
                publish(title)
            # ... chunk continues downstream unchanged
    """

    def __init__(self) -> None:
        self._state = _State.GROUND
        self._ps_buf = bytearray()
        self._payload: list[int] = []
        self._payload_truncated = False
        # True iff the active OSC's Ps is one of 0/1/2; tracked separately
        # from ``_state`` so commit-from-ST_PENDING knows whether to emit.
        self._capturing = False

    def feed(self, chunk: bytes) -> str | None:
        """Observe ``chunk``; return the most recent complete OSC 0/1/2 title.

        If the chunk contains multiple complete titles, returns the last one
        (intermediate values are dropped — the coalescer upstream applies the
        same last-value-wins rule across chunks).
        Returns ``None`` if no complete title is seen in this chunk.
        """
        # Fast path: when the parser is between sequences and the chunk has
        # no OSC introducer at all, skip the per-byte Python loop.
        # ``b"\x1b]"`` and ``b"\x9d"`` are the only ways an OSC can begin;
        # the C-level ``bytes.__contains__`` is ~200× cheaper than Python
        # iteration. The ``endswith(b"\x1b")`` guard catches the boundary
        # case where the chunk ends with a lone ESC that the next chunk's
        # ``]`` would complete — without it state would never transition
        # to ``ESC_SEEN``. CSI sequences (``\x1b[…``) and other non-OSC
        # escapes are now also skipped, recovering the dominant TUI-
        # repaint case which otherwise pays the Python-loop cost.
        if (
            self._state == _State.GROUND
            and b"\x1b]" not in chunk
            and b"\x9d" not in chunk
            and not chunk.endswith(b"\x1b")
        ):
            return None
        latest: str | None = None
        for byte in chunk:
            commit = self._step(byte)
            if commit is not None:
                latest = commit
        return latest

    def _step(self, b: int) -> str | None:
        """Advance the FSM by one byte; return a committed title if any."""
        s = self._state

        if s == _State.GROUND:
            if b == ESC:
                self._state = _State.ESC_SEEN
            elif b == OSC_8BIT:
                self._begin_osc()
            return None

        if s == _State.ESC_SEEN:
            if b == OSC_INTRO:
                self._begin_osc()
            elif b == ESC:
                pass
            else:
                self._state = _State.GROUND
            return None

        if s == _State.OSC_PS:
            if 0x30 <= b <= 0x39:  # ASCII digit
                if len(self._ps_buf) < MAX_PS_DIGITS:
                    self._ps_buf.append(b)
                else:
                    # Overlong Ps prefix: keep parsing through the terminator
                    # but discard the OSC so memory stays bounded.
                    self._state = _State.OSC_DISCARD
                    self._capturing = False
                return None
            if b == 0x3B:  # ';'
                self._enter_payload()
                return None
            return self._handle_terminator_or_abort(b)

        if s == _State.OSC_PAYLOAD or s == _State.OSC_DISCARD:
            return self._handle_terminator_or_abort(b)

        if s == _State.OSC_ST_PENDING:
            if b == ST_FINAL:
                return self._commit()
            if b == ESC:
                self._reset_osc_buffers()
                self._state = _State.ESC_SEEN
                return None
            self._reset_osc_buffers()
            self._state = _State.GROUND
            return None

        return None

    def _begin_osc(self) -> None:
        self._state = _State.OSC_PS
        self._ps_buf.clear()
        self._payload.clear()
        self._payload_truncated = False

    def _enter_payload(self) -> None:
        ps_str = self._ps_buf.decode("ascii", errors="replace") or "0"
        try:
            ps = int(ps_str)
        except ValueError:
            ps = -1
        if ps in (0, 1, 2):
            self._state = _State.OSC_PAYLOAD
            self._capturing = True
        else:
            self._state = _State.OSC_DISCARD
            self._capturing = False

    def _handle_terminator_or_abort(self, b: int) -> str | None:
        if b == BEL:
            return self._commit()
        if b == ESC:
            self._state = _State.OSC_ST_PENDING
            return None
        if self._state == _State.OSC_PAYLOAD:
            if len(self._payload) < MAX_PAYLOAD_CHARS:
                self._payload.append(b)
            else:
                self._payload_truncated = True
        return None

    def _commit(self) -> str | None:
        title: str | None = None
        if self._capturing:
            title = bytes(self._payload).decode("utf-8", errors="replace")
        self._reset_osc_buffers()
        self._state = _State.GROUND
        return title

    def _reset_osc_buffers(self) -> None:
        self._ps_buf.clear()
        self._payload.clear()
        self._payload_truncated = False
        self._capturing = False
