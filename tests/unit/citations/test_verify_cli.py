"""Offline CLI (§11.8): `python -m ctviz.citations.verify <response.json> [--raw PATH]` checks
an example without our server or the network (Task 6.3 Step 1: "a test using a small synthetic
response + raw file")."""

import gzip
import json
from pathlib import Path

import pytest

from ctviz.citations.verify import main
from tests.unit.citations.conftest import build_small_response


def _write_example(tmp_path: Path, response, raw_by_id) -> tuple[Path, Path]:
    response_path = tmp_path / "01.response.json"
    response_path.write_text(response.model_dump_json())
    raw_path = tmp_path / "01.raw.json.gz"
    with gzip.open(raw_path, "wt", encoding="utf-8") as handle:
        json.dump({"records": list(raw_by_id.values())}, handle)
    return response_path, raw_path


def test_cli_verifies_a_clean_response_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    response, raw_by_id, _plotted = build_small_response()
    response_path, raw_path = _write_example(tmp_path, response, raw_by_id)

    exit_code = main([str(response_path), "--raw", str(raw_path)])

    assert exit_code == 0
    assert "PASS" in capsys.readouterr().out


def test_cli_finds_the_raw_file_by_sibling_naming_convention(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No `--raw` flag: `NN.response.json` -> `NN.raw.json.gz` in the same directory (§11.8)."""
    response, raw_by_id, _plotted = build_small_response()
    response_path, _raw_path = _write_example(tmp_path, response, raw_by_id)

    exit_code = main([str(response_path)])

    assert exit_code == 0
    assert "PASS" in capsys.readouterr().out


def test_cli_reports_fail_and_nonzero_exit_on_a_corrupted_response(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    response, raw_by_id, _plotted = build_small_response()
    [row] = [r for r in response.visualization.data if r["category"] == "Phase 1"]
    bad = row["citations"][0].model_copy(update={"excerpt": "WRONG"})
    rows = [
        {**r, "citations": [bad]} if r["category"] == "Phase 1" else r
        for r in response.visualization.data
    ]
    viz = response.visualization.model_copy(update={"data": rows})
    corrupted = response.model_copy(update={"visualization": viz})
    response_path, raw_path = _write_example(tmp_path, corrupted, raw_by_id)

    exit_code = main([str(response_path), "--raw", str(raw_path)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "FAIL" in out
    assert "WRONG" in out


def test_cli_missing_response_file_is_a_readable_error_with_exit_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([str(tmp_path / "nope.response.json")])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "cannot read" in captured.err and "nope.response.json" in captured.err
    assert "Traceback" not in captured.err


def test_cli_missing_raw_file_is_a_readable_error_with_exit_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    response, raw_by_id, _populations = build_small_response()
    response_path, raw_path = _write_example(tmp_path, response, raw_by_id)
    raw_path.unlink()

    exit_code = main([str(response_path)])

    assert exit_code == 2
    assert "01.raw.json.gz" in capsys.readouterr().err


def test_cli_malformed_response_json_is_a_readable_error_with_exit_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "01.response.json"
    bad.write_text("{not json")

    exit_code = main([str(bad)])

    assert exit_code == 2
    assert "not a valid" in capsys.readouterr().err
