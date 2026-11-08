"""Infrastructure checks: API/static routing, missing builds, and file boundaries."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from catan_server.app import create_app


def test_api_works_without_a_frontend_build(tmp_path: Path) -> None:
    with TestClient(create_app(frontend_dir=tmp_path)) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert client.get("/").status_code == 503


@pytest.fixture
def built_frontend(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>Frontend fixture</h1>", encoding="utf-8")
    assets = dist / "assets"
    assets.mkdir()
    (assets / "main.js").write_text("console.log('fixture');", encoding="utf-8")
    (tmp_path / "private.txt").write_text("Do not serve this.", encoding="utf-8")
    return dist


def test_built_frontend_and_api_share_one_server(built_frontend: Path) -> None:
    with TestClient(create_app(frontend_dir=built_frontend)) as client:
        assert "Frontend fixture" in client.get("/").text
        assert client.get("/assets/main.js").status_code == 200
        assert client.get("/api/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/unknown",
        "/assets/missing.js",
        "/testing",
        "/secret",
        "/private.txt",
        "/%2e%2e/private.txt",
        "/assets/%2e%2e/%2e%2e/private.txt",
    ],
)
def test_missing_routes_and_external_files_are_not_served(built_frontend: Path, path: str) -> None:
    with TestClient(create_app(frontend_dir=built_frontend)) as client:
        response = client.get(path)
        assert response.status_code == 404
        assert "Do not serve this." not in response.text
