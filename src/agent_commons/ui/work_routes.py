"""Public shell and static assets for the isolated React Work application."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from agent_commons.ui import read_work_shell, work_static_directory
from agent_commons.ui.security import work_content_security_policy


def register_work_routes(app: FastAPI) -> None:
    """Attach the Work shell and its data-free same-origin assets flat on ``app``.

    Work intentionally adds no API routes. Its client exchanges the usual fragment
    code for an HTTP-only session and then uses the existing process-private API
    base, so lifecycle and authority checks remain with their established handlers.
    The committed bundle is required here: ``check_dir=True`` fails startup rather
    than serving a shell whose assets do not exist.
    """

    work_directory = work_static_directory()
    app.mount(
        "/work/assets",
        StaticFiles(directory=work_directory / "assets"),
        name="work-assets",
    )

    @app.get("/work", response_class=HTMLResponse)
    @app.get("/work/", response_class=HTMLResponse)
    async def work() -> Response:
        response = HTMLResponse(read_work_shell())
        response.headers["Content-Security-Policy"] = work_content_security_policy()
        return response
