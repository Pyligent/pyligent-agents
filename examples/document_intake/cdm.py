"""CSA clauses to ISDA Common Domain Model (CDM) JSON.

A flat `{field: value}` extraction is where most CSA projects stop. It is also
where the value stops: collateral systems consume CDM, not a bespoke dictionary,
and the point of digitising an agreement is that the terms flow into the margin
engine without a human retyping them.

The structure and cardinalities here follow the worked examples in ISDA's
*Benchmarking Generative AI for CSA Clause Extraction and CDM Representation*
(May 2025), Appendix Table A and the CDM Fields Reference:

    agreementTerms.agreement.creditSupportAgreementElections
      ├─ baseAndEligibleCurrency
      │    ├─ baseCurrency                     (1..1)  ISO code
      │    ├─ eligibleCurrency[]               (1..*)  ISO codes
      │    └─ eligibleCurrencyInclBaseCurrency (1..1)  bool
      ├─ minimumTransferAmount[]               per party
      │    └─ mtaType.fixedAmount{amount, currency, party}
      ├─ threshold[]                           per party
      │    └─ thresholdType.fixedAmount{amount, currency, party}
      └─ creditSupportObligations
           └─ rounding                         (0..1)
                ├─ currency          (1..1)  ISO code
                ├─ deliveryAmount    (1..1)  number
                ├─ deliveryDirection (1..1)  UP | DOWN
                ├─ returnAmount      (1..1)  number
                ├─ returnDirection   (1..1)  UP | DOWN
                └─ other             (0..1)  string, for Variant 2

Two rules from that paper are load-bearing and easy to get wrong:

**The no-rounding rule.** If the agreement does not mention rounding, emit no
rounding object at all. Do not default it. A defaulted `deliveryDirection: UP`
is an invented contractual term, and it is invisible downstream because it is
perfectly well-formed.

**Variant classification.** Variant 1 is *only* standard, unconditional
rounding: Delivery UP, Return DOWN, fixed multiples, one currency, no
conditions. Anything else is Variant 2, and Variant 2 must carry the complete
provision text in `other` rather than a truncated summary — the condition you
drop is the one that mattered.
"""

from __future__ import annotations

from typing import Any

PARTY_1 = "PARTY_1"
PARTY_2 = "PARTY_2"

UP = "UP"
DOWN = "DOWN"
DIRECTIONS = (UP, DOWN)


class CdmError(ValueError):
    """The extraction cannot be represented in CDM without inventing something."""


def _value(fields: dict[str, Any], name: str) -> Any:
    entry = fields.get(name)
    return entry.get("value") if isinstance(entry, dict) else entry


def _amount(fields: dict[str, Any], name: str) -> float | None:
    """A CDM amount is a number. A string that looks like one is a defect."""
    raw = _value(fields, name)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise CdmError(
            f"{name!r} is {raw!r}; CDM amounts must be numbers, not strings. "
            f"Parse it at extraction time or leave it out."
        )
    return float(raw)


def _per_party(fields: dict[str, Any], base: str) -> list[tuple[str, float]]:
    """Read `<base>_party_a` / `<base>_party_b`, falling back to `<base>`.

    Most CSAs say "with respect to each party", and both parties then carry the
    same figure. Plenty do not — an asymmetric Threshold is a normal commercial
    outcome between a dealer and a fund — so the CDM shape is a list either way
    and a single extracted value fans out to both parties explicitly.
    """
    a, b = _amount(fields, f"{base}_party_a"), _amount(fields, f"{base}_party_b")
    if a is None and b is None:
        shared = _amount(fields, base)
        return [] if shared is None else [(PARTY_1, shared), (PARTY_2, shared)]
    out = []
    if a is not None:
        out.append((PARTY_1, a))
    if b is not None:
        out.append((PARTY_2, b))
    return out


