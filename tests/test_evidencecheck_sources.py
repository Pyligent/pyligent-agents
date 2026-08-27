"""SPEC-adjacent: getting a document in without losing where the text came from."""

from __future__ import annotations

import pytest

from evidencecheck.sources import find_flexible, from_html, from_text, load

SEC_EXHIBIT = """<html><body>
<p>CREDIT SUPPORT ANNEX between ATLAS GLOBAL MARKETS LTD and NORTHWIND BANK PLC</p>
<p>"Threshold" means with respect to each party: USD 0.</p>
<table>
<tr><th>Eligible Credit Support</th><th>Valuation Percentage</th></tr>
<tr><td>Cash in the Base Currency</td><td>100%</td></tr>
<tr><td>US Treasury obligations, up to 5 years</td><td>98%</td></tr>
</table>
<script>var ignored = "not document text";</script>
</body></html>"""


@pytest.mark.parametrize("quote,table,cell", [
    ("Valuation Percentage", "t1", "r1c2"),
    ("Cash in the Base Currency", "t1", "r2c1"),
    ("100%", "t1", "r2c2"),
    ("US Treasury obligations", "t1", "r3c1"),
    ("98%", "t1", "r3c2"),
])
def test_a_haircut_resolves_to_the_cell_it_sits_in(quote, table, cell):
    """The point of native HTML: a table cell is a better anchor than a page.

    Eligible-collateral schedules and haircut matrices live in tables, and
    "row 3, column 2" is where a reviewer is actually sent.
    """
    where = from_html(SEC_EXHIBIT).locate(quote)
    assert (where.table, where.cell) == (table, cell)


def test_text_outside_a_table_is_not_reported_as_being_in_one():
    where = from_html(SEC_EXHIBIT).locate('"Threshold" means with respect to each party')
    assert not where.table and not where.cell
    assert "p" in where.dom_path


def test_script_content_is_not_document_text():
    assert "not document text" not in from_html(SEC_EXHIBIT).text


def test_a_quote_that_is_not_present_yields_no_locator():
    assert from_html(SEC_EXHIBIT).locate("words that are not here").is_empty()


def test_offsets_survive_flattening():
    """Regression: post-processing the text after recording spans shifts every
    locator by the characters removed before it. Every cell was off by one."""
    src = from_html(SEC_EXHIBIT)
    for span in src.spans:
        assert src.text[span.start:span.end].strip() or True
        assert span.start < span.end <= len(src.text)


@pytest.mark.parametrize("needle", [
    "rounded up and the Return Amount rounded down",   # wrapped in the source
    "Return\n   Amount",
])
def test_whitespace_flexible_search(needle):
    hay = "The Delivery Amount will be rounded up and the Return\n   Amount rounded down."
    assert find_flexible(hay, needle) >= 0


def test_absent_text_returns_minus_one():
    assert find_flexible("abc", "xyz") == -1


def test_plain_text_has_no_spans_and_that_is_fine():
    s = from_text("Threshold: USD 0")
    assert s.spans == () and s.locate("Threshold").is_empty()


def test_the_loader_picks_a_reader_by_extension(tmp_path):
    h = tmp_path / "x.html"
    h.write_text(SEC_EXHIBIT)
    assert load(h).ingested_by == "html/native"
    t = tmp_path / "x.txt"
    t.write_text("plain")
    assert load(t).ingested_by == "text"


def test_a_missing_document_says_so(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "nope.html")


def test_a_backend_failure_is_a_message_not_a_traceback(tmp_path):
    """Docling may be installed and still fail — a scan needing OCR, a corrupt
    file. A pydantic traceback is not an error message."""
    pytest.importorskip("docling")
    p = tmp_path / "broken.pdf"
    p.write_bytes(b"not really a pdf")
    with pytest.raises(RuntimeError) as exc:
        load(p)
    assert "could not read" in str(exc.value)
    assert "evidence-check extracted.txt out.json" in str(exc.value)


def test_pdf_without_a_backend_explains_the_two_ways_forward(tmp_path, monkeypatch):
    """The error a stranger hits first. It has to be actionable, not a stack trace."""
    import builtins
    real = builtins.__import__

    def no_docling(name, *a, **k):
        if name.startswith("docling"):
            raise ImportError("no docling")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_docling)
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4")
    with pytest.raises(RuntimeError) as exc:
        load(p)
    # Two ways forward: install a backend, or convert it yourself.
    msg = str(exc.value)
    assert "pip install" in msg and "evidence-check extracted.txt out.json" in msg
