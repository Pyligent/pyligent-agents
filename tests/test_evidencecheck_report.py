"""SPEC.md §5–§7 — severity, report shape, and determinism."""

from __future__ import annotations

import json

from evidencecheck import check
from evidencecheck.report import REPORT_VERSION

SOURCE = "Net total 824.99\nTOTAL DUE GBP 989.99\n"
EXTRACTION = {
    "z_repaired":   {"value": 989.99, "quote": "Net total 824.99"},
    "a_fabricated": {"value": 1.0, "quote": "not in the document"},
    "m_clean":      {"value": 824.99, "quote": "Net total 824.99"},
    "b_empty":      {"value": None, "quote": "Net total 824.99"},
}


def test_the_report_carries_its_schema_version():
    assert check(SOURCE, EXTRACTION).to_dict()["report_version"] == REPORT_VERSION == 1


def test_findings_sort_by_severity_then_field_never_by_discovery_order():
    """§6. Whoever reads this at 3am reads the critical ones first."""
    findings = check(SOURCE, EXTRACTION).to_dict()["findings"]
    severities = [f["severity"] for f in findings]
    assert severities == sorted(severities, key=lambda s: 0 if s == "critical" else 1)
    critical_fields = [f["field"] for f in findings if f["severity"] == "critical"]
    assert critical_fields == sorted(critical_fields)


def test_the_summary_counts_match_the_findings():
    d = check(SOURCE, EXTRACTION).to_dict()
    assert d["summary"]["fields"] == 4
    assert d["summary"]["findings"] == len(d["findings"])
    assert d["summary"]["critical"] == sum(
        1 for f in d["findings"] if f["severity"] == "critical")
    assert sum(d["summary"]["by_code"].values()) == len(d["findings"])


def test_the_report_is_byte_identical_across_runs():
    """§7. No timestamp, no randomness, no discovery order — so it diffs.

    A report you can commit is a report where a change means the extraction
    changed, which is the only way this is usable in CI.
    """
    a = check(SOURCE, EXTRACTION).to_json()
    b = check(SOURCE, EXTRACTION).to_json()
    assert a == b
    assert "timestamp" not in a and "generated_at" not in a


def test_the_source_is_identified_by_content():
    d = check(SOURCE, EXTRACTION).to_dict()["source"]
    assert len(d["sha256"]) == 64 and d["chars"] == len(SOURCE)
    # Same text under a different name is the same source.
    assert d["sha256"] == check(SOURCE, {}).to_dict()["source"]["sha256"]


def test_ok_is_false_when_anything_critical_survives():
    assert not check(SOURCE, EXTRACTION).ok
    assert check(SOURCE, {"clean": EXTRACTION["m_clean"]}).ok


def test_the_report_is_json_serialisable():
    json.loads(check(SOURCE, EXTRACTION).to_json())
