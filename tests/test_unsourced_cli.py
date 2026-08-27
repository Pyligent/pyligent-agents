"""The ten-minute path: what a stranger runs, and what they get back."""

from __future__ import annotations

import json

import pytest

from unsourced.cli import main, normalise_extraction

CSA = """<html><body>
<p>"Base Currency" means United States Dollars (USD).</p>
<p>"Threshold" means with respect to each party: USD 0.</p>
<table><tr><td>US Treasury obligations</td><td>98%</td></tr></table>
</body></html>"""


@pytest.fixture
def doc(tmp_path):
    p = tmp_path / "csa.html"
    p.write_text(CSA)
    return p


def write(tmp_path, payload):
    p = tmp_path / "out.json"
    p.write_text(json.dumps(payload))
    return p


# --- liberal in what it accepts ------------------------------------------


@pytest.mark.parametrize("payload", [
    {"fields": {"a": {"value": 1, "quote": "q"}}},           # nested
    {"a": {"value": 1, "quote": "q"}},                        # flat
    {"a": {"value": 1, "evidence_quote": "q"}},               # pyligent
    {"a": {"value": 1, "citation": "q"}},                     # another convention
    {"a": {"text": 1, "evidence": "q"}},                      # value under another key
    {"a": {"value": 1, "quote": ["q", "second"]}},            # several quotes
    {"a": {"value": 1, "evidence": {"text": "q"}}},           # quote wrapped in an object
])
def test_the_shapes_pipelines_actually_emit_are_understood(payload):
    """Adoption dies on a required input format."""
    assert normalise_extraction(payload) == {"a": {"value": 1, "quote": "q"}}


def test_a_bare_field_to_value_map_is_accepted_without_citations():
    assert normalise_extraction({"a": 5}) == {"a": {"value": 5, "quote": ""}}


def test_something_that_is_not_an_object_is_refused():
    with pytest.raises(ValueError, match="JSON object"):
        normalise_extraction([1, 2, 3])


# --- exit codes -----------------------------------------------------------


def test_a_clean_extraction_exits_zero(doc, tmp_path, capsys):
    ex = write(tmp_path, {"base_currency": {
        "value": "USD",
        "quote": '"Base Currency" means United States Dollars (USD).'}})
    assert main(["check", "--source", str(doc), "--extraction", str(ex)]) == 0
    assert "No findings" in capsys.readouterr().out


def test_a_critical_finding_exits_one(doc, tmp_path):
    ex = write(tmp_path, {"threshold": {
        "value": 5_000_000,
        "quote": '"Threshold" means with respect to each party: USD 0.'}})
    assert main(["check", "--source", str(doc), "--extraction", str(ex)]) == 1


def test_fail_on_never_reports_but_does_not_fail(doc, tmp_path):
    q = '"Threshold" means with respect to each party: USD 0.'
    ex = write(tmp_path, {"threshold": {"value": 5_000_000, "quote": q}})
    assert main(["check", "--source", str(doc), "--extraction", str(ex),
                 "--fail-on", "never"]) == 0


def test_a_missing_file_exits_two_not_one(doc, tmp_path):
    """Two is 'I could not run', one is 'I ran and found something'. CI needs
    to tell those apart."""
    assert main(["check", "--source", str(doc), "--extraction",
                 str(tmp_path / "absent.json")]) == 2


def test_malformed_json_exits_two_with_the_parse_error(tmp_path, doc, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert main(["check", "--source", str(doc), "--extraction", str(bad)]) == 2
    assert "not valid JSON" in capsys.readouterr().err


# --- output ---------------------------------------------------------------


def test_json_output_carries_the_locator(doc, tmp_path, capsys):
    """The reason HTML is native: the finding points at a table cell."""
    ex = write(tmp_path, {"haircut": {"value": 2, "quote": "98%"}})
    main(["check", "--source", str(doc), "--extraction", str(ex), "--json"])
    payload = json.loads(capsys.readouterr().out)
    finding = payload["findings"][0]
    assert finding["code"] == "SILENT_REPAIR"
    assert finding["locator"]["cell"] == "r1c2"
    assert payload["source"]["ingested_by"] == "html/native"


def test_human_output_names_the_document_position(doc, tmp_path, capsys):
    ex = write(tmp_path, {"haircut": {"value": 2, "quote": "98%"}})
    main(["check", "--source", str(doc), "--extraction", str(ex)])
    out = capsys.readouterr().out
    assert "table t1, cell r1c2" in out
    assert "SILENT_REPAIR is the one to look at first" in out


def test_the_common_case_needs_no_subcommand(doc, tmp_path, capsys):
    """`unsourced doc.html out.json` — the whole interface.

    A stranger should not have to read --help to run the one thing this does.
    """
    ex = write(tmp_path, {"haircut": {"value": 2, "quote": "98%"}})
    assert main([str(doc), str(ex)]) == 1
    assert "SILENT_REPAIR" in capsys.readouterr().out


def test_the_subcommand_form_still_works_for_scripts(doc, tmp_path):
    ex = write(tmp_path, {"haircut": {"value": 2, "quote": "98%"}})
    assert main(["check", "--source", str(doc), "--extraction", str(ex)]) == 1


def test_missing_arguments_explain_rather_than_traceback(doc, capsys):
    with pytest.raises(SystemExit):
        main([str(doc)])
    assert "required" in capsys.readouterr().err


def test_a_clean_report_says_what_it_does_not_prove(doc, tmp_path, capsys):
    """Overclaiming here is how the tool loses trust."""
    ex = write(tmp_path, {"base_currency": {
        "value": "USD", "quote": '"Base Currency" means United States Dollars (USD).'}})
    main(["check", "--source", str(doc), "--extraction", str(ex)])
    assert "does not mean the values are correct" in capsys.readouterr().out