def classify_rounding(fields: dict[str, Any]) -> str:
    """Variant 1 (standard) or Variant 2 (everything else).

    Per the paper's Important Notes: "Only use Variant 1 when rounding is
    completely standard and unconditional. ANY deviation ... should be
    classified as Variant 2. When in doubt, use Variant 2 and include the
    full text."
    """
    delivery = _amount(fields, "rounding_delivery_amount")
    ret = _amount(fields, "rounding_return_amount")
    d_dir = _value(fields, "rounding_delivery_direction")
    r_dir = _value(fields, "rounding_return_direction")
    conditional = bool(_value(fields, "rounding_conditions"))

    standard = (
        d_dir == UP
        and r_dir == DOWN
        and delivery is not None
        and delivery == ret
        and not conditional
    )
    return "VARIANT_1" if standard else "VARIANT_2"


def to_cdm(artifact: dict[str, Any]) -> dict[str, Any]:
    """Map an extracted CSA artifact into CDM JSON.

    Raises `CdmError` rather than guessing. A mapping that quietly fills a hole
    is worse than one that refuses: the hole is recoverable, the invention is
    not.
    """
    fields = artifact.get("fields") or {}

    base = _value(fields, "base_currency")
    if not base:
        raise CdmError("baseCurrency (1..1) is required and was not extracted")

    eligible = _value(fields, "eligible_currency")
    if eligible is None:
        eligible = [base]
    elif isinstance(eligible, str):
        eligible = [c.strip() for c in eligible.split(",") if c.strip()]

    elections: dict[str, Any] = {
        "baseAndEligibleCurrency": {
            "baseCurrency": base,
            "eligibleCurrency": list(eligible),
            "eligibleCurrencyInclBaseCurrency": base in eligible,
        }
    }

    mta = _per_party(fields, "mta")
    if mta:
        elections["minimumTransferAmount"] = [
            {"mtaType": {"fixedAmount": {"amount": amt, "currency": base, "party": party}}}
            for party, amt in mta
        ]

    threshold = _per_party(fields, "threshold")
    if threshold:
        elections["threshold"] = [
            {"thresholdType": {"fixedAmount": {"amount": amt, "currency": base, "party": party}}}
            for party, amt in threshold
        ]

    rounding = _rounding(fields, base)
    if rounding is not None:
        elections["creditSupportObligations"] = {"rounding": rounding}

    return {"agreementTerms": {"agreement": {"creditSupportAgreementElections": elections}}}


def _rounding(fields: dict[str, Any], base: str) -> dict[str, Any] | None:
    """The rounding object, or None when the agreement is silent.

    Silence is the important case. "If rounding is not mentioned at all, do not
    assume any default rounding and do not generate JSON output."
    """
    delivery = _amount(fields, "rounding_delivery_amount")
    ret = _amount(fields, "rounding_return_amount")
    d_dir = _value(fields, "rounding_delivery_direction")
    r_dir = _value(fields, "rounding_return_direction")

    if delivery is None and ret is None and not d_dir and not r_dir:
        return None  # not mentioned — emit nothing

    missing = [
        name for name, v in (
            ("deliveryAmount", delivery), ("returnAmount", ret),
            ("deliveryDirection", d_dir), ("returnDirection", r_dir),
        ) if v is None
    ]
    if missing:
        raise CdmError(
            f"rounding is present but {', '.join(missing)} (1..1) missing. "
            f"Per ISDA: if the direction is unspecified, classify as Variant 2 "
            f"and capture the full provision text."
        )

    for label, direction in (("deliveryDirection", d_dir), ("returnDirection", r_dir)):
        if direction not in DIRECTIONS:
            raise CdmError(f"{label} = {direction!r}; CDM allows only UP or DOWN")

    out: dict[str, Any] = {
        "currency": _value(fields, "rounding_currency") or base,
        "deliveryAmount": delivery,
        "deliveryDirection": d_dir,
        "returnAmount": ret,
        "returnDirection": r_dir,
    }

    if classify_rounding(fields) == "VARIANT_2":
        # A summary will not do: "Do not truncate or summarize the text,
        # as important details may be lost."
        text = _value(fields, "rounding_full_text")
        if not text:
            raise CdmError(
                "non-standard rounding (Variant 2) must carry the complete "
                "provision text in `other`; none was extracted"
            )
        out["other"] = text
    return out
