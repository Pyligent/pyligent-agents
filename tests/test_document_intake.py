"""Document intake: one pattern, three domains, one gate that catches each flaw."""

from __future__ import annotations

import pytest

from pyligent_agents.testing import build_test_stack
from pyligent_agents.verify import quote_is_in

from document_intake import app, policy
from document_intake.documents import DOCUMENTS

GENERIC_GATES = 5   # evidence_gated_extraction ships five


def _run(kind, *, flawed=False, fabricate=False, tmp_path=None, registry=None):
    stack = build_test_stack(
        policy.build_policy(kind, flawed=flawed, fabricate=fabricate),
        tools=registry, state_dir=tmp_path)
    graph = app.build_graph(stack.harness, kind, flawed=flawed)
    result = stack.runner(graph).start(f"intake {kind}", {})
    return stack, result, (result.state.get("gate_report") or {})


# --- the fixtures must stay honest ---------------------------------------


@pytest.mark.parametrize("kind", ["csa", "invoice", "kyc"])
@pytest.mark.parametrize("flawed", [False, True])
def test_every_evidence_quote_is_verbatim(kind, flawed):
    """A fixture that quietly stops matching turns the demo into theatre."""
    source = DOCUMENTS[kind].source(flawed=flawed)
    for name, field in policy._PAYLOADS[(kind, flawed)]["fields"].items():
        assert quote_is_in(source, field["evidence_quote"]), f"{kind}/{name}"


@pytest.mark.parametrize("kind", ["csa", "invoice", "kyc"])
def test_verifier_citations_are_verbatim(kind):
    for c in policy.APPROVALS[kind]["citations"]:
        assert quote_is_in(DOCUMENTS[kind].text, c["verbatim_quote"])


def test_the_fabricated_citation_is_genuinely_absent():
    quote = policy.FABRICATED["citations"][0]["verbatim_quote"]
    for spec in DOCUMENTS.values():
        assert not quote_is_in(spec.text, quote)


# --- the clean path ------------------------------------------------------


@pytest.mark.parametrize("kind", ["csa", "invoice", "kyc"])
def test_a_clean_document_is_accepted(kind, tmp_path, registry):
    stack, result, report = _run(kind, tmp_path=tmp_path / kind, registry=registry)
    assert report["passed"], report.get("failed")
    assert result.node_status["accept"] == "done"
    assert result.node_status["refer"] == "skipped"
    assert result.state.get("accepted")["status"] == "accepted_into_system_of_record"


@pytest.mark.parametrize("kind", ["csa", "invoice", "kyc"])
def test_every_document_type_shares_the_five_generic_gates(kind):
    names = [g.name for g in DOCUMENTS[kind].gates().gates[:GENERIC_GATES]]
    assert names == ["required_fields", "no_placeholders", "evidence_present",
                     "evidence_verbatim", "independently_verified"]


def test_each_document_type_adds_its_own_domain_gates():
    counts = {k: len(v.gates()) - GENERIC_GATES for k, v in DOCUMENTS.items()}
    assert counts == {"csa": 2, "invoice": 2, "kyc": 4}


# --- the flaws, and exactly which gate catches each ----------------------


@pytest.mark.parametrize("kind,expected", [
    ("csa", "mta_within_threshold"),
    ("invoice", "lines_sum_to_total"),
    ("kyc", "name_matches_document"),
])
def test_a_flaw_fails_exactly_one_domain_gate(kind, expected, tmp_path, registry):
    """The whole argument of this example.

    Every generic gate passes — required fields, no placeholders, evidence
    present, evidence verbatim, independently verified. Only the cross-field
    gate sees it. If a generic gate also failed, the flaw would not be
    demonstrating anything interesting.
    """
    stack, result, report = _run(kind, flawed=True, tmp_path=tmp_path / kind,
                                 registry=registry)
    assert report["failed"] == [expected]

    generic = report["results"][:GENERIC_GATES]
    assert all(g["passed"] for g in generic), [g["name"] for g in generic if not g["passed"]]

    assert result.node_status["accept"] == "skipped"
    assert result.node_status["refer"] == "done"


def test_the_kyc_flaw_lives_in_the_document_not_the_extraction(tmp_path, registry):
    """The extraction is perfect and every quote is verbatim; the pack is not.

    No amount of extraction quality helps here. Only a cross-field check does.
    """
    assert DOCUMENTS["kyc"].flaw_origin == "document"
    stack, result, report = _run("kyc", flawed=True, tmp_path=tmp_path, registry=registry)

    artifact = result.state.get("artifact")
    assert artifact["fields"]["applicant_name"]["value"] == "Jonathan Alexander Whitfield"
    assert artifact["fields"]["name_on_document"]["value"] == "Jonathon Alexander Whitfield"
    # ...and the extraction of that mismatch is itself fully evidenced.
    assert next(g for g in report["results"] if g["name"] == "evidence_verbatim")["passed"]


def test_the_csa_flaw_keeps_both_quotes_real(tmp_path, registry):
    """Transposed fields, genuine citations. Evidence checking cannot see this."""
    source = DOCUMENTS["csa"].text
    fields = policy.CSA_FLAWED["fields"]
    assert quote_is_in(source, fields["threshold"]["evidence_quote"])
    assert quote_is_in(source, fields["mta"]["evidence_quote"])
    assert fields["mta"]["value"] > fields["threshold"]["value"]


# --- the verifier --------------------------------------------------------


@pytest.mark.parametrize("kind", ["csa", "invoice", "kyc"])
def test_a_fabricated_citation_rejects_any_document(kind, tmp_path, registry):
    stack, result, report = _run(kind, fabricate=True, tmp_path=tmp_path / kind,
                                 registry=registry)
    assert report["failed"] == ["independently_verified"]
    assert result.node_status["refer"] == "done"

    verification = result.state.get("artifact")["_verification"]
    assert verification["approved"] is False, "the verifier said yes; the check said no"


# --- the graph itself ----------------------------------------------------


@pytest.mark.parametrize("kind", ["csa", "invoice", "kyc"])
def test_the_graph_validates_for_every_document_type(kind, registry):
    stack = build_test_stack(policy.build_policy(kind), tools=registry)
    assert app.build_graph(stack.harness, kind).validate()


def test_accepting_the_same_document_twice_is_impossible(tmp_path, registry):
    from pyligent_agents.testing import assert_effects_fire_once

    stack, result, _ = _run("invoice", tmp_path=tmp_path, registry=registry)
    stack.runner(app.build_graph(stack.harness, "invoice")).resume(result.run_id)
    assert_effects_fire_once(stack, result.run_id, expected=1)


def test_the_extractor_holds_no_tools(registry):
    """A document written outside your firm is untrusted input."""
    import inspect

    source = inspect.getsource(app.build_graph)
    assert "tools=()" in source
