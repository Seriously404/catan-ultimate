"""Route registration. Add future HTTP and WebSocket feature routers here."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from catan_server.engine import RuleError, require

router = APIRouter()


@router.get("/api/health")
async def health() -> dict[str, str]:
    """Report process availability; this does not imply a game is running."""
    return {"status": "ok"}


def credentials(request: Request, owner: bool = False, member: bool = False) -> tuple:
    session = request.app.state.session
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    pid = session.identify(token)
    is_owner = session.is_owner(request.headers.get("x-host-key", ""))
    if token and not pid or owner and not is_owner or member and not pid:
        raise HTTPException(401, "Seat token or host key is missing/invalid")
    return session, pid, is_owner


async def body(request: Request, limit: int = 65536, binary: bool = False):
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        require(len(chunks) <= limit, "Request is too large")
    if binary:
        return bytes(chunks)
    try:
        data = json.loads(chunks)
        require(isinstance(data, dict), "Request must be a JSON object")
        return data
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuleError("Invalid JSON") from exc


@router.get("/api/state")
async def state(request: Request) -> dict:
    session, pid, owner = credentials(request)
    return session.view(pid, owner)


@router.post("/api/join")
async def join(request: Request) -> dict:
    data = await body(request)
    require(
        all(isinstance(data.get(k, ""), str) for k in ("name", "token", "invite")),
        "Invalid name/token/code",
    )
    return await request.app.state.session.join(
        data.get("name", ""), data.get("token", ""), data.get("invite", "")
    )


@router.post("/api/command")
async def command(request: Request) -> dict:
    session, pid, _ = credentials(request, member=True)
    data = await body(request)
    require(isinstance(data.get("command"), dict), "A command object is required")
    require(data.get("revision") is None or type(data["revision"]) is int, "Invalid revision")
    return await session.command(pid, data["command"], data.get("id", ""), data.get("revision"))


@router.post("/api/host")
async def host(request: Request) -> dict:
    session, _, _ = credentials(request, owner=True)
    return await session.host(await body(request, 2 * 1024 * 1024))


@router.get("/api/save")
async def save(request: Request) -> Response:
    session, _, _ = credentials(request, owner=True)
    return Response(
        await session.export(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="catan-save.ctn"'},
    )


@router.post("/api/import")
async def restore(request: Request) -> dict:
    session, _, _ = credentials(request, owner=True)
    await session.restore(await body(request, 2 * 1024 * 1024, binary=True))
    return {"restored": True}


@router.websocket("/ws/game")
async def websocket(ws: WebSocket) -> None:
    from urllib.parse import urlsplit

    origin = ws.headers.get("origin")
    if origin and urlsplit(origin).netloc != ws.headers.get("host"):
        await ws.close(code=1008)
        return
    await ws.accept()
    session = ws.app.state.session
    try:
        hello = await asyncio.wait_for(ws.receive_json(), 8)
        require(isinstance(hello, dict), "Authentication object required")
        token, key = hello.get("token", ""), hello.get("host_key", "")
        require(isinstance(token, str) and isinstance(key, str), "Invalid credentials")
        pid, owner = session.identify(token), session.is_owner(key)
        require(not token or pid is not None, "Seat token is invalid; recover your seat")
        await session.connect(ws, pid, owner)
        while True:
            message = await ws.receive_text()
            if message == "ping":
                await ws.send_json({"type": "pong"})
    except (TimeoutError, WebSocketDisconnect, RuleError, ValueError):
        pass
    finally:
        await session.disconnect(ws)
