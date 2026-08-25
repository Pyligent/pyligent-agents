# The ladder — how much agent does this task need?

The three layers tell you **how** to build. The ladder tells you **how much**.

Answer in order. Stop at the first level that fits. The burden of proof is always
on moving *up*.

```
Is the task bounded to a single turn, needing no information the model
does not already have in the request?
│
├─ YES ──▶ LEVEL 1.  One call. No memory, no tools, no loop.
│          This is the correct final architecture for most production LLM
│          tasks. Resist the urge to add a framework.
│
└─ NO
   │
   Does it need live data, a calculation, or an external action?
   │
   ├─ YES ──▶ LEVEL 2.  A tool loop with a contract: hard turn cap, tool
   │          errors as observations, tiered permissions.
   │
   └─ (also) Does it span sessions, or must it survive interruption?
      │
      ├─ YES ──▶ LEVEL 3.  A graph: checkpoints per node, idempotency keys on
      │          anything with external consequences.
      │
      └─ (also) Is it genuinely several different jobs, each needing a
                different kind of attention?
         │
         └─ YES ──▶ LEVEL 4.  Fan out to specialists, fan in, gate the result,
                    and verify it independently.
```

---

## Worked examples

| Task | Level | Why |
|---|---|---|
| Classify an inbound support ticket | **1** | Single turn, bounded, no external data |
| Summarise a thread you already have | **1** | Summarisation is not an agent problem |
| Rewrite a policy notice in plainer English | **1** | Rewriting, not deciding |
| "Why is order A-1207 late, and what can we offer?" | **2** | Needs order data, live tracking, and a refund figure the model must not compute |
| "Is this return within policy?" | **2** | Needs the order and the policy document |
| Take a refund from ticket to money-moved | **3** | Spans sessions, needs approval, moves money |
| Reconcile 400 orders overnight | **3** | Long, interruptible, side-effecting |
| Intake a supplier invoice into accounts payable | **4** | Read + transcribe + reconcile + verify |
| Produce a quarterly vendor review pack | **4** | Research, analysis, drafting, fact-checking |

### The two misdiagnoses

**Volume mistaken for variety.** "Reconcile 400 orders overnight" looks like a
fan-out. It is the *same job 400 times* — a batch loop around a Level 2 agent,
made durable. Level 4 would add coordination over 400 identical specialists:
cost and failure surface for nothing.

**"It's just search" mistaken for "no external data."** Finding similar past
tickets feels like Level 1 because it is one question. The corpus is not in the
prompt, so it needs a tool — and then retrieval quality dominates every other
design decision.

---

## Before you move up

**1 → 2.** *What specific information does the model not have, and which tool
supplies it?* "It would be nice if it could look things up" is a wish, not a
breakage. Bring the failing example.

**2 → 3.** *Which run got interrupted, and what did restarting it cost?* In
tokens, in duplicate side effects, or in a human redoing work. A task that
finishes in eight seconds does not need durable state because it *could*
theoretically be interrupted.

**3 → 4.** *Which two parts of this task actively interfere in one context
window?* Name them. "It's complicated" is not a decomposition. If you cannot say
which specialist takes which half, you have an under-specified Level 3 problem.

---

## What each level costs

```bash
python examples/run.py demo ladder
```

| Level | Task | Calls | USD | Ratio |
|---|---|---|---|---|
| 1 stateless | classify one ticket | 1 | 0.00070 | 1× |
| 2 tool loop | answer a support question | 3 | 0.01500 | 21× |
| 3 durable graph | refund, to the approval gate | 1 | 0.00495 | 7× |
| 4 fan-out graph | intake a supplier invoice | 3 | 0.02810 | 40× |

**Level 3 costs less than Level 2.** That is the number worth internalising:
durability is cheap, **breadth** is expensive. The refund graph pushes work into
deterministic nodes and calls a model exactly once.

**Volume beats unit cost.** At 400 tickets a day against 3 invoices, Level 1 is
the largest line on the monthly bill despite being cheapest per run. The first
place to look for savings is the cheap thing you do constantly.

Which is also the argument to have ready when someone says *"let's make triage an
agent so it can look things up."* That is not a 2× change. A tool loop is 3+
calls on a bigger model — roughly 20× — applied to your highest-volume workload.
**Ask for the failing example first.**

---

## Levels compose; they do not replace

Level 4 does not replace Level 2 — it *contains* it. In `examples/`, the
extraction specialists inside the invoice graph are ordinary `Agent`s: same loop,
same contract type, same turn cap, running inside a graph node.

**You do not rewrite when you move up.** Teams that read the ladder as "replace
the previous architecture" end up doing the rewrite it exists to avoid.

---

## The four questions

Before **any** unattended run, at any level:

1. **What is the stop condition?**
2. **Who verifies before it ships?**
3. **What is the spend cap?**
4. **What happens when a subagent fails?**

Answered for the four examples:

| | Level 1 | Level 2 | Level 3 | Level 4 |
|---|---|---|---|---|
| **Stop** | one call, always | model done AND amounts grounded | all nodes terminal | all nine gates pass |
| **Verifier** | closed vocabulary, validated | figures from tested code | figures recomputed deterministically | independent verifier + checked citations |
| **Cap** | one cheap call | four governors that raise | per-run; replays are free | one budget across every node |
| **On failure** | degrade to manual triage | error → observation | checkpoint + idempotent resume | gate fail → escalate |

Cannot answer all four? You have an expensive experiment with no seatbelt.

---

## The one rule

**Match the architecture to the actual shape of the task, not to the most
sophisticated pattern you know how to build.**

A multi-agent system is not more model. It is more structure — and structure only
helps when the task actually needs it.
