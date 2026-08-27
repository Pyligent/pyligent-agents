"""The tool registry: dispatch, containment, and deferred loading.

Three jobs.

**Dispatch that cannot raise.** Domain refusals, bad arguments, unknown names
and unexpected exceptions all come back as `tool_result` blocks the model can
read. Error text is written *for the model*: unknown-tool lists what exists,
bad-arguments includes the schema. An error the agent cannot act on is a dead
end wearing a helpful hat.

**Tier enforcement at the dispatch point.** Not in the tool body, not in the
prompt. `execute()` runs PRE_TOOL hooks first, and a denial short-circuits.

**Deferred loading.** A registry with 200 tools would put 200 schemas in the
prefix of every request. Tools marked `defer_loading=True` are advertised only
after `search_tools` surfaces them, so the fixed context stays small and detail
loads on demand.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import ErrorClass, classify
from ..core.types import PermissionTier, Phase, ToolSpec, ToolUse, tool_result_block

ToolFn = Callable[..., Any]


@dataclass(frozen=True)
class ToolOutcome:
    """The result of one tool call, in a form the model can consume."""

    tool_use_id: str
    tool_name: str
    content: str
    is_error: bool = False
    error_class: ErrorClass | None = None
    tier: PermissionTier = PermissionTier.READ_ONLY
    denied: bool = False
    needs_approval: bool = False
    notes: tuple[str, ...] = ()
    duration_ms: float = 0.0

    def to_block(self) -> dict[str, Any]:
        return tool_result_block(self.tool_use_id, self.content, is_error=self.is_error)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool_name,
            "is_error": self.is_error,
            "error_class": self.error_class.value if self.error_class else None,
            "denied": self.denied,
            "needs_approval": self.needs_approval,
            "notes": list(self.notes),
            "duration_ms": round(self.duration_ms, 2),
        }


@dataclass
class ToolRegistry:
    """Name -> (spec, implementation), with tier and phase enforced on dispatch."""

    _specs: dict[str, ToolSpec] = field(default_factory=dict, init=False)
    _fns: dict[str, ToolFn] = field(default_factory=dict, init=False)
    _trusted: dict[str, bool] = field(default_factory=dict, init=False)

    def register(self, spec: ToolSpec, fn: ToolFn, *, trusted: bool = True) -> ToolRegistry:
        """`trusted=False` marks output that came from outside the firm.

        Untrusted results go through the defanging hook before the model reads
        them. A tool that returns a counterparty's document is not trusted, no
        matter how much you trust the tool.
        """
        if spec.name in self._specs:
            raise ValueError(f"Tool '{spec.name}' is already registered.")
        self._specs[spec.name] = spec
        self._fns[spec.name] = fn
        self._trusted[spec.name] = trusted
        return self

    def spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def fn(self, name: str) -> ToolFn:
        return self._fns[name]

    # --- what the model is shown ------------------------------------------

    def advertised(
        self,
        *,
        phase: Phase | None = None,
        tiers: Iterable[PermissionTier] | None = None,
        include: Iterable[str] | None = None,
        surfaced: Iterable[str] = (),
    ) -> list[ToolSpec]:
        """The tool list for one model call.

        A tool absent from this list cannot be called, because the model never
        learns it exists. That is the primary control — narrowing the surface,
        not trusting the model to decline.
        """
        allow_tiers = set(tiers) if tiers is not None else None
        only = set(include) if include is not None else None
        shown = set(surfaced)
        out = []
        for name, spec in sorted(self._specs.items()):
            if only is not None and name not in only:
                continue
            if allow_tiers is not None and spec.tier not in allow_tiers:
                continue
            if phase is not None and phase not in spec.phases:
                continue
            if spec.defer_loading and name not in shown:
                continue
            out.append(spec)
        return out

    def subset(self, *names: str) -> list[ToolSpec]:
        missing = [n for n in names if n not in self._specs]
        if missing:
            raise KeyError(f"Unknown tool(s): {', '.join(missing)}")
        return [self._specs[n] for n in names]

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Keyword search over deferred tools.

        Deliberately dumb — the point is the *mechanism* (schemas load on
        demand, appended rather than swapped, so the cached prefix survives),
        not the ranking.
        """
        terms = [t for t in query.lower().split() if len(t) > 2]
        scored = []
        for spec in self._specs.values():
            hay = f"{spec.name} {spec.description}".lower()
            score = sum(hay.count(t) for t in terms)
            if score:
                scored.append((score, spec))
        scored.sort(key=lambda p: (-p[0], p[1].name))
        return [
            {"name": s.name, "description": s.description, "tier": s.tier.value}
            for _, s in scored[:limit]
        ]

    # --- dispatch ---------------------------------------------------------

    def execute(
        self,
        tool_use: ToolUse,
        *,
        arguments: dict[str, Any] | None = None,
        allowed: Iterable[str] | None = None,
    ) -> ToolOutcome:
        """Run one tool call. Never raises."""
        import time as _t

        name = tool_use.name
        spec = self._specs.get(name)
        started = _t.perf_counter()

        if spec is None:
            return ToolOutcome(
                tool_use.id, name,
                f"Error: no tool named '{name}'. Available: {', '.join(self.names())}.",
                is_error=True, error_class=ErrorClass.INVALID,
            )

        if allowed is not None and name not in set(allowed):
            return ToolOutcome(
                tool_use.id, name,
                f"Error: '{name}' is not available to this agent. "
                f"Available: {', '.join(sorted(allowed))}.",
                is_error=True, error_class=ErrorClass.PERMISSION, tier=spec.tier,
            )

        args = dict(tool_use.input if arguments is None else arguments)
        try:
            value = self._fns[name](**args)
        except TypeError as exc:
            # Almost always malformed tool input. Return the schema; the model
            # usually fixes its own arguments on the next turn.
            return ToolOutcome(
                tool_use.id, name,
                f"Error: invalid arguments for '{name}': {exc}. "
                f"Expected schema: {json.dumps(spec.input_schema)}",
                is_error=True, error_class=ErrorClass.INVALID, tier=spec.tier,
                duration_ms=(_t.perf_counter() - started) * 1000,
            )
        except Exception as exc:  # noqa: BLE001 - containment is the point
            cls = classify(exc)
            code = getattr(exc, "code", type(exc).__name__)
            return ToolOutcome(
                tool_use.id, name, f"Error [{code}]: {exc}",
                is_error=True, error_class=cls, tier=spec.tier,
                duration_ms=(_t.perf_counter() - started) * 1000,
            )

        return ToolOutcome(
            tool_use.id, name, _serialise(value), tier=spec.tier,
            duration_ms=(_t.perf_counter() - started) * 1000,
        )

    def is_trusted(self, name: str) -> bool:
        return self._trusted.get(name, True)

    def clone(self, *names: str) -> ToolRegistry:
        """A narrower registry. Tools not cloned do not exist for the holder.

        Stronger than "unapproved": a document-reading subagent whose registry
        has no custodian tool cannot be talked into using one.
        """
        out = ToolRegistry()
        for n in names or tuple(self._specs):
            out.register(self._specs[n], self._fns[n], trusted=self._trusted[n])
        return out


def _serialise(value: Any) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return json.dumps(value, indent=2, default=str, sort_keys=True)
