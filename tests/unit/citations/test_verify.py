"""Independent verifier (§11.6, §11.9): a clean pipeline response passes, and each deliberate
corruption -- changing exactly one thing -- is caught by the SPECIFIC check meant to catch it."""

import asyncio
import subprocess
import sys
from dataclasses import replace
from datetime import date

import httpx
import respx

from ctviz.agent.planner import Planner
from ctviz.citations.verify import verify_response
from ctviz.config import CTGOV_BASE_URL
from ctviz.ctgov.client import CtGovClient
from ctviz.pipeline import run_pipeline
from ctviz.schemas.citations import Evidence
from ctviz.schemas.request import VisualizeRequest
from ctviz.schemas.response import ExcludedTrial, VisualizeResponse
from tests.factories import make_plan, make_study
from tests.unit.agent.test_planner import FakeBackend
from tests.unit.citations.conftest import (
    PEMBRO,
    build_small_response,
    replace_row,
    row,
    run_small,
    single_cohort_populations,
    tags_of,
    violations_of,
    with_rows,
)

PHASES = "/protocolSection/designModule/phases"
FAST_PATH_TODAY = date(2026, 9, 28)


def _run_fast_path_lookup(study: dict) -> VisualizeResponse:
    """A real pipeline run of the §8.4 NCT fast path (no planner/judge call): one key-facts
    table row, every cell cited."""

    nct_id = study["protocolSection"]["identificationModule"]["nctId"]

    async def _run() -> VisualizeResponse:
        async with CtGovClient() as client:
            return await run_pipeline(
                VisualizeRequest(query=f"How many participants in {nct_id}?"),
                planner=Planner(FakeBackend(make_plan())),
                client=client,
                today=FAST_PATH_TODAY,
            )

    with respx.mock:
        respx.get(f"{CTGOV_BASE_URL}/studies").mock(
            return_value=httpx.Response(200, json={"totalCount": 1, "studies": [study]})
        )
        return asyncio.run(_run())


def test_verifier_never_imports_the_counting_code() -> None:
    """Import the verifier in a fresh interpreter: no `ctviz.analysis` module may be loaded."""
    probe = (
        "import sys, ctviz.citations.verify; "
        "bad = [m for m in sys.modules if m.startswith('ctviz.analysis')]; "
        "assert not bad, bad"
    )

    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_verifier_accepts_a_clean_pipeline_response() -> None:
    response, raw_by_id, populations = build_small_response()

    check = verify_response(response, raw_by_id, populations)

    assert check.passed is True
    assert check.recount_ok is True
    assert check.citations_checked == 3  # 1 Phase 1 + 2 Phase 3
    assert check.ms >= 0


def test_wrong_pointer_is_a_pointer_violation() -> None:
    response, raw_by_id, populations = build_small_response()
    c0, c1 = row(response, "Phase 3")["citations"]
    bad = c0.model_copy(update={"field": f"{PHASES}/7"})  # still under the predicate's path

    violations = violations_of(
        replace_row(response, "Phase 3", citations=[bad, c1]), raw_by_id, populations
    )

    assert tags_of(violations) == {"pointer"}
    assert "does not resolve" in violations[0] and c0.nct_id in violations[0]


def test_altered_excerpt_is_a_pointer_violation() -> None:
    response, raw_by_id, populations = build_small_response()
    c0, c1 = row(response, "Phase 3")["citations"]
    bad = c0.model_copy(update={"excerpt": "PHASE1"})

    violations = violations_of(
        replace_row(response, "Phase 3", citations=[bad, c1]), raw_by_id, populations
    )

    assert tags_of(violations) == {"pointer"}
    assert "cited as 'PHASE1'" in violations[0]


def test_bucket_swap_is_a_soundness_violation() -> None:
    """A Phase 1 trial MOVED into the Phase 3 bar (counts kept consistent)."""
    response, raw_by_id, populations = build_small_response()
    [moved] = row(response, "Phase 1")["citations"]
    phase3 = row(response, "Phase 3")["citations"]
    swapped = replace_row(response, "Phase 3", citations=[*phase3, moved], trial_count=3)
    swapped = replace_row(swapped, "Phase 1", citations=[], trial_count=0)

    violations = violations_of(swapped, raw_by_id, populations)

    assert f"soundness: bar_chart[1] Phase 3: {moved.nct_id} does not satisfy" in "\n".join(
        violations
    )


def test_dropped_citation_is_a_completeness_violation() -> None:
    response, raw_by_id, populations = build_small_response()
    c0, c1 = row(response, "Phase 3")["citations"]

    violations = violations_of(
        replace_row(response, "Phase 3", citations=[c0], trial_count=1), raw_by_id, populations
    )

    assert any(v.startswith("completeness:") and c1.nct_id in v for v in violations)


