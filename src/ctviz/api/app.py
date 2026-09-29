"""FastAPI app: `POST /v1/visualize` and `GET /health`, one pooled `CtGovClient` per process."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ctviz.agent.judge import Judge, build_judge
from ctviz.agent.planner import Planner, build_planner
from ctviz.api.errors import to_response
from ctviz.api.rate_limit import RateLimiter, client_key
from ctviz.api.replay import build_replay_ctgov_client, build_replay_judge, build_replay_planner
from ctviz.config import Settings, get_settings
from ctviz.ctgov.client import CtGovClient
from ctviz.errors import CtvizError, RateLimitedError
from ctviz.pipeline import run_pipeline
from ctviz.pipeline_meta import resolve_code_version
from ctviz.schemas.request import VisualizeRequest
from ctviz.schemas.response import ErrorInfo, VisualizeResponse

log = logging.getLogger(__name__)
GZIP_MINIMUM_SIZE = 1000  # §11.7: GZipMiddleware is always on above this many response bytes


def _build_ctgov_client(settings: Settings) -> CtGovClient:
    """Task 8.1: `PLANNER_MODE=replay` swaps in the offline, fixture-backed client."""
    if settings.planner_mode == "replay":
        return build_replay_ctgov_client()
    return CtGovClient()


def _build_planner(settings: Settings) -> Planner:
    """Task 8.1: `PLANNER_MODE=replay` swaps in the canned-plan planner (no OpenAI call, ever)."""
    if settings.planner_mode == "replay":
        return build_replay_planner(settings)
    return build_planner(settings)


def _build_judge(settings: Settings) -> Judge:
    """Task 8.1: `PLANNER_MODE=replay` swaps in a judge that always reports a clean pass."""
    if settings.planner_mode == "replay":
        return build_replay_judge()
    return build_judge(settings)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open one pooled `CtGovClient` and build the planner and judge once for the process.

    Building them here -- not in a per-request dependency -- means a request with an invalid
    body never has to wait on (or fail from) SDK-client construction: the dependencies below just
    return what was already built. A missing key never fails startup: `build_planner` defers that
    failure to the first `.plan()` call, and `build_judge` returns a judge that reports
    "unavailable" on every review, so requests are still answered (fail open, §9.5).

    Task 8.1: `PLANNER_MODE=replay` swaps all three for offline, keyless fakes (`api/replay.py`),
    so `make demo-offline` needs no `.env`, no OpenAI/OpenRouter key, and no network at all.
    """
    settings = get_settings()
    await asyncio.to_thread(resolve_code_version)  # git SHA once, off the loop, before requests
    app.state.ctgov_client = _build_ctgov_client(settings)
    app.state.planner = _build_planner(settings)
    app.state.judge = _build_judge(settings)
    app.state.rate_limiter = RateLimiter(
        per_minute=settings.rate_limit_per_minute, per_day=settings.rate_limit_per_day
    )
    app.state.trust_forwarded_for = settings.rate_limit_trust_forwarded_for
    try:
        yield
    finally:
        await app.state.ctgov_client.aclose()


app = FastAPI(title="ctviz", lifespan=_lifespan)
app.add_middleware(GZipMiddleware, minimum_size=GZIP_MINIMUM_SIZE)


def get_ctgov_client(request: Request) -> CtGovClient:
    """FastAPI dependency: the process-wide client (overridable in tests)."""
    client: CtGovClient = request.app.state.ctgov_client
    return client


def get_planner(request: Request) -> Planner:
    """FastAPI dependency: the process-wide planner built once at startup (overridable)."""
    planner: Planner = request.app.state.planner
    return planner


def get_judge(request: Request) -> Judge:
    """FastAPI dependency: the process-wide judge built once at startup (overridable)."""
    judge: Judge = request.app.state.judge
    return judge


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
    headers = {"Retry-After": str(exc.retry_after_s)} if isinstance(exc, RateLimitedError) else None
    return JSONResponse(status_code=status, content=response.model_dump(), headers=headers)


@app.exception_handler(Exception)
async def _unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    """Anything else is our bug: 500 INTERNAL_ERROR, full traceback logged, never shown."""
    log.exception("Unhandled error")
    body = VisualizeResponse.failure(
        ErrorInfo(code="INTERNAL_ERROR", message="Internal error.")
    ).model_dump()
    return JSONResponse(status_code=500, content=body)


@app.get("/health")
async def health(client: Annotated[CtGovClient, Depends(get_ctgov_client)]) -> dict[str, object]:
    """Liveness probe (§8.4 task list, Task 8.1): which LLM providers are configured (booleans,
    never keys), which mode is running, and the ClinicalTrials.gov API version if already known.

    `ctgov` reads only what this process has already learned via a real `/v1/visualize` request
    (`CtGovClient.cached_api_version`) -- never a fresh network call -- so a liveness probe can
    never block on, or time out against, an external service. It is `null` until then, and always
    `null` in replay mode (genuinely offline, §8.4).
    """
    settings = get_settings()
    return {
        "ok": True,
        "ctgov": client.cached_api_version,
        "providers": {
            "openai": settings.openai_api_key is not None,
            "openrouter": settings.openrouter_api_key is not None,
        },
        "mode": settings.planner_mode,
    }


@app.get("/v1/schema")
async def schema() -> dict[str, object]:
    """§12.8: the request and response JSON Schemas (Pydantic v2), for clients that generate
    types or validate offline; the response schema's `visualization` union exports as
    `oneOf` + `discriminator` since every `Visualization` subtype is `Field(discriminator="type")`
    (schemas/viz.py)."""
    return {
        "request": VisualizeRequest.model_json_schema(),
        "response": VisualizeResponse.model_json_schema(),
    }


def get_rate_limiter(request: Request) -> RateLimiter:
    """The process-wide limiter built in the lifespan (a dependency so tests can override it)."""
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def enforce_rate_limit(
    request: Request, limiter: Annotated[RateLimiter, Depends(get_rate_limiter)]
) -> None:
    """Reject the request with 429 RATE_LIMITED when the caller or the daily cap is exhausted."""
    trust = bool(getattr(request.app.state, "trust_forwarded_for", False))
    decision = limiter.check(client_key(request, trust))
    if not decision.allowed:
        raise RateLimitedError(decision.retry_after_s)


@app.post("/v1/visualize", response_model=VisualizeResponse)
async def visualize(
    _: Annotated[None, Depends(enforce_rate_limit)],
    body: VisualizeRequest,
    planner: Annotated[Planner, Depends(get_planner)],
    judge: Annotated[Judge, Depends(get_judge)],
    client: Annotated[CtGovClient, Depends(get_ctgov_client)],
) -> VisualizeResponse:
    """Run the pipeline; failures are mapped by the registered exception handlers above."""
    today = datetime.now(UTC).date()
    return await run_pipeline(body, planner=planner, judge=judge, client=client, today=today)


# The web UI (web/) is served at "/" -- mounted LAST so /v1/* and /health keep precedence.
WEB_DIR = Path(__file__).resolve().parents[3] / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
