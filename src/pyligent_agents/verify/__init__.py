"""Verification — gates you can compute, and a verifier you can falsify.

Width (more tools, more subagents, more autonomy) and trust (something checking
the output before it ships) are two separate problems. Most teams build only the
first one.
"""

from .gates import (
    ISO_4217,
    Gate,
    GateReport,
    GateResult,
    GateSet,
    cross_field,
    every_value_has,
    evidence_gated_extraction,
    iso_currency,
    memory_is_current,
    no_cross_reference_values,
    no_placeholder_values,
    no_silent_repair,
    non_empty,
    numeric_range,
    one_of,
    quotes_appear_in_source,
    required_keys,
    values_are_numeric,
    verified_independently,
)
from .verifier import DocumentVerifier, GateVerifier, quote_is_in

__all__ = [
    "values_are_numeric",
    "no_cross_reference_values",
    "no_silent_repair",
    "iso_currency",
    "memory_is_current",
    "ISO_4217",
    "DocumentVerifier", "Gate", "GateReport", "GateResult", "GateSet",
    "GateVerifier", "cross_field", "evidence_gated_extraction",
    "every_value_has", "no_placeholder_values", "non_empty", "numeric_range",
    "one_of", "quote_is_in", "quotes_appear_in_source", "required_keys",
    "verified_independently",
]
