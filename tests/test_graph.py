"""Layer 3 — the graph owns coordination: validation, resume, idempotency."""

from __future__ import annotations

import pytest
from level3_refund_workflow import app as refund
from level3_refund_workflow import policy as refund_policy
from level4_invoice_intake import app as invoice
from level4_invoice_intake import policy as invoice_policy

from pyligent_agents import idempotency_key
from pyligent_agents.core.errors import GraphError
from pyligent_agents.graph import Graph, GraphState, RetryPolicy, Step
from pyligent_agents.testing import assert_effects_fire_once, build_test_stack

# --- validation happens before anything costs money -----------------------


def test_a_missing_dependency_is_a_build_time_error():
    with pytest.raises(GraphError, match="unknown node"):
        Graph("g").add(Step(id="b", fn=lambda s: 1, depends_on=("a",))).validate()


def test_a_cycle_is_a_build_time_error():
    with pytest.raises(GraphError, match="Cycle detected"):
        (Graph("g").add(Step(id="a", fn=lambda s: 1, depends_on=("b",)))
                   .add(Step(id="b", fn=lambda s: 1, depends_on=("a",))).validate())


def test_reading_a_key_nothing_provides_is_caught():
    """A node reading a missing key and getting None is how a graph produces a
    confident answer built on a hole."""
    with pytest.raises(GraphError, match="nothing upstream provides"):
        Graph("g").add(Step(id="a", fn=lambda s: 1, requires=("ghost",))).validate()


def test_seeds_satisfy_requires():
    Graph("g", seeds=("x",)).add(Step(id="a", fn=lambda s: 1, requires=("x",))).validate()


def test_duplicate_ids_are_rejected():
    with pytest.raises(GraphError, match="Duplicate"):
        Graph("g").add(Step(id="a", fn=lambda s: 1)).add(Step(id="a", fn=lambda s: 2))


def test_the_shipped_example_graphs_validate(registry):
    s = build_test_stack(invoice_policy.good_policy, tools=registry)
    assert refund.build_graph().validate()
    assert invoice.build_graph(s.harness).validate()


def test_topological_order_is_deterministic():
    """Replay only means something if node order is stable."""
    g = refund.build_graph().validate()
    assert g.topological() == g.topological()
    assert g.layers()[0] == ["read_ticket"]


def test_a_graph_can_be_rendered_and_exported(registry):
    s = build_test_stack(invoice_policy.good_policy, tools=registry)
    g = invoice.build_graph(s.harness).validate()
    assert "invoice_intake" in g.render()
    assert "graph TD" in g.to_mermaid()
    assert {n["id"] for n in g.to_dict()["nodes"]} == set(g.nodes)


def test_require_names_what_is_available():
    with pytest.raises(KeyError, match="Available"):
        GraphState("r", "g", data={"a": 1}).require("b")


def test_fingerprint_is_order_independent():
    a = GraphState("r", "g", data={"x": 1, "y": 2})
    b = GraphState("r", "g", data={"y": 2, "x": 1})
    assert a.fingerprint(("x", "y")) == b.fingerprint(("x", "y"))


# --- running, pausing, resuming ------------------------------------------


def _refund_stack(tmp_path, registry, name="state"):
    return build_test_stack(refund_policy.drafting_policy, tools=registry,
                            state_dir=tmp_path / name)


def test_a_human_gate_pauses_rather_than_fails(tmp_path, registry):
    s = _refund_stack(tmp_path, registry)
    r = s.runner(refund.build_graph()).start("Refund", {"ticket_id": "T-9001"})
    assert r.status == "paused" and r.paused_on == "approve_refund"
    assert "£257.99" in r.pause_prompt
    assert r.node_status["issue_refund"] == "pending"


