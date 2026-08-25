# ADR 0002 — Trellis ships no tools and no domain

**Status:** accepted · **Date:** 2026-08-25

## Context

Most agent libraries ship a starter toolkit: a web search, a shell, a file
reader, sometimes a vector store. It demos well and it is the first thing people
delete.

The pressure to include them is real — a library with no tools looks empty. But
a default tool set makes three promises the library cannot keep: that these are
the right tools, that their permission tiers match your risk appetite, and that
their failure modes match your recovery policy.

## Decision

The core library ships **no tools and no domain**, and has **no required
third-party dependencies**.

`build_stack()` defaults to an empty `ToolRegistry`. The four built-in tools that
do exist — `read_artifact` and `search_tools` — are harness mechanics, not
capabilities: they exist because offloading and deferred loading need a way back
in.

Examples live in `examples/`, outside the package.

## Consequences

**Good.** Nothing to delete, and nothing to fight. `pip install pyligent-agents`
adds no transitive dependencies, which matters in exactly the regulated
environments where the rest of this library is most useful. The absence also
makes the first design conversation the right one: *what are your tools, and
what tier is each?* — rather than *how do I turn off the shell tool?*

**Bad.** A worse first impression. `import trellis` and nothing happens. We
compensate with `trellis new`, which scaffolds a project whose three guardrail
tests pass immediately, and with four worked examples.

**Also.** It means the library cannot be evaluated by running it — you have to
read it or scaffold something. For a library whose entire argument is *structure
you can inspect*, that is an acceptable trade.

**Rejected alternative:** shipping tools behind an extra
(`pip install pyligent-agents[tools]`). It preserves the dependency story and
loses the design argument, which is the part that matters.
