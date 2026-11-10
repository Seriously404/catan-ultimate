import asyncio

import pytest

from catan_server import engine
from catan_server.session import Session


def test_presentation_gate_delays_moves_but_not_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("CATAN_ACTION_DELAY", "1")

    async def run():
        session = Session(tmp_path)
        now = [100.0]
        session.clock = lambda: now[0]
        try:
            for name in ("One", "Two", "Three"):
                await session.host({"type": "add_bot", "name": name})
            await session.host({"type": "start", "quick": True, "seed": "paced"})
            g = session.state["game"]
            pid = g["active"]
            g["rigged_dice"] = [[1, 1]]
            await session.command(pid, {"type": "roll"}, "roll")
            event = session.state["events"][-1]
            assert event["duration_ms"] == 2400 and event["dice"] == [1, 1]
            assert event["starts_at"] == 100 and session.state["presentation_until"] == 102.4
            with pytest.raises(engine.RuleError, match="still being presented"):
                await session.command(pid, {"type": "end_turn"}, "early")
            await session.command(pid, {"type": "chat", "text": "Nice roll", "to": []}, "chat")
            assert not any(a["type"] == "end_turn" for a in session.bot_actions(pid))
            now[0] = 102.4
            await session.command(pid, {"type": "end_turn"}, "next")
            assert session.state["game"]["active"] != pid
        finally:
            await session.close()

    asyncio.run(run())


def test_steal_animation_metadata_preserves_hand_privacy(tmp_path):
    async def run():
        session = Session(tmp_path)
        try:
            for name in ("One", "Two", "Three"):
                await session.host({"type": "add_bot", "name": name})
            await session.host({"type": "start", "quick": True, "seed": "private"})
            g = session.state["game"]
            thief, victim, observer = g["order"]
            engine.give(g, victim, {"wood": 1})
            g.update(active=thief, phase="steal", victims=[victim])
            await session.command(thief, {"type": "steal", "victim": victim}, "steal")
            own = session.view(thief)["events"][-1]
            other = session.view(observer)["events"][-1]
            assert own["resource"] in engine.RESOURCES
            assert "resource" not in other
            assert all("resources" not in effect for effect in other["effects"])
            assert all("resources" in effect for effect in own["effects"])
            assert sorted(effect["count"] for effect in other["effects"]) == [-1, 1]
            assert "resource" not in session.view(observer)["game"]["last_event"]
        finally:
            await session.close()

    asyncio.run(run())
