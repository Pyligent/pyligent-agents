# Layer 1 — Harness engineering

> The harness owns **context**: what the model sees, what it may touch, and what
> it costs.

A bare tool loop gives you two places to put policy — inside the tool, or inside
the prompt. Both are wrong. Tool code should not know about approval workflows,
and a prompt is not an enforcement mechanism. The harness is the third place,
and it is the right one.

```
                    ┌──────────────────────────────────────────────┐
   agent ──────────▶│  Harness                                     │
                    │                                              │
                    │  call_model:  governor.check                 │
                    │               → PRE_MODEL hooks              │
                    │               → client.complete              │
                    │               → POST_MODEL hooks             │
                    │               → meter + trace                │
                    │                                              │
                    │  run_tool:    PRE_TOOL hooks (gate/rewrite)  │
                    │               → registry.execute (contained) │
                    │               → POST_TOOL hooks (redact)     │
                    │               → offload if large             │
                    │               → trace                        │
                    └──────────────────────────────────────────────┘
                        ContextManager · ToolRegistry · Governor
                        HookBus · Workspace · MemoryStore
```

Two methods. Everything above the harness goes through one of them, which is
why there is exactly one place to enforce anything.

```bash
python examples/run.py demo harness
```

---

## 1. Context: offload, then compact

A long-running agent loses in three ways: it runs out of window, it fills the
window with noise, or it pays full price on every turn for something it read
once.

### Offloading (spatial, lossless) — do this first

A tool result over `offload_over_chars` is written to the run workspace and
replaced inline with a preview plus a handle:

```
[Result was 41,208 characters and has been stored as artifact art_9f2c...
 First 600 characters follow; call read_artifact(handle="art_9f2c...",
 offset=N) for the rest.]
```

The model reads the rest on demand via the built-in `read_artifact` tool. That
is progressive disclosure: the information is not lost, it is *not resident*.

Artifacts are content-addressed, so the same result stored twice — which happens
constantly on retries — reuses one file and one handle.

### Compaction (temporal, lossy) — only when you must

Past `compact_at` × window, older turns are folded into one summary and the
recent tail is kept verbatim. Two invariants make it safe mid-loop:

1. **The first user turn survives.** It carries the goal, and an agent that
   forgets its goal at turn 30 is worse than one that runs out of window.
2. **A tool_use is never separated from its tool_result.** `_safe_cut` moves the
   cut later until no tool call is orphaned. An orphan is a 400 on the very next
   request, and it is the bug that makes people abandon compaction.

The demo shows both, and the relationship between them:

| Run | offloading | compactions |
|---|---|---|
| A  | on  | **0** — the transcript never grew enough to need folding |
| A2 | off | **2** — folded twice, ~1,000 tokens saved each time |

Offload first. Compact only when offloading is not enough.

> **A trap worth knowing.** Compaction *removes messages*. Any loop whose exit
> condition reads the transcript ("have I called this tool six times?") breaks
> the day you turn compaction on. Count turns, not messages. The fixture in this
> repo hit exactly this and now counts `call_index`.

### Measuring

`estimate_tokens` is a ~4-chars-per-token heuristic used for *triggering*
decisions only. Real usage comes back from the API and is what the governor
bills against. **Never use the estimate for money.**

---

## 2. Hooks: four interception points

| Point | Job |
|---|---|
| `PRE_MODEL` | inject just-in-time context; last chance to change the request |
| `POST_MODEL` | observe what came back; enforce output shape |
| `PRE_TOOL` | gate, rewrite arguments, or deny — before anything runs |
| `POST_TOOL` | redact, truncate, defang untrusted content |

A denial short-circuits: later hooks cannot upgrade it.

### Shipped by default

```python
HookBus()
  .on(PRE_TOOL,  phase_guard)                  # verify phase is read-only
  .on(PRE_TOOL,  deny_restricted_without_approval)
  .on(POST_TOOL, defang_untrusted_content)
  .on(POST_TOOL, redact_secrets)
```

**`phase_guard`** — a verify-phase agent cannot hold a non-read-only tool.
Verification that can mutate what it verifies is not verification.

**`defang_untrusted_content`** — neutralises instruction-shaped text in output
marked `trusted=False`. A supplier's invoice, a scraped page, a
user-submitted ticket: all written by someone else. Text inside them that
reads like an instruction is *data*.

> This is the **second** line of defence. The first is that document-reading
> agents hold no restricted tools, so an injected instruction has nothing worth
> reaching. Filters can be evaded; capability boundaries cannot. Build the
> boundary; add the filter.

**`redact_secrets`** — anything entering the context is persisted, replayed on
every later turn and folded into compaction summaries. A key that lands there
once is in the run forever.

### Injecting context correctly

```python
def add_memory(ctx: ModelCallContext) -> None:
    ctx.add_context(harness.recall("threshold", sources=current))  # ✅ after the prefix
    ctx.system += "..."                                            # ❌ invalidates the cache
```

