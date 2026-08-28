"""Getting a document into text, without losing where the text came from.

A quote that checks out tells you the value is supported. A quote that checks
out *and* says "table 2, row 4, column 3, page 14" tells an auditor where to
look. The second is worth far more and costs almost nothing — if the loader
keeps track while it flattens.

So every loader here produces text **plus spans**: character ranges mapped back
to where they sat in the original. `Source.locate()` turns a quote into a
locator after the fact.

HTML is native and dependency-free, which is deliberate. SEC exhibits — the
best public corpus of real financial agreements — are HTML, and their tables
are where haircut matrices and eligible-collateral schedules live. A DOM path
with a table cell is a better anchor than a page number.

PDF is an adapter. Building a PDF layout engine here would be a large,
commoditised effort competing with people who have done it better; ingesting
their output and keeping the locators is a much better trade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# Elements whose *content* is not document text. Void elements must never
# appear here: they have no closing tag, so a skip counter incremented on one
# is never decremented, and every character after it is silently discarded.
# `meta` and `link` were in this set, which cost one real SEC exhibit 95% of
# its text — 72,000 characters became 3,699, with no error anywhere.
_SKIP = {"script", "style", "noscript", "template"}
# Belt and braces: even if a skip element is left unclosed by malformed markup,
# these end the region rather than swallowing the document.
_SKIP_RESET = {"body", "html"}
# Elements that end a line when flattened.
_BLOCK = {"p", "div", "br", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6",
          "li", "ul", "ol", "section", "article", "header", "footer", "hr"}


@dataclass(frozen=True)
class Locator:
    """Where a run of text sat before the document was flattened."""

    page: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    dom_path: str = ""
    table: str = ""
    cell: str = ""

    def is_empty(self) -> bool:
        return not any((self.page, self.dom_path, self.table, self.cell))

    def describe(self) -> str:
        if self.table and self.cell:
            return f"table {self.table}, cell {self.cell}"
        if self.dom_path:
            return self.dom_path
        if self.page is not None:
            return f"page {self.page}"
        if self.char_start is not None:
            return f"characters {self.char_start}–{self.char_end}"
        return "source"

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "")}


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    locator: Locator


@dataclass(frozen=True)
class Source:
    text: str
    spans: tuple[Span, ...] = ()
    media_type: str = "text/plain"
    ingested_by: str = "text"
    path: str = ""

    def locate(self, quote: str) -> Locator:
        """The locator for the span containing this quote.

        The quote is matched against the RAW text with a whitespace-flexible
        pattern, so a citation that wrapped across a line still resolves — and
        the offset it returns is a real offset, not one recovered by counting
        characters in a normalised copy.
        """
        if not quote or not self.spans:
            return Locator()
        at = find_flexible(self.text, quote)
        if at < 0:
            return Locator()
        for span in self.spans:
            if span.start <= at < span.end:
                return span.locator
        return Locator(char_start=at, char_end=at + len(quote))


def find_flexible(haystack: str, needle: str) -> int:
    """Index of `needle` in `haystack`, treating any whitespace run as equal.

    Documents wrap wherever the layout felt like it, so a quote that is real
    can still fail a literal `in` test. Returns -1 when absent.
    """
    tokens = [re.escape(t) for t in needle.split()]
    if not tokens:
        return -1
    m = re.search(r"\s+".join(tokens), haystack, re.IGNORECASE)
    return m.start() if m else -1


class _Flattener(HTMLParser):
    """Flatten HTML to text while recording where each run came from."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.spans: list[Span] = []
        self._stack: list[str] = []
        self._skip = 0
        self._table = 0
        self._row = 0
        self._col = 0
        self._in_cell = False

    @property
    def _length(self) -> int:
        return sum(len(p) for p in self.parts)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_RESET:
            self._skip = 0
        if tag in _SKIP:
            self._skip += 1
            return
        self._stack.append(tag)
        if tag == "table":
            self._table += 1
            self._row = 0
        elif tag == "tr":
            self._row += 1
            self._col = 0
        elif tag in ("td", "th"):
            self._col += 1
            self._in_cell = True
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_RESET:
            self._skip = 0
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if tag in ("td", "th"):
            self._in_cell = False
        if tag in _BLOCK:
            self.parts.append("\n")
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self._skip or not data.strip():
            if data.strip() or not self.parts:
                return
            self.parts.append(data)
            return
        start = self._length
        self.parts.append(data)
        self.spans.append(Span(start, start + len(data), Locator(
            dom_path=" > ".join(self._stack[-3:]),
            table=f"t{self._table}" if self._table and self._in_cell else "",
            cell=f"r{self._row}c{self._col}" if self._in_cell else "",
        )))


