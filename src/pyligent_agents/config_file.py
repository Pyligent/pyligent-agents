"""Configuration from a file; secrets from the environment. Never both.

A YAML file that holds structure belongs in the repository. A YAML file that
holds an API key belongs nowhere — and once one exists, it reaches git history
within a fortnight and stays there after it is deleted.

So the file names the variable, and the value is resolved at load:

    extractor:
      provider: anthropic
      model: claude-sonnet-5
      api_key_env: ANTHROPIC_API_KEY     # the NAME, never the value

`load()` refuses a file that appears to contain a secret rather than a
reference to one. That refusal is the whole point of the module: a check that
only runs when someone remembers to run it is not a control.

No YAML dependency. The subset parsed here — nested maps, scalars, simple
lists, comments — is all a configuration file needs, and adding a parser
dependency to a zero-dependency library to read six keys is a bad trade.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# A value that looks like a credential rather than a reference to one. These
# are deliberately broad: a false positive costs an argument, a false negative
# costs a rotation and an incident report.
_SECRET_SHAPES: tuple[tuple[str, str], ...] = (
    (r"sk-[A-Za-z0-9_\-]{16,}", "an OpenAI-style secret key"),
    (r"sk-ant-[A-Za-z0-9_\-]{16,}", "an Anthropic secret key"),
    (r"AIza[A-Za-z0-9_\-]{30,}", "a Google API key"),
    (r"AKIA[0-9A-Z]{16}", "an AWS access key id"),
    (r"ghp_[A-Za-z0-9]{20,}", "a GitHub token"),
    (r"xox[baprs]-[A-Za-z0-9\-]{10,}", "a Slack token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
    (r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.", "a JSON Web Token"),
)

# Keys whose value must be a variable name, not a credential.
_REFERENCE_KEYS = ("api_key_env", "token_env", "secret_env", "password_env")


class ConfigError(ValueError):
    """The configuration cannot be loaded safely."""


@dataclass(frozen=True)
class Config:
    values: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.values
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def secret(self, dotted: str, *, required: bool = True) -> str | None:
        """Resolve a secret by the variable name the config points at.

        The config says which environment variable holds the credential; this
        reads it. The value never appears in the file, in the repository, or in
        this object — only in the process environment.
        """
        var = self.get(dotted)
        if not var:
            if required:
                raise ConfigError(
                    f"'{dotted}' is not set. It should name an environment "
                    f"variable, for example: {dotted}: MY_PROVIDER_API_KEY"
                )
            return None
        value = os.getenv(str(var))
        if value is None and required:
            raise ConfigError(
                f"'{dotted}' points at ${var}, which is not set in this "
                f"environment. Export it, or use a secret manager that does."
            )
        return value


def scan_for_secrets(text: str) -> list[str]:
    """Credential-shaped strings in configuration text. Empty is the good case."""
    found = []
    for pattern, what in _SECRET_SHAPES:
        if re.search(pattern, text):
            found.append(what)
    for key in _REFERENCE_KEYS:
        for m in re.finditer(rf"{key}\s*:\s*(\S+)", text):
            v = m.group(1).strip("\"'")
            # A variable name is upper snake case. Anything else is a value.
            if v and not re.fullmatch(r"[A-Z][A-Z0-9_]*", v):
                found.append(f"'{key}' looks like a credential, not a variable name")
    return found


def parse(text: str) -> dict[str, Any]:
    """A deliberately small YAML subset: nested maps, scalars, simple lists."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()

        if body.startswith("- "):
            parent = stack[-1][1]
            key = parent.pop("__list_key__", None)
            if key is not None:
                parent.setdefault(key, [])
            target = parent.get(parent.get("__last__", ""), None)
            if isinstance(target, list):
                target.append(_scalar(body[2:].strip()))
            continue

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            stack = [(-1, root)]
        parent = stack[-1][1]

        if ":" not in body:
            raise ConfigError(f"cannot parse configuration line: {raw!r}")
        key, _, value = body.partition(":")
        key, value = key.strip(), value.strip()
        parent["__last__"] = key

        if not value:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _scalar(value)

    _strip_internals(root)
    return root


def _scalar(text: str) -> Any:
    t = text.strip().strip("\"'")
    low = t.casefold()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "none", "~", ""):
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        return t


def _strip_internals(node: Any) -> None:
    if isinstance(node, dict):
        node.pop("__last__", None)
        node.pop("__list_key__", None)
        for v in node.values():
            _strip_internals(v)
    elif isinstance(node, list):
        for v in node:
            _strip_internals(v)


def load(path: str | Path, *, allow_secrets: bool = False) -> Config:
    """Read a configuration file, refusing one that carries a credential."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"no configuration file at {p}")
    text = p.read_text(encoding="utf-8")

    if not allow_secrets:
        found = scan_for_secrets(text)
        if found:
            raise ConfigError(
                f"{p} appears to contain {found[0]}. Configuration files hold "
                f"the NAME of an environment variable, never its value — this "
                f"file will be committed, and deleting a secret from a "
                f"repository does not remove it from history. "
                f"Move it to the environment or a secret manager and point at "
                f"it with `api_key_env:`."
            )
    return Config(values=parse(text), path=p)
