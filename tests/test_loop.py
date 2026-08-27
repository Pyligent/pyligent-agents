"""Layer 2 — the loop owns control: stopping, verifying, recovering."""

from __future__ import annotations

import pytest
from level2_order_agent import app, policy

from pyligent_agents.core.errors import BudgetExhausted, ContractViolation, ErrorClass
from pyligent_agents.loop import (
    Agent,
    AgentContract,
    Budget,
    ModelSaysDone,
    OnFailure,
    Produced,
    RecoveryPolicy,
    StopVerdict,
    no_verification,
)
from pyligent_agents.loop.recovery import Action
from pyligent_agents.testing import (
    assert_capped,
    build_test_stack,
    capture_prompts,
    looping,
    tools_used,
)


def _stack(pol, registry):
    return build_test_stack(pol, tools=registry)


# --- the contract ---------------------------------------------------------


def test_an_uncapped_loop_cannot_be_constructed():
    with pytest.raises(ContractViolation, match="uncapped loop is the bug"):
        Budget(max_turns=0)


def test_no_cap_is_not_a_cap():
    with pytest.raises(ContractViolation, match="not a cap"):
        Budget(max_usd=0)


def test_waiving_verification_requires_a_real_reason():
    with pytest.raises(ContractViolation, match="needs a real reason"):
        no_verification("later")
    assert no_verification("Output is validated deterministically downstream.")


def test_degrade_without_a_default_is_just_a_crash():
    with pytest.raises(ContractViolation, match="requires degrade_to"):
        AgentContract(goal="g", stop=ModelSaysDone(),
                      verifier=no_verification("validated downstream by gates"),
                      on_failure=OnFailure.DEGRADE)


def test_the_contract_prints_the_four_answers():
    assert set(app.contract("g").summary()) == {
        "goal", "stop_condition", "verifier", "budget", "on_failure"}


# --- stopping -------------------------------------------------------------


def test_a_grounded_answer_satisfies_the_composed_condition(registry):
    s = _stack(policy.order_agent_policy, registry)
    r = app.build(s.harness).run("Why is order A-1207 late?")
    assert r.ok and "£257.99" in r.answer
    assert tools_used(s) == ["get_order", "get_tracking", "quote_refund"]


def test_an_invented_amount_does_not_satisfy_the_stop_condition(registry):
    """Fluent, confident, wrong. Nothing in the text signals it."""
    s = _stack(policy.ungrounded_policy, registry)
    r = app.build(s.harness, max_turns=6).run("How much can we refund on A-1207?")

    assert [e for e in s.ledger.events if e.kind == "push_back"], "turn 1 invented £310.00"
    assert r.ok and "£257.99" in r.answer


def test_a_delivery_date_is_not_claimed_when_tracking_failed(registry):
    s = _stack(policy.order_agent_policy, registry)
    r = app.build(s.harness).run("Where is order A-1422?")
    assert r.ok and r.failed_tool_calls >= 1
    assert "will arrive" not in r.answer.lower()


def test_produced_requires_a_non_empty_key():
    from pyligent_agents.loop.agent import LoopState

    state = LoopState(goal="g", artifact={"fields": {}})
    assert not Produced("fields").check(state).done
    state.artifact = {"fields": {"a": 1}}
    assert Produced("fields").check(state).done


def test_stop_verdicts_are_truthy():
    assert StopVerdict(True, "x") and not StopVerdict(False, "y")


# --- recovery -------------------------------------------------------------


def test_each_error_class_maps_to_one_action():
    p = RecoveryPolicy()
    for cls, action in ((ErrorClass.TRANSIENT, Action.RETRY),
                        (ErrorClass.INVALID, Action.OBSERVE),
                        (ErrorClass.DOMAIN, Action.OBSERVE),
                        (ErrorClass.PERMISSION, Action.OBSERVE),
                        (ErrorClass.FATAL, Action.ESCALATE)):
        assert p.decide("t", cls).action is action
        p.on_success()


def test_transient_retries_are_bounded_then_handed_back():
    p = RecoveryPolicy(max_retries_per_tool=2)
    assert p.decide("t", ErrorClass.TRANSIENT).action is Action.RETRY
    assert p.decide("t", ErrorClass.TRANSIENT).action is Action.RETRY
    assert p.decide("t", ErrorClass.TRANSIENT).action is Action.OBSERVE


def test_thrashing_escalates():
    p = RecoveryPolicy(max_consecutive_failures=3)
    for _ in range(3):
        p.decide("t", ErrorClass.DOMAIN)
    assert p.decide("t", ErrorClass.DOMAIN).action is Action.ESCALATE


def test_a_policy_refusal_produces_a_useful_answer(registry):
    s = _stack(policy.order_agent_policy, registry)
    r = app.build(s.harness).run("Can we refund order A-1310?")
    assert r.ok and "return window" in r.answer


def test_a_denied_refund_is_presented_for_approval(registry):
    s = _stack(policy.restricted_policy, registry)
    r = app.build(s.harness, max_turns=6).run("Refund order A-1207.")
    assert r.ok and any(o.denied for o in r.outcomes)
    assert "supervisor" in r.answer


# --- budgets --------------------------------------------------------------


def test_a_looping_model_hits_the_turn_cap(registry):
    s = build_test_stack(looping("get_order", order_id="A-1207"), tools=registry)
    assert_capped(lambda: app.build(s.harness, max_turns=3, max_usd=5.0).run("x"))


def test_the_spend_cap_can_bind_before_the_turn_cap(registry):
    s = build_test_stack(looping("get_order", order_id="A-1207"), tools=registry)
    with pytest.raises(BudgetExhausted):
        app.build(s.harness, max_turns=10_000, max_usd=0.02).run("x")


def test_degrade_returns_the_safe_default(registry):
    s = build_test_stack(looping("get_order", order_id="A-1207"), tools=registry)
    agent = Agent(
        s.harness,
        AgentContract(goal="g", stop=ModelSaysDone(),
                      verifier=no_verification("nothing downstream consumes this path"),
                      budget=Budget(max_turns=2, max_usd=1.0),
                      on_failure=OnFailure.DEGRADE,
                      degrade_to={"answer": "routed for manual review"}),
        system="s")
    r = agent.run("x")
    assert not r.ok and r.degraded
    assert r.artifact == {"answer": "routed for manual review"}


# --- discovery and message discipline -------------------------------------


def test_an_agent_can_find_a_tool_it_was_not_given(registry):
    s = _stack(policy.tool_search_policy, registry)
    r = app.build(s.harness, max_turns=5).run("Compare A-1207 and A-1588.")
    assert r.ok and tools_used(s) == ["search_tools", "compare_orders"]


def test_parallel_results_return_in_one_message(registry):
    """Splitting them trains the model out of asking for parallel calls."""
    s = _stack(policy.order_agent_policy, registry)
    seen = capture_prompts(s)
    app.build(s.harness).run("Why is order A-1207 late?")

    second = seen[1]["messages"]
    results = [m for m in second if m["role"] == "user" and isinstance(m["content"], list)]
    assert len([b for b in results[0]["content"] if b["type"] == "tool_result"]) == 2


def test_the_assistant_turn_is_replayed_verbatim(registry):
    """Rebuilding it from .text drops tool_use blocks and the next call 400s."""
    s = _stack(policy.order_agent_policy, registry)
    seen = capture_prompts(s)
    app.build(s.harness).run("Why is order A-1207 late?")

    assistant = [m for m in seen[1]["messages"] if m["role"] == "assistant"][0]
    assert any(b["type"] == "tool_use" for b in assistant["content"])
