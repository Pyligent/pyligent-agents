"""The agent contract: the four questions, as a type you cannot skip.

    1. What is the stop condition?
    2. Who verifies before it ships?
    3. What is the spend cap?
    4. What happens when something fails?

Every team agrees these matter. Most systems answer them in a design doc that
drifts from the code within a quarter. Here they are constructor arguments —
you cannot build an `Agent` without answering all four, and `no_verification()`
forces you to write down *why* when the answer is "nobody".

That is the entire trick: make the thing you would otherwise forget into the
thing you cannot omit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from ..core.errors import ContractViolation
from .stop import StopCondition


@runtime_checkable
class Verifier(Protocol):
    """Anything that can judge an artifact it did not produce."""

    def verify(self, artifact: dict[str, Any], context: dict[str, Any]) -> VerifierVerdict: ...


@dataclass(frozen=True)
class VerifierVerdict:
    approved: bool
    reasons: tuple[str, ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "reasons": list(self.reasons),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class NoVerification:
    """An explicit, justified decision not to verify.

    Legitimate at Level 1, where the output vocabulary is closed and validated
    deterministically. Not legitimate because you ran out of time.
    """

    reason: str

    def verify(self, artifact: dict[str, Any], context: dict[str, Any]) -> VerifierVerdict:
        return VerifierVerdict(True, (f"verification waived: {self.reason}",))


def no_verification(reason: str) -> NoVerification:
    if not reason or len(reason) < 12:
        raise ContractViolation(
            "no_verification() needs a real reason. If you cannot write one "
            "sentence explaining why this output does not need checking, it "
            "needs checking."
        )
    return NoVerification(reason)


class OnFailure(str, Enum):
    ESCALATE = "escalate"   # stop, raise, hand to a human
    DEGRADE = "degrade"     # return a safe default and say so
    RETRY_ONCE = "retry_once"


@dataclass(frozen=True)
class Budget:
    max_turns: int = 12
    max_usd: float = 2.00
    max_seconds: float = 600.0
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ContractViolation(
                "max_turns must be >= 1. An uncapped loop is the bug, not a feature."
            )
        if self.max_usd <= 0:
            raise ContractViolation("max_usd must be > 0. 'No cap' is not a cap.")

    def as_overrides(self) -> dict[str, Any]:
        return {
            "max_turns": self.max_turns,
            "max_usd": self.max_usd,
            "max_seconds": self.max_seconds,
            "max_tokens": self.max_tokens,
        }


@dataclass(frozen=True)
class AgentContract:
    """What this agent is for, and what governs it."""

    goal: str
    stop: StopCondition
    verifier: Verifier | NoVerification
    budget: Budget = field(default_factory=Budget)
    on_failure: OnFailure = OnFailure.ESCALATE
    degrade_to: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ContractViolation("An agent needs a stated goal.")
        if self.on_failure is OnFailure.DEGRADE and self.degrade_to is None:
            raise ContractViolation(
                "on_failure=DEGRADE requires degrade_to — the safe default to "
                "return. 'Degrade' with nothing to degrade to is just a crash."
            )

    @property
    def verified(self) -> bool:
        return not isinstance(self.verifier, NoVerification)

    def summary(self) -> dict[str, Any]:
        """The four answers, printable. Put this in your run log and your PR."""
        return {
            "goal": self.goal,
            "stop_condition": self.stop.describe(),
            "verifier": (
                type(self.verifier).__name__
                if self.verified
                else f"none — {self.verifier.reason}"
            ),
            "budget": {
                "turns": self.budget.max_turns,
                "usd": self.budget.max_usd,
                "seconds": self.budget.max_seconds,
            },
            "on_failure": self.on_failure.value,
        }
