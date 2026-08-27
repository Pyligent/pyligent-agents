# Evals

A fifteen-case gold set over three document types — an ISDA **CSA**, a supplier
**invoice**, and a **KYC** file — and the scoring that makes a document-intake
agent's failures visible.

```bash
python evals/run_evals.py                 # all four systems, side by side
python evals/run_evals.py --by-kind       # per document type
python evals/run_evals.py --system helpful --check   # regression gate
```

No API key. Runs in about a second.

---

## The result this exists to produce

```
  SYSTEM         FALSE ACC FALSE REF  FIELD ACC  EVIDENCE   REASON
  ----------------------------------------------------------------
  faithful            0          0       100.0%    100.0%   100.0%
  paraphraser         0          7       100.0%      0.0%   100.0%
  helpful             4          1        95.7%    100.0%   100.0%
  sloppy              0          7        81.5%    100.0%    87.5%
```

Rank that table by field accuracy — the number most eval harnesses headline —
and `helpful` places **second**, above `sloppy`. It is also the only system in
the table that let a flawed document through, four times.

`helpful` is not a broken extractor. It is a *good* one with an instinct to be
useful: when the name on the passport reads `Jonathon` and the application says
`Jonathan`, it writes down the one that makes the file consistent. When the
invoice lines do not sum to the stated total, it quietly reports the sum it
computed. Every individual correction is defensible. The aggregate is a system
that cannot find the thing it was hired to find, and it costs **6% of field
accuracy** to look that way.

That is the entire argument for the shape of this report: two decision errors,
counted separately, never averaged into one headline.

`paraphraser` is the second trap and the reason evidence is scored on its own
axis. 100% field accuracy, 0% evidence validity — every value correct, not one
of them provable from the page. *Requiring* citations does not catch this.
*Checking* them does.

---

## What is measured

| Metric | Question | Tolerance |
|---|---|---|
| **false accepts** | How many flawed documents did it approve? | **0. Always.** |
| **false refers** | How many clean documents did it hold up? | 5% |
| field accuracy | Of the fields, how many matched gold? | 2% drop |
| evidence validity | Of the quotes, how many are really in the source? | 2% drop |
| attribution | When it referred, was the expected gate among those that fired? | 5% drop |
| decision accuracy | Reported. Never the headline — it averages the two above. | — |

The asymmetry is the point and it is asymmetric in the code, not just in prose.
`compare()` gives `false_accept_rate` a tolerance of `0.0`: there is no
acceptable number of flawed documents let through, so any increase fails CI.
A false refer costs an analyst ten minutes. A false accept is the thing you find
out about from the counterparty.

Two smaller decisions worth knowing about:

- **Field comparison does not fuzzy-match.** `Jonathon` ≠ `Jonathan`. On a KYC
  file the one-letter difference *is* the finding; normalising it away would
  hand the `helpful` persona a perfect score. Numbers *are* compared
  numerically, so `500,000` matches `500000`.
- **An errored case counts on the unsafe side.** A system that crashes must not
  score as cautious.

---

## The dataset

Fifteen cases, seven clean and eight flawed — the balance is enforced, not
aspirational. `Dataset.validate()` rejects a single-class set in both
directions: with no flawed cases you are measuring extraction rather than
judgement, and with no clean ones a system that refuses everything scores 100%.

| Case | | Expected gate |
|---|---|---|
| `csa/clean`, `csa/clean-large` | accept | |
| `csa/vm-zero-threshold` | accept | — *(regression guard)* |
| `csa/mta-exceeds-threshold` | refer | `mta_not_transposed_with_threshold` |
| `csa/no-governing-law` | refer | `required_fields` |
| `invoice/clean`, `invoice/clean-single-line` | accept | |
| `invoice/lines-do-not-sum` | refer | `lines_sum_to_total` |
| `invoice/due-before-issue` | refer | `due_after_invoice_date` |
| `kyc/clean`, `kyc/clean-older-applicant` | accept | |
| `kyc/name-mismatch` | refer | `name_matches_document` |
| `kyc/underage` | refer | `applicant_is_of_age` |
| `kyc/address-proof-in-another-name` | refer | `address_proof_names_applicant` |
| `kyc/address-proof-screenshot` | refer | `address_proof_not_a_screenshot` |

