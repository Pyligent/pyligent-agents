"""The error taxonomy.

Most agent systems have exactly two error categories — "it worked" and "it
threw" — and that is why they behave badly under failure. A timeout and a
mistyped identifier and a denied permission all deserve different responses,
and if your code cannot tell them apart it will do the same wrong thing to all
three.

Five classes, and each one maps to exactly one recovery action:

    TRANSIENT   the world was briefly unavailable      -> retry with backoff
    INVALID     the agent's own arguments were wrong    -> feed back, let it fix
    DOMAIN      the business layer legitimately refused -> feed back, let it route
    PERMISSION  a human has not authorised this         -> feed back, present for sign-off
    FATAL       the run cannot continue                 -> stop, escalate

`RecoveryPolicy` in the loop layer consumes exactly this enum. Adding a sixth
class means adding a sixth branch there, on purpose.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorClass(str, Enum):
    TRANSIENT = "transient"
    INVALID = "invalid"
    DOMAIN = "domain"
    PERMISSION = "permission"
    FATAL = "fatal"

    @property
    def retryable(self) -> bool:
        """Only TRANSIENT gets a blind retry.

        Retrying an INVALID call with identical arguments is a busy-wait that
        costs money. The agent has to see the error and change something.
        """
        return self is ErrorClass.TRANSIENT

    @property
    def observable(self) -> bool:
        """Should the model see this as a tool_result and get another turn?"""
        return self in {
            ErrorClass.INVALID,
            ErrorClass.DOMAIN,
            ErrorClass.PERMISSION,
            ErrorClass.TRANSIENT,
        }


class StackError(Exception):
    """Base for everything this stack raises deliberately."""

    error_class: ErrorClass = ErrorClass.FATAL
    code: str = "stack_error"

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "class": self.error_class.value, "message": str(self)}


# --- control failures: the run has lost its guarantees ---------------------


class BudgetExhausted(StackError):
    """A governor tripped. Loud on purpose.

    A silently truncated agent that stops mid-workflow looks like a completed
    run to everything downstream, which is far more dangerous than a failure
    with a number you can put in an incident report.
    """

    code = "budget_exhausted"

    def __init__(self, resource: str, used: float, cap: float, unit: str = ""):
        self.resource, self.used, self.cap, self.unit = resource, used, cap, unit
        super().__init__(
            f"{resource} budget exhausted: {used:,.4g}{unit} used against a "
            f"{cap:,.4g}{unit} cap."
        )


class StopConditionNotMet(StackError):
    """The loop ran out of turns without satisfying its stop condition."""

    code = "stop_condition_not_met"

    def __init__(self, condition: str, turns: int, last_failure: str = ""):
        self.condition, self.turns = condition, turns
        suffix = f" Last check: {last_failure}" if last_failure else ""
        super().__init__(
            f"Did not satisfy '{condition}' within {turns} turn(s).{suffix}"
        )


class ContractViolation(StackError):
    """An agent was constructed without answering the four questions."""

    code = "contract_violation"


class GraphError(StackError):
    """A graph is malformed. Raised at build time, never at run time."""

    code = "graph_error"


class NodeFailed(StackError):
    """A graph node exhausted its retries."""

    code = "node_failed"

    def __init__(self, node: str, attempts: int, cause: str):
        self.node, self.attempts, self.cause = node, attempts, cause
        super().__init__(f"Node '{node}' failed after {attempts} attempt(s): {cause}")


class HumanApprovalRequired(StackError):
    """A graph reached a human gate and paused.

    Not a failure. The run is checkpointed and waiting; resume it once the
    decision is recorded.
    """

    error_class = ErrorClass.PERMISSION
    code = "human_approval_required"

    def __init__(self, node: str, prompt: str, payload: dict[str, Any]):
        self.node, self.prompt, self.payload = node, prompt, payload
        super().__init__(f"Node '{node}' needs a human decision: {prompt}")


# --- how YOUR errors join the taxonomy ------------------------------------


class DomainRefusal(Exception):
    """Base class for a legitimate business refusal from your own code.

    Subclass this for the things your domain says no to: outside the return
    window, insufficient balance, ineligible collateral, policy forbids it.
    They classify as DOMAIN, which means the agent sees the reason and routes
    around it instead of retrying or crashing.

        class RefundNotPermitted(DomainRefusal):
            ...

    The distinction that matters: a refusal is an *answer*, not a fault. If your
    domain raises a bare `ValueError` for "outside the return window", the agent
    is told its arguments were wrong — and it will try different arguments,
    which is exactly the wrong response.
    """

    code = "domain_refusal"


# Exceptions you do not own — an HTTP client's timeout, a driver's error — can
# be mapped here instead of subclassed.
_REGISTERED: list[tuple[type[BaseException], ErrorClass]] = []


def register_error_class(exc_type: type[BaseException], error_class: ErrorClass) -> None:
    """Teach the taxonomy about a third-party exception.

        register_error_class(httpx.TimeoutException, ErrorClass.TRANSIENT)
        register_error_class(stripe.error.CardError, ErrorClass.DOMAIN)

    Most specific registration wins; later registrations take precedence over
    earlier ones, so an application can override a library's mapping.
    """
    _REGISTERED.insert(0, (exc_type, error_class))


def classify(exc: BaseException) -> ErrorClass:
    """Map an arbitrary exception onto the taxonomy.

    Resolution order: explicit `StackError`, your `DomainRefusal`, anything
    registered, then built-in types.

    **Unknown exceptions are FATAL, not TRANSIENT.** An unrecognised failure
    must never be retried into a bill.
    """
    if isinstance(exc, StackError):
        return exc.error_class
    if isinstance(exc, DomainRefusal):
        return ErrorClass.DOMAIN
    for exc_type, error_class in _REGISTERED:
        if isinstance(exc, exc_type):
            return error_class
    if isinstance(exc, TimeoutError | ConnectionError):
        return ErrorClass.TRANSIENT
    if isinstance(exc, TypeError | ValueError | KeyError):
        return ErrorClass.INVALID
    if isinstance(exc, PermissionError):
        return ErrorClass.PERMISSION
    return ErrorClass.FATAL
