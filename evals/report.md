# ctviz eval report

Generated 2026-09-29T20:53:40Z by `uv run python -m evals.run_evals` · prompts: **v4** · planner `gpt-5.4-mini` · judge `google/gemini-2.5-flash-lite` → tier 2 `anthropic/claude-haiku-4.5` · 16 planner cases, 12 judge cases.

## Targets (PLAN.md §16.4)

| Metric | Result | Target | Status |
|---|---|---|---|
| Plan accuracy, attempt 1 | 100.0% | ≥ 85% | MET |
| Plan accuracy, after revise | 100.0% | ≥ 95% | MET |
| End-to-end success (expected outcome) | 100.0% | ≥ 95% | MET |
| Citation check pass rate | 100.0% | 100% | MET |
| Judge catch rate (unavailable = miss) | 75.0% (6/8) | ≥ 90% | MISS |
| Judge false-alarm rate | 25.0% (1/4) | ≤ 15% | MISS |
| Judge availability (available reviews ÷ reviews) | 100.0% (25/25) | reported (no §16.4 target) | INFO |
| p50 latency | 7.5 s | ≤ 9 s | MET |
| p95 latency | 32.8 s | ≤ 30 s | MISS |
| Mean LLM cost per request | $0.0028 | ≤ $0.01 | MET |

## Cost and calls

- Planner cases: 17 planner calls, 13 judge reviews (one review may make several API calls: a 429 retry, a tier-2 fallback), $0.0451 total ($0.0028 per request).
- Judge cases: 12 reviews, $0.0051 total.
- Pricing assumption (USD per 1M tokens, from PLAN.md §4.5/§9.5): planner gpt-5.4-mini $0.75 input / $0.075 cached input (assumed 10% of input) / $4.50 output (reasoning tokens included); judge tier 1 gemini-2.5-flash-lite $0.10 input / $0.40 output; judge tier 2 claude-haiku-4.5 $1.00 input / $5.00 output. Token counts are the SDKs' own `usage` fields, read from every response received -- including ones that failed schema validation; a call that errored before any response has no usage and is not priced.
- Latency is wall-clock per HTTP request, live ClinicalTrials.gov fetches included; percentiles are nearest-rank.

## Planner cases

| Case | Class | Query | Plan @1 | Plan after revise | Outcome (expected → actual) | Judge status | Citations | Planner calls / judge reviews | Latency | Cost |
|---|---|---|---|---|---|---|---|---|---|---|
| trend_pembro_since_2015 | trend | How has the number of trials for pembrolizumab changed per year since 2015? | pass | pass | ok → ok (pass) | passed | pass | 1/1 | 9.4 s | $0.0048 |
| phase_breast_cancer | phase_distribution | How are breast cancer trials distributed across phases? | pass | pass | ok → ok (pass) | passed | pass | 1/1 | 20.3 s | $0.0020 |
| country_ms_recruiting | country_recruiting | Which countries have the most recruiting trials for multiple sclerosis? | pass | pass | ok → ok (pass) | passed | pass | 1/1 | 5.8 s | $0.0025 |
| histogram_psoriasis_p2 | histogram | What does the distribution of enrollment sizes look like for Phase 2 psoriasis trials? | pass | pass | ok → ok (pass) | passed | pass | 1/1 | 6.0 s | $0.0025 |
| scatter_crohns_p3_completed | scatter | Is there a relationship between enrollment and trial duration for completed Phase 3 Crohn's disease trials? | pass | pass | ok → ok (pass) | passed | pass | 1/1 | 5.4 s | $0.0026 |
| network_gbm_sponsor_drug | network | Show me a network of which sponsors are testing which drugs in glioblastoma | pass | pass | ok → ok (pass) | passed | pass | 1/1 | 7.5 s | $0.0022 |
| compare_pembro_nivo_phases | comparison | Compare phases for trials involving pembrolizumab vs nivolumab | pass | pass | ok → ok (pass) | passed | pass | 1/1 | 8.1 s | $0.0026 |
| sponsor_lead_merck_status | sponsor_lead | What share of Merck's Phase 3 trials were completed versus terminated? | pass | pass | ok → ok (pass) | passed | pass | 1/1 | 7.7 s | $0.0036 |
| nct_lookup_status | nct_lookup | What is the status of NCT04368728? | pass | pass | ok → ok (pass) | skipped | pass | 0/0 | 0.7 s | $0.0000 |
| misspelled_drug_field | misspelled_drug | How many trials for this drug are in each phase? | pass | pass | NO_MATCHING_TRIALS → NO_MATCHING_TRIALS (pass) | - | - | 2/0 | 7.3 s | $0.0048 |
| ambiguous_recent_obesity | ambiguous | Show me recent trends in obesity trials | pass | pass | ok → ok (pass) | passed | pass | 1/1 | 10.1 s | $0.0026 |
| out_of_scope_best_drug | out_of_scope | What's the best cancer drug? | pass | pass | OUT_OF_SCOPE → OUT_OF_SCOPE (pass) | - | - | 1/0 | 2.2 s | $0.0013 |
| row19_drugs_co_occur_verbatim | network_unscoped | Which drugs frequently co-occur in combination studies? | pass | pass | ok → ok (pass) | passed_after_revision | pass | 2/1 | 32.8 s | $0.0065 |
| judge_revise_involving_sponsor | judge_revise | How are trials involving Novartis distributed across phases? | pass | pass | ok → ok (pass) | passed | pass | 1/1 | 9.8 s | $0.0023 |
| held_out_recency_lately | held_out_recency | Have Parkinson's disease trials picked up lately? | pass | pass | ok → ok (pass) | passed | pass | 1/1 | 5.9 s | $0.0026 |
| held_out_sponsor_variants_roche | held_out_sponsor_variants | How are Roche's trials distributed across phases? | pass | pass | ok → ok (pass) | passed | pass | 1/1 | 7.9 s | $0.0024 |

