# ctviz dev log

## 2026-09-28 — S0: scaffold

- Scaffolded the `ctviz` package with `uv` (Python 3.12, `src/` layout) plus ruff, mypy, pytest
  (async auto mode, `live` marker off by default) and a `Makefile` (`test`, `lint`, `check`,
  `run`, `smoke`).
- Added `config.py` (named constants + `Settings` with `SecretStr` keys) and `errors.py`
  (the exception hierarchy the API layer maps to error codes).
- Removed the `uv init` template `main()` / console script: it only printed a greeting and the
  project has no CLI entry point yet.
- **Decision: no agent framework.** The agent is a fixed two-call pipeline (planner, then judge
  with at most one revise), so plain OpenAI SDK + Pydantic is enough. It keeps every step
  unit-testable with fake backends, and the control flow reads top to bottom in one module.

## 2026-09-28 — S2: ClinicalTrials.gov data layer

- Built `ctgov/fields.py` (fields= per dimension/measure/network/match), `ctgov/compiler.py`
  (`QueryPlan` -> `RequestSpec[]`, no LLM text ever reaches `fields=` or Essie syntax),
  `ctgov/client.py` (async probe/paginate/truncate/retry/cache over httpx), and
  `ctgov/normalize.py` (raw study -> `Trial`). 27 unit tests, all green.
- **Fix (carried-over note):** raw ClinicalTrials.gov values carry stray whitespace (e.g. a
  `LocationCountry` seen as `"Bonaire, Saint Eustatius and Saba "`). `normalize()` now strips
  `country`, `facility`, `lead_sponsor`, `intervention name`, and `condition` values before
  they're used for matching/grouping; covered by
  `test_normalize_strips_stray_whitespace_from_country` and
  `test_normalize_strips_stray_whitespace_from_sponsor_and_intervention_names`.
- `ExcludedTrial.nct_id` tightening (S1 carried-over note) is **not applicable to S2**: no S2
  code constructs `ExcludedTrial`; deferred to whichever later stage first produces one.
- **Recorded 7 fixtures** for the PLAN.md §14 queries via `scripts/record_fixtures.py`
  (live ClinicalTrials.gov, no API key). Live totals **exactly matched** the plan's golden
  numbers, no drift: pembrolizumab 2960, nivolumab 2025, ms_recruiting 430, glioblastoma 2268,
  psoriasis_p2 512, crohns_p3_completed 127, nsclc 8572. Total fixture size on disk: **8.6 MB**
  gzipped (largest: `nsclc.json.gz` at 4.2 MB); none near the 15 MB single-file limit.
- **Measurement 1 — condition strict-match rate** (share of records whose conditions, keywords,
  or condition MeSH terms/ancestors contain the searched term, case-insensitive substring):
  glioblastoma 2154/2268 = **94.97%**, ms_recruiting 378/430 = **87.91%**,
  psoriasis_p2 508/512 = **99.22%**. None of the three clears the 97% bar consistently
  (ms_recruiting is well under), so **`CONDITIONS_STRICT = False`** (Q1 decision): conditions
  stay lenient for now. Recorded in `config.py` with the measured numbers inline.
- **Measurement 2 — pembrolizumab full fetch wall time** with the real (full union) field set:
  **3.00 s** for all 2960 records across 3 pages (close to PLAN.md §14 row 1's 3.14 s, which
  used a slimmer 2-field projection).
- **Ruling (Task 2.2):** the plan's given `test_fetch_all_follows_page_tokens` mocked only 2
  respx responses, but the plan's own `fetch_all()` always calls `probe()` first (confirmed by
  the truncation test's 3-response pattern), so the mock was one response short and the test
  undercounted records (1 instead of 3). Fixed by adding the missing probe response and moving
  the `pageToken` assertion to `calls[2]`. Cost if left wrong: the test would falsely appear to
  validate paging while actually only exercising 1 of 2 real pages.
- **Ruling (Task 2.2):** `CtGovClient._cache`'s key type as given (`tuple[tuple[str,str], ...]`
  built from `(path, *sorted(params.items()))`) mixes a `str` with `tuple[str,str]` elements and
  fails `mypy`. Fixed by nesting the params tuple: `(path, tuple(sorted(params.items())))` with a
  matching `dict[tuple[str, tuple[tuple[str, str], ...]], ...]` annotation. Cost if wrong: mypy
  gate fails; no runtime behavior change.
- **Ruling (Task 2.3 / formatting):** two docstrings from the plan's verbatim code exceeded the
  100-char line limit (`phase_label`'s docstring; `test_client.py`'s module docstring). Shortened
  wording to fit; no behavior change.
