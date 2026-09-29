"""The single source of which eval classes the planner suite must cover (PLAN.md §14, §16.4).

`SECTION_14_CLASSES` is §14's "Class" column, row by row (19 rows). `EVAL_CLASS_FOR` maps each of
those §14 classes to the eval class whose case covers it (one case can cover several rows: e.g.
rows 8/9/12/17 are all "network"; row 19, the verbatim appendix question, is its own class
because it exercises the too-broad -> revise path). `EXTRA_CLASSES` are eval classes beyond the
§14 table, each with the spec line it exercises. REQUIRED_CLASSES is derived, never hand-listed.
"""

SECTION_14_CLASSES: dict[int, str] = {
    1: "time trend",
    2: "time trend",
    3: "distribution",
    4: "distribution",
    5: "comparison",
    6: "comparison",
    7: "geographic",
    8: "network",
    9: "network",
    10: "relationship",
    11: "numeric distribution",
    12: "network",
    13: "distribution + entity",
    14: "lookup",
    15: "out of scope",
    16: "zero result",
    17: "network",
    18: "distribution",
    19: "network, unscoped",
}
EVAL_CLASS_FOR: dict[str, str] = {
    "time trend": "trend",
    "distribution": "phase_distribution",
    "comparison": "comparison",
    "geographic": "country_recruiting",
    "network": "network",
    "relationship": "scatter",
    "numeric distribution": "histogram",
    "distribution + entity": "sponsor_lead",
    "lookup": "nct_lookup",
    "out of scope": "out_of_scope",
    "zero result": "misspelled_drug",
    "network, unscoped": "network_unscoped",
}
EXTRA_CLASSES: dict[str, str] = {
    "ambiguous": "§9.3 rule 5: a vague time word gets a stated, concrete default window",
    "judge_revise": "§9.4: a plan the judge is likely to flag, so 'after revise' is exercised",
    "held_out_recency": "generalization: vague time wording that appears nowhere in the prompts",
    "held_out_sponsor_variants": "generalization: a sponsor with name variants, not in prompts",
}
REQUIRED_CLASSES = frozenset(EVAL_CLASS_FOR[c] for c in SECTION_14_CLASSES.values()) | frozenset(
    EXTRA_CLASSES
)
