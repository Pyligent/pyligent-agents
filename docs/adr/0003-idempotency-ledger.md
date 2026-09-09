# ADR 0003 — An effect ledger, separate from checkpoints

**Status:** accepted · **Date:** 2026-08-25 · **Amended:** 2026-09-09

## Context

Checkpointing after every step is the standard advice for durable agents, and it
is right. It is also insufficient.

There is a window: the side effect succeeded externally, and the state write had
not yet landed.

```
t0  node starts, checkpoint written
t1  the payment processor ACCEPTS the refund      ← it happened
t2  the process dies before the completion write
t3  resume: the checkpoint says it never finished
t4  ...so we refund the customer again.
```

Checkpoints narrow that window. They do not close it.

## Decision

A second table, `effects`, with `PRIMARY KEY (run_id, key)` — the constraint
enforced by the database, not the application.

A row is written in **two stages**, and the ordering is the decision:

| stage | when | meaning |
|---|---|---|
| `claimed` | **before** the side effect is attempted | we are about to act, or we died trying |
| `recorded` | once the action has returned | it happened, and this is what it returned |

Node execution resolves in four stages:

1. finished in this run's checkpoints? → replay from the checkpoint
2. **claim the key.** The `INSERT` either succeeds — the claim is ours and no
   other worker holds it — or it loses to a row that is already there
3. that row is `recorded`? → **replay from the ledger**
4. that row is `claimed`? → **stop the run.** Another worker is inside this
   action, or one died inside it. Its outcome is unknown, and repeating an
   action of unknown outcome is the one thing this table exists to prevent

Keys are built by `idempotency_key(action, **facts)` from the facts of the
action. `idempotency_key()` with no facts raises.

**Claims are released only by a reported failure.** An action that raised —
that *told* us it did not happen — releases its claim, and a later run may try
again. An action that returned nothing at all keeps its claim. That asymmetry is
the guarantee: a known negative may be retried, an unknown may not.

## What this establishes, precisely

**It does.** Replay suppression for a recorded effect, including when the node
checkpoint is lost entirely (`test_a_wiped_checkpoint_still_cannot_double_refund`).
A decided race between two workers, because the claim is taken before the action
rather than after — `test_a_second_worker_inside_the_window_cannot_fire_the_same_effect`
runs the second worker *inside* the first one's side effect, which is the interval
a check-then-act ledger cannot see. And an interrupted action that becomes a run
that stops and asks (`test_an_effect_claimed_but_never_recorded_stops_the_run`)
rather than one that quietly repeats.

**It does not.** Make the external call exactly-once. Nothing on this side of the
wire can. The claim narrows the unknown window to the interval between the claim
and the record, and makes what lands in it *detectable* — it does not remove it.
A process can die after the custodian accepts an instruction and before the
ledger learns of it, and the ledger will then correctly say "unknown", which is
an answer a human has to resolve against the destination system.

**Anything stronger needs the destination.** Exactly-once for a real external
effect requires an idempotency key that system honours, or a transactional
protocol with it. That is not a gap to apologise for; it is where the boundary
actually falls, and a framework that claims otherwise is claiming something it
cannot enforce.

**Scope.** The key is `(run_id, key)`. Two different runs of the same action are
two different claims. Deduplicating across runs is a business decision about what
counts as the same action, and belongs in the key's facts, not in the table.

## Consequences

**Good.** The guarantee survives losing the node checkpoint entirely. Two workers
racing on the same resumed run cannot both act. Keys are human-readable
(`refund:amount=257.99|fault=seller|order=A-1207`), so support can read off the
ledger why a refund did not re-send — and, now, why one stopped.

**Bad.** A crash inside an action leaves a claim that blocks the run until a
human resolves it. That is the intended trade — a stopped run is cheaper than a
duplicate payment — but it is an operational burden, and it needs somewhere for
that human to act. There is no review queue in this repository; see
[CAPABILITIES.md](../CAPABILITIES.md).

**Bad.** A fact-derived key suppresses a *legitimate* repeat of an identical
action. In refunds the second identical refund on the same order is almost always
an error, so that default is right — but it is a domain judgement and must be
re-made per domain. Where genuine repeats happen, add a business-meaningful
discriminator: a case reference, not a clock.

**Also.** Key format is a migration surface, not a refactor. Change it and every
in-flight run silently becomes eligible to re-fire.

## Amendment, 2026-09-09

The original version of this record claimed that two workers racing "cannot both
refund", and the runner at the time checked the ledger and *then* executed, so
both workers could read "not yet done" and proceed. The losing `INSERT` prevented
a duplicate ledger row, not a duplicate refund. Every idempotency test was a
sequential re-run, which is why the gap survived review.

Raised in external review; the claim was overstated, and the code has been
changed to support the claim rather than the claim weakened to match the code.
The two-stage row, the four-stage resolution, and the concurrency tests named
above are that change. The "what this establishes, precisely" section is new,
and exists so the next reader does not have to reproduce the finding.