def from_html(markup: str, *, path: str = "") -> Source:
    """Flatten HTML, keeping DOM paths and table cells. Standard library only."""
    f = _Flattener()
    f.feed(markup)
    f.close()
    # No post-processing of the text. Spans were recorded against these exact
    # offsets while parsing, and collapsing blank lines afterwards silently
    # shifts every locator by the number of characters removed before it.
    text = "".join(f.parts)
    return Source(text=text, spans=tuple(f.spans), media_type="text/html",
                  ingested_by="html/native", path=path)


def from_text(text: str, *, path: str = "") -> Source:
    return Source(text=text, media_type="text/plain", ingested_by="text", path=path)


def _quieten_backend() -> None:
    """Stop the PDF backend writing to our output.

    The report is meant to be diffable and pipeable. A loader that prints
    pydantic warnings and model-download notices to stdout makes it neither,
    and the noise is not the caller's problem to filter.
    """
    import logging
    import warnings

    warnings.filterwarnings("ignore", message=r".*protected namespace.*")
    for name in ("docling", "docling_core", "RapidOCR", "rapidocr", "PIL"):
        logging.getLogger(name).setLevel(logging.ERROR)


def _converter(*, ocr: bool):
    from docling.document_converter import DocumentConverter  # type: ignore

    if ocr:
        return DocumentConverter()
    try:
        from docling.datamodel.base_models import InputFormat  # type: ignore
        from docling.datamodel.pipeline_options import PdfPipelineOptions  # type: ignore
        from docling.document_converter import PdfFormatOption  # type: ignore

        opts = PdfPipelineOptions()
        opts.do_ocr = False
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    except ImportError:
        # A backend whose options moved is still a usable backend.
        return DocumentConverter()


def from_pdf(path: str | Path, *, ocr: bool = False) -> Source:
    """Adapter. Requires an extraction backend; this package ships none.

    Deliberate: PDF layout extraction is a large commoditised problem, and the
    people who have solved it emit page and cell references this tool can use
    directly. Competing with them would cost months and lose provenance.

    **OCR is off by default.** Executed agreements are almost always digital
    PDFs with a text layer, and running OCR over one costs minutes per document
    on CPU for no gain. Pass `ocr=True` for a scan.
    """
    _quieten_backend()
    try:
        import docling.document_converter  # noqa: F401  (availability probe)
    except ImportError as exc:
        raise RuntimeError(
            f"Reading {path} needs a PDF backend, which this package does not "
            f"ship.\n\n"
            f"    pip install 'pyligent-agents[pdf]'      # Docling, MIT, runs on CPU\n\n"
            f"Or convert it yourself and pass the text:\n"
            f"    evidence-check extracted.txt out.json"
        ) from exc

    try:
        doc = _converter(ocr=ocr).convert(str(path)).document
        text = doc.export_to_markdown()
    except Exception as exc:  # noqa: BLE001 — any backend failure, one message
        # A backend traceback is not an error message. Whatever went wrong
        # inside the converter, the reader needs to know what to do next.
        raise RuntimeError(
            f"The PDF backend could not read {path}: "
            f"{type(exc).__name__}: {exc}\n\n"
            f"If the file is a scan, the backend may need OCR enabled. You can "
            f"also convert it yourself and pass the text:\n"
            f"    evidence-check extracted.txt out.json"
        ) from exc

    return Source(text=text, media_type="application/pdf",
                  ingested_by="adapter/docling", path=str(path))


_META_CHARSET = re.compile(
    rb"""charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)


def decode(raw: bytes) -> str:
    """Decode a document, honouring what it says about itself.

    Older filings are routinely windows-1252, and decoding those bytes as UTF-8
    does not merely mangle a few characters — it corrupts the markup badly
    enough that the parser gives up early and returns a fraction of the
    document, with no error. One real SEC exhibit lost 95% of its text that
    way: 72,000 characters of a 2016 VM CSA became 3,699.

    Silent truncation is the worst failure a loader can have. Everything
    downstream still works, the checks still pass, and the answer is about a
    document nobody read.
    """
    declared = _META_CHARSET.search(raw[:4096])
    candidates = []
    if declared:
        candidates.append(declared.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8", "cp1252", "latin-1"]

    for enc in candidates:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # latin-1 maps every byte, so this is unreachable in practice.
    return raw.decode("utf-8", errors="replace")


def load(path: str | Path) -> Source:
    """Read a document, choosing the loader by extension."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"no document at {p}")
    suffix = p.suffix.casefold()
    if suffix == ".pdf":
        return from_pdf(p)
    text = decode(p.read_bytes())
    if suffix in (".html", ".htm", ".xhtml"):
        return from_html(text, path=str(p))
    return from_text(text, path=str(p))
