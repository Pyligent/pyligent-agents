"""Layer 2 — the loop.

    gather context -> take action -> verify the work -> repeat

Three phases, and the third is the one most implementations omit. Without it,
"done" means *the model stopped calling tools*, which is an opinion. With it,
the model proposing that it is finished is only a **candidate**: the loop
extracts an artifact, runs a verify pass, evaluates the stop condition, and —
if the condition does not hold — hands the specific failure back and keeps
going.

Everything the loop does with the outside world goes through the harness, so
context management, permissions, redaction and metering are not this file's
problem. This file owns exactly one thing: control.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import BudgetExhausted, StopConditionNotMet
from ..core.types import Message, PermissionTier, Phase, ToolSpec, assistant_turn, tool_result_turn
from ..harness.context import ContextManager
from ..harness.harness import Harness
from ..harness.registry import ToolOutcome
from .contract import AgentContract, OnFailure, VerifierVerdict
from .recovery import Action, RecoveryPolicy
from .stop import StopVerdict

ArtifactExtractor = Callable[["LoopState"], dict[str, Any]]

VERIFY_INSTRUCTION = """\
You are checking work that has just been completed, before it is released.

Re-read the goal and the result. Answer only:
  - does the result actually answer the goal?
  - is every figure traceable to a tool result rather than asserted?
  - is anything claimed that was not established?

