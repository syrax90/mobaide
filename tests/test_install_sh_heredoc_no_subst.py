"""Guard against a here-document nested inside ``$(...)`` in
``deploy/install.sh``.

macOS ships bash 3.2, which re-scans such a heredoc body as shell text. An
odd quote count in the body (the staging Python had one) then aborts the
whole install at parse time with ``unexpected end of file``. bash 4/5 parse
the same shape fine, so ``bash -n`` on Linux CI cannot catch it; this
line-level check can.
"""
from __future__ import annotations

import re

from test_install_script_trap import _read_install_sh_text

# A heredoc operator (``<<WORD``, ``<<-WORD``, ``<<'WORD'``, ``<<"WORD"``),
# not the Python ``1 << 20`` left shift.
_HEREDOC_OPENER = re.compile(r"<<-?\s*(['\"]?)[A-Za-z_][A-Za-z0-9_]*\1")


def test_install_sh_has_no_heredoc_inside_command_substitution() -> None:
    offenders = [
        (lineno, line)
        for lineno, line in enumerate(_read_install_sh_text().splitlines(), start=1)
        if _HEREDOC_OPENER.search(line) and line.count("$(") > line.count(")")
    ]
    assert not offenders, (
        "redirect the heredoc's stdout to a temp file and read it back "
        f"instead: {offenders}"
    )
