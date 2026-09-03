"""`evidence-check` — the ten-minute version.

Liberal in what it accepts, because the whole point is that you do not have to
change your pipeline to use it. Four extraction shapes are understood, and the
key holding the citation may be `quote`, `evidence_quote`, `citation`,
`evidence` or `source_text`.

Strict in what it reports: the same findings, in the same order, every run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .checks import check
from .console import use_utf8_stdout
from .report import CRITICAL, Report
from .sources import Source, load

QUOTE_KEYS = ("quote", "evidence_quote", "citation", "evidence", "source_text", "span")
VALUE_KEYS = ("value", "text", "answer", "extracted")


def normalise_extraction(payload: Any) -> dict[str, dict[str, Any]]:
    """Accept the shapes people's pipelines actually emit.

    Adoption dies on a required input format. If a shape can be understood
    without guessing, understand it.
    """
    if isinstance(payload, dict) and isinstance(payload.get("fields"), dict):
        payload = payload["fields"]
    if not isinstance(payload, dict):
        raise ValueError(
            "the extraction must be a JSON object of fields, or an object with "
            "a 'fields' key"
        )

    out: dict[str, dict[str, Any]] = {}
    for name, entry in payload.items():
        if isinstance(entry, dict):
            value = next((entry[k] for k in VALUE_KEYS if k in entry), None)
            quote = next((entry[k] for k in QUOTE_KEYS if entry.get(k)), "")
            if isinstance(quote, list):          # some pipelines emit several
                quote = quote[0] if quote else ""
            if isinstance(quote, dict):          # ...or an object around one
                quote = next((quote[k] for k in QUOTE_KEYS + ("text",)
                              if quote.get(k)), "")
            out[str(name)] = {"value": value, "quote": str(quote or "")}
        else:
            # A bare {field: value} map. No citations, and it will be told so.
            out[str(name)] = {"value": entry, "quote": ""}
    return out


def render(report: Report, source: Source, *, verbose: bool = False) -> str:
    lines: list[str] = []
    rule = "─" * 70

    if report.notes:
        for note in report.notes:
            lines += ["", note, ""]

    if not report.findings:
        lines.append(f"No findings. {report.fields_checked} field(s) checked "
                     f"against {source.ingested_by}.")
        lines.append("")
        lines.append("Each value is supported by a citation found in the document.")
        lines.append("This confirms evidential support, not correctness: a citation")
        lines.append("may be present and verbatim and still cite the wrong clause.")
        return "\n".join(lines)

    order = {CRITICAL: 0}
    for f in sorted(report.findings, key=lambda x: (order.get(x.severity, 1), x.field)):
        tag = "CRITICAL" if f.severity == CRITICAL else "warning "
        lines.append(f"{tag}  {f.code}")
        lines.append(f"          field    {f.field}")
        lines.append(f"          value    {f.value!r}")
        lines.append(f"          {f.message}")
        if f.quote:
            where = source.locate(f.quote)
            quoted = " ".join(str(f.quote).split())
            if len(quoted) > 88:
                quoted = quoted[:85] + "…"
            lines.append(f'          cited    "{quoted}"')
            if not where.is_empty():
                lines.append(f"          at       {where.describe()}")
        lines.append("")

    lines.append(rule)
    counts = ", ".join(f"{n} {c}" for c, n in report.by_code().items())
    lines.append(f"{len(report.findings)} finding(s) in {report.fields_checked} "
                 f"field(s): {counts}")
    if any(f.code == "SILENT_REPAIR" for f in report.findings):
        lines.append("")
        lines.append("Review SILENT_REPAIR findings first. The citation is present")
        lines.append("and verbatim, but states a value other than the one extracted.")
    return "\n".join(lines)


def cmd_check(a: argparse.Namespace) -> int:
    try:
        source = load(a.source)
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        payload = json.loads(Path(a.extraction).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"no extraction at {a.extraction}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"{a.extraction} is not valid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        fields = normalise_extraction(payload)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    report = check(source.text, fields, tool=f"evidence-check {__version__}")

    if a.json:
        payload_out = report.to_dict()
        payload_out["source"]["media_type"] = source.media_type
        payload_out["source"]["ingested_by"] = source.ingested_by
        for finding in payload_out["findings"]:
            where = source.locate(finding.get("quote", ""))
            if not where.is_empty():
                finding["locator"] = where.to_dict()
        print(json.dumps(payload_out, indent=2, default=str))
    else:
        print(render(report, source, verbose=a.verbose))

    if a.fail_on == "never":
        return 0
    if a.fail_on == "any":
        return 1 if report.findings else 0
    return 0 if report.ok else 1


# Stated in --help because a caller who tests only for non-zero will treat findings
# as a broken run. The two are different events and scripts must be able to tell them
# apart.
EXIT_CODES = """Exit codes:
  0   no findings
  1   findings that need review
  2   the run could not happen (missing file, unreadable input)

Exit 1 is a result, not a failure. In a script, treat 2 as a broken run
and 1 as findings to act on. Testing only for non-zero conflates them.
"""


def main(argv: list[str] | None = None) -> int:
    """Positional in the common case; the subcommand stays for scripts.

    A stranger should not have to read --help to run the one thing this does.
    `evidence-check contract.html out.json` is the whole interface.
    """
    use_utf8_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)

    p = argparse.ArgumentParser(
        prog="evidence-check",
        usage="evidence-check SOURCE EXTRACTION [--json] [--fail-on {critical,any,never}]",
        description="Which values in this extraction does the document not support?",
        epilog=EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"evidence-check {__version__}")
    p.add_argument("source", nargs="?", help="the document: .txt, .html or .pdf")
    p.add_argument("extraction", nargs="?", help="JSON of what your pipeline produced")
    p.add_argument("--source", dest="source_flag", help=argparse.SUPPRESS)
    p.add_argument("--extraction", dest="extraction_flag", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true", help="emit the report as JSON")
    p.add_argument("--fail-on", choices=("critical", "any", "never"), default="critical",
                   help="what makes the exit code non-zero (default: critical)")
    p.add_argument("--verbose", action="store_true")

    # `evidence-check check --source X --extraction Y` still works; the subcommand is
    # just dropped before parsing.
    if argv and argv[0] == "check":
        argv = argv[1:]

    a = p.parse_args(argv)
    a.source = a.source or a.source_flag
    a.extraction = a.extraction or a.extraction_flag
    if not a.source or not a.extraction:
        p.error("both a source document and an extraction are required")
    return int(cmd_check(a))


if __name__ == "__main__":
    raise SystemExit(main())
