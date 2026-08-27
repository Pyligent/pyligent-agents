# Document intake — one pattern, three domains

```bash
python examples/run.py intake all              # all three, clean
python examples/run.py intake csa --flaw
python examples/run.py intake invoice --flaw
python examples/run.py intake kyc --flaw
python examples/run.py intake kyc --fabricate  # a lying verifier, caught
```

A Credit Support Annex, a supplier invoice and a KYC onboarding pack share
nothing as *documents* — different vocabulary, different structure, different
regulator. As *work* they are identical: pull values out, prove each one came
from the page, and refuse the result if the values do not hang together.

So there is one graph, six nodes:

```
load ──▶ extract ──▶ assemble ──▶ verify ──▶ gates ──┬──▶ accept  (gates passed)
                                                     └──▶ refer   (gates failed)
```

What changes per document type is **one thing**: the domain gates.

---

## The point of the example

Every document type gets the same five generic gates from
`evidence_gated_extraction()`:

| | |
|---|---|
| `required_fields` | every mandatory field is present |
| `no_placeholders` | nothing was filled with "TBD" or "unknown" |
| `evidence_present` | every field carries a quote |
| `evidence_verbatim` | **every quote appears verbatim in the source** |
| `independently_verified` | a separate verifier approved, and its own citations were checked |

Then each type adds the checks a JSON schema could not make:

| Document | Domain gates | |
|---|---|---|
| **CSA** | ISO 4217 currencies · amounts are numbers not strings · no clause reference in a value field · rounding directions are UP/DOWN · **MTA and Threshold are not transposed** · rounding is finer than the MTA · non-standard rounding carries its full text · valuation percentages are not haircuts · representable in CDM | 9 |
| **Invoice** | line items reconcile to the stated totals · due date follows the invoice date | 2 |
| **KYC** | applicant is of age · identity document in date · document type is accepted · required identity data points present · **name on the document matches the application** · address proof type accepted · address proof names the applicant · address proof is not a screenshot · address proof is recent | 9 |

