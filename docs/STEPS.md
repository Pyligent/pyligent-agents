# The ten steps

How to build a production agent, in the order that works.

Steps 1–3 contain no model at all. That is not an accident: most agent failures
are domain failures and permission failures wearing an AI costume, and they are
cheapest to fix before there is a model in the room.

```bash
trellis steps        # this list, in your terminal
```

---

## Step 1 — Write the domain first, without a model

Implement the business logic as pure, deterministic, tested functions.

**Why:** every figure your agent quotes has to come from somewhere you can
defend. When a customer disputes a £257.99 refund, "the agent said so" is not an
answer. An itemised breakdown from tested code is.

```python
# examples/shopdesk/money.py
def quote_refund(order, *, fault, today) -> RefundQuote: ...
```

> **The rule you carry through everything else:** no model output is ever a
> monetary figure. The model chooses *which* calculation to run and *how to
> explain it*. The number comes from code.

📄 [`examples/shopdesk/money.py`](../examples/shopdesk/money.py)

---

## Step 2 — Pick a level, and write down what forced it

Level 1 (one call), 2 (tool loop), 3 (durable), or 4 (fan-out). Write one
sentence naming the concrete breakage that rules out the level below.

```bash
python examples/run.py demo ladder    # what each level actually costs
```

📄 [`docs/LADDER.md`](LADDER.md) — "It felt limiting" is not a breakage.

---

## Step 3 — Define the tools, with tiers

```python
ToolSpec(name="quote_refund",  tier=PermissionTier.READ_ONLY)
ToolSpec(name="issue_refund",  tier=PermissionTier.RESTRICTED)
```

One computes what we may refund; the other moves money and cannot be undone from
here. Giving them the same trust level is the mistake that shows up on a bank
statement.

Mark rarely-used tools `defer_loading=True`, and anything returning text written
outside your team `trusted=False`.

📄 [`examples/shopdesk/tools.py`](../examples/shopdesk/tools.py)

---

## Step 4 — Stand up the harness

```python
from trellis import build_stack
stack = build_stack(registry=my_tools)
```

One code path for model calls, one for tool calls — so there is exactly one place
to enforce a permission, meter a cost, redact a secret or trace a decision.

```bash
python examples/run.py demo harness
```

📄 [`docs/HARNESS.md`](HARNESS.md)

---

## Step 5 — Answer the four questions, as a contract

```python
AgentContract(
    goal="Answer a customer's question about an order.",
    stop=ModelSaysDone() & Predicate(grounded, "amounts traceable to tools"),
    verifier=no_verification("Every amount comes from quote_refund, which is tested."),
    budget=Budget(max_turns=8, max_usd=0.40, max_seconds=90),
    on_failure=OnFailure.ESCALATE,
)
```

You cannot construct an `Agent` without all four, and `no_verification()` refuses
a reason shorter than a sentence. Everyone agrees the four questions matter; as
constructor arguments they cannot drift out of the code.

📄 [`src/trellis/loop/contract.py`](../src/trellis/loop/contract.py)

---

## Step 6 — Write the stop condition before the prompt

If you cannot express "done" as a predicate, **you do not yet understand the task
well enough to automate it.** Prompt-first development hides that; predicate-first
surfaces it in ten minutes.

| Condition | Use when |
|---|---|
| `ModelSaysDone()` | open-ended analysis, no external check exists |
| `GatesPass(gates)` | the output is an artifact something downstream consumes |
| `Produced("key")` | a specific field must exist |
| `Predicate(fn, "label")` | anything else — name it, you will read it in traces |

Compose with `&` and `|`.

📄 [`src/trellis/loop/stop.py`](../src/trellis/loop/stop.py)

---

## Step 7 — Run the loop: gather, act, verify, repeat

The third phase is the one most implementations omit. Without it, "done" means
the model stopped calling tools — an opinion. With it, the model proposing it is
finished is a **candidate**: extract an artifact, verify, check the stop
condition, and if it fails, hand back the *specific* gap.

```bash
python examples/run.py demo loop     # section B2: an invented refund, caught
```

📄 [`docs/LOOP.md`](LOOP.md)

---

## Step 8 — Promote to a graph when the work outlives one loop

```python
Graph("refund_workflow", seeds=("ticket_id",)).extend(
    Step(id="quote", fn=..., provides=("quote",)),
    AgentNode(id="draft_reply", depends_on=("quote",), ...),
    HumanGate(id="approve_refund", ...),
    Step(id="issue_refund", idempotency=..., ...),
)
```

A graph is inspectable *before* it executes: missing inputs, cycles and
unreachable state are build-time errors rather than a confident answer built on
a hole. Checkpointing, resume, replay and tracing come from the runner.

```bash
trellis graph show level3_refund_workflow.app:build_graph
python examples/run.py demo graph
```

📄 [`docs/GRAPH.md`](GRAPH.md)

---

## Step 9 — Put an idempotency key on every external effect

```python
Step(id="issue_refund",
     idempotency=lambda s: idempotency_key(
         "refund", order=..., amount=..., fault=...))
```

Checkpointing narrows the window between "the payment processor accepted it" and
"our state write landed". Only a ledger with a database-level uniqueness
constraint closes it.

**Derive the key from the facts of the action.** Never a timestamp, never a uuid,
never a retry counter — a key that changes on every attempt guarantees the
duplicate it was supposed to prevent.

```bash
python examples/run.py demo graph    # section E: 3 runs, 1 refund
```

---

## Step 10 — Prove each guardrail with a test that fails without it

```python
from trellis.testing import assert_capped, build_test_stack, looping

def test_a_looping_model_is_stopped(registry):
    stack = build_test_stack(looping("get_order", order_id="A-1"), tools=registry)
    assert_capped(lambda: build(stack.harness).run("go"))
```

A rule that is not a failing test is a rule that will be broken within two
quarters, by someone who was not in the room when you agreed it.

📄 [`docs/TESTING.md`](TESTING.md)

---

## Then, before any unattended run

1. **What is the stop condition?** A predicate. Not "when it's done."
2. **Who verifies before it ships?** If it is the same agent, you have a model
   agreeing with itself.
3. **What is the spend cap?** Code that raises. Not a dashboard.
4. **What happens when a subagent fails?** Blocked dependents, a bounded retry,
   a human — pick one and write it down.

---

## The half-day path

| | |
|---|---|
| 0:00 | `pip install -e ".[dev]" && pytest` |
| 0:10 | `trellis steps` |
| 0:20 | Step 1 — read `examples/shopdesk/money.py`, run its tests |
| 0:40 | `python examples/run.py demo harness` — read every section |
| 1:20 | `python examples/run.py demo loop` |
| 2:00 | `python examples/run.py demo graph` |
| 2:45 | `python examples/run.py demo ladder` |
| 3:00 | `trellis new my_agent` and build something |
