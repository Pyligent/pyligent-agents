"""Identifiers, and the rule about them.

Run and span ids are random: they name one execution.

**Idempotency keys are never random.** They are derived from the facts of the
action — counterparty, asset, amount, date — so that the same action always
produces the same key. A key containing a clock or a uuid guarantees a
duplicate side effect on resume, which is the exact failure durable state
exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any


def run_id(prefix: str = "run") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def span_id() -> str:
    return f"sp_{uuid.uuid4().hex[:10]}"


def content_hash(payload: Any) -> str:
    """A stable hash of any JSON-able value.

    `sort_keys=True` is load-bearing: without it two equal dicts can hash
    differently depending on insertion order, and every derived key becomes
    non-deterministic.
    """
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def idempotency_key(action: str, **facts: Any) -> str:
    """Build a key from the facts of an action.

    >>> idempotency_key("instruct", cpty="CP-ATLAS", asset="CASH-USD-01",
    ...                 amount=1200000.0, settle="2026-08-22")
    'instruct:amount=1200000.0|asset=CASH-USD-01|cpty=CP-ATLAS|settle=2026-08-22'

    Human-readable on purpose. When Operations asks why an instruction did not
    re-send, you can read the answer off the ledger.
    """
    if not facts:
        raise ValueError(
            "An idempotency key needs facts. A key with no facts is a uuid "
            "wearing a costume, and will fire twice."
        )
    parts = "|".join(f"{k}={facts[k]}" for k in sorted(facts))
    return f"{action}:{parts}"
