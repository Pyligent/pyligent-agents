"""Gates, the verifier, and the deterministic domain the examples stand on."""

from __future__ import annotations

import pytest
from level4_invoice_intake.app import invoice_gates
from level4_invoice_intake.policy import HEADER, LINES, TRANSPOSED
from shopdesk import data, money
from shopdesk.errors import RefundNotPermitted

from pyligent_agents.testing import ScriptedTurn, build_test_stack
from pyligent_agents.verify import (
    DocumentVerifier,
    GateSet,
    GateVerifier,
    evidence_gated_extraction,
    no_placeholder_values,
    numeric_range,
    one_of,
    quote_is_in,
    quotes_appear_in_source,
    required_keys,
)

SOURCE = data.SUPPLIER_INVOICE_TEXT
GOOD = {**HEADER, **LINES, "_source_text": SOURCE,
        "_verification": {"approved": True, "evidence": [{"claim": "x"}]}}


# --- the domain -----------------------------------------------------------


def test_a_late_delivery_refunds_the_shipping():
    q = money.quote_refund(data.get_order("A-1207"), fault="seller", today=data.TODAY)
    assert q.refundable == 257.99 and q.shipping_refundable


def test_a_change_of_mind_does_not_refund_shipping_and_charges_restocking():
    order = data.get_order("A-1207")
    q = money.quote_refund(order, fault="customer", today=data.TODAY)
    assert not q.shipping_refundable and q.restocking_fee == 0.0  # unopened
    assert q.refundable == 248.00


def test_outside_the_window_refuses_with_a_reason():
    with pytest.raises(RefundNotPermitted, match="return window"):
        money.quote_refund(data.get_order("A-1310"), fault="customer", today=data.TODAY)


def test_an_already_refunded_order_cannot_be_refunded_again():
    """The bug this prevents is a customer paid twice."""
    with pytest.raises(RefundNotPermitted, match="already been refunded"):
        money.quote_refund(data.get_order("A-1588"), fault="customer", today=data.TODAY)


def test_the_invoice_arithmetic_matches_the_document():
    assert money.invoice_total(
        [{"quantity": 6, "unit_price": 82.50}, {"quantity": 2, "unit_price": 145.00},
         {"quantity": 1, "unit_price": 39.99}],
        tax_rate_pct=20.0) == {"net": 824.99, "tax": 165.00, "gross": 989.99}


def test_an_undelivered_order_cannot_be_refunded():
    with pytest.raises(RefundNotPermitted, match="not been delivered"):
        money.quote_refund(data.get_order("A-1422"), fault="seller", today=data.TODAY)


# --- the generic gate library ---------------------------------------------


def test_required_keys_names_what_is_missing():
    ok, msg = required_keys("a", "b", under="fields")({"fields": {"a": 1}})
    assert not ok and "b" in msg


def test_placeholders_are_caught():
    """A model that cannot find a value often invents a plausible-looking one."""
    ok, msg = no_placeholder_values(under="fields")(
        {"fields": {"po": {"value": "TBD"}, "num": {"value": "NW-1"}}})
    assert not ok and "po" in msg


def test_quotes_must_appear_in_the_source():
    check = quotes_appear_in_source(under="fields")
    assert check({"_source_text": SOURCE, "fields": HEADER["fields"]})[0]
    assert not check({"_source_text": SOURCE,
                      "fields": {"x": {"evidence_quote": "This sentence is not in the doc."}}})[0]


def test_whitespace_is_normalised_but_wording_is_not():
    """PDF text wraps; a paraphrase is still not a citation."""
    assert quote_is_in(SOURCE, "Invoice   number:\n\n  NW-2026-04417")
    assert not quote_is_in(SOURCE, "The invoice number is NW-2026-04417")


def test_numeric_range_and_one_of():
    in_range = numeric_range("fields.tax.value", 0, 100)
    assert in_range({"fields": {"tax": {"value": 20}}})[0]
    assert not in_range({"fields": {"tax": {"value": 120}}})[0]
    assert not in_range({"fields": {"tax": {"value": "twenty"}}})[0]

    assert one_of("urgency", ["low", "high"])({"urgency": "high"})[0]
    assert not one_of("urgency", ["low", "high"])({"urgency": "urgent"})[0]


