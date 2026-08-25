"""Primitives: types, the error taxonomy, and identifier rules."""

from .errors import (
    BudgetExhausted,
    ContractViolation,
    DomainRefusal,
    ErrorClass,
    GraphError,
    HumanApprovalRequired,
    NodeFailed,
    StackError,
    StopConditionNotMet,
    classify,
    register_error_class,
)
from .ids import content_hash, idempotency_key, run_id, span_id
from .types import (
    LLMClient,
    LLMResponse,
    Message,
    Phase,
    PermissionTier,
    ToolSpec,
    ToolUse,
    Usage,
    assistant_turn,
    tool_result_block,
    tool_result_turn,
    user_turn,
)

__all__ = [
    "BudgetExhausted", "ContractViolation", "DomainRefusal", "ErrorClass",
    "GraphError",
    "HumanApprovalRequired", "LLMClient", "LLMResponse", "Message", "NodeFailed",
    "Phase", "PermissionTier", "StackError", "StopConditionNotMet", "ToolSpec",
    "ToolUse", "Usage", "assistant_turn", "classify", "content_hash",
    "idempotency_key", "register_error_class", "run_id", "span_id",
    "tool_result_block",
    "tool_result_turn", "user_turn",
]
