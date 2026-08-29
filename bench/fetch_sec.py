"""Populate the corpus from SEC EDGAR. Public filings, no account needed.

    export SEC_CONTACT="you@yourcompany.com"
    python bench/fetch_sec.py --query "Credit Support Annex" --limit 20

SEC requires a User-Agent naming a real contact, and returns 403 to anything
else — including a descriptive but contactless one. That is their
`Undeclared Automated Tool` page, and it is why this script asks for an address
rather than guessing. It is a fair rule: they are giving away bulk data and want
to be able to reach whoever is hammering it.

Filed exhibits are public. They are also the best corpus in existence for this
work: real agreements, real tables, negotiated by people who were not thinking
about your extractor.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from evidencecheck.console import use_utf8_stdout

FTS = "https://efts.sec.gov/LATEST/search-index?q={q}&forms={forms}"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
COURTESY_DELAY_S = 0.15          # SEC asks for <= 10 requests/second


def _agent() -> str:
    contact = os.getenv("SEC_CONTACT", "").strip()
    if not contact or "@" not in contact:
        raise SystemExit(
            "SEC requires a User-Agent naming a real contact, and refuses "
            "anything else with a 403.\n\n"
            '    export SEC_CONTACT="you@yourcompany.com"\n\n'
            "This is not sent anywhere but sec.gov, and it is their published "
            "condition for bulk access to public filings."
        )
    return f"pyligent-benchmark {contact}"


def _ssl_context() -> ssl.SSLContext:
    """A context that trusts a real CA bundle.

    A python.org install on macOS ships without one, so the first HTTPS call
    fails with CERTIFICATE_VERIFY_FAILED and an unhelpful traceback. `certifi`
    carries a bundle and is present in most environments; fall back to the
    default when it is not, since a Linux install usually has one already.

    Never disables verification. An unverified fetch of the documents a
    benchmark is built on would undermine the benchmark.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _get(url: str, *, agent: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": urllib.parse.urlparse(url).netloc,
    })
    time.sleep(COURTESY_DELAY_S)
    try:
        with urllib.request.urlopen(req, timeout=30,      # noqa: S310
                                    context=_ssl_context()) as r:
            raw = r.read()
    except urllib.error.URLError as exc:
        if "CERTIFICATE_VERIFY" in str(exc.reason):
            raise SystemExit(
                "This Python has no CA bundle, so HTTPS cannot be verified.\n\n"
                "    pip install certifi\n\n"
                "or, on a python.org install for macOS, run once:\n"
                "    open '/Applications/Python 3.13/Install Certificates.command'"
            ) from exc
        raise
    if raw[:2] == b"\x1f\x8b":
        import gzip
        raw = gzip.decompress(raw)
    return raw


def search(query: str, forms: str, limit: int, *, agent: str) -> list[dict]:
    url = FTS.format(q=urllib.parse.quote(f'"{query}"'), forms=forms)
    try:
        payload = json.loads(_get(url, agent=agent))
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"EDGAR returned {exc.code}. If it is 403, the User-Agent was "
            f"rejected — check SEC_CONTACT is a real address."
        ) from exc
    hits = payload.get("hits", {}).get("hits", [])[:limit]
    out = []
    for h in hits:
        src, ident = h.get("_source", {}), h.get("_id", "")
        acc, _, doc = ident.partition(":")
        ciks = src.get("ciks") or []
        if not (acc and doc and ciks):
            continue
        out.append({
            "cik": str(int(ciks[0])), "accession": acc.replace("-", ""),
            "document": doc, "filed": src.get("file_date", ""),
            "form": src.get("root_form", ""),
            "company": (src.get("display_names") or [""])[0],
        })
    return out


def fetch(hits: list[dict], root: Path, *, agent: str) -> int:
    root.mkdir(parents=True, exist_ok=True)
    saved = 0
    for h in hits:
        url = ARCHIVE.format(cik=h["cik"], acc=h["accession"], doc=h["document"])
        name = f"{h['cik']}-{h['accession'][-6:]}"
        target = root / name
        try:
            body = _get(url, agent=agent)
        except urllib.error.HTTPError as exc:
            print(f"  skip {name}: HTTP {exc.code}", file=sys.stderr)
            continue
        target.mkdir(exist_ok=True)
        suffix = ".html" if h["document"].lower().endswith((".htm", ".html")) else ".txt"
        (target / f"source{suffix}").write_bytes(body)
        (target / "meta.json").write_text(json.dumps({
            "source_url": url,
            "licence": "US federal government work, public domain",
            "company": h["company"], "form": h["form"], "filed": h["filed"],
            "retrieved_by": "bench/fetch_sec.py",
        }, indent=2), encoding="utf-8")
        (target / "extractions").mkdir(exist_ok=True)
        saved += 1
        print(f"  {name}  {h['company'][:44]}")
    return saved


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    p = argparse.ArgumentParser(description="Fetch public filings into the corpus.")
    p.add_argument("--query", default="Credit Support Annex")
    p.add_argument("--forms", default="8-K,10-K,10-Q")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--out", default=str(Path(__file__).resolve().parent / "corpus"))
    a = p.parse_args(argv)

    agent = _agent()
    hits = search(a.query, a.forms, a.limit, agent=agent)
    if not hits:
        print("no filings matched", file=sys.stderr)
        return 1
    print(f"fetching {len(hits)} filing(s) into {a.out}")
    saved = fetch(hits, Path(a.out), agent=agent)
    print(f"\n{saved} document(s) saved. Next: produce extractions with\n"
          f"  python bench/extract.py --corpus {a.out} --model <name>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
