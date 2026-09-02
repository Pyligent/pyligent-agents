"""The benchmark harness. Scoring must be free, offline and deterministic."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

from corpus import extractors_in, load_corpus  # noqa: E402
from run import render, score_corpus  # noqa: E402

DOC = """\
"Threshold" means with respect to each party: USD 0.
Net total            824.99
"""


def build(tmp_path, extractions: dict[str, dict], meta: dict | None = None) -> Path:
    root = tmp_path / "corpus"
    d = root / "doc-1"
    (d / "extractions").mkdir(parents=True)
    (d / "source.txt").write_text(DOC, encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(meta or {"source_url": "https://x/1",
                                                     "licence": "public domain"}), encoding="utf-8")
    for name, payload in extractions.items():
        (d / "extractions" / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


def test_a_document_without_extractions_is_skipped(tmp_path):
    root = tmp_path / "corpus"
    (root / "lonely").mkdir(parents=True)
    (root / "lonely" / "source.txt").write_text(DOC, encoding="utf-8")
    assert load_corpus(root) == []


def test_extractions_are_loaded_through_the_same_normaliser_as_the_cli(tmp_path):
    """A pipeline's own shape must work here too, or the benchmark measures
    whoever bothered to reformat their output."""
    root = build(tmp_path, {
        "model-a": {"fields": {"t": {"value": 0, "evidence_quote": "USD 0."}}},
        "model-b": {"t": {"value": 0, "citation": "USD 0."}},
    })
    entries = load_corpus(root)
    assert extractors_in(entries) == ["model-a", "model-b"]
    for e in entries[0].extractions:
        assert e.fields["t"] == {"value": 0, "quote": "USD 0."}


def test_integrity_is_one_when_every_value_is_supported(tmp_path):
    root = build(tmp_path, {"good": {"fields": {
        "threshold": {"value": 0,
                      "quote": '"Threshold" means with respect to each party: USD 0.'},
    }}})
    scores = score_corpus(load_corpus(root))
    assert scores["good"].evidence_integrity == 1.0
    assert scores["good"].findings == 0


def test_a_fabricated_quote_drives_integrity_to_zero(tmp_path):
    root = build(tmp_path, {"bad": {"fields": {
        "threshold": {"value": 0, "quote": "a sentence that is not in the document"},
    }}})
    scores = score_corpus(load_corpus(root))
    assert scores["bad"].evidence_integrity == 0.0
    assert scores["bad"].by_code["FABRICATED_EVIDENCE"] == 1


def test_silent_repair_is_counted_separately_from_fabrication(tmp_path):
    """They are different failures with different owners, and a single
    'hallucination rate' would hide the expensive one."""
    root = build(tmp_path, {"helpful": {"fields": {
        "threshold": {"value": 5_000_000,
                      "quote": '"Threshold" means with respect to each party: USD 0.'},
    }}})
    s = score_corpus(load_corpus(root))["helpful"]
    assert s.by_code["SILENT_REPAIR"] == 1 and s.by_code["FABRICATED_EVIDENCE"] == 0


def test_scoring_is_deterministic(tmp_path):
    root = build(tmp_path, {"m": {"fields": {
        "a": {"value": 824.99, "quote": "Net total            824.99"},
        "b": {"value": 1, "quote": "not present"},
    }}})
    entries = load_corpus(root)
    first = score_corpus(entries)["m"].to_dict()
    assert first == score_corpus(entries)["m"].to_dict()


def test_the_table_states_that_integrity_is_not_accuracy(tmp_path):
    """Overclaiming here would be the same error the tool exists to catch."""
    root = build(tmp_path, {"m": {"fields": {"a": {"value": 824.99,
                                                   "quote": "Net total            824.99"}}}})
    entries = load_corpus(root)
    out = render(entries, score_corpus(entries))
    assert "None of these is accuracy" in out


def test_provenance_is_shown_so_synthetic_data_cannot_pass_as_real(tmp_path):
    """The output has to say where the documents came from. A synthetic
    benchmark presented as a real one is exactly the claim this project
    exists to catch."""
    root = build(tmp_path, {"m": {"fields": {"a": {"value": 1, "quote": "x"}}}},
                 meta={"licence": "synthetic, constructed for testing"})
    entries = load_corpus(root)
    out = render(entries, score_corpus(entries))
    assert "synthetic" in out


def test_a_missing_corpus_says_so(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_corpus(tmp_path / "nope")


# --- coverage: the denominator that stops integrity being gamed ----------------

def test_omitting_a_field_you_would_fail_raises_integrity_but_lowers_coverage(tmp_path):
    """Integrity alone rewards timidity, which is why it is never reported alone.

    A model that answers only the easy fields scores better than one that attempts
    the hard ones. Coverage restores the schema as the denominator, and effective
    integrity combines them into a figure omission cannot inflate.
    """
    from run import SCHEMA_FIELDS

    bold = {"fields": {
        "threshold": {"value": "0",
                      "quote": '"Threshold" means with respect to each party: USD 0.'},
        "base_currency": {"value": "USD", "quote": "A CLAUSE THAT IS NOT PRESENT"},
    }}
    timid = {"fields": {
        "threshold": {"value": "0",
                      "quote": '"Threshold" means with respect to each party: USD 0.'},
    }}
    scores = score_corpus(load_corpus(build(tmp_path, {"bold": bold, "timid": timid})))

    b, t_ = scores["bold"], scores["timid"]
    assert t_.evidence_integrity > b.evidence_integrity, "timidity wins on integrity alone"
    assert t_.coverage < b.coverage, "and loses on coverage — that is the point"
    assert b.expected == t_.expected == len(SCHEMA_FIELDS)


def test_effective_integrity_is_coverage_times_integrity(tmp_path):
    payload = {"fields": {
        "threshold": {"value": "0",
                      "quote": '"Threshold" means with respect to each party: USD 0.'}}}
    s = score_corpus(load_corpus(build(tmp_path, {"m": payload})))["m"]
    assert abs(s.effective_integrity - s.coverage * s.evidence_integrity) < 1e-9


def test_citation_coverage_counts_fields_that_cited_anything(tmp_path):
    payload = {"fields": {
        "threshold": {"value": "0",
                      "quote": '"Threshold" means with respect to each party: USD 0.'},
        "minimum_transfer_amount": {"value": "500000", "quote": ""},
    }}
    s = score_corpus(load_corpus(build(tmp_path, {"m": payload})))["m"]
    assert s.fields == 2 and s.cited == 1
    assert abs(s.citation_coverage - 0.5) < 1e-9


def test_omission_and_fabrication_score_the_same_and_that_is_deliberate(tmp_path):
    """Effective integrity counts supported schema, so silence and invention tie.

    A system emitting four sound fields and one emitting six of which two are
    fabricated both leave four fields supported out of nine. The tie is arithmetic,
    not an oversight, and it is left in place because a fabrication penalty would
    require a coefficient nobody can derive — a worse property in a control than a
    tie a reader can see and reason about.

    The distinction lives in the FABRICATED_EVIDENCE count reported beside it. This
    test exists so that nobody 'fixes' the tie without also deciding what to do about
    that.
    """
    good = {
        "threshold": {"value": "0",
                      "quote": '"Threshold" means with respect to each party: USD 0.'},
        "net_total": {"value": "824.99", "quote": "Net total            824.99"},
    }
    bad = {"value": "999", "quote": "A CLAUSE THAT DOES NOT EXIST ANYWHERE"}

    sound = dict(good)
    shaky = {**good, "rounding": bad, "party_a": bad}
    scores = score_corpus(load_corpus(build(tmp_path, {"sound": sound, "shaky": shaky})))

    s, k = scores["sound"], scores["shaky"]
    assert s.effective_integrity == k.effective_integrity, "the documented tie"
    assert s.evidence_integrity > k.evidence_integrity, "integrity still separates them"
    assert k.by_code["FABRICATED_EVIDENCE"] == 2 and s.by_code["FABRICATED_EVIDENCE"] == 0, (
        "and the fabrication count is where the difference is visible"
    )


def test_citing_nothing_does_not_score_better_than_citing_badly(tmp_path):
    """The hole that `effective integrity` was described as not having.

    A field carrying no citation cannot be flagged — there is nothing to check — so
    counting `emitted - findings` made silence score better than invention: three
    uncited values scored 33.3% where three fabricated ones scored 0%. A figure
    advertised as ungameable by omission was gameable by omitting the citations.

    Effective integrity now counts fields that cited something AND survived the
    check, so an uncited field is worth exactly what it is worth: nothing.
    """
    good = {"threshold": {"value": "0",
                          "quote": '"Threshold" means with respect to each party: USD 0.'}}
    fabricated = {"threshold": {"value": "0", "quote": "A CLAUSE THAT IS NOT PRESENT"}}
    uncited = {"threshold": {"value": "0"}}

    scores = score_corpus(load_corpus(build(
        tmp_path, {"sound": good, "fabricated": fabricated, "uncited": uncited})))

    assert scores["uncited"].effective_integrity == 0.0, "silence scored as support"
    assert scores["fabricated"].effective_integrity == 0.0
    assert scores["sound"].effective_integrity > 0.0
    # Integrity alone still flatters the uncited extractor — which is why the table
    # prints citation coverage next to it.
    assert scores["uncited"].evidence_integrity == 1.0
    assert scores["uncited"].citation_coverage == 0.0
