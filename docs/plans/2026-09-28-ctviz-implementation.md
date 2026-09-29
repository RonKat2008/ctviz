# ctviz — ClinicalTrials.gov Query-to-Visualization Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI backend that turns a natural-language clinical-trial question (plus optional structured fields) into a JSON visualization spec backed by ClinicalTrials.gov API v2. Every datum carries verifiable deep citations.

**Architecture:**
- An OpenAI planner fills a typed `QueryPlan` by choosing from menus.
- Code applies the user's structured fields, checks the plan, and probes real counts.
- An OpenRouter judge verifies intent, with one revise allowed.
- Deterministic Python compiles the plan to API calls, fetches and normalizes records, applies a strict match, aggregates while recording JSON-Pointer evidence, and builds the spec.
- An independent verifier recounts every datum before the response is returned.

**Tech Stack:** Python 3.12 · uv · FastAPI · Pydantic v2 · pydantic-settings · httpx · openai SDK (also used for OpenRouter) · PyYAML · pytest · pytest-asyncio · respx · pytest-cov · ruff · mypy. **No agent framework** (decision recorded in the README).

**Spec:** `PLAN.md` (full design; the Part II section numbers are cited below as §N) and `PLAN-short.md` (visual summary).

## Decisions confirmed by you (2026-09-28)

| Q | Decision | Where it lands |
|---|---|---|
| Q1 | **a**. Strict match for drugs and sponsors. Conditions stay lenient until measured, and switch to strict if ≥ 97% match. | S2 Task 2.4 (measure), S6 Task 6.1 |
| Q2 | **a**. Keep the API result and warn with the sponsor-name census. | S5 Task 5.3 |
| Q3 | **Deferred.** The HTML viewer gets its own plan later and is out of scope here. | — |
| Q4 | **a**. Combined phase buckets ("Phase 2/Phase 3" is its own bar). | S3 Task 3.3 |
| — | No agent framework: plain OpenAI SDK + Pydantic. | S4 Task 4.1 |

## Global Constraints

- Python `>=3.12`, managed with `uv` (`uv run …` for every command).
- Package name `ctviz`, source in `src/ctviz/`, tests in `tests/`.
- API base `https://clinicaltrials.gov/api/v2`; `pageSize` max 1000; fetch cap 20,000 records per cohort.
- Planner model `gpt-5.4-mini` (via `PLANNER_MODEL`); judge model `google/gemini-2.5-flash-lite` via OpenRouter (`JUDGE_MODEL`).
- **Never** pass `temperature` to gpt-5.x models.
- Search values are sent to the API **as given**, with no auto-quoting (D15).
- The LLM never writes URLs, `fields=`, Essie expressions, counts, category labels or excerpts.
- JSON Pointers follow RFC 6901. An excerpt is the exact scalar value (strings verbatim; numbers as JSON text, e.g. `"84"`).
- Default citation mode is `full`: every contributing trial is cited.
- API keys live only in `.env`, which is gitignored and excluded from the zip. They are never logged or returned.
- Response envelope is `{schema_version, ok, visualization, meta, error}`. Domain outcomes return HTTP 200 with `ok:false`.

## Review Focus

These are the five inputs most likely to hurt a real user that no happy-path test exercises. Each is pinned by a named test in the task that owns the code.

1. **Trials with missing fields** (no `phases`, no start date, no locations). They must land in an explicit, cited bucket or be listed as excluded, never crash or vanish. → S2 Task 2.3, `test_normalize_handles_missing_sections`.
2. **A misspelled drug returns 0 or a handful of fuzzy hits.** Expected: a revise, then `NO_MATCHING_TRIALS`, and never an empty chart. → S7 Task 7.4, `test_zero_probe_twice_returns_no_matching_trials`.
3. **A question matching > 20,000 trials.** Expected: the most recent 20k by start date, with `truncated=true` and the rule disclosed. → S2 Task 2.2, `test_fetch_all_truncates_and_sorts_when_over_cap`.
4. **A comparison whose cohorts overlap** (a trial tests both drugs). Expected: the trial is counted in both cohorts, with no verifier false alarm. → S6 Task 6.3, `test_verifier_accepts_overlapping_cohorts`.
5. **ClinicalTrials.gov returns 4xx/5xx (plain-text body) or times out.** Expected: retries, then HTTP 502 `UPSTREAM_API_ERROR` with a readable message. → S2 Task 2.2, `test_client_raises_upstream_error_with_text_body`.

---

## How we work: conventions and verification

### Code conventions (enforced by tooling, then checked in review)

| Rule | Enforcement |
|---|---|
| PEP 8, 100-char lines, sorted imports | `ruff check` + `ruff format` (config in `pyproject.toml`) |
| Type hints on every function; strict typing in `schemas/`, `citations/`, `common/` | `mypy --strict` on those packages, `mypy` on the rest |
| Names: `snake_case` functions/vars, `PascalCase` classes, `UPPER_SNAKE` constants, `is_/has_` booleans | review |
| One responsibility per module; modules ≤ 400 lines; functions ≤ 40 lines; nesting ≤ 3 | review (`ruff` C901 complexity ≤ 10) |
| Immutability: frozen dataclasses / frozen pydantic models; functions return new objects and never mutate their inputs | review + tests |
| Every public function gets a one-line docstring saying *what* and *why* (not how) | review |
| Errors are explicit: custom exceptions in `ctviz/errors.py`; nothing is swallowed silently; no bare `except` | ruff `BLE`, `E722` |
| Magic numbers become named constants in `ctviz/config.py` or at module top | review |
| No `print`; use `logging.getLogger(__name__)` | ruff `T20` |
| Tests follow Arrange-Act-Assert, with descriptive names (`test_<unit>_<behavior>_<condition>`) and no network (live tests carry the `@pytest.mark.live` marker) | review + `pytest -m "not live"` default |

### Verification protocol

- **Every task:**
  1. Write the failing test and confirm it fails for the right reason.
  2. Write the minimal implementation.
  3. Run that test file and confirm it passes.
  4. Run `make lint` (ruff check + ruff format --check + mypy).
- **End of every stage (checkpoint):**
  1. Full suite, `make test` (pytest with coverage).
  2. A code-review subagent reviews that stage's diff against these conventions; findings are fixed before moving on.
  3. **You review.** I post a checkpoint summary with:
     - the files changed, with line counts;
     - the test output and coverage;
     - the review findings and how each was resolved;
     - one runnable command or sample output to try;
     - 2–3 specific places in the code worth reading.
  4. After your OK, I commit (`git commit` only happens at checkpoints you approve).

### File map (what each file is responsible for)

```
src/ctviz/
  config.py               settings (env) + named constants (caps, timeouts)
  errors.py               exception hierarchy (UpstreamError, PlanInvalidError, CitationCheckError, LLMUnavailableError)
  common/names.py         normalize_text, text_matches, normalize_drug  (shared by analysis + citations)
  schemas/enums.py        API enums + our menus (Phase, OverallStatus, Dimension, VizType, …)
  schemas/request.py      VisualizeRequest + lenient coercion (phase/status/country aliases)
  schemas/plan.py         QueryPlan and its parts (strict-output compatible)
  schemas/citations.py    Evidence, Citation, Predicate type alias
  schemas/viz.py          Channel + 8 visualization models (discriminated union)
  schemas/response.py     Meta, DataCoverage, CohortSummary, Validation, ErrorInfo, VisualizeResponse
  catalog/catalog.yaml    API capability catalog the planner reads
  catalog/loader.py       load + validate catalog; render planner/judge text
  catalog/countries.py    the 226 API country spellings + aliases (generated snapshot)
  ctgov/fields.py         which fields= pieces each dimension/measure/network needs
  ctgov/compiler.py       QueryPlan → RequestSpec per cohort (params dict)
  ctgov/client.py         async fetch: probe, paginate, sort-on-truncation, retries, cache
  ctgov/normalize.py      raw study → Trial (phase label, partial dates, sites, interventions)
  citations/pointer.py    RFC 6901 build/resolve + json_text
  citations/predicates.py predicate evaluator (the verifier's only notion of "belongs")
  citations/match.py      strict match (predicate + evidence) and filter re-check
  citations/verify.py     independent verifier + `python -m ctviz.citations.verify` CLI
  analysis/dimensions.py  extractors: Trial → [DimensionHit(key, evidence, predicate)]
  analysis/aggregate.py   count_by (top-N + Other), cross-tab, time_trend
  analysis/numeric.py     histogram bins, scatter points
  analysis/network.py     sponsor_drug / drug_drug / condition_drug graphs + pruning
  analysis/entities.py    alias discovery (co-reference), sponsor census
  analysis/guards.py      shape guards (degenerate data → better chart type)
  viz/builder.py          buckets/points/graph → Visualization + encoding + options
  agent/prompts.py        planner/judge prompt builders + few-shots
  agent/planner.py        PlannerBackend protocol + OpenAIPlanner
  agent/overlay.py        structured request fields → plan slots (deterministic)
  agent/plan_checks.py    §7.4 rules + digit guard
  agent/judge.py          JudgeBackend protocol + OpenRouterJudge + verdict recompute
  agent/orchestrator.py   revise-loop state machine (§9.4)
  pipeline.py             request → response (used by API, CLI, examples)
  api/app.py              FastAPI app: POST /v1/visualize, GET /v1/schema, GET /health
  api/errors.py           exception → envelope + HTTP status
  api/replay.py           PLANNER_MODE=replay (canned plans + recorded fixtures)
tests/
  factories.py            make_study(...) — readable fake API records for unit tests
  unit/…                  one test module per source module
  integration/…           pipeline over recorded fixtures with fake planner/judge
  api/…                   FastAPI TestClient contract tests
  fixtures/ctgov/*.json.gz recorded API pages (S2 Task 2.4)
```

---

## Stage S0 — Project setup and smoke tests (~0.5 h)

**Stage done when:** `uv run pytest` runs, ruff and mypy pass on an empty package, both LLM keys answer a tiny structured call, and the API `/version` responds.

### Task 0.1: Repository, tooling and configuration

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `Makefile`, `DEVLOG.md`, `src/ctviz/__init__.py`, `src/ctviz/config.py`, `src/ctviz/errors.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/unit/test_config.py`
- Create (not committed): `.env` holding your two keys

**Interfaces:**
- Produces: `ctviz.config.Settings`, `get_settings() -> Settings`, constants `CTGOV_BASE_URL`, `PAGE_SIZE=1000`, `MAX_RECORDS=20_000`, `HTTP_TIMEOUT_S`, `HTTP_CONNECT_TIMEOUT_S`, `HTTP_RETRIES`, `LLM_TIMEOUT_S`, `COHORT_FETCH_CONCURRENCY`, `CACHE_TTL_S`; exceptions `CtvizError`, `UpstreamError(message, status_code)`, `PlanInvalidError(errors)`, `CitationCheckError(violations)`, `LLMUnavailableError`.

- [ ] **Step 1: Initialize the repo and project**

```bash
cd /Users/ronitkatikaneni/Desktop/cs61a/hw/CheironTakeHome
git init
uv init --package --name ctviz --python 3.12 --no-readme .
uv add fastapi "uvicorn[standard]" httpx pydantic pydantic-settings openai pyyaml
uv add --dev pytest pytest-asyncio pytest-cov respx ruff mypy types-PyYAML
```

- [ ] **Step 2: Configure tooling in `pyproject.toml`** (append these sections)

```toml
[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "C4", "C90", "SIM", "RET", "BLE", "T20", "PTH", "RUF"]

[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.mypy]
python_version = "3.12"
packages = ["ctviz"]
mypy_path = "src"
disallow_untyped_defs = true
warn_unused_ignores = true

[[tool.mypy.overrides]]
module = ["ctviz.schemas.*", "ctviz.citations.*", "ctviz.common.*"]
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = ["live: hits real network APIs (opt-in with -m live)"]
addopts = "-m 'not live'"
```

- [ ] **Step 3: Write `.gitignore`, `.env.example`, `Makefile`**

`.gitignore`:
```
.venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
.env
.env.*
!.env.example
dist/
*.zip
```

`.env.example` (then copy to `.env` and paste the two keys in by hand):
```
OPENAI_API_KEY=
OPENROUTER_API_KEY=
PLANNER_MODE=live
PLANNER_MODEL=gpt-5.4-mini
PLANNER_REASONING_EFFORT=low
JUDGE_MODEL=google/gemini-2.5-flash-lite
APP_URL=http://localhost:8000
APP_NAME=ctviz
```

`Makefile`:
```makefile
.PHONY: install test lint check run smoke
install: ; uv sync
test:    ; uv run pytest --cov=ctviz --cov-report=term-missing
lint:    ; uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
check:   lint test
run:     ; uv run uvicorn ctviz.api.app:app --reload
smoke:   ; uv run pytest -m live tests/live/test_smoke.py -v
```

- [ ] **Step 4: Write the failing config test** — `tests/unit/test_config.py`

```python
from ctviz.config import MAX_RECORDS, PAGE_SIZE, Settings


def test_settings_defaults_do_not_require_keys() -> None:
    settings = Settings(_env_file=None)

    assert settings.openai_api_key is None
    assert settings.planner_model == "gpt-5.4-mini"
    assert settings.planner_mode == "live"


def test_settings_never_expose_keys_in_repr() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-secret-value")

    assert "sk-secret-value" not in repr(settings)


def test_fetch_limits_match_the_api() -> None:
    assert PAGE_SIZE == 1000
    assert MAX_RECORDS == 20_000
```

- [ ] **Step 5: Run it and confirm it fails.** Run `uv run pytest tests/unit/test_config.py -v`. Expected: FAIL (`ModuleNotFoundError: ctviz.config`).

- [ ] **Step 6: Implement `src/ctviz/config.py` and `src/ctviz/errors.py`**

```python
"""Runtime settings (from environment / .env) and named constants used across ctviz."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

CTGOV_BASE_URL = "https://clinicaltrials.gov/api/v2"
PAGE_SIZE = 1000  # API hard cap per page
MAX_RECORDS = 20_000  # per-cohort fetch cap (D6)
HTTP_TIMEOUT_S = 20.0
HTTP_CONNECT_TIMEOUT_S = 5.0
HTTP_RETRIES = 3
LLM_TIMEOUT_S = 30.0
COHORT_FETCH_CONCURRENCY = 4
CACHE_TTL_S = 3600


class Settings(BaseSettings):
    """Environment-driven configuration. Keys are optional so the service can start without them."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    planner_mode: Literal["live", "replay"] = "live"
    planner_model: str = "gpt-5.4-mini"
    planner_reasoning_effort: Literal["low", "medium", "high"] = "low"
    judge_model: str = "google/gemini-2.5-flash-lite"
    app_url: str = "http://localhost:8000"
    app_name: str = "ctviz"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide settings (cached so .env is read once)."""
    return Settings()
```

```python
"""Exception hierarchy. api/errors.py maps each type to an error code + HTTP status."""


class CtvizError(Exception):
    """Base class for all expected, user-reportable failures."""


class UpstreamError(CtvizError):
    """ClinicalTrials.gov returned an error or was unreachable."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMUnavailableError(CtvizError):
    """No LLM provider could produce a plan."""


class PlanInvalidError(CtvizError):
    """The plan still failed deterministic checks after the revise attempt."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class CitationCheckError(CtvizError):
    """The independent verifier found an inconsistency — a bug in our code, never user error."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__(f"{len(violations)} citation violation(s)")
        self.violations = violations
```

- [ ] **Step 7: Run the tests and the quality gates.** Run `uv run pytest tests/unit/test_config.py -v && make lint`. Expected: 3 passed, lint clean.

- [ ] **Step 8: Start `DEVLOG.md`** with the first entry: the date, the scaffold, and the decision "no agent framework (fixed 2-call pipeline; testability; readability)".

### Task 0.2: Live smoke tests (keys + API reachable)

**Files:**
- Create: `tests/live/__init__.py`, `tests/live/test_smoke.py`

**Interfaces:** none. This only proves the environment works, including gpt-5.4-mini strict structured output (an [ASSUMPTION] in the spec).

- [ ] **Step 1: Write the live smoke tests**

```python
import httpx
import pytest
from openai import OpenAI
from pydantic import BaseModel

from ctviz.config import CTGOV_BASE_URL, get_settings

pytestmark = pytest.mark.live


class Ping(BaseModel):
    answer: str


def test_clinicaltrials_version_endpoint_responds() -> None:
    response = httpx.get(f"{CTGOV_BASE_URL}/version", timeout=10)

    assert response.status_code == 200
    assert "apiVersion" in response.json()


def test_openai_planner_model_supports_strict_structured_output() -> None:
    settings = get_settings()
    assert settings.openai_api_key is not None, "set OPENAI_API_KEY in .env"
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value())

    result = client.responses.parse(
        model=settings.planner_model,
        input=[{"role": "user", "content": "Reply with answer='pong'."}],
        text_format=Ping,
    )

    assert result.output_parsed is not None
    assert result.output_parsed.answer.lower() == "pong"


def test_openrouter_judge_model_returns_json_schema_output() -> None:
    settings = get_settings()
    assert settings.openrouter_api_key is not None, "set OPENROUTER_API_KEY in .env"
    client = OpenAI(api_key=settings.openrouter_api_key.get_secret_value(),
                    base_url="https://openrouter.ai/api/v1")

    completion = client.chat.completions.parse(
        model=settings.judge_model,
        messages=[{"role": "user", "content": "Reply with answer='pong'."}],
        response_format=Ping,
        temperature=0,
        extra_body={"provider": {"require_parameters": True}},
    )

    assert completion.choices[0].message.parsed is not None
```

- [ ] **Step 2: Run it.** Run `make smoke`. Expected: 3 passed. **If the OpenAI test fails on strict output**, set `PLANNER_MODEL=gpt-4.1-mini` in `.env`, note it in DEVLOG, and re-run. That's the documented fallback.

### ✅ Checkpoint S0 — you verify

- **I show you:** `make check` output, `make smoke` output, and the tree of created files.
- **You check:**
  1. `git status` never lists `.env`.
  2. `config.py` reads cleanly: constants on top, settings below.
  3. You're happy with the conventions table.
- **Then:** first commit, `chore: project scaffold, tooling, smoke tests`.

---

## Stage S1 — Contracts: request, plan, citation and response schemas (~2 h)

**Stage done when:**
- every schema validates the examples from PLAN.md §6, §7.5, §11.4 and §12;
- `QueryPlan`'s JSON Schema passes a strict-mode lint;
- request coercion accepts human spellings.

### Task 1.1: Enums and menus

**Files:**
- Create: `src/ctviz/schemas/__init__.py`, `src/ctviz/schemas/enums.py`
- Test: `tests/unit/schemas/test_enums.py`

**Interfaces:**
- Produces:
  - `StrEnum`s whose values equal the API strings: `Phase`, `OverallStatus`, `StudyType`, `InterventionType`, `AgencyClass`.
  - Menu `StrEnum`s: `SearchParam`, `Dimension`, `Measure`, `TimeField`, `NetworkType`, `AnalysisKind`, `VizType`.

- [ ] **Step 1: Write the failing test**

