"""Trellis — harness, loop and graph engineering for production agents.

A trellis is structure a plant grows on. It does not make the plant grow; it
decides what shape the growth can take. That is the argument of this library:

    A multi-agent system is not more model. It is more structure — and structure
    only helps when the task actually needs it.

Three layers, one sentence each:

    harness   owns CONTEXT        what the model sees, and what it may touch
    loop      owns CONTROL        when to stop, what to do when something breaks
    graph     owns COORDINATION   what runs in what order, and what survives a crash

Trellis ships **no tools and no domain**. Your tools are your domain, and a
library that guesses at them is a library you fight.

Quick start:

    from trellis import build_stack
    from trellis.loop import Agent, AgentContract, Budget, ModelSaysDone, no_verification

    stack = build_stack(registry=my_tools)
    agent = Agent(
        stack.harness,
        AgentContract(
            goal="Answer a question about an order.",
            stop=ModelSaysDone(),
            verifier=no_verification("Every figure comes from a tested tool."),
            budget=Budget(max_turns=6, max_usd=0.25, max_seconds=60),
        ),
        system=SYSTEM_PROMPT,
    )
    print(agent.run("Why is order A-1207 late?").answer)

See `examples/` for four worked applications, one per rung of the ladder.
"""

from .config import Settings, get_settings, register_model
from .core import (
    DomainRefusal,
    ErrorClass,
    Phase,
    PermissionTier,
    ToolSpec,
    ToolUse,
    idempotency_key,
    register_error_class,
)
from .runtime import Stack, build_stack

__version__ = "0.1.0"

__all__ = [
    "DomainRefusal", "ErrorClass", "Phase", "PermissionTier", "Settings",
    "Stack", "ToolSpec", "ToolUse", "__version__", "build_stack", "get_settings",
    "idempotency_key", "register_error_class", "register_model",
]
