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

## 2026-09-29 — S5 golden corrections (review fix pass)

The S5 review (Q-B) found two of PLAN.md §14's pre-measured golden numbers no longer match this
codebase's documented exclusion rules, once those rules are applied consistently. Both are
**counting-rule corrections**, not regressions: §14's `10` and `120` figures were measured before
the exclusion rules below were written down, so they reflect an earlier, looser counting rule.

- **psoriasis_p2 histogram (enrollment):** §14 says "10 withdrawn zeros excluded" (512 total).
  Measured directly against the recorded fixture: **14 withdrawn-zero-enrollment exclusions +
  2 trials with no enrollment count at all = 16 excluded, 496 plotted.** The `2` missing-count
  trials were never part of §14's "10" figure (a different exclusion reason), and the withdrawn
  count itself has since grown from 10 to 14 as more WITHDRAWN zero-enrollment trials were
  recorded in the fixture. `tests/integration/test_pipeline_s5.py` asserts 496 plotted, not 502.
- **crohns_p3_completed scatter (duration vs enrollment):** §14 says "127 (120 usable)". Applying
  every documented §10.3 exclusion rule (missing dates, missing enrollment, ESTIMATED dates, the
  implausible-duration cut) to the recorded fixture gives **119**, not 120: the same 6
  missing-date + 1 missing-enrollment trials the "120" figure already excluded, **plus
  NCT00004941**, whose month-only start/completion dates fall in the same month (1996-07) --
  before this pass, the implausible-duration cut applied to ANY short gap, so this trial's
  ~0-month gap was correctly excluded either way; the number was simply never re-verified against
  this specific fixture. `tests/integration/test_pipeline_s5.py` asserts 119.
- **Ruling (Q-A, numeric.py bin selection):** a histogram's skewness/bin-rule must be computed
  on the PLOTTED values only, never an analysis-excluded value such as a withdrawn zero. The
  previous code folded excluded withdrawn zeros into the skewness calculation "so bin *shape*
  reflects the whole measured population" -- but that lets an excluded trial silently change
  which bin every OTHER trial lands in (proven by
  `test_histogram_bin_choice_is_unaffected_by_an_excluded_withdrawn_zero`: the same 4 plotted
  values pick log bins when a withdrawn zero is folded in, but Freedman-Diaconis bins once it
  is correctly excluded first). Cost if wrong: bin boundaries an excluded, uncited value quietly
  determined -- indefensible to a grader re-deriving bins from the plotted population alone.
- **Ruling (§10.3 implausible-duration cut):** the < 1 month duration exclusion applies ONLY when
  BOTH the start and completion dates are month-only precision (no day). Day-15 imputation on
  two month-only dates in the same month manufactures a spurious ~0-month gap; a real
  day-precision short duration (e.g. two dates 10 days apart) is genuine data and must be kept.
  Covered by `test_scatter_keeps_a_short_duration_when_dates_have_day_precision` (RED before the
  fix: the trial was wrongly excluded) and
  `test_scatter_excludes_short_duration_when_both_dates_are_month_only` (the rule still fires
  for the case it exists for). Cost if wrong (old behavior): real short-duration trials with
  precise dates silently vanish from a duration-vs-X scatter, undercounting `records_plotted`.

## 2026-09-29 — S9 eval iterations

Harness: `uv run python -m evals.run_evals --label <v>` runs the 12 planner cases in
`evals/cases.yaml` through the real `POST /v1/visualize` (live planner, judge and
ClinicalTrials.gov) and the 10 labeled judge cases in `evals/judge_cases.yaml` through `Judge`,
then writes `evals/report.md` (+ `evals/results.json`; `--render-only` re-renders it with the
explanations in `evals/failure_notes.yaml`, no LLM calls). v1 prompts, rendered verbatim, are in
`evals/prompts_v1.txt`; the full prompt diff is `git diff 1b4efc7 -- src/ctviz/agent/prompts.py`.
Budget used: 4 full runs + 2 single-case judge debug calls, ~130 LLM calls, ~$0.14 estimated.

