import asyncio
import json
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from catan_server import botlogic, engine
from catan_server.app import create_app
from catan_server.session import Session


@pytest.fixture
def table(tmp_path, monkeypatch):
    async def no_scheduler(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(Session, "_bots", no_scheduler)
    app = create_app(frontend_dir=tmp_path / "dist", data_dir=tmp_path / "data")
    with TestClient(app) as client:
        owner = {"X-Host-Key": app.state.session.store.host_key}
        seats = [
            client.post("/api/join", json={"name": name}).json()
            for name in ("Alice", "Bob", "Charlie")
        ]
        headers = [{"Authorization": f"Bearer {seat['token']}"} for seat in seats]
        with ExitStack() as stack:
            for seat in seats:
                ws = stack.enter_context(client.websocket_connect("/ws/game"))
                ws.send_json({"token": seat["token"]})
                ws.receive_json()
            for auth in headers:
                assert (
                    client.post(
                        "/api/command",
                        headers=auth,
                        json={"id": "ready", "command": {"type": "ready", "ready": True}},
                    ).status_code
                    == 200
                )
            yield client, owner, seats, headers


def start(client, owner, **values):
    response = client.post(
        "/api/host",
        headers=owner,
        json={"type": "start", "quick": True, "seed": "session", **values},
    )
    assert response.status_code == 200, response.text


def test_member_cannot_start_or_read_owner_state(table):
    client, owner, seats, headers = table
    assert client.post("/api/host", headers=headers[0], json={"type": "start"}).status_code == 401
    start(client, owner)
    own = client.get("/api/state", headers=headers[0]).json()
    assert own["me"] == seats[0]["player"] and not own["owner"]
    assert "raw_game" not in own and "sandbox" not in own
    assert "resources" in own["game"]["players"][seats[0]["player"]]
    assert "resources" not in own["game"]["players"][seats[1]["player"]]
    assert client.get("/api/save", headers=headers[0]).status_code == 401


def test_private_chat_recipient_filter_and_command_deduplication(table):
    client, _, seats, headers = table
    command = {
        "id": "one-private-message",
        "command": {"type": "chat", "to": [seats[1]["player"]], "text": "Our private plan"},
    }
    first = client.post("/api/command", headers=headers[0], json=command).json()
    second = client.post("/api/command", headers=headers[0], json=command).json()
    assert second["duplicate"] and first["revision"] == second["revision"]
    assert len(client.get("/api/state", headers=headers[1]).json()["chat"]) == 1
    assert client.get("/api/state", headers=headers[2]).json()["chat"] == []
    assert client.get("/api/state").json()["chat"] == []
    command["command"]["text"] = "Reused ID"
    assert client.post("/api/command", headers=headers[0], json=command).status_code == 400


def test_revision_rejection_does_not_mutate_game(table):
    client, owner, seats, headers = table
    start(client, owner)
    before = client.get("/api/state", headers=owner).json()
    actor = next(i for i, seat in enumerate(seats) if seat["player"] == before["game"]["active"])
    response = client.post(
        "/api/command",
        headers=headers[actor],
        json={"id": "stale-roll", "revision": 0, "command": {"type": "roll"}},
    )
    assert response.status_code == 400
    after = client.get("/api/state", headers=owner).json()
    assert before["revision"] == after["revision"] and before["raw_game"] == after["raw_game"]


def test_sandbox_isolation_validation_and_save_import(table):
    client, owner, seats, _ = table
    start(client, owner)
    client.post("/api/host", headers=owner, json={"type": "test_create"})
    before = client.get("/api/state", headers=owner).json()
    result = client.post(
        "/api/host",
        headers=owner,
        json={
            "type": "test_edit",
            "edit": "resources",
            "player": seats[0]["player"],
            "resources": {"ore": 8},
        },
    )
    assert result.status_code == 200, result.text
    after = client.get("/api/state", headers=owner).json()
    assert before["raw_game"] == after["raw_game"]
    assert after["sandbox"]["players"][seats[0]["player"]]["resources"]["ore"] == 8
    response = client.post(
        "/api/host",
        headers=owner,
        json={
            "type": "edit",
            "edit": "resources",
            "player": seats[0]["player"],
            "resources": {"ore": 8},
        },
    )
    assert response.status_code == 400  # Must pause live editing.
    saved = client.get("/api/save", headers=owner).content
    assert saved[:4] == b"CTN1"
    client.post("/api/host", headers=owner, json={"type": "end_game"})
    response = client.post("/api/import", headers=owner, content=saved)
    assert response.status_code == 200, response.text
    restored = client.get("/api/state", headers=owner).json()
    for field in ("order", "board", "players", "bank", "deck", "active"):
        assert restored["raw_game"][field] == after["raw_game"][field]
    assert restored["paused"]


def test_cross_origin_and_malformed_commands_are_rejected(table):
    client, owner, _, headers = table
    response = client.post(
        "/api/host",
        headers={**owner, "Origin": "https://unrelated.example"},
        json={"type": "end_game"},
    )
    assert response.status_code == 403
    for payload in ([1, 2], {"command": []}, {"command": {"type": "chat", "text": 9}, "id": "bad"}):
        assert client.post("/api/command", headers=headers[0], json=payload).status_code == 400
    assert (
        client.post(
            "/api/host",
            headers=owner,
            json={"type": "add_bot", "name": "Malformed", "timeout": "not a number"},
        ).status_code
        == 400
    )


def test_disconnect_autosaves_and_reconnect_reclaims_same_seat(tmp_path):
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        owner = {"X-Host-Key": app.state.session.store.host_key}
        human = client.post("/api/join", json={"name": "Human"}).json()
        auth = {"Authorization": f"Bearer {human['token']}"}
        for name in ("Bot1", "Bot2"):
            client.post("/api/host", headers=owner, json={"type": "add_bot", "name": name})
        with client.websocket_connect("/ws/game") as ws:
            ws.send_json({"token": human["token"]})
            ws.receive_json()
            client.post(
                "/api/command",
                headers=auth,
                json={"id": "ready", "command": {"type": "ready", "ready": True}},
            )
            start(client, owner)
        assert client.get("/api/state", headers=auth).json()["paused"]
        with client.websocket_connect("/ws/game") as ws:
            ws.send_json({"token": human["token"]})
            state = ws.receive_json()["state"]
            assert not state["paused"] and state["me"] == human["player"]
    # A new process recovers the durable snapshot and pauses until everyone reconnects.
    with TestClient(create_app(data_dir=tmp_path)) as client:
        state = client.get("/api/state", headers=auth).json()
        assert state["game"] and state["paused"] and state["me"] == human["player"]


@pytest.mark.parametrize("provider", ["ollama", "openai"])
def test_local_model_adapter_uses_private_bounded_view_and_json(monkeypatch, provider):
    calls = []

    def fake_post(url, body, timeout, api_key):
        calls.append((url, body, timeout))
        content = json.dumps({"choice": 0, "message": "Hello", "recipients": []})
        if provider == "openai":
            return {"choices": [{"message": {"content": content}}]}
        return {
            "message": {"content": json.dumps({"choice": 0, "message": "Hello", "recipients": []})}
        }

    monkeypatch.setattr(botlogic, "_post", fake_post)
    g = engine.quick_setup(engine.new_game(["a", "b", "c"], "model"))
    view = {"game": engine.player_view(g, "a"), "chat": [{"text": "x" * 1200}] * 400}
    result = asyncio.run(
        botlogic.ask_model(
            {
                "provider": provider,
                "model": "test-model",
                "endpoint": "http://127.0.0.1:11434",
                "timeout": 2,
            },
            view,
            [{"type": "roll"}],
            [],
        )
    )
    assert result["choice"] == 0
    url, body, timeout = calls[0]
    if provider == "ollama":
        assert url.endswith("/api/chat") and body["keep_alive"] == "30m" and not body["think"]
        assert body["options"]["num_predict"] <= 160
    else:
        assert url.endswith("/chat/completions") and body["max_tokens"] <= 160
        assert body["response_format"] == {"type": "json_object"}
    assert timeout == 2
    sent = json.loads(body["messages"][-1]["content"])["observation"]
    assert len(sent["chat"]) == 8 and len(sent["chat"][0]["text"]) == 240
    assert "resources" not in sent["game"]["players"]["b"]


def test_model_failure_uses_valid_fallback_and_saves_context(tmp_path, monkeypatch):
    async def fail(*args, **kwargs):
        raise TimeoutError("model offline")

    monkeypatch.setattr(botlogic, "ask_model", fail)

    async def run():
        session = Session(tmp_path)
        try:
            for name in ("One", "Two", "Three"):
                await session.host(
                    {"type": "add_bot", "name": name, "provider": "ollama", "model": "test"}
                )
            await session.host({"type": "start", "seed": "fallback"})
            before = session.state["revision"]
            pid = session.state["game"]["active"]
            await session._bot_step(
                pid, engine.legal_actions(session.state["game"], pid), False, ""
            )
            assert session.state["game"]["phase"] == "setup_road"
            assert session.state["revision"] > before and pid in session.bot_errors
            assert next(p for p in session.state["players"] if p["id"] == pid)["memory"]
        finally:
            await session.close()

    asyncio.run(run())
