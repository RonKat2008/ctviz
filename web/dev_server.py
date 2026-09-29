"""Dev-only launcher: the ctviz API plus this static frontend on one origin, without touching src/.

    PLANNER_MODE=replay uv run python web/dev_server.py        # keyless offline demo
    uv run python web/dev_server.py                            # live (needs .env keys)
    PORT=8765 uv run python web/dev_server.py

Once `src/ctviz/api/app.py` mounts `web/` itself (see web/README.md), this file is unnecessary.
"""

import os
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

from ctviz.api.app import app

WEB_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8765

# Mounted last so /health, /v1/*, /docs keep priority; skip if app.py already mounts the frontend.
if not any(isinstance(route, Mount) and route.path in ("", "/") for route in app.routes):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", DEFAULT_PORT)))
