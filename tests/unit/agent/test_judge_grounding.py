"""Evidence grounding: a judge issue survives only if its verbatim quote appears in the context
the judge was given (query, structured fields, plan JSON, probe totals)."""

from ctviz.agent.judge import Judge
from ctviz.agent.orchestrator import _judge_event
from ctviz.agent.prompts import build_judge_system
from ctviz.catalog.loader import load_catalog
from ctviz.schemas.judge import JudgeIssue
from ctviz.schemas.request import VisualizeRequest
from tests.factories import make_plan
from tests.unit.agent.test_judge import ScriptedJudgeBackend, issue, verdict

_REQUEST = VisualizeRequest(query="Pembrolizumab trials by phase", country="Canada")


def _review(quote: str, severity: str = "major"):  # type: ignore[no-untyped-def]
    judge = Judge(ScriptedJudgeBackend(verdict([issue(severity, quote=quote)])), "judge")
    return judge.review(_REQUEST, make_plan(), [], {"pembrolizumab": 2960})


def test_a_fabricated_quote_is_discarded_and_does_not_revise() -> None:
    review = _review("the user explicitly asked for Fabricated Sponsor Inc")

    assert review.needs_revision is False
    assert review.issues == ()
    assert review.discarded_ungrounded == 1


def test_a_real_quote_from_the_query_is_kept() -> None:
    review = _review("Pembrolizumab trials")

    assert review.needs_revision is True
    assert len(review.issues) == 1
    assert review.discarded_ungrounded == 0


def test_a_real_quote_from_the_plan_json_is_kept() -> None:
    assert _review('"search_terms"').needs_revision is True


def test_a_quote_from_probe_totals_or_structured_values_is_kept() -> None:
    assert _review("2960").needs_revision is True
    assert _review("Canada").needs_revision is True


def test_case_whitespace_and_edge_punctuation_still_match() -> None:
    review = _review('  "PEMBROLIZUMAB\n   trials," ')

    assert review.needs_revision is True
    assert review.discarded_ungrounded == 0


def test_an_empty_quote_is_ungrounded() -> None:
    for quote in ("", "   ", '"."'):
        review = _review(quote)
        assert review.needs_revision is False
        assert review.discarded_ungrounded == 1


def test_grounded_issue_survives_next_to_a_fabricated_one() -> None:
    issues = [issue("major", quote="made up"), issue("minor", quote="Pembrolizumab")]
    judge = Judge(ScriptedJudgeBackend(verdict(issues)), "judge")

    review = judge.review(_REQUEST, make_plan(), [], {})

    assert review.needs_revision is False  # only the minor one survived
    assert len(review.issues) == 1
    assert review.discarded_ungrounded == 1


def test_the_count_is_in_the_judge_trace_event() -> None:
    review = _review("nothing like this anywhere")

    event = _judge_event(review, 1)

    assert event["discarded_ungrounded_issues"] == 1
    assert "discarded_structured_slot_issues" in event


def test_issue_schema_requires_an_evidence_quote() -> None:
    assert "evidence_quote" in JudgeIssue.model_fields


def test_rubric_states_the_resolved_ambiguity_rule_without_eval_entities() -> None:
    system = " ".join(build_judge_system(load_catalog()).casefold().split())

    assert "resolved" in system
    assert "picks one reading" in system
    assert "includes all readings" in system
    assert "only a silent choice" in system
