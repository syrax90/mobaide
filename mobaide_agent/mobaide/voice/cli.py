"""``mobaide-agent voice status | install | remove | benchmark``.

For headless machines and support. Like the other CLI verbs it works on the
files under the install prefix, never through the running agent: it uses the
same voice root as the installed agent (``<prefix>/voice``) and the same
cross-process lock.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from array import array
from pathlib import Path

from mobaide.agent._env_writer import env_path_for_prefix, parse_env_file
from mobaide.agent.config import _TRUTHY
from mobaide.agent.install_paths import _is_writable_dir, default_prefix, state_prefix

from .dictation import MAX_CHUNK_BYTES, SAMPLE_RATE, STATE_DONE
from .errors import VoiceError
from .host import RECOMMENDATION_UNSUPPORTED
from .locking import FileLock
from .models import (
    VoiceDictationAudioRequest,
    VoiceDictationFinishRequest,
    VoiceStatusResponse,
)
from .service import STATE_READY, STATE_UNSUPPORTED, VoiceService

_BENCHMARK_PAUSE_SECONDS = 0.6


def _agent_setting(args: argparse.Namespace, key: str) -> str:
    """``key`` as the agent resolves it: its process environment first, then
    the ``.env`` it loads (``""`` when neither sets it)."""
    if key in os.environ:
        return os.environ[key]
    if args.prefix:
        env_file: Path | None = env_path_for_prefix(Path(args.prefix))
    else:
        from mobaide.agent.app import _resolve_env_file

        env_file = _resolve_env_file()
    if env_file is None or not env_file.is_file():
        return ""
    try:
        return parse_env_file(env_file).get(key, "")
    except OSError:
        return ""


def _fake(args: argparse.Namespace) -> bool:
    return _agent_setting(args, "MOBAIDE_VOICE_FAKE").strip().lower() in _TRUTHY


def _voice_root(args: argparse.Namespace, *, write: bool) -> Path | None:
    """The voice dir the agent on this machine uses, or ``None`` (with a
    message) when this process could not safely write there."""
    if args.voice_dir:
        return Path(args.voice_dir)
    configured = _agent_setting(args, "MOBAIDE_VOICE_DIR").strip()
    if configured and Path(configured).is_absolute():
        return Path(configured)
    # A relative MOBAIDE_VOICE_DIR is anchored under the state prefix, as the
    # agent's config anchors it.
    name = configured or ("voice-fake" if _fake(args) else "voice")
    prefix = Path(args.prefix) if args.prefix else default_prefix()
    installed = (prefix / "current_version").exists()
    if not (args.prefix or os.environ.get("MOBAIDE_AGENT_PREFIX") or installed):
        return state_prefix() / name
    if write:
        if sys.platform != "win32" and os.geteuid() == 0:
            try:
                owner = prefix.stat().st_uid
            except OSError:
                owner = 0
            if owner != 0:
                print(
                    f"Run this as the user that owns {prefix} (the agent's user), not as "
                    "root, so the agent can still read and update the files.",
                    file=sys.stderr,
                )
                return None
        if not _is_writable_dir(prefix):
            hint = "an elevated (Administrator) prompt" if sys.platform == "win32" else "the agent's user"
            print(f"{prefix} is not writable from here; run this from {hint}.", file=sys.stderr)
            return None
    return prefix / name


def _gb(value: int | None) -> str:
    return "unknown" if value is None else f"{value / 1_000_000_000:.1f} GB"


def _print_status(status: VoiceStatusResponse, root: Path) -> None:
    print(f"Voice input: {status.state}")
    model = status.model
    print(f"Model: {model.name} ({len(model.languages)} languages: {', '.join(model.languages)})")
    print(
        f"Download: {_gb(model.download_bytes)} · Disk: {_gb(model.disk_bytes)} · "
        f"Memory while dictating: about {_gb(model.ram_bytes)}"
    )
    if status.progress is not None and status.progress.total_bytes:
        pct = 100 * status.progress.done_bytes // status.progress.total_bytes
        print(f"Progress: {pct}% of {_gb(status.progress.total_bytes)}")
    if status.speed_factor is not None:
        print(f"Speed: {status.speed_factor:g}x faster than real time (install self-test)")
    host = status.host
    print(
        f"This computer: {_gb(host.ram_total_bytes)} RAM, {host.cpu_count or '?'} CPUs, "
        f"{_gb(host.disk_free_bytes)} free disk — {host.recommendation}"
    )
    for reason in host.reasons:
        print(f"  ! {reason.message}")
    if status.error is not None:
        print(f"Error ({status.error.code}): {status.error.message}")
    print(f"Location: {root}")


def _progress_printer(interactive: bool):
    last = {"line": ""}

    def publish(_event: str, payload: dict) -> None:
        progress = payload.get("progress") or {}
        total = progress.get("total_bytes") or 0
        done = progress.get("done_bytes") or 0
        pct = f" {100 * done // total}% of {_gb(total)}" if total else ""
        line = f"{payload.get('state')}{pct}"
        if line == last["line"]:
            return
        last["line"] = line
        if interactive:
            print(f"\r{line:<40}", end="", flush=True)
        else:
            print(line, flush=True)

    return publish


async def _install(service: VoiceService, root: Path) -> int:
    status = service.status()
    if status.state == STATE_UNSUPPORTED or status.host.recommendation == RECOMMENDATION_UNSUPPORTED:
        _print_status(status, root)
        return 1
    if status.state == STATE_READY:
        print("Voice input is already installed.")
        return 0
    if FileLock(service.paths.lock_file).is_held_elsewhere():
        print(
            "Another Mobaide process (the running agent?) is installing voice input; "
            "check progress with `mobaide-agent voice status`.",
            file=sys.stderr,
        )
        return 1
    print(
        f"Installing {status.model.name}: {_gb(status.model.download_bytes)} download, "
        f"about {_gb(status.model.ram_bytes)} of memory while dictating."
    )
    for reason in status.host.reasons:
        print(f"  ! {reason.message}")
    try:
        await service.install()
        final = await service.wait_for_install()
    except asyncio.CancelledError:
        service.mark_interrupted()
        raise
    except VoiceError as exc:
        print(f"\n{exc.message}", file=sys.stderr)
        return 1
    finally:
        await service.shutdown()
    print()
    _print_status(final, root)
    return 0 if final.state == STATE_READY else 1


async def _remove(service: VoiceService) -> int:
    try:
        await service.remove()
    except VoiceError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        await service.shutdown()
    print("Voice input is off and its files are deleted.")
    return 0


def _to_16k(pcm: bytes, rate: int) -> bytes:
    if rate == SAMPLE_RATE:
        return pcm
    src = array("h")
    src.frombytes(pcm)
    if sys.byteorder == "big":
        src.byteswap()
    ratio = rate / SAMPLE_RATE
    out = array("h")
    for i in range(int(len(src) / ratio)):
        pos = i * ratio
        lo = int(pos)
        hi = min(lo + 1, len(src) - 1)
        out.append(int(src[lo] + (src[hi] - src[lo]) * (pos - lo)))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


async def _benchmark(service: VoiceService, lengths: list[float]) -> int:
    status = service.status()
    if status.state != STATE_READY:
        print("Voice input is not installed; run `mobaide-agent voice install` first.", file=sys.stderr)
        return 1
    pcm, rate = await asyncio.to_thread(service.selftest_clip)
    unit = _to_16k(pcm, rate) + b"\x00\x00" * int(_BENCHMARK_PAUSE_SECONDS * SAMPLE_RATE)
    print(f"Self-test speed: {status.speed_factor}x real time")
    print("seconds  finish_ms  words")
    try:
        for seconds in lengths:
            size = int(seconds * SAMPLE_RATE) * 2
            audio = (unit * (size // len(unit) + 1))[:size]
            created = await service.create_dictation()
            seq = -1
            for seq, offset in enumerate(range(0, len(audio), MAX_CHUNK_BYTES)):
                chunk = audio[offset : offset + MAX_CHUNK_BYTES]
                await service.append_audio(
                    created.dictation_id,
                    VoiceDictationAudioRequest(seq=seq, pcm16_b64=base64.b64encode(chunk).decode()),
                )
                # Real-time pacing, so segments are transcribed while "speaking"
                # exactly as they are for a live microphone.
                await asyncio.sleep(len(chunk) / 2 / SAMPLE_RATE)
            started = time.monotonic()
            request = VoiceDictationFinishRequest(last_seq=seq)
            result = await service.finish_dictation(created.dictation_id, request)
            while result.state != STATE_DONE:
                result = await service.finish_dictation(created.dictation_id, request)
            finish_ms = int((time.monotonic() - started) * 1000)
            words = len((result.text or "").split())
            print(f"{seconds:7g}  {finish_ms:9d}  {words:5d}")
    except VoiceError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    finally:
        await service.shutdown()
    if sys.platform != "win32":
        import resource

        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        peak_bytes = peak if sys.platform == "darwin" else peak * 1024
        print(f"Helper peak memory: {_gb(peak_bytes)}")
    return 0


def run(args: argparse.Namespace) -> int:
    command = args.voice_command
    root = _voice_root(args, write=command in ("install", "remove", "benchmark"))
    if root is None:
        return 2
    interactive = sys.stdout.isatty()
    service = VoiceService(
        voice_dir=root,
        fake=_fake(args),
        publish=_progress_printer(interactive) if command == "install" else None,
    )
    if command == "status":
        status = service.status()
        if args.json:
            print(json.dumps(status.model_dump(mode="json"), indent=2))
        else:
            _print_status(status, root)
        return 0
    if command == "remove":
        if not root.exists():
            print("Voice input is not installed.")
            return 0
        if not args.yes:
            if not sys.stdin.isatty():
                print("Refusing to delete without --yes.", file=sys.stderr)
                return 2
            answer = input(f"Turn voice input off and delete {root}? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                return 1
        return asyncio.run(_remove(service))
    if command == "install":
        try:
            return asyncio.run(_install(service, root))
        except KeyboardInterrupt:
            print("\nInterrupted; run `mobaide-agent voice install` again to resume.")
            return 130
    try:
        lengths = [float(x) for x in args.seconds.split(",") if x.strip()]
    except ValueError:
        print("--seconds takes comma-separated numbers.", file=sys.stderr)
        return 2
    return asyncio.run(_benchmark(service, lengths))
