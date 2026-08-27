# The benchmark

**Evidence integrity: the share of extracted fields whose value is supported by
a citation that actually appears in the document.**

```bash
python bench/run.py --corpus bench/corpus
```

Scoring is free, offline, deterministic and needs no API key. That is the point
— anyone who does not trust the numbers can recompute them.

---

## Why this needs no ground truth

You do not need to know the right answer to know that a quote is not in the
document. Evidence integrity is **reference-free**, which is what makes it
cheap enough to run over thousands of pages and honest enough to publish: there
is no annotation step where a judgement call could quietly favour one model.

It is also **not accuracy**. A quote can be genuine, contain the value, and be
the wrong clause entirely. Integrity is a floor, not a verdict.

---

## The state of this corpus

| | |
|---|---|
| `bench/corpus/` | **empty.** Real filings go here. |
| `bench/corpus-synthetic/` | 15 constructed documents × 4 extractors, for testing the harness. |

The separation is deliberate and the naming is not incidental. The synthetic
documents were **built to contain the failures they demonstrate**, so numbers
computed on them prove the harness works and prove nothing whatever about any
model. `run.py` prints each document's provenance in the output, so a synthetic
run cannot be screenshotted as a real one without the word `synthetic` in the
frame.

**No claim about any model should be published from `corpus-synthetic/`.**

---

## Populating the real corpus

```bash
export SEC_CONTACT="you@yourcompany.com"
python bench/fetch_sec.py --query "Credit Support Annex" --limit 50

export ANTHROPIC_API_KEY=...
python bench/extract.py --corpus bench/corpus --model claude-sonnet-5
python bench/extract.py --corpus bench/corpus --model gpt-5
python bench/extract.py --corpus bench/corpus --model gemini-3-pro

python bench/run.py --corpus bench/corpus --json results.json
```

SEC returns **403 to any User-Agent without a real contact address** — their
`Undeclared Automated Tool` page. `fetch_sec.py` asks for one rather than
guessing, which is why the corpus cannot be populated by a machine that has no
address to give.

Filed exhibits are the best corpus available for this work: real agreements,
real tables, negotiated by people who had never heard of your extractor. They
are public-domain US government works.

For invoices, [DocILE](https://docile.rossum.ai/) is MIT-licensed with 6,680
annotated documents behind a research access request. Ship a loader, never the
data.

**Never use real identity documents for the KYC case.**
[MIDV-2020](https://arxiv.org/abs/2107.00396) is mock by construction and exists
precisely because real ID data is security-protected.

---

## Adding a document

```
bench/corpus/<name>/
    source.html
    meta.json           source_url, licence, retrieved date
    extractions/
        <model>.json    {"fields": {"x": {"value": …, "evidence_quote": "…"}}}
```

Seven extraction shapes are understood — `fields` nested or flat, and
`quote` / `evidence_quote` / `citation` / `evidence` — because a benchmark that
requires reformatting measures whoever bothered to reformat.

---

## Reading the table

```
  extractor              integrity  fields  fabricated  repair  placeholder
  --------------------------------------------------------------------------
  faithful                 100.0%     162           0       0            0
  helpful                   98.8%     163           0       2            0
  sloppy                    89.8%     147           0       0           15
  paraphraser                0.0%     162         162       0            0
```

*(synthetic corpus — harness demonstration only)*

The **repair** column is the one to read first. Fabrication is loud: any check
that asks "did it cite something real" catches it. Silent repair is quiet by
construction — the citation is genuine, so every such check passes, and the
discrepancy the extraction was meant to surface is the thing it removed.

An extractor can sit at 98% integrity and still be the most dangerous one in
the table.

---

## Where this sits

The benchmark scores the checks in [`src/evidencecheck/`](../src/evidencecheck),
which are the same checks the framework's evidence gates call — one definition,
never copied. `evidence-check` is the single-document command; this is the
many-document one.

```bash
evidence-check contract.html extraction.json     # one document, exit 1 on findings
python bench/run.py --corpus bench/corpus        # a portfolio, with a table
```
