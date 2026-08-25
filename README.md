# Pyligent Agents

**Harness, loop and graph engineering for production AI agents.**

[![ci](https://github.com/pyligent/pyligent-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/pyligent/pyligent-agents/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

The argument, in one line:

> A multi-agent system is not more model. It is more **structure** — and
> structure only helps when the task actually needs it.

This is that structure: three layers you can adopt one at a time, a domain you
supply yourself, and guardrails that fail a test when you remove them.

```bash
pip install pyligent-agents

pyligent-agents steps           # the ten build steps
pyligent-agents new my_agent    # a project whose guardrail tests already pass
```

```python
import pyligent_agents
```

**No required dependencies.** **No tools shipped.** **115 tests that run offline
in five seconds with no API key.**

---

## The mental model

```
   harness   owns CONTEXT        what the model sees, and what it may touch
   loop      owns CONTROL        when to stop, what to do when something breaks
   graph     owns COORDINATION   what runs in what order, and what survives a crash
```

Three sentences. If you remember nothing else, remember which layer owns which
question — most agent code is hard to read because those three concerns are
tangled in one file.

| Layer | Read | Run |
|---|---|---|
| **1 · Harness** | [`docs/HARNESS.md`](docs/HARNESS.md) | `python examples/run.py demo harness` |
| **2 · Loop** | [`docs/LOOP.md`](docs/LOOP.md) | `python examples/run.py demo loop` |
| **3 · Graph** | [`docs/GRAPH.md`](docs/GRAPH.md) | `python examples/run.py demo graph` |
| **The ladder** | [`docs/LADDER.md`](docs/LADDER.md) | `python examples/run.py demo ladder` |

---

## Thirty seconds

```python
from pyligent_agents import build_stack
from pyligent_agents.loop import (
    Agent, AgentContract, Budget, ModelSaysDone, Predicate, no_verification,
)

stack = build_stack(registry=my_tools)

agent = Agent(
    stack.harness,
    AgentContract(
        goal="Answer a customer's question about an order.",
        # "Done" is a predicate, not the model's opinion.
        stop=ModelSaysDone() & Predicate(grounded, "amounts traceable to tools"),
        # You must say who verifies — or write down why nobody does.
        verifier=no_verification("Every amount comes from tested deterministic code."),
        # Four governors. Whichever binds first stops the run, loudly.
        budget=Budget(max_turns=8, max_usd=0.40, max_seconds=90),
    ),
    system=SYSTEM_PROMPT,
)

print(agent.run("Why is order A-1207 late?").answer)
print(stack.cost())
```

You cannot construct that `AgentContract` without answering the four questions.
`Budget(max_turns=0)` raises. `no_verification("later")` raises — the reason has
to be a real sentence.

---

## Three demos that change the conversation

### 1. Offloading versus compaction

```
Run A  (offloading on)     results offloaded: 7    compactions: 0
Run A2 (offloading off)    results offloaded: 0    compactions: 3
                             turn 4: folded 2 messages, 2,159 → 1,553 tokens
```

Same agent, same work. Offload first — lossless, spatial: a big tool result goes
to a content-addressed workspace and the model gets a preview plus a handle.
Compact only when that is not enough — lossy, temporal. And compaction never
orphans a `tool_use`; that particular 400 is why people abandon compaction.

### 2. A confident, invented number — caught

```
turn 1  stop_check  amounts traceable to tools: not met
turn 1  push_back   amounts traceable to tools: not met
turn 3  stop_check  model_says_done and predicate(amounts traceable to tools)

final: Correction: the refundable amount for A-1207 is £257.99, from quote_refund.
```

Turn 1 said *"We can refund you £310.00."* Fluent, confident, entirely invented,
and **nothing in the text signals it.** Under "the model stopped calling tools"
that answer goes to a customer. An eight-line grounding predicate rejected it,
said exactly why, and the agent fetched the real figure.

### 3. Three executions, one refund

```
workflow executions      : 3
refunds actually issued  : 1
emails actually sent     : 1
  issue_refund   refund:amount=257.99|fault=seller|order=A-1207
  send_reply     reply:amount=257.99|order=A-1207
```

The demo asserts it. Checkpointing narrows the duplicate window; only a ledger
with a database uniqueness constraint closes it — and only if the key is derived
from the **facts** of the action rather than a clock.

```bash
python examples/run.py demo graph
```

---

## What each layer gives you

### Layer 1 — Harness

Everything around the model, in one place, so there is exactly one code path to
enforce a policy or meter a cost.

- **Context**: tool-result offloading to a content-addressed workspace with
  `read_artifact` paging; compaction that preserves the goal turn and never
  splits a `tool_use` from its `tool_result`
- **Hooks**: four interception points — `PRE_MODEL`, `POST_MODEL`, `PRE_TOOL`,
  `POST_TOOL` — with untrusted-content defanging, secret redaction, a read-only
  verify phase, and deny-by-default on restricted tools shipped as defaults
- **Tools**: permission tiers on the tool itself; deferred loading via
  `search_tools` (appends, never swaps, so the cached prefix survives); dispatch
  that **never raises**
- **Governors**: turns, tokens, USD and wall-clock, checked **before** the call.
  An unknown model is priced at the dearest tier we know — it can never look free
- **Memory**: cross-run notes, one fact per file

### Layer 2 — Loop

```
gather context  →  take action  →  verify the work  →  repeat
```

The third phase is the one most implementations omit. Without it, "done" means
the model stopped calling tools — an opinion. With it, that is a **candidate**:
the loop extracts an artifact, verifies, checks the stop condition, and if it
fails hands back the *specific* gap.

- **`AgentContract`** — the four questions as constructor arguments
- **Composable stop conditions** — `ModelSaysDone`, `GatesPass`, `Produced`,
  `Predicate`, combined with `&` and `|`, and self-describing in traces
- **`RecoveryPolicy`** — one branch per error class, with a per-tool retry cap
  and a consecutive-failure cap so recovery cannot become its own runaway

| Error class | Action | Why |
|---|---|---|
| `TRANSIENT` | retry with backoff | the world was briefly unavailable |
| `INVALID` | hand it back | the agent's own arguments were wrong |
| `DOMAIN` | hand it back | a legitimate business refusal |
| `PERMISSION` | hand it back | present for sign-off |
| `FATAL` | escalate | a human owns this |

Unknown exceptions classify `FATAL`, not `TRANSIENT`. **An unrecognised failure
must never be retried into a bill.**

### Layer 3 — Graph

Six node kinds, closed on purpose: `Step`, `AgentNode`, `GateNode`, `HumanGate`,
`MapNode`, `ReduceNode`.

The argument for a declared graph over an orchestrator agent is not elegance. It
is that a graph is **inspectable before it executes**:

```
$ pyligent-agents graph show level3_refund_workflow.app:build_graph

graph: refund_workflow
  seeds: ticket_id
  ── layer 4 ────────────────────────────────
     approve_refund   human   after quote,draft_reply  (conditional)
  ── layer 5 ────────────────────────────────
     issue_refund     step    after approve_refund  (idempotent, conditional)
```

Build-time validation catches unknown dependencies, cycles, and reads of keys
nothing upstream provides:

```
Node 'assemble' requires ['artifact'] which nothing upstream provides.
Available at that point: ['document', 'source_text'].
Add it to a predecessor's `provides`, or to the graph's `seeds`.
```

Five execution rules: replay from checkpoint · replay from the effect ledger ·
checkpoint before the work · a failed node blocks its dependents · a human gate
**pauses** (paused is not failed).

---

## Verification: the part most teams skip

Width — more tools, more subagents, more autonomy — is what an architecture
diagram shows. **Trust** is what decides whether it can run unattended.

**Self-grading is a bias, not a safeguard.** An agent that extracts a document
and then reviews its own extraction reads the same line the same way twice. So
the verifier is a separate agent that sees the artifact and the source and
**nothing about how the artifact was produced**.

That is necessary and not sufficient — a verifier can invent a quote, and it is
*more* likely to on a structured document because the register is so predictable.
So every citation is substring-checked against the source, and one fabricated
quote rejects the artifact regardless of the verdict:

```bash
$ python examples/run.py invoice --fabricate

✓ gates                  done
— post_to_ledger         skipped
✓ escalate               done

  [FAIL] independently_verified: 1 citation(s) quote text absent from the source
```

And the gate worth studying most:

```bash
$ python examples/run.py invoice --transposed

  [FAIL] lines_sum_to_total: line items do not reconcile to the stated totals —
         a transposed digit or a missed line. Do not post.
```

One unit price mis-read, 82.50 → 85.20. Every field present, every type correct,
the evidence quote **real**, and the verifier **approved it**. One line of
arithmetic caught it.

> **Every gate set should contain at least one check a JSON schema could not
> express.** If yours does not, you have written a validator, not a gate set.

---

## Testing agents

The most useful thing here is that agent *behaviour* is testable. `ScriptedLLM`
is not a mock — it is a second implementation of the `LLMClient` contract,
running through the same `build_stack` path production uses.

```python
from pyligent_agents.testing import assert_capped, build_test_stack, calls, looping, turn

def test_a_looping_model_is_stopped(registry):
    stack = build_test_stack(looping("get_order", order_id="A-1"), tools=registry)
    assert_capped(lambda: build(stack.harness).run("go"))

def test_the_customer_is_refunded_once(tmp_path, registry):
    ...
    assert_effects_fire_once(stack, run_id, expected=2)   # refund + email
```

You cannot test a turn cap against a live model — it will behave differently on
the retry and hide the bug.

📄 [`docs/TESTING.md`](docs/TESTING.md)

---

## The examples

Four applications on one ordinary domain — a support desk for an online retailer
— one per rung of the ladder.

| | What it is | Why the level below broke |
|---|---|---|
| **1** | classify a support ticket | — this is the right final architecture |
| **2** | "why is order A-1207 late?" | needs live data and a figure the model must not compute |
| **3** | a refund, ticket to money-moved | spans sessions, needs approval, moves money |
| **4** | supplier invoice intake | four different jobs in one task |

The seed contains the awkward cases on purpose: an order outside the return
window (the domain **refuses**), one whose carrier API is **down** (the tool
*raises*), and one already refunded in full (a naive refund pays twice).

📄 [`examples/README.md`](examples/README.md)

---

## What it costs

Measured, not asserted.

| Level | Task | Calls | USD | Ratio |
|---|---|---|---|---|
| 1 | classify one ticket | 1 | 0.00070 | 1× |
| 2 | answer a support question | 3 | 0.01500 | 21× |
| 3 | refund, to the approval gate | 1 | 0.00495 | 7× |
| 4 | intake a supplier invoice | 3 | 0.02810 | 40× |

**Level 3 costs less than Level 2.** Durability is cheap; **breadth** is
expensive. The refund graph pushes work into deterministic nodes and calls a
model exactly once.

And volume beats unit cost: at 400 tickets a day against 3 invoices, Level 1 is
the largest line on the bill despite being cheapest per run.

---

## Where to start

| You are | Start here |
|---|---|
| Deciding whether to adopt this | this README, then `python examples/run.py demo graph` |
| About to build an agent | [`docs/STEPS.md`](docs/STEPS.md) — ten steps, each with a command |
| Choosing an architecture | [`docs/LADDER.md`](docs/LADDER.md) |
| Reviewing someone's agent PR | [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) |
| Writing tests | [`docs/TESTING.md`](docs/TESTING.md) |
| Curious about a decision | [`docs/adr/`](docs/adr/) |
| Ready to build | `pyligent-agents new my_agent && cd my_agent && pytest` |

---

## Design decisions worth knowing about

- **[Three layers](docs/adr/0001-three-layers.md)**, not one module per ladder
  level. Level 4 workers were re-implementing Level 2; now they *are* Level 2.
- **[No tools, no domain, no dependencies](docs/adr/0002-ships-no-tools.md)**. A
  default tool set makes three promises the library cannot keep.
- **[An effect ledger separate from checkpoints](docs/adr/0003-idempotency-ledger.md)**.
  Checkpointing narrows the duplicate window; only the ledger closes it.
- **[A declared graph, not an orchestrator agent](docs/adr/0004-graph-over-orchestrator.md)**.
  Enforcing "the orchestrator must not execute" is working around the fact that
  it could.
- **[Two backends behind one interface](docs/adr/0005-two-backends.md)**.
  `ScriptedLLM` is why turn caps and idempotency are testable at all.

**No framework.** The loop is forty lines and you should be able to read all of
them. The interesting engineering is the structure around it.

---

## Adding a backend

`LLMClient` is one method. An OpenAI, Bedrock, Vertex or local backend is a small
file, and nothing above it changes:

```python
class MyLLM(LLMClient):
    def complete(self, *, model, system, messages, tools=None,
                 max_tokens=4096, effort=None) -> LLMResponse: ...

register_model("my-model-1", price_in=0.5, price_out=1.5, context_window=128_000)
```

Contributions very welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## Honest limits

Stated here rather than discovered later:

- **The citation check catches *fabricated* evidence, not *irrelevant* evidence.**
  A genuine sentence that does not support the claim passes.
- **`ScriptedLLM` proves control flow, not output quality.** It will never tell
  you a prompt got worse. A gold-set eval harness is separate work and is
  deliberately not in this library.
- **Pyligent Agents does not sandbox your tools.** They run in your process with your
  privileges. See [`SECURITY.md`](SECURITY.md).
- **`estimate_tokens` is a heuristic** used only for triggering decisions. Real
  usage comes from the API and is what the governor bills against.
- **Async is not supported yet.** Wanted; the design needs discussion first.

---

## Contributing

New backends, new gates, new stop conditions, and examples in other domains are
all very welcome. The house rule:

> **Every guardrail has a test that fails when the guardrail is removed.** A rule
> that is not a failing test is a rule that will be broken within two quarters.

[`CONTRIBUTING.md`](CONTRIBUTING.md) · [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
· [`SECURITY.md`](SECURITY.md)

MIT licensed.
