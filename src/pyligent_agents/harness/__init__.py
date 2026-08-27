"""Layer 1 — the harness. Everything around the model."""

from .client import (
    AnthropicLLM,
    ScriptedCall,
    ScriptedLLM,
    ScriptedTurn,
    build_backend,
    looping_llm,
)
from .context import CompactionEvent, ContextManager, estimate_tokens
from .governor import Governor
from .harness import Harness
from .hooks import (
    HookBus,
    HookPoint,
    ModelCallContext,
    ToolCallContext,
    ToolResultContext,
    Verdict,
    defang_untrusted_content,
    default_hooks,
    redact_secrets,
)
from .memory import Binding, Freshness, MemoryStore, Note, Recalled
from .registry import ToolOutcome, ToolRegistry
from .workspace import Artifact, Workspace

__all__ = [
    "AnthropicLLM", "Artifact", "CompactionEvent", "ContextManager", "Governor",
    "Binding", "Freshness", "Harness", "HookBus", "HookPoint", "MemoryStore",
    "ModelCallContext", "Note", "Recalled",
    "ScriptedCall", "ScriptedLLM", "ScriptedTurn", "ToolCallContext",
    "ToolOutcome", "ToolRegistry", "ToolResultContext", "Verdict",
    "Workspace", "build_backend", "default_hooks", "defang_untrusted_content",
    "estimate_tokens", "looping_llm", "redact_secrets",
]
