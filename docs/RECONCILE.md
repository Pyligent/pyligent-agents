# Shadow reconciliation

Compare signed agreements against what a collateral system currently holds. Nothing
is written anywhere.

```bash
pyligent-agents reconcile \
    --documents ./agreements/ \
    --extractions ./extractions/ \
    --system ./collateral_export.csv \
    --out exceptions.csv
```

---

## Why this and not something cleverer

The bank already has both halves: the executed agreements, and the terms keyed into
the margin system. Where they disagree is what margin disputes are made of, and
nobody has time to read four thousand agreements to find out.

This runs beside the existing process. **Read-only** — no write path, no integration,
no migration. Worst case is a report nobody acts on, which is why it is possible to
say yes to in weeks rather than quarters.

---

## The rule that makes it safe to act on

Every compared field lands in exactly one of three states:

| state | meaning |
|---|---|
| `agrees` | the system matches the agreement |
| `discrepancy` | they differ, **and the clause behind our value checks out** |
| `unverified` | the citation failed its check — a human must read the document |

**A discrepancy is only raised when the extraction's own citation survives
verification.** If the quote does not appear in the document, or states a different
value from the one extracted, the output is *"we could not verify this"* — never
*"your system is wrong"*.

Telling an operations team their record contradicts a signed agreement, on the
strength of a sentence a model invented, is worse than saying nothing at all. It
spends exactly the credibility a shadow trial exists to build. Only `discrepancy`
is an exception an analyst should work.

---

## Inputs

**`--documents`** a file or a directory. `.htm`, `.html`, `.txt`, `.pdf`.

**`--extractions`** whatever your pipeline already produces, as JSON. Seven shapes
are understood — see [SPEC-evidence-checks.md](SPEC-evidence-checks.md). Matched to
documents by filename stem, so `CP-001.pdf` pairs with `CP-001.json`.

**`--system`** the export from your collateral system. CSV or JSON.

```csv
document,counterparty,threshold,mta,base_currency
CP-001,Atlas Bank,5000000,500000,USD
```

The key column is `document`, `document_id`, `counterparty`, `id`, or the first
column. Every other column is treated as a stored term, except identifiers
(`counterparty`, `as_of`, `book`, `desk`, `portfolio`, `legal_entity`, …), which are
metadata rather than terms and are not compared.

**Only fields the system actually holds are compared.** A term the export does not
carry is a scope question, not a disagreement — inventing disagreements is the
fastest way to end a trial.

---

## Output

A page per document, exceptions first, then anything that could not be verified:

```
── MATERIAL · threshold ──────────────────────────
  agreement says : 0
  system says    : 5000000
  impact         : changes when a call is made and by how much
  clause         : "Threshold" means with respect to each party: USD 0.
```

`--out` writes one CSV row per field needing attention — document, counterparty,
field, state, materiality, both values, the impact, and the clause. That is the file
an analyst works from.

Materiality is stated, not inferred: `threshold`, `mta`, `independent_amount`,
`base_currency`, `rounding`, `valuation_percentage` change the size or timing of a
call. Everything else is record-keeping. Pass your own map to `reconcile()` to
override.

---

## Exit codes

| code | meaning |
|---|---|
| `0` | no material discrepancies |
| `1` | material discrepancies — a human should look |
| `2` | the run could not happen (missing export, unreadable input) |

`1` and `2` are kept apart deliberately. A scheduled job that cannot tell "the
export moved" from "we found drift" will page someone for the wrong reason.

---

## How values are compared

Tolerant about formatting, **strict about units**.

| | |
|---|---|
| agree | `500000` · `500,000` · `USD 500,000` · `US$500,000` · `500,000 USD` |
| agree | `100%` and `100` · `0` and `0.00` · `English law` and `english law` |
| **differ** | `USD 500,000` and `EUR 500,000` — a redenomination is not a formatting difference |

A unit stated on only one side is treated as compatible, because exports routinely
carry a bare number in a column whose currency lives in the schema, and manufacturing
a discrepancy from that is noise.

A duplicated key in the export makes it ambiguous which row is authoritative for that
counterparty. Those documents are named and skipped rather than resolved by whichever
row happened to be last.

---

## What this does not do

- **It does not extract.** Bring your own extractions, from whatever pipeline you
  already run. This checks them and compares them.
- **It does not decide materiality for your book.** The default map is a starting
  point, not a calibrated policy.
- **It does not tell you a value is correct** — only whether the document supports it
  and whether the system agrees. A genuine quote can still be the wrong clause.
- **It writes nothing.** Not to your system, not anywhere. That is the point.
