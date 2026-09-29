"""`scripts/package_zip.sh`: what ships, what must never ship, and the secret scan (Task 10.2)."""

import importlib.util
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "package_zip.sh"
ZIP_NAME = "ctviz.zip"
# Built at runtime so this file itself never contains a key-shaped literal (the zip scan would
# reject the archive that ships this test).
FAKE_KEY = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}  # fmt: skip


def _run(out_dir: Path, repo: Path = REPO) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CTVIZ_REPO_ROOT": str(repo)}
    return subprocess.run(
        ["bash", str(SCRIPT), str(out_dir)], capture_output=True, text=True, env=env, check=False
    )


@pytest.fixture(scope="module")
def zip_names(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    out = tmp_path_factory.mktemp("dist")
    done = _run(out)
    assert done.returncode == 0, done.stderr
    with zipfile.ZipFile(out / ZIP_NAME) as archive:
        return archive.namelist()


def test_zip_contains_no_env_file_except_the_example(zip_names: list[str]) -> None:
    env_like = [n for n in zip_names if Path(n).name.startswith(".env")]

    assert env_like == [n for n in env_like if Path(n).name == ".env.example"]
    assert any(Path(n).name == ".env.example" for n in zip_names)


def test_zip_excludes_the_internal_plan_and_its_pdfs(zip_names: list[str]) -> None:
    banned = [n for n in zip_names if "PLAN" in Path(n).name or "/plans/" in f"/{n}"]

    assert banned == []


def test_zip_excludes_venv_caches_and_agent_scratch(zip_names: list[str]) -> None:
    noisy = (".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".superpowers")

    assert [n for n in zip_names if any(part in noisy for part in Path(n).parts)] == []


def test_zip_has_readme_design_source_and_tests(zip_names: list[str]) -> None:
    for required in ("README.md", "docs/DESIGN.md", "pyproject.toml", "DEVLOG.md", "Makefile"):
        assert required in zip_names, required
    assert any(n.startswith("src/ctviz/") for n in zip_names)
    assert any(n.startswith("tests/") for n in zip_names)


def test_zip_ships_every_example_file_that_exists_now(zip_names: list[str]) -> None:
    on_disk = [p.name for p in (REPO / "examples").iterdir() if p.is_file()]

    assert on_disk, "expected at least examples/canned_plans.json"
    for name in on_disk:
        assert f"examples/{name}" in zip_names


def _expected_example_files() -> list[str]:
    spec = importlib.util.spec_from_file_location("ge", REPO / "scripts" / "generate_examples.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    files: list[str] = module.expected_example_files()
    return files


def test_zip_ships_all_five_generated_examples(zip_names: list[str]) -> None:
    missing = [n for n in _expected_example_files() if f"examples/{n}" not in zip_names]

    assert missing == []


def test_secret_scan_rejects_a_planted_key_and_leaves_no_zip(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("hi\n")
    (repo / "config.txt").write_text(f"OPENAI_API_KEY={FAKE_KEY}\n")
    env = {**os.environ, **GIT_ENV}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)
    out = tmp_path / "out"

    done = _run(out, repo)

    assert done.returncode == 1
    assert "secret" in done.stderr.lower()
    assert not (out / ZIP_NAME).exists()


def test_clean_repo_packages_successfully(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("hi\n")
    env = {**os.environ, **GIT_ENV}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)
    out = tmp_path / "out"

    done = _run(out, repo)

    assert done.returncode == 0, done.stderr
    assert (out / ZIP_NAME).exists()
