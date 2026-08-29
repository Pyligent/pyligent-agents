"""Make stdout able to carry the characters these tools actually print.

Python on Windows picks the console's ANSI code page for stdout — cp1252 in most of
the world — and cp1252 cannot encode `─`, `✓`, `→` or an em dash. Every report here
uses at least one of them, so the first line of output raises UnicodeEncodeError and
the process dies with a traceback instead of a report.

That is not a CI problem, though CI is where it was noticed. It is what a Windows user
gets today when they run `evidence-check` on a real document: no output, no findings,
a stack trace ending in `charmap`. A verification tool that cannot print its verdict
has failed at the only thing it does.

Reconfiguring to UTF-8 fixes the pipe. `errors="replace"` covers the rest: if a stream
genuinely cannot be reconfigured — a redirect to something exotic, an embedded
interpreter — a character degrades to `?` rather than taking the run down with it. A
mangled glyph is a cosmetic problem; a traceback instead of a report is not.
"""

from __future__ import annotations

import contextlib
import sys
from typing import IO, Any


def _retune(stream: IO[Any] | None) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:  # not a TextIOWrapper: nothing to fix, nothing to break
        return
    # Detached, already closed, or a stream that refuses to be retuned. Printing is
    # not worth crashing over, and the caller has real work to do.
    with contextlib.suppress(ValueError, OSError):
        reconfigure(encoding="utf-8", errors="replace")


def use_utf8_stdout() -> None:
    """Call once, first thing in `main()`. Safe to call more than once."""
    _retune(sys.stdout)
    _retune(sys.stderr)
