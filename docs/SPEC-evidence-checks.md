# The evidence-check contract, v1

`evidence-check` answers one question about a document extraction:

> **Which of these values does the document not actually support?**

It takes a source document and whatever your pipeline produced. It runs no
model, makes no network call, and returns the same report every time. It has no
opinion about your schema, your domain, or your prompt.

This document defines the checks precisely enough that a second implementation
would agree with this one. If the two disagree, one of them has a bug, and this
document decides which.

---

## 1. What this is for, and what it is not

**For.** Telling you that a value in your extraction is not supported by the
text your own pipeline cited for it.

**Not for.** Deciding whether the extraction is *correct*. A quote can be
genuine, contain the value, and still be the wrong clause. `evidence-check` cannot know
that, and does not guess. Everything here is a claim about *support*, never a
claim about truth.

Three specific non-goals, stated so nobody has to discover them:

- **Irrelevant-but-genuine evidence passes.** A real sentence that does not
  bear on the claim is invisible to this tool.
- **Omissions are invisible.** A field your extractor never emitted is not a
  finding here; `evidence-check` sees what you gave it.
- **Domain rules are out of scope.** Whether a Minimum Transfer Amount may
  exceed a Threshold is a question for a domain gate, not for this.

---

## 2. Input

```json
{
  "source": "the full document text",
  "fields": {
    "net_total": { "value": 824.99, "quote": "Net total            824.99" }
  }
}
```

`value` may be any JSON scalar. `quote` is the text the pipeline says
establishes that value. Any pipeline can emit this shape, which is the point —
`evidence-check` checks Claude, GPT, Gemini, a vendor IDP or a regex equally.

---

## 3. The checks

Exactly three, and they are **mutually exclusive by construction**: a field
yields at most one finding, and each finding has exactly one cause. Checks are
evaluated in this order and the first match wins.

### 3.1 `UNSUPPORTED_FIELD` — nothing was offered

The field carries no usable claim to check.

| Condition | Code |
|---|---|
| `quote` absent or empty | `MISSING_EVIDENCE` |
| `value` absent, empty or whitespace | `EMPTY_VALUE` |
| `value` matches a placeholder marker | `PLACEHOLDER_VALUE` |

Default markers: `TODO`, `TBD`, `N/A`, `NA`, `unknown`, `none`, `null`, `-`,
compared case-insensitively after trimming.

**Report-level exception.** If *no* field in the extraction carries a quote, the
report emits a single `NO_EVIDENCE_SUPPLIED` note and no per-field
`MISSING_EVIDENCE` findings. A pipeline that does not produce citations at all
should be told that once, not once per field.

### 3.2 `FABRICATED_EVIDENCE` — the citation is not in the document

The quote does not appear in the source under §4.1 normalisation.

This is the check that makes an extraction falsifiable. A model that invents
its evidence has told you nothing, however correct the value happens to be.

### 3.3 `SILENT_REPAIR` — the cited text names a different value

The quote **is** genuine, the value is **not** in it, and the quote contains a
competing value of the same type.

This is the finding this tool exists for. A model asked to extract a name that
reads `Jonathon` on the passport and `Jonathan` on the form will often write
down whichever makes the file consistent, quote the passport line honestly, and
produce an artifact where every other check passes. The discrepancy it was
hired to surface is the thing it quietly removed.

**The legitimate-inference rule.** If the quote contains **no** value of the
same type as the extracted value, this is **not a finding**. A correct
extraction may derive a value the cited text does not state:

> `eligible_currency = "USD"` from *"Eligible Currency means the Base Currency."*

The quote names no currency, so nothing is contradicted, so nothing is
reported. Without this rule the check fires on perfect extractions, which was
measured and is not a hypothetical.

**Derived numbers are an exception, deliberately.** The rule protects a value
whose type the quote does not mention at all. It does *not* protect a number
derived from a different number in the same quote: a haircut of `2` cited
against `98%` is reported, because the cited text states 98. That is where
transcription errors live — a valuation percentage stored as a haircut
misprices the whole book — so the tool surfaces the transformation and lets a
human confirm it. Cite the source figure, or cite a clause that states the
derived one.

