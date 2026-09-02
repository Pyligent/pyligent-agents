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
| `bench/corpus/` | 12 real SEC exhibits. |
| `bench/corpus-sec/` | **100 real SEC exhibits**, each verified to *be* a CSA. See below. |
| `bench/corpus-synthetic/` | 15 constructed documents × 4 extractors, for testing the harness. |

The separation is deliberate and the naming is not incidental. The synthetic
documents were **built to contain the failures they demonstrate**, so numbers
computed on them prove the harness works and prove nothing whatever about any
model. `run.py` prints each document's provenance in the output, so a synthetic
run cannot be screenshotted as a real one without the word `synthetic` in the
frame.

**No claim about any model should be published from `corpus-synthetic/`.**

---

## Documents that *are* a CSA, not documents that mention one

`fetch_sec.py --query "Credit Support Annex"` returns full-text hits, and full-text
hits are mostly wrong for this purpose. A 10-K whose notes say the parties entered
into a Credit Support Annex contains the phrase exactly once and contains no annex.
Build a corpus from that search and most of it has nothing to extract, so every score
computed on it is really a measure of how often a model correctly finds nothing.

`classify.py` separates the two using the annex's own vocabulary. "Credit Support
Annex" is a name and travels into prose freely. "Delivery Amount" and "Return Amount"
are operative defined terms of the transfer obligation: a document that *is* an annex
cannot avoid them, and a document merely referring to one has no reason to use them.
The gate requires both legs plus corroboration from paragraph structure or the
Paragraph 11/13 elections.

Every verdict carries the evidence that produced it, so a disputed call is inspected
rather than re-tuned:

```bash
python bench/classify.py path/to/filings --json verdicts.json
```

Measured separation on the material to hand:

| set | n | admitted | score range |
|---|---|---|---|
| hand-verified CSAs (`bench/corpus/`) | 12 | 12 | 36–44 |
| SEC exhibits | 108 | 107 | 33–44 |
| hard negatives — design docs discussing CSAs at length | 24 | **0** | 0–11 |

Nothing scores between 11 and 33. The single rejected exhibit is an *Amendment
Agreement* to an ISDA Master that names a CSA once — the exact document full-text
search is wrong about, correctly refused.

Zero false accepts is the number that matters here, for the same reason it does in
`evals/`: admitting a non-annex silently poisons every score computed afterwards,
while refusing a real one costs a single document.

### Building it

```bash
python bench/build_corpus.py path/to/filings --out bench/corpus-sec --limit 100
```

`build_corpus.py` enforces provenance in code, not in instructions. Benchmark
documents are sent to third-party model APIs, so only public material may enter: SEC
EDGAR exhibits and ISDA's published forms. **Executed bilateral agreements between
named counterparties are excluded unconditionally, by filename pattern, before
anything reads them.** A corpus builder is precisely where such a file slips through
unnoticed, because it looks like every other CSA. Each record's `meta.json` carries
the source, licence, content hash, and the classifier evidence that admitted it.

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

## The one live call in CI

Every other job in `ci.yml` runs against the deterministic backend and spends nothing.
That is the right default, and it leaves one gap: a provider can rename a model, retire
a version, or change a response shape, and this repo would learn about it from a user.

That is not hypothetical. `gemini-2.5-flash` now returns `404 NOT_FOUND` to new keys —
*"no longer available to new users"* — and no test caught it, because no test had ever
called it. The Anthropic path had the same exposure: it was the backend most likely to
be tried first by anyone evaluating this project, and nothing exercised it.

So `ci.yml` has a `live-model` job that makes exactly one real call and verifies the
reply survives `normalise_extraction` **and** that every quote it cites is genuinely
present in the source. A live test that accepted an invented quote would be worse than
no live test at all.

**Measured cost** — `claude-sonnet-5`, list price, not an estimate:

| | |
|---|---|
| input / output tokens | 473 / 547 |
| per run | **$0.0096** |
| weekly schedule | ≈ $0.50/year |
| if also enabled on every push to main (~200/mo) | ≈ $1.92/month |

