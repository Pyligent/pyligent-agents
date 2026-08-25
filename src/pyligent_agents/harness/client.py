"""Model backends. Two of them, one interface.

`AnthropicLLM` is the real Messages API. `ScriptedLLM` is a deterministic second
implementation of the same contract — not a mock. It is why turn caps,
compaction triggers, error recovery and idempotency are unit-testable at all:
you cannot test a turn cap against a live model, because it will behave
differently on the retry and hide the bug.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..core.types import (
    LLMClient,
    LLMResponse,
    Message,
    ToolSpec,
    ToolUse,
    Usage,
)

# Below the model's minimum cacheable prefix a cache_control marker is a silent
# no-op, so we skip it rather than pay a write for nothing.
_CACHE_MIN_CHARS = 2_400


class AnthropicLLM(LLMClient):
    """Thin. The interesting engineering is above this file, not in it."""

    def __init__(self, client: Any | None = None, *, cache_system: bool = True):
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "`pip install anthropic`, or run with PYLIGENT_AGENTS_BACKEND=scripted."
                ) from exc
            # Zero-arg: resolves ANTHROPIC_API_KEY, then ANTHROPIC_AUTH_TOKEN,
            # then an `ant auth login` profile.
            client = anthropic.Anthropic()
        self._c = client
        self._cache_system = cache_system

    def complete(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": self._system(system),
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = [t.to_wire() for t in tools]
        if effort:
            # Effort lives inside output_config, not at the top level. Sampling
            # params (temperature/top_p/top_k) are rejected on current models
            # and are never sent — steer with the prompt instead.
            kwargs["output_config"] = {"effort": effort}

        r = self._c.messages.create(**kwargs)

        # Check the stop reason FIRST. A safety refusal returns HTTP 200 with an
        # empty content list; `content[0].text` raises an IndexError that then
        # gets misreported as a network fault.
        if r.stop_reason == "refusal":
            details = getattr(r, "stop_details", None)
            return LLMResponse(
                stop_reason="refusal",
                text=f"Model declined (category={getattr(details, 'category', None)}).",
                usage=self._usage(r),
                model=getattr(r, "model", model),
            )

        text, uses, raw = [], [], []
        for b in r.content:
            raw.append(b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else dict(b))
            if b.type == "text":
                text.append(b.text)
            elif b.type == "tool_use":
                uses.append(ToolUse(id=b.id, name=b.name, input=dict(b.input)))

        return LLMResponse(
            stop_reason=r.stop_reason,
            text="\n".join(text).strip(),
            tool_uses=tuple(uses),
            content=tuple(raw),
            usage=self._usage(r),
            model=getattr(r, "model", model),
        )

    def _system(self, system: str | list[dict[str, Any]]) -> Any:
        if isinstance(system, list):
            return system
        if not self._cache_system or len(system) < _CACHE_MIN_CHARS:
            return system
        # Render order is tools -> system -> messages, so one breakpoint on the
        # last system block covers both. Keep timestamps and per-request ids
        # OUT of the system prompt: one byte of drift invalidates everything
        # after it, on every call.
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    @staticmethod
    def _usage(r: Any) -> Usage:
        u = getattr(r, "usage", None)
        if u is None:  # pragma: no cover
            return Usage()
        return Usage(
            getattr(u, "input_tokens", 0) or 0,
            getattr(u, "output_tokens", 0) or 0,
            getattr(u, "cache_read_input_tokens", 0) or 0,
            getattr(u, "cache_creation_input_tokens", 0) or 0,
        )


# --- the deterministic backend --------------------------------------------


@dataclass
class ScriptedTurn:
    """One pre-baked model turn: finish, or ask for tools."""

    text: str = ""
    tool_calls: Sequence[tuple[str, dict[str, Any]]] = ()
    stop_reason: str | None = None
    input_tokens: int = 900
    output_tokens: int = 160

    def to_response(self, model: str, i: int) -> LLMResponse:
        content: list[dict[str, Any]] = []
        uses: list[ToolUse] = []
        if self.text:
            content.append({"type": "text", "text": self.text})
        for j, (name, payload) in enumerate(self.tool_calls):
            uid = f"toolu_s{i}_{j}"
            content.append({"type": "tool_use", "id": uid, "name": name, "input": payload})
            uses.append(ToolUse(id=uid, name=name, input=dict(payload)))
        return LLMResponse(
            stop_reason=self.stop_reason or ("tool_use" if uses else "end_turn"),
            text=self.text,
            tool_uses=tuple(uses),
            content=tuple(content),
            usage=Usage(input_tokens=self.input_tokens, output_tokens=self.output_tokens),
            model=model,
        )


@dataclass(frozen=True)
class ScriptedCall:
    """Everything the caller asked for, handed to a policy.

    `system` is included because in one graph run the orchestrator, several
    workers and the verifier share a client, and the system prompt is how a
    policy tells them apart — exactly how prompt-routing works in real fixtures.
    """

    model: str
    system: str
    messages: list[Message]
    tools: list[ToolSpec]
    max_tokens: int
    effort: str | None
    call_index: int

    def last_tool_results(self) -> list[dict[str, Any]]:
        if not self.messages:
            return []
        content = self.messages[-1].get("content")
        if not isinstance(content, list):
            return []
        return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]

    def called(self, tool_name: str) -> bool:
        for m in self.messages:
            c = m.get("content")
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("name") == tool_name:
                        return True
        return False

    def tool_names(self) -> set[str]:
        return {t.name for t in self.tools}


Policy = Callable[[ScriptedCall], ScriptedTurn]


@dataclass
class ScriptedLLM(LLMClient):
    """Replays `turns`, then falls back to `policy`, then to `default`."""

    turns: list[ScriptedTurn] = field(default_factory=list)
    policy: Policy | None = None
    default: ScriptedTurn = field(
        default_factory=lambda: ScriptedTurn(text="(scripted: no turn configured)")
    )
    calls: int = field(default=0, init=False)
    seen: list[dict[str, Any]] = field(default_factory=list, init=False)

    def complete(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> LLMResponse:
        i = self.calls
        self.calls += 1
        sys_text = system if isinstance(system, str) else "\n".join(
            str(b.get("text", "")) for b in system
        )
        self.seen.append(
            {
                "model": model,
                "n_messages": len(messages),
                "tools": sorted(t.name for t in (tools or [])),
                "effort": effort,
                "system_chars": len(sys_text),
            }
        )
        if i < len(self.turns):
            turn = self.turns[i]
        elif self.policy is not None:
            turn = self.policy(
                ScriptedCall(model, sys_text, messages, list(tools or []), max_tokens, effort, i)
            )
        else:
            turn = self.default
        return turn.to_response(model, i)


def looping_llm(tool: str, payload: dict[str, Any] | None = None) -> ScriptedLLM:
    """A model that never stops. Proves the turn cap actually binds."""
    body = payload or {}
    return ScriptedLLM(
        policy=lambda _c: ScriptedTurn(text="Still gathering.", tool_calls=[(tool, body)])
    )


def build_backend(backend: str = "auto") -> LLMClient:
    """Pick a backend. `auto` uses the real API only if a credential exists."""
    if backend == "scripted":
        return ScriptedLLM()
    if backend == "anthropic":
        return AnthropicLLM()
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return AnthropicLLM()
    return ScriptedLLM()
