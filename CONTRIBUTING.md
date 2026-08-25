# Contributing to Trellis

Thanks for considering it. This document is short and specific, because a
contributing guide that is neither wastes your time.

## Getting set up

```bash
git clone https://github.com/pyligent/trellis && cd trellis
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                          # 115 tests, offline, ~5 seconds
python examples/run.py demo all # the four layer demos
ruff check src tests examples
```

**No API key is needed for anything**, including CI. The suite runs against
`ScriptedLLM`, which is a second implementation of the `LLMClient` contract
rather than a mock. If you find yourself needing a live model to test something,
that is a signal the thing under test is in the wrong place.

## What we are looking for

**Very welcome**

- **Backends.** `LLMClient` is one method. An OpenAI, Bedrock, Vertex, Ollama or
  local backend is a genuinely small file, and the rest of the stack does not
  change. Add prices via `register_model()`.
- **Gates.** `trellis/verify/gates.py` is a library of reusable checks. If you
  wrote a good cross-field gate for your domain, a generalised version probably
  belongs here.
- **Stop conditions.** Same argument. Grounding predicates especially.
- **Examples in other domains.** Four exist; healthcare intake, legal review,
  DevOps runbooks and data-pipeline repair have all been asked for.
- **Documentation fixes**, particularly where a doc claims something the code
  does not do.
- **A failing test that demonstrates a bug**, with or without a fix.

**Please discuss first (open an issue)**

- A **seventh node kind**. The set of six is closed on purpose — that is what
  makes graphs analysable. Show that no combination of the existing six
  expresses your shape.
- Anything that gives the library **required third-party dependencies**.
- Anything that adds a **domain** or a **default tool** to the core library.
  Trellis ships neither, deliberately.
- Async. It is wanted; the design needs discussion before the code.

**Probably not**

- A plugin system, a config DSL, or a YAML layer. The library is small enough to
  read; indirection would cost more than it saves.
- Anything that makes a guardrail optional by default.

## The bar for a pull request

### 1. Every guardrail has a test that fails without it

This is the house rule. Delete the guardrail, run the test, watch it go red. A
rule that is not a failing test is a rule that will be broken within two
quarters, by someone who was not in the room when you agreed it.

Use `trellis.testing`:

```python
from trellis.testing import assert_capped, build_test_stack, calls, looping, turn

def test_a_looping_model_is_stopped(registry):
    stack = build_test_stack(looping("get_order", order_id="A-1"), tools=registry)
    assert_capped(lambda: build(stack.harness).run("go"))
```

### 2. Failure paths, not just the happy path

A demo or a test that only covers success teaches nothing about production. Every
example in this repo has at least one section where something goes wrong and is
handled.

### 3. Comments explain *why*, not *what*

The codebase leans on comments that say what a line prevents:

```python
# PERMISSION, not FATAL: the agent must be able to respond with "here is what I
# would send; it needs sign-off" rather than treating a denial as the end.
```

That is the standard. `# increment the counter` is not.

### 4. Keep the public surface small

New public names need a reason. Prefer extending an existing type over adding a
sibling.

### 5. Honest limits

If your gate catches fabricated evidence but not irrelevant evidence, say so in
the docstring. Overstating a control is worse than not having it, because
someone will rely on it.

## Style

- Ruff for lint and import order; line length 100.
- Type hints on public functions. `from __future__ import annotations` at the top.
- Docstrings that would help a reviewer at 3am, not a documentation generator.
- No emoji in library code.

## Commit and PR

Conventional-ish commits are appreciated (`fix:`, `feat:`, `docs:`), not
enforced. Describe **what breaks without this change**.

If your PR touches agent behaviour, fill in the four questions in the PR
template. They are the same four the library makes you answer in code.

## Releasing

Maintainers: bump `__version__` and `pyproject.toml`, update `CHANGELOG.md`, tag
`v0.x.y`.

**The distribution name is `pyligent-agents`; the import name is `trellis`.**
They differ, so say both whenever you write an install line — a reader who
sees only one of them will get it wrong.

## Getting help

Open a discussion for design questions, an issue for bugs. Include the output of
`trellis doctor` — it reports backend, model routing, pricing coverage and
governor settings, which answers most "why is it doing that?" questions.
