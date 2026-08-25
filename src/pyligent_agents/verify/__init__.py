"""Verification — gates you can compute, and a verifier you can falsify.

Width (more tools, more subagents, more autonomy) and trust (something checking
the output before it ships) are two separate problems. Most teams build only the
first one.
"""

from .gates import (
    Gate,
    GateReport,
    GateResult,
    GateSet,
    cross_field,
    evidence_gated_extraction,
    every_value_has,
    no_placeholder_values,
    non_empty,
    numeric_range,
    one_of,
    quotes_appear_in_source,
    required_keys,
    verified_independently,
)
from .verifier import DocumentVerifier, GateVerifier, quote_is_in

__all__ = [
    "DocumentVerifier", "Gate", "GateReport", "GateResult", "GateSet",
    "GateVerifier", "cross_field", "evidence_gated_extraction",
    "every_value_has", "no_placeholder_values", "non_empty", "numeric_range",
    "one_of", "quote_is_in", "quotes_appear_in_source", "required_keys",
    "verified_independently",
]
