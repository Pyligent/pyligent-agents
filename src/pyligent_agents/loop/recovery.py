"""Recovery: one branch per error class, and no more.

The error taxonomy in `core.errors` exists so this file can be a lookup table
instead of a pile of heuristics. Every failure maps to exactly one action:

    TRANSIENT   -> RETRY     same call, after a backoff
    INVALID     -> OBSERVE   hand the error back; the agent fixes its arguments
    DOMAIN      -> OBSERVE   hand it back; the agent picks another route
    PERMISSION  -> OBSERVE   hand it back; the agent presents for sign-off
    FATAL       -> ESCALATE  stop the loop; a human owns this

Two limits stop recovery becoming its own runaway: a per-tool retry cap, and a
consecutive-failure cap across the whole loop. An agent whose last four tool
calls all failed is not recovering, it is thrashing, and continuing costs money
to learn nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..core.errors import ErrorClass


class Action(str, Enum):
    RETRY = "retry"
    OBSERVE = "observe"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class Recovery:
    action: Action
    reason: str
    backoff_s: float = 0.0


@dataclass
class RecoveryPolicy:
    max_retries_per_tool: int = 2
    max_consecutive_failures: int = 4
    base_backoff_s: float = 0.5

    _retries: dict[str, int] = field(default_factory=dict, init=False)
    _consecutive: int = field(default=0, init=False)

    def on_success(self) -> None:
        self._consecutive = 0

    def decide(self, tool: str, error_class: ErrorClass | None) -> Recovery:
        self._consecutive += 1

        if self._consecutive > self.max_consecutive_failures:
            return Recovery(
                Action.ESCALATE,
                f"{self._consecutive} consecutive tool failures — the agent is "
                f"thrashing, not recovering.",
            )

        if error_class is None or error_class is ErrorClass.FATAL:
            return Recovery(Action.ESCALATE, "unrecoverable failure")

        if error_class is ErrorClass.TRANSIENT:
            n = self._retries.get(tool, 0)
            if n < self.max_retries_per_tool:
                self._retries[tool] = n + 1
                return Recovery(
                    Action.RETRY,
                    f"transient failure, retry {n + 1}/{self.max_retries_per_tool}",
                    self.base_backoff_s * (2**n),
                )
            return Recovery(
                Action.OBSERVE,
                f"'{tool}' still failing after {n} retries; letting the agent "
                f"route around it",
            )

        # INVALID / DOMAIN / PERMISSION: the agent must change something.
        # Retrying identical arguments is a busy-wait that costs money.
        return Recovery(Action.OBSERVE, f"{error_class.value} error returned to the agent")

    def report(self) -> dict[str, object]:
        return {
            "retries_by_tool": dict(self._retries),
            "consecutive_failures": self._consecutive,
        }
