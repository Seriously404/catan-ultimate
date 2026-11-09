import asyncio

import pytest

from catan_server import botlogic, engine
from catan_server.check_model import probe
from catan_server.scenarios import decode_save, validate_game
from catan_server.session import Session


@pytest.mark.parametrize("fixture", ["economy", "development", "robber", "late_game"])
def test_workshop_fixtures_are_valid_and_do_not_start_live_game(tmp_path, fixture):
    async def run():
        session = Session(tmp_path)
        try:
            for i in range(6):
                await session.host({"type": "add_bot", "name": f"Bot{i}"})
            await session.host({"type": "test_create", "fixture": fixture})
            sandbox = session.state["sandbox"]
            validate_game(sandbox)
            assert session.state["game"] is None
            assert len(sandbox["board"]["tiles"]) == 30
            if fixture == "development":
                assert len(sandbox["players"][sandbox["active"]]["dev"]) == 4
            if fixture == "late_game":
                assert engine.points(sandbox, sandbox["active"], True) == 9
            if fixture == "robber":
                assert sandbox["rigged_dice"] == [[3, 4]]
            await session.host({"type": "publish_test"})
            assert session.state["game"]["modified"]
        finally:
            await session.close()

    asyncio.run(run())


def test_ending_game_preserves_downloadable_private_archive(tmp_path):
    async def run():
        session = Session(tmp_path)
        try:
            for i in range(3):
                await session.host({"type": "add_bot", "name": f"Bot{i}"})
            await session.host({"type": "start", "quick": True, "seed": "archive"})
            old_game = session.state["game"]
            await session.host({"type": "end_game"})
            assert (tmp_path / "last-game.ctn").is_file()
            restored = decode_save(await session.export())
            assert restored["game"] == old_game
        finally:
            await session.close()

    asyncio.run(run())


def test_model_probe_checks_a_real_rule_command(monkeypatch):
    async def response(*args, **kwargs):
        return {"choice": 0, "message": "Ready", "recipients": []}

    monkeypatch.setattr(botlogic, "ask_model", response)
    result = asyncio.run(probe({"provider": "ollama", "model": "mock"}))
    assert result["ok"] and result["command"]["type"] == "end_turn"


def test_model_cooldown_prevents_a_timeout_on_every_action(tmp_path, monkeypatch):
    calls = []

    async def fail(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("unavailable")

    monkeypatch.setattr(botlogic, "ask_model", fail)

    async def run():
        session = Session(tmp_path)
        try:
            for i in range(3):
                await session.host(
                    {"type": "add_bot", "name": f"Bot{i}", "provider": "ollama", "model": "mock"}
                )
            await session.host({"type": "start", "seed": "cooldown"})
            pid = session.state["game"]["active"]
            for _ in range(2):
                await session._bot_step(
                    pid, engine.legal_actions(session.state["game"], pid), False, ""
                )
            assert len(calls) == 1
            assert session.state["game"]["active"] != pid
        finally:
            await session.close()

    asyncio.run(run())
