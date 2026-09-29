"""FastAPI app: `POST /v1/visualize` and `GET /health`, one pooled `CtGovClient` per process."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ctviz.agent.planner import Planner, build_planner
from ctviz.api.errors import to_response
from ctviz.config import get_settings
from ctviz.ctgov.client import CtGovClient
from ctviz.errors import CtvizError
from ctviz.pipeline import run_pipeline
from ctviz.schemas.request import VisualizeRequest
from ctviz.schemas.response import ErrorInfo, VisualizeResponse

log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open one pooled `CtGovClient` and build the planner once for the process (§S4 review).

    Building the planner here -- not in a per-request dependency -- means a request with an
    invalid body never has to wait on (or fail from) planner/OpenAI-client construction: the
    `get_planner` dependency below just returns what was already built. A missing API key still
    doesn't fail startup: `build_planner` defers that failure to the first `.plan()` call.
    """
    app.state.ctgov_client = CtGovClient()
    app.state.planner = build_planner(get_settings())
    try:
        yield
    finally:
        await app.state.ctgov_client.aclose()


app = FastAPI(title="ctviz", lifespan=_lifespan)


def get_ctgov_client(request: Request) -> CtGovClient:
    """FastAPI dependency: the process-wide client (overridable in tests)."""
    client: CtGovClient = request.app.state.ctgov_client
    return client


def get_planner(request: Request) -> Planner:
    """FastAPI dependency: the process-wide planner built once at startup (overridable)."""
    planner: Planner = request.app.state.planner
    return planner


def _field_error(error: dict[str, Any]) -> str:
    """One readable 'field: reason' line from a single pydantic error dict."""
    loc = ".".join(str(part) for part in error["loc"] if part != "body")
    return f"{loc}: {error['msg']}"


def _readable_validation_message(exc: RequestValidationError) -> str:
    """Every invalid field, one per line, instead of the raw pydantic `.errors()` repr."""
    return "; ".join(_field_error(error) for error in exc.errors())


@app.exception_handler(RequestValidationError)
async def _invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    """422 in our envelope shape instead of FastAPI's default body (§12.2)."""
    body = VisualizeResponse.failure(
        ErrorInfo(code="INVALID_REQUEST", message=_readable_validation_message(exc))
    ).model_dump()
    return JSONResponse(status_code=422, content=body)


@app.exception_handler(CtvizError)
async def _domain_error(_: Request, exc: CtvizError) -> JSONResponse:
    """Every expected pipeline failure: mapped by `api.errors.to_response` (§12.2)."""
    status, response = to_response(exc)
    return JSONResponse(status_code=status, content=response.model_dump())


@app.exception_handler(Exception)
async def _unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    """Anything else is our bug: 500 INTERNAL_ERROR, full traceback logged, never shown."""
    log.exception("Unhandled error")
    body = VisualizeResponse.failure(
        ErrorInfo(code="INTERNAL_ERROR", message="Internal error.")
    ).model_dump()
    return JSONResponse(status_code=500, content=body)


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness probe: which LLM providers are configured, as booleans, never their keys."""
    settings = get_settings()
    return {
        "status": "ok",
        "openai_configured": settings.openai_api_key is not None,
        "openrouter_configured": settings.openrouter_api_key is not None,
    }


@app.post("/v1/visualize", response_model=VisualizeResponse)
async def visualize(
    body: VisualizeRequest,
    planner: Annotated[Planner, Depends(get_planner)],
    client: Annotated[CtGovClient, Depends(get_ctgov_client)],
) -> VisualizeResponse:
    """Run the pipeline; failures are mapped by the registered exception handlers above."""
    return await run_pipeline(body, planner=planner, client=client, today=datetime.now(UTC).date())
