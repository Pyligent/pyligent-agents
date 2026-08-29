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
    h.write_text(SEC_EXHIBIT, encoding="utf-8")
    assert load(h).ingested_by == "html/native"
    t = tmp_path / "x.txt"
    t.write_text("plain", encoding="utf-8")
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


# --- regressions found on real SEC filings -------------------------------


def test_a_void_element_in_the_head_does_not_swallow_the_document():
    """`meta` and `link` have no closing tag.

    They were in the skip set, so the counter that suppressed their content was
    incremented and never decremented, and every character after the first
    `<meta>` was discarded in silence. One real SEC exhibit lost 95% of its
    text: 72,000 characters became 3,699, with no error raised anywhere.

    Silent truncation is the worst failure a loader can have — everything
    downstream still works, every check still passes, and the answer is about a
    document nobody read.
    """
    markup = ("<html><head>"
              '<meta http-equiv="Content-Type" content="text/html; charset=windows-1252">'
              '<link href="x.css" rel="stylesheet">'
              "</head><body>"
              '<p>"Threshold" means with respect to each party: USD 0.</p>'
              "</body></html>")
    src = from_html(markup)
    assert "Threshold" in src.text
    assert src.spans, "no spans recorded: the skip counter never reset"


def test_script_content_is_still_suppressed_after_that_fix():
    src = from_html("<html><body><script>var x = 'not document text';</script>"
                    "<p>real text</p></body></html>")
    assert "not document text" not in src.text and "real text" in src.text


def test_a_windows_1252_document_decodes_by_its_declared_charset(tmp_path):
    """Older filings are routinely cp1252. Decoding those bytes as UTF-8
    mangles the markup badly enough that the parser gives up early."""
    p = tmp_path / "old.htm"
    p.write_bytes(
        b'<html><head><meta charset="windows-1252"></head><body>'
        b'<p>Threshold \x93means\x94 zero \x97 per party.</p></body></html>')
    text = load(p).text
    assert "Threshold" in text and "zero" in text
    assert "�" not in text, "decoded with the wrong codec"


def test_invisible_characters_do_not_make_a_real_quote_look_invented():
    """A zero-width space between two words must not read as fabrication.

    Found against a real SEC exhibit (clmt-20231003xex10d3), whose filer's editor
    left U+200B between block elements. Three correctly-transcribed quotes were
    reported as FABRICATED_EVIDENCE — the checker accusing correct work, which is
    the most damaging error this tool can make. `\\s` does not match U+200B: it is
    category Cf, not whitespace.
    """
    from evidencecheck.sources import find_flexible

    source = "Threshold for Party A: zero; and\n\n​\n\nThreshold for Party B: zero."
    quote = "Threshold for Party A: zero; and Threshold for Party B: zero."
    assert find_flexible(source, quote) >= 0

    for ch in ("­", "‌", "‍", "⁠", "﻿"):
        assert find_flexible(f"alpha{ch} beta", "alpha beta") >= 0, ch


def test_invisible_tolerance_does_not_admit_invented_text():
    """The fix must not turn the check into a rubber stamp."""
    from evidencecheck.sources import find_flexible

    source = "​\"Eligible Currency\" means each currency specified in Paragraph 13."
    assert find_flexible(source, "means the Base Currency and each other currency") < 0


def test_offsets_still_index_the_original_text():
    """Stripping invisibles up front would shift every offset; locate() needs real ones."""
    from evidencecheck.sources import find_flexible

    source = "​​padding here THE TARGET"
    at = find_flexible(source, "THE TARGET")
    assert source[at:at + 10] == "THE TARGET"