```python
from ctviz.schemas.enums import AnalysisKind, Dimension, NetworkType, Phase, SearchParam, VizType


def test_phase_values_match_the_api_exactly() -> None:
    assert [p.value for p in Phase] == [
        "NA", "EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4",
    ]


def test_menus_contain_exactly_the_documented_choices() -> None:
    assert {v.value for v in VizType} == {
        "bar_chart", "grouped_bar_chart", "time_series", "scatter_plot",
        "histogram", "network_graph", "table", "metric",
    }
    assert "query.intr" in {p.value for p in SearchParam}
    assert Dimension.PHASE.value == "phase"
    assert {n.value for n in NetworkType} == {"sponsor_drug", "drug_drug", "condition_drug"}
    assert AnalysisKind.TIME_TREND.value == "time_trend"
```

- [ ] **Step 2: Run it and confirm it fails.** Expected: FAIL (module missing).

- [ ] **Step 3: Implement `enums.py`.** Each class is a `StrEnum` whose members exactly mirror PLAN.md §5.6 (API enums) and §7.2 (menus):
  - `Phase`: NA, EARLY_PHASE1, PHASE1..PHASE4.
  - `OverallStatus`: the 14 values in §5.6.
  - `StudyType`: 3 values.
  - `InterventionType`: 11 values.
  - `AgencyClass`: 9 values.
  - `SearchParam`: members COND…TERM, with values `"query.cond"` … `"query.term"`.
  - `Dimension`: 10 members, e.g. `PHASE="phase"`, `START_YEAR="start_year"`.
  - `Measure`: 3 values.
  - `TimeField`: 3 values.
  - `NetworkType`: the 3 core values.
  - `AnalysisKind`: 7 values.
  - `VizType`: 8 values.

  Write each member explicitly, e.g.

```python
"""Controlled vocabularies: ClinicalTrials.gov enums (values = API strings) and our planner menus."""

from enum import StrEnum


class Phase(StrEnum):
    NA = "NA"
    EARLY_PHASE1 = "EARLY_PHASE1"
    PHASE1 = "PHASE1"
    PHASE2 = "PHASE2"
    PHASE3 = "PHASE3"
    PHASE4 = "PHASE4"


class SearchParam(StrEnum):
    COND = "query.cond"
    INTR = "query.intr"
    LEAD = "query.lead"
    SPONS = "query.spons"
    LOCN = "query.locn"
    TITLES = "query.titles"
    OUTC = "query.outc"
    TERM = "query.term"

# … OverallStatus, StudyType, InterventionType, AgencyClass, Dimension, Measure, TimeField,
#   NetworkType, AnalysisKind, VizType written out member-by-member in the same style.
```

- [ ] **Step 4: Run it and confirm it passes.** Expected: 2 passed.

### Task 1.2: Request schema with forgiving coercion

**Files:**
- Create: `src/ctviz/schemas/request.py`, `src/ctviz/catalog/__init__.py`, `src/ctviz/catalog/countries.py`, `scripts/snapshot_countries.py`
- Test: `tests/unit/schemas/test_request.py`

**Interfaces:**
- Consumes: `Phase`, `OverallStatus`, `StudyType`; `MAX_RECORDS`.
- Produces:
  - `VisualizeRequest(query, drug_name, condition, sponsor, sponsor_role, trial_phase: list[Phase]|None, status: list[OverallStatus]|None, country: str|None, start_year, end_year, study_type, nct_ids, options: RequestOptions)`.
  - `RequestOptions(max_records, citations: Literal["full","sample","none"], top_n, include_collaborators, strict_match: Literal["auto","all","off"])`.
  - Helpers `coerce_phases`, `coerce_statuses`, `canonical_country`.

