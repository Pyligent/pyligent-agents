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

**Nothing is claimed without a clause — its own.** Every constraint carries the
verbatim agreement text behind it, already proved a substring of the source by
the gates upstream, and an eligibility row cites the schedule line that lists it
rather than a definition from elsewhere in the document. A finding a
counterparty cannot check is an opinion with a number attached; a finding
carrying somebody else's citation is worse, because it survives inspection.

---

## What certification actually asks

`certify()` is not "did it parse":

| Question | Failure means |
|---|---|
| Is every constraint kind an optimiser needs present? | It cannot size a call |
| Does every constraint cite a clause? | The recommendation is not defensible |
| Does it cite the clause that establishes **it**? | The citation is real and proves something else |
| Was every cited clause confirmed in the source? | Something was invented |
| Did anything the source implied fail to make it in? | The pack reads as complete and is not |
| Did a known unsupported-term marker match? | A human must read it first |

**What a certified pack establishes, exactly.** Every constraint quotes a clause
that appears in the source and mentions what the constraint is about; the kinds
an optimiser needs are present; nothing was dropped in silence; no known marker
matched. **What it does not establish:** that the quoted clause is the
*governing* one, that amendments and precedence were resolved, or that the
agreement holds no unsupported term the marker list has never seen. Those are
human judgements. Certification is a floor beneath them, not a substitute.

That last row is the one usually skipped, and it is why `certify()` returns
reasons rather than a boolean:

```
  constraints derived : 14
  every one traceable : yes
  could not establish : 0
  certified for use   : NO
    · 1 known marker(s) for terms that are not expressible as constraints
      matched the source and must be read by a human before this counterparty
      is optimised.
```

A CSA contains terms no linear constraint set represents — a Valuation Agent's
discretion, a ratings trigger, substitution rights, bespoke dispute mechanics.
Dropping them silently produces a pack that **looks complete and is not**, and
the optimiser then solves the wrong problem with total confidence.

`allow_unsupported=True` lets a human accept that risk explicitly. That is a
different thing from never being told.

**The marker scan is a tripwire, not an inventory.** It matches a fixed list of
wordings on the source text, so it misses a provision phrased in a way the list
has not seen, and it fires on a mention that creates no unsupported condition —
on the SEC benchmark corpus at least one marker matches in 97 of 97 documents,
median five. Both errors point the same way, towards asking a human, which is
the bias to want here. What it supports is *"no known marker was detected."* It
does not support *"all material obligations are represented"*, and a clause
inventory — mapped, referred, excluded with a reason, unresolved — is the thing
that would.

**A row that cannot be quoted is dropped, and says so.** `ConstraintPack.omitted`
carries every constraint the source implied but that could not be established,
with the reason, and any entry in it blocks certification. An eligibility row
with no quote of its own, a quote that does not mention the asset it claims to
establish, a valuation percentage above 100, or a percentage that does not appear
in its own clause — each becomes a line a human reads rather than a constraint an
optimiser trusts.

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
