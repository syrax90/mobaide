"""Speech-to-text engines, imported only inside the worker process.

The agent process never imports sherpa-onnx: an ONNX Runtime session keeps
hundreds of MB after it is released, so the model lives in a worker that
exits when idle. An engine turns one segment of 16-bit mono PCM into text.
"""
from __future__ import annotations

import sys
from array import array
from pathlib import Path
from typing import Protocol


class SttEngine(Protocol):
    def transcribe(self, pcm16: bytes, sample_rate: int) -> str: ...


def _pcm16_to_floats(pcm16: bytes) -> array:
    samples = array("h")
    samples.frombytes(pcm16[: len(pcm16) - len(pcm16) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return array("f", (s / 32768.0 for s in samples))


class SherpaTransducerEngine:
    """NeMo transducer models (Parakeet TDT) through sherpa-onnx, CPU only."""

    def __init__(self, model_dir: Path, files: dict[str, str], num_threads: int) -> None:
        import sherpa_onnx

        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model_dir / files["encoder"]),
            decoder=str(model_dir / files["decoder"]),
            joiner=str(model_dir / files["joiner"]),
            tokens=str(model_dir / files["tokens"]),
            num_threads=num_threads,
            sample_rate=16000,
            feature_dim=80,
            decoding_method="greedy_search",
            model_type="nemo_transducer",
            provider="cpu",
        )

    def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, _pcm16_to_floats(pcm16))
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()


class FakeEngine:
    """Deterministic stand-in for tests and client work without the model."""

    def __init__(self, model_dir: Path, files: dict[str, str], num_threads: int) -> None:
        del model_dir, files, num_threads

    def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        duration_ms = (len(pcm16) // 2) * 1000 // max(sample_rate, 1)
        return f"fake transcript {duration_ms} ms"


ENGINES: dict[str, type] = {
    "sherpa_transducer": SherpaTransducerEngine,
    "fake": FakeEngine,
}
