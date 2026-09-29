import httpx
import pytest
from openai import OpenAI
from pydantic import BaseModel

from ctviz.config import CTGOV_BASE_URL, get_settings

pytestmark = pytest.mark.live


class Ping(BaseModel):
    answer: str


def test_clinicaltrials_version_endpoint_responds() -> None:
    response = httpx.get(f"{CTGOV_BASE_URL}/version", timeout=10)

    assert response.status_code == 200
    assert "apiVersion" in response.json()


def test_openai_planner_model_supports_strict_structured_output() -> None:
    settings = get_settings()
    assert settings.openai_api_key is not None, "set OPENAI_API_KEY in .env"
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value())

    result = client.responses.parse(
        model=settings.planner_model,
        input=[{"role": "user", "content": "Reply with answer='pong'."}],
        text_format=Ping,
    )

    assert result.output_parsed is not None
    assert result.output_parsed.answer.lower() == "pong"


def test_openrouter_judge_model_returns_json_schema_output() -> None:
    settings = get_settings()
    assert settings.openrouter_api_key is not None, "set OPENROUTER_API_KEY in .env"
    client = OpenAI(
        api_key=settings.openrouter_api_key.get_secret_value(),
        base_url="https://openrouter.ai/api/v1",
    )

    completion = client.chat.completions.parse(
        model=settings.judge_model,
        messages=[{"role": "user", "content": "Reply with answer='pong'."}],
        response_format=Ping,
        temperature=0,
        extra_body={"provider": {"require_parameters": True}},
    )

    assert completion.choices[0].message.parsed is not None
