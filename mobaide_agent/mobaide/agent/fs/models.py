"""Wire models for the host-filesystem browsing routes (``/api/fs``)."""
from __future__ import annotations

from pydantic import BaseModel

# Advertised as ``capabilities.fs`` on ``GET /api/version`` (both transports).
# ``dirs`` covers ``GET /api/fs/dirs``; clients offer path suggestions only
# when it is true, so an older agent simply shows none.
FS_CAPABILITIES: dict[str, bool] = {
    "dirs": True,
}


class FsDirsResponse(BaseModel):
    """Subdirectory names of one host directory.

    ``path`` is the listed directory after ``~`` expansion, so a client that
    completes ``~/co`` can write back an absolute path. ``truncated`` means a
    scan cap or the time budget stopped the listing early: the client must
    not filter it locally as if it were complete, and re-asks with ``q``.
    """

    path: str
    dirs: list[str]
    truncated: bool
