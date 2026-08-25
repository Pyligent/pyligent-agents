"""One place where every operational knob is decided."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# The most capable model plans and does the least work; cheaper models do the
# bulk. Routing is a design decision, not a default.
DEFAULT_ORCHESTRATOR_MODEL = "claude-opus-5"
DEFAULT_WORKER_MODEL = "claude-sonnet-5"
DEFAULT_CHEAP_MODEL = "claude-haiku-4-5"

# USD per million tokens, (input, output). Anthropic first-party list price
# ships as the default; register your own with `register_model`.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
# An unpriced model is charged at the dearest tier we know about. A new model
# id must never silently look free.
FALLBACK_PRICE = (5.00, 25.00)

CONTEXT_WINDOW: dict[str, int] = {
    "claude-opus-5": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-haiku-4-5": 200_000,
}
DEFAULT_CONTEXT_WINDOW = 200_000


def register_model(model_id: str, *, price_in: float, price_out: float,
                   context_window: int) -> None:
    """Teach Pyligent Agents about a model it does not ship prices for.

    Call this once at startup for any provider or model you use. The alternative
    — letting an unknown model fall through — is handled safely (it prices at
    the dearest tier we know about), but a real number beats a safe guess.
    """
    PRICES[model_id] = (price_in, price_out)
    CONTEXT_WINDOW[model_id] = context_window


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    backend: str = "auto"                       # auto | scripted | anthropic
    orchestrator_model: str = DEFAULT_ORCHESTRATOR_MODEL
    worker_model: str = DEFAULT_WORKER_MODEL
    cheap_model: str = DEFAULT_CHEAP_MODEL

    # --- governors: the four questions, as numbers ---
    max_turns: int = 12
    run_budget_usd: float = 2.00
    run_budget_seconds: float = 600.0
    max_output_tokens: int = 4096

    # --- context management ---
    # Compact when the transcript passes this share of the window.
    compact_at: float = 0.70
    # Keep this many recent turns verbatim when compacting.
    keep_recent_turns: int = 6
    # Tool results longer than this are offloaded to the workspace.
    offload_over_chars: int = 4_000
    # Preview kept inline when a result is offloaded.
    offload_preview_chars: int = 600

    # --- storage ---
    state_dir: Path = field(default_factory=lambda: Path(".pyligent-agents"))

    # Deliberately run a tighter window than the model's own — for testing
    # compaction, for cost control, or to keep a long run honest.
    context_window_override: int | None = None

    effort: str | None = None                   # low | medium | high | xhigh | max

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            backend=(os.getenv("PYLIGENT_AGENTS_BACKEND") or "auto").strip().lower(),
            orchestrator_model=os.getenv("PYLIGENT_AGENTS_ORCHESTRATOR_MODEL",
                                         DEFAULT_ORCHESTRATOR_MODEL),
            worker_model=os.getenv("PYLIGENT_AGENTS_WORKER_MODEL", DEFAULT_WORKER_MODEL),
            cheap_model=os.getenv("PYLIGENT_AGENTS_CHEAP_MODEL", DEFAULT_CHEAP_MODEL),
            max_turns=_i("PYLIGENT_AGENTS_MAX_TURNS", 12),
            run_budget_usd=_f("PYLIGENT_AGENTS_BUDGET_USD", 2.00),
            run_budget_seconds=_f("PYLIGENT_AGENTS_BUDGET_SECONDS", 600.0),
            compact_at=_f("PYLIGENT_AGENTS_COMPACT_AT", 0.70),
            keep_recent_turns=_i("PYLIGENT_AGENTS_KEEP_RECENT", 6),
            offload_over_chars=_i("PYLIGENT_AGENTS_OFFLOAD_OVER", 4_000),
            state_dir=Path(os.getenv("PYLIGENT_AGENTS_STATE_DIR", ".pyligent-agents")),
            context_window_override=_i("PYLIGENT_AGENTS_WINDOW", 0) or None,
            effort=os.getenv("PYLIGENT_AGENTS_EFFORT") or None,
        )

    def price(self, model: str) -> tuple[float, float]:
        return PRICES.get(model, FALLBACK_PRICE)

    def window(self, model: str) -> int:
        if self.context_window_override:
            return self.context_window_override
        return CONTEXT_WINDOW.get(model, DEFAULT_CONTEXT_WINDOW)


def get_settings() -> Settings:
    """Read fresh from the environment every time.

    Not cached: tests and the API both flip env vars between calls, and a
    cached singleton makes that silently not work.
    """
    return Settings.from_env()
