"""Find out whether a credential is available, and say what to do when it is not.

This module never reads, prints, stores or transmits a credential value. It checks
whether a variable is *set* and reports the name. Everything else is instructions for
the person at the keyboard, who is the only one who should be handling the secret.

That is a deliberate limit, not an oversight. A setup helper that accepts a pasted key
and writes it somewhere is the single most common way keys end up in repositories, and
a library whose subject is verifiable claims has no business being the tool that does
it.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# In the order the Anthropic SDK resolves them.
ANTHROPIC_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# Set by an identity-linked key; the API rejects requests that omit it.
WORKSPACE_VAR = "ANTHROPIC_WORKSPACE_ID"


@dataclass(frozen=True)
class Credential:
    """What is available, without ever holding what it is."""

    variable: str | None          # which variable supplied it
    present: bool
    workspace: bool               # a workspace id is also set

    @property
    def summary(self) -> str:
        if not self.present:
            return "no credential found"
        extra = " + workspace id" if self.workspace else ""
        return f"found in {self.variable}{extra}"


def detect() -> Credential:
    """Which credential variable is set, if any. The value is never read."""
    for name in ANTHROPIC_VARS:
        if os.environ.get(name, "").strip():
            return Credential(name, True, bool(os.environ.get(WORKSPACE_VAR, "").strip()))
    return Credential(None, False, bool(os.environ.get(WORKSPACE_VAR, "").strip()))


def _shell_hint() -> tuple[str, str]:
    """The right export syntax and profile file for this shell."""
    if sys.platform == "win32":
        return ("setx ANTHROPIC_API_KEY \"sk-ant-...\"", "a new terminal (setx affects future sessions)")
    shell = Path(os.environ.get("SHELL", "/bin/sh")).name
    profile = {"zsh": "~/.zshrc", "bash": "~/.bashrc", "fish": "~/.config/fish/config.fish"}.get(shell, "your shell profile")
    if shell == "fish":
        return ('set -Ux ANTHROPIC_API_KEY "sk-ant-..."', profile)
    return ('export ANTHROPIC_API_KEY="sk-ant-..."', profile)


def guidance(*, for_error: bool = False) -> str:
    """What to tell someone who has no credential.

    `for_error` shortens it: at the point of a failed call the person wants the fix,
    not the tour.
    """
    command, profile = _shell_hint()
    lines: list[str] = []
    if for_error:
        lines.append("No API credential is set, so this call cannot be made.")
    lines += [
        "",
        "  Get a key from https://console.anthropic.com/settings/keys, then:",
        "",
        f"      {command}",
        "",
        f"  To keep it across sessions, add that line to {profile}.",
        "",
        "  Nothing in this library reads a .env file. If you keep keys in one,",
        "  export them into the environment first — a key sitting in an unread",
        "  file looks identical to no key at all.",
        "",
        "  To run with no credential and no spend at all:",
        "",
        "      PYLIGENT_AGENTS_BACKEND=scripted",
        "",
        "  Then check what the library can see:  pyligent-agents setup",
    ]
    return "\n".join(lines)


def missing_credential_error() -> RuntimeError:
    """The error to raise instead of the SDK's, which names no remedy."""
    return RuntimeError(guidance(for_error=True))


def gitignore_protects(name: str = ".env", root: Path | None = None) -> bool | None:
    """Whether `name` is ignored here. None when there is no git repository."""
    base = Path(root or Path.cwd())
    ignore = base / ".gitignore"
    if not (base / ".git").exists():
        return None
    if not ignore.exists():
        return False
    patterns = {
        line.strip().lstrip("/")
        for line in ignore.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    return name in patterns or name.lstrip(".") in patterns or "*.env" in patterns
