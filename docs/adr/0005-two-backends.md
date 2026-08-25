# ADR 0005 — Two backends behind one `LLMClient`

**Status:** accepted · **Date:** 2026-08-25

## Context

We need to test agent *behaviour*: that a turn cap fires, that a tool error is
recovered from, that a restricted tool is denied, that a resumed workflow does
not refund a customer twice.

None of that is testable against a live model. Ask a real model to loop forever
and it will sometimes stop. Ask it to recover from an error and it recovers
differently each run. A flaky test on a control is worse than no test: it gets
marked `xfail`, and the control quietly stops being one.

Separately, every new contributor needs to run the whole thing on day one, and CI
should not hold a credential.

## Decision

`LLMClient` is a `Protocol` with one method and two implementations:

- `AnthropicLLM` — the real Messages API
- `ScriptedLLM` — deterministic, driven by a policy function that sees the whole
  call: model, system prompt, messages, tools

`build_stack(policy=...)` selects the scripted path; without it, `build_backend`
uses the real API only when a credential is present.

`ScriptedLLM` is explicitly **not a mock of the SDK**. It is a second
implementation of the same contract. Mocks assert on calls; this one produces
behaviour — including behaviour we want to be bad. `looping()` never stops;
`fabricating_policy` invents its evidence.

## Consequences

**Good.** Guardrails have tests that go red when the guardrail is deleted. CI
needs no credential and spends nothing. Fault injection is a fixture rather than
a chaos exercise. Swapping to the real API is one environment variable, through
the identical code path — there is no separate test harness, because a separate
test harness is a harness you are not testing.

**Bad.** Two implementations can drift. Mitigated by keeping the interface tiny
and using the Anthropic wire format for messages, so the scripted path exercises
the same shapes. And the scripted backend proves control flow, not output
quality: it will never tell you a prompt got worse. A gold-set eval harness is
separate work and is deliberately not in this library.

**Also.** Policies route on the system prompt, which couples fixtures to prompt
text. Acceptable: a prompt change that breaks a fixture is a prompt change that
deserves a second look.

**Adding a backend** is a small file. `LLMClient` is one method; the rest of the
stack does not change. Register prices with `register_model()` so the governor
bills correctly.