def test_duplicate_citation_is_a_count_violation_only() -> None:
    """[c0, c1, c0] with trial_count left at 2: same witness set, one duplicated citation."""
    response, raw_by_id, populations = build_small_response()
    c0, c1 = row(response, "Phase 3")["citations"]

    violations = violations_of(
        replace_row(response, "Phase 3", citations=[c0, c1, c0]), raw_by_id, populations
    )

    assert tags_of(violations) == {"count"}
    assert any("distinct" in v for v in violations)


def test_trial_count_that_disagrees_with_its_citations_is_a_count_violation() -> None:
    response, raw_by_id, populations = build_small_response()

    violations = violations_of(
        replace_row(response, "Phase 3", trial_count=99), raw_by_id, populations
    )

    assert violations == ["count: bar_chart[1] Phase 3: trial_count=99 but 2 citations"]


def test_primary_field_outside_the_predicate_paths_is_a_relevance_violation() -> None:
    """The nctId resolves and matches its excerpt -- it just doesn't support 'Phase 3'."""
    response, raw_by_id, populations = build_small_response()
    c0, c1 = row(response, "Phase 3")["citations"]
    nct_field = "/protocolSection/identificationModule/nctId"
    off_topic = c0.model_copy(update={"field": nct_field, "excerpt": c0.nct_id})

    violations = violations_of(
        replace_row(response, "Phase 3", citations=[off_topic, c1]), raw_by_id, populations
    )

    assert tags_of(violations) == {"relevance"}


def test_bucket_evidence_item_outside_the_predicate_paths_is_a_relevance_violation() -> None:
    response, raw_by_id, populations = build_small_response()
    c0, c1 = row(response, "Phase 3")["citations"]
    stray = Evidence(
        role="bucket", field="/protocolSection/designModule/studyType", excerpt="INTERVENTIONAL"
    )
    bad = c0.model_copy(update={"evidence": [*c0.evidence, stray]})

    violations = violations_of(
        replace_row(response, "Phase 3", citations=[bad, c1]), raw_by_id, populations
    )

    assert tags_of(violations) == {"relevance"}


def test_context_evidence_is_not_held_to_the_relevance_check() -> None:
    response, raw_by_id, populations = build_small_response()
    c0, c1 = row(response, "Phase 3")["citations"]
    context = Evidence(
        role="context", field="/protocolSection/designModule/studyType", excerpt="INTERVENTIONAL"
    )
    annotated = c0.model_copy(update={"evidence": [*c0.evidence, context]})

    check = verify_response(
        replace_row(response, "Phase 3", citations=[annotated, c1]), raw_by_id, populations
    )

    assert check.passed is True


def test_records_plotted_that_disagrees_with_the_population_is_a_structure_violation() -> None:
    response, raw_by_id, populations = build_small_response()
    cohort = response.meta.cohorts[0].model_copy(update={"records_plotted": 2})
    meta = response.meta.model_copy(update={"cohorts": [cohort]})

    violations = violations_of(response.model_copy(update={"meta": meta}), raw_by_id, populations)

    assert tags_of(violations) == {"structure"}
    assert "records_plotted=2" in violations[0]


def test_an_exclusive_dimension_whose_buckets_overcount_is_a_structure_violation() -> None:
    """A second, identical Phase 3 row: every datum is individually sound and complete, but an
    exclusive dimension's bucket sum (5) now exceeds records_plotted (3)."""
    response, raw_by_id, populations = build_small_response()
    rows = [*response.visualization.data, {**row(response, "Phase 3"), "category": "Phase 3*"}]

    violations = violations_of(with_rows(response, rows), raw_by_id, populations)

    assert tags_of(violations) == {"structure"}
    assert "bucket sum 5 != records_plotted 3" in violations[0]