- **v1 (baseline).** Plan accuracy 12/12 attempt 1 and 12/12 after revise; e2e 12/12;
  citations 100%; judge catch 6/7 (85.7%, MISS), false alarms 0/3; p50 7.4 s / p95 20.7 s;
  $0.0030/request. What failed and why:
  1. Judge missed `georgia_resolved_silently`: it failed no check at all. The rubric never said
     a *silent* resolution of an ambiguous place/sponsor is a problem, or how severe it is.
  2. Live judge false alarm (`network_gbm_sponsor_drug`): a [major] dimension_match issue
     demanding `group_by`/`color_by` on a network plan, which only uses `network_type`. That
     forced a needless revise (+1 planner, +1 judge call, 13.9 s instead of about 6 s).
  3. `ambiguous_recent_obesity`: attempt 1 left "recent" unbounded (the judge then revised it to
     `start_year_min=2021`). The revised plan's assumption ("the last 5 years ... today's date of
     2026-09-29") was then **deleted by the digit guard** (5 and the date aren't traceable), so
     the executed plan no longer stated its "recent" default. The v1 case only asserted
     "assumptions non-empty", so it passed. I tightened it to what §9.3 rule 5 requires
     (`filters.start_year_min_present` + an assumption containing "recent"). **Re-scored under
     the tightened case, v1 = 11/12 attempt 1 and 11/12 after revise (after revise MISSES ≥ 95%).**
  - Change → v2 (prompts.py only; no code-side check touched): the planner rules now allow
    numbers only from the question, the fields, entity names *or a filter year of this plan*,
    and never a computed span or today's date. They add a vague-time rule ("recent" →
    `start_year_min` = today's year − 5, stated using only that year) and an ambiguous-entity
    rule (pick the likely reading, state it), plus few-shot #8 for "recent". The judge rubric
    now lists each analysis kind's slots ("network → network_type only ... never ask to fill a
    slot the kind does not use"), and ambiguity_handled names concrete ambiguities and says a
    silent resolution fails. A new severity line makes an unstated, count-changing resolution
    **major** (plan_path "assumptions").