def test_resume_replays_finished_nodes_for_free(tmp_path, registry):
    s = _refund_stack(tmp_path, registry)
    first = s.runner(refund.build_graph()).start("Refund", {"ticket_id": "T-9001"})
    assert s.cost()["calls"] == 1, "the drafting node made one model call"

    s2 = _refund_stack(tmp_path, registry)
    second = s2.runner(refund.build_graph()).resume(
        first.run_id, decisions={"approve_refund": {"approved": True}})
    assert second.status == "completed"
    assert "draft_reply" in second.replayed
    assert s2.cost()["calls"] == 0, "a replayed node must not re-spend"


def test_the_customer_is_refunded_once_across_three_executions(tmp_path, registry):
    s = _refund_stack(tmp_path, registry)
    first = s.runner(refund.build_graph()).start("Refund", {"ticket_id": "T-9001"})
    for _ in range(2):
        st = _refund_stack(tmp_path, registry)
        st.runner(refund.build_graph()).resume(
            first.run_id, decisions={"approve_refund": {"approved": True}})

    final = _refund_stack(tmp_path, registry)
    assert_effects_fire_once(final, first.run_id, expected=2)  # refund + email
    kinds = [e["node_id"] for e in final.store.effects(first.run_id)]
    assert kinds.count("issue_refund") == 1
    assert kinds.count("send_reply") == 1


def test_a_wiped_checkpoint_still_cannot_double_refund(tmp_path, registry):
    """The nastiest window: money moved externally, our state write did not."""
    s = _refund_stack(tmp_path, registry)
    first = s.runner(refund.build_graph()).start("Refund", {"ticket_id": "T-9001"})
    s2 = _refund_stack(tmp_path, registry)
    s2.runner(refund.build_graph()).resume(
        first.run_id, decisions={"approve_refund": {"approved": True}})

    store = _refund_stack(tmp_path, registry).store
    with store._c() as conn:  # noqa: SLF001 - white-box on purpose
        conn.execute("DELETE FROM node_runs WHERE run_id=? AND node_id='issue_refund'",
                     (first.run_id,))

    s3 = _refund_stack(tmp_path, registry)
    s3.runner(refund.build_graph()).resume(
        first.run_id, decisions={"approve_refund": {"approved": True}})
    kinds = [e["node_id"] for e in s3.store.effects(first.run_id)]
    assert kinds.count("issue_refund") == 1


def test_the_idempotency_key_is_facts_not_a_clock(tmp_path, registry):
    s = _refund_stack(tmp_path, registry)
    first = s.runner(refund.build_graph()).start("Refund", {"ticket_id": "T-9001"})
    s2 = _refund_stack(tmp_path, registry)
    s2.runner(refund.build_graph()).resume(
        first.run_id, decisions={"approve_refund": {"approved": True}})

    key = next(e["key"] for e in s2.store.effects(first.run_id)
               if e["node_id"] == "issue_refund")
    assert key == idempotency_key("refund", order="A-1207", amount=257.99, fault="seller")


def test_a_key_with_no_facts_is_refused():
    with pytest.raises(ValueError, match="uuid wearing a costume"):
        idempotency_key("refund")


def test_a_failed_node_blocks_its_dependents(tmp_path, registry):
    def boom(_s):
        raise RuntimeError("upstream exploded")

    g = (Graph("g", seeds=("x",))
         .add(Step(id="a", fn=boom, provides=("y",)))
         .add(Step(id="b", fn=lambda s: 1, depends_on=("a",), requires=("y",))))
    r = _refund_stack(tmp_path, registry).runner(g).start("g", {"x": 1})
    assert r.status == "failed" and r.failed_on == "a"
    assert r.node_status["b"] == "blocked"


def test_retries_are_bounded_and_recorded(tmp_path, registry):
    attempts = {"n": 0}

    def flaky(_s):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TimeoutError("try again")
        return {"y": attempts["n"]}

    g = Graph("g").add(Step(id="a", fn=flaky, provides=("y",),
                            retry=RetryPolicy(max_attempts=3, backoff_s=0.0)))
    s = _refund_stack(tmp_path, registry)
    r = s.runner(g).start("g")
    assert r.ok and r.state.get("y") == 3
    assert sum(1 for sp in s.store.spans(r.run_id) if sp["kind"] == "error") == 2


