"""Static checks for the web frontend in web/: the page exists, CDN scripts are pinned with SRI,
every JS module parses, the exhibits cover every canned replay question, and no module uses
innerHTML (API text must only ever reach the DOM as text)."""

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount

from ctviz.api.app import app

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "web"
INDEX = WEB_DIR / "index.html"
PINNED = ("vega@5.33.1", "vega-lite@5.23.0", "vega-embed@6.29.0")
JS_FILES = sorted(WEB_DIR.rglob("*.js"))
CSS_REFS = re.compile(r'href="(styles/[^"]+\.css)"')


def test_index_exists_and_loads_app_module() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert '<script type="module" src="app.js"></script>' in html
    for ref in CSS_REFS.findall(html):
        assert (WEB_DIR / ref).is_file(), ref


def test_cdn_scripts_are_pinned_with_sri() -> None:
    html = INDEX.read_text(encoding="utf-8")
    scripts = re.findall(r"<script[^>]+src=\"(https://[^\"]+)\"[^>]*>", html)
    assert len(scripts) == len(PINNED)
    for pin in PINNED:
        assert f"https://cdn.jsdelivr.net/npm/{pin}/" in html, pin
    for tag in re.findall(r"<script[^>]+src=\"https://[^>]+>", html):
        assert 'integrity="sha384-' in tag and 'crossorigin="anonymous"' in tag, tag


def test_exhibits_cover_every_canned_replay_question() -> None:
    canned = json.loads((REPO_ROOT / "examples" / "canned_plans.json").read_text(encoding="utf-8"))
    examples_js = (WEB_DIR / "js" / "examples.js").read_text(encoding="utf-8")
    for example in canned["examples"]:
        assert json.dumps(example["query"]) in examples_js, example["query"]
    assert "Details of NCT02658279" in examples_js


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: str(p.relative_to(WEB_DIR)))
def test_js_never_uses_inner_html(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    assert "innerHTML" not in source and "insertAdjacentHTML" not in source


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: str(p.relative_to(WEB_DIR)))
def test_js_module_parses(path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; JS syntax check needs `node --check`")
    # Fed on stdin as --input-type=module so import/export parse without a package.json.
    result = subprocess.run(
        [node, "--input-type=module", "--check"],
        input=path.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_dev_server_mounts_frontend_after_api_routes() -> None:
    """dev_server.py adds the static mount to the shared app; removed afterwards so other API
    tests see the app exactly as src/ defines it."""
    before = list(app.router.routes)
    spec = importlib.util.spec_from_file_location("ctviz_web_dev_server", WEB_DIR / "dev_server.py")
    assert spec is not None and spec.loader is not None
    try:
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        assert isinstance(app.router.routes[-1], Mount)
        with TestClient(app) as client:
            page = client.get("/")
            assert page.status_code == 200 and "ctviz" in page.text
            assert client.get("/app.js").status_code == 200
            assert client.get("/js/charts/specs.js").status_code == 200
            assert client.get("/health").json()["ok"] is True
    finally:
        app.router.routes[:] = before
