# Examples

Four applications on one domain, one per rung of the ladder. The domain —
`shopdesk`, a support and operations desk for an online retailer — is
deliberately ordinary, so nothing about the *domain* is what you are learning.

```bash
python examples/run.py triage                      # Level 1
python examples/run.py order-agent --trace         # Level 2
python examples/run.py refund                      # Level 3 — pauses
python examples/run.py resume <run_id> --approve
python examples/run.py invoice [--fabricate | --transposed]   # Level 4

python examples/run.py demo harness|loop|graph|ladder|all
```

Everything runs offline. Set `ANTHROPIC_API_KEY` and `TRELLIS_BACKEND=anthropic`
and the identical code runs against the real API.

---

## The shared domain: `shopdesk/`

| File | What it holds |
|---|---|
| `money.py` | refund and invoice arithmetic — pure, deterministic, tested |
| `data.py` | a seeded book of orders, tickets and one supplier invoice |
| `errors.py` | domain refusals, subclassing `trellis.DomainRefusal` |
| `tools.py` | the tool surface, with tiers, deferral and trust flags |

**No model output is ever a monetary figure.** Every amount comes from
`money.py`. Read that file first; the agents above it are the easy part.

The seed contains the awkward cases on purpose:

| Order | Wrinkle | Used by |
|---|---|---|
| `A-1207` | delivered 8 days late; fully refundable | all four |
| `A-1310` | outside the 30-day window — the domain **refuses** | Level 2 |
| `A-1422` | the carrier API is **down** — the tool *raises* | Level 2 |
| `A-1588` | already refunded in full — a naive refund would pay twice | Level 2 |

A demo containing only the happy path teaches nothing about production.

---

## Level 1 — `level1_triage/`

Classify a support ticket. One model call. No tools, no loop, no memory.

**Why it stops here:** bounded to a single turn, needs nothing the model does not
already have. There is no `Agent` in the file — a loop with `max_turns=1` is a
loop pretending not to be one. The *contract* is still written down, because it
records that nobody verifies this and why that is acceptable.

What is actually engineered: a closed output vocabulary validated *after* the
model answers, and a deterministic fallback so a malformed reply routes to a
human instead of taking the inbox down.

## Level 2 — `level2_order_agent/`

Answer "why is order A-1207 late, and what can we offer?"

**Why Level 1 broke:** the answer depends on order data, live tracking, and a
refund figure the model must not compute.

The interesting line is the stop condition — three predicates, all required:

```python
stop=(ModelSaysDone()
      & Predicate(_grounded, "amounts traceable to tools")
      & Predicate(_no_invented_dates, "no delivery date without tracking"))
```

Run `demo loop` section B2 to watch the second one catch a confident, invented
£310.00 refund that nothing in the text signals.

## Level 3 — `level3_refund_workflow/`

A refund from ticket to money-moved: seven nodes, one human gate, two idempotent
side effects.

**Why Level 2 broke:** it forgets everything when the function returns, and this
work spans a restart, a deploy and a shift change.

```bash
python examples/run.py demo graph   # section E: three executions, one refund
```

## Level 4 — `level4_invoice_intake/`

Extract a supplier invoice with evidence, gate it, verify it independently, post
it to the ledger.

**Why Level 3 broke:** four different jobs — read, transcribe, reconcile, prove.

**The gate worth studying** is `lines_sum_to_total`. Run:

```bash
python examples/run.py invoice --transposed
```

One unit price is mis-read: 82.50 → 85.20. Every field is present, every type is
right, the evidence quote is **real**, and the independent verifier **approved
it**. One line of arithmetic caught it. No JSON schema would have.

And:

```bash
python examples/run.py invoice --fabricate
```

The verifier approves the artifact while citing text that is not in the document.
Its citation is substring-checked against the source, the approval does not
survive, `post_to_ledger` is skipped and `escalate` runs instead.

---

## Adapting one

The fastest path to your own domain:

1. Copy `shopdesk/money.py` → your deterministic logic, and test it.
2. Copy `shopdesk/tools.py` → your tools. Get the **tiers** right before anything
   else.
3. Copy the level closest to your task's shape.
4. Rewrite the stop condition. It will be different, and it is the part that
   matters most.

Or start from scratch with guardrails already wired:

```bash
trellis new my_agent && cd my_agent && pytest
```
