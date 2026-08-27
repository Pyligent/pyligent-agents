"""The admissible artifact: status, provenance, evidence, and interop.

The paper's claim is that the unit of control must move from the model response
to a typed artifact. These tests hold that claim to three properties: status is
a lifecycle rather than a boolean, provenance is per field, and transitions
produce new records instead of mutating one.
"""

from __future__ import annotations

import json

import pytest

from pyligent_agents.record import (
    Evidence,
    Field,
    Locator,
    Provenance,
    Record,
    ReviewItem,
    SourceRef,
    Status,
)


def _record(**fields) -> Record:
    text = "the Threshold is USD 0 and the MTA is USD 500,000"
    return Record(
        document_id="DOC-1", doc_type="csa", source_text=text,
        source=SourceRef.of(text, ingested_by="html/sec-exhibit"),
        fields={
            name: Field(name, value,
                        evidence=(Evidence(quote=text[:20], locator=Locator(page=4)),),
                        provenance=Provenance(extractor="csa/paragraph-11",
                                              prompt_version="csa/v3", gate_set="csa/v7"))
            for name, value in (fields or {"threshold": 0}).items()
        },
    )


# --- status is a lifecycle, not a boolean --------------------------------


def test_a_new_record_is_proposed_not_valid():
    """Nothing has checked it yet, and the type says so."""
    assert _record().status is Status.PROPOSED


def test_abstained_is_distinct_from_referred():
    """The distinction a boolean destroys.

    REFERRED means a gate failed and a named human decides. ABSTAINED means the
    system could not tell. Collapsing them into False is how a control ends up
    guessing in whichever direction its threshold happens to fall.
    """
    r = _record()
    referred = r.referred(ReviewItem("mta", "transposed?", owner="collateral ops"))
    abstained = r.abstained(ReviewItem("threshold", "clause unreadable"))
    assert referred.status is not abstained.status
    assert {referred.status, abstained.status} == {Status.REFERRED, Status.ABSTAINED}


def test_a_transition_returns_a_new_record_and_leaves_the_original_alone():
    """Replayable, not reconstructed."""
    original = _record()
    admitted = original.certified({"passed": True}).admitted()
    assert original.status is Status.PROPOSED
    assert admitted.status is Status.ADMITTED
    assert admitted.gate_report == {"passed": True}


def test_review_items_accumulate_across_transitions():
    r = (_record()
         .needing_review(ReviewItem("ratings trigger", "not modelled", owner="legal"))
         .referred(ReviewItem("mta", "exceeds threshold", owner="collateral ops")))
    assert len(r.review) == 2
    assert {i.owner for i in r.review} == {"legal", "collateral ops"}


def test_blocking_review_is_separable_from_advisory():
    r = _record().needing_review(
        ReviewItem("a", "must decide", blocking=True),
        ReviewItem("b", "worth knowing", blocking=False))
    assert len(r.review) == 2 and len(r.blocking_review) == 1


# --- provenance is per field ---------------------------------------------


def test_provenance_is_recorded_per_field_not_per_document():
    """'Processed by v2.1' is useless at review time.

    What an auditor asks is which prompt produced *this* value under which gate
    set — and the answer has to survive the document being reprocessed later.
    """
    r = _record(threshold=0, mta=500_000)
    prov = r.fields["threshold"].provenance
    assert prov.prompt_version == "csa/v3" and prov.gate_set == "csa/v7"
    assert prov.to_dict()["extractor"] == "csa/paragraph-11"


def test_a_repaired_value_is_flagged_as_repaired():
    """Semantic repair must be visible, or it is indistinguishable from extraction."""
    f = Field("mta", 500_000, provenance=Provenance(repaired=True))
    assert f.to_dict()["provenance"]["repaired"] is True


# --- evidence carries a locator ------------------------------------------


@pytest.mark.parametrize("locator,expected", [
    (Locator(table="t1", cell="r4c2"), "table t1 cell r4c2"),
    (Locator(dom_path="table.haircuts > tr:nth-child(3)"), "table.haircuts > tr:nth-child(3)"),
    (Locator(page=14), "page 14"),
    (Locator(char_start=10, char_end=40), "chars 10–40"),
])
def test_a_locator_describes_where_the_value_physically_came_from(locator, expected):
    assert locator.describe() == expected


def test_a_plain_text_source_has_an_empty_locator_and_that_is_fine():
    """Locators are populated when the adapter knows. Absence is not a failure."""
    assert Locator().is_empty()
    assert Locator().describe() == "source"


def test_evidence_is_unverified_until_a_gate_says_otherwise():
    """The extractor does not get to mark its own homework."""
    assert Evidence(quote="anything").verified is False


# --- interop: the migration path -----------------------------------------


def test_the_dict_shape_the_existing_gates_read_is_preserved():
    """Locators and provenance are additive, not a migration.

    Every gate shipped before this type existed reads
    artifact["fields"][name]["value"] and ["evidence_quote"]. They must keep
    working untouched.
    """
    artifact = _record(threshold=0).to_artifact()
    entry = artifact["fields"]["threshold"]
    assert entry["value"] == 0
    assert isinstance(entry["evidence_quote"], str) and entry["evidence_quote"]
    assert artifact["_source_text"] and artifact["kind"] == "csa"


def test_an_untyped_extraction_can_be_lifted_without_rewriting_the_extractor():
    legacy = {
        "document_id": "DOC-9", "kind": "kyc",
        "fields": {"applicant_name": {"value": "J Whitfield",
                                      "evidence_quote": "Full legal name: J Whitfield"}},
        "_source_text": "Full legal name: J Whitfield",
        "eligible_collateral": [{"description": "Cash"}],
    }
    r = Record.from_artifact(legacy, provenance=Provenance(model="scripted"))
    assert r.value("applicant_name") == "J Whitfield"
    assert r.quote("applicant_name").startswith("Full legal name")
    assert r.fields["applicant_name"].provenance.model == "scripted"
    # extras survive the round trip, so domain payloads are not silently dropped
    assert r.extras["eligible_collateral"] == [{"description": "Cash"}]
    assert r.to_artifact()["eligible_collateral"] == [{"description": "Cash"}]


def test_the_source_is_identified_by_content_not_by_filename():
    """Two copies of the same agreement under different names are one source."""
    a = SourceRef.of("identical text", uri="/a/one.html")
    b = SourceRef.of("identical text", uri="/b/two.html")
    assert a.sha256 == b.sha256 and a.uri != b.uri


def test_a_record_serialises_for_the_audit_package():
    r = _record().certified({"passed": True}).admitted()
    payload = json.loads(r.to_json())
    assert payload["status"] == "admitted"
    assert payload["fields"]["threshold"]["provenance"]["gate_set"] == "csa/v7"
    assert payload["source"]["ingested_by"] == "html/sec-exhibit"


def test_missing_reports_which_required_fields_are_absent():
    r = _record(threshold=0)
    assert r.missing("threshold", "mta", "base_currency") == ["mta", "base_currency"]
