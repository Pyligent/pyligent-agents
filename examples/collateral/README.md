# Collateral: from a signed agreement to a certified constraint pack

```bash
python examples/run.py shadow           # the margin system agrees
python examples/run.py shadow --drift   # ...and the case where it does not
python examples/run.py shadow --json    # the constraint pack itself
```

This is the chain the rest of the repository builds toward:

```
CSA text ─▶ extraction ─▶ evidence check ─▶ gates ─▶ CDM ─▶ constraints ─▶ certification
```

and it stops there, on purpose. Allocation, optimisation and settlement are
downstream and out of scope here.

---

## The finding a trial exists to produce

Not "the AI agrees with us". A short list of counterparties whose **stored terms
do not match their signed agreement** — each with the clause that settles it.

```
── MATERIAL · threshold ───────────────────────────────────
  agreement says : 0
  system says    : 5000000
  impact         : changes when a call is made and by how much
  clause         : ""Threshold" means with respect to each party: USD 0."
```

The parties adhered to the VM protocol in 2017; the Threshold went to zero
because variation margin is fully collateralised. The amendment was executed.
Nobody re-keyed it. Every call since has been sized against a 5,000,000
unsecured band that no longer exists.

Nothing in the margin system looks wrong. Every field is populated, every value
is plausible, and no reconciliation of that system against itself will ever find
it. Only reading the agreement finds it — which is the argument for the trial.

Severity is not decoration. A `threshold` mismatch changes the size of every
call; a `governing_law` mismatch is record-keeping. The report sorts by what a
human should read first.

---

## Two properties, both enforced rather than promised

**Nothing is written.** A shadow run denies every tool above `READ_ONLY` at the
harness — *even when an approver is attached*, so the guarantee does not depend
on how the stack happened to be built. The test is
`test_shadow_mode_cannot_reach_a_tool_with_an_external_effect`.

**Nothing is claimed without a clause.** Every constraint carries the verbatim
agreement text behind it, already proved a substring of the source by the gates
upstream. A finding a counterparty cannot check is an opinion with a number
attached.

---

## What certification actually asks

`certify()` is not "did it parse":

| Question | Failure means |
|---|---|
| Is every constraint kind an optimiser needs present? | It cannot size a call |
| Does every constraint cite a clause? | The recommendation is not defensible |
| Was every cited clause confirmed in the source? | Something was invented |
| Is anything in the agreement **not** expressible? | A human must read it first |

That last row is the one usually skipped, and it is why `certify()` returns
reasons rather than a boolean:

```
  constraints derived : 14
  every one traceable : yes
  certified for use   : NO
    · 1 term(s) in the agreement are not expressible as constraints and must be
      read by a human before this counterparty is optimised.
```

A CSA contains terms no linear constraint set represents — a Valuation Agent's
discretion, a ratings trigger, substitution rights, bespoke dispute mechanics.
Dropping them silently produces a pack that **looks complete and is not**, and
the optimiser then solves the wrong problem with total confidence.

`allow_unsupported=True` lets a human accept that risk explicitly. That is a
different thing from never being told.

---

## Adapting it

`SYSTEM_OF_RECORD` in [`app.py`](app.py) is a fixture. In a trial it is an
extract from the collateral system, and the only thing that changes is where the
dictionary comes from.

`UNSUPPORTED_MARKERS` in [`constraints.py`](constraints.py) is deliberately
short and deliberately conservative. Add to it whenever you meet a term your
constraint model does not represent — the cost of a false positive is a human
reading a clause; the cost of a false negative is an optimiser confidently
solving the wrong problem.

---

## Honest limits

- The documents here are **synthetic**. Nothing in this repository is calibrated
  to any institution's policy, and the gate thresholds are worked examples.
- The extractor is the deterministic backend. It proves the *pipeline*, not
  extraction quality against real scanned agreements — see
  [`evals/`](../../evals/README.md) for how quality is measured separately.
- Certification is a statement about internal coherence and traceability. It is
  not a legal opinion and not a compliance attestation.
