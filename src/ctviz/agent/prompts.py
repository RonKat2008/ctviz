"""Prompt assembly for the OpenAI planner (PLAN.md §8.2) and the OpenRouter judge (§9.2-9.3).

The system prompt is a static, cacheable prefix (role + catalog + rules + few-shots) so OpenAI's
prompt caching applies across requests. The user prompt is the only dynamic part: the question,
the *names* of structured fields present (never their values -- those are applied by the
overlay, not the LLM), today's date, and any revise feedback plus the previous plan. That
"previous plan" is always the planner's OWN prior raw output (before `apply_overlay` writes a
structured field's value into one of its slots) -- §8.2 is absolute: the planner never sees a
structured field's value, on the first call or on revise.
"""

import json
from datetime import date
from typing import Any

from ctviz.agent.overlay import FieldOverride
from ctviz.agent.plan_checks import OPTIONAL_ANALYSIS_MODIFIERS, REQUIRED_ANALYSIS_FIELDS
from ctviz.catalog.loader import Catalog
from ctviz.schemas.plan import QueryPlan
from ctviz.schemas.request import VisualizeRequest

ROLE = "You translate clinical-trial questions into a QueryPlan. You never compute data."

RULES = """Rules:
- Structured fields listed in the request are applied automatically; do not re-emit them.
- One search term per entity; pick the param whose "provides" note best matches.
- Prefer specific params over query.term.
- Comparisons vary exactly one param.
- Numbers in title/interpretation/assumptions only if they appear in the question, the
  structured fields, the entity names or a filter year of this plan -- never a computed count,
  a computed span ("last 5 years") or today's date. Code deletes any sentence breaking this.
- A vague time window (e.g. "recent") -> filters.start_year_min = today's year minus 5, plus
  one assumption that states that start year as a number (the only number it contains).
- Ambiguous entities (a place name that can mean more than one place, a sponsor name several
  organizations share or a company's subsidiaries use, an abbreviation with several
  expansions) -> pick the most likely reading and state it in assumptions. Never claim a
  narrowing the search can't do: a sponsor search matches every organization whose name
  contains the term, so say which organizations are included instead of naming just one.
- Out-of-scope questions (opinion, pricing, efficacy ranking) get answerable=false with nulls."""

COMPATIBILITY_TABLE = """Analysis x visualization compatibility:
- count_by -> bar_chart (or grouped_bar_chart when comparing cohorts)
- time_trend -> time_series
- histogram -> histogram
- scatter -> scatter_plot
- network -> network_graph
- trial_lookup / trial_list -> table or metric"""

FEW_SHOT_EXAMPLES = """Examples:
1. "Trials for adalimumab by phase" -> count_by, group_by=phase, bar_chart.
2. "How has the number of trials for dupilumab changed over time?" -> time_trend,
   time_field=start_date, granularity=year, time_series.
3. "Compare phases for tofacitinib vs baricitinib" -> comparison(vary_param=query.intr,
   values=[tofacitinib, baricitinib]), count_by, group_by=phase, grouped_bar_chart.
4. "Distribution of enrollment size for sickle cell disease trials" -> histogram,
   measure_x=enrollment.
5. "Network of sponsors and drugs for pancreatic cancer" -> network,
   network_type=sponsor_drug, network_graph.
6. "Details for NCT03548935" -> trial_lookup, table or metric.
7. "Which drug is more effective for rheumatoid arthritis?" -> answerable=false (efficacy
   ranking is out of scope; this API has no outcomes data)."""

FEEDBACK_LINE_MAX_CHARS = 300
FEEDBACK_MAX_LINES = 12

_STRUCTURED_FIELDS = (
    "drug_name",
    "condition",
    "sponsor",
    "trial_phase",
    "status",
    "country",
    "start_year",
    "end_year",
    "study_type",
    "nct_ids",
)


def build_planner_system(catalog: Catalog) -> str:
    """The static, cacheable system prompt: role, catalog, rules, compatibility, few-shots."""
    return "\n\n".join(
        [ROLE, catalog.render_for_planner(), RULES, COMPATIBILITY_TABLE, FEW_SHOT_EXAMPLES]
    )


def _structured_field_names(request: VisualizeRequest) -> list[str]:
    """Names (never values) of the structured fields present on the request (§8.2)."""
    return [name for name in _STRUCTURED_FIELDS if getattr(request, name) is not None]


def one_line(text: str, cap: int) -> str:
    """`text` with every line break/run of whitespace collapsed to one space, cut to `cap`."""
    flat = " ".join(text.split())
    return flat if len(flat) <= cap else flat[: cap - 1] + "\u2026"


