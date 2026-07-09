"""APNs push pipeline: attention alerts for devices without a
live user-events WebSocket."""

from .dispatcher import PushDispatcher

__all__ = ["PushDispatcher"]
