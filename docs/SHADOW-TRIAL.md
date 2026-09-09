# A shadow trial

Everything this repository measures is reference-free: it asks whether a citation
holds up, never whether an extraction is right. That is the property that makes it
cheap enough to run over an inventory and honest enough to publish, and it is also
the ceiling. **The next piece of evidence is not another offline metric. It is a
bounded trial against agreements whose terms somebody already knows.**

This document is the shape of that trial, written before running one so the
measures cannot be chosen after seeing the results.

---

## The design

Run the extraction and its gates over agreements already represented in a system
of record. Compare, and write nothing anywhere. `examples/collateral` runs in
exactly this mode — every tool above `READ_ONLY` is denied at the harness, and
the test that proves it is `test_shadow_mode_cannot_reach_a_tool_with_an_external_effect`.

**Scope it small enough to finish.** 50 to 200 agreements, one asset class, one
counterparty type, one reviewer. A trial nobody completes produces no evidence,
and the failure mode of these exercises is scope, not method.

---

## What to measure

Agreed in advance, and reported separately. Averaging any two of these hides the
one that matters.

| Measure | Definition | Why it is here |
|---|---|---|
| **Confirmed material discrepancies** | Cases where the agreement and the system of record genuinely disagree, confirmed by a reviewer | The only measure that pays for the exercise |
| **False referrals** | Cases routed to a human that a reviewer judged fine | The running cost. A control nobody trusts gets switched off |
| **False admissions** | Cases admitted that a reviewer judged wrong | Tolerance is zero. Never averaged against false referrals |
| **Unresolved terms** | Terms nobody could map, with reasons | The honest denominator. `ConstraintPack.omitted` and `unsupported` are the machine's contribution to it |
| **Review time** | Minutes per agreement, and how that splits between reading and confirming | Decides whether this scales at your inventory size |
| **Cost per agreement** | Tokens, at the model you would actually deploy | Small, and worth knowing exactly rather than approximately |

**Asymmetry is the point.** A false referral costs a reviewer ten minutes. A
false admission puts a wrong term into a live process. They are different
currencies, and a single accuracy figure spends one to buy the other.

---

## Keep optimisation measured separately

If allocation sits downstream, resist reporting one improvement figure. Better
agreement data and better allocation decisions are two claims, they fail
independently, and a combined number lets a weak one hide behind a strong one.

The boundary is real and it is in the code: this repository stops at the
constraint pack. See the assurance table in
[CAPABILITIES.md](CAPABILITIES.md) — validating an allocation against a set of
constraints establishes nothing about whether those constraints represent the
agreement.

---

## What a trial cannot tell you

The reviewer is the ground truth, and one reviewer is one opinion. Where a term
is genuinely contested, record it as contested rather than resolving it by
seniority — the disagreement rate between two reviewers on the same agreements is
itself the most useful number this exercise can produce, because it bounds every
accuracy claim anyone will make afterwards.
