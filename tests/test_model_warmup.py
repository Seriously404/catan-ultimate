import asyncio
import json

import pytest

from catan_server import botlogic, engine
from catan_server.session import Session


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_warmup_loads_the_decision_prefix_without_a_deadline(monkeypatch, provider):
    calls = []

    async def fake_post(url, body, timeout, api_key):
        calls.append((body, timeout))
        if timeout is None:
            await asyncio.sleep(0.03)  # Longer than the following decision's budget.
            content = '{"status":"active"}'
        else:
            content = '{"choice":0,"message":"","recipients":[]}'
        if provider == "ollama":
            return {"message": {"content": content}}
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(botlogic, "_post", fake_post)
    config = {
        "provider": provider,
        "model": "test",
        "endpoint": "http://localhost",
        "timeout": 0.01,
    }
    game = engine.quick_setup(engine.new_game(["a", "b", "c"], "warmup"))
    observation = {"game": engine.player_view(game, "a"), "me": "a"}

    async def run():
        ready = await botlogic.ask_model(config, observation, [], [], warmup=True)
        assert ready == {"status": "active"}
        await botlogic.ask_model(config, observation, [], [])

    asyncio.run(run())
    warm, decision = calls
    assert warm[1] is None and decision[1] == 0.01
    assert warm[0]["messages"][:-1] == decision[0]["messages"]
    assert json.loads(warm[0]["messages"][1]["content"])["observation"]["me"] == "a"
    if provider == "openai":
        assert warm[0]["reasoning_effort"] == decision[0]["reasoning_effort"] == "none"
        assert warm[0]["max_tokens"] == 16
    else:
        assert not warm[0]["think"] and not decision[0]["think"]
        assert warm[0]["options"]["num_predict"] == 16


def test_warmup_rejects_a_move_instead_of_acknowledgement(monkeypatch):
    async def fake_post(*args):
        return {"choices": [{"message": {"content": '{"choice":0}'}}]}

    monkeypatch.setattr(botlogic, "_post", fake_post)
    with pytest.raises(ValueError, match="warm-up must acknowledge"):
        asyncio.run(
            botlogic.ask_model(
                {"provider": "openai", "model": "test", "endpoint": "http://localhost"},
                {},
                [],
                [],
                warmup=True,
            )
        )


def test_static_prompt_prefix_survives_status_and_view_changes(monkeypatch):
    requests = []

    async def fake_post(url, body, *args):
        requests.append(body)
        return {"choices": [{"message": {"content": '{"choice":-1}'}}]}

    monkeypatch.setattr(botlogic, "_post", fake_post)

    async def run():
        game = engine.quick_setup(engine.new_game(["a", "b", "c"], "cache"))
        config = {"provider": "openai", "model": "test", "endpoint": "http://localhost/v1"}
        for pid, observe in (("a", False), ("b", True)):
            observation = {
                "me": pid,
                "game": engine.player_view(game, pid),
                "server_time": 123,
                "players": [{"id": pid, "thinking": True, "warming": observe}],
            }
            await botlogic.ask_model(config, observation, [], [], observe)

    asyncio.run(run())
    assert requests[0]["messages"][0] == requests[1]["messages"][0]
    first, second = [body["messages"][1]["content"] for body in requests]
    assert first.split('"observation":', 1)[0] == second.split('"observation":', 1)[0]
    payload = json.loads(first)
    assert payload["board_layout"]["tiles"]
    assert "server_time" not in payload["observation"]
    assert "warming" not in payload["observation"]["players"][0]


def test_session_warms_once_before_starting_the_decision_budget(tmp_path, monkeypatch):
    calls = []

    async def respond(*args, warmup=False):
        calls.append("warm" if warmup else "decision")
        if warmup:
            await asyncio.sleep(1.6)  # Exceeds the old outer 1s + 0.5s timeout.
            return {"status": "active"}
        return {"choice": 0}

    monkeypatch.setattr(botlogic, "ask_model", respond)

    async def run():
        session = Session(tmp_path)
        try:
            for name in ("One", "Two", "Three"):
                await session.host(
                    {
                        "type": "add_bot",
                        "name": name,
                        "provider": "openai",
                        "model": "test",
                        "timeout": 1,
                    }
                )
            await session.host({"type": "start", "seed": "warm"})
            session.state["game"]["phase"] = "setup_settlement"
            pid = session.state["game"]["active"]
            for _ in range(2):
                await session._bot_step(
                    pid, engine.legal_actions(session.state["game"], pid), False, ""
                )
            assert calls == ["warm", "decision", "decision"]
            assert pid not in session.bot_errors
        finally:
            await session.close()

    asyncio.run(run())


def test_untimed_http_request_can_be_cancelled_and_closes_connection():
    async def run():
        connected, disconnected = asyncio.Event(), asyncio.Event()

        async def stalled_model(reader, writer):
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                await reader.readexactly(length)
                connected.set()
                assert await reader.read() == b""
                disconnected.set()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(stalled_model, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            task = asyncio.create_task(botlogic._post(f"http://127.0.0.1:{port}", {}, None))
            try:
                await asyncio.wait_for(connected.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(disconnected.wait(), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("host_action", ["configure_bot", "end_game"])
def test_host_can_cancel_a_bot_waiting_for_initialization(tmp_path, monkeypatch, host_action):
    async def run():
        entered = asyncio.Event()

        async def stalled(*args, **kwargs):
            assert kwargs.get("warmup")
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(botlogic, "ask_model", stalled)
        session = Session(tmp_path)
        try:
            for name in ("One", "Two", "Three"):
                await session.host(
                    {"type": "add_bot", "name": name, "provider": "openai", "model": "test"}
                )
            await session.host({"type": "start", "seed": "cancel"})
            pid = session.state["game"]["active"]
            task = asyncio.create_task(
                session._bot_step(pid, engine.legal_actions(session.state["game"], pid), False, "")
            )
            session.bot_tasks[pid] = task
            await asyncio.wait_for(entered.wait(), 2)
            assert session.view(None)["players"][
                next(i for i, p in enumerate(session.state["players"]) if p["id"] == pid)
            ]["warming"]
            await session.host(
                {"type": host_action, "player": pid, "provider": "openai", "model": "replacement"}
            )
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert pid not in session.warming
            assert pid not in session.thinking
            assert pid not in session.bot_warmed
            assert pid not in session.bot_errors
        finally:
            await session.close()

    asyncio.run(run())
