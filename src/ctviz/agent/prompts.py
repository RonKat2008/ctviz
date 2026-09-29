"""Prompt assembly for the OpenAI planner (PLAN.md §8.2).

The system prompt is a static, cacheable prefix (role + catalog + rules + few-shots) so OpenAI's
prompt caching applies across requests. The user prompt is the only dynamic part: the question,
the *names* of structured fields present (never their values -- those are applied by the
overlay, not the LLM), today's date, and any revise feedback.
"""

from datetime import date

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
  structured fields, or the entity names -- never a computed count.
- Out-of-scope questions (opinion, pricing, efficacy ranking) get answerable=false with nulls."""

COMPATIBILITY_TABLE = """Analysis x visualization compatibility:
- count_by -> bar_chart (or grouped_bar_chart when comparing cohorts)
- time_trend -> time_series
- histogram -> histogram
- scatter -> scatter_plot
- network -> network_graph
- trial_lookup / trial_list -> table or metric"""

FEW_SHOT_EXAMPLES = """Examples:
1. "Trials for pembrolizumab by phase" -> count_by, group_by=phase, bar_chart.
2. "How has the number of trials for pembrolizumab changed over time?" -> time_trend,
   time_field=start_date, granularity=year, time_series.
3. "Compare phases for pembrolizumab vs nivolumab" -> comparison(vary_param=query.intr,
   values=[pembrolizumab, nivolumab]), count_by, group_by=phase, grouped_bar_chart.
4. "Distribution of enrollment size for glioblastoma trials" -> histogram, measure_x=enrollment.
5. "Network of sponsors and drugs for glioblastoma" -> network, network_type=sponsor_drug,
   network_graph.
6. "Details for NCT02760485" -> trial_lookup, table or metric.
7. "Which drug is more effective for lung cancer?" -> answerable=false (efficacy ranking is
   out of scope; this API has no outcomes data)."""

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


def _feedback_block(feedback: list[str] | None, previous: QueryPlan | None) -> str:
    """The revise block: the previous interpretation plus every check/probe/judge issue."""
    if not feedback:
        return ""
    lines = ["Revise your previous plan. Issues:"] + [f"- {issue}" for issue in feedback]
    if previous is not None:
        lines.append(f"Previous interpretation: {previous.interpretation}")
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
