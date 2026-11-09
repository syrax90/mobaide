#!/usr/bin/env python3
"""upload_voice_model.py — Copy the pinned voice-model files to our download mirror.

Usage:
    python deploy/prod/upload_voice_model.py                    # upload what is missing, then check
    python deploy/prod/upload_voice_model.py --check            # only check what the mirror serves
    python deploy/prod/upload_voice_model.py --source-dir DIR   # take the files from DIR, not the internet

Agents download each voice-model file from https://dl.mobaide.com/voice/...
first (a public Cloudflare R2 bucket) and from Hugging Face second. This
script fills the bucket from mobaide_agent/mobaide/voice/catalog.py. For each
model file it:
  1) skips the upload when the bucket already has the object at its size
  2) gets the file from --source-dir or the catalog's other URLs, and checks
     its size and SHA-256
  3) uploads it to the key that the mirror URL names
  4) downloads it anonymously from the mirror URL and checks size and SHA-256

The key holds the SHA-256, so a re-pinned file gets a new URL and an old one
never changes meaning. Re-running is safe.

Credentials come from <release dir>/voice/r2.properties (see release_common.py):
    account_id=<Cloudflare account ID>
    bucket=<R2 bucket name>
    access_key_id=<R2 API token access key ID>
    secret_access_key=<R2 API token secret access key>
--check needs no credentials. Uploading needs `boto3` 1.36 or newer in the
interpreter that runs this.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from deploy_central import (
    PROJECT_ROOT,
    DeployError,
    dim,
    enable_ansi,
    fail,
    http_ssl_context,
    ok,
    require_min_python,
    step,
)
from release_common import (
    ReleaseError,
    read_properties,
    resolve_release_dir,
    secret_file,
    sha256_file,
    with_retries,
)

sys.path.insert(0, str(PROJECT_ROOT / "mobaide_agent"))
from mobaide.voice import catalog  # noqa: E402

CREDENTIALS_FILE = "voice/r2.properties"
CREDENTIAL_KEYS = ("account_id", "bucket", "access_key_id", "secret_access_key")
USER_AGENT = "mobaide-release-script"
# The key names the bytes, so the edge may keep the object for good.
CACHE_CONTROL = "public, max-age=31536000, immutable"
# octet-stream for every file: Cloudflare compresses text types on the fly,
# and a compressed reply cannot answer the agent's Range resume.
CONTENT_TYPE = "application/octet-stream"
_CHUNK = 1 << 20


@dataclass(frozen=True)
class MirrorJob:
    model_id: str
    file: catalog.CatalogFile
    mirror_url: str
    key: str
    sources: tuple[str, ...]


def mirror_jobs() -> list[MirrorJob]:
    """One job per catalog file; a file without a mirror URL is an error."""
    jobs = []
    for spec in catalog.MODELS.values():
        for file in spec.files:
            mirror = catalog.mirror_url(spec.id, file.name, file.sha256)
            if mirror not in file.urls:
                raise ReleaseError(f"{spec.id}/{file.name} has no {catalog.VOICE_MIRROR_BASE} URL")
            sources = tuple(u for u in file.urls if u != mirror)
            key = urlsplit(mirror).path.lstrip("/")
            jobs.append(MirrorJob(spec.id, file, mirror, key, sources))
    return jobs


def load_credentials(release_dir: Path) -> dict[str, str]:
    path = secret_file(release_dir, CREDENTIALS_FILE)
    props = read_properties(path)
    missing = [k for k in CREDENTIAL_KEYS if not props.get(k)]
    if missing:
        raise ReleaseError(f"{path} lacks {', '.join(missing)}")
    return props


# ------------------------------------------------------------ HTTP
def stream(url: str, size: int, sink: BinaryIO | None) -> str:
    """GET ``url`` without credentials, write it to ``sink``, return the
    SHA-256. More than ``size`` bytes or fewer is an error."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    got = 0
    with urllib.request.urlopen(req, timeout=120, context=http_ssl_context()) as resp:
        while chunk := resp.read(_CHUNK):
            got += len(chunk)
            if got > size:
                raise ReleaseError(f"{url} sends more than the {size} bytes the catalog pins")
            digest.update(chunk)
            if sink is not None:
                sink.write(chunk)
    if got != size:
        raise ReleaseError(f"{url} sent {got} bytes, the catalog pins {size}")
    return digest.hexdigest()


def fetch_source(job: MirrorJob, work_dir: Path) -> Path:
    dest = work_dir / job.file.name
    errors = []
    for url in job.sources:
        try:
            def attempt() -> str:
                with dest.open("wb") as fh:
                    return stream(url, job.file.size, fh)

            actual = with_retries(f"GET {url}", attempt, attempts=3, delay=5)
        except ReleaseError as e:
            errors.append(str(e))
            continue
        if actual == job.file.sha256:
            ok(f"Downloaded {job.file.name} from {urlsplit(url).hostname}")
            return dest
        errors.append(f"{url} serves sha256 {actual}, the catalog pins {job.file.sha256}")
    dest.unlink(missing_ok=True)
    raise ReleaseError(f"no source serves {job.file.name}:\n  " + "\n  ".join(errors))


