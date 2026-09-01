# Pyligent Agents

**Evidence-backed extraction of collateral terms from ISDA agreements — with the
audit trail that makes a recommendation defensible.**

[![ci](https://github.com/pyligent/pyligent-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/pyligent/pyligent-agents/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![license](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

A Credit Support Annex is a signed contract that a margin system holds as a row
in a table. When those two disagree — an amendment nobody re-keyed, a term read
into the wrong field — every margin call against that counterparty is sized
wrong, and nothing in the margin system looks broken.

This repository reads the agreement, derives the constraints, and shows you the
disagreements with the clause that settles each one. It writes nothing.

```bash
python examples/run.py shadow --drift
```

```
── MATERIAL · threshold ───────────────────────────────────
  agreement says : 0
  system says    : 5000000
  impact         : changes when a call is made and by how much
  clause         : ""Threshold" means with respect to each party: USD 0."
```

The parties moved to a VM CSA in 2017 and the Threshold went to zero. The margin
system still holds 5,000,000, and has been sizing every call against an
unsecured band that no longer exists. No reconciliation of that system against
*itself* would ever find this. Only reading the agreement finds it.

---

## Where this stops

Deliberately, at a **certified constraint pack** — the terms, machine-readable,
each traceable to signed language:

```
agreement ─▶ extraction ─▶ evidence check ─▶ gates ─▶ CDM ─▶ constraints ─▶ │ certified │
                                                                            └─────┬─────┘
                                                                    allocation, optimisation
                                                                    and settlement are
                                                                    downstream and not here
```

Certification is not "did it parse". It asks whether every constraint an
optimiser needs is present, whether each one traces to a clause that was
machine-checked against the source, and — the part usually skipped — whether
anything in the agreement could **not** be expressed:

```
  constraints derived : 14
  every one traceable : yes
  certified for use   : NO
    · 1 term(s) in the agreement are not expressible as constraints and must be
      read by a human before this counterparty is optimised.
```

A pack that silently drops a Valuation Agent's discretion or a ratings trigger
looks complete and is not. The optimiser then solves the wrong problem with
total confidence.

---

## Shadow mode, and why it is safe to say yes to

A trial starts by watching, not touching. Shadow mode is a first-class mode
whose guarantee is a test, not a promise:

```python
def test_shadow_mode_cannot_reach_a_tool_with_an_external_effect():
    outcome = stack.harness.run_tool(
        ToolUse(id="t1", name="issue_refund", input={...}),
        phase=Phase.ACT,
        approver=lambda ctx: True,       # a human saying yes...
    )
    assert outcome.denied                # ...and it is still denied
```

Note the approver. The guarantee does not depend on how the stack happened to be
configured — the tier is denied outright.

Two more questions every regulated review asks, answered by the tool rather than
a slide:

```bash
pyligent-agents doctor            # where does the document text actually go?
pyligent-agents validation-pack   # the evidence a model-risk review asks for
```

`validation-pack` inventories the enforced controls, the reproducibility basis,
the measurement baselines and — the section reviewers actually read — what this
system does not do. It reports; it certifies nothing, and says so.

---

## How it is built

Three layers, adoptable one at a time: a **harness** that owns context and
permission, a **loop** that owns when to stop, and a **graph** that owns what
survives a crash. The collateral work above is an application of them, and the
patterns generalise — [`docs/PATTERNS.md`](docs/PATTERNS.md) works them through
on an ordinary support desk, away from the domain.

**No required dependencies.** **No tools shipped.** **over 440 tests that run offline
in sixteen seconds with no API key** — plus one live-model test in CI, opt-in and
costed at $0.0096 a run, because a deterministic suite cannot notice a provider
retiring a model.

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

## The artifact is the unit of control

A chat response is not a governable object. It has no schema, no provenance, no
status, and no way to say *this value came from that sentence, extracted by that
prompt at that version*. You can read it and hope.

`Record` makes the artifact the unit instead:

```python
from pyligent_agents import Record, Status

record = Record.from_artifact(extraction, doc_type="csa")
record.status                       # Status.PROPOSED — nothing has checked it
record.fields["threshold"].provenance.gate_set          # "csa/v7"
record.fields["threshold"].evidence[0].locator.describe()   # "table t1, cell r3c2"

record.certified(gate_report).admitted()      # a NEW record; nothing mutates
record.referred(ReviewItem("ratings trigger", "not modelled", owner="legal"))
```

Three properties make it governable rather than merely structured.

**Status is a lifecycle, not a boolean** — `PROPOSED → CERTIFIED → ADMITTED |
REFERRED | ABSTAINED`. `ABSTAINED` is the one usually missing and the one that
keeps a control honest: the system could not tell, and said so, rather than
guessing in whichever direction its threshold happened to fall.

**Provenance is per field.** "Processed by v2.1" is useless at review time.
"This threshold came from prompt `csa/v3` under gate set `csa/v7`, quote on page
4" is what an auditor asks for, and the answer has to survive the document being
reprocessed later.

**Transitions return new records.** Nothing mutates status in place, so the
chain from source to decision replays rather than being reconstructed.

`to_artifact()` emits the exact dict shape the gate library already reads, so
locators and provenance are **additive, not a migration**. Gates written before
this type existed keep working untouched.

---

## Memory that knows when it has gone out of date

Memory outlives every control around it, which is why it is the most dangerous
thing in an agent. A note written from an agreement that has since been amended
is not merely unhelpful — it is confidently wrong, and it **suppresses the
lookup that would have corrected it**. The absence of a fact prompts a search; a
wrong fact prevents one.

```
run 1   reads a CSA, notes "ATLAS Threshold is USD 5,000,000"
...     the parties adhere to the VM protocol; Threshold becomes zero
run 9   recalls the note and sizes a call against a band that no longer exists
```

That is the same drift the shadow-mode reconciliation exists to find, happening
*inside the agent* where nothing looks at it. So a note records what it was
derived from, by content hash, and recall checks that hash against the source as
it is now:

```python
memory.write("atlas-threshold", "Threshold is USD 5,000,000.",
             why="Avoids re-reading Paragraph 11 on every call.",
             derived_from=[Binding.of("DOC-CSA-ATLAS", csa_text)])

memory.recall("atlas threshold", sources={"DOC-CSA-ATLAS": current_sha})
# -> withheld once the agreement changes, and reported as STALE
```

Four states, and the third is the one usually missing:

| | |
|---|---|
| `FRESH` | bound to a source, and the source still hashes the same |
| `STALE` | bound, and the source has changed since |
| `UNVERIFIED` | bound, but no current hash was supplied — **we cannot tell** |
| `UNBOUND` | no provenance: general knowledge, or written before this existed |

`UNVERIFIED` abstains rather than guessing, for the same reason a gate does. A
control that answers when it cannot tell answers wrongly in whichever direction
its default happens to fall.

Three consequences worth knowing:

- **Injection is budgeted.** Memory that grows without a cap is a context leak
  with a good reputation. `inject()` obeys a character budget and **counts what
  it withheld rather than hiding it** — a prompt that silently drops half of
  what it recalled is worse than one that says so.
- **Recall is lexical, not embedded.** An embedding would recall more and
  justify less, and a memory whose retrieval you cannot explain is a memory you
  cannot audit.
- **`memory_is_current()` is a gate.** An artifact that leaned on a note whose
  source has changed is not admissible, and `harness.report()["memory_used"]`
  puts the notes a decision relied on into the audit trail.

---

## Configuration and secrets

Structure lives in a file. Credentials do not.

```yaml
# pyligent.yaml — committed
extractor:
  provider: anthropic
  model: claude-sonnet-5
  api_key_env: ANTHROPIC_API_KEY     # the NAME, never the value
```

```python
from pyligent_agents.config_file import load

cfg = load("pyligent.yaml")
cfg.get("extractor.model")           # "claude-sonnet-5"
cfg.secret("extractor.api_key_env")  # read from the environment, at use
```

`load()` **refuses** a file that appears to carry a credential rather than a
reference to one, and says why: deleting a secret from a repository does not
remove it from history. A check that only runs when someone remembers to run it
is not a control.

No YAML dependency — the subset parsed is what a configuration file needs, and
adding a parser to read six keys is a bad trade.

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

And the counterpart, learned the expensive way:

> **A gate that cannot tell must pass, not fail.** When a check's precondition
> does not hold, abstain. One that fires on "I cannot tell" turns every
> unusual-but-valid document into a referral, and a queue full of correct
> documents is how a control gets switched off.
> ([ADR 0006](docs/adr/0006-gates-cite-published-guidance.md))

The evidence checks also ship as a **standalone command**, so you can point them
at any pipeline's output without adopting anything:

```bash
evidence-check contract.html extraction.json
```

No model, no network, no configuration. It works on Claude, GPT, Gemini, a
vendor IDP or a regex — seven extraction shapes are understood, because a tool
that requires reformatting measures whoever bothered to reformat.

It is **necessary, not sufficient**, and says so: roughly half of what a
"helpful" extractor gets wrong is catchable with no domain knowledge at all —
that is this command. The other half needs rules about your documents, which is
what the gates above are. And a genuine quote can still be the wrong clause;
every claim here is about *support*, never about truth.
[`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) states what this can and cannot
do, checked against the source rather than recalled;
[`docs/SPEC-evidence-checks.md`](docs/SPEC-evidence-checks.md) defines each check
precisely enough for a second implementation to agree; `bench/` scores evidence
integrity across extractors, free and offline, over **97 SEC exhibits verified to
*be* Credit Support Annexes** rather than to mention one — a distinction that
decides whether the corpus has anything to extract at all
([`bench/README.md`](bench/README.md)).

`no_silent_repair()` catches what the other evidence gates cannot see: a
citation that is genuine and names a *different* value. `evidence_present`
passes, `evidence_verbatim` passes, and the discrepancy the extraction was hired
to surface is the thing it removed. Available now, and deliberately not yet in
the default bundle — adding it changes gate counts and every published figure.

Every evidence check lives in `src/evidencecheck/` and is imported, never copied,
so there is one definition and it cannot drift. It already had: this module knew
five placeholder markers, the checker knew ten, so `-` and `none` passed one
path and failed the other.

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

## Scoring the output: `evals/`

Tests prove the loop stops, the gate fires, the refund happens once. They say
nothing about whether the extraction is any good. That is a different question
and it needs a gold set.

```bash
python evals/run_evals.py
```

Fifteen labelled cases over the three document types, scored four ways:

```
  SYSTEM         FALSE ACC FALSE REF  FIELD ACC  EVIDENCE   REASON
  ----------------------------------------------------------------
  faithful            0          0       100.0%    100.0%   100.0%
  paraphraser         0          7       100.0%      0.0%   100.0%
  helpful             4          1        95.7%    100.0%   100.0%
  sloppy              0          7        81.5%    100.0%    87.5%
```

Rank by field accuracy and `helpful` comes **second**, ahead of `sloppy`. It is
also the only system in the table that approved a flawed document — four times.
It is not a bad extractor; it is a good one with an instinct to be useful. When
the passport reads `Jonathon` and the application says `Jonathan`, it writes down
whichever makes the file consistent. Every correction is individually
defensible, and the aggregate is a system that cannot find what it was hired to
find. Looking that way costs it 4% of field accuracy.

`paraphraser` is the other trap: every value right, not one quote real.
*Requiring* citations misses this. *Checking* them catches it.

So the report never leads with a single accuracy figure. It counts the two
decision errors separately and treats them asymmetrically in code — a false
refer costs an analyst ten minutes, a false accept is what the counterparty
calls about. `false_accept_rate` has a regression tolerance of **zero** and CI
fails on any increase.

📄 [`evals/README.md`](evals/README.md)

---

## The examples

Four applications on one ordinary domain — a support desk for an online retailer
— one per rung of the ladder.

| | What it is | Why the level below broke |
|---|---|---|
| **0** | [document intake](examples/document_intake/) — CSA, invoice, KYC | *start here* — one graph, three domains, one gate that catches each flaw |
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
| Measuring output quality | [`evals/README.md`](evals/README.md) |
| Curious about a decision | [`docs/adr/`](docs/adr/) — start with [0006](docs/adr/0006-gates-cite-published-guidance.md) |
| Ready to build | `pyligent-agents new my_agent && cd my_agent && pytest` |

---

## Design decisions worth knowing about

- **[Three layers](docs/adr/0001-three-layers.md)**, not one module per ladder
  level. Level 4 workers were re-implementing Level 2; now they *are* Level 2.
- **[No tools, no domain](docs/adr/0002-ships-no-tools.md)**. A
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
  you a prompt got worse — that is what [`evals/`](evals/README.md) is for, and
  the two answer different questions. Neither is a substitute for measuring your
  own documents against your own gold set.
- **A domain gate is only as good as the domain knowledge behind it.** The CSA
  gate set once asserted `MTA <= Threshold` — fluent, specific, and wrong for
  every standard VM CSA, where the Threshold is zero. The demo passed and the
  tests passed; reading the ISDA source caught it. Gates that implement
  published guidance now cite it and are held to it by a conformance test. See
  [ADR 0006](docs/adr/0006-gates-cite-published-guidance.md).
- **The shipped domain gates are worked examples, not a compliance product.**
  They implement two public documents against synthetic files. Your policy,
  your regulator and your counterparties are yours to encode.
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

Apache-2.0 licensed. The patent grant is the reason: it is what legal and open-source review functions in regulated industries ask for, and it costs
nothing to give.
