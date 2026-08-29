# What this can and cannot do

Written from the code, not from intention. Every "cannot" below was checked against
the source rather than recalled — three of them were nearly recorded backwards,
because the strings `retriev`, `sandbox` and `stream` all appear in this repository
inside sentences saying those things are absent.

The machine-readable version of the limitations is `pyligent-agents validation-pack`,
which emits them as JSON for a model-risk review.

---

## Shipped and exercised

| Area | What you get | Exports |
|---|---|---|
| `harness` | Context, permission tiers, budget caps in turns / dollars / seconds, prompt caching, per-model cost accounting, memory bound to its source by content hash with four freshness states | 29 |
| `loop` | Turn caps that bind, stop conditions, escalation and failure policy, pushback and recovery | 22 |
| `graph` | Declarative DAG inspectable *before* it runs, idempotency ledger, crash resume, compensation, Mermaid and text rendering, per-node run traces | 15 |
| `verify` | 23 gates: evidence present / verbatim / independently verified, silent-repair detection, cross-field rules, ISO-4217, numeric ranges, memory freshness, explicit abstention | 23 |
| `record` | The typed artifact and its lifecycle — `PROPOSED → CERTIFIED → ADMITTED \| REFERRED \| ABSTAINED` | 8 |
| `evals` | Four personas, asymmetric scoring that never averages a false accept against a false refer, committed baselines, a `--check` regression gate | 14 |
| `evidencecheck` | Standalone CLI and library. Reads `.txt`, `.html`, `.pdf`; understands seven extraction shapes; needs no model, no network, no ground truth | 6 |

**Command line.** `pyligent-agents` (`steps`, `doctor`, `validation-pack`, `new`,
`graph`, `runs`, `trace`) and `evidence-check`.

**Examples.** Seven, from a one-call triage agent to a four-layer invoice intake.

**Tested.** 444 tests offline in ~16 seconds with no credential, on Python 3.10–3.13
across Linux, macOS and Windows, plus one live model call in CI at a measured
$0.0096 per run.

**Benchmark.** 97 SEC exhibits verified to *be* Credit Support Annexes, with the
classifier that admitted them and its measured separation (0 false accepts on 24
hard negatives).

---

## Not there

Grouped by *why* it is absent, because that decides whether waiting is sensible.

### Deliberate — changing it would change the shape of the library

| | |
|---|---|
| **Tool sandboxing** | Tools run in your process with your privileges. A permission tier is a declaration of *intent*, not a containment boundary. Do not rely on it against hostile input. |
| **Retrieval / RAG** | Everything must fit in context. A real corpus does not, and retrieval quality would then dominate every other design decision here. |

### Hard — limits of the method, not of the implementation

| | |
|---|---|
| **Relevance checking** | The citation check catches *fabricated* evidence, not *irrelevant* evidence. A genuine sentence that does not support the claim passes. |
| **Accuracy** | Evidence integrity is not accuracy. Nothing here tells you an extracted value is *right* — only whether the document supports it. A quote can be real, contain the value, and be the wrong clause. |

### Scope — real, but not what this is

| | |
|---|---|
| **Calibrated domain gates** | The CSA, invoice and KYC gates are worked examples over synthetic documents. They are not calibrated to any institution's policy and must not be deployed as controls without that work. |
| **`estimate_tokens`** | A heuristic, used only to trigger context management. Billing uses the figures the API returns. |

### Unbuilt — work nobody has done yet

| | |
|---|---|
| **Providers beyond Anthropic** | The runtime ships `anthropic` and `scripted`. The *benchmark* speaks to three vendors; the agent runtime does not. |
| **Async API** | Wanted. The design needs discussion before the surface is committed to. |
| **Streaming responses** | The loop is turn-shaped, so this is a change rather than an addition. |
| **Rate limiting, multi-tenant isolation** | Single-tenant, single-process assumptions run through the state directory. |
| **OpenTelemetry export** | Runs and spans are recorded and inspectable via `runs` and `trace`, but nothing exports to a collector. |
| **Human-in-the-loop UI** | Approval is a programmatic interface. No review queue, no reviewer identity, no audit of who approved what. |
| **Live coverage beyond Anthropic** | One live call per CI run. The OpenAI and Gemini paths have no live test — and one of them was silently dead until a live call caught it. |

---

## The line that matters most

Evidence integrity is a claim about **support**, never about truth. If you need to
know an extraction is *correct*, you need a gold set and domain review. What you get
here is the much cheaper guarantee that nobody invented the citation — which is
worth having precisely because it is checkable by anyone, offline, without trusting
us.
