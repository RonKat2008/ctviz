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


class JudgeUnavailableError(CtvizError):
    """The judge could not be reached or answered unusably. Never reaches a client: the revise
    loop fails open on it (`judge.status = "unavailable"`, PLAN.md §9.5)."""


class OutOfScopeError(CtvizError):
    """The planner model refused the request outright (§8.3): a domain outcome, not a failure."""


class PlanInvalidError(CtvizError):
    """The plan still failed deterministic checks after the revise attempt."""

    def __init__(self, errors: list[str], details: dict[str, object] | None = None) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        self.details = details


class CitationCheckError(CtvizError):
    """The independent verifier found an inconsistency — a bug in our code, never user error."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__(f"{len(violations)} citation violation(s)")
        self.violations = violations
