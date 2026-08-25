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

| Document | Domain gates |
|---|---|
| **CSA** | MTA does not exceed the Threshold · valuation percentages are not haircuts |
| **Invoice** | line items reconcile to the stated totals · due date follows the invoice date |
| **KYC** | applicant is of age · identity document in date · **name on the document matches the application** · proof of address is recent |

Run `--flaw` and watch what happens:

```
[PASS] generic  required_fields: all 9 required key(s) present
[PASS] generic  no_placeholders: no placeholder values in 14 entries
[PASS] generic  evidence_present: all 14 entries carry 'evidence_quote'
[PASS] generic  evidence_verbatim: all 14 quote(s) appear verbatim in the source
[PASS] generic  independently_verified: approved with 2 citation(s)
[PASS] domain   applicant_is_of_age: applicant is of age
[PASS] domain   identity_document_valid: identity document is in date
[FAIL] domain   name_matches_document: the name on the identity document does not
                match the name on the application. Refer to compliance.
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

## Where this fits

This example is deliberately simpler than
[`level4_invoice_intake/`](../level4_invoice_intake/), which shows the same
territory with fan-out (`MapNode`), fan-in (`ReduceNode`) and per-specialist
model routing. Start here; go there when one extraction agent is genuinely doing
several different jobs.
