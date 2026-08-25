# ADR 0003 — An effect ledger, separate from checkpoints

**Status:** accepted · **Date:** 2026-08-25

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

Node execution resolves in three stages:

1. finished in this run's checkpoints? → replay from the checkpoint
2. idempotency key already on the ledger? → **replay from the ledger**
3. otherwise: checkpoint, execute, record the effect in the same moment we learn
   it happened

Keys are built by `idempotency_key(action, **facts)` from the facts of the
action. `idempotency_key()` with no facts raises.

## Consequences

**Good.** The guarantee survives losing the node checkpoint entirely
(`test_a_wiped_checkpoint_still_cannot_double_refund`). Two workers racing on the
same resumed run cannot both refund — the second `INSERT` loses, rather than both
reading "not yet done" and proceeding. Keys are human-readable
(`refund:amount=257.99|fault=seller|order=A-1207`), so support can read off the
ledger why a refund did not re-send.

**Bad.** A fact-derived key suppresses a *legitimate* repeat of an identical
action. In refunds the second identical refund on the same order is almost always
an error, so that default is right — but it is a domain judgement and must be
re-made per domain. Where genuine repeats happen, add a business-meaningful
discriminator: a case reference, not a clock.

**Also.** Key format is a migration surface, not a refactor. Change it and every
in-flight run silently becomes eligible to re-fire.
