"""Wire models for the host-filesystem browsing routes (``/api/fs``)."""
from __future__ import annotations

import os

from pydantic import BaseModel

# Advertised as ``capabilities.fs`` on ``GET /api/version`` (both transports).
# ``dirs`` covers ``GET /api/fs/dirs``; clients offer path suggestions only
# when it is true, so an older agent simply shows none. ``windows_paths`` says
# host paths are Windows paths (drive letters, ``\`` separators), so clients
# word their path hints for that host.
FS_CAPABILITIES: dict[str, bool] = {
    "dirs": True,
    "windows_paths": os.name == "nt",
}


class FsDirsResponse(BaseModel):
    """Subdirectory names of one host directory.

    ``path`` is the listed directory after ``~`` expansion, so a client that
    completes ``~/co`` can write back an absolute path; on Windows its ``/``
    separators are rewritten to ``\\``, so a picked folder extends it with
    the host's own separator. ``truncated`` means a scan cap or the time
    budget stopped the listing early: the client must not filter it locally
    as if it were complete, and re-asks with ``q``.
    """

    path: str
    dirs: list[str]
    truncated: bool
