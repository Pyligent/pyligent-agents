"""Cross-run memory, and the failure it exists to prevent.

Memory outlives every control around it. A note written from an agreement that
has since been amended is not merely unhelpful — it is confidently wrong, and
it suppresses the lookup that would have corrected it. The absence of a fact
prompts a search; a wrong fact prevents one.

The module had no tests at all before this, which for the one component that
persists across runs was the wrong place to have none.
"""

from __future__ import annotations

import pytest

from pyligent_agents.harness.memory import (
    Binding,
    Freshness,
    MemoryStore,
    Note,
    content_hash,
)
from pyligent_agents.verify import memory_is_current

CSA_V1 = 'Paragraph 11(b)(i) "Threshold" means with respect to each party: USD 5,000,000.'
CSA_V2 = 'Paragraph 11(b)(i) "Threshold" means with respect to each party: USD 0.'


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory")


def seed(store, *, source: str = CSA_V1):
    store.write("atlas-threshold", "ATLAS Threshold is USD 5,000,000.",
                why="Avoids re-reading Paragraph 11 on every margin call.",
                derived_from=[Binding.of("DOC-CSA-ATLAS", source)],
                run_id="run-1")
    store.write("atlas-contact", "The collateral desk prefers email.",
                why="Replies same day; phone goes to a queue.")
    return {"DOC-CSA-ATLAS": content_hash(source)}


# --- the failure this exists to prevent ----------------------------------


def test_a_note_goes_stale_when_its_source_changes(store):
    """The whole point.

    Run 1 reads the CSA and remembers the Threshold. The parties adhere to the
    VM protocol and it becomes zero. Run 9 must not recall the old number.
    """
    seed(store)
    after = {"DOC-CSA-ATLAS": content_hash(CSA_V2)}

    recalled = store.recall("atlas threshold", sources=after)
    assert "atlas-threshold" not in [r.note.name for r in recalled]

    stale = store.stale(after)
    assert [r.note.name for r in stale] == ["atlas-threshold"]


def test_the_same_note_is_returned_while_the_source_is_unchanged(store):
    now = seed(store)
    r = store.recall("atlas threshold", sources=now)
    assert r[0].note.name == "atlas-threshold"
    assert r[0].freshness is Freshness.FRESH and r[0].usable


def test_a_note_bound_to_nothing_survives_a_document_change(store):
    """General knowledge is not invalidated by an amendment to an agreement."""
    seed(store)
    names = [r.note.name for r in
             store.recall("collateral desk email",
                          sources={"DOC-CSA-ATLAS": content_hash(CSA_V2)})]
    assert names == ["atlas-contact"]


# --- the third state, which is the one usually missing --------------------


def test_a_note_whose_source_was_not_supplied_abstains_rather_than_guessing(store):
    """UNVERIFIED is not FRESH and not STALE.

    Same discipline as a gate: a control that answers when it cannot tell
    answers wrongly in whichever direction its default happens to fall.
    """
    seed(store)
    # The unbound note still matches "atlas" and is still usable; the bound one
    # is withheld because nothing vouched for its source.
    names = [r.note.name for r in store.recall("atlas threshold", sources={})]
    assert "atlas-threshold" not in names and "atlas-contact" in names

    unusable = {r.note.name: r for r in
                store.recall("atlas threshold", sources={}, include_unusable=True)}
    assert unusable["atlas-threshold"].freshness is Freshness.UNVERIFIED
    assert not unusable["atlas-threshold"].usable


def test_a_source_map_that_omits_this_note_is_also_unverified(store):
    """Supplying *some* sources does not vouch for the ones you left out."""
    seed(store)
    r = store.recall("atlas threshold", sources={"DOC-OTHER": "abc"},
                     include_unusable=True)
    assert r[0].freshness is Freshness.UNVERIFIED


@pytest.mark.parametrize("strict,expected", [(False, ["atlas-contact"]), (True, [])])
def test_strict_mode_withholds_notes_with_no_provenance(store, strict, expected):
    """For a run where nothing unprovenanced may influence a decision."""
    seed(store)
    names = [r.note.name for r in
             store.recall("collateral desk email", sources={}, strict=strict)]
    assert names == expected


# --- identity, revision, deletion ----------------------------------------


def test_a_source_is_identified_by_content_not_by_name(store):
    """Two copies of an agreement under different filenames are one source."""
    a = Binding.of("DOC-A", CSA_V1)
    b = Binding.of("DOC-B", CSA_V1)
    assert a.sha256 == b.sha256 and a.ref != b.ref