def local_copy(job: MirrorJob, source_dir: Path | None, work_dir: Path) -> Path:
    if source_dir is not None:
        path = source_dir / job.file.name
        if not path.is_file():
            raise ReleaseError(f"{path} does not exist")
        if path.stat().st_size != job.file.size or sha256_file(path) != job.file.sha256:
            raise ReleaseError(f"{path} does not match the catalog's size and SHA-256")
        ok(f"Using {path}")
        return path
    return fetch_source(job, work_dir)


def check_mirror(job: MirrorJob) -> None:
    """Fetch the file the way an agent does: no credentials, the exact URL."""
    actual = with_retries(
        f"GET {job.mirror_url}", lambda: stream(job.mirror_url, job.file.size, None),
        attempts=3, delay=5,
    )
    if actual != job.file.sha256:
        raise ReleaseError(f"{job.mirror_url} serves sha256 {actual}, the catalog pins {job.file.sha256}")
    ok(f"Mirror serves {job.file.name}")


# ------------------------------------------------------------ R2
def r2_client(creds: dict[str, str]):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as e:
        raise ReleaseError("boto3 is not installed: python -m pip install 'boto3>=1.36'") from e
    return boto3.client(
        "s3",
        endpoint_url=f"https://{creds['account_id']}.r2.cloudflarestorage.com",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name="auto",
        # Send checksums only when an API needs them: R2 rejected the
        # default ones that boto3 1.36 started to add.
        config=Config(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )


def in_bucket(client, bucket: str, job: MirrorJob) -> bool:
    from botocore.exceptions import ClientError

    try:
        head = client.head_object(Bucket=bucket, Key=job.key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise ReleaseError(f"HEAD {job.key}: {e}") from e
    return head.get("ContentLength") == job.file.size


class _UploadProgress:
    """boto3 calls this from its worker threads with each sent chunk."""

    def __init__(self, job: MirrorJob) -> None:
        self._job = job
        self._sent = 0
        self._shown = 0
        self._lock = threading.Lock()

    def __call__(self, sent: int) -> None:
        with self._lock:
            self._sent += sent
            tenth = self._sent * 10 // self._job.file.size
            if tenth > self._shown:
                self._shown = tenth
                dim(f"{self._job.file.name}: {tenth * 10}% sent")


def upload(client, bucket: str, job: MirrorJob, path: Path) -> None:
    from boto3.s3.transfer import TransferConfig
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        client.upload_file(
            str(path),
            bucket,
            job.key,
            ExtraArgs={
                "ContentType": CONTENT_TYPE,
                "CacheControl": CACHE_CONTROL,
                "Metadata": {"sha256": job.file.sha256},
            },
            Config=TransferConfig(multipart_chunksize=64 * _CHUNK),
            Callback=_UploadProgress(job),
        )
    except (BotoCoreError, ClientError) as e:
        raise ReleaseError(f"upload of {job.key} failed: {e}") from e
    ok(f"Uploaded {job.key}")


# ------------------------------------------------------------ main flow
def run(args: argparse.Namespace) -> int:
    jobs = mirror_jobs()
    if not args.check:
        step("Upload to the R2 bucket")
        creds = load_credentials(resolve_release_dir(args.release_dir))
        client = r2_client(creds)
        source_dir = Path(args.source_dir).expanduser().resolve() if args.source_dir else None
        with tempfile.TemporaryDirectory(prefix="mobaide-voice-") as tmp:
            for job in jobs:
                if in_bucket(client, creds["bucket"], job):
                    ok(f"Already in the bucket: {job.key}")
                    continue
                dim(f"{job.file.name}: {job.file.size / 1e6:.1f} MB")
                upload(client, creds["bucket"], job, local_copy(job, source_dir, Path(tmp)))

    step(f"Check {catalog.VOICE_MIRROR_BASE}")
    for job in jobs:
        check_mirror(job)
    ok(f"The mirror serves all {len(jobs)} files")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Copy the pinned voice-model files to the R2 download mirror")
    p.add_argument("--check", action="store_true", help="only download from the mirror and check each file")
    p.add_argument("--source-dir", help="folder that already holds the model files (e.g. an agent's voice/models/<model-id>)")
    p.add_argument("--release-dir", help="folder with the release keys (default: $MOBAIDE_RELEASE_DIR, else ~/.config/mobaide-release, on Windows %%LOCALAPPDATA%%\\mobaide-release)")
    return p


def main(argv: list[str] | None = None) -> int:
    if not require_min_python("upload_voice_model.py"):
        return 1
    args = build_parser().parse_args(argv)
    enable_ansi()
    try:
        return run(args)
    except DeployError as e:
        if str(e):
            fail(str(e))
        return 1
    except KeyboardInterrupt:
        fail("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