def test_compensation_unwinds_completed_side_effects(tmp_path, registry):
    undone = []
    g = (Graph("g")
         .add(Step(id="a", fn=lambda s: {"y": 1}, provides=("y",),
                   idempotency=lambda st: "a:once",
                   compensate=lambda st, out: undone.append(out)))
         .add(Step(id="b", fn=lambda s: 1 / 0, depends_on=("a",))))
    r = _refund_stack(tmp_path, registry).runner(g).start("g")
    assert r.status == "failed" and undone == [{"y": 1}]


def test_compensation_without_an_idempotency_key_fails_at_build_time(tmp_path, registry):
    """You cannot undo an effect the graph cannot tell whether it made.

    Compensation says "this landed, unwind it". Without a key there is no record
    that it landed, so on resume the graph can neither make it once nor safely
    undo it. That is the duplicate custodian instruction; it is a build error.
    """
    g = Graph("g").add(Step(id="a", fn=lambda s: {"y": 1}, provides=("y",),
                            compensate=lambda st, out: None))
    with pytest.raises(GraphError, match="declares `compensate` but no `idempotency`"):
        g.validate()


# --- conditional routing --------------------------------------------------


def _invoice_run(pol, tmp_path, registry, name):
    s = build_test_stack(pol, tools=registry, state_dir=tmp_path / name)
    return s, s.runner(invoice.build_graph(s.harness)).start("Intake", {})


def test_a_clean_invoice_posts_and_skips_escalation(tmp_path, registry):
    s, r = _invoice_run(invoice_policy.good_policy, tmp_path, registry, "ok")
    assert r.ok
    assert r.node_status["post_to_ledger"] == "done"
    assert r.node_status["escalate"] == "skipped"
    assert r.state.get("posted")["status"] == "posted_to_accounts_payable"


def test_a_fabricated_citation_routes_to_escalation(tmp_path, registry):
    """The verifier approved. The substring check disagreed. It loses."""
    s, r = _invoice_run(invoice_policy.fabricating_policy, tmp_path, registry, "fab")
    assert r.node_status["post_to_ledger"] == "skipped"
    assert r.node_status["escalate"] == "done"
    assert "independently_verified" in (r.state.get("gate_report") or {})["failed"]


def test_a_transposed_digit_is_caught_by_arithmetic(tmp_path, registry):
    """Every field present, every type right, evidence quote REAL — and wrong.

    No JSON schema catches this. One line of arithmetic does.
    """
    s, r = _invoice_run(invoice_policy.transposed_policy, tmp_path, registry, "bad")
    report = r.state.get("gate_report") or {}
    assert report["failed"] == ["lines_sum_to_total"]
    assert r.node_status["post_to_ledger"] == "skipped"


def test_fan_out_produces_one_result_per_item_in_order(tmp_path, registry):
    s, r = _invoice_run(invoice_policy.good_policy, tmp_path, registry, "map")
    children = r.state.outputs["extract"]
    assert [c["id"] for c in children] == ["header", "lines"]
    assert children[0]["model"] == "claude-sonnet-5"   # reconciliation
    assert children[1]["model"] == "claude-haiku-4-5"  # transcription


def test_posting_an_invoice_twice_is_impossible(tmp_path, registry):
    s, r = _invoice_run(invoice_policy.good_policy, tmp_path, registry, "once")
    s.runner(invoice.build_graph(s.harness)).resume(r.run_id)
    assert_effects_fire_once(s, r.run_id, expected=1)


# --- the window the ledger exists for ------------------------------------
#
# These four are the ones that would have caught the guarantee being stated
# more strongly than the code delivered it. Every earlier idempotency test is a
# sequential re-run, and a sequential re-run cannot observe the interval
# between "we are about to act" and "we acted".


