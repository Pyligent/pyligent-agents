# Testing agents

The most useful thing Pyligent Agents gives you is that **agent behaviour is testable**.

Turn caps, error recovery, permission denials, compaction triggers and
idempotency guarantees cannot be tested against a live model: it behaves
differently on the retry and hides the bug. A flaky test on a control is worse
than no test, because it gets marked `xfail` and the control quietly stops being
one.

`ScriptedLLM` is not a mock of an SDK. It is a second implementation of the same
`LLMClient` contract, driven by policies you write — and it runs through the
exact `build_stack` path production uses. There is no separate test harness,
because a separate test harness is a harness you are not testing.

```python
from pyligent_agents.testing import build_test_stack, calls, turn, tools_used
```

---

## Writing a policy

A policy is a function from a `ScriptedCall` to a `ScriptedTurn`.

```python
def policy(call):
    if not call.called("get_order"):
        return calls("get_order", order_id="A-1207")
    return turn("Order A-1207 was delivered on the 14th.")

stack = build_test_stack(policy, tools=my_registry)
result = build_agent(stack.harness).run("When did A-1207 arrive?")
assert tools_used(stack) == ["get_order"]
```

### Turn builders

| Builder | Produces |
|---|---|
| `turn(text)` | a final answer — a completion *candidate* |
| `calls(name, **args)` | one tool call |
| `parallel(("a", {}), ("b", {}))` | several tool calls in one assistant turn |
| `refusal()` | `stop_reason="refusal"` — exercises the check-before-content path |
| `truncated()` | `stop_reason="max_tokens"` |

### Policy builders

| Builder | Use |
|---|---|
| `sequence(t1, t2, ...)` | a fixed script |
| `router({"substring of system prompt": policy})` | one client, several agents |
| `looping(tool, **args)` | a model that never stops |
| `after_pushback(before, after)` | behave one way until the loop pushes back |

`router` matters more than it looks. In one graph run an orchestrating node,
several workers and a verifier share a client; the system prompt is how a policy
tells them apart — which is how prompt-routing works in real fixtures too.

### Inspecting the conversation

`ScriptedCall` exposes what the agent has actually done:

```python
call.called("quote_refund")      # has this tool been called?
call.last_tool_results()         # results from the most recent user turn
call.tool_names()                # what is advertised on THIS call
call.call_index                  # monotonic; survives compaction
```

> **Count `call_index`, not messages.** Compaction *removes* messages, so a
> policy whose exit condition counts them never terminates. Any real loop whose
> exit condition reads the transcript breaks the day you enable compaction — the
> fixture in this repo hit exactly that.

---

## The five tests every agent should have

### 1. It converges using the tools you expect

```python
def test_the_agent_answers_from_tools(registry):
    stack = build_test_stack(policy, tools=registry)
    result = build(stack.harness).run("Why is A-1207 late?")
    assert result.ok
    assert tools_used(stack) == ["get_order", "get_tracking", "quote_refund"]
```

### 2. A runaway is stopped

```python
def test_a_looping_model_is_stopped(registry):
    stack = build_test_stack(looping("get_order", order_id="A-1"), tools=registry)
    assert_capped(lambda: build(stack.harness).run("go"))
```

`assert_capped` fails if the agent *finishes* — a runaway test whose model can
terminate is not testing a runaway.

### 3. A failing tool does not end the run, and nothing is invented

```python
def test_a_carrier_outage_does_not_produce_a_fake_date(registry):
    stack = build_test_stack(policy, tools=registry)
    result = build(stack.harness).run("Where is order A-1422?")
    assert result.ok and result.failed_tool_calls >= 1
    assert "will arrive" not in result.answer.lower()
```

The second assertion is the valuable one. Recovering is easy; recovering *without
filling the gap from memory* is the property you actually want.

### 4. An ungrounded answer does not satisfy the stop condition

```python
def test_an_invented_amount_is_rejected(registry):
    stack = build_test_stack(ungrounded_policy, tools=registry)
    result = build(stack.harness, max_turns=6).run("How much can we refund?")
    assert [e for e in stack.ledger.events if e.kind == "push_back"]
    assert "£257.99" in result.answer   # the real figure, fetched after push-back
```

### 5. The side effect fires exactly once

```python
def test_the_customer_is_refunded_once(tmp_path, registry):
    stack = build_test_stack(policy, tools=registry, state_dir=tmp_path)
    first = stack.runner(graph).start("Refund", {"ticket_id": "T-9001"})
    for _ in range(2):
        s = build_test_stack(policy, tools=registry, state_dir=tmp_path)
        s.runner(graph).resume(first.run_id, decisions={"approve_refund": {"approved": True}})

    assert_effects_fire_once(final_stack, first.run_id, expected=2)  # refund + email
```

This is the assertion worth putting in front of stakeholders. Run the workflow
three times; count the refunds.

---

## Asserting on what the model was shown

`capture_prompts` records every request as it happens:

```python
def test_the_verifier_never_sees_the_producers_reasoning(registry):
    stack = build_test_stack(policy, tools=registry)
    seen = capture_prompts(stack)
    run_the_pipeline(stack)

    verifier_prompt = seen[-1]["messages"][-1]["content"]
    assert "SENTINEL_PRIOR_VERDICT" not in verifier_prompt
```

Also useful for asserting that tool results come back in **one** message, that
the assistant turn is replayed verbatim (with its `tool_use` blocks), and that
the system prompt is byte-stable across turns — the last of which is what makes
prompt caching work.

---

## Testing gates

Gates need no harness at all. They are pure functions:

```python
def test_a_transposed_digit_fails_only_the_arithmetic_gate():
    report = invoice_gates().evaluate({**GOOD, **TRANSPOSED})
    assert [f.name for f in report.failures] == ["lines_sum_to_total"]
```

Write the *wrong* artifacts first. Three artifacts that are plausible,
schema-valid and wrong will teach you more about your gate set than ten correct
ones.

---

## Running against a real model

Everything above runs offline. When you want a live smoke test:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
PYLIGENT_AGENTS_BACKEND=anthropic pytest tests/test_smoke_live.py
```

Keep those tests separate and few. They tell you the wiring is right; they cannot
tell you the guardrails hold.

---

## Checking an extraction without writing a test

Some failures are not control-flow failures and no scripted turn will find them.
For those there is a command:

```bash
evidence-check contract.html extraction.json
```

Three checks, mutually exclusive, at most one finding per field: the citation is
not in the document; the citation is genuine and names a **different** value;
the field offered nothing to check. No model, no network, deterministic — so a
report can be committed and a change in it means the extraction changed.

The middle one is what a scripted test cannot reach. A model that quotes the
right line and writes a different number passes every structural check you would
think to write.

[`docs/SPEC-evidence-checks.md`](SPEC-evidence-checks.md) defines each check
precisely enough for a second implementation to agree.

## Measuring across extractors

```bash
python bench/run.py --corpus bench/corpus
```

Evidence integrity is **reference-free** — you do not need to know the right
answer to know a quote is not in the document — so it can be computed on any
corpus by anyone, with no annotation step where a judgement call could quietly
favour one model. Scoring is free, offline and deterministic; extraction is a
separate program precisely so that someone who distrusts your numbers can
recompute them without a key.
