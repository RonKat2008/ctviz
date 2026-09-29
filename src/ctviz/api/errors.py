"""Exception -> (HTTP status, `VisualizeResponse`) mapping (§12.2). No stack traces leave here."""

import logging

from ctviz.errors import (
    CitationCheckError,
    CtvizError,
    LLMUnavailableError,
    OutOfScopeError,
    PlanInvalidError,
    UpstreamError,
)
from ctviz.schemas.response import ErrorInfo, VisualizeResponse

log = logging.getLogger(__name__)

# Domain outcomes stay HTTP 200 (ok:false); only transport-level failures get a non-200 status.
_STATUS_AND_CODE: dict[type[CtvizError], tuple[int, str]] = {
    UpstreamError: (502, "UPSTREAM_API_ERROR"),
    LLMUnavailableError: (503, "LLM_UNAVAILABLE"),
    OutOfScopeError: (200, "OUT_OF_SCOPE"),
    PlanInvalidError: (200, "PLAN_INVALID"),
    CitationCheckError: (500, "CITATION_CHECK_FAILED"),
}


def _log_failure(exc: Exception, status: int) -> None:
    """A 200 is a domain outcome, not a bug: log it at warning with no traceback. A real failure
    (502/503/500) logs the full traceback for on-call debugging."""
    if status == 200:
        log.warning("Pipeline domain outcome: %s", exc)
    else:
        log.exception("Pipeline failure")


def to_response(exc: Exception) -> tuple[int, VisualizeResponse]:
    """Map a pipeline exception to an HTTP status and a `VisualizeResponse`."""
    for exc_type, (status, code) in _STATUS_AND_CODE.items():
        if isinstance(exc, exc_type):
            _log_failure(exc, status)
            return status, VisualizeResponse.failure(ErrorInfo(code=code, message=str(exc)))  # type: ignore[arg-type]
    log.exception("Unmapped pipeline failure")
    return 500, VisualizeResponse.failure(
        ErrorInfo(code="INTERNAL_ERROR", message="Internal error.")
    )
