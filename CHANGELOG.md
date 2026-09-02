# Changelog

All notable changes to this project are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added — shadow reconciliation

- **`pyligent-agents reconcile`** compares signed agreements against what a
  collateral system holds, and writes nothing anywhere. Takes a directory of
  agreements, the extractions your pipeline already produces, and the CSV your
  system exports; emits a page per document and an exception report an analyst
  works from. Read-only: no write path, no integration, no migration.
- **A discrepancy is raised only when the extraction's own citation survives
  checking.** A field whose quote is not in the document, or which states a
  different value, is reported `unverified` — never as a claim that the system is
  wrong. Telling an operations team their record contradicts a signed agreement on
  the strength of an invented sentence is worse than silence: it spends the
  credibility a shadow trial exists to build.
- Exit codes are a contract for whoever schedules it: `0` clean, `1` findings a
  human must work, `2` the run could not happen. A missing export returning the
  same code as real drift would page someone for the wrong reason.
- [`docs/RECONCILE.md`](docs/RECONCILE.md).

### Fixed — the comparison was hiding a currency change

- **`USD 500,000` and `EUR 500,000` compared equal.** Currency symbols were
  stripped before the numbers were compared, so a redenomination — among the most
  material things that can happen to a collateral book — was reported as
  agreement. The comparison was performing, on itself, exactly the silent repair
  this project exists to detect. It is now strict when both sides state a unit and
  those units differ, and tolerant when only one side states one.
- **`US$250,000` was unparseable**, so a genuine match was reported as a mismatch
  — the opposite error, found while fixing the first. `US$` appears verbatim in
  the SEC corpus. Numbers are extracted by pattern now, not by removing every unit
  anyone might write.
- **A duplicated key in an export was resolved by row order**, making a material
  finding depend on which row came last. Duplicates are named and skipped;
  which row is authoritative is not this tool's to guess.
- **Export identifier columns** (`counterparty`, `book`, `desk`) were compared as
  if they were stored terms, producing noise shaped like findings.
- **A document where nothing overlapped reported agreement.** "The system matches
  the agreement on every field compared" is vacuously true when zero fields were
  compared, and is the one claim this project must never print.


### Added — credential setup that does not handle your credential

- **`pyligent-agents setup`** reports which variable holds a credential, whether
  `.env` is git-ignored, and what will actually happen on the next run. It is
  read-only by design: it never asks for a key, never accepts one, and never
  writes one anywhere. A helper that takes a pasted secret and puts it in a file
  is how secrets reach repositories, and a library about verifiable claims should
  not be that tool.
- **The value is never printed** — not by `setup`, `doctor`, or any error — so
  their output is safe to paste into an issue. Asserted by a test that plants a
  known string and greps every surface for it.
- **[`docs/CREDENTIALS.md`](docs/CREDENTIALS.md)**, which opens by saying you
  probably do not need a key: every test, demo, eval and benchmark runs on the
  deterministic backend for nothing.

### Fixed — two things that misled a first-time user

- **`doctor` claimed the scripted backend when the call would fail.** With
  `PYLIGENT_AGENTS_BACKEND=anthropic` and no credential it printed
  `(no credential — scripted)`, but that path does not fall back: it builds the
  real client and raises at the first call. It told people they were on the safe
  deterministic path when they were one call from an error.
- **The missing-credential error was the SDK's**, a `TypeError` reading
  *"Could not resolve authentication method"* — accurate, and silent on which
  variable to set, where to put it, or that this library never reads `.env`. It
  now names the variable, the shell profile, the console URL, and the zero-spend
  alternative.
- **`.env` is a trap worth naming.** Only `bench/` reads one; the library never
  does. A key in an unread file looks exactly like no key, and the failure lands
  somewhere unrelated.


### Added — a benchmark corpus of documents that *are* CSAs

- **`bench/classify.py`** decides whether a filing *is* a Credit Support Annex
  rather than whether it mentions one. SEC full-text search answers the second
  question, so a corpus built from `q="Credit Support Annex"` is mostly filings
  with nothing to extract, and every score computed on it really measures how
  often a model correctly finds nothing. The discriminator is the annex's own
  vocabulary: the phrase is a name and travels into prose, while `Delivery
  Amount` and `Return Amount` are operative terms of the transfer obligation and
  do not. Measured: 119/120 known annexes admitted (scores 33–44), **0/24 hard
  negatives** admitted (0–11), nothing in between. The one refusal is an
  Amendment Agreement naming a CSA once — the document the phrase search is
  wrong about. Every verdict carries the evidence that produced it, so a disputed
  call is inspected rather than re-tuned.