def test_a_range_gate_cannot_catch_a_unit_error_and_that_is_the_point():
    """A tax rate of 0.2 (a multiplier) instead of 20 (a percentage) sits
    happily inside [0, 100].

    Range gates catch impossible values, not *wrong* ones. That is exactly why
    every gate set needs at least one cross-field check: only
    `lines_sum_to_total` notices that 0.2 makes the arithmetic fail.
    """
    assert numeric_range("fields.tax.value", 0, 100)({"fields": {"tax": {"value": 0.2}}})[0]

    artifact = {**GOOD, "fields": {**HEADER["fields"],
                                   "tax_rate_pct": {**HEADER["fields"]["tax_rate_pct"],
                                                    "value": 0.2}}}
    assert "lines_sum_to_total" in [f.name for f in invoice_gates().evaluate(artifact).failures]


def test_a_gate_that_raises_counts_as_a_failure():
    def boom(_a):
        raise RuntimeError("gate is broken")

    report = GateSet().add("boom", "d", boom).evaluate({})
    assert not report.passed and "RuntimeError" in report.results[0].message


def test_evidence_gated_extraction_bundles_five_gates():
    assert len(evidence_gated_extraction("a", under="fields")) == 5


# --- the domain gate set --------------------------------------------------


def test_a_clean_invoice_passes_every_gate():
    assert invoice_gates().evaluate(GOOD).passed


def test_a_transposed_digit_fails_only_the_arithmetic_gate():
    """Every field present, every type right, evidence real — and wrong."""
    artifact = {**GOOD, **TRANSPOSED}
    report = invoice_gates().evaluate(artifact)
    assert [f.name for f in report.failures] == ["lines_sum_to_total"]
    assert "transposed digit" in report.failures[0].message


def test_a_missing_line_item_also_fails_arithmetic():
    artifact = {**GOOD, "line_items": LINES["line_items"][:2]}
    assert "lines_sum_to_total" in [f.name for f in invoice_gates().evaluate(artifact).failures]


def test_a_due_date_before_the_invoice_date_is_caught():
    fields = {**HEADER["fields"],
              "due_date": {**HEADER["fields"]["due_date"], "value": "1 August 2026"}}
    artifact = {**GOOD, "fields": fields}
    assert "due_after_invoice" in [f.name for f in invoice_gates().evaluate(artifact).failures]


def test_an_unverified_artifact_fails_even_when_perfect():
    artifact = {k: v for k, v in GOOD.items() if k != "_verification"}
    assert "independently_verified" in [f.name for f in invoice_gates().evaluate(artifact).failures]


# --- the verifier ---------------------------------------------------------


def _verifier(text, registry):
    return DocumentVerifier(build_test_stack(lambda c: ScriptedTurn(text=text),
                                             tools=registry).harness)


def test_approval_without_citations_is_not_approval(registry):
    out = _verifier('{"approved": true, "citations": []}', registry).verify(GOOD, {})
    assert not out.approved and any("evidence is required" in r for r in out.reasons)


def test_unparseable_verifier_output_is_a_rejection(registry):
    """Failing open on a control is worse than not having the control."""
    assert not _verifier("Looks fine to me!", registry).verify(GOOD, {}).approved


def test_a_fabricated_quote_rejects_regardless_of_the_verdict(registry):
    out = _verifier(
        '{"approved": true, "citations": [{"claim": "total", "paragraph": "p",'
        ' "verbatim_quote": "The total payable is nine hundred pounds exactly."}]}',
        registry).verify(GOOD, {})
    assert not out.approved
    assert any("absent from the source" in r for r in out.reasons)


def test_the_verifier_never_sees_our_bookkeeping(registry):
    seen = []

    def capture(call):
        seen.append(call.messages[-1]["content"])
        return ScriptedTurn(text='{"approved": false, "citations": [], "reasons": []}')

    stack = build_test_stack(capture, tools=registry)
    DocumentVerifier(stack.harness).verify(
        {"fields": {"a": {"value": 1}}, "_source_text": SOURCE,
         "_verification": {"note": "SENTINEL_PRIOR_VERDICT"},
         "_worker_trace": "SENTINEL_REASONING"}, {})

    prompt = seen[0]
    assert "SENTINEL_PRIOR_VERDICT" not in prompt
    assert "SENTINEL_REASONING" not in prompt


def test_a_gate_verifier_needs_no_model_and_cannot_be_wrong():
    v = GateVerifier(invoice_gates())
    assert v.verify(GOOD, {}).approved
    out = v.verify({**GOOD, "line_items": []}, {})
    assert not out.approved and any("line_items_present" in r for r in out.reasons)
