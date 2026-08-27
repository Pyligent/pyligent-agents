# ADR 0006 — Domain gates cite published guidance, and a gate that says nothing does not vote

**Status:** accepted · **Date:** 2026-08-26

## Context

The document-intake example shipped a CSA gate asserting `MTA <= Threshold`,
with a confident comment explaining that a Minimum Transfer Amount above the
Threshold "is almost always the two numbers read into the wrong fields".

It was wrong, and wrong in the direction that costs the most.

A 2016 VM CSA elects a Threshold of **zero** — variation margin is fully
collateralised, so there is no uncollateralised band — while the MTA stays at a
normal operational figure. MTA therefore exceeds Threshold in what is probably
the single most common CSA shape in the market. ISDA's own worked example
(*Benchmarking Generative AI for CSA Clause Extraction and CDM Representation*,
May 2025, Appendix Table A) is exactly this shape: Threshold zero, MTA
5,000,000.

The gate would have referred every standard VM CSA to a human.

Two things made it survive review. The demo fixture used a non-zero Threshold,
so it never fired. And the reasoning *sounded* like domain knowledge — it was
fluent, specific, and plausible to anyone who had not recently read a VM CSA.

## Decision

**1. Domain gates cite the document they implement.**

A gate whose justification lives only in a code comment is a gate justified by
whoever wrote it. Where published guidance exists — an ISDA paper, a regulator's
rule, a counterparty's onboarding policy — the gate names it, and a conformance
test in `tests/test_guideline_alignment.py` holds the code to that text with one
test per rule.

This is not ceremony. It changes what happens when the guidance is revised: a
failing test that names the rule, instead of a silent divergence nobody notices
until an auditor does.

**2. A gate that says nothing does not vote.**

The replacement reads the ordering *only when the Threshold is non-zero*. Above
zero the comparison still carries information and still catches a transposition.
At zero it carries none, so it abstains rather than guessing.

The general rule: when a check's precondition does not hold, it must pass, not
fail. A gate that fires on "I cannot tell" converts every unusual-but-valid
document into a referral, and a queue full of correct documents is how a control
gets switched off.

**3. Conformance cases are load-bearing and are not deleted to make gates pass.**

`csa/vm-zero-threshold` encodes the shape that broke. It is marked in the
dataset, in the eval README, and here. If a future change makes it fail, the
change is wrong.

## Consequences

The CSA gate set went from 2 domain gates to 9, and KYC from 4 to 9 — most of
them checks the guidance names explicitly and we had simply not implemented:
ISO 4217 currency validation, amounts as numbers rather than `"500,000"`, clause
pointers such as `13(c)(ii)` kept out of value fields, accepted document types,
proof of address addressed to the applicant, the published 180-day recency
window in place of a guessed 90.

The CSA deliverable is now CDM JSON rather than a dictionary, because that is
what the guidance targets and what a collateral system can actually load.

**The cost is honest:** more gates mean more referrals, and every one of them
needs to be a referral somebody would thank you for. That is exactly what the
false-refer metric in `evals/` measures, and why its tolerance is not zero while
the false-accept tolerance is.

**What this does not fix.** Nothing here would have caught the original bug
*before* someone read the ISDA paper. The demo passed, the tests passed, the
comment was persuasive. The only control that worked was going and reading the
primary source — which is an argument for doing that at design time, not an
argument for a process.

## See also

- [`examples/document_intake/README.md`](../../examples/document_intake/README.md) — Conformance
- [`evals/README.md`](../../evals/README.md) — the regression guard
- `tests/test_guideline_alignment.py`
