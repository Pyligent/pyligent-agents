"""Primitive types shared by every layer.

Deliberately small, deliberately in the Anthropic wire shape. When you graduate
from this repo to your own service, the conversation plumbing transfers
unchanged — you are not learning a private message format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

Message = dict[str, Any]


class PermissionTier(str, Enum):
    """How much damage a tool can do if the model is wrong.

    The tier is part of the tool's identity, not a policy lookup bolted on
    later. A tool that issues a refund and a tool that reads an order must not
    be constructible with the same blast radius by accident.
    """

    READ_ONLY = "read_only"       # cannot change anything; parallel-safe
    REVERSIBLE = "reversible"     # writes something you can undo
    RESTRICTED = "restricted"     # leaves the building; needs a decision


class Phase(str, Enum):
    """The four phases of an agent turn, made observable.

    The whole loop is: gather context, take action, verify the work, repeat.
    Naming the phases is not ceremony — it is what lets the harness apply a
    different context policy, a different tool surface and a different model to
    each one, and what lets a trace tell you *where* a run went wrong.
    """

    GATHER = "gather"
    ACT = "act"
    VERIFY = "verify"
    SYNTHESISE = "synthesise"


@dataclass(frozen=True)
class ToolSpec:
    """A tool as the model sees it, plus what the harness enforces."""

    name: str
    description: str
    input_schema: dict[str, Any]
    tier: PermissionTier = PermissionTier.READ_ONLY

    # Deferred tools are declared but not sent to the model until something
    # surfaces them. This is how you carry 200 tools without paying 200 schemas
    # of context on every call.
    defer_loading: bool = False

    # Phases this tool belongs to. A verify-phase agent should not be holding
    # write tools.
    phases: tuple[Phase, ...] = (Phase.GATHER, Phase.ACT)

    # Read-only tools are safe to run concurrently. The harness uses this;
    # the model never sees it.
    parallel_safe: bool = True

    def to_wire(self) -> dict[str, Any]:
        """The subset the Messages API accepts. Everything else is ours."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True)
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )

    @property
    def total_input(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }


@dataclass(frozen=True)
class LLMResponse:
    """One model turn, normalised across backends."""

    stop_reason: str  # end_turn | tool_use | max_tokens | refusal
    text: str
    tool_uses: tuple[ToolUse, ...] = ()
    content: tuple[dict[str, Any], ...] = ()  # replayed verbatim, never rebuilt
    usage: Usage = field(default_factory=Usage)
    model: str = ""

    @property
    def wants_tools(self) -> bool:
        return self.stop_reason == "tool_use" and bool(self.tool_uses)


@runtime_checkable
class LLMClient(Protocol):
    """The only model surface anything above the harness knows about."""

    def complete(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> LLMResponse: ...


# --- message constructors -------------------------------------------------


def user_turn(text: str) -> Message:
    return {"role": "user", "content": text}


def assistant_turn(response: LLMResponse) -> Message:
    """Replay the assistant turn verbatim.

    Reconstructing this from `response.text` drops the tool_use blocks, and the
    next request 400s on an unmatched tool_result. Always echo `content`.
    """
    return {"role": "assistant", "content": list(response.content)}


def tool_result_turn(blocks: list[dict[str, Any]]) -> Message:
    """All tool results for one assistant turn go back in ONE user message.

    Splitting them across messages trains the model out of asking for parallel
    tool calls, and every later turn gets slower.
    """
    return {"role": "user", "content": blocks}


def tool_result_block(
    tool_use_id: str, content: str, *, is_error: bool = False
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
    if is_error:
        # An error is an observation, not an exception.
        block["is_error"] = True
    return block
