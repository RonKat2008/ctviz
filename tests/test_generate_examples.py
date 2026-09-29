"""Pure helpers of `scripts/generate_examples.py` (abridging, naming, raw-record merging).

The live `main()` is deliberately untested: it needs real OpenAI/OpenRouter/ClinicalTrials.gov.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_examples.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_examples", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_examples"] = module
    spec.loader.exec_module(module)
    return module


ge = _load()


def _citation(i: int) -> dict[str, object]:
    return {"nct_id": f"NCT{i:08d}", "field": "/a", "excerpt": "x", "evidence": []}


def _row(label: str, n_citations: int) -> dict[str, object]:
    return {"label": label, "citations": [_citation(i) for i in range(n_citations)]}


def test_the_five_examples_cover_the_five_spec_chart_types_in_order() -> None:
    types = [e.chart_type for e in ge.EXAMPLES]

    assert types == [
        "time_series",
        "histogram",
        "grouped_bar_chart",
        "network_graph",
        "bar_chart",
    ]
    assert [e.number for e in ge.EXAMPLES] == [1, 2, 3, 4, 5]


def test_first_example_is_the_specs_own_pembrolizumab_request() -> None:
    assert ge.EXAMPLES[0].request == {
        "query": "How has the number of trials for this drug changed over time?",
        "drug_name": "Pembrolizumab",
    }


def test_example_paths_follow_the_nn_dot_kind_convention() -> None:
    paths = ge.example_paths(3, Path("out"))

    assert paths.request == Path("out/03.request.json")
    assert paths.response == Path("out/03.response.json")
    assert paths.raw == Path("out/03.raw.json.gz")
    assert paths.verify == Path("out/03.verify.txt")


def test_expected_example_files_lists_four_files_per_example() -> None:
    names = ge.expected_example_files()

    assert len(names) == 20
    assert "01.request.json" in names and "05.verify.txt" in names


def test_abridge_keeps_three_rows_and_marks_the_rest_elided() -> None:
    response = {"visualization": {"data": [_row(str(i), 1) for i in range(7)]}}

    out = ge.abridge_response(response)

    data = out["visualization"]["data"]
    assert [r.get("label") for r in data[:3]] == ["0", "1", "2"]
    assert data[3] == {"_elided": 4}
    assert len(data) == 4


def test_abridge_keeps_two_citations_per_datum_and_marks_the_rest_elided() -> None:
    response = {"visualization": {"data": [_row("a", 5)]}}

    out = ge.abridge_response(response)

    cites = out["visualization"]["data"][0]["citations"]
    assert len(cites) == 3
    assert cites[2] == {"_elided": 3}


def test_abridge_leaves_short_lists_untouched_with_no_marker() -> None:
    response = {"visualization": {"data": [_row("a", 2), _row("b", 1)]}}

    out = ge.abridge_response(response)

    assert out == response


def test_abridge_handles_network_nodes_and_edges() -> None:
    nodes = [_row(str(i), 4) for i in range(5)]
    edges = [_row(str(i), 1) for i in range(4)]
    response = {"visualization": {"data": {"directed": True, "nodes": nodes, "edges": edges}}}

    out = ge.abridge_response(response)

    data = out["visualization"]["data"]
    assert data["directed"] is True
    assert data["nodes"][3] == {"_elided": 2}
    assert data["nodes"][0]["citations"][2] == {"_elided": 2}
    assert data["edges"][3] == {"_elided": 1}


def test_abridge_shortens_long_meta_lists() -> None:
    excluded = [{"nct_id": f"NCT{i:08d}", "stage": "match", "reason": "r"} for i in range(10)]
    response = {"meta": {"data_coverage": {"excluded_trials": excluded}}}

    out = ge.abridge_response(response)

    shortened = out["meta"]["data_coverage"]["excluded_trials"]
    assert len(shortened) == 4 and shortened[3] == {"_elided": 7}


def test_abridge_does_not_mutate_its_input() -> None:
    response = {"visualization": {"data": [_row(str(i), 5) for i in range(6)]}}
    before = repr(response)

    ge.abridge_response(response)

    assert repr(response) == before


def test_abridge_leaves_error_responses_alone() -> None:
    response = {"ok": False, "visualization": None, "meta": None, "error": {"code": "X"}}

    assert ge.abridge_response(response) == response


def _record(nct: str) -> dict[str, object]:
    return {"protocolSection": {"identificationModule": {"nctId": nct}}}


def test_merge_raw_records_dedupes_by_nct_id_keeping_first_seen_order() -> None:
    merged = ge.merge_raw_records(
        [
            [_record("NCT00000002"), _record("NCT00000001")],
            [_record("NCT00000001"), _record("NCT00000003")],
        ]
    )

    ids = [r["protocolSection"]["identificationModule"]["nctId"] for r in merged]
    assert ids == ["NCT00000002", "NCT00000001", "NCT00000003"]


def test_verify_argv_points_the_module_cli_at_response_and_raw() -> None:
    paths = ge.example_paths(2, Path("out"))

    argv = ge.verify_argv(paths, python="py")

    assert argv == [
        "py",
        "-m",
        "ctviz.citations.verify",
        "out/02.response.json",
        "--raw",
        "out/02.raw.json.gz",
    ]


def test_render_snippets_has_one_section_per_example_with_abridged_json() -> None:
    entries = [
        (
            ge.EXAMPLES[0],
            {"ok": True, "visualization": {"data": [_row("a", 1)]}},
            "PASS -- 1 citations",
        ),
        (ge.EXAMPLES[1], {"ok": True, "visualization": {"data": [_row("b", 1)]}}, None),
    ]

    text = ge.render_snippets(entries)

    assert text.count("\n## Example ") + text.startswith("## Example ") == 2
    assert "01.response.json" in text and "02.verify.txt" in text
    assert "PASS -- 1 citations" in text
    assert "```json" in text


def test_write_json_is_stable_and_utf8(tmp_path: Path) -> None:
    target = tmp_path / "x.json"

    ge.write_json(target, {"b": "é", "a": 1})

    assert target.read_text(encoding="utf-8") == '{\n  "b": "é",\n  "a": 1\n}\n'


@pytest.mark.parametrize("bad", [0, 6])
def test_example_paths_rejects_numbers_outside_one_to_five(bad: int) -> None:
    with pytest.raises(ValueError):
        ge.example_paths(bad, Path("out"))
