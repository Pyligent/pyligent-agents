"""Helpers for testing agents.

The single most useful thing Pyligent Agents gives you is that **agent behaviour is
testable**. Turn caps, error recovery, permission denials, compaction triggers
and idempotency guarantees cannot be tested against a live model: it will behave
differently on the retry and hide the bug.

`ScriptedLLM` is not a mock of an SDK. It is a second implementation of the same
`LLMClient` contract, driven by policies you write. This module is the sugar
that makes writing those policies quick.

    from pyligent_agents.testing import turn, calls, router, looping, build_test_stack

    def policy(call):
        if not call.called("get_order"):
            return calls("get_order", order_id="A-1")
        return turn("Order A-1 shipped on the 3rd.")

    stack = build_test_stack(policy, tools=my_registry)
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .harness.client import ScriptedCall, ScriptedLLM, ScriptedTurn
from .harness.registry import ToolRegistry
from .runtime import Stack, build_stack

Policy = Callable[[ScriptedCall], ScriptedTurn]


# --- turn builders --------------------------------------------------------


def turn(text: str, *, input_tokens: int = 900, output_tokens: int = 160) -> ScriptedTurn:
    """A final answer. The loop will treat this as a completion candidate."""
    return ScriptedTurn(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


def calls(tool: str, /, _text: str = "", **arguments: Any) -> ScriptedTurn:
    """A single tool call."""
    return ScriptedTurn(text=_text, tool_calls=[(tool, arguments)])


def parallel(*tool_calls: tuple[str, dict[str, Any]], text: str = "") -> ScriptedTurn:
    """Several tool calls in one assistant turn.

    Worth exercising: all results must return in ONE user message, and a loop
    that splits them quietly trains the model out of parallel calls.
    """
    return ScriptedTurn(text=text, tool_calls=list(tool_calls))


def refusal(text: str = "Declined.") -> ScriptedTurn:
    """A safety refusal. Exercises the stop_reason-before-content path."""
    return ScriptedTurn(text=text, stop_reason="refusal")


def truncated(text: str = "") -> ScriptedTurn:
    """A response cut off at max_tokens."""
    return ScriptedTurn(text=text, stop_reason="max_tokens")


# --- policy builders ------------------------------------------------------


def sequence(*turns: ScriptedTurn) -> Policy:
    """Play a fixed script, then repeat the last turn forever."""
    script = list(turns) or [turn("(no script)")]

    def _policy(call: ScriptedCall) -> ScriptedTurn:
        return script[min(call.call_index, len(script) - 1)]

    return _policy


def router(routes: dict[str, Policy], default: Policy | None = None) -> Policy:
    """Route on a substring of the system prompt.

    In one graph run an orchestrating node, several workers and a verifier all
    share a client. The system prompt is how a policy tells them apart — which
    is also how prompt-routing works in real fixtures.

        router({
            "You extract invoice fields": extractor_policy,
            "You are an independent reviewer": verifier_policy,
        })
    """
    def _policy(call: ScriptedCall) -> ScriptedTurn:
        for needle, route in routes.items():
            if needle in call.system:
                return route(call)
        if default is not None:
            return default(call)
        return turn("{}")

    return _policy


def looping(tool: str, **arguments: Any) -> Policy:
    """A model that never stops. Proves your turn cap actually binds.

    Every agent you ship should have a test using this.
    """
    return lambda _call: calls(tool, _text="Still working.", **arguments)


def after_pushback(before: ScriptedTurn, after: Policy) -> Policy:
    """Behave one way until the loop pushes back, then another.

    The canonical shape for testing a stop condition: assert the first answer is
    rejected, and that the agent then does the right thing.
    """
    def _policy(call: ScriptedCall) -> ScriptedTurn:
        pushed = any(
            isinstance(m.get("content"), str) and "NOT DONE YET" in m["content"]
            for m in call.messages
        )
        return after(call) if pushed else before

    return _policy


# --- stack builder --------------------------------------------------------


def build_test_stack(
    policy: Policy,
    *,
    tools: ToolRegistry | None = None,
    state_dir: str | Path | None = None,
    budget_usd: float = 5.0,
    **settings: Any,
) -> Stack:
    """A stack wired to a scripted policy, with a throwaway state directory.

    Uses the same `build_stack` production uses — there is no separate test
    harness, because a separate test harness is a harness you are not testing.
    """
    import tempfile
    from dataclasses import replace

    from .config import get_settings

    base = get_settings()
    if settings:
        base = replace(base, **settings)
    return build_stack(
        policy=policy,
        settings=base,
        registry=tools,
        state_dir=state_dir or tempfile.mkdtemp(prefix="pyligent-agents-test-"),
        budget_usd=budget_usd,
    )


def capture_prompts(stack: Stack) -> list[dict[str, Any]]:
    """Record every request the stack sends. Returns a list that fills as it runs.

    Use it to assert on what the model was actually shown — that a verifier
    never saw the producer's reasoning, that tool results came back in one
    message, that the system prompt is byte-stable across turns.
    """
    seen: list[dict[str, Any]] = []
    original = stack.harness.client.complete

    def spy(**kwargs: Any):
        seen.append({
            "model": kwargs.get("model"),
            "system": kwargs.get("system"),
            "messages": kwargs.get("messages"),
            "tools": [t.name for t in (kwargs.get("tools") or [])],
        })
        return original(**kwargs)

    stack.harness.client.complete = spy  # type: ignore[method-assign]
    return seen


# --- assertions -----------------------------------------------------------


def assert_capped(fn: Callable[[], Any], *, within: int | None = None) -> None:
    """Assert a runaway is stopped by a governor rather than running forever."""
    from .core.errors import BudgetExhausted, StopConditionNotMet

    try:
        fn()
    except (StopConditionNotMet, BudgetExhausted) as exc:
        if within is not None and getattr(exc, "turns", within) > within:
            raise AssertionError(f"stopped, but only after {exc}") from exc
        return
    raise AssertionError(
        "the agent finished. If it can finish, this is not a runaway test — use "
        "pyligent_agents.testing.looping() to build a model that never stops."
    )


def assert_effects_fire_once(stack: Stack, run_id: str, *, expected: int = 1) -> None:
    """Assert a workflow instructed the outside world exactly `expected` times.

    Run this after resuming the same run several times. It is the assertion that
    proves your idempotency keys work, and the one worth putting in front of
    stakeholders.
    """
    effects = stack.store.effects(run_id)
    if len(effects) != expected:
        keys = [e["key"] for e in effects]
        raise AssertionError(
            f"expected {expected} effect(s), found {len(effects)}: {keys}. "
            f"A key that changes between attempts (a clock, a uuid, a counter) "
            f"is the usual cause."
        )


def assert_no_tool_reached(stack: Stack, *names: str) -> None:
    """Assert none of the named tools ran — e.g. after a permission denial."""
    ran = {
        e.detail.get("name") for e in stack.ledger.events
        if e.kind == "tool_call" and not e.detail.get("denied") and not e.detail.get("is_error")
    }
    leaked = sorted(set(names) & ran)
    if leaked:
        raise AssertionError(f"these tools executed and should not have: {leaked}")


def tools_used(stack: Stack) -> list[str]:
    """Tool names in call order, for readable assertions."""
    return [e.detail["name"] for e in stack.ledger.events if e.kind == "tool_call"]


def gate_failures(report: Any) -> list[str]:
    return [f.name for f in report.failures]


__all__ = [
    "Policy", "ScriptedCall", "ScriptedTurn", "after_pushback",
    "assert_capped", "assert_effects_fire_once", "assert_no_tool_reached",
    "build_test_stack", "calls", "capture_prompts", "gate_failures", "looping",
    "parallel", "refusal", "router", "sequence", "tools_used", "truncated",
    "turn",
]
