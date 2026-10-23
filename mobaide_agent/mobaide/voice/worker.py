"""STT worker: ``python -m mobaide.voice.worker``.

Started lazily by :class:`mobaide.voice.helper.SttHelper` and spoken to only
through framed messages on its stdin/stdout (no network port):

* ``load {id, engine, model_dir, runtime_dir, files, num_threads}`` →
  ``{id, ok, load_ms, pid}`` or ``{id, ok: false, error: {code, message}}``
* ``transcribe {id, sample_rate}`` + PCM16 payload →
  ``{id, ok, text, decode_ms, peak_rss_bytes}``
* ``shutdown`` → exit

End of stdin means the agent is gone, so the worker exits and can never
outlive it. Audio and text are never logged; stderr carries only failures.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path
from typing import IO, Any

from .engines import ENGINES
from .ipc import encode_frame, read_frame

_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def _lower_priority() -> None:
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), _BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(10)
    except (OSError, AttributeError):
        pass


def _peak_rss_bytes() -> int | None:
    if sys.platform == "win32":
        return None
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _error(code: str, exc: BaseException) -> dict[str, Any]:
    return {"code": code, "message": f"{type(exc).__name__}: {exc}"[:500]}


def _send(out: IO[bytes], header: dict[str, Any]) -> None:
    out.write(encode_frame(header))
    out.flush()


def serve(inp: IO[bytes], out: IO[bytes]) -> int:
    engine = None
    while True:
        frame = read_frame(inp)
        if frame is None:
            return 0
        header, payload = frame
        op = header.get("op")
        if op == "shutdown":
            return 0
        if op == "load":
            started = time.monotonic()
            try:
                runtime_dir = header.get("runtime_dir")
                if runtime_dir and runtime_dir not in sys.path:
                    sys.path.insert(0, runtime_dir)
                engine_cls = ENGINES[header["engine"]]
                engine = engine_cls(
                    Path(header["model_dir"]),
                    dict(header.get("files") or {}),
                    int(header.get("num_threads") or 2),
                )
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                engine = None
                _send(out, {"id": header.get("id"), "ok": False, "error": _error("load_failed", exc)})
                continue
            _send(
                out,
                {
                    "id": header.get("id"),
                    "ok": True,
                    "load_ms": int((time.monotonic() - started) * 1000),
                    "pid": os.getpid(),
                    "peak_rss_bytes": _peak_rss_bytes(),
                },
            )
            continue
        if op == "transcribe":
            request_id = header.get("id")
            if engine is None:
                _send(out, {"id": request_id, "ok": False, "error": {"code": "not_loaded", "message": "No model loaded."}})
                continue
            started = time.monotonic()
            try:
                text = engine.transcribe(payload, int(header.get("sample_rate") or 16000))
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                _send(out, {"id": request_id, "ok": False, "error": _error("transcribe_failed", exc)})
                continue
            _send(
                out,
                {
                    "id": request_id,
                    "ok": True,
                    "text": text,
                    "decode_ms": int((time.monotonic() - started) * 1000),
                    "peak_rss_bytes": _peak_rss_bytes(),
                },
            )
            continue
        _send(out, {"id": header.get("id"), "ok": False, "error": {"code": "bad_request", "message": f"unknown op {op!r}"}})


def main() -> int:
    # Frames go out on a private copy of fd 1; fd 1 itself is pointed at
    # stderr so a native library printing to stdout cannot corrupt them.
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    inp = os.fdopen(os.dup(0), "rb", buffering=0)
    _lower_priority()
    try:
        return serve(inp, out)
    except (BrokenPipeError, ConnectionResetError):
        return 0


if __name__ == "__main__":
    sys.exit(main())