- **`bench/build_corpus.py`** and a **97-document corpus** of SEC exhibits, each
  record carrying its source, licence, content hash, and the classifier evidence
  that admitted it.
- **First scored run.** `gemini-3.6-flash`, 97 documents, 654 fields: **99.5%
  evidence integrity**, 2 fabricated, 0 silent repair, 0 placeholder. Both
  survivors are quotes stitched across a page break — real fragments joined over
  page furniture — rather than inventions.

### Added — one live model call in CI

- The Anthropic backend had **no test of any kind**, though it is the path most
  likely to be tried first. Backend selection, the workspace header and the
  identity-linked-key error translation are all testable without a credential, so
  those run in ordinary CI and cost nothing.
- A `live-model` job makes **one real call**, verifying the reply survives
  `normalise_extraction` and that every quote it cites is actually in the source.
  **Measured cost: $0.0096 per run** (473 in + 547 out, `claude-sonnet-5` at list
  price), ≈$0.50/year on the weekly schedule. It skips itself where no secret is
  configured, so forks see it neutral rather than red.
- This exists because a deterministic suite cannot see provider drift.
  `gemini-2.5-flash` now returns `404 NOT_FOUND` to new keys — *"no longer
  available to new users"* — and nothing caught it, because nothing called it.

### Fixed — the checker was accusing correct work

- **Invisible characters no longer read as fabrication.** An SEC exhibit's filer
  left **U+200B zero-width spaces** between block elements. Python's `\s` does not
  match U+200B — it is category Cf, not whitespace — so three quotes that
  transcribed exactly what a reader sees were reported as `FABRICATED_EVIDENCE`.
  For a tool whose value rests on being believed when it says *"this citation is
  not real"*, a false accusation is worse than a miss: it teaches people to ignore
  the output. `normalize.squash` now removes invisible characters, and
  `find_flexible` absorbs them in its gap pattern rather than stripping them up
  front, so offsets still index the original document and `Source.locate` keeps
  resolving real positions.

### Fixed — reports now print on Windows

- Python on Windows picks the console's ANSI code page for stdout, and cp1252
  cannot encode the box rules, check marks, arrows and em dashes every report
  here prints. The first line of output raised `UnicodeEncodeError` and the
  process died with a traceback instead of a report — which is what a Windows
  user got running `evidence-check` on a real document. A verification tool that
  cannot print its verdict has failed at the only thing it does.
- `evidencecheck.console.use_utf8_stdout()` retunes stdout and stderr, called
  from both console entry points, the examples, the evals and the bench scripts.
  Guarded by a test that runs them under `PYTHONIOENCODING=cp1252`, so the
  regression is caught on Linux and macOS too, not only on the one Windows job.

### Fixed — the corpus builder asserted a licence it had not checked

- The first version stamped `US federal government work, public domain` onto
  every record it admitted. That was a claim about someone else's copyright with
  nothing behind it, written into the tool whose entire subject is unsupported
  claims. It was wrong for three files: two ISDA-published forms, which are
  ISDA's copyright and not federal works, and one named securitisation's
  transaction document carrying its own legal notice. The
  confidential-filename filter caught none of them, because nothing in their
  names suggested anything — a pattern list only refuses what someone thought to
  name.
- Provenance is now evidence: a document is admitted only if it carries EDGAR
  filing furniture, and the marker that admitted it is recorded. Anything else is
  **refused rather than relabelled**, because this corpus is redistributed and
  sent to third-party APIs.

### Changed

- **Licence is Apache-2.0, was MIT.** The explicit patent grant is what legal
  and open-source review functions in regulated industries ask for, and it is
  the standard for enterprise-consumable open source. It also resolves a real
  inconsistency: the evidence checks were written as Apache-2.0 and merged into
  an MIT-declared package, so the repository was claiming two things at once.
  `NOTICE` added per the licence's convention.


