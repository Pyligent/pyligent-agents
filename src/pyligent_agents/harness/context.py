"""Context engineering: the part that decides what the model actually sees.

A long-running agent has three ways to lose: it runs out of window, it fills the
window with noise, or it pays full price on every turn for something it read
once. This module addresses all three with two mechanisms and one measurement.

**Offloading** (spatial). A tool result over the threshold is written to the
workspace and replaced inline with a preview plus a handle. The model reads the
rest on demand.

**Compaction** (temporal). When the transcript passes a share of the window,
older turns are summarised into a single synthetic exchange and the recent tail
is kept verbatim. Compaction is lossy by design; what makes it safe is that the
*durable* record lives in the graph store, not in the transcript.

**Estimation.** `estimate_tokens` is a cheap ~4-chars-per-token heuristic used
for triggering decisions. Real usage comes back from the API and is what the
governor bills against. Never use the estimate for money.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..core.types import LLMClient, Message, user_turn

COMPACTION_PROMPT = """\
You are compacting an agent transcript so the agent can keep working.

Write a dense factual summary of what has happened so far. Preserve, verbatim
where they appear:
  - every identifier, amount, date and reference already established
  - every decision taken and the reason for it
  - every tool that failed, and why
  - anything still outstanding

Do not add commentary, do not speculate, and do not soften uncertainty. This
summary REPLACES the turns it covers; a fact you drop is a fact the agent loses.

Artifact handles remain valid — list any that are still relevant."""


def estimate_tokens(value: Any) -> int:
    """Cheap heuristic for triggering decisions only. Never for billing."""
    if isinstance(value, str):
        return max(1, len(value) // 4)
    return max(1, len(json.dumps(value, default=str)) // 4)


@dataclass
class CompactionEvent:
    turn: int
    before_tokens: int
    after_tokens: int
    turns_folded: int

    @property
    def saved(self) -> int:
        return self.before_tokens - self.after_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "before_tokens": self.before_tokens,
            "after_tokens": self.after_tokens,
            "saved_tokens": self.saved,
            "turns_folded": self.turns_folded,
        }


@dataclass
class ContextManager:
    """Owns the message list. Nothing else appends to it directly."""

    settings: Settings
    model: str
    system: str
    messages: list[Message] = field(default_factory=list)
    compactions: list[CompactionEvent] = field(default_factory=list)
    offloaded: int = 0

    # --- measurement ------------------------------------------------------

    @property
    def window(self) -> int:
        return self.settings.window(self.model)

    def size_tokens(self) -> int:
        return estimate_tokens(self.system) + sum(
            estimate_tokens(m.get("content")) for m in self.messages
        )

    @property
    def pressure(self) -> float:
        """Share of the window in use. The number to put on a dashboard."""
        return self.size_tokens() / self.window

    # --- appending --------------------------------------------------------

    def append(self, message: Message) -> None:
        self.messages.append(message)

    def append_user(self, text: str) -> None:
        self.messages.append(user_turn(text))

    def snapshot(self) -> list[Message]:
        """What gets sent. A copy, so callers cannot mutate history."""
        return [dict(m) for m in self.messages]

    # --- offloading -------------------------------------------------------

    def maybe_offload(self, body: str, *, workspace, source: str) -> str:
        """Return either the body, or a preview plus a handle.

        The replacement text is written for the model: it says exactly how to
        get the rest, so a truncated result never becomes a dead end.
        """
        limit = self.settings.offload_over_chars
        if len(body) <= limit:
            return body

        art = workspace.put(
            body,
            kind="tool_result",
            source=source,
            preview_chars=self.settings.offload_preview_chars,
        )
        self.offloaded += 1
        return (
            f"[Result was {art.total_chars:,} characters and has been stored as "
            f"artifact {art.handle}. First {len(art.preview):,} characters follow; "
            f"call read_artifact(handle=\"{art.handle}\", offset=N) for the rest.]\n\n"
            f"{art.preview}"
        )

    # --- compaction -------------------------------------------------------

    def should_compact(self) -> bool:
        return self.pressure >= self.settings.compact_at

    def compact(self, client: LLMClient, *, turn: int, model: str | None = None) -> CompactionEvent | None:
        """Fold older turns into one summary; keep the recent tail verbatim.

        Two invariants make this safe to run mid-loop:

        1. The **first user turn survives**. It carries the goal, and an agent
           that forgets its goal at turn 30 is worse than one that runs out of
           window.
        2. We never split a tool_use from its tool_result. Compaction cuts on a
           turn boundary that leaves no orphaned tool call, or it does nothing.
        """
        keep = self.settings.keep_recent_turns
        if len(self.messages) <= keep + 2:
            return None

        cut = self._safe_cut(len(self.messages) - keep)
        if cut <= 1:
            return None

        before = self.size_tokens()
        head, folded, tail = self.messages[:1], self.messages[1:cut], self.messages[cut:]

        transcript = json.dumps(folded, default=str)[:120_000]
        summary = client.complete(
            model=model or self.settings.cheap_model,
            system=COMPACTION_PROMPT,
            messages=[user_turn(transcript)],
            max_tokens=1_500,
        )

        self.messages = [
            *head,
            {"role": "assistant", "content": [{"type": "text", "text": "(context compacted)"}]},
            user_turn(f"SUMMARY OF EARLIER WORK\n{summary.text}"),
            *tail,
        ]
        event = CompactionEvent(turn, before, self.size_tokens(), len(folded))
        self.compactions.append(event)
        return event

    def _safe_cut(self, want: int) -> int:
        """Move the cut later until it leaves no orphaned tool_use.

        A transcript that ends an assistant turn containing tool_use, with the
        matching tool_result on the other side of the cut, is a 400 on the very
        next request. This is the bug that makes people give up on compaction.
        """
        cut = max(1, min(want, len(self.messages)))
        while cut < len(self.messages) and self._has_open_tool_use(self.messages[:cut]):
            cut += 1
        return cut

    @staticmethod
    def _has_open_tool_use(msgs: list[Message]) -> bool:
        opened: set[str] = set()
        for m in msgs:
            c = m.get("content")
            if not isinstance(c, list):
                continue
            for b in c:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    opened.add(str(b.get("id")))
                elif b.get("type") == "tool_result":
                    opened.discard(str(b.get("tool_use_id")))
        return bool(opened)

    def report(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "window": self.window,
            "tokens_estimated": self.size_tokens(),
            "pressure": round(self.pressure, 4),
            "messages": len(self.messages),
            "offloaded_results": self.offloaded,
            "compactions": [c.to_dict() for c in self.compactions],
        }