def _effect_graph(fired: list[str], inner=None):
    """One node with a side effect, and an optional hook fired mid-effect."""
    def fn(state):
        if inner is not None:
            inner()
        fired.append("fired")
        return {"receipt": f"R-{len(fired)}"}

    return Graph("effects").add(Step(
        id="pay", fn=fn, provides=("receipt",),
        idempotency=lambda s: idempotency_key("pay", order="A-1"),
    )).validate()


def test_a_second_worker_inside_the_window_cannot_fire_the_same_effect(tmp_path, registry):
    """The race, made deterministic by nesting it.

    The second worker runs *while* the first is inside the side effect — the
    exact interval a check-then-act ledger cannot see, because the row it would
    check is only written afterwards. Claiming the key first is what makes the
    primary key decide this, once.
    """
    fired: list[str] = []
    second: dict[str, object] = {}
    entered = {"once": False}

    def inner():
        if entered["once"]:
            return
        entered["once"] = True
        other = _refund_stack(tmp_path, registry)
        second["result"] = other.runner(_effect_graph(fired)).start(
            "pay", {}, run_id="gr-race")

    s = _refund_stack(tmp_path, registry)
    first = s.runner(_effect_graph(fired, inner)).start("pay", {}, run_id="gr-race")

    assert fired == ["fired"], "the second worker fired the same effect again"
    assert first.status == "completed"
    assert second["result"].status == "failed"
    assert "never recorded" in second["result"].error


def test_an_effect_claimed_but_never_recorded_stops_the_run(tmp_path, registry):
    """The process died between the custodian accepting and the ledger learning.

    The old ledger had no way to represent this state, so a resume re-fired.
    A claim that outlives its process is now a fact on disk, and the run stops
    on it rather than guessing.
    """
    s = _refund_stack(tmp_path, registry)
    assert s.store.claim_effect("gr-dead", idempotency_key("pay", order="A-1"), "pay") is None

    fired: list[str] = []
    result = s.runner(_effect_graph(fired)).start("pay", {}, run_id="gr-dead")

    assert fired == [], "an action of unknown outcome must not be repeated"
    assert result.status == "failed"
    assert "outcome is unknown" in result.error
    assert s.store.effects("gr-dead") == [], "an unresolved claim is not a result"


def test_a_reported_failure_releases_the_claim_so_a_retry_can_run(tmp_path, registry):
    """The asymmetry that makes the refusal above tolerable.

    An action that *said* it failed did not happen, so its claim is released and
    a later run may try again. An action that said nothing keeps its claim. The
    difference between the two is the whole guarantee.
    """
    calls: list[int] = []

    def fn(state):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("declined by the processor")
        return {"receipt": "R-2"}

    def graph():
        return Graph("effects").add(Step(
            id="pay", fn=fn, provides=("receipt",),
            idempotency=lambda s: idempotency_key("pay", order="A-2"),
        )).validate()

    s = _refund_stack(tmp_path, registry)
    first = s.runner(graph()).start("pay", {}, run_id="gr-retry")
    assert first.status == "failed"

    again = _refund_stack(tmp_path, registry)
    second = again.runner(graph()).start("pay", {}, run_id="gr-retry")
    assert second.status == "completed"
    assert calls == [1, 1]
    assert [e["node_id"] for e in again.store.effects("gr-retry")] == ["pay"]


def test_the_ledger_distinguishes_claimed_from_recorded(tmp_path, registry):
    s = _refund_stack(tmp_path, registry)
    store, key = s.store, idempotency_key("pay", order="A-3")

    assert store.claim_effect("gr-1", key, "pay") is None
    held = store.claim_effect("gr-1", key, "pay")
    assert held is not None and held["status"] == "claimed" and held["result"] is None

    store.record_effect("gr-1", key, "pay", {"receipt": "R-1"})
    done = store.claim_effect("gr-1", key, "pay")
    assert done is not None and done["status"] == "recorded"
    assert done["result"] == {"receipt": "R-1"}