The CSA and KYC sets are not invented. They implement two published guidelines —
see [Conformance](#conformance) below.

Run `--flaw` and watch what happens:

```
[PASS] generic  required_fields: all 14 required key(s) present
[PASS] generic  no_placeholders: no placeholder values in 19 entries
[PASS] generic  evidence_present: all 19 entries carry 'evidence_quote'
[PASS] generic  evidence_verbatim: all 19 quote(s) appear verbatim in the source
[PASS] generic  independently_verified: approved with 2 citation(s)
[PASS] domain   applicant_is_of_age: applicant is of age
[PASS] domain   identity_document_valid: identity document is in date
[PASS] domain   identity_document_type_accepted: identity document type is accepted
[PASS] domain   identity_data_points_present: required identity data points present
[FAIL] domain   name_matches_document: the name on the identity document does not
                match the name on the application. Refer to compliance.
[PASS] domain   address_proof_type_accepted: address proof type is accepted
[PASS] domain   address_proof_names_applicant: address proof names the applicant
[PASS] domain   address_proof_not_a_screenshot: address proof is not a screenshot
[PASS] domain   address_proof_recent: address proof is recent
```

**Every generic gate passed. The independent verifier approved. Every evidence
quote was genuine.** Only the cross-field gate saw it.

> Every gate set should contain at least one check a JSON schema could not
> express. If yours does not, you have written a validator, not a gate set.

---

## Three flaws, three different origins

Deliberately, because they are not the same kind of problem:

**CSA — the extraction is wrong.** Threshold and Minimum Transfer Amount are
read into each other's fields. Both quotes are still *genuine substrings of the
document* — the extractor found the right lines and assigned them to the wrong
keys, which is exactly how this happens in practice. Both values are money, both
are present, both are individually plausible.

**Invoice — the extraction is wrong.** One unit price is misread, 82.50 → 85.20.
The quote is untouched and real. Every field is present and correctly typed. The
invoice simply no longer adds up, and one line of arithmetic notices.

**KYC — the *document* is wrong.** The passport name differs from the application
form by a single letter: *Jonathon* against *Jonathan*. The extraction is
**perfect**, every quote is verbatim, and the file is still not acceptable. No
amount of extraction quality helps here — the inconsistency is in the source, and
only a check that compares two fields against each other finds it.

That third case is the one worth dwelling on. It is the most common real KYC
finding, it is the kind a human reviewer skims past, and it is invisible to every
control that looks at one field at a time.

---

## And the verifier itself

```bash
python examples/run.py intake kyc --fabricate
```

The verifier approves the pack while citing:

> *"All values in this document have been checked and are certified complete and
> internally consistent by the issuing party."*

That sentence is not in the document. The citation is substring-checked against
the source, is not found, and the approval does not survive — regardless of the
verdict the verifier returned.

Requiring cited evidence is common advice. **Checking the citation** is what makes
the approval falsifiable.

---

## Adapting this to your documents

`documents.py` is the whole surface. A new document type is one `DocumentSpec`:

```python
DocumentSpec(
    key="policy_schedule",
    title="Insurance policy schedule",
    document_id="DOC-POL-...",
    text=POLICY_TEXT,
    required=("policy_number", "inception_date", "expiry_date", "sum_insured"),
    system=POLICY_SYSTEM,        # the extraction prompt
    domain_gates=_policy_gates,  # the checks a schema cannot make
    what_the_domain_gate_catches="...",
)
```

Nothing in `app.py` changes. The order to build in:

1. **Write the domain gates first**, before the prompt. If you cannot name a
   cross-field check for your document, you do not yet know what "correct" means
   for it — and that is worth discovering in ten minutes rather than in
   production.
2. Write the extraction prompt.
3. Write a scripted policy with real quotes, and a flawed variant.
4. Add a test asserting the flawed variant fails **only** the gate you intended.
   If it fails a generic gate too, your flaw is not subtle enough to be the one
   you should be worried about.

---

## Conformance

The CSA and KYC gate sets implement published guidance rather than a plausible
guess at it. `tests/test_guideline_alignment.py` holds the code to both, one
test per rule, so a revised guideline produces a failing test that names the
rule instead of a silent divergence.

### ISDA — CSA clause extraction and CDM

From *Benchmarking Generative AI for CSA Clause Extraction and CDM
Representation* (ISDA, May 2025):

- **The five benchmarked clauses** — base currency, eligible currency, MTA,
  threshold, rounding — are all extracted and all gated.
- **CDM JSON is the deliverable.** [`cdm.py`](cdm.py) maps the elections into
  `agreementTerms → agreement → creditSupportAgreementElections`, with per-party
  `minimumTransferAmount[]` and `threshold[]` arrays. An accepted CSA carries
  `cdm`. A dictionary is not a deliverable; a collateral system consumes CDM.
- **Rounding is four fields, not one.** `rounding: 100000` cannot say that the
  Delivery Amount rounds UP while the Return Amount rounds DOWN, and that
  direction decides who ends up over-collateralised.
- **The no-rounding rule.** If the Annex is silent on rounding, no rounding
  object is emitted. A defaulted `deliveryDirection: UP` is a contractual term
  the parties never agreed, and it is invisible downstream precisely because it
  is perfectly well-formed.
- **Variant 1 / Variant 2.** Anything other than standard unconditional rounding
  is Variant 2 and must carry the *complete* provision text — not a summary.
  "Do not truncate or summarize the text, as important details may be lost."
- **The validation protocol** becomes three reusable gates: valid ISO 4217
  codes, amounts as numbers rather than `"500,000"`, and no clause pointer such
  as `13(c)(ii)` transcribed into a value field.
- **Vocabulary in the prompt.** Threshold vs Threshold Amount vs Minimum
  Transfer Amount vs Independent Amount are routinely conflated. The paper's
  central finding is that supplying this domain detail moved accuracy from
  around 67% to over 90% across every model tested — the largest single lever
  in the study, and it costs nothing.

> **The gate this replaced was wrong.** It asserted `MTA ≤ Threshold`. A 2016 VM
> CSA elects a Threshold of **zero** — variation margin is fully collateralised
> — while the MTA stays at a normal operational figure, so MTA legitimately
> exceeds Threshold in the most common CSA shape in the market. ISDA's own
> worked example is exactly that shape, and the gate referred it every time.
> The ordering only carries information above zero; at zero it says nothing, and
> a gate that says nothing must not vote. The `csa/vm-zero-threshold` eval case
> exists to keep it that way.

### AWS Marketplace — KYC documentation

From *Know Your Customer (KYC) Documentation Upload Best Practices*:

| Rule | Gate |
|---|---|
| Accepted identity documents: passport, national identity card, US passport card, driving licence, residence permit | `identity_document_type_accepted` |
| The document must show full name, date of birth, **place of birth** and **country of citizenship** | `identity_data_points_present` |
| The document must not be expired | `identity_document_valid` |
| Accepted proof of address, **excluding** statements from non-bank providers and online digital banks | `address_proof_type_accepted` |
| Must be addressed to the applicant — "names should match the ID/legal document provided" | `address_proof_names_applicant` |
| "The document must not be a screenshot" | `address_proof_not_a_screenshot` |
| "Dated within **180 days**" | `address_proof_recent` |

Two of these are worth pausing on. A statement from an e-money institution is a
perfectly well-formed bank statement that **does not count**, and nothing about
its shape says so. And a utility bill in a partner's or landlord's name proves
an address exists — it does not tie *this applicant* to it, which is the only
thing it was collected to do.

Note the guide is a business (KYB) onboarding process; this example models
individual verification, so the entity-level requirements — beneficial ownership
above 25%, statute documents, letters of authority — are noted but not
implemented.

---

## Where this fits

This example is deliberately simpler than
[`level4_invoice_intake/`](../level4_invoice_intake/), which shows the same
territory with fan-out (`MapNode`), fan-in (`ReduceNode`) and per-specialist
model routing. Start here; go there when one extraction agent is genuinely doing
several different jobs.
