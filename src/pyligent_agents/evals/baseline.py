"""Saving and loading baselines, so an eval can detect a regression.

An eval you run once is a demo. An eval you can compare against last week is a
control. The difference is a file on disk and the discipline to update it
deliberately rather than whenever it goes red.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .metrics import Report


def save_baseline(report: Report, path: str | Path, *, note: str = "") -> Path:
    """Write a baseline. Do this when you have *decided* the numbers are good."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {**report.to_dict(), "recorded_at": time.time(), "note": note}
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def load_baseline(path: str | Path) -> dict[str, Any] | None:
    target = Path(path)
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def render_comparison(baseline: dict[str, Any], report: Report) -> str:
    """A side-by-side table. Arrows point the way the change is *good*."""
    base, now = baseline.get("metrics", {}), report.to_dict()["metrics"]
    better_is_lower = {"false_accept_rate", "false_refer_rate"}

    lines = [f"  {'METRIC':<24} {'BASELINE':>9} {'NOW':>9} {'DELTA':>9}"]
    lines.append("  " + "-" * 54)
    for name in sorted(set(base) | set(now)):
        b, n = base.get(name), now.get(name)
        if b is None or n is None:
            continue
        delta = n - b
        good = (delta <= 0) if name in better_is_lower else (delta >= 0)
        mark = "  " if abs(delta) < 1e-9 else ("ok" if good else "!!")
        lines.append(f"  {name:<24} {b:>8.1%} {n:>8.1%} {delta:>+8.1%} {mark}")
    return "\n".join(lines)