### Added — memory bound to its evidence

- **`MemoryStore` notes now record what they were derived from**, by content
  hash, and `recall()` checks that hash against the source as it is now. A note
  written from an agreement that has since been amended is withheld and reported
  `STALE`. This is the same drift the shadow-mode reconciliation finds between a
  margin system and a signed agreement — except it was happening inside the
  agent, where nothing looked at it, and a wrong remembered fact suppresses the
  lookup that would have corrected it.
- Four freshness states. `UNVERIFIED` — bound, but no current hash supplied —
  abstains rather than guessing, on the same principle as ADR 0006: a control
  that answers when it cannot tell answers wrongly in whichever direction its
  default falls.
- `inject()` obeys a character budget and counts what it withheld rather than
  hiding it. Memory was the one input to a prompt that grew without anyone
  deciding to grow it.
- `harness.recall()` routes memory through the harness, so one place counts what
  was injected and one place records what was used;
  `harness.report()["memory_used"]` puts those notes in the audit trail.
- `memory_is_current()` gate: an artifact that leaned on a stale note is not
  admissible.
- **24 tests.** The module previously had none, which for the one component that
  persists across every run was the wrong place to have none.
- Retrieval stays lexical on purpose. An embedding would recall more and justify
  less, and a memory whose retrieval cannot be explained cannot be audited.

### Changed

- The evidence checker is named **`evidence-check`**. It was briefly `unsourced`,
  which named the defect rather than the product and read like a bug report in a
  procurement document. `certify` was rejected despite sounding the most
  enterprise-ready: the tool reports and certifies nothing, and naming it
  otherwise would be the same overclaim it exists to detect.

- **Evidence checks are now imported from `evidence-check`, not defined here.** The
  logic existed in two places and had already drifted: this module knew five
  placeholder markers, `evidence-check` knew ten, so a field holding `-` or `none`
  passed the gate and failed the CLI. The same artifact got two verdicts.
  Drift in a comparison rule is silent — both copies keep passing their own
  tests — and every number measured with either is quietly wrong until somebody
  compares them.
- This costs the "no required dependencies" claim. Now stated honestly: one
  dependency, and it has none of its own. See ADR 0002's amendment.

### Added

- `no_silent_repair()` — a genuine citation naming a different value, which
  `evidence_present` and `evidence_verbatim` both pass. Not in the
  `evidence_gated_extraction` bundle yet: adding it changes gate counts, eval
  baselines and every published figure, which is a separate decision.
- `tests/test_shared_checks.py` — asserts the gate and the CLI return the same
  verdict on the same artifact.

### Added — collateral chain and shadow mode

Repositions the repository around ISDA collateral, with the generic agent
patterns retained as supporting material in `docs/PATTERNS.md`.

- **`examples/collateral/constraints.py`** — a verified CSA extraction becomes a
  machine-readable constraint set where **every constraint carries the verbatim
  clause that established it**. `certify()` asks whether the constraints an
  optimiser needs are present, whether each traces to a machine-checked clause,
  and whether anything in the agreement could not be expressed at all.
  `ConstraintPack.unsupported` declares the latter: a pack that silently drops a
  Valuation Agent's discretion or a ratings trigger looks complete and is not.
- **`examples/collateral/shadow.py`** — shadow-mode reconciliation against a
  margin system's stored terms, with severity by call impact and the clause
  behind every disagreement. `python examples/run.py shadow --drift` shows the
  case that justifies a trial: a VM amendment nobody re-keyed, so the system has
  been sizing calls against an unsecured band that no longer exists.
- Shadow mode denies every tool above `READ_ONLY` at the harness **even with an
  approver attached**, so the no-write guarantee does not depend on how the
  stack was configured. Asserted by
  `test_shadow_mode_cannot_reach_a_tool_with_an_external_effect`.
- **`pyligent-agents validation-pack`** — assembles the evidence a model-risk
  review asks for: enforced controls, reproducibility basis, measurement
  baselines and tolerances, and an explicit limitations section. It reports; it
  certifies nothing, and says so.
- **`doctor` reports data residency** — whether document text leaves the
  process, where state is written, that there is no telemetry, and that tools
  are not sandboxed.
- `build_test_stack(hooks=...)` now passes hooks through to `build_stack`, which
  it previously dropped.
