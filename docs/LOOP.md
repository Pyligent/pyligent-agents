# Layer 2 — Loop engineering

> The loop owns **control**: when to stop, and what to do when something breaks.

```
                   ┌─────────────────────────────────────────────┐
                   │                                             │
   task ──────────▶│  GATHER / ACT     model + tools             │
                   │       │                                     │
                   │       ├─ wants tools? ──▶ dispatch ─────────┤
                   │       │                   recovery policy   │
                   │       │                                     │
                   │       └─ says done? ──▶ extract artifact    │
                   │                              │              │
                   │                         VERIFY phase        │
                   │                              │              │
                   │                    stop condition holds?    │
                   │                    no ──▶ push back ────────┤
                   │                    yes ──▶ return           │
                   └─────────────────────────────────────────────┘
```

The loop is about forty lines. Everything interesting is the four things wrapped
around it.

```bash
python examples/run.py demo loop
```

---

## 1. The contract — the four questions as a type

```python
AgentContract(
    goal="Answer a customer's question about an order.",
    stop=ModelSaysDone() & Predicate(grounded, "figures traceable to tools"),
    verifier=no_verification("Figures come from tested deterministic tools."),
    budget=Budget(max_turns=8, max_usd=0.50, max_seconds=120),
    on_failure=OnFailure.ESCALATE,
)
```

You cannot construct an `Agent` without all four. The forcing functions:

- `Budget(max_turns=0)` → `ContractViolation: an uncapped loop is the bug`
- `Budget(max_usd=0)` → `ContractViolation: 'No cap' is not a cap`
- `no_verification("later")` → refused; the reason must be a real sentence
- `OnFailure.DEGRADE` without `degrade_to` → refused; degrading to nothing is a crash

`contract.summary()` prints the four answers. Put it in your run log and your PR
description.

---

## 2. The stop condition — written before the prompt

The model never gets to declare the task done. That single pattern eliminates
the whole category of looks-done-but-isn't bugs, and it only works if "done" is
a predicate rather than a sentence the model writes.

| Condition | Use when |
|---|---|
| `ModelSaysDone()` | open-ended analysis, no external check exists |
| `GatesPass(gates)` | the output is an artifact something downstream consumes |
| `Produced("key")` | a specific field must exist and be non-empty |
| `Predicate(fn, "label")` | anything else |

Compose with `&` / `|`. The composite describes itself in traces:

```
(model_says_done AND predicate(figures traceable to tools))
```

`ModelSaysDone()` is legitimate — plenty of tasks genuinely end when the model
stops calling tools. It is just an *explicit* choice, named in the contract,
rather than the default that happens when nobody thought about it.

### The grounding predicate, worth stealing

```python
def _grounded(state: LoopState) -> bool:
    if "$" not in state.answer and "USD" not in state.answer:
        return True                       # no figure claimed
    return any(o.tool_name in CALCULATORS and not o.is_error for o in state.outcomes)
```

Demo section B2 shows it working. The model asserts *"We can refund you
£310.00 for order A-1207"* — fluent, confident, entirely invented, and nothing
in the text signals it. Under `ModelSaysDone()` alone that goes to a customer.
The grounding predicate rejects it, says exactly why, and the agent fetches the
real number (£257.99).

```
turn 1  stop_check  figures traceable to tools: not met
turn 1  push_back   figures traceable to tools: not met
turn 3  stop_check  model_says_done and predicate(figures traceable to tools)
```

---

## 3. The verify phase

Most loops have two phases. The third is where trust comes from.

When the model stops calling tools, that is a **candidate**, not a conclusion:

1. extract an artifact from the run
2. run the verify phase
3. evaluate the stop condition
4. if it does not hold, push back with the *specific* gap and continue

Two different checks live here, and they are not interchangeable:

**Self-check** (`self_check=True`, optional). Same model, fresh context,
read-only tools, asked whether the result answers the goal. Cheap. Catches
carelessness. **Does not catch blind spots** — an agent that misread a paragraph
reads it the same way twice.

**The contract's verifier.** A different agent that sees the artifact and the
source and *nothing about how the artifact was produced*. This is the one that
counts. See [`GRAPH.md`](GRAPH.md#verification) and
[`verification/verifier.py`](../src/trellis/verify/verifier.py).

### Push back, never "try again"

```python
ctx.append_user(
    f"NOT DONE YET. The completion check failed: {reason}\n"
    f"Address that specific point and continue. Do not restate work you have "
    f"already done."
)
```

Naming the gap turns a retry into progress. "Try again" produces the same answer
with more adjectives.

---

## 4. Recovery — one branch per error class

The taxonomy in [`core/errors.py`](../src/trellis/core/errors.py) exists so
this is a lookup table rather than a pile of heuristics:

| Class | Action | Why |
|---|---|---|
| `TRANSIENT` | **RETRY** with backoff | the world was briefly unavailable |
| `INVALID` | **OBSERVE** | the agent's own arguments were wrong; it must change something |
| `DOMAIN` | **OBSERVE** | a legitimate business refusal; let it route around |
| `PERMISSION` | **OBSERVE** | present for sign-off |
| `FATAL` | **ESCALATE** | a human owns this |

Two limits stop recovery becoming its own runaway:

- **per-tool retry cap** — retrying an `INVALID` call with identical arguments
  is a busy-wait that costs money
- **consecutive-failure cap** — an agent whose last four tool calls all failed is
  thrashing, not recovering, and continuing costs money to learn nothing

Unknown exceptions classify as `FATAL`, not `TRANSIENT`. An unrecognised failure
must never be retried into a bill.

---

## 5. Budgets, and what to do when one binds

Four limits, checked before every call. Whichever binds first stops the run,
loudly.

```
turn cap: StopConditionNotMet
  Did not satisfy '(model_says_done AND predicate(figures traceable to tools))'
  within 4 turn(s). Last check: figures traceable to tools: not met
  spent before the stop: $0.0204

spend cap: BudgetExhausted
  spend budget exhausted: 0.0204 USD used against a 0.02 USD cap.
```

Note the turn-cap message includes **the last failing check**. That is the
difference between a diagnosable incident and a shrug.

> **Raising the cap is never the fix.** Hitting it means one of three things:
> the task is wrong for this architecture, a tool is missing, or the prompt is
> ambiguous. Diagnose before you touch the number.

`OnFailure.DEGRADE` returns a declared safe default with `degraded=True` instead
of raising — right for a high-volume classifier where one bad message should not
take the queue down, wrong for anything that moves money to a customer.

---

## 6. Message discipline

Two details that look cosmetic and are not:

```python
ctx.append(assistant_turn(response))    # response.content, VERBATIM
ctx.append(tool_result_turn(blocks))    # ALL results, ONE message
```

Rebuilding the assistant turn from `response.text` drops the `tool_use` blocks;
the next request then carries a `tool_result` with no matching `tool_use` and
returns a 400. This is the most common first bug when hand-writing a loop.

Splitting results across several messages works, and quietly trains the model out
of asking for parallel calls, so every later turn gets slower. Both are pinned by
tests.

---

## What to copy into your own system

- A contract type that refuses to be constructed incomplete.
- A stop condition written before the prompt.
- A verify phase between "model stopped" and "we're done".
- Push-back messages that name the specific gap.
- One recovery branch per error class, with two runaway limits.
- Turn-cap failures that report the last failing check.
