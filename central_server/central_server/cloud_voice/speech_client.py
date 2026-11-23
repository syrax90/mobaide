"""HTTP client of the speech service behind cloud voice input.

The service speaks the OpenAI chat-completions API with inline audio. Its
address, key and model name come from the server configuration only; nothing
here names them. Neither audio nor text is ever logged.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass

import httpx

from .audio import wav

logger = logging.getLogger(__name__)
# httpx logs every request line, URL included, at INFO. The speech service's
# address belongs to the configuration only, so it stays out of the log.
logging.getLogger("httpx").setLevel(logging.WARNING)

# A plain instruction any speech model follows. A deployment that has tuned
# the wording for its own service sets it in CLOUD_VOICE_PROMPT instead.
DEFAULT_PROMPT = (
    "Transcribe this recording in the language it is spoken in. Leave out filler "
    "words and accidental repetitions, and keep the rest of the wording as spoken. "
    "Use punctuation and capitalization. Reply with the transcript only."
)

_CONNECT_TIMEOUT = 5.0
_WRITE_TIMEOUT = 15.0
_POOL_TIMEOUT = 5.0


class SpeechServiceError(Exception):
    """A request the speech service did not answer with a transcript.

    ``kind``: ``unavailable`` (unreachable, timed out, a 5xx, or it refused
    this server's key), ``busy`` (it is rate limiting this server),
    ``blocked`` (it refuses this one user) or ``rejected`` (it refused this
    one request, and works for others).

    ``timed_out``: the service had the whole request and did not answer in
    time, so it may have done the work.
    """

    def __init__(self, kind: str, reason: str, *, timed_out: bool = False) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason
        self.timed_out = timed_out


@dataclass(frozen=True)
class Transcript:
    text: str
    # The answer hit the token limit: longer than speech of that length can be.
    truncated: bool


class SpeechClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        prompt: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        root = base_url.strip().rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        self._url = f"{root}/v1/chat/completions"
        self._model = model
        self._prompt = prompt or DEFAULT_PROMPT
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            # One recording is at most 5 MB of JSON. Idle connections are kept
            # for a while, so most requests skip the TLS handshake.
            limits=httpx.Limits(
                max_connections=16, max_keepalive_connections=4, keepalive_expiry=60.0
            ),
            follow_redirects=False,
        )

    async def transcribe(
        self, pcm: bytes, *, user: str, max_tokens: int, timeout: float
    ) -> Transcript:
        """The text of ``pcm`` (16 kHz mono PCM16); raises ``SpeechServiceError``."""
        try:
            response = await self._http.post(
                self._url,
                content=self._body(pcm, user, max_tokens),
                headers={"Content-Type": "application/json"},
                timeout=httpx.Timeout(
                    timeout,
                    connect=_CONNECT_TIMEOUT,
                    write=_WRITE_TIMEOUT,
                    pool=_POOL_TIMEOUT,
                ),
            )
        except httpx.TimeoutException as exc:
            # Only a read timeout comes after the request was sent in full.
            raise SpeechServiceError(
                "unavailable", type(exc).__name__, timed_out=isinstance(exc, httpx.ReadTimeout)
            ) from None
        except httpx.HTTPError as exc:
            raise SpeechServiceError("unavailable", type(exc).__name__) from None
        if response.status_code != 200:
            raise _refusal(response)
        try:
            choice = response.json()["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, LookupError, TypeError):
            raise SpeechServiceError("unavailable", "unreadable_answer") from None
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise SpeechServiceError("unavailable", "unreadable_answer")
        return Transcript(text=content, truncated=choice.get("finish_reason") == "length")

    def _body(self, pcm: bytes, user: str, max_tokens: int) -> bytes:
        """The request as bytes, built so that the recording is held once:
        the base64 text goes straight into the body, never through a dict."""
        head = json.dumps(
            {
                "model": self._model,
                "temperature": 0,
                "seed": 0,
                "max_tokens": max_tokens,
                # Lets the service's operator tell users apart and refuse one.
                "user": user,
            },
            separators=(",", ":"),
        ).encode()
        text_part = json.dumps({"type": "text", "text": self._prompt}, separators=(",", ":"))
        return b"".join(
            (
                head[:-1],
                b',"messages":[{"role":"user","content":[',
                text_part.encode(),
                b',{"type":"audio_url","audio_url":{"url":"data:audio/wav;base64,',
                base64.b64encode(wav(pcm)),
                b'"}}]}]}',
            )
        )

    async def aclose(self) -> None:
        await self._http.aclose()


def _refusal(response: httpx.Response) -> SpeechServiceError:
    status = response.status_code
    if status == 429:
        return SpeechServiceError("busy", "http_429")
    if status == 403 and "user is blocked" in response.text[:2000].lower():
        return SpeechServiceError("blocked", "user_blocked")
    if status in (400, 413, 422):
        return SpeechServiceError("rejected", f"http_{status}")
    if status in (401, 403):
        # The key in this server's configuration is wrong or was revoked.
        logger.error("cloud_voice.key_rejected status=%d", status)
    return SpeechServiceError("unavailable", f"http_{status}")