`add_context` appends a user turn after the cached prefix. Editing `system`
changes the front of the prefix and re-bills the entire conversation.

### 2.4 Memory, and why it is the dangerous input

Memory outlives every control around it. A note written from a document that
has since been amended is not merely unhelpful — it is confidently wrong, and
it **suppresses the lookup that would have corrected it**. A missing fact
prompts a search; a wrong one prevents it.

So a note records what it was derived from, by content hash, and recall checks
that hash against the source as it is now:

```python
memory.write("atlas-threshold", "Threshold is USD 5,000,000.",
             why="Avoids re-reading Paragraph 11 on every call.",
             derived_from=[Binding.of("DOC-CSA-ATLAS", csa_text)])

harness.recall("atlas threshold", sources={"DOC-CSA-ATLAS": current_sha})
# withheld once the agreement changes, and reported STALE
```

| | |
|---|---|
| `FRESH` | bound, and the source still hashes the same |
| `STALE` | bound, and the source has changed since |
| `UNVERIFIED` | bound, but no current hash supplied — **cannot tell** |
| `UNBOUND` | no provenance: general knowledge, or written before this existed |

`UNVERIFIED` abstains rather than guessing, on the same principle as a gate
([ADR 0006](adr/0006-gates-cite-published-guidance.md)).

Three properties worth knowing:

- **Injection is budgeted.** Memory was the one prompt input that grew without
  anyone deciding to grow it. `inject()` obeys a character cap and **counts what
  it withheld rather than hiding it**.
- **Recall goes through the harness**, so one place counts what was injected and
  one records what was used — `report()["memory_used"]` puts it in the audit
  trail.
- **Retrieval is lexical, not embedded.** An embedding recalls more and
  justifies less, and a memory whose retrieval you cannot explain is a memory
  you cannot audit.

---

## 3. Permissions

Three tiers, on the tool:

| Tier | Meaning |
|---|---|
| `READ_ONLY` | cannot change anything; parallel-safe |
| `REVERSIBLE` | writes something you can undo |
| `RESTRICTED` | leaves the building; needs an explicit decision |

`RESTRICTED` tools go through an approver. **The default denies** — absence of
an approver is never an allow. A denial comes back as an observation classified
`PERMISSION`, so the agent can respond with *"here is the instruction I would
send; it needs sign-off"* rather than crashing or retrying.

> Classifying a denial as `FATAL` was a real bug in this codebase, found by a
> demo. The desk got an exception instead of a drafted instruction. Error
> classification is not bookkeeping — it decides behaviour.

**Narrow the surface, do not rely on restraint.** `registry.clone(*names)`
returns a registry in which the other tools do not exist. Absent beats
unapproved.

---

## 4. Deferred tools

A registry of 200 tools would put 200 schemas in the prefix of every request.
Tools marked `defer_loading=True` are declared but not advertised until
`search_tools` surfaces them:

```
registered : 10 tools
advertised : 8
deferred   : ['compare_orders', 'list_orders']
```

Surfacing **appends** to the advertised set; it never swaps the tool list.
Swapping would invalidate the cached prefix for the rest of the run.

---

## 5. Governors

Four independent limits, because runs fail in four different ways:

| Limit | Catches |
|---|---|
| `max_turns` | a loop that will not converge |
| `max_tokens` | a loop that converges but drags the window along |
| `max_usd` | the one Finance asks about |
| `max_seconds` | a loop blocked on something that will never answer |

Checked **before** the call, not after. An unpriced model id is charged at the
dearest tier we know about, so a new model can never silently look free.

Subagents share the parent's governor: **one budget for the run**. Ten subagents
with their own caps is not a cap.

`headroom()` returns each limit as a fraction — useful for telling an agent to
start wrapping up rather than being cut off mid-sentence.

---

## 6. Working with the API

Five things in `harness/client.py` that are easy to get wrong:

1. **Check `stop_reason` before reading `content`.** A safety refusal returns
   HTTP 200 with an empty content list; `content[0].text` raises an IndexError
   that then gets logged as a network fault and debugged for an afternoon.
2. **Never send `temperature` / `top_p` / `top_k`.** Rejected on current Opus
   and Sonnet models. Steer with the prompt.
3. **Adaptive thinking; depth via `output_config.effort`.** `budget_tokens` is
   removed on current models.
4. **Cache the stable prefix.** One `cache_control` breakpoint on the last
   system block covers tools + system (render order is tools → system →
   messages). Keep timestamps and request ids *out* of the system prompt — one
   byte of drift invalidates everything after it, on every call.
5. **Stream large outputs.** Above ~16K `max_tokens` a non-streaming request
   risks an HTTP timeout.

---

## What to copy into your own system

- Two methods, one path each, for model calls and tool calls.
- Tool result offloading before compaction.
- Tier on the tool; deny by default; a denial is `PERMISSION`, not fatal.
- Narrow registries for subagents.
- One governor per run, shared by children.
- A ledger entry per model call and per tool call, with arguments.
