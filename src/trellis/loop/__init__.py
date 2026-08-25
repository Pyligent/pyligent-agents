"""Layer 2 — the loop. Gather, act, verify, repeat."""

from .agent import Agent, AgentResult, LoopState, default_extractor
from .contract import (
    AgentContract,
    Budget,
    NoVerification,
    OnFailure,
    Verifier,
    VerifierVerdict,
    no_verification,
)
from .recovery import Action, Recovery, RecoveryPolicy
from .stop import (
    AllOf,
    AnyOf,
    GatesPass,
    ModelSaysDone,
    Predicate,
    Produced,
    StopCondition,
    StopVerdict,
)

__all__ = [
    "Action", "Agent", "AgentContract", "AgentResult", "AllOf", "AnyOf",
    "Budget", "GatesPass", "LoopState", "ModelSaysDone", "NoVerification",
    "OnFailure", "Predicate", "Produced", "Recovery", "RecoveryPolicy",
    "StopCondition", "StopVerdict", "Verifier", "VerifierVerdict",
    "default_extractor", "no_verification",
]
