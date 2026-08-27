"""Produce extractions from a real model, for a real corpus.

    export ANTHROPIC_API_KEY=...
    python bench/extract.py --corpus bench/corpus --model claude-sonnet-5

One prompt, held constant across providers, because the benchmark compares
models and a per-model prompt would compare prompt engineering instead. The
prompt asks for a value and a verbatim quote per field — which is the minimum
any pipeline claiming to be auditable already does.

This costs money and needs a key. `bench/run.py` does not: scoring extractions
that already exist is free, offline and deterministic, which is the property
that makes the benchmark reproducible by someone who does not trust you.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PROMPT = """\
Extract the following fields from the document below.

For each field give the value, and an `evidence_quote` copied CHARACTER FOR
CHARACTER from the document. Do not paraphrase the quote, do not tidy it, and
do not correct anything you believe is an error in the document — transcribe
what is there.

If a field is absent, omit it rather than writing a placeholder.

Fields: {fields}

Respond with JSON only:
{{"fields": {{"<name>": {{"value": <value>, "evidence_quote": "<exact words>"}}}}}}

DOCUMENT
--------
{document}
"""

DEFAULT_FIELDS = ("base_currency", "eligible_currency", "threshold",
                  "minimum_transfer_amount", "rounding", "governing_law",
                  "party_a", "party_b", "valuation_percentage")


def call_anthropic(model: str, prompt: str) -> str:
    import anthropic

    # An identity-linked key must say which workspace it is acting in. A plain
    # account key does not, and sending an empty header would be worse than
    # sending none, so this is set only when present.
    workspace = os.getenv("ANTHROPIC_WORKSPACE_ID", "").strip()
    headers = {"anthropic-workspace-id": workspace} if workspace else None
    client = anthropic.Anthropic(default_headers=headers)
    try:
        r = client.messages.create(model=model, max_tokens=4096,
                                   messages=[{"role": "user", "content": prompt}])
    except anthropic.BadRequestError as exc:
        if "workspace-id is required" in str(exc):
            raise SystemExit(
                "This API key is identity-linked, so every request must name the "
                "workspace it acts in.\n\n"
                "Find the workspace id in the Anthropic Console (Settings ->\n"
                "Workspaces; it looks like wrkspc_...), then add it alongside the\n"
                "key:\n\n"
                '    echo \'ANTHROPIC_WORKSPACE_ID=wrkspc_...\' >> .env\n'
            ) from exc
        raise
    return "".join(b.text for b in r.content if b.type == "text")


def call_openai(model: str, prompt: str) -> str:
    from openai import OpenAI

    r = OpenAI().chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}])
    return r.choices[0].message.content or ""


def call_gemini(model: str, prompt: str) -> str:
    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return client.models.generate_content(model=model, contents=prompt).text or ""


BACKENDS = {"claude": call_anthropic, "gpt": call_openai, "gemini": call_gemini}


def backend_for(model: str):
    for prefix, fn in BACKENDS.items():
        if model.lower().startswith(prefix):
            return fn
    raise SystemExit(
        f"no backend for {model!r}. Names beginning claude-, gpt- or gemini- "
        f"are routed automatically; add one to BACKENDS otherwise."
    )


def parse_json(text: str) -> dict:
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(body)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate extractions for a corpus.")
    p.add_argument("--corpus", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--fields", nargs="*", default=list(DEFAULT_FIELDS))
    p.add_argument("--overwrite", action="store_true")
    a = p.parse_args(argv)

    from evidencecheck.sources import load as load_source

    call = backend_for(a.model)
    root = Path(a.corpus)
    done = failed = 0

    for d in sorted(x for x in root.iterdir() if x.is_dir()):
        sources = [f for f in d.iterdir()
                   if f.suffix.lower() in (".html", ".htm", ".txt", ".pdf")]
        if not sources:
            continue
        out = d / "extractions" / f"{a.model}.json"
        if out.exists() and not a.overwrite:
            continue
        source = load_source(sources[0])
        prompt = PROMPT.format(fields=", ".join(a.fields), document=source.text[:120_000])
        try:
            payload = parse_json(call(a.model, prompt))
        except Exception as exc:  # noqa: BLE001 — one line per failure, keep going
            print(f"  {d.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed += 1
            continue
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(payload, indent=2))
        done += 1
        print(f"  {d.name}")

    print(f"\n{done} extraction(s) written, {failed} failed.")
    print(f"Score them with: python bench/run.py --corpus {a.corpus}")
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
