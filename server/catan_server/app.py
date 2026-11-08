"""Compose the API and built frontend without depending on the working directory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from catan_server.api import router

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(*, frontend_dir: Path = FRONTEND_DIST) -> FastAPI:
    """Build one application; explicit paths also allow isolated infrastructure tests."""
    app = FastAPI(title="Catan Ultimate")
    app.include_router(router)

    # Register API and future authenticated feature routes before this static mount.
    # StaticFiles confines requests to the build directory and does not expose sources.
    if (frontend_dir / "index.html").is_file():
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
