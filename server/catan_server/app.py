"""Compose the API and built frontend without depending on the working directory."""

import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from catan_server.api import router
from catan_server.engine import RuleError
from catan_server.session import Session

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(*, frontend_dir: Path = FRONTEND_DIST, data_dir: Path | None = None) -> FastAPI:
    """Build one application; explicit paths also allow isolated infrastructure tests."""

    @asynccontextmanager
    async def lifespan(app):
        directory = data_dir or Path(
            os.getenv("CATAN_DATA_DIR", str(FRONTEND_DIST.parents[1] / "data"))
        )
        app.state.session = Session(directory)
        await app.state.session.start()
        try:
            yield
        finally:
            await app.state.session.close()

    app = FastAPI(title="Catan Ultimate", lifespan=lifespan)
    buckets = defaultdict(list)

    @app.exception_handler(RuleError)
    async def rule_error(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse(status_code=400, content={"detail": "Invalid field value"})

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse(
                    status_code=403, content={"detail": "Cross-origin request rejected"}
                )
            key = (request.client.host if request.client else "local", request.url.path)
            now = time.monotonic()
            if len(buckets) > 2048:
                buckets.clear()
            buckets[key] = [at for at in buckets[key] if at > now - 60]
            if len(buckets[key]) >= (20 if request.url.path == "/api/join" else 600):
                return JSONResponse(
                    status_code=429, content={"detail": "Too many requests; wait a minute"}
                )
            buckets[key].append(now)
        try:
            return await call_next(request)
        except (KeyError, TypeError, AttributeError, IndexError) as exc:
            return JSONResponse(
                status_code=400, content={"detail": f"Malformed request ({type(exc).__name__})"}
            )

    app.include_router(router)

    # Register API and future authenticated feature routes before this static mount.
    # StaticFiles confines requests to the build directory and does not expose sources.
    if (frontend_dir / "index.html").is_file():

        @app.get("/testing", include_in_schema=False)
        @app.get("/secret", include_in_schema=False)
        async def tools_page():
            return FileResponse(frontend_dir / "index.html")

        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    else:

        @app.get("/", include_in_schema=False)
        async def missing_frontend() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Frontend build missing. Run npm run build, or use npm run dev "
                    "and open http://127.0.0.1:5173."
                },
            )

    return app