def test_writing_the_same_note_updates_rather_than_appends(store):
    store.write("k", "first", why="w")
    note = store.write("k", "second", why="w")
    assert note.revisions == 2 and note.body == "second"
    assert store.read("k").body == "second"
    assert len(list((store.root).glob("*.json"))) == 1


def test_created_at_survives_a_revision(store):
    first = store.write("k", "first")
    second = store.write("k", "second")
    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at


def test_forgetting_a_wrong_note_is_maintenance(store):
    store.write("k", "wrong")
    assert store.forget("k") is True
    assert store.read("k") is None and store.forget("k") is False


def test_a_corrupt_note_does_not_take_the_others_with_it(store):
    seed(store)
    (store.root / "broken.json").write_text("{not json")
    assert len(store.recall("atlas", sources={}, include_unusable=True)) == 2


# --- injection is budgeted -----------------------------------------------


def test_injection_is_capped(store):
    """Memory that grows without a budget is a context leak with a good name."""
    for i in range(60):
        store.write(f"note-{i}", f"fact number {i} about collateral " + "x" * 200)
    text = store.inject("collateral", sources={}, budget_chars=400)
    assert len(text) <= 400 + 200        # allowance for the header lines
    assert text.count("\n- ") <= 4


def test_withheld_notes_are_counted_not_hidden(store):
    """A prompt that silently drops half of what it recalled is worse than one
    that says so."""
    seed(store)
    text = store.inject("atlas threshold",
                        sources={"DOC-CSA-ATLAS": content_hash(CSA_V2)})
    assert "withheld" in text and "the source has changed" in text
    assert "5,000,000" not in text


def test_injected_text_frames_memory_as_a_claim_not_a_fact(store):
    seed(store)
    text = store.inject("atlas", sources={"DOC-CSA-ATLAS": content_hash(CSA_V1)})
    assert "may check" in text and "not a fact you must accept" in text


def test_nothing_recalled_produces_no_text(store):
    assert store.inject("nothing matches this", sources={}) == ""


# --- what the audit trail sees -------------------------------------------


def test_the_store_records_which_notes_were_actually_used(store):
    now = seed(store)
    store.recall("atlas threshold", sources=now)
    assert "atlas-threshold" in store.used


def test_a_withheld_note_is_not_recorded_as_used(store):
    seed(store)
    store.recall("atlas threshold", sources={"DOC-CSA-ATLAS": content_hash(CSA_V2)})
    assert "atlas-threshold" not in store.used


def test_the_harness_reports_what_memory_a_run_leaned_on(tmp_path):
    from pyligent_agents.testing import build_test_stack, turn

    stack = build_test_stack(lambda call: turn("done"), state_dir=tmp_path)
    now = seed(stack.harness.memory)
    stack.harness.recall("atlas threshold", sources=now)
    assert "atlas-threshold" in stack.harness.report()["memory_used"]


# --- the gate ------------------------------------------------------------


def test_a_stale_memory_blocks_the_artifact():
    passed, msg = memory_is_current()(
        {"_memory": {"used": ["atlas-threshold"], "stale": ["atlas-threshold"]}})
    assert not passed
    assert "has since changed" in msg and "Re-read the source" in msg


def test_current_memory_passes_and_says_how_much_was_used():
    passed, msg = memory_is_current()({"_memory": {"used": ["a", "b"], "stale": []}})
    assert passed and "2 remembered fact(s)" in msg


def test_an_artifact_that_consulted_no_memory_is_not_penalised():
    passed, msg = memory_is_current()({"fields": {}})
    assert passed and "no memory" in msg


# --- serialisation --------------------------------------------------------


def test_a_note_round_trips_with_its_provenance(store):
    seed(store)
    note = store.read("atlas-threshold")
    assert Note.from_dict(note.to_dict()) == note
    assert note.derived_from[0].ref == "DOC-CSA-ATLAS"
    assert note.why.startswith("Avoids re-reading")


def test_notes_written_before_provenance_existed_still_load(store):
    """Backward compatibility: an old note is UNBOUND, not broken."""
    import json
    (store.root / "legacy.json").write_text(json.dumps(
        {"name": "legacy", "kind": "observation", "body": "an older fact",
         "tags": [], "created_at": 1.0, "updated_at": 1.0, "revisions": 1}))
    r = store.recall("older fact", sources={})
    assert r[0].freshness is Freshness.UNBOUND and r[0].usable
