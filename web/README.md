# ctviz web frontend

A static page (no build step, no npm) served next to the API: ask a question, get the cited
chart, click any mark to read its citations, and inspect the verifier, data coverage and
agent trace. Plain ES modules, plus Vega 5.33.1, Vega-Lite 5.23.0 and vega-embed 6.29.0
loaded from jsDelivr with pinned versions and SRI hashes.

## Mounting it in the API (required)

Add this to the **end** of `src/ctviz/api/app.py`, after every `@app.get/@app.post` route, so
`/health`, `/v1/*` and `/docs` keep priority (Starlette matches routes in the order they were
registered):

```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).resolve().parents[3] / "web"  # repo-root/web
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
```

`html=True` serves `web/index.html` at `/`. The page calls the API on the same origin
(`/health`, `/v1/visualize`), so no CORS setup is needed.

## Running it

| Mode | Command | Open |
|---|---|---|
| Offline replay (no keys, no network) | `make demo-offline` | http://127.0.0.1:8000/ |
| Live | `make run` | http://127.0.0.1:8000/ |
| Before the mount lands | `PLANNER_MODE=replay uv run python web/dev_server.py` | http://127.0.0.1:8765/ |

The page reads `/health` and shows a **Replay edition** or **Live edition** badge. Replay mode
answers only the canned questions in `examples/canned_plans.json` (plus `Details of NCT…`
lookups); anything else returns `PLAN_INVALID` with the available questions as clickable chips.

In replay mode, exhibit thumbnails load lazily as they scroll into view. In live mode each
exhibit costs real LLM calls, so exhibits run only when you open them.

## Layout

```
web/
  index.html            page shell, CSP, pinned CDN scripts
  app.js                bootstrap: health badge, form, viewer, exhibits, theme
  js/api.js             fetch + per-request cache (gallery and viewer share results)
  js/ask.js             form → VisualizeRequest, error cards for every error code
  js/viewer.js          figure + data table + trust rail + drawer wiring
  js/citations.js       paginated, filterable citation drawer
  js/trust.js           verifier, coverage, trace, notes, entities, provenance, raw JSON
  js/gallery.js         exhibit cards
  js/charts/specs.js    pure Vega-Lite spec builders (also run under Node for checks)
  js/charts/network.js  Vega force-directed network spec
  js/charts/render.js   vega-embed mount, HTML table/metric, accessible data table
  styles/*.css          tokens (light + dark), base, components, panels
  dev_server.py         dev-only: API + static mount without editing src/
```

All API text is inserted with `textContent`; there is no `innerHTML` anywhere.