## Judge cases

Catch rate 75.0% (6/8 labeled-bad flagged); false-alarm rate 25.0% (1/4 labeled-good flagged); 0 of 12 reviews unavailable -- each counts as a miss on a bad plan and as not flagged on a good one (see the availability row). 'Flagged' is the code-side decision: a critical/major issue survived the structured-slot filter.

Available reviews answered by model (planner cases + judge cases): google/gemini-2.5-flash-lite: 25.

| Case | Category | Label | Flagged | Correct | Failed checks | Expected check failed |
|---|---|---|---|---|---|---|
| drug_searched_as_condition | filter_fidelity | bad | no | FAIL | filter_fidelity | yes |
| invented_status_and_country | no_invented_filters | bad | yes | pass | no_invented_filters | yes |
| countries_asked_phase_grouped | dimension_match | bad | yes | pass | dimension_match, viz_fit | yes |
| relationships_as_bar_chart | viz_fit | bad | yes | pass | filter_fidelity, dimension_match, viz_fit | yes |
| since_2015_as_2005 | time_range | bad | yes | pass | time_range | yes |
| vs_nivolumab_compared_to_ipilimumab | comparison_cohorts | bad | yes | pass | comparison_cohorts | yes |
| georgia_resolved_silently | ambiguity_handled | bad | no | FAIL | - | no |
| structured_start_year_overrides_question | structured_override | good | no | pass | time_range | no |
| clean_trend | clean | good | no | pass | - | no |
| clean_comparison | clean | good | no | pass | - | no |
| held_out_washington_silent | ambiguity_handled | bad | yes | pass | comparison_cohorts | no |
| held_out_washington_stated | ambiguity_handled | good | yes | FAIL | ambiguity_handled | yes |

## Failures

### drug_searched_as_condition (judge, filter_fidelity, labeled bad)

- Judge: not flagged
- Explanation: _No explanation recorded for this run yet._

### georgia_resolved_silently (judge, ambiguity_handled, labeled bad)

- Judge: not flagged
- Explanation: (v4) A real judge miss, not an outage: the review was available (gemini-2.5-flash-lite answered) and passed every check. v3 caught this case only after the rubric named "Georgia" as the example ambiguity; S9-fix removed every eval entity from the prompts (overfit guard), and with the generic wording ("a place name that can mean more than one place") the tier-1 judge no longer flags a silently resolved place. This is the honest, non-overfit catch rate for ambiguity_handled. Not re-tuned: one live run was allowed after the de-overfitting.

### held_out_washington_stated (judge, ambiguity_handled, labeled good)

- Judge: flagged
- Issue: [major] assumptions[0]: The question asks about 'Washington' without specifying if it refers to Washington State or Washington D.C. The plan assumes both are included, but this ambiguity should be explicitly stated as a potential issue or resolved by asking the user for clarification.
- Explanation: (v4, held out) False alarm: the plan states the resolution ("both Washington State and Washington, D.C. are included") yet the judge raised a [major] ambiguity issue claiming it is not stated. The judge reads a stated "both are included" as unresolved; the rubric does not say that including every reading and saying so IS a resolution. Candidate generic fix for a later run (not applied, one run allowed): "stating that all readings are included resolves it".