def _compact_plan(plan: QueryPlan) -> str:
    """The previous plan as one line of JSON (null slots dropped), so feedback paths resolve."""
    dumped = plan.model_dump(mode="json", exclude_none=True)
    return json.dumps(dumped, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _feedback_block(feedback: list[str] | None, previous: QueryPlan | None) -> str:
    """The revise block (§8.2): every check/probe/judge issue as one capped line (fix F: LLM-
    authored text can't inject prompt lines), at most FEEDBACK_MAX_LINES, then the previous plan."""
    if not feedback:
        return ""
    shown = [f"- {one_line(issue, FEEDBACK_LINE_MAX_CHARS)}" for issue in feedback]
    lines = ["Revise your previous plan. Issues:", *shown[:FEEDBACK_MAX_LINES]]
    if len(shown) > FEEDBACK_MAX_LINES:
        lines.append(f"({len(shown) - FEEDBACK_MAX_LINES} more issue(s) omitted)")
    if previous is not None:
        lines.append(
            f"Previous interpretation: {one_line(previous.interpretation, FEEDBACK_LINE_MAX_CHARS)}"
        )
        lines.append(f"Previous plan: {_compact_plan(previous)}")
    return "\n".join(lines)


def build_planner_user(
    request: VisualizeRequest,
    feedback: list[str] | None = None,
    previous: QueryPlan | None = None,
    today: date | None = None,
) -> str:
    """The dynamic user prompt: question, structured field names, today's date, revise feedback."""
    fields = _structured_field_names(request)
    lines = [
        f"Question: {request.query}",
        f"Structured fields present (applied automatically): {', '.join(fields) or 'none'}",
        f"Today's date: {(today or date.today()).isoformat()}",
    ]
    feedback_block = _feedback_block(feedback, previous)
    if feedback_block:
        lines.append(feedback_block)
    return "\n".join(lines)


JUDGE_ROLE = (
    "You review a QueryPlan that another model wrote for a clinical-trial question. You never "
    "see data; you check that the plan asks ClinicalTrials.gov the question the user asked."
)


def _slot_rule() -> str:
    """Rubric check 3's slot list, rendered from plan_checks' own tables (never hand-copied)."""
    kinds = "; ".join(
        f"{kind.value} -> {', '.join(fields) or 'none'}"
        for kind, fields in REQUIRED_ANALYSIS_FIELDS.items()
    )
    modifiers = ", ".join(OPTIONAL_ANALYSIS_MODIFIERS)
    return (
        f"Each analysis kind requires exactly these slots: {kinds}. Any other of those slots is "
        f"cleared by code. The optional modifiers {modifiers} are allowed with every kind -- "
        "never ask to fill or clear them."
    )


JUDGE_RUBRIC = f"""Rubric -- answer every one of these seven checks, always, each with a short quote
(from the question, the structured fields, the plan or the probe totals) as evidence:
1. filter_fidelity: every entity in the question is mapped to the right param (drug -> query.intr,
   condition -> query.cond, "run by X" -> query.lead, "involving X" -> query.spons). The probe
   totals are evidence: a drug searched as a condition usually returns 0 or a tiny count.
2. no_invented_filters: every filter traces back to the question or the structured fields.
3. dimension_match: the group-by is what the user asked to break down or compare by.
   {_slot_rule()}
4. viz_fit: the chart suits the analysis (trend -> time series; relationships -> network;
   distribution of a numeric -> histogram).
5. time_range: "since <year>" -> start_year_min=<year>, unless a field override shows a
   structured field set it. A vague time window -> a concrete default stated in assumptions.
6. comparison_cohorts: "A vs B" varies exactly the right param with the right values.
7. ambiguity_handled: real ambiguities -- a place name that can mean more than one place, a
   sponsor name several organizations share, an abbreviation with several expansions, a vague
   time word -- are resolved AND the resolution is stated in assumptions. An ambiguity counts as
   RESOLVED when the plan either picks one reading and states it in assumptions, or includes all
   readings and states that it did; only a SILENT choice that changes the counted set fails this
   check. A documented substitution (e.g. an unsupported network type replaced by a
   supported one) passes."""

JUDGE_OUTPUT_RULES = """Output rules:
- Structured fields and field overrides are authoritative: they were applied by code on the
  user's explicit instruction. Never raise an issue against a slot they filled.
- Every issue carries an evidence_quote: a short VERBATIM quote copied from the question, the
  structured fields, the plan or the probe totals that shows the problem. An issue whose quote
  cannot be found there is discarded by code, so never paraphrase or invent one.
- Each issue names its plan_path (e.g. "search_terms[0].param") and a concrete suggested_fix
  (e.g. 'set search_terms[0].param = "query.intr"').
- severity: critical = the chart would answer a different question (wrong entity or param, an
  invented filter that removes trials); major = a materially wrong dimension, chart, time range
  or cohort, or a silent (unstated) resolution of a real ambiguity that decides which trials are
  counted (plan_path "assumptions"); minor = wording or a harmless choice.
- Do not flag style, titles or phrasing as critical or major."""


def build_judge_system(catalog: Catalog) -> str:
    """The static judge system prompt: role, the §9.3 rubric, output rules, catalog summary."""
    return "\n\n".join([JUDGE_ROLE, JUDGE_RUBRIC, JUDGE_OUTPUT_RULES, catalog.render_for_planner()])


def _structured_values(request: VisualizeRequest) -> dict[str, Any]:
    """The structured fields the caller supplied (the judge, unlike the planner, sees values)."""
    values = {name: getattr(request, name) for name in _STRUCTURED_FIELDS}
    present = {name: value for name, value in values.items() if value is not None}
    if request.sponsor is not None:
        present["sponsor_role"] = request.sponsor_role
    return present


def _as_json(value: Any) -> str:
    """Stable, readable JSON for a prompt section."""
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def build_judge_user(
    request: VisualizeRequest,
    plan: QueryPlan,
    overrides: list[FieldOverride],
    probe_totals: dict[str, int],
    today: date,
) -> str:
    """The dynamic judge prompt: the verbatim question, fields, overrides, probe totals, plan."""
    sections = [
        f"Question (verbatim): {request.query}",
        f"Structured fields (authoritative):\n{_as_json(_structured_values(request))}",
        f"Field overrides (authoritative):\n{_as_json([o.model_dump() for o in overrides])}",
        f"Probe totals (trials per cohort):\n{_as_json(probe_totals)}",
        f"Today's date: {today.isoformat()}",
        f"QueryPlan:\n{_as_json(plan.model_dump(mode='json'))}",
    ]
    return "\n\n".join(sections)
