"""Layer 3 — the graph owns coordination: validation, resume, idempotency."""

from __future__ import annotations

import pytest

from trellis import idempotency_key
from trellis.core.errors import GraphError
from trellis.graph import Graph, GraphState, RetryPolicy, Step
from trellis.testing import assert_effects_fire_once, build_test_stack

from level3_refund_workflow import app as refund, policy as refund_policy
from level4_invoice_intake import app as invoice, policy as invoice_policy


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
                   compensate=lambda st, out: undone.append(out)))
         .add(Step(id="b", fn=lambda s: 1 / 0, depends_on=("a",))))
    r = _refund_stack(tmp_path, registry).runner(g).start("g")
    assert r.status == "failed" and undone == [{"y": 1}]


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
