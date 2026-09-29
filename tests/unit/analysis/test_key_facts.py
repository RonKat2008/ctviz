"""Fix G (§8.4): the NCT fast path's table of key facts, every cell cited by its exact pointer."""

from ctviz.analysis.aggregate import MatchedTrial
from ctviz.analysis.key_facts import KEY_FACT_COLUMNS, column_of, key_facts
from ctviz.citations import pointer as p
from ctviz.citations.pointer import json_text, resolve_pointer
from ctviz.ctgov.normalize import normalize
from ctviz.viz.builder import build_key_facts_table
from tests.factories import make_study

FULL = make_study(
    "NCT04368728",
    phases=["PHASE2", "PHASE3"],
    start="2020-04-29",
    completion="2023-02-10",
    status="COMPLETED",
    sponsor="BioNTech SE",
    conditions=["COVID-19", "SARS-CoV-2 Infection"],
    interventions=[{"type": "BIOLOGICAL", "name": "BNT162b2"}, {"type": "OTHER", "name": "Saline"}],
    enrollment=47079,
    enrollment_type="ACTUAL",
)


def _matched(*studies: dict) -> list[MatchedTrial]:
    return [MatchedTrial(normalize(s), ()) for s in studies]


def test_one_row_per_trial_whose_predicate_is_its_own_nct_id() -> None:
    result = key_facts(_matched(FULL, make_study("NCT00000002")))

    assert [b.key for b in result.buckets] == ["NCT04368728", "NCT00000002"]
    first = result.buckets[0]
    assert first.predicate == {"op": "equals", "path": p.NCT_ID, "value": "NCT04368728"}
    assert first.trial_count == 1
    [citation] = first.citations
    assert (citation.field, citation.excerpt) == (p.NCT_ID, "NCT04368728")
    assert result.excluded == {}


def test_every_fact_cell_is_cited_by_its_exact_raw_pointer_and_text() -> None:
    [bucket] = key_facts(_matched(FULL)).buckets
    [citation] = bucket.citations

    facts = [e for e in citation.evidence if e.role == "context"]
    assert {column_of(e.field) for e in facts} == set(KEY_FACT_COLUMNS)
    for e in facts:
        assert json_text(resolve_pointer(FULL, e.field)) == e.excerpt


def test_absent_fields_are_simply_not_cited() -> None:
    [bucket] = key_facts(_matched(make_study("NCT00000002"))).buckets

    columns = {column_of(e.field) for e in bucket.citations[0].evidence}
    assert "enrollment" not in columns and "conditions" not in columns
    assert {"title", "overall_status", "study_type", "lead_sponsor"} <= columns


def test_key_facts_table_rows_show_each_cell_with_its_pointers() -> None:
    table = build_key_facts_table("Key facts: NCT04368728", key_facts(_matched(FULL)))

    [row] = table.data
    assert row["nct_id"] == "NCT04368728" and row["trial_count"] == 1
    assert row["enrollment"] == "47079" and row["enrollment_type"] == "ACTUAL"
    assert row["phases"] == ["PHASE2", "PHASE3"]
    assert row["interventions"] == ["BNT162b2", "Saline"]
    assert row["cell_fields"]["enrollment"] == [p.ENROLLMENT_COUNT]
    assert row["cell_fields"]["interventions"] == [
        f"{p.INTERVENTIONS}/0/name",
        f"{p.INTERVENTIONS}/1/name",
    ]
    assert set(table.encoding) == {"nct_id", *KEY_FACT_COLUMNS}
