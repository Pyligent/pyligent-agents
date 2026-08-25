"""Stop conditions: composable predicates that decide when a loop is finished.

The model never gets to declare the task done. That is the single pattern that
eliminates the whole category of looks-done-but-isn't bugs, and it only works if
"done" is a predicate a computer evaluates rather than a sentence the model
writes.

`ModelSaysDone` exists because plenty of tasks genuinely end when the model
stops calling tools. It is a legitimate stop condition — it is just an
*explicit* one, chosen and named, rather than the default that happens when
nobody thought about it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .agent import LoopState


@dataclass(frozen=True)
class StopVerdict:
    done: bool
    reason: str

    def __bool__(self) -> bool:
        return self.done


class StopCondition:
    """Base class. Subclasses implement `check`."""

    name: str = "stop"

    def check(self, state: LoopState) -> StopVerdict:  # pragma: no cover - abstract
        raise NotImplementedError

    def __and__(self, other: StopCondition) -> StopCondition:
        return AllOf(self, other)

    def __or__(self, other: StopCondition) -> StopCondition:
        return AnyOf(self, other)

    def describe(self) -> str:
        return self.name


@dataclass(frozen=True)
class ModelSaysDone(StopCondition):
    """The model stopped asking for tools.

    Correct for open-ended analysis where no external check exists. Wrong for
    anything that produces an artifact somebody downstream depends on — use
    `GatesPass` there.
    """

    name: str = "model_says_done"

    def check(self, state: LoopState) -> StopVerdict:
        if state.last_response is not None and not state.last_response.wants_tools:
            return StopVerdict(True, "model ended its turn")
        return StopVerdict(False, "model is still calling tools")


@dataclass(frozen=True)
class GatesPass(StopCondition):
    """Every gate in the set holds against the artifact the loop produced.

    The engineering equivalent of *all tests pass and lint reports zero errors*.
    """

    gates: Any
    name: str = "gates_pass"

    def check(self, state: LoopState) -> StopVerdict:
        artifact = state.artifact
        if artifact is None:
            return StopVerdict(False, "no artifact produced yet")
        report = self.gates.evaluate(artifact)
        state.gate_report = report
        if report.passed:
            return StopVerdict(True, f"all {len(report.results)} gate(s) passed")
        failed = ", ".join(f.name for f in report.failures)
        return StopVerdict(False, f"failing gate(s): {failed}")

    def describe(self) -> str:
        return f"gates_pass({len(self.gates.gates)} gates)"


@dataclass(frozen=True)
class Produced(StopCondition):
    """A named key is present in the artifact and non-empty."""

    key: str
    name: str = "produced"

    def check(self, state: LoopState) -> StopVerdict:
        value = (state.artifact or {}).get(self.key)
        if value:
            return StopVerdict(True, f"'{self.key}' produced")
        return StopVerdict(False, f"'{self.key}' not produced yet")

    def describe(self) -> str:
        return f"produced({self.key})"


@dataclass(frozen=True)
class Predicate(StopCondition):
    """Escape hatch for a one-off condition. Give it a name you can read in a trace."""

    fn: Callable[[LoopState], bool]
    label: str
    name: str = "predicate"

    def check(self, state: LoopState) -> StopVerdict:
        ok = bool(self.fn(state))
        return StopVerdict(ok, f"{self.label}: {'met' if ok else 'not met'}")

    def describe(self) -> str:
        return f"predicate({self.label})"


@dataclass(frozen=True)
class AllOf(StopCondition):
    a: StopCondition
    b: StopCondition
    name: str = "all_of"

    def check(self, state: LoopState) -> StopVerdict:
        for cond in (self.a, self.b):
            v = cond.check(state)
            if not v.done:
                return v
        return StopVerdict(True, f"{self.a.describe()} and {self.b.describe()}")

    def describe(self) -> str:
        return f"({self.a.describe()} AND {self.b.describe()})"


@dataclass(frozen=True)
class AnyOf(StopCondition):
    a: StopCondition
    b: StopCondition
    name: str = "any_of"

    def check(self, state: LoopState) -> StopVerdict:
        va = self.a.check(state)
        if va.done:
            return va
        vb = self.b.check(state)
        if vb.done:
            return vb
        return StopVerdict(False, f"{va.reason}; {vb.reason}")

    def describe(self) -> str:
        return f"({self.a.describe()} OR {self.b.describe()})"
