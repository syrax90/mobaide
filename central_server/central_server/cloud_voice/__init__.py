"""Cloud voice input: dictation that a hosted speech service transcribes.

The client streams the same numbered PCM16 chunks it sends to an agent's
``/api/voice/dictations``, but to this server, which forwards the finished
recording to the speech service named in its configuration and returns the
text. See PROTOCOL.md, "cloud voice input".
"""
from .service import CloudVoiceService

__all__ = ["CloudVoiceService"]
