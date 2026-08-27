"""One definition of each evidence check, shared with the `unsourced` CLI.

Two copies of a comparison rule drift. A drifted rule invalidates every number
measured with it, and it does so silently — the tests still pass, both sides
still look right, and the benchmark is quietly wrong for months.

They had already drifted before this was wired: this module knew five
placeholder markers, `unsourced` knew ten.
"""

from __future__ import annotations

import pytest
from unsourced.checks import PLACEHOLDERS, check_field
from unsourced.normalize import contains

from pyligent_agents.verify import (
    GateSet,
    no_placeholder_values,
    no_silent_repair,
    quotes_appear_in_source,
)

SOURCE = (
    'Net total            824.99\n'
    '"Threshold" means with respect to each party: USD 0.\n'
    'Name as shown on document:  Jonathon Whitfield\n'
    '"Eligible Currency" means the Base Currency.\n'
)


def artifact(**fields):
    return {
        "_source_text": SOURCE,
        "fields": {k: {"value": v[0], "evidence_quote": v[1]} for k, v in fields.items()},
    }


def run(check, art):
    passed, message = check(art)
    return passed, message


# --- one definition ------------------------------------------------------


@pytest.mark.parametrize("marker", PLACEHOLDERS)
def test_every_placeholder_unsourced_knows_is_also_rejected_here(marker):
    """The drift that already existed. `-`, `none` and `na` passed this module
    and failed the CLI, so the same artifact got two different verdicts."""
    passed, _ = run(no_placeholder_values(under="fields"),
                    artifact(f=(marker.upper(), "Net total            824.99")))
    assert not passed, f"{marker!r} is a placeholder to unsourced but not here"


def test_quote_matching_uses_the_shared_comparison():
    """Whitespace-normalised, case-insensitive, never fuzzy — defined once."""
    wrapped = "Name as shown on\n   document:  Jonathon Whitfield"
    assert contains(SOURCE, wrapped)
    passed, _ = run(quotes_appear_in_source(under="fields"),
                    artifact(name=("Jonathon Whitfield", wrapped)))
    assert passed


def test_a_paraphrase_is_still_not_a_citation():
    passed, msg = run(quotes_appear_in_source(under="fields"),
                      artifact(t=(0, "The threshold is set at nil for both parties")))
    assert not passed and "not found in source" in msg


# --- the capability this module gained -----------------------------------


def test_silent_repair_is_now_catchable_here():
    """The failure the other evidence gates cannot see.

    Both quotes below are genuine. `evidence_present` passes,
    `evidence_verbatim` passes, and the value is still wrong.
    """
    passed, msg = run(no_silent_repair(),
                      artifact(name=("Jonathan Whitfield",
                                     "Name as shown on document:  Jonathon Whitfield")))
    assert not passed
    assert "names a different value" in msg and "jonathon" in msg


def test_a_recomputed_total_is_caught():
    passed, msg = run(no_silent_repair(),
                      artifact(net_total=(989.99, "Net total            824.99")))
    assert not passed and "824.99" in msg


def test_legitimate_inference_does_not_trip_it():
    """Measured, not hypothetical: without this rule the gate fires on a
    perfect extraction. The quote names no currency, so nothing is
    contradicted."""
    passed, _ = run(no_silent_repair(),
                    artifact(ccy=("USD", '"Eligible Currency" means the Base Currency.')))
    assert passed


def test_a_correct_extraction_passes_every_shared_gate():
    art = artifact(
        net_total=(824.99, "Net total            824.99"),
        threshold=(0, '"Threshold" means with respect to each party: USD 0.'),
        name=("Jonathon Whitfield", "Name as shown on document:  Jonathon Whitfield"),
        ccy=("USD", '"Eligible Currency" means the Base Currency.'),
    )
    report = (GateSet()
              .add("verbatim", "", quotes_appear_in_source(under="fields"))
              .add("no_placeholders", "", no_placeholder_values(under="fields"))
              .add("no_repair", "", no_silent_repair())
              .evaluate(art))
    assert report.passed, [r.message for r in report.failures]


def test_the_gate_and_the_cli_agree_on_the_same_artifact():
    """The property worth having a test for: identical verdict, both paths."""
    entry = ("Jonathan Whitfield", "Name as shown on document:  Jonathon Whitfield")
    art = artifact(name=entry)

    gate_passed, _ = run(no_silent_repair(), art)
    cli_finding = check_field("name", entry[0], entry[1], SOURCE)

    assert gate_passed is (cli_finding is None)
    assert cli_finding is not None and cli_finding.code == "SILENT_REPAIR"