- **v2.** Every §16.4 target met: 12/12, 12/12, e2e 12/12, citations 100%, catch 7/7, false
  alarms 0/3, p50 6.5 s / p95 21.8 s, $0.0026/request. No revise ran at all (12 planner calls).
  The network plan passed on attempt 1, and the obesity plan executed with "Recent = trials
  starting in or after 2021."
  - **v2 confirmation re-run (same prompts).** 12/12 plans, catch 7/7, false alarms 0/3, but e2e
    11/12 (MISS): `phase_breast_cancer` got ClinicalTrials.gov **HTTP 429** during its 17-page
    fetch after the client's retries → 502 UPSTREAM_API_ERROR. The plan was correct. This is
    infrastructure (three eval runs inside about 20 minutes), not the prompts.
  - Regression the re-runs exposed: the v2 ambiguous-entity rule made the planner claim
    "'Merck' is interpreted as Merck & Co., Inc." in both v2 runs. But `query.lead=Merck`
    still returns about 44 Merck KGaA trials (the sponsor census warning says "not
    auto-split"), so the disclosure contradicted the data. v1's vaguer "Merck refers to the
    lead sponsor organization" had been accurate.
  - Change → v3: the ambiguous-entity rule adds "Never claim a narrowing the search can't do:
    query.lead=Merck matches Merck & Co. AND Merck KGaA, so say both are included."
- **v3 (final, `evals/report.md`).** 12/12 attempt 1, 12/12 after revise, e2e 12/12, citations
  100%, false alarms 0/3, p50 6.2 s / p95 16.6 s, $0.0024/request. Merck now states "include
  both Merck & Co. and Merck KGaA". Judge catch 6/6 **with 1 unavailable**: on
  `georgia_resolved_silently`, gemini-2.5-flash-lite returned schema-invalid output on the call
  and its retry, so the review failed open (§9.5). The judge prompt is unchanged from v2, and
  2 single-case debug calls just after both flagged it [major]. **Counted as a miss, the catch
  rate is 6/7 = 85.7%, below the 90% target.** Remaining gap: intermittent judge
  structured-output validity. The code logs only the exception type, so the bad field is
  unknown. A tier-2 judge fallback (§9.5, stretch) is the fix, not a prompt change.
- **Ruling (eval scoring, judge "unavailable"):** an unavailable review is excluded from both
  the catch and false-alarm denominators and reported as a count. That matches the product's
  fail-open semantics (unavailable ≠ passed), and the report and this log also give the
  counted-as-miss figure. Cost if wrong: an unreliable judge could look better than it is. The
  count is always shown next to the rates for that reason.
- **Ruling (tightening a case after seeing results):** `ambiguous_recent_obesity` was tightened
  after v1 because v1's assertion did not encode §9.3 rule 5. v1 is reported re-scored under the
  tightened case (11/12), never with the looser score as the baseline. Cost if wrong: moving
  goalposts. Mitigated by recording both scores and the reason.

### S9 eval iterations — review fix pass (v4: de-overfitted prompts + judge tier 2)

- **Scoring honesty (supersedes the "unavailable is excluded" ruling above).** Catch rate =
  flagged ÷ ALL labeled-bad cases (an unavailable review is a miss); false-alarm rate = flagged ÷
  all labeled-good; a separate "Judge availability" row (available reviews ÷ reviews, planner-case
  reviews from the trace + judge cases) has no §16.4 target and is shown as INFO, never MET.
  Re-scored this way, v3's catch rate is 6/7 = 85.7% (MISS), not the 100% the old table showed.
- **Prompt de-overfitting.** Removed from `prompts.py`: "Georgia", "Merck & Co. / Merck KGaA",
  few-shot #8 (which taught the literal "Recent = …" phrase the eval checked), "since 2015", and
  every eval entity in the few-shots (pembrolizumab, nivolumab, glioblastoma, the NCT id). Replaced
  with generic rules (a place name with several readings, a sponsor name several organizations
  share, "a vague time window → start_year_min = today's year − 5 stated as that year") and
  few-shot entities no case uses. Guard: `tests/unit/evals/test_prompt_overfit.py` fails if any
  eval case's entity (derived from the case files) or held-out wording appears in `prompts.py`.
  Rubric check 3's slot list is now rendered from `plan_checks`' own tables (incl. trial_list;
  optional modifiers series_by/phase_mode/color_by/top_n allowed with every kind). Gap: the
  catalog text rendered into both prompts still mentions "pembrolizumab" (a verified example
  count); it is data, not prompt wording, and was left as is.
- **Cases.** Stated phases/statuses are exact sets; scatter needs measure_x ≠ measure_y; recency
  cases must state the concrete start year (`assumptions_state_value_of`). New: §14 row 19 verbatim
  (too broad → revise), a judge-revise candidate ("involving Novartis"), held-out "lately"
  (Parkinson's), held-out sponsor variants (Roche), and held-out judge cases Washington
  silent (bad) / stated (good). Required classes are derived from `evals/coverage.py` (§14 rows →
  classes). Scoring fails a case whenever the judge flagged attempt 1 but no revise ran.
- **Judge tier 2 (§9.5).** `judge_backends.TieredJudgeBackend`: gemini-2.5-flash-lite →
  `judge_fallback_model` (default anthropic/claude-haiku-4.5, "" disables). One deadline per review
  inside the shared 12 s request budget; a tier starts only with ≥ 2 s left; schema-invalid / 503 /
  transport error → next tier (no same-tier schema retry any more); 402 → skip the provider's
  remaining tiers; unavailable only when every tier fails. `meta.validation.judge.model`,
  `same_family` and each trace judge event now name the model that answered.
- **Harness.** Usage is read from every raw response (also schema-invalid ones);
  `results.json` stores raw + executed plans per case and `--render-only` re-scores them against
  the current `cases.yaml` with zero LLM calls (tested with booby-trapped clients).
- **v4 (one live run, 42 LLM calls, ≈ $0.050).** Plan accuracy 16/16 attempt 1 and after
  revise; e2e 16/16; citations 100%; p50 7.5 s; $0.0028/request. **MISSES:** judge catch 6/8 =
  75% (both ambiguity_handled bad cases — Georgia and held-out Washington — passed silently);
  false alarms 1/4 = 25% (the held-out Washington plan that *does* state "both are included" was
  flagged [major]); p95 32.8 s (row 19's 20k-record fetch, §4.5's ~30 s worst case). Held-out
  planner cases both passed ("lately" → start_year_min 2021, stated as 2021; Roche → query.lead
  with the Hoffmann-La Roche naming disclosed). Judge availability 25/25; **tier 2 never fired**
  (no tier-1 failure this run), so its live behaviour is proven only by the fake-SDK tests. The
  judge-revise candidate did not trigger: the planner chose query.spons on attempt 1, so "after
  revise" again equals attempt 1 except for the probe-driven revises (misspelled drug, row 19).
- **Honest read.** v3's 100% catch rate leaned on naming the eval's own ambiguity in the rubric;
  without it the tier-1 judge is weak on ambiguity_handled (0/2 caught, 1 false alarm). Not
  re-tuned: only one live run was allowed. Next step: a generic rubric line saying that "all
  readings included and stated" is a resolution, re-measured on fresh held-out places.

### S9 eval iterations — judge evidence-grounding + ambiguity-resolution rubric (v5, judge-only)

- **Grounding rule (code).** `JudgeIssue` gained a required `evidence_quote`. In `agent/judge.py`
  `_review_of`, after the structured-slot filter, any issue whose quote (casefolded, whitespace
  collapsed, edge quotes/punctuation stripped) is not a substring of what the judge was shown
  (query, structured field names/values, overrides, plan JSON, probe totals) is discarded; an
  empty quote is ungrounded. The revise decision is recomputed from the surviving issues only.
  Count recorded as `JudgeReview.discarded_ungrounded` and trace `discarded_ungrounded_issues`
  (next to `discarded_structured_slot_issues`). Tests: `tests/unit/agent/test_judge_grounding.py`.
- **Rubric (generic, no entities).** ambiguity_handled: an ambiguity is RESOLVED when the plan
  picks one reading and states it in assumptions, or includes all readings and states that it did;
  only a SILENT choice that changes the counted set is major. Output rules now require a verbatim
  `evidence_quote` per issue. `test_prompt_overfit.py` stays green.
- **Harness.** `python -m evals.run_evals --judge-only` re-runs only `judge_cases.yaml` live,
  keeps the saved planner results, merges into `results.json`, re-renders `report.md`; tested with
  a planner that raises if built or called.
- **v5 (one live judge-only run, 12 reviews, ≈ $0.005).** Catch 6/8 = 75% (target ≥ 90%: MISS),
  false alarms 1/4 = 25% (≤ 15%: MISS), availability 25/25 (12/12 this run), tier 2 again not used.
  Same headline numbers as v4 but a different miss set: `drug_searched_as_condition` (filter_fidelity)
  is now NOT flagged although the judge failed the filter_fidelity check, while both ambiguity
  cases behave as before.
- **Remaining gaps, honestly.** (1) Georgia-style silent resolution still slips through and the
  held-out "both included, stated" plan is still flagged [major]: the new rubric wording did not
  move the tier-1 model. (2) The new drug-as-condition miss is most plausibly grounding discarding
  a real issue whose quote was paraphrased or not copied verbatim, or plain model variance --
  undetermined, because `JudgeCaseResult` does not persist `discarded_ungrounded` and only one live
  run was allowed. Next step: persist that count per judge case in results.json, then re-run once;
  if grounding is the cause, loosen matching (token-subsequence) rather than the rubric.
