"""Findings and the report. Schema in SPEC.md §6.

The report is diffable on purpose: no timestamp, no discovery order, no
randomness. Two runs over the same inputs produce identical bytes, so a report
can be committed and a change in it means a change in the extraction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

REPORT_VERSION = 1

CRITICAL = "critical"
WARNING = "warning"

# SPEC.md §5. Severity decides what a human reads first, so it is data, not prose.
SEVERITY: dict[str, str] = {
    "FABRICATED_EVIDENCE": CRITICAL,
    "SILENT_REPAIR": CRITICAL,
    "PLACEHOLDER_VALUE": CRITICAL,
    "EMPTY_VALUE": WARNING,
    "MISSING_EVIDENCE": WARNING,
}

_ORDER = {CRITICAL: 0, WARNING: 1}


@dataclass(frozen=True)
class Finding:
    code: str
    field: str
    message: str
    value: Any = None
    quote: str = ""
    competing: tuple[str, ...] = ()

    @property
    def severity(self) -> str:
        return SEVERITY.get(self.code, WARNING)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code, "severity": self.severity,
            "field": self.field, "value": self.value,
        }
        if self.quote:
            out["quote"] = self.quote
        if self.competing:
            out["competing"] = list(self.competing)
        out["message"] = self.message
        return out


@dataclass(frozen=True)
class Report:
    findings: tuple[Finding, ...] = ()
    fields_checked: int = 0
    source_sha256: str = ""
    source_chars: int = 0
    notes: tuple[str, ...] = ()
    tool: str = "evidence-check 0.2.0"

    @property
    def critical(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == CRITICAL)

    @property
    def ok(self) -> bool:
        return not self.critical

    def by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.code] = counts.get(f.code, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict[str, Any]:
        ordered = sorted(self.findings, key=lambda f: (_ORDER[f.severity], f.field, f.code))
        return {
            "report_version": REPORT_VERSION,
            "tool": self.tool,
            "source": {"sha256": self.source_sha256, "chars": self.source_chars},
            "summary": {
                "fields": self.fields_checked,
                "findings": len(self.findings),
                "critical": len(self.critical),
                "by_code": self.by_code(),
            },
            "notes": list(self.notes),
            "findings": [f.to_dict() for f in ordered],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False, default=str)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
