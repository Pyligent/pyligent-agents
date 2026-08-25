"""Governors: the four questions, expressed as numbers that raise.

"What's the spend cap?" has exactly one acceptable answer, and it is not "we
watch the dashboard." A dashboard tells you about the money after you have
spent it.

Four independent limits, because runs fail in four different ways:

    turns     a loop that will not converge
    tokens    a loop that converges but drags the whole window along
    usd       the one Finance asks about
    seconds   a loop blocked on something that will never answer

Whichever binds first stops the run. They are checked **before** the call, not
after — the call you are about to make is the one that turns a bad run into an
expensive one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..core.errors import BudgetExhausted
from ..core.types import Usage


@dataclass
class Governor:
    """Meters a run and refuses to let it continue past its limits."""

    settings: Settings
    max_turns: int
    max_usd: float
    max_seconds: float
    max_tokens: int | None = None

    turns: int = field(default=0, init=False)
    calls: int = field(default=0, init=False)
    spent_usd: float = field(default=0.0, init=False)
    usage: Usage = field(default_factory=Usage, init=False)
    per_model_usd: dict[str, float] = field(default_factory=dict, init=False)
    started_at: float = field(default_factory=time.monotonic, init=False)

    @classmethod
    def from_settings(cls, s: Settings, **overrides: Any) -> Governor:
        return cls(
            settings=s,
            max_turns=overrides.get("max_turns", s.max_turns),
            max_usd=overrides.get("max_usd", s.run_budget_usd),
            max_seconds=overrides.get("max_seconds", s.run_budget_seconds),
            max_tokens=overrides.get("max_tokens"),
        )

    # --- checks -----------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def check(self) -> None:
        """Call before every model call. Raises the specific limit that tripped."""
        if self.spent_usd >= self.max_usd:
            raise BudgetExhausted("spend", self.spent_usd, self.max_usd, " USD")
        if self.elapsed >= self.max_seconds:
            raise BudgetExhausted("wall-clock", self.elapsed, self.max_seconds, "s")
        if self.max_tokens is not None and self.usage.total_input >= self.max_tokens:
            raise BudgetExhausted("tokens", self.usage.total_input, self.max_tokens)

    def check_turn(self, turn: int) -> None:
        if turn > self.max_turns:
            raise BudgetExhausted("turns", turn, self.max_turns)

    # --- accounting -------------------------------------------------------

    def record(self, model: str, usage: Usage) -> float:
        cost = self.price(model, usage)
        self.calls += 1
        self.spent_usd += cost
        self.usage = self.usage + usage
        self.per_model_usd[model] = self.per_model_usd.get(model, 0.0) + cost
        return cost

    def price(self, model: str, usage: Usage) -> float:
        """Tokens to USD. Cache reads at ~0.1x, cache writes at 1.25x."""
        rin, rout = self.settings.price(model)
        billable = usage.input_tokens + usage.cache_creation_input_tokens * 1.25
        cached = usage.cache_read_input_tokens * 0.10
        return ((billable + cached) * rin + usage.output_tokens * rout) / 1_000_000

    # --- reporting --------------------------------------------------------

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.max_usd - self.spent_usd)

    def headroom(self) -> dict[str, float]:
        """How close each limit is, as a fraction. Use it to decide to wrap up."""
        return {
            "turns": self.turns / self.max_turns if self.max_turns else 0.0,
            "usd": self.spent_usd / self.max_usd if self.max_usd else 0.0,
            "seconds": self.elapsed / self.max_seconds if self.max_seconds else 0.0,
        }

    def report(self) -> dict[str, Any]:
        return {
            "turns": self.turns,
            "calls": self.calls,
            "spent_usd": round(self.spent_usd, 6),
            "cap_usd": self.max_usd,
            "remaining_usd": round(self.remaining_usd, 6),
            "elapsed_s": round(self.elapsed, 3),
            "usage": self.usage.to_dict(),
            "per_model_usd": {k: round(v, 6) for k, v in self.per_model_usd.items()},
            "headroom": {k: round(v, 3) for k, v in self.headroom().items()},
        }
