"""The harness: everything around the model, assembled.

    Harness = client + context + tools + hooks + governor + workspace + memory

Nothing above this layer calls a model or a tool directly. That is the whole
point: there is exactly one code path where a request is built, one where a
tool is dispatched, and therefore exactly one place to enforce a policy, meter a
cost, or trace a decision.

The division of labour across the three layers:

    harness   owns CONTEXT   — what the model sees, and what it may touch
    loop      owns CONTROL   — when to stop, what to do when something breaks
    graph     owns COORDINATION — what runs in what order, and what survives a crash
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..core.errors import ErrorClass
from ..core.ids import run_id
from ..core.types import (
    LLMClient,
    LLMResponse,
    PermissionTier,
    Phase,
    ToolSpec,
    ToolUse,
    user_turn,
)
from ..observability.trace import RunLedger
from .client import build_backend
from .context import ContextManager
from .governor import Governor
from .hooks import (
    HookBus,
    ModelCallContext,
    ToolCallContext,
    ToolResultContext,
    Verdict,
    default_hooks,
)
from .memory import MemoryStore
from .registry import ToolOutcome, ToolRegistry
from .workspace import Workspace

READ_ARTIFACT = ToolSpec(
    name="read_artifact",
    description=(
        "Read a stored artifact by handle. Large tool results are stored rather "
        "than pasted into the conversation; you get a preview and a handle. Use "
        "this with an offset to read the rest, a page at a time."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "handle": {"type": "string", "description": "e.g. art_1a2b3c4d"},
            "offset": {"type": "integer", "description": "Character offset. Default 0."},
            "limit": {"type": "integer", "description": "Characters to read. Default 4000."},
        },
        "required": ["handle"],
    },
    tier=PermissionTier.READ_ONLY,
    phases=(Phase.GATHER, Phase.ACT, Phase.VERIFY),
)

SEARCH_TOOLS = ToolSpec(
    name="search_tools",
    description=(
        "Find tools available to you that are not currently loaded. Returns "
        "names and descriptions; the matching tools become callable afterwards. "
        "Use it when you need a capability you cannot see in your tool list."
    ),
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    tier=PermissionTier.READ_ONLY,
    phases=(Phase.GATHER, Phase.ACT, Phase.VERIFY),
)


@dataclass
class Harness:
    settings: Settings = field(default_factory=get_settings)
    client: LLMClient | None = None
    registry: ToolRegistry = field(default_factory=ToolRegistry)
    hooks: HookBus = field(default_factory=default_hooks)
    governor: Governor | None = None
    ledger: RunLedger | None = None
    run: str = field(default_factory=lambda: run_id())
    workspace: Workspace | None = None
    memory: MemoryStore | None = None

    # Deferred tools the model has surfaced via search_tools this run.
    surfaced: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        s = self.settings
        if self.client is None:
            self.client = build_backend(s.backend)
        if self.governor is None:
            self.governor = Governor.from_settings(s)
        if self.ledger is None:
            self.ledger = RunLedger(run_id=self.run, label="harness")
        if self.workspace is None:
            self.workspace = Workspace(Path(s.state_dir) / "runs" / self.run)
        if self.memory is None:
            self.memory = MemoryStore(Path(s.state_dir) / "memory")
        self._install_builtins()

    # --- built-ins --------------------------------------------------------

    def _install_builtins(self) -> None:
        if self.registry.spec("read_artifact") is None:
            self.registry.register(
                READ_ARTIFACT,
                lambda handle, offset=0, limit=4_000: self.workspace.read(
                    handle, offset=int(offset), limit=int(limit)
                ),
            )
        if self.registry.spec("search_tools") is None:
            self.registry.register(SEARCH_TOOLS, self._search_tools)

    def _search_tools(self, query: str) -> dict[str, Any]:
        hits = self.registry.search(query)
        # Surfacing APPENDS to the advertised set; it never swaps the tool list.
        # Swapping would invalidate the cached prefix for the whole run.
        self.surfaced.update(h["name"] for h in hits)
        return {
            "matches": hits,
            "note": "These tools are now callable." if hits else "No match; use what you have.",
        }

    # --- the two operations everything else goes through ------------------

    def call_model(
        self,
        *,
        phase: Phase,
        model: str,
        context: ContextManager,
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
        effort: str | None = None,
        turn: int = 0,
    ) -> LLMResponse:
        """One metered, hooked, traced model call."""
        gov = self.governor
        gov.check()

        mc = self.hooks.run_pre_model(
            ModelCallContext(phase=phase.value, model=model, system=context.system, turn=turn)
        )

        messages = context.snapshot()
        if mc.extra_context:
            # Just-in-time context goes AFTER the cached prefix, never into the
            # system prompt.
            messages.append(user_turn("\n\n".join(mc.extra_context)))

        response = self.client.complete(
            model=model,
            system=context.system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens or self.settings.max_output_tokens,
            effort=effort or self.settings.effort,
        )

        cost = gov.record(model, response.usage)
        self.hooks.run_post_model(mc, response)
        self.ledger.record(
            "model_call",
            phase=phase.value,
            model=model,
            turn=turn,
            stop_reason=response.stop_reason,
            tool_uses=[t.name for t in response.tool_uses],
            cost_usd=round(cost, 6),
            context_pressure=round(context.pressure, 3),
        )
        return response

    def run_tool(
        self,
        tool_use: ToolUse,
        *,
        phase: Phase,
        context: ContextManager | None = None,
        allowed: list[str] | None = None,
        approver=None,
    ) -> ToolOutcome:
        """One gated, hooked, offloaded, traced tool call. Never raises."""
        spec = self.registry.spec(tool_use.name)
        tier = spec.tier if spec else PermissionTier.READ_ONLY

        # 1. PRE_TOOL: gate, rewrite, or deny — before anything runs.
        tc = self.hooks.run_pre_tool(
            ToolCallContext(
                tool_use=tool_use, tier=tier, phase=phase.value,
                arguments=dict(tool_use.input),
            )
        )

        if tc.verdict is Verdict.ASK:
            decision = approver(tc) if approver else None
            if decision is None or not decision.approved:
                reason = decision.reason if decision else tc.reason
                outcome = ToolOutcome(
                    tool_use.id, tool_use.name,
                    f"Denied: {reason} Present the instruction for sign-off "
                    f"instead of retrying.",
                    # PERMISSION, not FATAL: the agent must be able to respond
                    # with "here is what I would send; it needs sign-off"
                    # rather than treating a denial as the end of the run.
                    is_error=True, error_class=ErrorClass.PERMISSION,
                    tier=tier, denied=True, needs_approval=True,
                )
                self._trace_tool(outcome, tc, phase)
                return outcome
            tc.notes.append(f"approved: {decision.reason}")

        if tc.verdict is Verdict.DENY:
            outcome = ToolOutcome(
                tool_use.id, tool_use.name, f"Denied: {tc.reason}",
                is_error=True, error_class=ErrorClass.PERMISSION,
                tier=tier, denied=True,
            )
            self._trace_tool(outcome, tc, phase)
            return outcome

        # 2. Execute. The registry contains the blast radius.
        outcome = self.registry.execute(tool_use, arguments=tc.arguments, allowed=allowed)

        # 3. POST_TOOL: redact, defang untrusted content.
        rc = self.hooks.run_post_tool(
            ToolResultContext(
                tool_name=outcome.tool_name, content=outcome.content,
                is_error=outcome.is_error,
                trusted=self.registry.is_trusted(outcome.tool_name),
            )
        )

        # 4. Offload if it is too big to belong in the transcript.
        body = rc.content
        if context is not None and not outcome.is_error:
            body = context.maybe_offload(body, workspace=self.workspace, source=outcome.tool_name)

        outcome = ToolOutcome(
            outcome.tool_use_id, outcome.tool_name, body,
            is_error=outcome.is_error, error_class=outcome.error_class, tier=tier,
            denied=outcome.denied, needs_approval=outcome.needs_approval,
            notes=tuple(tc.notes + rc.notes), duration_ms=outcome.duration_ms,
        )
        self._trace_tool(outcome, tc, phase)
        return outcome

    def _trace_tool(self, outcome: ToolOutcome, tc: ToolCallContext, phase: Phase) -> None:
        self.ledger.record(
            "tool_call",
            phase=phase.value,
            name=outcome.tool_name,
            arguments=tc.arguments,
            is_error=outcome.is_error,
            error_class=outcome.error_class.value if outcome.error_class else None,
            denied=outcome.denied,
            notes=list(outcome.notes),
            duration_ms=round(outcome.duration_ms, 2),
        )

    # --- helpers ----------------------------------------------------------

    def new_context(self, *, model: str, system: str) -> ContextManager:
        return ContextManager(settings=self.settings, model=model, system=system)

    def tools_for(self, phase: Phase, *, include: list[str] | None = None,
                  tiers=None) -> list[ToolSpec]:
        return self.registry.advertised(
            phase=phase, tiers=tiers, include=include, surfaced=self.surfaced
        )

    def child(self, *, registry: ToolRegistry | None = None, hooks: HookBus | None = None) -> Harness:
        """A subagent harness sharing the governor, ledger and workspace.

        Sharing the governor is deliberate: one budget for the whole run, not
        one per subagent. Ten subagents with their own caps is not a cap.
        """
        sub = Harness(
            settings=self.settings,
            client=self.client,
            registry=registry or self.registry,
            hooks=hooks or self.hooks,
            governor=self.governor,
            ledger=self.ledger,
            run=self.run,
            workspace=self.workspace,
            memory=self.memory,
        )
        sub.surfaced = self.surfaced
        return sub

    def report(self) -> dict[str, Any]:
        return {
            "run": self.run,
            "governor": self.governor.report(),
            "artifacts": self.workspace.index(),
            "surfaced_tools": sorted(self.surfaced),
        }
