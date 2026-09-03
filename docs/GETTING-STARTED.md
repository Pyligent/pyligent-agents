# Getting started

There are two ways in, and they give you different things. Pick by what you want to
do. Neither needs an API key.

| | `pip install` | `git clone` |
|---|---|---|
| The two commands, and the library | yes | yes |
| Check your own extractions | yes | yes |
| Reconcile against your system of record | yes | yes |
| Scaffold a new project | yes | yes |
| Run the worked examples | no | yes |
| Reproduce the published benchmark figures | no | yes |

The package ships the code, not the demonstration material. The examples and the
97-document corpus live in the repository because they are large and because a
benchmark you cannot re-run is not evidence.

---

## Track A — use it on your own documents

```bash
pip install pyligent-agents
pyligent-agents setup
```

`setup` reports whether a credential is configured, whether `.env` is ignored by git,
and whether the next run will make billed API calls or use the deterministic backend.
It never reads or prints the credential value. Nothing below needs a key.

Two commands are installed:

```bash
evidence-check --help        # check an extraction against its source
pyligent-agents --help       # setup, reconcile, new, doctor, graph, runs, trace
```

Go to **section 3**. Sections 1, 4 and 5 need the repository.

---

## Track B — evaluate the project, or contribute

```bash
git clone https://github.com/Pyligent/pyligent-agents
cd pyligent-agents
python3 examples/run.py shadow --drift
```

That first command needs no installation at all. It reads a credit support annex,
compares the terms against what a margin system holds, and prints the disagreements
with the clause behind each one. Nothing is written anywhere.

Expect output like:

```
── MATERIAL · threshold ───────────────────────────
  agreement says : 0
  system says    : 5000000
  impact         : changes when a call is made and by how much
  clause         : "Threshold" means with respect to each party: USD 0.
```

**Exit code 1 is expected here.** It means material discrepancies were found and a
person should look. Exit 0 means the system and the agreement agree. Exit 2 means the
run could not happen — a missing file, usually.

Then install for the rest:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                       # 484 tests, about 20 seconds
```

---

## Check an extraction you already have

*Works from either track.*

This is the smallest useful thing the project does, and it needs no model.

```bash
evidence-check contract.pdf extraction.json
```

`extraction.json` is whatever your existing pipeline produces. Seven shapes are
understood; the common one is:

```json
{
  "threshold": {
    "value": "USD 0",
    "quote": "\"Threshold\" means with respect to each party: USD 0."
  }
}
```

Three findings are reported:

| Finding | Meaning |
|---|---|
| `FABRICATED_EVIDENCE` | the citation does not appear in the document |
| `SILENT_REPAIR` | the citation appears, but states a value other than the one extracted |
| `PLACEHOLDER_VALUE` | the value is `TBD`, `N/A` or similar |

Exit code is 0 when nothing is found, 1 on findings, 2 when the run could not happen.
Use `--fail-on any` in CI if you want warnings to fail the build too.

**This confirms evidential support, not accuracy.** A citation may be present,
verbatim, and still cite the wrong clause.

---

## See the three document packs

*Track B — these live in the repository.*

```bash
python examples/run.py intake invoice
python examples/run.py intake kyc --flaw
python examples/run.py intake csa
```

Each pack pairs five generic gates with domain rules. The `--flaw` variants introduce
an error the generic gates cannot see:

| Pack | What the domain gate catches |
|---|---|
| invoice | line items that do not reconcile to the stated totals |
| kyc | a name on the identity document that differs from the application |
| csa | minimum transfer amount and threshold transposed |

The KYC case is worth running. The extraction cites the passport accurately, so the
evidence check reports nothing — correctly, because nothing contradicts anything. The
`name_matches_document` gate is what refers it to a person. Generic checks catch
invented citations; domain rules catch values that are wrong but honestly cited.

These packs are **worked examples, not calibrated controls.** They encode plausible
rules, not any institution's policy.

---

## Reproduce the published measurements

*Track B — the corpus lives in the repository.*

```bash
python bench/run.py --corpus bench/corpus-sec   # 97 SEC exhibits, one model
python bench/run.py --corpus bench/corpus       # 12 documents, three models
```

Both run offline from stored model outputs. Read coverage together with integrity:
integrity is measured only over the fields an extractor answered, so omitting a field
raises it.

---

## Start your own project

*Works from either track.*

```bash
pyligent-agents new myproject
cd myproject
pip install pytest        # if you installed without the [dev] extra
pytest
```

This scaffolds an agent with budget caps, a stop condition and a verifier already
wired, plus tests that fail if you remove a guardrail. The generated `README.md`
repeats the install line, so the project stands on its own.

---

## Using a real model

Not required for anything above.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
pyligent-agents setup          # confirm the library sees it
```

This library does not read `.env` files. If you keep keys in one, export them into
the environment first. See [CREDENTIALS.md](CREDENTIALS.md) for details, including
identity-linked keys and CI secrets.

---

## Where to go next

| Question | Document |
|---|---|
| What can this do, and what can it not? | [CAPABILITIES.md](CAPABILITIES.md) |
| How do I compare agreements against my system of record? | [RECONCILE.md](RECONCILE.md) |
| How do I set up credentials properly? | [CREDENTIALS.md](CREDENTIALS.md) |
| What exactly does each evidence check do? | [SPEC-evidence-checks.md](SPEC-evidence-checks.md) |
| How do I test an agent? | [TESTING.md](TESTING.md) |
| How are the three layers meant to be used? | [HARNESS.md](HARNESS.md), [LOOP.md](LOOP.md), [GRAPH.md](GRAPH.md) |
| Why was something designed this way? | [adr/](adr/) |

---

## Common questions

**Do I need an API key?** No, for everything on this page. Only real model calls need
one.

**Does this extract terms from documents?** No. It checks extractions that another
pipeline produced, and runs the gates and workflow around them.

**Can I use my own document type?** Yes. A pack is a set of required fields and gate
functions. [CAPABILITIES.md](CAPABILITIES.md) describes the interface.

**Why did a command exit non-zero?** For the checking commands, `1` means findings
that need a person and `2` means the run could not happen. A non-zero exit is not
necessarily an error.