- [ ] **Step 1: Generate the country snapshot.**
  - `scripts/snapshot_countries.py` GETs `/stats/field/values?fields=LocationCountry` (unfiltered, so it's allowed).
  - It writes `src/ctviz/catalog/countries.py` with the header `# Generated by scripts/snapshot_countries.py on <date>. Do not edit by hand.` and `COUNTRIES: frozenset[str]`.
  - Run it with `uv run python scripts/snapshot_countries.py`.
  - Then append this hand-written alias table:

```python
COUNTRY_ALIASES: dict[str, str] = {
    "us": "United States", "usa": "United States", "united states of america": "United States",
    "uk": "United Kingdom", "great britain": "United Kingdom", "england": "United Kingdom",
    "turkey": "Turkey (Türkiye)", "türkiye": "Turkey (Türkiye)",
    "korea": "South Korea", "republic of korea": "South Korea",
}
```

- [ ] **Step 2: Write the failing tests**

```python
import pytest
from pydantic import ValidationError

from ctviz.schemas.enums import OverallStatus, Phase
from ctviz.schemas.request import VisualizeRequest


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("Phase 3", [Phase.PHASE3]),
        ("phase3", [Phase.PHASE3]),
        ("3", [Phase.PHASE3]),
        ("PHASE3", [Phase.PHASE3]),
        ("Phase 2/3", [Phase.PHASE2, Phase.PHASE3]),
        ("early phase 1", [Phase.EARLY_PHASE1]),
        ("N/A", [Phase.NA]),
        (["Phase 1", "Phase 2"], [Phase.PHASE1, Phase.PHASE2]),
    ],
)
def test_trial_phase_accepts_human_spellings(given: object, expected: list[Phase]) -> None:
    assert VisualizeRequest(query="phases please", trial_phase=given).trial_phase == expected


def test_status_is_case_and_punctuation_insensitive() -> None:
    request = VisualizeRequest(query="q q", status=["recruiting", "Active, not recruiting"])

    assert request.status == [OverallStatus.RECRUITING, OverallStatus.ACTIVE_NOT_RECRUITING]


@pytest.mark.parametrize(("given", "expected"), [("USA", "United States"), ("japan", "Japan")])
def test_country_is_normalized_to_the_api_spelling(given: str, expected: str) -> None:
    assert VisualizeRequest(query="q q", country=given).country == expected


def test_unknown_country_is_rejected_with_a_helpful_message() -> None:
    with pytest.raises(ValidationError, match="Unknown country 'Atlantis'"):
        VisualizeRequest(query="q q", country="Atlantis")


def test_end_year_before_start_year_is_rejected() -> None:
    with pytest.raises(ValidationError, match="end_year"):
        VisualizeRequest(query="q q", start_year=2020, end_year=2015)


def test_query_is_trimmed_and_must_not_be_blank() -> None:
    assert VisualizeRequest(query="  hello  ").query == "hello"
    with pytest.raises(ValidationError):
        VisualizeRequest(query="   ")


def test_nct_ids_must_match_the_registry_format() -> None:
    with pytest.raises(ValidationError):
        VisualizeRequest(query="q q", nct_ids=["NCT123"])


def test_options_default_to_full_citations_and_auto_strict_match() -> None:
    options = VisualizeRequest(query="q q").options

    assert (options.citations, options.strict_match, options.max_records) == ("full", "auto", 20_000)
```

- [ ] **Step 3: Run the tests and confirm they fail.** Expected: FAIL (module missing).

- [ ] **Step 4: Implement `request.py`**

```python
"""The POST /v1/visualize request body. Inputs are forgiving: human spellings are coerced first."""

import re
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from ctviz.catalog.countries import COUNTRIES, COUNTRY_ALIASES
from ctviz.config import MAX_RECORDS
from ctviz.schemas.enums import OverallStatus, Phase, StudyType

NctId = Annotated[str, StringConstraints(pattern=r"^NCT\d{8}$")]
MIN_YEAR = 1900
MAX_YEARS_AHEAD = 5
_PHASE_PATTERN = re.compile(r"(phase\s*)?([1-4])(\s*/\s*(phase\s*)?([1-4]))?")
_PHASE_WORDS = {"na": Phase.NA, "n/a": Phase.NA, "not applicable": Phase.NA,
                "early phase 1": Phase.EARLY_PHASE1, "early phase1": Phase.EARLY_PHASE1}
_COUNTRY_BY_LOWER = {name.lower(): name for name in COUNTRIES}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _coerce_one_phase(item: Any) -> list[Phase]:
    text = str(item).strip().lower()
    if text in _PHASE_WORDS:
        return [_PHASE_WORDS[text]]
    if text.upper() in Phase.__members__:
        return [Phase(text.upper())]
    match = _PHASE_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError(f"Unknown trial phase {item!r}; use e.g. 'Phase 3' or 'Phase 2/3'")
    digits = [d for d in (match.group(2), match.group(5)) if d]
    return [Phase(f"PHASE{d}") for d in digits]


def coerce_phases(value: Any) -> list[Phase]:
    """Map 'Phase 3', '3', 'phase2/3', 'N/A', 'PHASE3' … onto Phase enums."""
    return [phase for item in _as_list(value) for phase in _coerce_one_phase(item)]


def coerce_statuses(value: Any) -> list[OverallStatus]:
    """Map 'recruiting', 'Active, not recruiting' … onto OverallStatus enums."""
    statuses = []
    for item in _as_list(value):
        key = re.sub(r"[^a-z]+", "_", str(item).strip().lower()).strip("_").upper()
        if key not in OverallStatus.__members__:
            raise ValueError(f"Unknown status {item!r}; valid: {', '.join(OverallStatus)}")
        statuses.append(OverallStatus(key))
    return statuses


def canonical_country(value: str) -> str:
    """Return the API's spelling of a country (e.g. 'USA' → 'United States')."""
    lowered = value.strip().lower()
    if lowered in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[lowered]
    if lowered in _COUNTRY_BY_LOWER:
        return _COUNTRY_BY_LOWER[lowered]
    raise ValueError(f"Unknown country {value!r}; use the ClinicalTrials.gov spelling")


class RequestOptions(BaseModel):
    """Tuning knobs; every field has a safe default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_records: int = Field(default=MAX_RECORDS, ge=100, le=MAX_RECORDS)
    citations: Literal["full", "sample", "none"] = "full"
    top_n: int | None = Field(default=None, ge=3, le=50)
    include_collaborators: bool = False
    strict_match: Literal["auto", "all", "off"] = "auto"


class VisualizeRequest(BaseModel):
    """Only `query` is required; structured fields are applied by code, never copied by the LLM."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=3, max_length=500)
    drug_name: str | None = Field(default=None, min_length=1, max_length=100)
    condition: str | None = Field(default=None, min_length=1, max_length=100)
    sponsor: str | None = Field(default=None, min_length=1, max_length=120)
    sponsor_role: Literal["lead", "any"] = "lead"
    trial_phase: list[Phase] | None = None
    status: list[OverallStatus] | None = None
    country: str | None = None
    start_year: int | None = None
    end_year: int | None = None
    study_type: StudyType | None = None
    nct_ids: list[NctId] | None = Field(default=None, max_length=50)
    options: RequestOptions = RequestOptions()

    @field_validator("trial_phase", mode="before")
    @classmethod
    def _coerce_phase(cls, value: Any) -> Any:
        return None if value is None else coerce_phases(value)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, value: Any) -> Any:
        return None if value is None else coerce_statuses(value)

    @field_validator("study_type", mode="before")
    @classmethod
    def _coerce_study_type(cls, value: Any) -> Any:
        return None if value is None else str(value).strip().upper().replace(" ", "_")

    @field_validator("country")
    @classmethod
    def _canonical_country(cls, value: str | None) -> str | None:
        return None if value is None else canonical_country(value)

    @model_validator(mode="after")
    def _check_years(self) -> "VisualizeRequest":
        latest = date.today().year + MAX_YEARS_AHEAD
        for name in ("start_year", "end_year"):
            year = getattr(self, name)
            if year is not None and not MIN_YEAR <= year <= latest:
                raise ValueError(f"{name} must be between {MIN_YEAR} and {latest}")
        if self.start_year and self.end_year and self.end_year < self.start_year:
            raise ValueError("end_year (latest start year) must be ≥ start_year")
        return self
```

- [ ] **Step 5: Run the tests and confirm they pass, then run `make lint`.** Expected: all passed; lint clean.

### Task 1.3: QueryPlan (the planner's only output)

**Files:**
- Create: `src/ctviz/schemas/plan.py`
- Test: `tests/unit/schemas/test_plan.py`

**Interfaces:**
- Produces: `SearchTerm(param, value, source, rationale)`, `EnumFilters(phases, overall_statuses, study_types, intervention_types, lead_sponsor_classes, countries, start_year_min, start_year_max, nct_ids)`, `Comparison(vary_param, values)`, `Analysis(kind, group_by, series_by, phase_mode, time_field, granularity, measure_x, measure_y, color_by, network_type, top_n)`, `VizChoice(type, title, rationale)`, `QueryPlan(answerable, out_of_scope_reason, suggested_reframing, interpretation, search_terms, filters, comparison, analysis, visualization, assumptions)` + `QueryPlan.strict_json_schema()`. Every field is required-nullable, which is what strict mode needs.

- [ ] **Step 1: Write the failing tests**

```python
from typing import Any

from ctviz.schemas.plan import QueryPlan

MAX_NESTING = 4
EMPTY_ANALYSIS = {"series_by": None, "phase_mode": None, "time_field": None, "granularity": None,
                  "measure_x": None, "measure_y": None, "color_by": None, "network_type": None,
                  "top_n": None}


def _walk(schema: dict[str, Any], defs: dict[str, Any], depth: int = 0) -> int:
    """Assert strict-mode rules on every object node; return max object nesting depth."""
    if "$ref" in schema:
        return _walk(defs[schema["$ref"].split("/")[-1]], defs, depth)
    depth = max([depth] + [_walk(b, defs, depth) for b in schema.get("anyOf", [])])
    if schema.get("type") == "object":
        props = schema.get("properties", {})
        assert set(schema.get("required", [])) == set(props), "every field must be required"
        assert schema.get("additionalProperties") is False
        return max([depth + 1] + [_walk(p, defs, depth + 1) for p in props.values()])
    if schema.get("type") == "array":
        return _walk(schema.get("items", {}), defs, depth)
    for banned in ("minLength", "maxLength", "pattern", "minimum", "maximum", "format"):
        assert banned not in schema, f"strict mode forbids {banned}"
    return depth


def test_query_plan_schema_is_strict_structured_output_compatible() -> None:
    schema = QueryPlan.strict_json_schema()

    assert _walk(schema, schema.get("$defs", {})) <= MAX_NESTING


def test_out_of_scope_plan_parses_with_nulls() -> None:
    plan = QueryPlan.model_validate({
        "answerable": False, "out_of_scope_reason": "Registry has no efficacy data.",
        "suggested_reframing": "Which drugs have the most Phase 3 cancer trials?",
        "interpretation": "Asks which cancer drug is best.", "search_terms": [], "filters": None,
        "comparison": None, "analysis": None, "visualization": None, "assumptions": [],
    })

    assert plan.answerable is False
    assert plan.analysis is None


def test_comparison_plan_from_the_spec_parses() -> None:
    plan = QueryPlan.model_validate({
        "answerable": True, "out_of_scope_reason": None, "suggested_reframing": None,
        "interpretation": "Phases for pembrolizumab vs nivolumab.", "search_terms": [],
        "filters": None,
        "comparison": {"vary_param": "query.intr", "values": ["pembrolizumab", "nivolumab"]},
        "analysis": {"kind": "count_by", "group_by": "phase", **EMPTY_ANALYSIS,
                     "phase_mode": "combined"},
        "visualization": {"type": "grouped_bar_chart", "title": "Trial Phases",
                          "rationale": "Two cohorts, one categorical dimension."},
        "assumptions": [],
    })

    assert plan.comparison is not None
    assert plan.comparison.values == ["pembrolizumab", "nivolumab"]
```

- [ ] **Step 2: Run the tests and confirm they fail.**

- [ ] **Step 3: Implement `plan.py`**

```python
"""QueryPlan: the planner's entire output. Menus + entity strings only — no URLs, numbers or data.

All fields are required-but-nullable because OpenAI strict mode demands every key be present.
Domain rules (years, comparison size, compatibility) live in agent/plan_checks.py, not here.
"""

from typing import Any, Literal

from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ConfigDict

from ctviz.schemas.enums import (
    AgencyClass, AnalysisKind, Dimension, InterventionType, Measure, NetworkType,
    OverallStatus, Phase, SearchParam, StudyType, TimeField, VizType,
)


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SearchTerm(_Strict):
    param: SearchParam
    value: str
    source: Literal["query_text", "structured_field"]
    rationale: str


class EnumFilters(_Strict):
    phases: list[Phase] | None
    overall_statuses: list[OverallStatus] | None
    study_types: list[StudyType] | None
    intervention_types: list[InterventionType] | None
    lead_sponsor_classes: list[AgencyClass] | None
    countries: list[str] | None
    start_year_min: int | None
    start_year_max: int | None
    nct_ids: list[str] | None


class Comparison(_Strict):
    vary_param: SearchParam
    values: list[str]


class Analysis(_Strict):
    kind: AnalysisKind
    group_by: Dimension | None
    series_by: Dimension | None
    phase_mode: Literal["combined", "membership"] | None
    time_field: TimeField | None
    granularity: Literal["year", "month"] | None
    measure_x: Measure | None
    measure_y: Measure | None
    color_by: Dimension | None
    network_type: NetworkType | None
    top_n: int | None


class VizChoice(_Strict):
    type: VizType
    title: str
    rationale: str


class QueryPlan(_Strict):
    answerable: bool
    out_of_scope_reason: str | None
    suggested_reframing: str | None
    interpretation: str
    search_terms: list[SearchTerm]
    filters: EnumFilters | None
    comparison: Comparison | None
    analysis: Analysis | None
    visualization: VizChoice | None
    assumptions: list[str]

    @classmethod
    def strict_json_schema(cls) -> dict[str, Any]:
        """The exact schema OpenAI receives in strict mode (linted by the schema test)."""
        return to_strict_json_schema(cls)
```

- [ ] **Step 4: Run the tests and confirm they pass.** Expected: 3 passed.

### Task 1.4: Citations, visualization and response schemas

**Files:**
- Create: `src/ctviz/schemas/citations.py`, `src/ctviz/schemas/viz.py`, `src/ctviz/schemas/response.py`
- Test: `tests/unit/schemas/test_citations.py`, `tests/unit/schemas/test_viz.py`, `tests/unit/schemas/test_response.py`

**Interfaces:**
- Produces:
  - `Predicate = dict[str, Any]`, `Evidence(role, field, excerpt, span)`, `Citation(nct_id, field, excerpt, evidence)`.
  - `Channel(field, type, title, unit, format, time_unit, sort, bin)`.
  - `BarChart`, `GroupedBarChart`, `TimeSeries`, `Histogram`, `ScatterPlot`, `Table`, `Metric` (each with `data: list[Row]`) and `NetworkGraph` (with `data: NetworkData`). All share `type`, `title`, `encoding: dict[str, Channel]` (required) and `options: dict`.
  - `Visualization` (the discriminated union).
  - `NetworkNode`, `NetworkEdge`, `NetworkData`.
  - `ExcludedTrial`, `DataCoverage`, `CohortSummary`, `CitationCheck`, `JudgeSummary`, `Validation`, `Provenance`, `Meta`, `ErrorInfo`, `VisualizeResponse` + `VisualizeResponse.failure(error, meta=None)`.
  - Constant `SCHEMA_VERSION = "1.0.0"`.

- [ ] **Step 1: Write the failing tests**

`test_citations.py`:
```python
import pytest
from pydantic import ValidationError

from ctviz.schemas.citations import Citation, Evidence


def test_citation_matches_the_assignment_shape_plus_evidence() -> None:
    citation = Citation(
        nct_id="NCT06472076", field="/protocolSection/designModule/phases/0", excerpt="PHASE3",
        evidence=[Evidence(role="match",
                           field="/protocolSection/armsInterventionsModule/interventions/0/name",
                           excerpt="Pembrolizumab")],
    )

    dumped = citation.model_dump(exclude_none=True)

    assert (dumped["nct_id"], dumped["excerpt"]) == ("NCT06472076", "PHASE3")
    assert dumped["evidence"][0]["role"] == "match"


def test_citation_rejects_non_pointer_fields() -> None:
    with pytest.raises(ValidationError):
        Citation(nct_id="NCT06472076", field="protocolSection.designModule", excerpt="x", evidence=[])
```

`test_viz.py`:
```python
import pytest
from pydantic import TypeAdapter, ValidationError

from ctviz.schemas.viz import NetworkGraph, Visualization

ADAPTER: TypeAdapter[Visualization] = TypeAdapter(Visualization)
ONE_CITATION = [{"nct_id": "NCT00000001", "field": "/a", "excerpt": "x", "evidence": []}]


def test_union_dispatches_on_type() -> None:
    viz = ADAPTER.validate_python({
        "type": "bar_chart", "title": "Trials by phase", "options": {},
        "encoding": {"x": {"field": "phase", "type": "ordinal", "title": "Phase"},
                     "y": {"field": "trial_count", "type": "quantitative", "title": "Trials"}},
        "data": [{"phase": "Phase 3", "trial_count": 1, "predicate": {}, "citations": ONE_CITATION}],
    })

    assert viz.type == "bar_chart"


def test_every_visualization_type_requires_encoding() -> None:
    with pytest.raises(ValidationError, match="encoding"):
        ADAPTER.validate_python({"type": "metric", "title": "t", "data": [], "options": {}})


def test_network_edges_must_join_existing_nodes() -> None:
    with pytest.raises(ValidationError, match="unknown node"):
        NetworkGraph.model_validate({
            "type": "network_graph", "title": "t", "options": {},
            "encoding": {"node_id": {"field": "id", "type": "nominal"}},
            "data": {"directed": True,
                     "nodes": [{"id": "a", "label": "A", "type": "sponsor", "weight": 1,
                                "predicate": {}, "citations": []}],
                     "edges": [{"id": "e1", "source": "a", "target": "missing",
                                "type": "sponsor_drug", "weight": 1, "predicate": {},
                                "citations": []}]},
        })


def test_histogram_open_last_bin_serializes_as_null() -> None:
    viz = ADAPTER.validate_python({
        "type": "histogram", "title": "Enrollment", "options": {"scale": "log"},
        "encoding": {"x": {"field": "bin_start", "type": "quantitative", "bin": True},
                     "x2": {"field": "bin_end", "type": "quantitative"},
                     "y": {"field": "trial_count", "type": "quantitative"}},
        "data": [{"bin_start": 5000, "bin_end": None, "bin_label": "≥5000", "trial_count": 0,
                  "predicate": {}, "citations": []}],
    })

    assert '"bin_end":null' in viz.model_dump_json()
```

`test_response.py`:
```python
from ctviz.schemas.response import ErrorInfo, VisualizeResponse


def test_error_envelope_has_one_parse_path() -> None:
    body = VisualizeResponse.failure(ErrorInfo(code="OUT_OF_SCOPE", message="No efficacy data.")).model_dump()

    assert body["ok"] is False
    assert body["visualization"] is None
    assert body["error"]["code"] == "OUT_OF_SCOPE"
    assert body["schema_version"] == "1.0.0"
```

- [ ] **Step 2: Run the tests and confirm they fail.**

- [ ] **Step 3: Implement `citations.py`**

```python
"""Deep-citation data model. A Citation proves why one trial is counted in one datum."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

JsonPointer = Annotated[str, StringConstraints(pattern=r"^(/[^/]*)+$")]
NctId = Annotated[str, StringConstraints(pattern=r"^NCT\d{8}$")]
Predicate = dict[str, Any]  # grammar enforced by ctviz.citations.predicates


class Evidence(BaseModel):
    """One exact value inside a raw study record, addressed by an RFC 6901 JSON Pointer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["match", "filter", "bucket", "context"]
    field: JsonPointer
    excerpt: str
    span: tuple[int, int] | None = None


class Citation(BaseModel):
    """{nct_id, field, excerpt} is the primary bucket evidence; `evidence` holds everything else."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nct_id: NctId
    field: JsonPointer
    excerpt: str
    evidence: list[Evidence]
```

- [ ] **Step 4: Implement `viz.py`.** Use this exact structure:
  - `Row = dict[str, Any]`.
  - `Channel` as listed in Interfaces, with `Literal` types from PLAN.md §12.3.
  - Base class `_Viz` with `title: str`, `encoding: dict[str, Channel]` (**no default**, hence required), `options: dict[str, Any] = {}`, frozen, and `extra="forbid"`.
  - One subclass per type, with `type: Literal["<name>"]` and `data: list[Row]`.
  - `NetworkNode(id, label, type: Literal["sponsor","drug","condition"], weight: int ≥0, predicate, citations)`.
  - `NetworkEdge(id, source, target, type: Literal["sponsor_drug","drug_drug","condition_drug"], weight ≥0, predicate, citations, flags: list[str] = [])`.
  - `NetworkData(directed, nodes, edges)` with this validator:

```python
    @model_validator(mode="after")
    def _edges_join_known_nodes(self) -> "NetworkData":
        ids = {node.id for node in self.nodes}
        bad = [e.id for e in self.edges if e.source not in ids or e.target not in ids]
        if bad:
            raise ValueError(f"edges reference unknown node ids: {bad}")
        return self
```

  Then `NetworkGraph(_Viz)` with `type: Literal["network_graph"]`, `data: NetworkData`, and the union:

```python
Visualization = Annotated[
    BarChart | GroupedBarChart | TimeSeries | Histogram | ScatterPlot | NetworkGraph | Table | Metric,
    Field(discriminator="type"),
]
```

- [ ] **Step 5: Implement `response.py`.** These are frozen, `extra="forbid"` models whose fields are exactly those in PLAN.md §12.6:
  - `DataCoverage.excluded: dict[str, dict[str, int]]`, keyed by stage (`match`/`filter`/`analysis`).
  - `ErrorCode` is a `Literal` of the 8 codes in §12.2.
  - `JudgeStatus` is a `Literal` of the 6 statuses in §9.4.
  - `VisualizeResponse` has `schema_version: Literal["1.0.0"] = SCHEMA_VERSION`, `ok`, `visualization: Visualization | None`, `meta: Meta | None` and `error: ErrorInfo | None`, plus:

```python
    @classmethod
    def failure(cls, error: ErrorInfo, meta: "Meta | None" = None) -> "VisualizeResponse":
        """Build an ok:false envelope (domain outcomes are HTTP 200; see api/errors.py)."""
        return cls(ok=False, visualization=None, meta=meta, error=error)
```

- [ ] **Step 6: Run all the schema tests and `make lint`.** Expected: all passed.

### ✅ Checkpoint S1 — you verify

- **I show you:** the schema test output; `QueryPlan.strict_json_schema()` pretty-printed (exactly what the LLM is constrained to); and a coerced request example (`"Phase 2/3"` → `[PHASE2, PHASE3]`).
- **You check:**
  1. `schemas/plan.py` is the *only* thing the LLM can say, so confirm there are no free-form URL or number fields (only years and top-N).
  2. Every visualization type has `encoding`.
  3. The error messages in `request.py` read well.
- **Then:** commit `feat: request/plan/citation/response contracts`.

---

## Stage S2 — ClinicalTrials.gov data layer (~2.5 h)

**Stage done when:**
- a plan compiles into the exact params from PLAN.md §5.3;
- the client pages, probes, retries, and sorts when truncating;
- raw records normalize into `Trial` objects under every rule in §10.3;
- recorded fixtures exist for the §14 queries.

### Task 2.1: Test factory + field selection + compiler

**Files:**
- Create: `tests/factories.py`, `src/ctviz/ctgov/__init__.py`, `src/ctviz/ctgov/fields.py`, `src/ctviz/ctgov/compiler.py`
- Test: `tests/unit/ctgov/test_compiler.py`

**Interfaces:**
- Consumes: `QueryPlan`, `SearchTerm`, `EnumFilters`, `Comparison` (S1).
- Produces:
  - `RequestSpec(cohort_label: str, cohort_value: str | None, params: dict[str, str])`, frozen dataclass.
  - `compile_plan(plan: QueryPlan) -> list[RequestSpec]`.
  - `advanced_filter(filters: EnumFilters | None) -> str | None`.
  - `fields_for_plan(plan: QueryPlan) -> list[str]`.
  - `tests.factories.make_study(...) -> dict` and `tests.factories.make_plan(**overrides) -> QueryPlan`.

- [ ] **Step 1: Write `tests/factories.py`**, which builds readable fake API records (used by every later unit test):

```python
"""Readable builders for fake ClinicalTrials.gov records and QueryPlans (unit tests only)."""

from typing import Any

from ctviz.schemas.plan import QueryPlan


def make_study(
    nct_id: str = "NCT00000001",
    *,
    phases: list[str] | None = None,
    study_type: str = "INTERVENTIONAL",
    start: str | None = None,
    start_type: str | None = "ACTUAL",
    status: str = "COMPLETED",
    sponsor: str = "Merck Sharp & Dohme LLC",
    sponsor_class: str = "INDUSTRY",
    interventions: list[dict[str, Any]] | None = None,
    arms: list[dict[str, Any]] | None = None,
    conditions: list[str] | None = None,
    locations: list[dict[str, Any]] | None = None,
    enrollment: int | None = None,
    enrollment_type: str = "ACTUAL",
    mesh_interventions: list[str] | None = None,
) -> dict[str, Any]:
    """Build a raw study shaped exactly like GET /studies output; omitted parts stay absent."""
    design: dict[str, Any] = {"studyType": study_type}
    if phases is not None:
        design["phases"] = phases
    if enrollment is not None:
        design["enrollmentInfo"] = {"count": enrollment, "type": enrollment_type}
    status_module: dict[str, Any] = {"overallStatus": status}
    if start is not None:
        status_module["startDateStruct"] = {"date": start, **({"type": start_type} if start_type else {})}
    protocol: dict[str, Any] = {
        "identificationModule": {"nctId": nct_id, "briefTitle": f"Study {nct_id}"},
        "statusModule": status_module,
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor, "class": sponsor_class}},
        "designModule": design,
    }
    if interventions is not None or arms is not None:
        protocol["armsInterventionsModule"] = {
            "interventions": interventions or [], "armGroups": arms or []}
    if conditions is not None:
        protocol["conditionsModule"] = {"conditions": conditions}
    if locations is not None:
        protocol["contactsLocationsModule"] = {"locations": locations}
    study: dict[str, Any] = {"protocolSection": protocol}
    if mesh_interventions is not None:
        study["derivedSection"] = {"interventionBrowseModule": {
            "meshes": [{"id": f"D{i:06d}", "term": t} for i, t in enumerate(mesh_interventions)]}}
    return study


def make_plan(**overrides: Any) -> QueryPlan:
    """A valid answerable plan (pembrolizumab trials by phase) with selective overrides."""
    base: dict[str, Any] = {
        "answerable": True, "out_of_scope_reason": None, "suggested_reframing": None,
        "interpretation": "Pembrolizumab trials by phase.",
        "search_terms": [{"param": "query.intr", "value": "pembrolizumab",
                          "source": "query_text", "rationale": "drug"}],
        "filters": None, "comparison": None,
        "analysis": {"kind": "count_by", "group_by": "phase", "series_by": None,
                     "phase_mode": "combined", "time_field": None, "granularity": None,
                     "measure_x": None, "measure_y": None, "color_by": None,
                     "network_type": None, "top_n": None},
        "visualization": {"type": "bar_chart", "title": "Pembrolizumab trials by phase",
                          "rationale": "categorical distribution"},
        "assumptions": [],
    }
    return QueryPlan.model_validate(base | overrides)
```

- [ ] **Step 2: Write the failing compiler tests**

```python
from ctviz.ctgov.compiler import advanced_filter, compile_plan
from ctviz.schemas.plan import EnumFilters
from tests.factories import make_plan

NO_FILTERS = dict.fromkeys(EnumFilters.model_fields)


def test_single_cohort_plan_compiles_search_terms_as_given() -> None:
    [spec] = compile_plan(make_plan())

    assert spec.params["query.intr"] == "pembrolizumab"
    assert spec.params["pageSize"] == "1000"
    assert spec.params["countTotal"] == "true"
    assert "NCTId" in spec.params["fields"].split(",")


def test_multi_word_values_are_not_auto_quoted() -> None:
    plan = make_plan(search_terms=[{"param": "query.cond", "value": "breast cancer",
                                    "source": "query_text", "rationale": "condition"}])

    [spec] = compile_plan(plan)

    assert spec.params["query.cond"] == "breast cancer"


def test_filters_compile_to_the_verified_essie_expression() -> None:
    filters = EnumFilters(**NO_FILTERS | {"phases": ["PHASE3"], "start_year_min": 2015})

    assert advanced_filter(filters) == "AREA[Phase]PHASE3 AND AREA[StartDate]RANGE[2015-01-01,MAX]"


def test_status_filter_uses_the_typed_parameter() -> None:
    plan = make_plan(filters=NO_FILTERS | {"overall_statuses": ["RECRUITING", "COMPLETED"]})

    [spec] = compile_plan(plan)

    assert spec.params["filter.overallStatus"] == "RECRUITING,COMPLETED"


def test_comparison_produces_one_spec_per_cohort() -> None:
    plan = make_plan(search_terms=[],
                     comparison={"vary_param": "query.intr", "values": ["pembrolizumab", "nivolumab"]})

    specs = compile_plan(plan)

    assert [s.cohort_label for s in specs] == ["pembrolizumab", "nivolumab"]
    assert [s.params["query.intr"] for s in specs] == ["pembrolizumab", "nivolumab"]


def test_essie_control_syntax_in_values_is_stripped() -> None:
    plan = make_plan(search_terms=[{"param": "query.intr", "value": 'drug"]AREA[x',
                                    "source": "query_text", "rationale": "x"}])

    [spec] = compile_plan(plan)

    assert "[" not in spec.params["query.intr"] and '"' not in spec.params["query.intr"]
```

- [ ] **Step 3: Run the tests and confirm they fail.** Then implement `fields.py` and `compiler.py`:

`fields.py`:
```python
"""Which `fields=` pieces each analysis needs. The API projects leaf fields only (PLAN.md §5.5)."""

from ctviz.schemas.enums import Dimension, Measure, NetworkType, SearchParam
from ctviz.schemas.plan import QueryPlan

ALWAYS = ("NCTId", "BriefTitle", "StudyType", "OverallStatus", "StartDate", "StartDateType",
          "LastUpdatePostDate")
FOR_DIMENSION: dict[Dimension, tuple[str, ...]] = {
    Dimension.PHASE: ("Phase",),
    Dimension.OVERALL_STATUS: (),
    Dimension.STUDY_TYPE: (),
    Dimension.LEAD_SPONSOR_CLASS: ("LeadSponsorClass",),
    Dimension.LEAD_SPONSOR: ("LeadSponsorName",),
    Dimension.INTERVENTION_TYPE: ("InterventionType",),
    Dimension.INTERVENTION: ("InterventionName", "InterventionType"),
    Dimension.CONDITION: ("Condition",),
    Dimension.COUNTRY: ("LocationCountry", "LocationStatus"),
    Dimension.START_YEAR: (),
}
FOR_MEASURE: dict[Measure, tuple[str, ...]] = {
    Measure.ENROLLMENT: ("EnrollmentCount", "EnrollmentType"),
    Measure.DURATION_MONTHS: ("CompletionDate", "CompletionDateType"),
    Measure.SITE_COUNT: ("LocationFacility",),
}
FOR_NETWORK: dict[NetworkType, tuple[str, ...]] = {
    NetworkType.SPONSOR_DRUG: ("LeadSponsorName", "CollaboratorName", "InterventionName",
                               "InterventionType"),
    NetworkType.DRUG_DRUG: ("InterventionName", "InterventionType", "ArmGroupLabel",
                            "ArmGroupInterventionName"),
    NetworkType.CONDITION_DRUG: ("Condition", "InterventionName", "InterventionType"),
}
FOR_MATCH: dict[SearchParam, tuple[str, ...]] = {
    SearchParam.INTR: ("InterventionName", "InterventionOtherName", "ArmGroupInterventionName",
                       "InterventionMeshTerm"),
    SearchParam.LEAD: ("LeadSponsorName",),
    SearchParam.SPONS: ("LeadSponsorName", "CollaboratorName"),
    SearchParam.COND: ("Condition", "Keyword", "ConditionMeshTerm", "ConditionAncestorTerm"),
}
FOR_FILTER = ("Phase", "LeadSponsorClass", "InterventionType", "LocationCountry")


def fields_for_plan(plan: QueryPlan) -> list[str]:
    """Union of every leaf the plan's aggregation, evidence and filter re-checks will read."""
    pieces: list[str] = [*ALWAYS, *FOR_FILTER]
    analysis = plan.analysis
    if analysis is not None:
        for dim in (analysis.group_by, analysis.series_by, analysis.color_by):
            pieces += FOR_DIMENSION.get(dim, ()) if dim else ()
        for measure in (analysis.measure_x, analysis.measure_y):
            pieces += FOR_MEASURE.get(measure, ()) if measure else ()
        pieces += FOR_NETWORK.get(analysis.network_type, ()) if analysis.network_type else ()
    params = [t.param for t in plan.search_terms]
    params += [plan.comparison.vary_param] if plan.comparison else []
    for param in params:
        pieces += FOR_MATCH.get(param, ())
    return list(dict.fromkeys(pieces))  # de-duplicate, keep order
```

`compiler.py`:
```python
"""QueryPlan → ClinicalTrials.gov request params, one RequestSpec per cohort. Pure and total."""

import re
from dataclasses import dataclass

from ctviz.config import PAGE_SIZE
from ctviz.ctgov.fields import fields_for_plan
from ctviz.schemas.plan import EnumFilters, QueryPlan

_UNSAFE = re.compile(r'["\[\]\x00-\x1f]')
MAX_VALUE_LENGTH = 120


@dataclass(frozen=True)
class RequestSpec:
    cohort_label: str
    cohort_value: str | None
    params: dict[str, str]


def _clean(value: str) -> str:
    return _UNSAFE.sub(" ", value).strip()[:MAX_VALUE_LENGTH]


def _any_of(area: str, values: list[str]) -> str:
    joined = " OR ".join(f'AREA[{area}]{v}' for v in values)
    return f"({joined})" if len(values) > 1 else joined


def _year_range(low: int | None, high: int | None) -> str:
    start = f"{low}-01-01" if low else "MIN"
    end = f"{high}-12-31" if high else "MAX"
    return f"AREA[StartDate]RANGE[{start},{end}]"


def advanced_filter(filters: EnumFilters | None) -> str | None:
    """Build the filter.advanced Essie expression; the LLM never writes this string."""
    if filters is None:
        return None
    clauses: list[str] = []
    if filters.phases:
        clauses.append(_any_of("Phase", list(filters.phases)))
    if filters.study_types:
        clauses.append(_any_of("StudyType", list(filters.study_types)))
    if filters.intervention_types:
        clauses.append(_any_of("InterventionType", list(filters.intervention_types)))
    if filters.lead_sponsor_classes:
        clauses.append(_any_of("LeadSponsorClass", list(filters.lead_sponsor_classes)))
    if filters.countries:
        clauses.append(_any_of("LocationCountry", [f'"{c}"' for c in filters.countries]))
    if filters.start_year_min or filters.start_year_max:
        clauses.append(_year_range(filters.start_year_min, filters.start_year_max))
    return " AND ".join(clauses) or None


def _base_params(plan: QueryPlan) -> dict[str, str]:
    params = {"pageSize": str(PAGE_SIZE), "countTotal": "true", "format": "json",
              "fields": ",".join(fields_for_plan(plan))}
    params |= {t.param.value: _clean(t.value) for t in plan.search_terms}
    expression = advanced_filter(plan.filters)
    if expression:
        params["filter.advanced"] = expression
    if plan.filters and plan.filters.overall_statuses:
        params["filter.overallStatus"] = ",".join(plan.filters.overall_statuses)
    if plan.filters and plan.filters.nct_ids:
        params["filter.ids"] = ",".join(plan.filters.nct_ids)
    return params


def compile_plan(plan: QueryPlan) -> list[RequestSpec]:
    """One RequestSpec per cohort: a single cohort, or one per compared value."""
    base = _base_params(plan)
    if plan.comparison is None:
        label = plan.search_terms[0].value if plan.search_terms else "All trials"
        return [RequestSpec(label, None, base)]
    key = plan.comparison.vary_param.value
    return [RequestSpec(v, v, base | {key: _clean(v)}) for v in plan.comparison.values]
```

- [ ] **Step 4: Run the tests and confirm they pass, then `make lint`.** Expected: 6 passed.

### Task 2.2: Async client (probe, paginate, truncate, retry, cache)

**Files:**
- Create: `src/ctviz/ctgov/client.py`
- Test: `tests/unit/ctgov/test_client.py`

**Interfaces:**
- Consumes: `RequestSpec`, `UpstreamError`, and constants from `config`.
- Produces:
  - `FetchResult(records: list[dict], api_total_count: int, truncated: bool, truncation_rule: str | None, requests: list[RequestLog])`.
  - `RequestLog(url: str, fetched_at: str, records: int)`.
  - `class CtGovClient` with `async probe(params) -> int`, `async fetch_all(params, max_records) -> FetchResult`, `async get_study(nct_id) -> dict`, `async version() -> dict`, and `aclose()`.

- [ ] **Step 1: Write the failing tests** (respx mocks httpx, so there's no network)

```python
import httpx
import pytest
import respx

from ctviz.config import CTGOV_BASE_URL
from ctviz.ctgov.client import CtGovClient
from ctviz.errors import UpstreamError

STUDIES = f"{CTGOV_BASE_URL}/studies"


def _page(ids: range, token: str | None, total: int | None = None) -> dict:
    body: dict = {"studies": [{"protocolSection": {"identificationModule": {"nctId": f"NCT{i:08d}"}}}
                              for i in ids]}
    if token:
        body["nextPageToken"] = token
    if total is not None:
        body["totalCount"] = total
    return body


@respx.mock
async def test_probe_returns_total_count() -> None:
    respx.get(STUDIES).mock(return_value=httpx.Response(200, json=_page(range(1), None, total=2960)))

    async with CtGovClient() as client:
        assert await client.probe({"query.intr": "pembrolizumab"}) == 2960


@respx.mock
async def test_fetch_all_follows_page_tokens() -> None:
    route = respx.get(STUDIES).mock(side_effect=[
        httpx.Response(200, json=_page(range(0, 2), "t1", total=3)),
        httpx.Response(200, json=_page(range(2, 3), None)),
    ])

    async with CtGovClient() as client:
        result = await client.fetch_all({"query.intr": "x"}, max_records=100)

    assert len(result.records) == 3 and result.api_total_count == 3
    assert result.truncated is False
    assert route.calls[1].request.url.params["pageToken"] == "t1"


@respx.mock
async def test_fetch_all_truncates_and_sorts_when_over_cap() -> None:
    route = respx.get(STUDIES).mock(side_effect=[
        httpx.Response(200, json=_page(range(0, 1), None, total=50_000)),  # probe
        httpx.Response(200, json=_page(range(0, 2), "t1", total=50_000)),
        httpx.Response(200, json=_page(range(2, 4), "t2")),
    ])

    async with CtGovClient() as client:
        result = await client.fetch_all({"query.cond": "oncology"}, max_records=3)

    assert len(result.records) == 3
    assert result.truncated is True
    assert result.truncation_rule == "most recent 3 by start date"
    assert route.calls[1].request.url.params["sort"] == "StartDate:desc"


@respx.mock
async def test_client_raises_upstream_error_with_text_body() -> None:
    respx.get(STUDIES).mock(return_value=httpx.Response(
        400, text="Parameter 'fields' contains invalid field name: 'Nope'"))

    async with CtGovClient() as client:
        with pytest.raises(UpstreamError, match="invalid field name") as info:
            await client.probe({"fields": "Nope"})

    assert info.value.status_code == 400


@respx.mock
async def test_client_retries_transient_5xx_then_succeeds() -> None:
    respx.get(STUDIES).mock(side_effect=[
        httpx.Response(503, text="busy"),
        httpx.Response(200, json=_page(range(1), None, total=1)),
    ])

    async with CtGovClient(backoff_base_s=0) as client:
        assert await client.probe({}) == 1
```

- [ ] **Step 2: Run the tests and confirm they fail.** Then implement `client.py`:

```python
"""Async ClinicalTrials.gov client: probe counts, fetch all pages (with a cap), retry, cache."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

import httpx

from ctviz.config import (
    CACHE_TTL_S, CTGOV_BASE_URL, HTTP_CONNECT_TIMEOUT_S, HTTP_RETRIES, HTTP_TIMEOUT_S, PAGE_SIZE,
)
from ctviz.errors import UpstreamError

log = logging.getLogger(__name__)
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
TRUNCATION_SORT = "StartDate:desc"


@dataclass(frozen=True)
class RequestLog:
    url: str
    fetched_at: str
    records: int


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, Any]]
    api_total_count: int
    truncated: bool
    truncation_rule: str | None
    requests: list[RequestLog] = field(default_factory=list)


class CtGovClient:
    """Thin, well-behaved wrapper over GET /studies; all errors surface as UpstreamError."""

    def __init__(self, base_url: str = CTGOV_BASE_URL, backoff_base_s: float = 0.5) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(HTTP_TIMEOUT_S, connect=HTTP_CONNECT_TIMEOUT_S),
        )
        self._backoff_base_s = backoff_base_s
        self._cache: dict[tuple[tuple[str, str], ...], tuple[float, dict[str, Any]]] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: type[BaseException] | BaseException | TracebackType | None) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, str]) -> tuple[dict[str, Any], str]:
        key = (path, *sorted(params.items()))
        cached = self._cache.get(key)
        now = asyncio.get_running_loop().time()
        if cached and now - cached[0] < CACHE_TTL_S:
            return cached[1], "cache"
        body, url = await self._get_with_retries(path, params)
        self._cache[key] = (now, body)
        return body, url

    async def _get_with_retries(self, path: str, params: dict[str, str]) -> tuple[dict[str, Any], str]:
        for attempt in range(HTTP_RETRIES):
            try:
                response = await self._http.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt == HTTP_RETRIES - 1:
                    raise UpstreamError(f"ClinicalTrials.gov unreachable: {exc}") from exc
            else:
                if response.status_code == 200:
                    return response.json(), str(response.url)
                if response.status_code not in RETRYABLE_STATUS or attempt == HTTP_RETRIES - 1:
                    raise UpstreamError(response.text.strip()[:300], response.status_code)
            await asyncio.sleep(self._backoff_base_s * 2**attempt)
        raise UpstreamError("retries exhausted")  # unreachable; keeps mypy happy

    async def probe(self, params: dict[str, str]) -> int:
        """Total matching trials for these params, using a 1-record request."""
        body, _ = await self._get("/studies", params | {"pageSize": "1", "countTotal": "true",
                                                         "fields": "NCTId"})
        return int(body.get("totalCount", 0))

    async def fetch_all(self, params: dict[str, str], max_records: int) -> FetchResult:
        """Every page up to max_records; over the cap, the most recent trials by start date."""
        total = await self.probe(params)
        truncated = total > max_records
        page_params = params | {"pageSize": str(min(PAGE_SIZE, max_records))}
        if truncated:
            page_params["sort"] = TRUNCATION_SORT
        records: list[dict[str, Any]] = []
        logs: list[RequestLog] = []
        token: str | None = None
        while len(records) < max_records:
            body, url = await self._get("/studies", page_params | ({"pageToken": token} if token else {}))
            page = body.get("studies", [])
            records += page
            logs.append(RequestLog(url, datetime.now(UTC).isoformat(), len(page)))
            token = body.get("nextPageToken")
            if not token:
                break
        rule = f"most recent {max_records} by start date" if truncated else None
        return FetchResult(records[:max_records], total, truncated, rule, logs)

    async def get_study(self, nct_id: str) -> dict[str, Any]:
        body, _ = await self._get(f"/studies/{nct_id}", {})
        return body

    async def version(self) -> dict[str, Any]:
        body, _ = await self._get("/version", {})
        return body
```

Note: in the truncation test, the probe uses `pageSize=1` and the page requests use `pageSize=3`, so the three mocked responses line up in order.

- [ ] **Step 3: Run the tests and confirm they pass, then `make lint`.** Expected: 5 passed.

### Task 2.3: Normalizer (raw study → Trial)

**Files:**
- Create: `src/ctviz/ctgov/normalize.py`
- Test: `tests/unit/ctgov/test_normalize.py`

**Interfaces:**
- Produces:
  - `PartialDate(year: int, month: int | None, day: int | None, raw: str, type: str | None)`.
  - `Intervention(index, name, type, other_names: tuple[str, ...], arm_labels: tuple[str, ...])`.
  - `Site(index, facility, country, status)`.
  - `Trial(nct_id, title, phases: tuple[str, ...] | None, phase_label, status, study_type, start, completion, enrollment, enrollment_type, lead_sponsor, lead_sponsor_class, interventions, conditions, sites, raw)`.
  - `normalize(raw: dict) -> Trial`, `phase_label(phases, study_type) -> str`, `PHASE_DISPLAY_ORDER: tuple[str, ...]`, `NON_INTERVENTIONAL = "Non-interventional"`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from ctviz.ctgov.normalize import NON_INTERVENTIONAL, normalize, parse_partial_date, phase_label
from tests.factories import make_study


@pytest.mark.parametrize(
    ("phases", "study_type", "expected"),
    [
        (["PHASE3"], "INTERVENTIONAL", "Phase 3"),
        (["PHASE2", "PHASE1"], "INTERVENTIONAL", "Phase 1/Phase 2"),
        (["NA"], "INTERVENTIONAL", "Phase N/A"),
        (["EARLY_PHASE1"], "INTERVENTIONAL", "Early Phase 1"),
        (None, "OBSERVATIONAL", NON_INTERVENTIONAL),
        (None, "EXPANDED_ACCESS", NON_INTERVENTIONAL),
        (None, "INTERVENTIONAL", "Phase not reported"),
    ],
)
def test_phase_label_uses_combined_buckets(phases, study_type, expected) -> None:
    assert phase_label(phases, study_type) == expected


@pytest.mark.parametrize(("raw", "year", "month"), [("2019", 2019, None), ("2019-06", 2019, 6),
                                                    ("2019-06-12", 2019, 6)])
def test_partial_dates_keep_their_precision(raw: str, year: int, month: int | None) -> None:
    parsed = parse_partial_date(raw, "ACTUAL")

    assert (parsed.year, parsed.month, parsed.raw) == (year, month, raw)


def test_normalize_handles_missing_sections() -> None:
    raw = make_study("NCT00000009", phases=None, study_type="OBSERVATIONAL", start=None)

    trial = normalize(raw)

    assert trial.phase_label == NON_INTERVENTIONAL
    assert trial.start is None
    assert trial.interventions == () and trial.sites == () and trial.conditions == ()
    assert trial.raw is raw  # raw kept for evidence + verification (never mutated)


def test_normalize_keeps_array_indices_for_evidence() -> None:
    raw = make_study(interventions=[{"type": "DRUG", "name": "Placebo"},
                                    {"type": "DRUG", "name": "Pembrolizumab",
                                     "otherNames": ["MK-3475"]}],
                     locations=[{"country": "Japan", "status": "RECRUITING"}])

    trial = normalize(raw)

    assert trial.interventions[1].index == 1
    assert trial.interventions[1].other_names == ("MK-3475",)
    assert trial.sites[0].country == "Japan"
```

- [ ] **Step 2: Run the tests and confirm they fail.** Then implement `normalize.py`:

```python
"""Raw ClinicalTrials.gov study → Trial. Every rule here is from PLAN.md §10.3 (measured data)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PHASE_ORDER = ("EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4")
PHASE_NAMES = {"EARLY_PHASE1": "Early Phase 1", "PHASE1": "Phase 1", "PHASE2": "Phase 2",
               "PHASE3": "Phase 3", "PHASE4": "Phase 4"}
NON_INTERVENTIONAL = "Non-interventional"
PHASE_NA = "Phase N/A"
PHASE_NOT_REPORTED = "Phase not reported"
PHASE_DISPLAY_ORDER = ("Early Phase 1", "Phase 1", "Phase 1/Phase 2", "Phase 2",
                       "Phase 2/Phase 3", "Phase 3", "Phase 4", PHASE_NA, NON_INTERVENTIONAL,
                       PHASE_NOT_REPORTED)


@dataclass(frozen=True)
class PartialDate:
    year: int
    month: int | None
    day: int | None
    raw: str
    type: str | None  # ACTUAL, ESTIMATED, or None (legacy, untyped)


@dataclass(frozen=True)
class Intervention:
    index: int
    name: str
    type: str | None
    other_names: tuple[str, ...]
    arm_labels: tuple[str, ...]


@dataclass(frozen=True)
class Site:
    index: int
    facility: str | None
    country: str | None
    status: str | None


@dataclass(frozen=True)
class Trial:
    nct_id: str
    title: str
    phases: tuple[str, ...] | None
    phase_label: str
    status: str | None
    study_type: str | None
    start: PartialDate | None
    completion: PartialDate | None
    enrollment: int | None
    enrollment_type: str | None
    lead_sponsor: str | None
    lead_sponsor_class: str | None
    interventions: tuple[Intervention, ...]
    conditions: tuple[str, ...]
    sites: tuple[Site, ...]
    raw: Mapping[str, Any]


def phase_label(phases: list[str] | tuple[str, ...] | None, study_type: str | None) -> str:
    """One bucket per trial (Q4 = combined): 'Phase 1/Phase 2', 'Phase N/A', 'Non-interventional'."""
    if not phases:
        return PHASE_NOT_REPORTED if study_type == "INTERVENTIONAL" else NON_INTERVENTIONAL
    real = sorted((p for p in phases if p != "NA"), key=PHASE_ORDER.index)
    return "/".join(PHASE_NAMES[p] for p in real) if real else PHASE_NA


def parse_partial_date(raw: str, date_type: str | None) -> PartialDate:
    """Parse 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' without inventing precision."""
    parts = [int(p) for p in raw.split("-")]
    return PartialDate(parts[0], parts[1] if len(parts) > 1 else None,
                       parts[2] if len(parts) > 2 else None, raw, date_type)


def _date(struct: Mapping[str, Any] | None) -> PartialDate | None:
    if not struct or "date" not in struct:
        return None
    return parse_partial_date(struct["date"], struct.get("type"))


def normalize(raw: Mapping[str, Any]) -> Trial:
    """Flatten the fields we analyze, keeping array indices so evidence pointers stay exact."""
    protocol = raw.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    sponsor = protocol.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
    arms_module = protocol.get("armsInterventionsModule", {})
    enrollment = design.get("enrollmentInfo", {})
    phases = design.get("phases")
    return Trial(
        nct_id=ident["nctId"],
        title=ident.get("briefTitle", ""),
        phases=tuple(phases) if phases else None,
        phase_label=phase_label(phases, design.get("studyType")),
        status=status.get("overallStatus"),
        study_type=design.get("studyType"),
        start=_date(status.get("startDateStruct")),
        completion=_date(status.get("completionDateStruct")),
        enrollment=enrollment.get("count"),
        enrollment_type=enrollment.get("type"),
        lead_sponsor=sponsor.get("name"),
        lead_sponsor_class=sponsor.get("class"),
        interventions=tuple(
            Intervention(i, item.get("name", ""), item.get("type"),
                         tuple(item.get("otherNames", [])), tuple(item.get("armGroupLabels", [])))
            for i, item in enumerate(arms_module.get("interventions", []))),
        conditions=tuple(protocol.get("conditionsModule", {}).get("conditions", [])),
        sites=tuple(
            Site(i, loc.get("facility"), loc.get("country"), loc.get("status"))
            for i, loc in enumerate(protocol.get("contactsLocationsModule", {}).get("locations", []))),
        raw=raw,
    )
```

- [ ] **Step 3: Run the tests and confirm they pass, then `make lint`.**

### Task 2.4: Recorded fixtures + measurements

**Files:**
- Create: `scripts/record_fixtures.py`, `tests/fixtures/ctgov/*.json.gz`, `tests/fixtures/__init__.py`, `tests/fixtures/load.py`
- Test: `tests/integration/test_fixtures_load.py`

**Interfaces:**
- Produces: `tests.fixtures.load.load_fixture(name: str) -> list[dict]`. Fixture names: `pembrolizumab`, `nivolumab`, `ms_recruiting`, `glioblastoma`, `psoriasis_p2`, `crohns_p3_completed`, `nsclc`.

- [ ] **Step 1: Write `scripts/record_fixtures.py`.**
  - For each named query in a `QUERIES` dict (the params from PLAN.md §14, unquoted), it calls `CtGovClient().fetch_all(params, max_records=20_000)`.
  - It writes each result as gzipped JSON `{"total": …, "fetched_at": …, "records": […]}`.
  - It uses the union of all `fields.py` pieces as `fields=` so the fixtures serve every analysis.

- [ ] **Step 2: Run it once:** `uv run python scripts/record_fixtures.py`. Expected: 7 files, a few MB in total.

- [ ] **Step 3: Write the fixture test.** It records the golden totals we'll assert on later.

```python
from tests.fixtures.load import load_fixture


def test_recorded_fixtures_have_the_expected_totals() -> None:
    assert len(load_fixture("pembrolizumab")) == 2960
    assert len(load_fixture("ms_recruiting")) == 430
    assert len(load_fixture("psoriasis_p2")) == 512
```

If the live totals have drifted since 2026-09-28, update the expected numbers to the recorded ones and note the drift in DEVLOG.

- [ ] **Step 4: Measure two open questions from the spec, and log both in DEVLOG:**
  1. The condition strict-match rate on `glioblastoma`, `ms_recruiting` and `psoriasis_p2`: the share of records whose conditions, keywords or MeSH terms contain the searched term. If it's ≥ 97%, set `CONDITIONS_STRICT = True` in `config.py` (Q1).
  2. Wall time of the full pembrolizumab fetch with the real field set.

### ✅ Checkpoint S2 — you verify

- **I show you:**
  - the compiled params for 3 real questions (pembro by year; MS recruiting countries; pembro vs nivo);
  - a `normalize()` of one real record, printed;
  - the measurement numbers.
- **You check:**
  1. `compiler.py` never passes LLM text into `fields` or Essie syntax.
  2. `normalize.py` rules match your expectations for phases and dates.
  3. The fixture sizes are reasonable.
- **Then:** commit `feat: ClinicalTrials.gov compiler, client, normalizer, fixtures`.

---

## Stage S3 — Core analysis with evidence (~2.5 h)

**Stage done when:** phase, year, status and sponsor-class charts can be computed from fixtures, with a citation for every trial, and `bar_chart`/`time_series` specs validate.

### Task 3.1: Name normalization and JSON Pointers

**Files:**
- Create: `src/ctviz/common/__init__.py`, `src/ctviz/common/names.py`, `src/ctviz/citations/__init__.py`, `src/ctviz/citations/pointer.py`
- Test: `tests/unit/common/test_names.py`, `tests/unit/citations/test_pointer.py`

**Interfaces:**
- Produces:
  - `normalize_text(text) -> str`, `text_matches(text: str | list[str], term: str) -> bool`, `normalize_drug(raw) -> str | None`.
  - `build_pointer(*tokens: str | int) -> str`, `resolve_pointer(doc, pointer) -> Any`, `json_text(value) -> str`.
  - Path constants in `pointer.py`: `PHASES`, `STUDY_TYPE`, `START_DATE`, `OVERALL_STATUS`, `LEAD_SPONSOR_NAME`, `LEAD_SPONSOR_CLASS`, `INTERVENTIONS`, `ARM_GROUPS`, `CONDITIONS`, `LOCATIONS`, `ENROLLMENT_COUNT`, `ENROLLMENT_TYPE`, `COMPLETION_DATE`, `NCT_ID`, `INTERVENTION_MESH`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from ctviz.citations.pointer import build_pointer, json_text, resolve_pointer
from ctviz.common.names import normalize_drug, text_matches


@pytest.mark.parametrize(
    ("raw", "key"),
    [("Temozolomide (TMZ)", "temozolomide"), ("temozolomide 60 mg x 21 days", "temozolomide"),
     ("Ipilimumab 3mg/kg", "ipilimumab"), ("Drug: Lenvatinib Oral Product", "lenvatinib oral product"),
     ("Placebo", None), ("Standard of care", None)],
)
def test_normalize_drug(raw: str, key: str | None) -> None:
    assert normalize_drug(raw) == key


def test_text_matches_is_case_and_space_insensitive_and_accepts_lists() -> None:
    assert text_matches("Pembrolizumab  Injection", "pembrolizumab injection")
    assert text_matches(["Keytruda", "MK-3475"], "mk-3475")
    assert not text_matches("Nivolumab", "pembrolizumab")


def test_pointer_round_trip_with_rfc6901_escaping() -> None:
    doc = {"a/b": {"c~d": [10, {"e": "x"}]}}
    pointer = build_pointer("a/b", "c~d", 1, "e")

    assert pointer == "/a~1b/c~0d/1/e"
    assert resolve_pointer(doc, pointer) == "x"


def test_resolve_pointer_raises_on_missing_paths() -> None:
    with pytest.raises((KeyError, IndexError)):
        resolve_pointer({"a": [1]}, "/a/5")


@pytest.mark.parametrize(("value", "text"), [(84, "84"), ("PHASE3", "PHASE3"), (True, "true")])
def test_json_text_renders_scalars_like_the_api(value: object, text: str) -> None:
    assert json_text(value) == text
```

- [ ] **Step 2: Run the tests and confirm they fail.** Then implement both modules:

```python
"""Name/text normalization shared by aggregation and verification (so both apply one rule)."""

import re

_PREFIX = re.compile(r"^(drug|biological|combination product|genetic)\s*:\s*", re.IGNORECASE)
_BRACKETED = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_DOSE = re.compile(r"\s*\b\d[\d,.]*\s*(mg/m2|mg/kg|mg/day|mg|mcg|µg|g|ml|iu|units?)\b.*$",
                   re.IGNORECASE)
_NOT_A_DRUG = frozenset({"placebo", "standard of care", "best supportive care", "soc", "saline",
                         "normal saline"})


def normalize_text(text: str) -> str:
    """Casefold and collapse whitespace."""
    return " ".join(text.casefold().split())


def text_matches(text: str | list[str], term: str) -> bool:
    """True if the normalized term occurs in the text (or in any item of a list)."""
    needle = normalize_text(term)
    items = text if isinstance(text, list) else [text]
    return any(needle in normalize_text(item) for item in items if isinstance(item, str))


def normalize_drug(raw: str) -> str | None:
    """Canonical drug key: strip type prefix, brackets and doses; None for placebo/SOC."""
    text = _PREFIX.sub("", raw.strip())
    text = _BRACKETED.sub("", text)
    text = _DOSE.sub("", text)
    key = normalize_text(text)
    if not key or key in _NOT_A_DRUG or key.startswith("placebo"):
        return None
    return key
```

```python
"""RFC 6901 JSON Pointers: how every citation addresses its evidence in a raw study record."""

import json
from typing import Any

PHASES = "/protocolSection/designModule/phases"
STUDY_TYPE = "/protocolSection/designModule/studyType"
ENROLLMENT_COUNT = "/protocolSection/designModule/enrollmentInfo/count"
ENROLLMENT_TYPE = "/protocolSection/designModule/enrollmentInfo/type"
NCT_ID = "/protocolSection/identificationModule/nctId"
OVERALL_STATUS = "/protocolSection/statusModule/overallStatus"
START_DATE = "/protocolSection/statusModule/startDateStruct/date"
COMPLETION_DATE = "/protocolSection/statusModule/completionDateStruct/date"
LEAD_SPONSOR_NAME = "/protocolSection/sponsorCollaboratorsModule/leadSponsor/name"
LEAD_SPONSOR_CLASS = "/protocolSection/sponsorCollaboratorsModule/leadSponsor/class"
INTERVENTIONS = "/protocolSection/armsInterventionsModule/interventions"
ARM_GROUPS = "/protocolSection/armsInterventionsModule/armGroups"
CONDITIONS = "/protocolSection/conditionsModule/conditions"
LOCATIONS = "/protocolSection/contactsLocationsModule/locations"
INTERVENTION_MESH = "/derivedSection/interventionBrowseModule/meshes"


def _escape(token: str | int) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def build_pointer(*tokens: str | int) -> str:
    """Join raw tokens into a pointer, escaping '~' and '/' per RFC 6901."""
    return "".join(f"/{_escape(t)}" for t in tokens)


def resolve_pointer(document: Any, pointer: str) -> Any:
    """Return the single value at `pointer`; raise KeyError/IndexError if it does not exist."""
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise KeyError(f"JSON Pointer must start with '/': {pointer!r}")
    node = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not token.isdigit():
                raise KeyError(f"expected an array index, got {token!r}")
            node = node[int(token)]
        elif isinstance(node, dict):
            node = node[token]
        else:
            raise KeyError(f"cannot descend into {type(node).__name__}")
    return node


def json_text(value: Any) -> str:
    """Render a scalar exactly as it appears in the API's JSON (so excerpts are exact text)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return json.dumps(value)
    if isinstance(value, str):
        return value
    raise TypeError(f"excerpts must be scalars, got {type(value).__name__}")
```

Pointer constants are complete paths, so evidence for element `i` is `f"{INTERVENTIONS}/{i}/name"`. Tests always compare against those constants.

- [ ] **Step 3: Run the tests and confirm they pass, then `make lint`.**

### Task 3.2: Dimension extractors (key + evidence + predicate)

**Files:**
- Create: `src/ctviz/analysis/__init__.py`, `src/ctviz/analysis/dimensions.py`
- Test: `tests/unit/analysis/test_dimensions.py`

**Interfaces:**
- Consumes: `Trial` (S2), `Evidence` (S1), pointer constants (3.1).
- Produces:
  - `DimensionHit(key: str, evidence: tuple[Evidence, ...], predicate: Predicate)`, frozen. `evidence[0]` is the primary bucket evidence.
  - `extract(trial: Trial, dimension: Dimension) -> list[DimensionHit]`. An empty list means the trial has no value for this dimension, which becomes an analysis exclusion.
  - `EXCLUSIVE_DIMENSIONS: frozenset[Dimension]`.

- [ ] **Step 1: Write the failing tests** (phase, year, status, class; the rest arrive in S5)

```python
from ctviz.analysis.dimensions import extract
from ctviz.citations.pointer import PHASES, START_DATE, STUDY_TYPE
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.enums import Dimension
from tests.factories import make_study


def test_multi_phase_trial_cites_each_array_element() -> None:
    trial = normalize(make_study(phases=["PHASE2", "PHASE3"]))

    [hit] = extract(trial, Dimension.PHASE)

    assert hit.key == "Phase 2/Phase 3"
    assert [(e.field, e.excerpt) for e in hit.evidence] == [
        (f"{PHASES}/0", "PHASE2"), (f"{PHASES}/1", "PHASE3")]
    assert hit.predicate == {"op": "set_equals", "path": PHASES, "value": ["PHASE2", "PHASE3"]}


def test_non_interventional_bucket_is_cited_by_study_type() -> None:
    trial = normalize(make_study(phases=None, study_type="OBSERVATIONAL"))

    [hit] = extract(trial, Dimension.PHASE)

    assert hit.key == "Non-interventional"
    assert (hit.evidence[0].field, hit.evidence[0].excerpt) == (STUDY_TYPE, "OBSERVATIONAL")


def test_start_year_uses_the_partial_date_as_is() -> None:
    [hit] = extract(normalize(make_study(start="2019-10")), Dimension.START_YEAR)

    assert (hit.key, hit.evidence[0].field, hit.evidence[0].excerpt) == ("2019", START_DATE, "2019-10")


def test_missing_start_date_yields_no_hit() -> None:
    assert extract(normalize(make_study(start=None)), Dimension.START_YEAR) == []
```

- [ ] **Step 2: Run the tests and confirm they fail.** Then implement `dimensions.py`. Use **one small function per dimension**, registered in a dict, which keeps each function short and the module open for extension:

```python
"""Per-dimension extractors: which bucket(s) a trial belongs to, with the exact evidence why."""

from collections.abc import Callable
from dataclasses import dataclass

from ctviz.citations import pointer as p
from ctviz.ctgov.normalize import NON_INTERVENTIONAL, PHASE_NOT_REPORTED, Trial
from ctviz.schemas.citations import Evidence, Predicate
from ctviz.schemas.enums import Dimension

EXCLUSIVE_DIMENSIONS = frozenset({Dimension.PHASE, Dimension.OVERALL_STATUS, Dimension.STUDY_TYPE,
                                  Dimension.LEAD_SPONSOR_CLASS, Dimension.LEAD_SPONSOR,
                                  Dimension.START_YEAR})
NON_INTERVENTIONAL_TYPES = ["OBSERVATIONAL", "EXPANDED_ACCESS"]


@dataclass(frozen=True)
class DimensionHit:
    key: str
    evidence: tuple[Evidence, ...]
    predicate: Predicate


def _bucket(field: str, excerpt: str) -> Evidence:
    return Evidence(role="bucket", field=field, excerpt=excerpt)


def _phase(trial: Trial) -> list[DimensionHit]:
    if trial.phases:
        evidence = tuple(_bucket(f"{p.PHASES}/{i}", v) for i, v in enumerate(trial.phases))
        predicate = {"op": "set_equals", "path": p.PHASES, "value": list(trial.phases)}
        return [DimensionHit(trial.phase_label, evidence, predicate)]
    if trial.study_type is None:
        return []
    evidence = (_bucket(p.STUDY_TYPE, trial.study_type),)
    types = NON_INTERVENTIONAL_TYPES if trial.phase_label == NON_INTERVENTIONAL else ["INTERVENTIONAL"]
    predicate = {"all": [{"not": {"op": "exists", "path": p.PHASES}},
                         {"op": "in", "path": p.STUDY_TYPE, "value": types}]}
    return [DimensionHit(trial.phase_label, evidence, predicate)]


def _scalar(path: str, value: str | None) -> list[DimensionHit]:
    if value is None:
        return []
    return [DimensionHit(value, (_bucket(path, value),), {"op": "equals", "path": path, "value": value})]


def _start_year(trial: Trial) -> list[DimensionHit]:
    if trial.start is None:
        return []
    year = str(trial.start.year)
    return [DimensionHit(year, (_bucket(p.START_DATE, trial.start.raw),),
                         {"op": "year_equals", "path": p.START_DATE, "value": trial.start.year})]


_EXTRACTORS: dict[Dimension, Callable[[Trial], list[DimensionHit]]] = {
    Dimension.PHASE: _phase,
    Dimension.START_YEAR: _start_year,
    Dimension.OVERALL_STATUS: lambda t: _scalar(p.OVERALL_STATUS, t.status),
    Dimension.STUDY_TYPE: lambda t: _scalar(p.STUDY_TYPE, t.study_type),
    Dimension.LEAD_SPONSOR_CLASS: lambda t: _scalar(p.LEAD_SPONSOR_CLASS, t.lead_sponsor_class),
    Dimension.LEAD_SPONSOR: lambda t: _scalar(p.LEAD_SPONSOR_NAME, t.lead_sponsor),
}


def extract(trial: Trial, dimension: Dimension) -> list[DimensionHit]:
    """Bucket(s) for this trial; [] means the trial lacks the field (an analysis exclusion)."""
    return _EXTRACTORS[dimension](trial)


def register(dimension: Dimension, extractor: Callable[[Trial], list[DimensionHit]]) -> None:
    """Extension point used by S5 to add multi-valued dimensions without touching this module."""
    _EXTRACTORS[dimension] = extractor
```

Note: `PHASE_NOT_REPORTED` is imported so the label constant is shared; the "Phase not reported" case is covered by the `types = ["INTERVENTIONAL"]` branch.

- [ ] **Step 3: Run the tests and confirm they pass, then `make lint`.**

### Task 3.3: Aggregation (count_by + time_trend) with citations

**Files:**
- Create: `src/ctviz/analysis/aggregate.py`
- Test: `tests/unit/analysis/test_aggregate.py`, `tests/integration/test_aggregate_fixtures.py`

**Interfaces:**
- Consumes: `extract`, `DimensionHit`, `Trial`, `Citation`, `Evidence`, `PHASE_DISPLAY_ORDER`.
- Produces:
  - `MatchedTrial(trial: Trial, match_evidence: tuple[Evidence, ...])`, frozen.
  - `Bucket(key: str, predicate: Predicate, citations: tuple[Citation, ...], flags: tuple[str, ...] = ())`, with the property `trial_count == len(citations)`.
  - `AggregateResult(buckets: tuple[Bucket, ...], excluded: dict[str, str])`, where `excluded` maps nct_id → reason.
  - `count_by(trials, dimension, top_n=None) -> AggregateResult` and `time_trend(trials, today_year) -> AggregateResult`.

- [ ] **Step 1: Write the failing tests**

```python
from ctviz.analysis.aggregate import MatchedTrial, count_by, time_trend
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.enums import Dimension
from tests.factories import make_study


def _matched(*studies: dict) -> list[MatchedTrial]:
    return [MatchedTrial(normalize(s), ()) for s in studies]


def test_count_by_phase_counts_each_trial_once_in_combined_buckets() -> None:
    trials = _matched(make_study("NCT00000001", phases=["PHASE3"]),
                      make_study("NCT00000002", phases=["PHASE2", "PHASE3"]),
                      make_study("NCT00000003", phases=["PHASE3"]))

    result = count_by(trials, Dimension.PHASE)

    counts = {b.key: b.trial_count for b in result.buckets}
    assert counts == {"Phase 2/Phase 3": 1, "Phase 3": 2}
    assert sum(counts.values()) == len(trials)


def test_every_citation_belongs_to_its_bucket_trial() -> None:
    result = count_by(_matched(make_study("NCT00000001", phases=["PHASE1"])), Dimension.PHASE)

    [bucket] = result.buckets
    [citation] = bucket.citations
    assert (citation.nct_id, citation.excerpt) == ("NCT00000001", "PHASE1")


def test_top_n_rolls_the_rest_into_a_cited_other_row() -> None:
    trials = _matched(*[make_study(f"NCT0000000{i}", sponsor=f"Sponsor {i}") for i in range(5)])

    result = count_by(trials, Dimension.LEAD_SPONSOR, top_n=3)

    assert [b.key for b in result.buckets][-1] == "Other (2 categories)"
    assert result.buckets[-1].trial_count == 2
    assert "not" in result.buckets[-1].predicate


def test_time_trend_fills_gaps_and_flags_partial_and_projected_years() -> None:
    trials = _matched(make_study("NCT00000001", start="2024-01"),
                      make_study("NCT00000002", start="2026-03"),
                      make_study("NCT00000003", start="2027-01", start_type="ESTIMATED"),
                      make_study("NCT00000004", start=None))

    result = time_trend(trials, today_year=2026)

    assert [(b.key, b.trial_count) for b in result.buckets] == [
        ("2024", 1), ("2025", 0), ("2026", 1), ("2027", 1)]
    assert result.buckets[2].flags == ("partial_period",)
    assert result.buckets[3].flags == ("projected",)
    assert result.excluded == {"NCT00000004": "missing_start_date"}
```

Integration test (golden number from the spec):
```python
from ctviz.analysis.aggregate import MatchedTrial, count_by
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.enums import Dimension
from tests.fixtures.load import load_fixture


def test_pembrolizumab_phase_buckets_sum_to_all_fetched_trials() -> None:
    trials = [MatchedTrial(normalize(r), ()) for r in load_fixture("pembrolizumab")]

    result = count_by(trials, Dimension.PHASE)

    assert sum(b.trial_count for b in result.buckets) == len(trials)
    assert {b.key: b.trial_count for b in result.buckets}["Phase 3"] == 324
```

- [ ] **Step 2: Run the tests and confirm they fail.** Then implement `aggregate.py`:

```python
"""Aggregation where counting and citing are one step: a trial joins a bucket with its evidence."""

from collections import defaultdict
from dataclasses import dataclass

from ctviz.analysis.dimensions import DimensionHit, extract
from ctviz.ctgov.normalize import PHASE_DISPLAY_ORDER, Trial
from ctviz.schemas.citations import Citation, Evidence, Predicate
from ctviz.schemas.enums import Dimension

MISSING_REASON = {Dimension.START_YEAR: "missing_start_date"}


@dataclass(frozen=True)
class MatchedTrial:
    trial: Trial
    match_evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Bucket:
    key: str
    predicate: Predicate
    citations: tuple[Citation, ...]
    flags: tuple[str, ...] = ()

    @property
    def trial_count(self) -> int:
        return len(self.citations)


@dataclass(frozen=True)
class AggregateResult:
    buckets: tuple[Bucket, ...]
    excluded: dict[str, str]


def _citation(matched: MatchedTrial, hit: DimensionHit) -> Citation:
    primary, *extra = hit.evidence
    return Citation(nct_id=matched.trial.nct_id, field=primary.field, excerpt=primary.excerpt,
                    evidence=[*extra, *matched.match_evidence])


def _group(trials: list[MatchedTrial], dimension: Dimension) -> tuple[dict[str, list[Citation]],
                                                                     dict[str, Predicate],
                                                                     dict[str, str]]:
    citations: dict[str, list[Citation]] = defaultdict(list)
    predicates: dict[str, Predicate] = {}
    excluded: dict[str, str] = {}
    for matched in trials:
        hits = extract(matched.trial, dimension)
        if not hits:
            excluded[matched.trial.nct_id] = MISSING_REASON.get(dimension, f"missing_{dimension}")
        for hit in hits:
            citations[hit.key].append(_citation(matched, hit))
            predicates.setdefault(hit.key, hit.predicate)
    return citations, predicates, excluded


def _order(keys: list[str], counts: dict[str, int], dimension: Dimension) -> list[str]:
    if dimension is Dimension.PHASE:
        return sorted(keys, key=lambda k: PHASE_DISPLAY_ORDER.index(k))
    return sorted(keys, key=lambda k: (-counts[k], k))


def count_by(trials: list[MatchedTrial], dimension: Dimension, top_n: int | None = None) -> AggregateResult:
    """Distinct trials per category, top-N plus a cited 'Other' row, in display order."""
    citations, predicates, excluded = _group(trials, dimension)
    counts = {k: len(v) for k, v in citations.items()}
    ordered = _order(list(citations), counts, dimension)
    keep, rest = (ordered[:top_n], ordered[top_n:]) if top_n else (ordered, [])
    buckets = [Bucket(k, predicates[k], tuple(citations[k])) for k in keep]
    if rest:
        other_predicate = {"not": {"any": [predicates[k] for k in keep]}}
        other_citations = tuple(c for k in rest for c in citations[k])
        buckets.append(Bucket(f"Other ({len(rest)} categories)", other_predicate, other_citations))
    return AggregateResult(tuple(buckets), excluded)


def time_trend(trials: list[MatchedTrial], today_year: int) -> AggregateResult:
    """Trials per start year, with empty years filled and current/future years flagged."""
    citations, predicates, excluded = _group(trials, Dimension.START_YEAR)
    if not citations:
        return AggregateResult((), excluded)
    years = range(min(map(int, citations)), max(map(int, citations)) + 1)
    buckets = []
    for year in years:
        key = str(year)
        flags = ("partial_period",) if year == today_year else ("projected",) if year > today_year else ()
        predicate = predicates.get(key, {"op": "year_equals", "path": "/protocolSection/statusModule/startDateStruct/date", "value": year})
        buckets.append(Bucket(key, predicate, tuple(citations.get(key, [])), flags))
    return AggregateResult(tuple(buckets), excluded)
```

(Replace the inline path with `pointer.START_DATE` in the implementation. It's shown inline here only for clarity.)

- [ ] **Step 3: Run the unit and integration tests and confirm they pass, then `make lint`.**

### Task 3.4: Visualization builder (bar_chart, grouped_bar_chart, time_series)

**Files:**
- Create: `src/ctviz/viz/__init__.py`, `src/ctviz/viz/builder.py`
- Test: `tests/unit/viz/test_builder.py`

**Interfaces:**
- Consumes: `Bucket`, `AggregateResult`, `Channel`, and the viz models.
- Produces:
  - `build_bar_chart(title, dimension_label: str, result) -> BarChart`.
  - `build_time_series(title, cohorts: dict[str, AggregateResult]) -> TimeSeries`.
  - `build_grouped_bar(title, dimension_label, cohorts: dict[str, AggregateResult]) -> GroupedBarChart`.
  - Rows always use the key names `category`/`year`/`cohort`/`trial_count`/`share`, and every row carries `predicate`, `citations` and `flags`.

- [ ] **Step 1: Write the failing tests**

```python
from ctviz.analysis.aggregate import AggregateResult, Bucket
from ctviz.schemas.citations import Citation
from ctviz.viz.builder import build_bar_chart, build_grouped_bar

CITE = Citation(nct_id="NCT00000001", field="/protocolSection/designModule/phases/0",
                excerpt="PHASE3", evidence=[])


def test_bar_chart_rows_are_named_by_encoding_and_carry_citations() -> None:
    result = AggregateResult((Bucket("Phase 3", {"op": "x"}, (CITE,)),), {})

    viz = build_bar_chart("Pembrolizumab trials by phase", "Phase", result)

    assert viz.encoding["x"].field == "category" and viz.encoding["y"].field == "trial_count"
    assert viz.data[0] == {"category": "Phase 3", "trial_count": 1, "flags": [],
                           "predicate": {"op": "x"}, "citations": [CITE]}


def test_grouped_bar_uses_share_when_cohorts_differ_more_than_twofold() -> None:
    big = AggregateResult((Bucket("INDUSTRY", {}, (CITE,) * 6),), {})
    small = AggregateResult((Bucket("INDUSTRY", {}, (CITE,) * 2),), {})

    viz = build_grouped_bar("Sponsor class", "Sponsor class", {"Lung cancer": big, "Melanoma": small})

    assert viz.encoding["y"].field == "share"
    assert {r["cohort"] for r in viz.data} == {"Lung cancer", "Melanoma"}
```

- [ ] **Step 2: Run the tests and confirm they fail.** Then implement `builder.py`, with small functions `_row(bucket, **keys)`, `build_bar_chart`, `build_time_series` (`x` temporal `year`, `color` = cohort when there's more than one cohort) and `build_grouped_bar`. Share normalization triggers when `max(size)/min(size) > 2`, where `size` counts the unique cited trials in a cohort. Rows keep `trial_count` alongside `share`.

```python
"""Aggregates → visualization models. Deterministic; the only text from the LLM is the title."""

from ctviz.analysis.aggregate import AggregateResult, Bucket
from ctviz.schemas.viz import BarChart, Channel, GroupedBarChart, Row, TimeSeries

SHARE_RATIO_THRESHOLD = 2.0


def _row(bucket: Bucket, **keys: object) -> Row:
    return {**keys, "trial_count": bucket.trial_count, "flags": list(bucket.flags),
            "predicate": bucket.predicate, "citations": list(bucket.citations)}


def _cohort_size(result: AggregateResult) -> int:
    return len({c.nct_id for b in result.buckets for c in b.citations})


def build_bar_chart(title: str, dimension_label: str, result: AggregateResult) -> BarChart:
    """One bar per bucket, in the aggregator's display order."""
    return BarChart(
        type="bar_chart", title=title,
        encoding={"x": Channel(field="category", type="nominal", title=dimension_label),
                  "y": Channel(field="trial_count", type="quantitative", title="Trials", unit="trials")},
        data=[_row(b, category=b.key) for b in result.buckets],
    )


def build_time_series(title: str, cohorts: dict[str, AggregateResult]) -> TimeSeries:
    """One point per (cohort, year); multi-line when there is more than one cohort."""
    encoding = {"x": Channel(field="year", type="temporal", title="Start year", time_unit="year"),
                "y": Channel(field="trial_count", type="quantitative", title="Trials started", unit="trials")}
    if len(cohorts) > 1:
        encoding["color"] = Channel(field="cohort", type="nominal", title="Cohort")
    data = [_row(b, year=b.key, cohort=label) for label, r in cohorts.items() for b in r.buckets]
    return TimeSeries(type="time_series", title=title, encoding=encoding, data=data,
                      options={"mark": "line"})


def build_grouped_bar(title: str, dimension_label: str,
                      cohorts: dict[str, AggregateResult]) -> GroupedBarChart:
    """Tidy rows per (category, cohort); share of cohort when cohort sizes differ > 2×."""
    sizes = {label: max(_cohort_size(r), 1) for label, r in cohorts.items()}
    use_share = max(sizes.values()) / min(sizes.values()) > SHARE_RATIO_THRESHOLD
    data = [{**_row(b, category=b.key, cohort=label), "share": round(b.trial_count / sizes[label], 4)}
            for label, r in cohorts.items() for b in r.buckets]
    y = (Channel(field="share", type="quantitative", title="Share of cohort", unit="share", format=".0%")
         if use_share else Channel(field="trial_count", type="quantitative", title="Trials", unit="trials"))
    return GroupedBarChart(
        type="grouped_bar_chart", title=title, data=data,
        encoding={"x": Channel(field="category", type="nominal", title=dimension_label), "y": y,
                  "color": Channel(field="cohort", type="nominal", title="Cohort")},
        options={"stacked": False, "normalize": "share" if use_share else "count"},
    )
```

- [ ] **Step 3: Run the tests and confirm they pass, then `make lint`.**

### ✅ Checkpoint S3 — you verify

- **I show you:** a real bar chart JSON computed from the pembrolizumab fixture, trimmed to 2 citations per bar for reading; the golden number (Phase 3 = 324); and the coverage report.
- **You check:**
  1. In `aggregate.py`, `_citation` and the count happen in one place (`citations[hit.key].append`), so a count can't exist without its citation.
  2. A citation looks exactly like the assignment's example, plus `field` and `evidence`.
  3. `dimensions.py` reads as one small function per dimension.
- **Then:** commit `feat: evidence-carrying aggregation and chart builder`.

---

## Stage S4 — Vertical slice: the first end-to-end answer (~1.5 h)

**Stage done when:** `curl -X POST localhost:8000/v1/visualize -d '{"query":"How has the number of trials for this drug changed over time?","drug_name":"Pembrolizumab"}'` returns a valid `time_series` with citations. The planner is real; there's no judge yet.

### Task 4.1: Catalog, prompts, overlay and OpenAI planner

**Files:**
- Create: `src/ctviz/catalog/catalog.yaml`, `src/ctviz/catalog/loader.py`, `src/ctviz/agent/__init__.py`, `src/ctviz/agent/prompts.py`, `src/ctviz/agent/planner.py`, `src/ctviz/agent/overlay.py`
- Test: `tests/unit/agent/test_overlay.py`, `tests/unit/agent/test_planner.py`, `tests/unit/catalog/test_loader.py`

**Interfaces:**
- Consumes: `QueryPlan`, `VisualizeRequest`, `Settings`, `LLMUnavailableError`.
- Produces:
  - `Catalog` (pydantic) with `load_catalog() -> Catalog` and `Catalog.render_for_planner() -> str`.
  - `class PlannerBackend(Protocol): def complete(self, system: str, user: str) -> QueryPlan`, plus `OpenAIPlannerBackend(settings)`.
  - `class Planner` with `plan(request: VisualizeRequest, feedback: list[str] | None = None, previous: QueryPlan | None = None) -> QueryPlan`.
  - `FieldOverride(field, text_value, applied_value)`.
  - `apply_overlay(plan, request) -> tuple[QueryPlan, list[FieldOverride]]`.

- [ ] **Step 1: Write the overlay tests** (the overlay is pure, so these tests are the most valuable)

```python
from ctviz.agent.overlay import apply_overlay
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan


def test_structured_drug_name_becomes_a_structured_search_term() -> None:
    plan = make_plan(search_terms=[])
    request = VisualizeRequest(query="How has this changed?", drug_name="Pembrolizumab")

    new_plan, overrides = apply_overlay(plan, request)

    [term] = new_plan.search_terms
    assert (term.param, term.value, term.source) == ("query.intr", "Pembrolizumab", "structured_field")
    assert overrides == []
    assert plan.search_terms == []  # input plan is not mutated


def test_structured_field_replaces_llm_value_in_the_same_slot_and_is_logged() -> None:
    plan = make_plan(filters={"phases": None, "overall_statuses": None, "study_types": None,
                              "intervention_types": None, "lead_sponsor_classes": None,
                              "countries": None, "start_year_min": 2018, "start_year_max": None,
                              "nct_ids": None})
    request = VisualizeRequest(query="since 2018?", start_year=2015)

    new_plan, overrides = apply_overlay(plan, request)

    assert new_plan.filters is not None and new_plan.filters.start_year_min == 2015
    assert overrides[0].field == "start_year" and overrides[0].text_value == "2018"


def test_sponsor_role_any_maps_to_query_spons() -> None:
    new_plan, _ = apply_overlay(make_plan(search_terms=[]),
                                VisualizeRequest(query="q q", sponsor="Merck", sponsor_role="any"))

    assert new_plan.search_terms[0].param == "query.spons"
```

- [ ] **Step 2: Implement `overlay.py`.** Use a mapping table plus one short function per slot type. Search-term fields (`drug_name` → `query.intr`, `condition` → `query.cond`, `sponsor` → `query.lead`/`query.spons`) drop any LLM term on the same param and append a `structured_field` term. Filter fields (`trial_phase` → `phases`, `status` → `overall_statuses`, `country` → `countries=[country]`, `start_year` → `start_year_min`, `end_year` → `start_year_max`, `study_type` → `study_types=[…]`, `nct_ids` → `nct_ids`) replace the slot via `model_copy(update=…)`, recording a `FieldOverride` when the LLM value differed. The overlay returns new objects and never mutates its input.

- [ ] **Step 3: Write `catalog.yaml`** with the content of PLAN.md §5.10 (search params with `provides` / `use_for` / `avoid_for`, dimensions, measures, time fields, networks, limits). Then write `loader.py`, which validates it into a `Catalog` model and renders compact planner text. Test: `test_catalog_lists_every_menu_value`, which asserts that every `SearchParam`, `Dimension` and `NetworkType` enum member appears in the YAML, so the catalog and the schema can't drift.

- [ ] **Step 4: Write the planner test with a fake backend** (no network)

```python
from ctviz.agent.planner import Planner
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan


class FakeBackend:
    def __init__(self, plan: QueryPlan) -> None:
        self.plan, self.calls = plan, []

    def complete(self, system: str, user: str) -> QueryPlan:
        self.calls.append((system, user))
        return self.plan


def test_planner_sends_catalog_in_system_and_question_in_user_message() -> None:
    backend = FakeBackend(make_plan())

    Planner(backend).plan(VisualizeRequest(query="Trials by phase?", drug_name="Pembrolizumab"))

    system, user = backend.calls[0]
    assert "query.intr" in system and "provides" in system.lower()
    assert "Trials by phase?" in user
    assert "drug_name" in user and "Pembrolizumab" not in system


def test_planner_includes_feedback_on_revise() -> None:
    backend = FakeBackend(make_plan())

    Planner(backend).plan(VisualizeRequest(query="q q"), feedback=["0 trials for query.cond='Keytruda'"],
                          previous=make_plan())

    assert "0 trials for query.cond='Keytruda'" in backend.calls[0][1]
```

- [ ] **Step 5: Implement `prompts.py` and `planner.py`.**
  - `prompts.build_planner_system(catalog)`: role, catalog text, the rules from PLAN.md §8.2, the compatibility table, and 6 few-shot examples.
  - `prompts.build_planner_user(request, feedback, previous)`: the question, the *names* of the structured fields present, today's date, and the feedback block.
  - `OpenAIPlannerBackend.complete` calls `client.responses.parse(model=…, reasoning={"effort": settings.planner_reasoning_effort}, input=[system, user], text_format=QueryPlan, timeout=LLM_TIMEOUT_S)`. It checks `response.status == "incomplete"` first (retries once with a higher `max_output_tokens`), maps refusals and `openai.APIError` to `LLMUnavailableError`, and never passes `temperature`.

- [ ] **Step 6: Run the tests and confirm they pass, then `make lint`.**

### Task 4.2: Pipeline + API endpoint (happy path)

**Files:**
- Create: `src/ctviz/pipeline.py`, `src/ctviz/api/__init__.py`, `src/ctviz/api/app.py`, `src/ctviz/api/errors.py`
- Test: `tests/integration/test_pipeline_slice.py`, `tests/api/test_visualize_endpoint.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `async run_pipeline(request, *, planner: Planner, client: CtGovClient, today: date) -> VisualizeResponse`.
  - The FastAPI `app` with `POST /v1/visualize` and `GET /health`.
  - `api.errors.to_response(exc) -> tuple[int, VisualizeResponse]`.

- [ ] **Step 1: Write the integration test.** It uses a fake planner plus respx replaying the pembrolizumab fixture, and asserts:
  - `response.ok`;
  - `response.visualization.type == "time_series"`;
  - every row with `trial_count > 0` has exactly `trial_count` citations;
  - `meta.cohorts[0].coverage.api_total_count == 2960`.

- [ ] **Step 2: Implement `pipeline.py`.** It's a short, readable top-to-bottom function, with each step a named helper of ≤ 40 lines:

```python
async def run_pipeline(request: VisualizeRequest, *, planner: Planner, client: CtGovClient,
                       today: date) -> VisualizeResponse:
    """request → plan → fetch per cohort → normalize → aggregate + cite → spec (slice version)."""
    plan, overrides = apply_overlay(planner.plan(request), request)
    if not plan.answerable:
        return _out_of_scope(plan)
    cohorts = await _fetch_cohorts(compile_plan(plan), client, request.options.max_records)
    results = {c.label: _aggregate(plan, c.trials, today) for c in cohorts}
    visualization = _build(plan, results)
    return VisualizeResponse(ok=True, visualization=visualization,
                             meta=_meta(plan, overrides, cohorts, results), error=None)
```

Strict match, the probe, the judge and the verifier are wired in during S6–S7. Each gets its own step inside this function, and the function stays short because every step is a helper.

- [ ] **Step 3: Implement `api/app.py` and `api/errors.py`.**
  - The app keeps one `CtGovClient` per process via a FastAPI lifespan; the planner is built from settings.
  - `errors.to_response` maps exceptions to responses:
    - `UpstreamError` → 502 `UPSTREAM_API_ERROR`;
    - `LLMUnavailableError` → 503;
    - `PlanInvalidError` → 200 `PLAN_INVALID`;
    - `CitationCheckError` → 500 `CITATION_CHECK_FAILED`;
    - FastAPI validation errors → 422 `INVALID_REQUEST`.
  - No stack traces reach the client; the full error is logged server-side.

- [ ] **Step 4: Write the API contract test** with `TestClient` and a dependency override that injects the fake planner and a respx-mocked client. It asserts:
  - 200 + `ok:true` on the happy path;
  - 422 + `error.code == "INVALID_REQUEST"` for `{"query": ""}`;
  - 502 + `UPSTREAM_API_ERROR` when the mock returns 500 three times.

- [ ] **Step 5: Run everything, then run the service live once.**

```bash
make check
make run   # in another terminal:
curl -s -X POST localhost:8000/v1/visualize -H 'content-type: application/json' \
  -d '{"query":"How has the number of trials for this drug changed over time?","drug_name":"Pembrolizumab"}' \
  | python -m json.tool | head -60
```

Save the output as `examples/01_draft.response.json`.

### ✅ Checkpoint S4 — you verify (first real answer)

- **I show you:** the curl output (trimmed), the time taken, and `pipeline.py` in full, since it should read like the architecture diagram.
- **You check:**
  1. The answer is correct (bars by year, 2026 flagged partial).
  2. Open 2 citations and paste their `field` into the raw record to confirm the values.
  3. `pipeline.py` is readable top to bottom.
- **Then:** commit `feat: first end-to-end slice (planner → fetch → cited time series)`.

---

## Stage S5 — Full analysis coverage (~2.5 h)

**Stage done when:**
- every dimension, the histogram, the scatter plot and all 3 core network types produce cited output from fixtures;
- shape guards adjust degenerate charts;
- the §14 golden numbers hold.

### Task 5.1: Multi-valued dimensions (intervention type, drug, condition, country)

**Files:**
- Modify: `src/ctviz/analysis/dimensions.py` (register 4 extractors)
- Test: `tests/unit/analysis/test_dimensions_multi.py`, `tests/integration/test_country_golden.py`

**Interfaces:**
- Produces:
  - Extractors registered for `INTERVENTION_TYPE`, `INTERVENTION`, `CONDITION`, `COUNTRY`.
  - `country_extractor(recruiting_only: bool)`: a factory, because the recruiting rule depends on the plan.
  - Each trial is deduped per key, and the evidence is the first matching array index.

- [ ] **Step 1: Write the failing tests**

```python
from ctviz.analysis.dimensions import country_extractor, extract
from ctviz.citations.pointer import INTERVENTIONS, LOCATIONS
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.enums import Dimension
from tests.factories import make_study


def test_intervention_type_counts_each_type_once_per_trial() -> None:
    trial = normalize(make_study(interventions=[{"type": "DRUG", "name": "A"},
                                                {"type": "DRUG", "name": "B"},
                                                {"type": "BEHAVIORAL", "name": "C"}]))

    hits = extract(trial, Dimension.INTERVENTION_TYPE)

    assert [(h.key, h.evidence[0].field) for h in hits] == [
        ("DRUG", f"{INTERVENTIONS}/0/type"), ("BEHAVIORAL", f"{INTERVENTIONS}/2/type")]


def test_country_counts_once_per_trial_and_cites_first_site() -> None:
    trial = normalize(make_study(locations=[{"country": "Japan"}, {"country": "Japan"},
                                            {"country": "France"}]))

    hits = country_extractor(recruiting_only=False)(trial)

    assert [(h.key, h.evidence[0].field) for h in hits] == [
        ("Japan", f"{LOCATIONS}/0/country"), ("France", f"{LOCATIONS}/2/country")]


def test_recruiting_rule_requires_a_recruiting_site_in_that_country() -> None:
    trial = normalize(make_study(status="RECRUITING", locations=[
        {"country": "Germany", "status": "WITHDRAWN"}, {"country": "Japan", "status": "RECRUITING"}]))

    hits = country_extractor(recruiting_only=True)(trial)

    assert [h.key for h in hits] == ["Japan"]
    assert [e.field for e in hits[0].evidence] == [f"{LOCATIONS}/1/country", f"{LOCATIONS}/1/status"]


def test_recruiting_rule_falls_back_to_trial_status_when_site_statuses_are_null() -> None:
    trial = normalize(make_study(status="RECRUITING", locations=[{"country": "Japan"}]))

    [hit] = country_extractor(recruiting_only=True)(trial)

    assert hit.evidence[-1].field == "/protocolSection/statusModule/overallStatus"
```

Golden integration test: `count_by` on the `ms_recruiting` fixture, using the recruiting extractor, gives United States = 157.

- [ ] **Step 2: Implement the four extractors.** Each is a short function using the same `_bucket` helper.
  - **Predicates** are `any_element` over the array with sub-predicates, e.g. `{"op": "any_element", "path": LOCATIONS, "where": [{"op": "equals", "path": "/country", "value": "Japan"}, {"op": "equals", "path": "/status", "value": "RECRUITING"}]}`.
  - **The drug extractor** keeps `DRUG`/`BIOLOGICAL` types, keys on `normalize_drug(name)`, and skips `None` (placebo). Its predicate uses `normalizes_to`.
  - **The condition extractor** keys on `normalize_text(condition)`, with predicate `{"op": "any_text_equals", "path": CONDITIONS, "value": key}`.

- [ ] **Step 3: Run the tests and confirm they pass, then `make lint`.**

### Task 5.2: Numeric analyses (histogram, scatter) and shape guards

**Files:**
- Create: `src/ctviz/analysis/numeric.py`, `src/ctviz/analysis/guards.py`
- Modify: `src/ctviz/viz/builder.py` (`build_histogram`, `build_scatter`, `build_metric`, `build_table`)
- Test: `tests/unit/analysis/test_numeric.py`, `tests/unit/analysis/test_guards.py`

**Interfaces:**
- Produces:
  - `histogram(trials, measure) -> AggregateResult`. Bucket keys are bin labels, and each `Bucket.flags` holds `("actual:N", "estimated:M")`.
  - `LOG_EDGES = (0, 10, 25, 50, 100, 200, 500, 1000, 2000, 5000)`.
  - `scatter(trials, x: Measure, y: Measure) -> tuple[list[Point], dict[str, str]]`, where `Point(nct_id, x, y, date_type, citations)`.
  - `apply_guards(plan, result) -> tuple[VizType, list[str]]`, returning the new type plus the adjustment notes.

- [ ] **Step 1: Write the failing tests**

```python
from ctviz.analysis.aggregate import MatchedTrial
from ctviz.analysis.numeric import histogram, scatter
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.enums import Measure
from tests.factories import make_study


def _m(*studies: dict) -> list[MatchedTrial]:
    return [MatchedTrial(normalize(s), ()) for s in studies]


def test_histogram_uses_log_bins_for_skewed_enrollment_and_excludes_withdrawn_zeros() -> None:
    trials = _m(*[make_study(f"NCT0000000{i}", enrollment=n) for i, n in enumerate([5, 30, 80, 2500])],
                make_study("NCT00000009", enrollment=0, status="WITHDRAWN"))

    result = histogram(trials, Measure.ENROLLMENT)

    labels = {b.key: b.trial_count for b in result.buckets if b.trial_count}
    assert labels == {"0–9": 1, "25–49": 1, "50–99": 1, "2000–4999": 1}
    assert result.excluded == {"NCT00000009": "withdrawn_zero_enrollment"}


def test_scatter_excludes_estimated_dates_and_flags_untyped_ones() -> None:
    # make_study gains `completion` / `completion_type` kwargs in this task (same style as `start`)
    trials = _m(
        make_study("NCT00000001", enrollment=100, start="2017-08", start_type=None,
                   completion="2020-08", completion_type="ACTUAL"),
        make_study("NCT00000002", enrollment=200, start="2017-08",
                   completion="2020-08", completion_type="ACTUAL"),
        make_study("NCT00000003", enrollment=300, start="2024-01",
                   completion="2028-05", completion_type="ESTIMATED"),
    )

    points, excluded = scatter(trials, Measure.DURATION_MONTHS, Measure.ENROLLMENT)

    assert [(p.nct_id, p.date_type) for p in points] == [
        ("NCT00000001", "untyped"), ("NCT00000002", "actual")]
    assert excluded == {"NCT00000003": "non_actual_duration"}
```

Guard tests:

```python
from ctviz.analysis.aggregate import AggregateResult, Bucket
from ctviz.analysis.guards import apply_guards
from ctviz.schemas.enums import VizType
from tests.factories import make_plan


def test_single_bucket_bar_becomes_metric() -> None:
    viz_type, notes = apply_guards(make_plan(), AggregateResult((Bucket("Phase 3", {}, ()),), {}))

    assert viz_type is VizType.METRIC and "1 category" in notes[0]


def test_short_time_series_becomes_bar_chart() -> None:
    plan = make_plan(visualization={"type": "time_series", "title": "t", "rationale": "r"})
    result = AggregateResult((Bucket("2025", {}, ()), Bucket("2026", {}, ())), {})

    viz_type, _ = apply_guards(plan, result)

    assert viz_type is VizType.BAR_CHART
```

- [ ] **Step 2: Implement `numeric.py` and `guards.py`.**
  - Bin selection: log edges when skewness > 2, otherwise Freedman–Diaconis clamped to 8–30 bins. Put the skewness function in `numeric.py` with a docstring citing PLAN.md §10.5.
  - Labels read `"100–199"`, and the last bin is `"≥5000"` with `bin_end=None`.
  - Duration months = `(completion − start).days / 30.4375`, using day 15 when the day is missing. Durations under 1 month are excluded as `"implausible_duration"`.
  - Guards follow the table in §10.6. Each guard is one small predicate function plus its adjustment note.

- [ ] **Step 3: Add the builder functions** for histogram (`x`=`bin_start` with `bin: true`, `x2`=`bin_end`, `y`=`trial_count`, `label`=`bin_label`), scatter (one row per trial with `nct_id`), metric and table. Test that each validates through the `Visualization` union.

- [ ] **Step 4: Golden tests** on fixtures:
  - `psoriasis_p2` has 10 withdrawn zeros excluded, and log bins are chosen;
  - `crohns_p3_completed` has 120 scatter points.

  Then run `make check`.

### Task 5.3: Networks + entity resolution

**Files:**
- Create: `src/ctviz/analysis/network.py`, `src/ctviz/analysis/entities.py`
- Modify: `src/ctviz/viz/builder.py` (`build_network`)
- Test: `tests/unit/analysis/test_network.py`, `tests/unit/analysis/test_entities.py`, `tests/integration/test_network_fixtures.py`

**Interfaces:**
- Produces:
  - `Graph(nodes: tuple[GraphNode, ...], edges: tuple[GraphEdge, ...], summary: dict)`.
  - `GraphNode(id, label, type, predicate, citations)` and `GraphEdge(id, source, target, type, predicate, citations, flags)`; weight is `len(citations)`.
  - `build_graph(trials, network_type, top_n=50, include_collaborators=False) -> Graph`.
  - `prune(graph, max_nodes=50, max_edges=150, min_weight=2) -> Graph`.
  - `discover_aliases(trials, term) -> list[str]` (co-reference rule, §10.4).
  - `sponsor_census(trials, top=10) -> list[tuple[str, int]]` and `KNOWN_DISTINCT_ORGS: dict[str, list[str]]`, seeded with Merck & Co. vs Merck KGaA. **Q2 = a**: the census drives a warning and never auto-splits.

- [ ] **Step 1: Write the failing tests**

```python
from ctviz.analysis.aggregate import MatchedTrial
from ctviz.analysis.entities import discover_aliases, sponsor_census
from ctviz.analysis.network import build_graph, prune
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.enums import NetworkType
from tests.factories import make_study


def _m(*studies: dict) -> list[MatchedTrial]:
    return [MatchedTrial(normalize(s), ()) for s in studies]


def test_sponsor_drug_edge_needs_both_endpoints_in_the_same_trial() -> None:
    trials = _m(make_study("NCT00000001", sponsor="Merck", interventions=[
        {"type": "DRUG", "name": "Pembrolizumab"}, {"type": "DRUG", "name": "Placebo"}]))

    graph = build_graph(trials, NetworkType.SPONSOR_DRUG)

    assert {n.id for n in graph.nodes} == {"sponsor:merck", "drug:pembrolizumab"}
    [edge] = graph.edges
    assert len(edge.citations[0].evidence) >= 1  # second endpoint evidence from the same record


def test_drug_drug_edges_require_the_same_arm() -> None:
    arms = [{"label": "A", "interventionNames": ["Drug: Carboplatin", "Drug: Paclitaxel"]},
            {"label": "B", "interventionNames": ["Drug: Docetaxel"]}]
    trials = _m(make_study(interventions=[{"type": "DRUG", "name": n}
                                          for n in ("Carboplatin", "Paclitaxel", "Docetaxel")],
                           arms=arms))

    graph = build_graph(trials, NetworkType.DRUG_DRUG)

    assert [(e.source, e.target) for e in graph.edges] == [("drug:carboplatin", "drug:paclitaxel")]


def test_prune_caps_nodes_and_edges_and_reports_before_after() -> None:
    trials = _m(*[make_study(f"NCT{i:08d}", sponsor=f"S{i % 60}",
                             interventions=[{"type": "DRUG", "name": f"D{i % 70}"}])
                  for i in range(400)])

    pruned = prune(build_graph(trials, NetworkType.SPONSOR_DRUG), max_nodes=50, max_edges=150)

    assert len(pruned.nodes) <= 50 and len(pruned.edges) <= 150
    assert pruned.summary["nodes_before_pruning"] > len(pruned.nodes)


def test_aliases_come_only_from_the_same_intervention_object() -> None:
    trials = _m(*[make_study(f"NCT0000000{i}", interventions=[
        {"type": "DRUG", "name": "Pembrolizumab", "otherNames": ["Keytruda", "MK-3475"]},
        {"type": "DRUG", "name": "Paclitaxel"}]) for i in range(3)])

    assert discover_aliases(trials, "Keytruda") == ["mk-3475", "pembrolizumab"]


def test_sponsor_census_lists_distinct_names_with_counts() -> None:
    trials = _m(make_study("NCT00000001", sponsor="Merck Sharp & Dohme LLC"),
                make_study("NCT00000002", sponsor="Merck KGaA, Darmstadt, Germany"))

    assert dict(sponsor_census(trials)) == {"Merck Sharp & Dohme LLC": 1,
                                            "Merck KGaA, Darmstadt, Germany": 1}
```

- [ ] **Step 2: Implement `network.py`** (≈ 250 lines, split into `_sponsor_drug_pairs`, `_drug_drug_pairs`, `_condition_drug_pairs`, `_assemble`, `prune`), then `entities.py` and `build_network`.
  - Edge predicates are `all` of the two endpoint predicates, so the witness set is the intersection.
  - Pruning follows the order in §10.5: steps 1–5, plus step 0 only for the stretch site networks.
  - Citations are attached only to the elements kept after pruning.

- [ ] **Step 3: Golden fixture tests:**
  - glioblastoma sponsor↔drug has ≤ 50 nodes, ≤ 150 edges, and every edge weight ≥ 2;
  - NSCLC drug↔drug has fewer edges at arm level than the trial-level count, which is computed in the test.

- [ ] **Step 4: `make check`.**

### ✅ Checkpoint S5 — you verify

- **I show you:**
  - one real output per type (histogram for psoriasis, scatter for Crohn's, network for glioblastoma), trimmed;
  - the golden-number test results;
  - the Merck census output (the Q2 warning text).
- **You check:**
  1. Each extractor in `dimensions.py` stays small.
  2. Network pruning is reported in `summary`.
  3. The warning wording for Merck.
- **Then:** commit `feat: full analysis coverage (histogram, scatter, networks, guards)`.

---

## Stage S6 — Deep citations: strict match, predicates, independent verifier (~3 h)

**Stage done when:**
- trials that only mention the entity in passing are excluded and listed (Q1 = a);
- every response passes the independent verifier;
- 9 corruption tests fail as expected;
- `python -m ctviz.citations.verify` checks an example offline.

### Task 6.1: Predicate evaluator + strict match

**Files:**
- Create: `src/ctviz/citations/predicates.py`, `src/ctviz/citations/match.py`
- Test: `tests/unit/citations/test_predicates.py`, `tests/unit/citations/test_match.py`

**Interfaces:**
- Produces:
  - `evaluate(predicate: Predicate, document: Mapping) -> bool`, supporting ops `equals, in, set_equals, contains, exists, year_equals, year_in_range, in_range, any_element, normalizes_to, text_matches, any_text_equals` and combinators `all, any, not`. Unknown ops raise `ValueError`.
  - `referenced_paths(predicate) -> set[str]`, used by the verifier's relevance check.
  - `MatchPolicy = Literal["strict", "lenient"]`.
  - `match_predicate(term: SearchTerm, aliases: list[str]) -> Predicate | None` (None for broad params).
  - `filter_predicate(filters: EnumFilters | None) -> Predicate | None`.
  - `apply_strict_match(trials, terms, aliases_by_term, policy_for: Callable[[SearchParam], MatchPolicy]) -> MatchOutcome(kept: list[MatchedTrial], excluded: list[ExcludedTrial], base_predicate: Predicate)`.

- [ ] **Step 1: Write the failing predicate tests** (table-driven, one case per op)

```python
import pytest

from ctviz.citations.predicates import evaluate, referenced_paths
from ctviz.citations.pointer import INTERVENTIONS, PHASES, START_DATE
from tests.factories import make_study

DOC = make_study(phases=["PHASE2", "PHASE3"], start="2019-10",
                 interventions=[{"type": "DRUG", "name": "Pembrolizumab 200 mg",
                                 "otherNames": ["Keytruda"]}])


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ({"op": "set_equals", "path": PHASES, "value": ["PHASE3", "PHASE2"]}, True),
        ({"op": "contains", "path": PHASES, "value": "PHASE3"}, True),
        ({"op": "year_equals", "path": START_DATE, "value": 2019}, True),
        ({"op": "year_in_range", "path": START_DATE, "value": [2015, None]}, True),
        ({"op": "exists", "path": "/protocolSection/conditionsModule"}, False),
        ({"op": "any_element", "path": INTERVENTIONS,
          "where": [{"op": "normalizes_to", "path": "/name", "value": "pembrolizumab"}]}, True),
        ({"op": "any_element", "path": INTERVENTIONS,
          "where": [{"op": "text_matches", "path": "/otherNames", "value": "keytruda"}]}, True),
        ({"not": {"op": "equals", "path": PHASES, "value": ["PHASE1"]}}, True),
        ({"all": [{"op": "contains", "path": PHASES, "value": "PHASE1"}]}, False),
    ],
)
def test_predicate_ops(predicate: dict, expected: bool) -> None:
    assert evaluate(predicate, DOC) is expected


def test_unknown_op_is_an_error_not_false() -> None:
    with pytest.raises(ValueError, match="unknown predicate op"):
        evaluate({"op": "fuzzy", "path": PHASES, "value": 1}, DOC)


def test_referenced_paths_collects_nested_paths() -> None:
    assert referenced_paths({"all": [{"op": "exists", "path": PHASES},
                                     {"op": "equals", "path": START_DATE, "value": 1}]}) == {PHASES, START_DATE}
```

- [ ] **Step 2: Write the failing strict-match tests**

```python
from ctviz.analysis.aggregate import MatchedTrial
from ctviz.citations.match import apply_strict_match
from ctviz.ctgov.normalize import normalize
from ctviz.schemas.plan import SearchTerm
from tests.factories import make_study

TERM = SearchTerm(param="query.intr", value="pembrolizumab", source="query_text", rationale="drug")


def test_trial_that_only_mentions_the_drug_in_passing_is_excluded_with_reason() -> None:
    listed = normalize(make_study("NCT00000001", interventions=[{"type": "DRUG", "name": "Pembrolizumab"}]))
    passing = normalize(make_study("NCT03307785", interventions=[{"type": "DRUG", "name": "TSR-042"}]))

    outcome = apply_strict_match([listed, passing], [TERM], {}, lambda _: "strict")

    assert [m.trial.nct_id for m in outcome.kept] == ["NCT00000001"]
    assert outcome.excluded[0].nct_id == "NCT03307785"
    assert outcome.excluded[0].reason == "api_fulltext_match_only"
    assert outcome.kept[0].match_evidence[0].excerpt == "Pembrolizumab"


def test_other_names_count_as_a_match() -> None:
    trial = normalize(make_study(interventions=[{"type": "DRUG", "name": "MK-3475",
                                                 "otherNames": ["Pembrolizumab"]}]))

    outcome = apply_strict_match([trial], [TERM], {}, lambda _: "strict")

    assert outcome.kept[0].match_evidence[0].field.endswith("/otherNames/0")


def test_lenient_policy_keeps_unmatched_trials_without_match_evidence() -> None:
    trial = normalize(make_study(conditions=["Something else"]))
    term = SearchTerm(param="query.cond", value="glioblastoma", source="query_text", rationale="c")

    outcome = apply_strict_match([trial], [term], {}, lambda _: "lenient")

    assert len(outcome.kept) == 1 and outcome.kept[0].match_evidence == ()
```

- [ ] **Step 3: Implement `predicates.py`.**
  - A dispatch dict maps op → small function; `_lookup(document, path)` returns `None` when the path is missing.
  - `any_element` evaluates `{"all": where}` against each element, with paths relative to the element.
  - `normalizes_to` and `text_matches` reuse `common/names.py`.
  - The module is ≤ 150 lines.
- [ ] **Step 4: Implement `match.py`.**
  - Match fields follow the table in §11.3 (priority order).
  - The policy function implements **Q1 = a**: strict for `query.intr`/`lead`/`spons`; for `query.cond`, lenient unless `config.CONDITIONS_STRICT` (set in Task 2.4).
  - The filter re-check drops any record that fails the filter predicate, with stage `"filter"` and reason `"api_filter_mismatch"`.
  - The base predicate is `all[match predicates for strict terms, filter predicate]`.
- [ ] **Step 5: Run the tests and confirm they pass, then `make lint`.**

### Task 6.2: Wire strict match + cohort predicates into the pipeline

**Files:**
- Modify: `src/ctviz/pipeline.py`, `src/ctviz/analysis/aggregate.py` (no change to its API)
- Test: `tests/integration/test_pipeline_citations.py`

- [ ] **Step 1: Write the integration test.** On the pembrolizumab fixture:
  - `meta.cohorts[0].coverage.excluded["match"]["api_fulltext_match_only"] > 0`;
  - `records_matched == api_total_count − excluded`;
  - every citation's `evidence` includes one `role == "match"` item;
  - `NCT03307785` appears in `excluded_trials` (if present in the fixture).
- [ ] **Step 2: Implement.** The pipeline runs `discover_aliases` → `apply_strict_match` per cohort, then aggregates `kept`, and fills `CohortSummary.base_predicate` and `DataCoverage` (match/filter/analysis exclusions split by stage). Add the `cohort` key to rows for comparisons.
- [ ] **Step 3: `make check`.**

### Task 6.3: Independent verifier + CLI

**Files:**
- Create: `src/ctviz/citations/verify.py`, `src/ctviz/citations/__main__.py`
- Modify: `src/ctviz/pipeline.py` (verify before returning; fail closed)
- Test: `tests/unit/citations/test_verify.py`

**Interfaces:**
- Produces:
  - `verify_response(response: VisualizeResponse, raw_by_id: Mapping[str, Mapping], plotted_ids: Mapping[str, set[str]]) -> CitationCheck`, which raises `CitationCheckError(violations)` on any failure.
  - CLI: `python -m ctviz.citations.verify <response.json> <raw.json.gz>` prints a PASS/FAIL report.
- **Independence rule:** `verify.py` imports only `citations.pointer`, `citations.predicates`, `common.names` and `schemas`, never `analysis`. It gets a test of its own:

```python
import ast
from pathlib import Path


def test_verifier_never_imports_the_counting_code() -> None:
    source = Path("src/ctviz/citations/verify.py").read_text()
    imported = {node.module for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.ImportFrom) and node.module}

    assert not any(module.startswith("ctviz.analysis") for module in imported)
```

- [ ] **Step 1: Write the failing tests.**
  - Start from a small valid response built by the real pipeline from 3 factory studies (fixture helper `build_small_response()` in `tests/unit/citations/conftest.py`), and check it verifies clean.
  - Then write the nine corruption tests, each using `model_copy(update=…)` to change exactly one thing and asserting `CitationCheckError`:
    1. `test_verifier_rejects_wrong_pointer`
    2. `test_verifier_rejects_altered_excerpt`
    3. `test_verifier_rejects_trial_moved_to_wrong_bucket` (a Phase 1 trial placed in the Phase 3 row)
    4. `test_verifier_rejects_dropped_citation` (completeness)
    5. `test_verifier_rejects_duplicate_citation`
    6. `test_verifier_rejects_edge_evidence_from_two_trials`
    7. `test_verifier_rejects_trial_failing_match_rule`
    8. `test_verifier_rejects_trial_in_wrong_non_interventional_bucket`
    9. `test_verifier_rejects_trial_in_wrong_cohort_row`
  - Plus `test_verifier_accepts_overlapping_cohorts` (Review Focus #4): one trial listing both drugs appears in both cohorts' rows, and verification passes.
- [ ] **Step 2: Implement `verify.py`** as five functions, one per check in §11.6, so each reads on its own. `verify_response` runs them all, collects violations (rather than stopping at the first), and returns `CitationCheck` with timing.
- [ ] **Step 3: Wire it into the pipeline.** The verifier runs last; `CitationCheckError` becomes a 500 via `api/errors.py`.
- [ ] **Step 4: `make check`.** Also time the verifier on the 16,859-record breast cancer run (the live test marker) and record the result in DEVLOG.

### ✅ Checkpoint S6 — you verify (the bonus feature)

- **I show you:**
  - one citation traced by hand: its `field` pointer resolved in the raw record;
  - the 9 corruption tests failing correctly (pytest output);
  - the CLI verifying `examples/01` offline.
- **You check:**
  1. In `verify.py`, each check is a separate readable function, and there's no import from `analysis`.
  2. The excluded-trials list for pembrolizumab looks legitimately off-topic (spot-check 3 NCT IDs on clinicaltrials.gov).
- **Then:** commit `feat: deep citations — strict match, predicates, independent verifier`.

---

## Stage S7 — Agent: plan checks, digit guard, probe, judge, revise loop (~3 h)

**Stage done when:** every path of the §9.4 state machine is covered by a test using fakes, and one live request per analysis kind succeeds.

### Task 7.1: Plan checks + digit guard

**Files:**
- Create: `src/ctviz/agent/plan_checks.py`
- Test: `tests/unit/agent/test_plan_checks.py`

**Interfaces:**
- Produces:
  - `check_plan(plan, today) -> list[str]` (an empty list means OK). It covers checks 3–8 from §7.4 plus the compatibility matrix from §7.3.
  - `guard_text(plan, request) -> tuple[QueryPlan, list[str]]` (the digit guard). It returns a plan with its title, interpretation and assumptions made safe, plus notes for `meta.adjustments`.

- [ ] **Step 1: Write the failing tests**, including:
  - `test_time_trend_requires_time_series_or_bar`
  - `test_comparison_needs_two_to_four_distinct_values`
  - `test_essie_syntax_in_values_is_rejected`
  - `test_trial_lookup_requires_nct_ids`
  - the digit-guard cases. Titles "Merck Phase 3 Trials by Status", "Intervention Types in Type 2 Diabetes Trials" and "COVID-19 Vaccine Trials" (where "COVID-19" is in the query) survive unchanged. A title "Trials rose 45% since 2015" (45 appears nowhere) is replaced by the template, and the note records the replacement. An assumption sentence containing an unsupported number is dropped.
- [ ] **Step 2: Implement.** Write one function per check, each returning `list[str]`; `check_plan` concatenates them. In the digit guard, the allowed tokens are built exactly as in §7.4 check 9.
- [ ] **Step 3: `make lint` + tests.**

### Task 7.2: Judge (OpenRouter) with code-side verdict recompute

**Files:**
- Create: `src/ctviz/schemas/judge.py`, `src/ctviz/agent/judge.py`
- Test: `tests/unit/agent/test_judge.py`

**Interfaces:**
- Produces:
  - `CheckResult`, `JudgeIssue`, `JudgeVerdict` (§9.2).
  - `class JudgeBackend(Protocol): def complete(self, system: str, user: str) -> JudgeVerdict`, plus `OpenRouterJudgeBackend(settings)`.
  - `class Judge` with `review(request, plan, overrides, probe_totals) -> JudgeReview(needs_revision: bool, issues: list[JudgeIssue], status_model: str | None, available: bool)`.

- [ ] **Step 1: Write the failing tests:**
  - `test_major_issue_forces_revision_even_if_model_said_pass`
  - `test_failed_check_without_major_issue_does_not_revise`
  - `test_issue_on_structured_field_slot_is_discarded` (a `field_overrides` slot)
  - `test_unreachable_judge_fails_open` (the backend raises `openai.APIConnectionError` → `available=False`, `needs_revision=False`)
- [ ] **Step 2: Implement.**
  - The OpenRouter backend uses `OpenAI(base_url="https://openrouter.ai/api/v1")`, `chat.completions.parse(response_format=JudgeVerdict, temperature=0, extra_body={"provider": {"require_parameters": True}}, extra_headers={"HTTP-Referer": settings.app_url, "X-Title": settings.app_name})`.
  - A 402 or 503 fails open immediately; there are no retries on 402.
  - The recompute rule lives in a pure function, `needs_revision(verdict, overridden_paths) -> bool`.

### Task 7.3: Probe integration

**Files:**
- Create: `src/ctviz/agent/probe.py`
- Test: `tests/unit/agent/test_probe.py`

**Interfaces:**
- Produces: `ProbeResult(totals: dict[str, int], feedback: list[str], verdict: Literal["ok", "revise", "no_matches", "too_broad_accept"])` and `async probe_plan(plan, client, max_records, attempt: int) -> ProbeResult`, implementing the table in §9.1.
- Tests: zero on attempt 1 → revise with feedback naming the param and value; zero on attempt 2 → `no_matches`; one empty cohort in a comparison on attempt 2 → `ok` plus a warning; over the cap on attempt 2 → `too_broad_accept`.

### Task 7.4: Orchestrator (the §9.4 state machine) + NCT fast path

**Files:**
- Create: `src/ctviz/agent/orchestrator.py`
- Modify: `src/ctviz/pipeline.py` (use the orchestrator instead of calling the planner directly)
- Test: `tests/unit/agent/test_orchestrator.py`

**Interfaces:**
- Produces:
  - `PlanningOutcome(plan: QueryPlan | None, error_code: str | None, judge_status: JudgeStatus, executed_attempt: int, trace: list[dict], overrides, adjustments, probe_totals)`.
  - `async orchestrate(request, *, planner, judge, client, today) -> PlanningOutcome`.
  - `is_fast_path(request) -> list[str] | None`, using the deterministic rule from §8.4.

- [ ] **Step 1: Write one test per path**, using scripted fakes: `ScriptedPlanner([plan1, plan2])`, `ScriptedJudge([...])` and a fake probe client.
  - `test_pass_on_first_attempt` → `passed`, attempt 1
  - `test_out_of_scope_skips_checks_and_judge` → `OUT_OF_SCOPE`, `skipped`, judge never called
  - `test_checks_fail_then_revised_plan_passes` → `passed_after_revision`
  - `test_zero_probe_twice_returns_no_matching_trials` (Review Focus #2)
  - `test_judge_revise_then_pass` → `passed_after_revision`
  - `test_judge_revise_twice_ships_flagged` → `rejected_after_revision`, with issues attached
  - `test_second_plan_invalid_executes_first_plan` → `executed_previous_plan`
  - `test_both_plans_invalid` → `PLAN_INVALID`
  - `test_judge_unavailable_fails_open` → `unavailable`
  - `test_planner_unavailable_raises_llm_unavailable`
  - `test_fast_path_skips_llms_for_simple_nct_lookup` (`"Status of NCT04368728?"`) versus `test_nct_question_with_compare_goes_to_planner`
- [ ] **Step 2: Implement.** The orchestrator is a small loop over `attempt in (1, 2)` that delegates to `_try_attempt(...)`. Each branch returns early with a named status, and there's no nesting deeper than 3.
- [ ] **Step 3: Run a live smoke test per analysis kind** (`pytest -m live tests/live/test_agent_live.py`), 6 requests from §14, and record the results in DEVLOG.

### ✅ Checkpoint S7 — you verify

- **I show you:** the orchestrator test names mapped to the V3 diagram (every arrow has a test); 3 live responses with their `meta.validation.trace`; and one judge rejection caught live, if any occurred.
- **You check:**
  1. `orchestrator.py` reads like the V3 diagram.
  2. The judge prompt rubric wording in `prompts.py`.
  3. The digit-guard examples.
- **Then:** commit `feat: agent — plan checks, probe, judge, revise loop, fast path`.

---

## Stage S8 — API polish + replay mode (~1 h)

### Task 8.1: Schema endpoint, health, gzip, replay mode

**Files:**
- Modify: `src/ctviz/api/app.py`
- Create: `src/ctviz/api/replay.py`, `examples/canned_plans.json`
- Test: `tests/api/test_contract.py`

- [ ] **Step 1: Write the failing tests:**
  - `test_schema_endpoint_exports_request_and_response_json_schema` (the response schema contains `"discriminator"`);
  - `test_health_reports_providers_as_booleans_only` (no `sk-` anywhere in the body);
  - `test_responses_are_gzipped_when_requested`;
  - `test_service_starts_without_keys_and_llm_routes_return_503`;
  - `test_replay_mode_answers_example_requests_offline` (`PLANNER_MODE=replay` + respx fixtures);
  - `test_every_example_file_validates_against_the_response_schema`.
- [ ] **Step 2: Implement.**
  - `GZipMiddleware(minimum_size=1000)`.
  - `GET /v1/schema` returns `{"request": VisualizeRequest.model_json_schema(), "response": VisualizeResponse.model_json_schema()}`.
  - `GET /health` returns `{"ok": true, "ctgov": <version>, "providers": {"openai": bool, "openrouter": bool}}`.
  - Replay mode swaps the planner and judge for canned-plan fakes keyed by the normalized query text.
- [ ] **Step 3: Add `make demo-offline`** (`PLANNER_MODE=replay uv run uvicorn …`) and run `make check`.

### ✅ Checkpoint S8 — you verify

- Run `make demo-offline` yourself and curl one example. Then read `api/errors.py`: the status mapping should match §12.2 exactly.
- **Then:** commit `feat: schema/health endpoints, gzip, keyless replay mode`.

---

## Stage S9 — Evals + prompt iteration (~1.5 h)

### Task 9.1: Planner and judge eval harness

**Files:**
- Create: `evals/cases.yaml` (12 cases), `evals/judge_cases.yaml` (10 cases), `evals/run_evals.py`, `evals/report.md` (generated)

- [ ] **Step 1: Write `cases.yaml`.** It holds 12 questions covering every §14 class, each with *property* assertions, e.g.

```yaml
- id: trend_pembro
  request: {query: "How has the number of trials for this drug changed over time?", drug_name: Pembrolizumab}
  expect:
    search_terms: [{param: query.intr, value_contains: pembrolizumab}]
    analysis.kind: time_trend
    visualization.type: [time_series, bar_chart]
- id: compare_phases
  request: {query: "Compare phases for trials involving pembrolizumab vs nivolumab"}
  expect:
    comparison.vary_param: query.intr
    comparison.values_contain: [pembrolizumab, nivolumab]
    analysis.group_by: phase
- id: out_of_scope
  request: {query: "What's the best cancer drug?"}
  expect: {answerable: false}
```

- [ ] **Step 2: Write `judge_cases.yaml`.** It holds 10 labeled plans: one per issue category, plus the structured-field-override case that must pass, plus 2 clean plans.
- [ ] **Step 3: Write `run_evals.py`.** It runs the real planner and judge (live), then writes `evals/report.md` with:
  - plan accuracy on attempt 1 and after revise;
  - judge catch rate and false-alarm rate;
  - p50/p95 latency and mean cost.
- [ ] **Step 4: Iterate the prompts until the §16.4 targets are met**, or document the gaps honestly. Each prompt change gets a DEVLOG entry: what failed, what changed, and the new score.

### ✅ Checkpoint S9 — you verify

- **I show you:** `evals/report.md`, the prompt diff between v1 and the final version, and the DEVLOG iteration notes.
- **You check:** that each failure's explanation makes sense.
- **Then:** commit `test: planner/judge eval harness + prompt iteration`.

---

## Stage S10 — Deliverables (~2.5 h)

### Task 10.1: Example runs

**Files:**
- Create: `scripts/generate_examples.py`, `examples/0{1..5}.{request,response}.json`, `examples/0{1..5}.raw.json.gz`, `examples/0{1..5}.verify.txt`, `examples/README_snippets.md`

- [ ] **Step 1: Generate the five examples** from PLAN.md §20.2 (time_series, histogram, grouped_bar_chart, network_graph, bar_chart) with real LLM calls.
- [ ] **Step 2: For each, run the offline verifier CLI** and save the report.
- [ ] **Step 3: Produce abridged README snippets:** the first 3 rows, 2 citations per datum, and `"_elided": N`.
- [ ] **Step 4: The example-schema contract test from S8 now covers these files.** Run `make check`.

### Task 10.2: README, DESIGN, zip

**Files:**
- Create: `README.md`, `docs/DESIGN.md`, `scripts/package_zip.sh`
- Test: `tests/test_packaging.py`

- [ ] **Step 1: Write the README** following the outline in PLAN.md §20.1 (10 sections). Include "No keys? `make demo-offline`", the request-field table, the response-schema section, a mapping from the spec's example keys to ours, and the AI-tools/integrity section drawn from DEVLOG.
- [ ] **Step 2: Write `docs/DESIGN.md`**, derived from PLAN.md §3–§13 without the internal notes (key handling, questions for you).
- [ ] **Step 3: Write `package_zip.sh`.** It runs `git archive` of HEAD plus the `examples/` and `evals/report.md` files, excludes `PLAN*.md`/`PLAN*.pdf`, `.env*` (except `.env.example`) and caches, and then does:

```bash
if unzip -p "$ZIP" | grep -aE 'sk-(proj|or-v1)-[A-Za-z0-9_-]{20,}' >/dev/null; then
  echo "secret detected in archive — aborting" >&2; rm -f "$ZIP"; exit 1
fi
```

- [ ] **Step 4: Write the packaging test.** It builds the zip into a temp dir and asserts: no `.env`; no `PLAN.md`; README present; 5 examples present; and the secret scan passes on a planted fake key (the script must exit 1).
- [ ] **Step 5: Final `make check`**, plus a clean clone into `/tmp`, then `make install && make demo-offline` to prove it runs from scratch.

### ✅ Checkpoint S10 — final review

- **I show you:** the README rendered, the zip listing, coverage ≥ 80%, and the final code-review report for the whole codebase.
- **You check:** read the README once as if you were the grader.
- **Then:** tag `v1.0` and create the zip. The HTML viewer (Q3) gets its own plan next.

---

## Self-review (done while writing this plan)

1. **Spec coverage:**
   - R1–R10 map to S1 (R1, R5–R7), S2 (R3), S4/S7 (R2, R4), S5 (R8), S6 (R9) and S10 (R10).
   - Q1/Q2/Q4 are placed at S6.1/S5.3/S3.3.
   - Q3 is deferred (explicitly out of scope).
   - Stretch items S1–S6 from PLAN.md §17 are not in this plan and will be added only after S10 if time remains.
2. **Placeholders:** code steps show real code; the few "implement following the table in §N" steps name exact rules and test names. Watch those at review time: the S5 scatter test notes that `make_study` needs `completion` kwargs, so add them in Task 5.2.
3. **Type consistency:**
   - `MatchedTrial`/`Bucket`/`AggregateResult` (S3) are consumed unchanged by S5–S6.
   - `Citation(nct_id, field, excerpt, evidence)` is used everywhere.
   - `DimensionHit.evidence[0]` is always the primary evidence.
   - `RequestSpec.params` and `CtGovClient.fetch_all(params, max_records)` match across S2/S4/S7.
4. **Review Focus:** every item has a named test in its owning task (2.3, 7.4, 2.2, 6.3, 2.2).
