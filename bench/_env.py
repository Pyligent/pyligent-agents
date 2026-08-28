"""Load a .env file the way people actually write them.

Shell sourcing is unforgiving: `KEY = value` makes the shell treat `KEY` as a
command, and the failure is a confusing "command not found" rather than a
missing variable. That is not the author's mistake to pay for.

Tolerated: spaces around `=`, quotes, comments, blank lines, `export` prefixes.
Never printed, never logged, never written anywhere.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path = ".env", *, override: bool = False) -> list[str]:
    """Set variables from `path`. Returns the NAMES set, never the values."""
    p = Path(path)
    if not p.exists():
        return []
    names = []
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value
        names.append(key)
    return names
