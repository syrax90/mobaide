"""Pins the pure pieces of ``deploy/prod/upload_voice_model.py``: the
catalog → bucket-key mapping, the credentials file, the size and digest
checks on a streamed download, and ``--source-dir`` validation. No network
and no R2."""
from __future__ import annotations

import hashlib
import io
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "deploy" / "prod"))

import upload_voice_model as uvm  # noqa: E402
from release_common import ReleaseError  # noqa: E402


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _serve(monkeypatch, body: bytes) -> list[str]:
    seen: list[str] = []

    def urlopen(req, timeout, context):
        seen.append(req.full_url)
        return _Response(body)

    monkeypatch.setattr(uvm.urllib.request, "urlopen", urlopen)
    return seen


def _job(body: bytes, sources: tuple[str, ...] = ("https://src.test/f.bin",)) -> uvm.MirrorJob:
    sha = hashlib.sha256(body).hexdigest()
    file = uvm.catalog.CatalogFile(name="f.bin", size=len(body), sha256=sha, urls=sources)
    return uvm.MirrorJob("m", file, f"https://dl.test/voice/m/{sha}/f.bin", f"voice/m/{sha}/f.bin", sources)


class TestMirrorJobs:
    def test_every_catalog_file_maps_to_a_content_addressed_key(self):
        jobs = uvm.mirror_jobs()
        files = [f for spec in uvm.catalog.MODELS.values() for f in spec.files]

        assert len(jobs) == len(files)
        for job in jobs:
            assert job.key == f"voice/{job.model_id}/{job.file.sha256}/{job.file.name}"
            assert job.mirror_url == "https://dl.mobaide.com/" + job.key
            assert job.sources
            assert all(not s.startswith(uvm.catalog.VOICE_MIRROR_BASE) for s in job.sources)

    def test_a_file_without_a_mirror_url_is_an_error(self, monkeypatch):
        spec = uvm.catalog.PARAKEET_V3
        first, *rest = spec.files
        no_mirror = uvm.catalog.CatalogFile(first.name, first.size, first.sha256, first.urls[1:])
        broken = uvm.catalog.ModelSpec(**{**spec.__dict__, "files": (no_mirror, *rest)})
        monkeypatch.setattr(uvm.catalog, "MODELS", {spec.id: broken})

        with pytest.raises(ReleaseError, match="has no https://dl.mobaide.com/voice/ URL"):
            uvm.mirror_jobs()


class TestCredentials:
    def _write(self, tmp_path: Path, text: str) -> Path:
        release_dir = tmp_path / "release"
        voice = release_dir / "voice"
        voice.mkdir(parents=True)
        path = voice / "r2.properties"
        path.write_text(text, encoding="utf-8")
        if os.name != "nt":
            os.chmod(voice, 0o700)
            os.chmod(path, 0o600)
        return release_dir

    def test_reads_all_four_keys(self, tmp_path):
        release_dir = self._write(
            tmp_path,
            "# R2\naccount_id=acc\nbucket=b\naccess_key_id=k\nsecret_access_key=s\n",
        )

        creds = uvm.load_credentials(release_dir)

        assert creds == {"account_id": "acc", "bucket": "b", "access_key_id": "k", "secret_access_key": "s"}

    def test_names_the_missing_keys(self, tmp_path):
        release_dir = self._write(tmp_path, "account_id=acc\nbucket=\n")

        with pytest.raises(ReleaseError, match="lacks bucket, access_key_id, secret_access_key"):
            uvm.load_credentials(release_dir)


class TestStream:
    def test_returns_the_digest_and_writes_the_sink(self, monkeypatch):
        body = b"x" * 3000
        _serve(monkeypatch, body)
        sink = io.BytesIO()

        assert uvm.stream("https://a.test/f", len(body), sink) == hashlib.sha256(body).hexdigest()
        assert sink.getvalue() == body

    def test_more_bytes_than_pinned_is_an_error(self, monkeypatch):
        _serve(monkeypatch, b"x" * 10)

        with pytest.raises(ReleaseError, match="more than the 9 bytes"):
            uvm.stream("https://a.test/f", 9, None)

    def test_fewer_bytes_than_pinned_is_an_error(self, monkeypatch):
        _serve(monkeypatch, b"x" * 8)

        with pytest.raises(ReleaseError, match="sent 8 bytes, the catalog pins 9"):
            uvm.stream("https://a.test/f", 9, None)


class TestLocalCopy:
    def test_source_dir_file_must_match_the_catalog(self, tmp_path):
        job = _job(b"good bytes")
        (tmp_path / "f.bin").write_bytes(b"bad bytes!")

        with pytest.raises(ReleaseError, match="does not match"):
            uvm.local_copy(job, tmp_path, tmp_path / "work")

    def test_matching_source_dir_file_is_used_without_a_download(self, tmp_path, monkeypatch):
        job = _job(b"good bytes")
        (tmp_path / "f.bin").write_bytes(b"good bytes")
        seen = _serve(monkeypatch, b"")

        assert uvm.local_copy(job, tmp_path, tmp_path / "work") == tmp_path / "f.bin"
        assert seen == []

    def test_wrong_bytes_from_one_source_fall_through_to_the_next(self, tmp_path, monkeypatch):
        body = b"good bytes"
        job = _job(body, sources=("https://bad.test/f.bin", "https://good.test/f.bin"))

        def urlopen(req, timeout, context):
            return _Response(b"evil bytes" if "bad.test" in req.full_url else body)

        monkeypatch.setattr(uvm.urllib.request, "urlopen", urlopen)

        path = uvm.local_copy(job, None, tmp_path)

        assert path.read_bytes() == body