The test document is ~500 characters specifically to keep that figure true. It is a
contract test, not an accuracy test: accuracy is what `run.py` measures, over a corpus,
with evidence.

Secrets are unavailable to `pull_request` runs from forks, so the job is restricted to
this repository and skips itself when no key is configured — a fork sees it neutral,
never red. Locally it is opt-in twice over: the `live` marker plus
`PYLIGENT_LIVE_MODEL=1`, so a developer with a key exported never pays by accident.

```bash
PYLIGENT_LIVE_MODEL=1 pytest -m live -s
```

---

## First run on the corpus

`gemini-3.6-flash`, 97 documents, nine schema fields per document.

| | |
|---|---|
| coverage — share of the schema attempted | 74.9% |
| evidence integrity — of what it emitted | 99.5% |
| **effective integrity — answered *and* supported** | **74.6%** |
| fabricated | 2 |
| silent repair | 0 |
| empty value | 1 |

**Do not quote the 99.5% alone.** Its denominator is what the model chose to emit, so
omitting a field you would have failed raises it: on a three-field document, dropping
the one bad field moves integrity from 66.7% to 100%. Coverage is the correction, and
effective integrity is the figure omission cannot inflate.

Three things went wrong on the way to those numbers, and all are worth more than them.

### The metric rewarded timidity

Reported alone, evidence integrity is gameable: its denominator is the set of fields
the extractor *chose* to answer. A model that attempts only the easy fields outscores
one that attempts the hard ones. Found by an external reviewer, not by us.

`run.py` now reports coverage, citation coverage, integrity and effective integrity,
ordered by the last. It changes the ranking, not just the presentation: on the
ten-document intersection all three models cover, `claude-sonnet-5` leads on integrity
(100.0%) while `gemini-2.5-pro` leads on effective integrity (80.0% against 77.8%),
because it attempted 86.7% of the schema against 77.8%.

### The checker accused correct work

The first run reported six fabrications. Three were the checker's fault. One exhibit's
filer left **U+200B zero-width spaces** between block elements; Python's `\s` does not
match U+200B, because it is category Cf and not whitespace. Three quotes that
transcribed exactly what a reader sees were reported as invented.

A tool whose value rests on being believed when it says *"this citation is not real"*
cannot afford to accuse correct work. That error is worse than missing a fabrication,
because it teaches people to ignore the output. `normalize.squash` now removes
invisible characters before matching, and `find_flexible` absorbs them in its gap
pattern rather than stripping them up front, so offsets still index the original
document and `Source.locate` keeps resolving real positions.

### The corpus builder asserted a licence it had not checked

The first version stamped `US federal government work, public domain` onto every
record it admitted. That was a claim about someone else's copyright with nothing
behind it — written into the tool whose entire subject is unsupported claims.

It was wrong for three files: two ISDA-published forms, which are ISDA's copyright and
not federal works at all, and one named securitisation's transaction document carrying
its own legal notice. None was caught by the confidential-filename filter, because
nothing in their names suggested anything.

Provenance is now evidence like everything else. A document is admitted only if it
carries EDGAR filing furniture — `<TYPE>`, `<SEQUENCE>`, `SEC-HEADER`, `<PAGE>`, or an
`EX-` exhibit tag — and the marker that admitted it is recorded in its `meta.json`.
Anything else is **refused rather than relabelled**: this corpus is redistributed and
sent to third-party APIs, and "probably fine" is not a licence. Eight documents are now
refused, five on filename and three on provenance.

### What survived

Both remaining findings are quotes **stitched across a page break** — `efc6-1070`
/`threshold` and `efc7-2680`/`valuation_percentage` — joining real fragments over
intervening page furniture (`13`, `<PAGE>`, `REFERENCE NUMBER: N727633N`). Every word
is in the document; the sentence is not.

The check is right to flag them: a citation that is not verbatim cannot be verified by
the person relying on it. But stitching over a page number and inventing a definition
outright are different failures with different remedies, and they currently share one
code. Splitting `FABRICATED_EVIDENCE` is worth doing before anyone triages a large run.

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