---

## 4. Normalisation

Comparison is where this tool is right or wrong, so the rules are exact.

### 4.1 Text

- Runs of whitespace collapse to a single space; leading and trailing trimmed.
  Documents wrap at arbitrary points and a quote spanning a line break is still
  a quote.
- Comparison is case-insensitive.
- **Wording is never normalised.** A paraphrase is not a citation. No stemming,
  no synonyms, no fuzzy distance.

### 4.2 Numbers

A token is numeric if it parses under this algorithm:

1. Trim; convert accounting negatives `(1,234)` → `-1234`.
2. Remove currency symbols (`£ $ € ¥ ₹`) and adjacent ISO codes.
3. Remove a trailing `.` `,` `;` `:` that is sentence punctuation — a separator
   is only a decimal point when digits follow it.
4. Resolve separators:
   - both `,` and `.` present → the **rightmost** is the decimal mark
   - only `,` → decimal if it occurs once with exactly two following digits,
     otherwise a thousands separator
   - only `.` → decimal, unless it occurs more than once (then thousands)
5. Strip thousands separators, normalise the decimal mark to `.`, parse.

Comparison is **exact after normalisation**. No tolerance: a tolerance window
is a way to miss the transposed digit that this tool exists to find.

`5,000,000.` and `5000000` are the same number. `82.50` and `85.20` are not.

### 4.3 Dates

Parsed from a fixed format list (ISO, `D Month YYYY`, `Month D, YYYY`,
`DD/MM/YYYY`, `MM/DD/YYYY`). **`DD/MM` versus `MM/DD` is ambiguous and both
readings are accepted as a match** — reporting a false discrepancy on date
order would make the tool untrustworthy in exactly the places it matters.

### 4.4 Proper nouns

Capitalised word tokens of three or more letters. A value matches if it shares
at least one such token with the quote. `Jonathan Whitfield` and
`Jonathon Whitfield` share `Whitfield` but differ on the given name — and
because neither given name matches, the pair is a competing value.

---

## 5. Severity

| Code | Severity | Why |
|---|---|---|
| `FABRICATED_EVIDENCE` | `critical` | The citation is not real. Nothing downstream can be trusted. |
| `SILENT_REPAIR` | `critical` | A discrepancy was removed rather than reported. |
| `PLACEHOLDER_VALUE` | `critical` | Shape-valid and content-free; it passes schemas and fails people. |
| `EMPTY_VALUE` | `warning` | Visible on inspection. |
| `MISSING_EVIDENCE` | `warning` | May be legitimate for a derived field. |

---

## 6. Report

```json
{
  "report_version": 1,
  "tool": "evidence-check 0.1.0",
  "source": { "sha256": "…", "chars": 4821 },
  "summary": {
    "fields": 12, "findings": 2, "critical": 2,
    "by_code": { "SILENT_REPAIR": 1, "FABRICATED_EVIDENCE": 1 }
  },
  "notes": [],
  "findings": [
    {
      "code": "SILENT_REPAIR",
      "severity": "critical",
      "field": "net_total",
      "value": 824.99,
      "quote": "Net total            989.99",
      "competing": ["989.99"],
      "message": "The cited text states 989.99, not 824.99."
    }
  ]
}
```

Findings are sorted by severity, then by field name — never by discovery order.

---

## 7. Determinism

Same source and same extraction produce a **byte-identical** report, except for
the `tool` version string. There is no model, no network, no randomness and no
timestamp in the output, so two reports can be diffed directly.

`report_version` increments when the shape changes. Adding a finding code is
not a shape change; removing or renaming one is.

---

## 8. Conformance

An implementation conforms if it produces the same finding codes on the same
fields for the corpus in `tests/`. `tests/test_contract.py` is the executable
form of this document — every rule above has a test that cites its section.
