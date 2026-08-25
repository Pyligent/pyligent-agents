"""Hooks: the interception points that make a harness a harness.

A bare loop gives you two places to put policy — inside the tool, or inside the
model prompt. Both are wrong. Tool code should not know about approval
workflows, and a prompt is not an enforcement mechanism.

Four points, each with one job:

    PRE_MODEL    inject just-in-time context; last chance to change the request
    POST_MODEL   observe what came back; enforce output shape
    PRE_TOOL     gate, rewrite arguments, or deny — before anything runs
    POST_TOOL    redact, truncate, offload, defang untrusted content

`PRE_TOOL` is where permission decisions land. `POST_TOOL` is where prompt
injection is handled — not by asking the model nicely, but by neutralising
instruction-shaped text in content the model is about to read.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..core.types import LLMResponse, PermissionTier, ToolUse


class HookPoint(str, Enum):
    PRE_MODEL = "pre_model"
    POST_MODEL = "post_model"
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"


class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class ToolCallContext:
    """Mutable view of a tool call, handed to PRE_TOOL hooks."""

    tool_use: ToolUse
    tier: PermissionTier
    phase: str
    arguments: dict[str, Any]
    verdict: Verdict = Verdict.ALLOW
    reason: str = ""
    notes: list[str] = field(default_factory=list)

    def deny(self, reason: str) -> None:
        self.verdict, self.reason = Verdict.DENY, reason

    def ask(self, reason: str) -> None:
        self.verdict, self.reason = Verdict.ASK, reason

    def rewrite(self, **changes: Any) -> None:
        """Correct arguments before execution. Use sparingly and log it."""
        self.arguments.update(changes)
        self.notes.append(f"arguments rewritten: {sorted(changes)}")


@dataclass
class ToolResultContext:
    """Mutable view of a tool result, handed to POST_TOOL hooks."""

    tool_name: str
    content: str
    is_error: bool
    trusted: bool = True
    notes: list[str] = field(default_factory=list)

    def replace(self, content: str, note: str = "") -> None:
        self.content = content
        if note:
            self.notes.append(note)


@dataclass
class ModelCallContext:
    phase: str
    model: str
    system: str
    turn: int
    extra_context: list[str] = field(default_factory=list)

    def add_context(self, text: str) -> None:
        """Just-in-time context, appended after the cached prefix.

        Never edit `system` from a hook — it sits at the front of the prefix,
        and changing it invalidates the prompt cache for the whole run.
        """
        self.extra_context.append(text)


PreModelHook = Callable[[ModelCallContext], None]
PostModelHook = Callable[[ModelCallContext, LLMResponse], None]
PreToolHook = Callable[[ToolCallContext], None]
PostToolHook = Callable[[ToolResultContext], None]


@dataclass
class HookBus:
    """Ordered hook registry. First DENY wins; ASK does not override DENY."""

    pre_model: list[PreModelHook] = field(default_factory=list)
    post_model: list[PostModelHook] = field(default_factory=list)
    pre_tool: list[PreToolHook] = field(default_factory=list)
    post_tool: list[PostToolHook] = field(default_factory=list)

    def on(self, point: HookPoint, fn: Callable) -> HookBus:
        getattr(self, point.value).append(fn)
        return self

    def run_pre_model(self, ctx: ModelCallContext) -> ModelCallContext:
        for h in self.pre_model:
            h(ctx)
        return ctx

    def run_post_model(self, ctx: ModelCallContext, response: LLMResponse) -> None:
        for h in self.post_model:
            h(ctx, response)

    def run_pre_tool(self, ctx: ToolCallContext) -> ToolCallContext:
        for h in self.pre_tool:
            h(ctx)
            if ctx.verdict is Verdict.DENY:
                break  # a denial is final; later hooks cannot upgrade it
        return ctx

    def run_post_tool(self, ctx: ToolResultContext) -> ToolResultContext:
        for h in self.post_tool:
            h(ctx)
        return ctx


# --- hooks worth having by default ----------------------------------------

# Instruction-shaped text that has no business appearing in a document a
# counterparty's lawyers wrote.
_INJECTION = re.compile(
    r"(?im)^\s*(?:"
    r"ignore (?:all |any )?(?:previous|prior|above) instructions"
    r"|disregard (?:the )?(?:above|previous|system)"
    r"|you are now\b|new instructions?:|system prompt:"
    r"|</?(?:system|instructions?)>"
    r")\b.*$"
)


def defang_untrusted_content(ctx: ToolResultContext) -> None:
    """Neutralise instruction-shaped text in untrusted tool output.

    A scraped page, a supplier's invoice, a user-submitted ticket: all written
    by someone else. Text inside them that reads like an instruction is *data*,
    and must not be able to steer the agent.

    This is the second line of defence, not the first. The first is that
    document-reading agents hold no restricted tools, so an injected
    instruction has nothing worth reaching. Filters can be evaded; capability
    boundaries cannot.
    """
    if ctx.trusted or ctx.is_error:
        return
    cleaned, n = _INJECTION.subn("[redacted: instruction-shaped text in untrusted content]", ctx.content)
    if n:
        ctx.replace(cleaned, f"defanged {n} instruction-shaped line(s)")


def redact_secrets(ctx: ToolResultContext) -> None:
    """Keep credential-shaped strings out of the transcript.

    Anything that enters the context is persisted, replayed on every subsequent
    turn, and folded into compaction summaries. A key that lands there once is
    in the run forever.
    """
    cleaned, n = re.subn(r"\b(sk-[A-Za-z0-9_\-]{12,}|ghp_[A-Za-z0-9]{20,})\b", "[redacted]", ctx.content)
    if n:
        ctx.replace(cleaned, f"redacted {n} credential-shaped token(s)")


def deny_restricted_without_approval(ctx: ToolCallContext) -> None:
    """The default posture: absence of an approver is a denial, never an allow."""
    if ctx.tier is PermissionTier.RESTRICTED:
        ctx.ask("restricted tool requires an explicit human decision")


def phase_guard(ctx: ToolCallContext) -> None:
    """A verify-phase agent must not be able to change anything.

    Verification that can mutate the thing it is verifying is not verification.
    """
    if ctx.phase == "verify" and ctx.tier is not PermissionTier.READ_ONLY:
        ctx.deny("the verify phase is read-only")


def default_hooks() -> HookBus:
    return (
        HookBus()
        .on(HookPoint.PRE_TOOL, phase_guard)
        .on(HookPoint.PRE_TOOL, deny_restricted_without_approval)
        .on(HookPoint.POST_TOOL, defang_untrusted_content)
        .on(HookPoint.POST_TOOL, redact_secrets)
    )