def test_trial_that_fails_the_match_rule_but_is_cited_is_a_soundness_violation() -> None:
    """A forged record that never mentions pembrolizumab, in the population AND the bar (as a
    pipeline that skipped strict match would produce): only `cohort.base_predicate` catches it."""
    response, raw_by_id, populations = build_small_response()
    forged = make_study("NCT00000099", phases=["PHASE3"])  # no interventions at all
    raw_by_id = {**raw_by_id, "NCT00000099": forged}
    [(label, population)] = populations.items()
    populations = {label: replace(population, kept_ids=population.kept_ids | {"NCT00000099"})}
    c0, c1 = row(response, "Phase 3")["citations"]
    forged_citation = c0.model_copy(update={"nct_id": "NCT00000099", "evidence": []})
    corrupted = replace_row(response, "Phase 3", citations=[c0, c1, forged_citation], trial_count=3)
    cohort = response.meta.cohorts[0].model_copy(
        update={"records_matched": 4, "records_plotted": 4}
    )
    coverage = response.meta.data_coverage.model_copy(
        update={"records_matched": 4, "records_plotted": 4}
    )
    meta = corrupted.meta.model_copy(update={"cohorts": [cohort], "data_coverage": coverage})

    violations = violations_of(corrupted.model_copy(update={"meta": meta}), raw_by_id, populations)

    assert tags_of(violations) == {"soundness"}
    assert "NCT00000099" in violations[0]


def test_trial_misplaced_into_the_non_interventional_bucket_is_a_soundness_violation() -> None:
    studies = [
        make_study("NCT00000001", phases=["PHASE3"], interventions=PEMBRO),
        make_study("NCT00000002", phases=None, study_type="OBSERVATIONAL", interventions=PEMBRO),
    ]
    response, raw_by_id, populations = run_small(make_plan(), studies, drug_name="Pembrolizumab")
    [phase3_citation] = row(response, "Phase 3")["citations"]
    non_interventional = row(response, "Non-interventional")["citations"]
    corrupted = replace_row(
        response,
        "Non-interventional",
        citations=[*non_interventional, phase3_citation],
        trial_count=2,
    )

    violations = violations_of(corrupted, raw_by_id, populations)

    assert any(
        v.startswith("soundness:") and "NCT00000001" in v and "Non-interventional" in v
        for v in violations
    )


def test_malformed_datum_predicate_is_a_predicate_violation_not_a_crash() -> None:
    response, raw_by_id, populations = build_small_response()
    bad = {"op": "normalizes_to", "path": PHASES, "value": "x", "fn": "no-such-fn"}

    violations = violations_of(
        replace_row(response, "Phase 3", predicate=bad), raw_by_id, populations
    )

    assert "predicate" in tags_of(violations)
    assert any("unknown normalizes_to fn" in v for v in violations)


def test_records_matched_that_disagrees_with_the_kept_population_is_a_structure_violation() -> None:
    response, raw_by_id, populations = build_small_response()
    cohort = response.meta.cohorts[0].model_copy(update={"records_matched": 4})
    meta = response.meta.model_copy(update={"cohorts": [cohort]})

    violations = violations_of(response.model_copy(update={"meta": meta}), raw_by_id, populations)

    assert tags_of(violations) == {"structure"}
    assert "records_matched=4" in violations[0]


def test_data_coverage_that_disagrees_with_the_declared_exclusions_is_a_structure_violation() -> (
    None
):
    response, raw_by_id, populations = build_small_response()
    phantom = ExcludedTrial(nct_id="NCT00000002", stage="analysis", reason="made_up")
    coverage = response.meta.data_coverage.model_copy(update={"excluded_trials": [phantom]})
    meta = response.meta.model_copy(update={"data_coverage": coverage})

    violations = violations_of(response.model_copy(update={"meta": meta}), raw_by_id, populations)

    assert violations == [
        "structure: data_coverage analysis exclusions differ from the declared exclusions"
    ]


def test_key_facts_table_with_untouched_cells_passes() -> None:
    """Baseline: a real fast-path key-facts table, forged nowhere, verifies clean."""
    study = make_study("NCT04368728", enrollment=47079, conditions=["COVID-19"], phases=["PHASE3"])
    response = _run_fast_path_lookup(study)
    raw_by_id = {"NCT04368728": study}
    populations = single_cohort_populations(response, raw_by_id)

    check = verify_response(response, raw_by_id, populations)

    assert check.passed is True


def test_forged_displayed_cell_is_a_display_violation() -> None:
    """The enrollment cell is forged (47079 -> 99999) while its citation excerpt is left
    untouched -- only a check on the DISPLAYED value (not just the excerpt) catches this."""
    study = make_study("NCT04368728", enrollment=47079, conditions=["COVID-19"], phases=["PHASE3"])
    response = _run_fast_path_lookup(study)
    raw_by_id = {"NCT04368728": study}
    populations = single_cohort_populations(response, raw_by_id)
    [clean_row] = response.visualization.data
    assert clean_row["enrollment"] == "47079"
    forged = with_rows(response, [{**clean_row, "enrollment": "99999"}])

    violations = violations_of(forged, raw_by_id, populations)

    assert tags_of(violations) == {"display"}
    assert "enrollment" in violations[0]
    assert "99999" in violations[0] and "47079" in violations[0]
