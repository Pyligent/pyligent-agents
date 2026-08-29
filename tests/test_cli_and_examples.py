"""The CLI, the scaffold and the example runner.

Teaching material that silently rots is worse than none: the first thing a new
contributor does is run it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV = {"PYTHONPATH": f"{ROOT / 'src'}:{ROOT / 'examples'}", "PATH": "/usr/bin:/bin",
       "PYLIGENT_AGENTS_BACKEND": "scripted"}


# `text=True` alone decodes a child's output with the machine's ANSI codepage —
# cp1252 on most Windows installs. These children deliberately emit UTF-8 (see
# evidencecheck.console), so the parent must be told to read UTF-8 rather than
# guess from the locale. Without this the fix to the child's *output* simply moves
# the UnicodeError into the test harness's *input*.
TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


def _cli(*args, cwd=None, env=None):
    return subprocess.run([sys.executable, "-m", "pyligent_agents", *args], capture_output=True,
                          cwd=cwd or ROOT, env={**ENV, **(env or {})}, timeout=120, **TEXT)


def _example(*args, cwd=None):
    return subprocess.run([sys.executable, str(ROOT / "examples" / "run.py"), *args],
                          capture_output=True, cwd=cwd or ROOT,
                          env={**ENV, "HOME": str(cwd or ROOT)}, timeout=240, **TEXT)


# --- the library CLI ------------------------------------------------------


def test_steps_lists_ten_steps_and_the_four_questions():
    out = _cli("steps").stdout
    assert "10. Prove each guardrail" in out
    assert "What is the stop condition?" in out


def test_doctor_reports_pricing_and_governors():
    out = _cli("doctor").stdout
    assert "claude-opus-5" in out and "priced" in out
    assert "governors" in out


def test_doctor_flags_an_unpriced_model():
    out = _cli("doctor", env={"PYLIGENT_AGENTS_WORKER_MODEL": "some-model-we-never-heard-of"}).stdout
    assert "UNPRICED" in out
    assert "register_model" in out


def test_graph_can_be_inspected_without_running_it():
    out = _cli("graph", "show", "level3_refund_workflow.app:build_graph").stdout
    assert "issue_refund" in out and "idempotent" in out
    assert "graph TD" in _cli("graph", "mermaid",
                              "level3_refund_workflow.app:build_graph").stdout


def test_new_scaffolds_a_project_whose_tests_pass(tmp_path):
    target = tmp_path / "orderbot"
    assert _cli("new", str(target)).returncode == 0
    assert (target / "orderbot.py").exists()
    assert (target / "tests" / "test_orderbot.py").exists()
    assert "Who verifies" in (target / "README.md").read_text(encoding="utf-8")

    # PATH and PYTHONPATH are platform-shaped: os.pathsep is ";" on Windows, and
    # "/usr/bin:/bin" names nothing there. Inherit PATH rather than inventing one.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], capture_output=True, cwd=target,
        env={"PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(target)]),
             "PATH": os.environ.get("PATH", "")},
        timeout=120, **TEXT)
    assert result.returncode == 0, result.stdout[-1500:]


def test_new_refuses_to_overwrite(tmp_path):
    target = tmp_path / "taken"
    target.mkdir()
    (target / "x.py").write_text("keep me", encoding="utf-8")
    assert _cli("new", str(target)).returncode == 2


# --- the example applications ---------------------------------------------


@pytest.mark.parametrize("layer", ["harness", "loop", "graph", "ladder"])
def test_each_layer_demo_runs_clean(layer, tmp_path):
    r = _example("demo", layer, cwd=tmp_path)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "Traceback" not in r.stderr


def test_the_harness_demo_actually_demonstrates_the_mechanisms(tmp_path):
    out = _example("demo", "harness", cwd=tmp_path).stdout
    assert "results offloaded : 7" in out
    assert "compactions       : 3" in out, "the compaction demo must actually compact"
    assert "denied=True" in out


def test_the_graph_demo_proves_the_idempotency_guarantee(tmp_path):
    """The demo asserts it internally; a regression fails the process."""
    out = _example("demo", "graph", cwd=tmp_path).stdout
    assert "workflow executions      : 3" in out
    assert "refunds actually issued  : 1" in out


def test_triage_classifies_the_seeded_inbox(tmp_path):
    out = _example("triage", cwd=tmp_path).stdout
    for row in ("late_delivery", "return_request", "missing_order", "acknowledgement"):
        assert row in out
    assert "fallback_used=True" in out


def test_the_order_agent_answers_from_tools(tmp_path):
    assert "£257.99" in _example("order-agent", cwd=tmp_path).stdout


def test_refund_pauses_and_resume_completes_once(tmp_path):
    started = _example("refund", cwd=tmp_path).stdout
    assert "PAUSED" in started
    run_id = next(w for w in started.split() if w.startswith("gr_"))

    resumed = _example("resume", run_id, "--approve", cwd=tmp_path).stdout
    assert "status=completed" in resumed
    assert "model calls in this attempt: 0" in resumed
    assert resumed.count("refund:amount=") == 1


def test_a_clean_invoice_posts(tmp_path):
    r = _example("invoice", cwd=tmp_path)
    assert r.returncode == 0
    assert "posted_to_accounts_payable" in r.stdout


def test_a_fabricated_citation_is_caught_from_the_cli(tmp_path):
    r = _example("invoice", "--fabricate", cwd=tmp_path)
    assert r.returncode == 1, "a rejected artifact must be a non-zero exit"
    assert "[FAIL] independently_verified" in r.stdout


def test_a_transposed_digit_is_caught_from_the_cli(tmp_path):
    r = _example("invoice", "--transposed", cwd=tmp_path)
    assert r.returncode == 1
    assert "[FAIL] lines_sum_to_total" in r.stdout
    assert "transposed digit" in r.stdout


# --- the testing helpers themselves ---------------------------------------


def test_assert_capped_rejects_an_agent_that_finishes(registry):
    from level2_order_agent import app

    from pyligent_agents.testing import assert_capped, build_test_stack, turn

    stack = build_test_stack(lambda c: turn("Done, nothing to compute."), tools=registry)
    with pytest.raises(AssertionError, match="not a runaway test"):
        assert_capped(lambda: app.build(stack.harness).run("hello"))


def test_assert_effects_fire_once_reports_the_keys(tmp_path, registry):
    from pyligent_agents.testing import assert_effects_fire_once, build_test_stack

    stack = build_test_stack(lambda c: None, tools=registry, state_dir=tmp_path)
    stack.store.save_run("r1", "g", "completed", {"run_id": "r1", "goal": ""})
    stack.store.record_effect("r1", "k1", "n", {})
    stack.store.record_effect("r1", "k2", "n", {})
    with pytest.raises(AssertionError, match="k1"):
        assert_effects_fire_once(stack, "r1")


@pytest.mark.parametrize("entry", [
    ["examples/run.py", "demo", "all"],
    ["bench/run.py", "--corpus", "bench/corpus-synthetic"],
    ["evals/run_evals.py", "--system", "faithful", "--check"],
])
def test_entry_points_survive_a_legacy_codepage(entry):
    """Windows picks cp1252 for stdout, and cp1252 cannot encode `─`, `✓` or `→`.

    Every report here prints at least one of them, so before `use_utf8_stdout` the
    first line of output raised UnicodeEncodeError and the process died with a
    traceback instead of a report. Eleven tests failed this way on
    windows-latest, and a Windows user running `evidence-check` got the same
    thing: no findings, just `charmap` in a stack trace.

    PYTHONIOENCODING reproduces that on any platform, which is why this test can
    guard the fix from Linux and macOS as well.
    """
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run(
        [sys.executable, *entry], cwd=ROOT, env=env,
        capture_output=True, **TEXT,
    )
    assert "UnicodeEncodeError" not in result.stderr, result.stderr[-600:]
    assert result.returncode == 0, result.stderr[-600:]


def test_no_file_io_relies_on_the_machines_locale():
    """`Path.read_text()` with no encoding decodes with the platform's ANSI codepage.

    On Windows that is cp1252, which cannot decode the UTF-8 these tools read and
    write — extraction JSON full of curly quotes, memory notes quoting legal text,
    a scaffolded README with an em dash. JSON is UTF-8 by specification, so reading
    it through the locale is wrong everywhere; Windows is merely where it raises.

    This is checked structurally rather than by running under every locale, because
    the failure is silent on a UTF-8 machine and only appears on someone else's.
    """
    import ast

    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in {".venv", "node_modules", ".git"} or part.startswith("corpus")
               for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"read_text", "write_text"}
                    and not any(kw.arg == "encoding" for kw in node.keywords)):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert not offenders, (
        "these calls decode/encode with the machine's locale and will fail on a "
        "non-UTF-8 platform:\n  " + "\n  ".join(offenders)
    )
