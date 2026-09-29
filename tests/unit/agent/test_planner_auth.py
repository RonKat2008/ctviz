"""Guardrail: OpenAI 401/403 are credential failures -- never retried, never leak SDK text."""

import logging

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from ctviz.agent.planner import CLIENT_AUTH_MESSAGE, CLIENT_UNAVAILABLE_MESSAGE, Planner
from ctviz.api.app import app, get_planner
from ctviz.errors import LLMUnavailableError
from tests.unit.agent.test_planner import _fake_backend, _FakeParse, _install

KEY_FRAGMENT = "sk-proj-AbCd****wxyz"
SDK_MESSAGE = f"Incorrect API key provided: {KEY_FRAGMENT}"
REQUEST_BODY = {"query": "How many trials for this drug by phase?", "drug_name": "Pembrolizumab"}
AUTH_ERRORS = [
    (openai.AuthenticationError, 401),
    (openai.PermissionDeniedError, 403),
]


def _sdk_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(status, request=request, json={"error": {"message": SDK_MESSAGE}})
    return cls(message=SDK_MESSAGE, response=response, body={"message": SDK_MESSAGE})


@pytest.mark.parametrize(("cls", "status"), AUTH_ERRORS)
def test_auth_failure_is_not_retried_and_uses_the_fixed_auth_message(
    cls: type[openai.APIStatusError], status: int, caplog: pytest.LogCaptureFixture
) -> None:
    backend = _fake_backend()
    parse = _FakeParse(_sdk_error(cls, status), _sdk_error(cls, status))
    _install(backend, parse)

    with caplog.at_level(logging.ERROR), pytest.raises(LLMUnavailableError) as info:
        backend.complete("system", "user")

    assert len(parse.calls) == 1
    assert str(info.value) == CLIENT_AUTH_MESSAGE
    assert CLIENT_AUTH_MESSAGE != CLIENT_UNAVAILABLE_MESSAGE
    assert "credentials" in CLIENT_AUTH_MESSAGE
    assert "sk-" not in str(info.value)
    assert f"planner auth failed: {cls.__name__} status={status}" in caplog.text
    assert "sk-" not in caplog.text
    assert SDK_MESSAGE not in caplog.text


@pytest.mark.parametrize(("cls", "status"), AUTH_ERRORS)
def test_auth_failure_api_response_is_503_with_the_fixed_message_and_no_key(
    cls: type[openai.APIStatusError], status: int
) -> None:
    backend = _fake_backend()
    _install(backend, _FakeParse(_sdk_error(cls, status)))
    app.dependency_overrides[get_planner] = lambda: Planner(backend)
    try:
        with TestClient(app) as test_client:
            response = test_client.post("/v1/visualize", json=REQUEST_BODY)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_UNAVAILABLE"
    assert response.json()["error"]["message"] == CLIENT_AUTH_MESSAGE
    assert "sk-" not in response.text