A REFER case **must** name the gate it expects, and `EvalCase` raises if it does
not. Referring for the wrong reason is not a pass: a system that holds the
underage applicant because it could not parse the address has not detected
anything, and next month it will hold the wrong file for the same reason.

Every flaw is one a real desk sees. None is a typo or a malformed field — those
are easy. `csa/mta-exceeds-threshold` is a Minimum Transfer Amount larger than
the Threshold, which is internally coherent, passes every schema, and is wrong.

### Documents and labels are generated together

`dataset.py` does not contain fifteen document strings with fifteen label
dictionaries beside them. Each case comes from a builder — `build_csa()`,
`build_invoice()`, `build_kyc()` — that emits the text **and** the gold fields
and quotes from the same inputs.

Hand-maintained gold labels drift the first time someone edits a document to fix
a typo, and a drifted gold set reports failures that are really label bugs —
which is how teams learn to ignore their evals. A test asserts every gold quote
is a verbatim substring of its document, so drift is a red build.

---

## The four systems

They are not mocks. Each is a `router()` over the real graph's prompts, so the
document flows through `examples/document_intake/app.py::build_graph` —
the actual extract → assemble → verify → gate pipeline, not a re-implementation.

| System | Behaviour | Should score |
|---|---|---|
| `faithful` | Correct values, real quotes | clean |
| `paraphraser` | Correct values, invented quotes | 0% evidence, 0 false accepts |
| `helpful` | Silently repairs the inconsistencies it was meant to report | **false accepts** |
| `sloppy` | Drops a field, writes `TBD` in another | low fields, 0 false accepts |

Two are known-good, two are known-bad in *different* directions. That is what
makes them useful: an eval you have only ever pointed at one system tells you
nothing about whether the metrics work. `tests/test_evals.py` asserts the report
separates all four — and specifically that `helpful` outranks `sloppy` on field
accuracy while being the only unsafe one. If that inversion ever stops
reproducing, the metrics have been broken, and a test says so.

---

## Regression gates

```bash
python evals/run_evals.py --system faithful --baseline   # record
python evals/run_evals.py --system faithful --check      # compare, exit 1 on regression
```

Baselines live in `evals/baselines/*.json` and are committed. CI re-runs all
four personas on every push. A test also re-derives each persona's score and
asserts the committed baseline still matches — a baseline nobody re-checks is a
baseline that has quietly rotted.

Running against the live API is `--live` plus `ANTHROPIC_API_KEY`. That number
is the one that matters for a real deployment; the scripted personas exist to
prove the *harness* is sound before you spend anything on it.

---

## Adapting this to your domain

The scoring in `pyligent_agents.evals` is domain-free. What is local to document
intake is `evals/dataset.py` and `examples/document_intake/documents.py`.

1. Write a builder per document type that returns text, gold fields, and gold
   quotes together.
2. For each flaw, name the gate that should catch it. If no gate would, you have
   found a missing gate rather than a missing test case — write the gate first.
3. Keep both classes present and roughly balanced.
4. Write a `helpful` persona for *your* domain — the one that fixes problems
   instead of reporting them. It is the one that will hurt you in production,
   and it is invisible to any metric that averages your two error types.

The last one matters most. Every domain has a version of `helpful`, and it never
shows up in an accuracy number.

---

## Conformance

The CSA and KYC cases are built against published guidance rather than
invented:

- ISDA, *Benchmarking Generative AI for CSA Clause Extraction and CDM
  Representation* (May 2025) — the five benchmarked clauses, the CDM JSON
  target, and the validation protocol.
- AWS Marketplace, *Know Your Customer (KYC) Documentation Upload Best
  Practices* — accepted document types, required data points, the 180-day
  recency window.

`tests/test_guideline_alignment.py` holds the code to those documents, one test
per rule, so a revised guideline produces a failing test that names the rule
rather than a silent divergence.

**`csa/vm-zero-threshold` is load-bearing.** It encodes the most common CSA
shape in the market — Threshold zero, MTA 500,000 — which an earlier ordering
gate referred to a human every time. If a change makes that case fail, the
change is wrong, not the case.