- 15 tests in `tests/test_collateral_shadow.py`.


## [0.2.0] — 2026-08-26

Aligns the document-intake example with two published guidelines, and fixes a
domain gate that was wrong in the expensive direction.

### Fixed

- **`mta_within_threshold` rejected the standard VM CSA.** The gate asserted
  `MTA <= Threshold`. A 2016 VM CSA elects a Threshold of **zero** — variation
  margin is fully collateralised — while the MTA remains a normal operational
  figure, so MTA legitimately exceeds Threshold in the most common CSA shape in
  the market. ISDA's own worked example (*Benchmarking Generative AI for CSA
  Clause Extraction and CDM Representation*, May 2025, Appendix Table A) is
  exactly this shape, and the gate referred it to a human every time.
  Replaced by `mta_not_transposed_with_threshold`, which only reads the
  ordering when the Threshold is non-zero — above zero it still catches a
  transposition, at zero it says nothing and does not vote. Guarded by
  `tests/test_guideline_alignment.py` and by the `csa/vm-zero-threshold` eval
  case, which must never be deleted to make a gate pass.
- Non-standard (Variant 2) rounding accepted a conditions *summary* where ISDA
  requires the complete provision text: "Do not truncate or summarize the text,
  as important details may be lost."

### Added

**ISDA CSA conformance** (`examples/document_intake/cdm.py`)
- CDM JSON representation following the published structure — `agreementTerms`
  → `agreement` → `creditSupportAgreementElections` — with per-party
  `minimumTransferAmount[]` and `threshold[]` arrays, cardinality rules, and
  the `baseAndEligibleCurrency` block. Accepted CSAs now carry `cdm`.
- Rounding decomposed into its four real fields (delivery/return amount and
  direction). A single `rounding: 100000` cannot say which way each leg goes,
  and direction decides who is over-collateralised.
- The no-rounding rule: an unmentioned rounding clause emits no rounding object.
  A defaulted `deliveryDirection: UP` is a term the parties never agreed, and it
  is invisible downstream because it is perfectly well-formed.
- Variant 1 / Variant 2 classification, with Variant 2 required to carry the
  complete provision text.
- `to_cdm()` raises `CdmError` rather than guessing; a `representable_in_cdm`
  gate makes that failure a gate failure rather than an exception.

**ISDA validation protocol** (`pyligent_agents.verify`)
- `iso_currency()` — currency codes are valid ISO 4217.
- `values_are_numeric()` — amounts are numbers, not `"500,000"`.
- `no_cross_reference_values()` — a paragraph pointer such as `13(c)(ii)` was
  not transcribed into a value field. The paper singles this out: a CSA is
  dense with references and money in adjacent sentences.
- Extraction prompts now carry the CSA vocabulary the paper found decisive —
  Threshold vs Threshold Amount vs MTA vs Independent Amount. Supplying that
  domain detail moved accuracy from ~67% to over 90% across every model tested.

**AWS Marketplace KYC conformance**
- Recency window corrected from a guessed 90 days to the published **180**.
- Accepted identity document types and accepted proof-of-address types, with
  the explicit exclusion of statements from non-bank providers and online
  digital banks — a perfectly well-formed bank statement that does not count.
- The guide's required identity data points: place of birth and country of
  citizenship, neither previously collected.
- Proof of address must be addressed to the applicant, and must not be a
  screenshot.

**Evals**
- 12 → 15 cases: `csa/vm-zero-threshold` (the regression guard),
  `kyc/address-proof-in-another-name`, `kyc/address-proof-screenshot`.
- 48 new conformance tests in `tests/test_guideline_alignment.py`, each naming
  the guideline rule it holds the code to.


### Added

**Evals** (`pyligent_agents.evals`, `evals/`)
- `EvalCase` / `Dataset`: labelled cases that must declare which gate a flawed
  document should trip. A REFER case with no named gate is rejected at
  construction — referring for the wrong reason is not a pass. A single-class
  dataset is rejected in both directions.
- `score()` / `Report`: false accepts and false refers counted separately and
  never averaged into a headline. Field comparison is numeric where possible and
  deliberately not fuzzy — on a KYC file, `Jonathon` vs `Jonathan` is the
  finding. An errored case counts on the unsafe side.