If it is sound, reply exactly: VERIFIED
Otherwise reply with the specific problem, in one sentence. Do not fix it
yourself and do not restate the work."""


@dataclass
class LoopState:
    """Everything a stop condition or extractor is allowed to look at."""

    goal: str
    turn: int = 0
    phase: Phase = Phase.GATHER
    last_response: Any | None = None
    outcomes: list[ToolOutcome] = field(default_factory=list)
    artifact: dict[str, Any] | None = None
    gate_report: Any | None = None
    verdicts: list[VerifierVerdict] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)

    @property
    def answer(self) -> str:
        return getattr(self.last_response, "text", "") or ""

    @property
    def failed_tool_calls(self) -> int:
        return sum(1 for o in self.outcomes if o.is_error)

    def tool_output(self, name: str) -> str | None:
        """Most recent successful output of a named tool."""
        for o in reversed(self.outcomes):
            if o.tool_name == name and not o.is_error:
                return o.content
        return None


@dataclass
class AgentResult:
    goal: str
    ok: bool
    answer: str
    artifact: dict[str, Any]
    turns: int
    stop_reason: str
    outcomes: list[ToolOutcome] = field(default_factory=list)
    verdicts: list[VerifierVerdict] = field(default_factory=list)
    gate_report: Any | None = None
    context_report: dict[str, Any] = field(default_factory=dict)
    degraded: bool = False

    @property
    def tool_calls(self) -> int:
        return len(self.outcomes)

    @property
    def failed_tool_calls(self) -> int:
        return sum(1 for o in self.outcomes if o.is_error)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "ok": self.ok,
            "answer": self.answer,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
            "stop_reason": self.stop_reason,
            "degraded": self.degraded,
            "tools_used": [o.tool_name for o in self.outcomes],
            "verdicts": [v.to_dict() for v in self.verdicts],
            "gates": self.gate_report.to_dict() if self.gate_report else None,
            "context": self.context_report,
        }


def default_extractor(state: LoopState) -> dict[str, Any]:
    return {"answer": state.answer}


class Agent:
    """A contract, a harness, and a loop. Nothing else."""

    def __init__(
        self,
        harness: Harness,
        contract: AgentContract,
        *,
        model: str | None = None,
        system: str = "",
        tools: list[str] | None = None,
        extractor: ArtifactExtractor = default_extractor,
        recovery: RecoveryPolicy | None = None,
        self_check: bool = False,
        effort: str | None = None,
        name: str = "agent",
    ):
        self.h = harness
        self.contract = contract
        self.name = name
        self.model = model or harness.settings.worker_model
        self.system = system
        self.tool_names = tools
        self.extractor = extractor
        self.recovery = recovery or RecoveryPolicy()
        # An in-loop self-check is cheap and catches sloppiness. It is NOT
        # verification: same model, same blind spots. Real verification is the
        # contract's verifier, which sees the artifact and not the reasoning.
        self.self_check = self_check
        self.effort = effort

        # Apply the contract's budget to this run's governor.
        for k, v in contract.budget.as_overrides().items():
            if v is not None:
                setattr(harness.governor, k, v)

    # --- the loop ---------------------------------------------------------

    def run(self, task: str, *, history: list[Message] | None = None) -> AgentResult:
        h = self.h
        ctx = h.new_context(model=self.model, system=self.system or self.contract.goal)
        for m in history or []:
            ctx.append(m)
        ctx.append_user(task)

        state = LoopState(goal=self.contract.goal)
        h.ledger.record("agent_start", agent=self.name, **self.contract.summary())

        try:
            return self._loop(ctx, state)
        except BudgetExhausted as exc:
            h.ledger.error(message=str(exc), resource=exc.resource)
            return self._fail(state, ctx, f"budget:{exc.resource}", exc)
        except StopConditionNotMet as exc:
            h.ledger.error(message=str(exc))
            return self._fail(state, ctx, "stop_condition_not_met", exc)

    def _loop(self, ctx: ContextManager, state: LoopState) -> AgentResult:
        h = self.h
        max_turns = self.contract.budget.max_turns

        for turn in range(1, max_turns + 1):
            state.turn = turn
            h.governor.turns = turn

            # Context hygiene runs before the call, not after we have blown the
            # window and got a 400.
            if ctx.should_compact():
                event = ctx.compact(h.client, turn=turn)
                if event:
                    h.ledger.record("compaction", **event.to_dict())

            state.phase = Phase.ACT
            response = h.call_model(
                phase=state.phase, model=self.model, context=ctx,
                tools=self._tools(Phase.ACT), effort=self.effort, turn=turn,
            )
            state.last_response = response

            if response.stop_reason == "refusal":
                return self._fail(state, ctx, "refusal", None)

            if response.wants_tools:
                ctx.append(assistant_turn(response))
                blocks, escalate = self._dispatch(response, ctx, state)
                ctx.append(tool_result_turn(blocks))
                if escalate:
                    return self._fail(state, ctx, "recovery_escalated", None)
                continue

            # --- the model believes it is finished. It is a candidate. ---
            ctx.append(assistant_turn(response))
            state.artifact = self.extractor(state)

            state.phase = Phase.VERIFY
            verdict = self._verify(ctx, state)
            if verdict is not None and not verdict.approved:
                self._push_back(ctx, state, "; ".join(verdict.reasons) or "verification failed")
                continue

            stop: StopVerdict = self.contract.stop.check(state)
            h.ledger.record(
                "stop_check", turn=turn, done=stop.done, reason=stop.reason,
                condition=self.contract.stop.describe(),
            )
            if stop.done:
                return AgentResult(
                    goal=state.goal, ok=True, answer=state.answer,
                    artifact=state.artifact or {}, turns=turn,
                    stop_reason=stop.reason, outcomes=state.outcomes,
                    verdicts=state.verdicts, gate_report=state.gate_report,
                    context_report=ctx.report(),
                )

            # Not done, and we know exactly why. Say so and keep going.
            self._push_back(ctx, state, stop.reason)

        raise StopConditionNotMet(
            self.contract.stop.describe(), max_turns,
            state.feedback[-1] if state.feedback else "",
        )

    # --- phases -----------------------------------------------------------

    def _tools(self, phase: Phase) -> list[ToolSpec]:
        return self.h.tools_for(phase, include=self.tool_names)

    def _dispatch(
        self, response: Any, ctx: ContextManager, state: LoopState
    ) -> tuple[list[dict[str, Any]], bool]:
        """Execute this turn's tool calls. All results return in ONE message."""
        blocks: list[dict[str, Any]] = []
        escalate = False

        for use in response.tool_uses:
            outcome = self.h.run_tool(
                use, phase=state.phase, context=ctx, allowed=self.tool_names
            )
            state.outcomes.append(outcome)

            if not outcome.is_error:
                self.recovery.on_success()
                blocks.append(outcome.to_block())
                continue

            decision = self.recovery.decide(outcome.tool_name, outcome.error_class)
            self.h.ledger.record(
                "recovery", tool=outcome.tool_name, action=decision.action.value,
                reason=decision.reason,
                error_class=outcome.error_class.value if outcome.error_class else None,
            )

            if decision.action is Action.RETRY:
                if decision.backoff_s:
                    time.sleep(min(decision.backoff_s, 2.0))
                retried = self.h.run_tool(
                    use, phase=state.phase, context=ctx, allowed=self.tool_names
                )
                state.outcomes.append(retried)
                blocks.append(retried.to_block())
                if not retried.is_error:
                    self.recovery.on_success()
                continue

            if decision.action is Action.ESCALATE:
                escalate = True

            # OBSERVE (and escalation too): the model always sees what happened.
            blocks.append(outcome.to_block())

        return blocks, escalate

    def _verify(self, ctx: ContextManager, state: LoopState) -> VerifierVerdict | None:
        """Two different checks, and they are not interchangeable."""
        verdict: VerifierVerdict | None = None

        # (a) Cheap same-model self-check. Catches carelessness, not blind spots.
        if self.self_check:
            probe = self.h.new_context(model=self.model, system=VERIFY_INSTRUCTION)
            probe.append_user(
                f"GOAL\n{state.goal}\n\nRESULT\n{state.answer}\n\n"
                f"TOOLS USED\n{[o.tool_name for o in state.outcomes]}"
            )
            r = self.h.call_model(
                phase=Phase.VERIFY, model=self.model, context=probe,
                tools=self.h.tools_for(Phase.VERIFY, tiers=[PermissionTier.READ_ONLY]),
                max_tokens=400, turn=state.turn,
            )
            if "VERIFIED" not in r.text.upper():
                verdict = VerifierVerdict(False, (f"self-check: {r.text.strip()[:300]}",))
                state.verdicts.append(verdict)
                return verdict

        # (b) The real one: a verifier that never saw how the work was done.
        v = self.contract.verifier.verify(
            state.artifact or {}, {"goal": state.goal, "turn": state.turn}
        )
        state.verdicts.append(v)
        self.h.ledger.record(
            "verification", approved=v.approved, reasons=list(v.reasons),
            evidence_count=len(v.evidence),
        )
        return v if not v.approved else verdict

    def _push_back(self, ctx: ContextManager, state: LoopState, reason: str) -> None:
        """Tell the agent precisely what is missing. Never just 'try again'."""
        state.feedback.append(reason)
        ctx.append_user(
            f"NOT DONE YET. The completion check failed: {reason}\n"
            f"Address that specific point and continue. Do not restate work "
            f"you have already done."
        )
        self.h.ledger.record("push_back", turn=state.turn, reason=reason)

    # --- failure ----------------------------------------------------------

    def _fail(
        self, state: LoopState, ctx: ContextManager, stop_reason: str, exc: Exception | None
    ) -> AgentResult:
        policy = self.contract.on_failure

        if policy is OnFailure.DEGRADE:
            self.h.ledger.record("degraded", stop_reason=stop_reason)
            return AgentResult(
                goal=state.goal, ok=False,
                answer=state.answer or "(degraded to a safe default)",
                artifact=dict(self.contract.degrade_to or {}), turns=state.turn,
                stop_reason=stop_reason, outcomes=state.outcomes,
                verdicts=state.verdicts, gate_report=state.gate_report,
                context_report=ctx.report(), degraded=True,
            )

        if exc is not None:
            raise exc
        return AgentResult(
            goal=state.goal, ok=False, answer=state.answer,
            artifact=state.artifact or {}, turns=state.turn, stop_reason=stop_reason,
            outcomes=state.outcomes, verdicts=state.verdicts,
            gate_report=state.gate_report, context_report=ctx.report(),
        )