- `run_eval()` / `run_by_kind()`: a crashing system produces a report, not a
  stopped run.
- `compare()` with asymmetric tolerances: `false_accept_rate` tolerates `0.0`.
- `evals/dataset.py`: twelve cases over CSA, invoice and KYC, six clean and six
  flawed. Documents and gold labels are generated together from the same inputs
  so they cannot drift; a test asserts every gold quote is verbatim.
- `evals/personas.py`: four systems — two known-good, two known-bad in different
  directions — routed through the real `document_intake` graph rather than a
  re-implementation. `helpful` silently repairs the inconsistencies it was meant
  to report, and outranks a visibly-broken extractor on field accuracy while
  being the only one that lets flawed documents through.
- `evals/baselines/*.json`, committed, re-checked by CI and by a test.

### Fixed
- `Graph._check_side_effects` identified the dangerous node and then returned
  without raising — the build-time guarantee its docstring promised was never
  enforced. It now rejects a node declaring `compensate` with no `idempotency`:
  compensation undoes an effect that landed, and without a key the graph cannot
  tell whether it landed. The structural heuristic it used before could not
  work, and is now documented as such — `idempotency` being present is what
  *declares* a side effect, so a node without one has nothing to protect.
- CI invoked `python -m pyligent-agents` (hyphen), which is not an importable
  module name; the scaffold step could never have passed.
- `ruff check` failed repo-wide on any ruff >=0.5 (unsorted imports, unused
  imports, semicolon statements). CI would have failed on the first push.
  `evals/` is now in the lint scope too.


## [0.1.0] — 2026-08-25

First public release.

### Added

**Layer 1 — harness** (`pyligent_agents.harness`)
- `Harness`: one code path for model calls, one for tool calls
- `ContextManager`: tool-result offloading to a content-addressed `Workspace`,
  then compaction that preserves the goal turn and never orphans a `tool_use`
- `HookBus`: four interception points, with `defang_untrusted_content`,
  `redact_secrets`, `phase_guard` and `deny_restricted_without_approval` shipped
- `ToolRegistry`: permission tiers, deferred loading via `search_tools`,
  dispatch that never raises
- `Governor`: turns, tokens, USD and wall-clock, checked before the call
- `MemoryStore`: cross-run notes

**Layer 2 — loop** (`pyligent_agents.loop`)
- `AgentContract`: the four questions as constructor arguments
- Composable `StopCondition`s (`ModelSaysDone`, `GatesPass`, `Produced`,
  `Predicate`, `&`, `|`)
- `RecoveryPolicy`: one branch per error class, with runaway limits
- `Agent`: gather → act → **verify** → repeat, with push-back that names the gap

**Layer 3 — graph** (`pyligent_agents.graph`)
- Six node kinds: `Step`, `AgentNode`, `GateNode`, `HumanGate`, `MapNode`,
  `ReduceNode`
- Build-time validation: unknown dependencies, cycles, unsatisfiable `requires`
- `GraphRunner`: checkpoint, resume, deterministic replay, conditional routing,
  human pauses, compensation
- `GraphStore`: SQLite checkpoints, spans, and an idempotency ledger with a
  database-level uniqueness constraint

**Verification** (`pyligent_agents.verify`)
- A composable gate library, plus `evidence_gated_extraction()`
- `DocumentVerifier`: an independent verifier whose citations are
  substring-checked against the source
- `GateVerifier`: verification with no model and no cost

**Tooling**
- `pyligent_agents.testing`: policy builders, `build_test_stack`, `capture_prompts`,
  `assert_capped`, `assert_effects_fire_once`
- `pyligent-agents` CLI: `steps`, `doctor`, `new`, `graph`, `runs`, `trace`
- Four worked examples, one per rung of the ladder
- `examples/document_intake/`: one graph over three document types — an ISDA
  Credit Support Annex, a supplier invoice and a KYC onboarding pack — showing
  five shared generic gates plus the cross-field checks each domain needs

### Notes
- Repository `pyligent/pyligent-agents`, distribution **`pyligent-agents`**,
  import name **`pyligent_agents`**, CLI **`pyligent-agents`**.
- The core library has no required third-party dependencies.
- Pyligent Agents ships no tools and no domain, on purpose.
