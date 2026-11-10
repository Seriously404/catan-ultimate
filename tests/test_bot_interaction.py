import asyncio
import copy

import pytest

from catan_server import botlogic, engine
from catan_server.session import Session


async def setup_session(path, provider="openai"):
    session = Session(path)
    for name in ("Ada", "Bob", "Charlie", "Dora"):
        await session.host({"type": "add_bot", "name": name, "provider": provider, "model": "mock"})
    await session.host({"type": "start", "quick": True, "seed": "conversation"})
    session.state["game"]["phase"] = "action"
    return session


def test_llm_can_initiate_public_private_and_group_chat_and_remember_alliance(
    tmp_path, monkeypatch
):
    observations = []

    async def run():
        session = await setup_session(tmp_path)
        try:
            a, b, c, d = session.state["game"]["order"]

            async def respond(
                config, observation, actions, memory, chat_only=False, *, warmup=False
            ):
                if warmup:
                    return {"status": "active"}
                observations.append(copy.deepcopy(observation))
                return {
                    "choice": -1,
                    "memory": "I promised Bob a wood trade; cooperate until he leads.",
                    "messages": [
                        {"text": "Anyone need wood?", "to": []},
                        {"text": "Let's work together.", "to": [b]},
                        {"text": "Our three-way plan.", "to": [b, c]},
                    ],
                }

            monkeypatch.setattr(botlogic, "ask_model", respond)
            await session._bot_step(a, [], True, "")
            assert len(session.view(a)["chat"]) == 3
            assert len(session.view(b)["chat"]) == 3
            assert len(session.view(c)["chat"]) == 2
            assert len(session.view(d)["chat"]) == 1
            assert session.view(d)["private_strategy"] == ""
            assert "promised Bob" in session.view(a)["private_strategy"]
            await session._bot_step(a, [], True, "")
            assert "promised Bob" in observations[-1]["private_strategy"]
            assert "resources" not in observations[-1]["game"]["players"][b]
            assert all(m["from"] == a for m in session.state["chat"])
        finally:
            await session.close()

    asyncio.run(run())


def test_bad_private_recipient_does_not_leak_as_public_chat(tmp_path, monkeypatch):
    async def respond(*args, warmup=False):
        return (
            {"status": "active"}
            if warmup
            else {"choice": -1, "messages": [{"text": "Private plan", "to": ["missing-player"]}]}
        )

    monkeypatch.setattr(botlogic, "ask_model", respond)

    async def run():
        session = await setup_session(tmp_path)
        try:
            await session._bot_step(session.state["game"]["active"], [], True, "")
            assert session.state["chat"] == []
        finally:
            await session.close()

    asyncio.run(run())


def test_llm_can_reject_an_affordable_offer_and_choose_between_accepting_partners(
    tmp_path, monkeypatch
):
    async def run():
        session = await setup_session(tmp_path)
        now = [100.0]
        session.clock = lambda: now[0]
        try:
            game = session.state["game"]
            a, b, c, _ = game["order"]
            engine.give(game, a, {"wood": 2})
            engine.give(game, b, {"ore": 2})
            engine.give(game, c, {"ore": 2})

            async def respond(
                config, observation, actions, memory, chat_only=False, *, warmup=False
            ):
                if warmup:
                    return {"status": "active"}
                if observation["me"] == b:
                    return {"choice": -1, "command": {"type": "reject_trade", "offer": 1}}
                return {"choice": -1, "command": {"type": "select_trade", "offer": 2, "player": c}}

            monkeypatch.setattr(botlogic, "ask_model", respond)
            await session.command(
                a,
                {"type": "offer_trade", "to": [b], "give": {"wood": 1}, "want": {"ore": 1}},
                "first",
            )
            await session._bot_step(b, session.bot_actions(b), False, "")
            assert not session.state["game"]["offers"]
            await session.command(
                a,
                {"type": "offer_trade", "to": [b, c], "give": {"wood": 1}, "want": {"ore": 1}},
                "second",
            )
            await session.command(b, {"type": "accept_trade", "offer": 2}, "b")
            await session.command(c, {"type": "accept_trade", "offer": 2}, "c")
            now[0] += 5
            await session.settle_trades()
            await session._bot_step(a, session.bot_actions(a), False, "")
            assert not session.state["game"]["offers"]
            assert session.state["events"][-1]["partner"] == c
        finally:
            await session.close()

    asyncio.run(run())


def test_llm_can_propose_subset_trade_and_other_llm_evaluates_it(tmp_path, monkeypatch):
    async def run():
        session = await setup_session(tmp_path)
        try:
            game = session.state["game"]
            a, b, c, d = game["order"]
            engine.give(game, a, {"wood": 2})
            engine.give(game, b, {"ore": 2})

            async def respond(
                config, observation, actions, memory, chat_only=False, *, warmup=False
            ):
                if warmup:
                    return {"status": "active"}
                if observation["me"] == a:
                    return {
                        "choice": -1,
                        "command": {
                            "type": "offer_trade",
                            "to": [b, c],
                            "give": {"wood": 1},
                            "want": {"ore": 1},
                        },
                    }
                options = [
                    i for i, action in enumerate(actions) if action["type"] == "accept_trade"
                ]
                assert options
                return {"choice": options[0], "messages": [{"text": "Deal, ally.", "to": [a]}]}

            monkeypatch.setattr(botlogic, "ask_model", respond)
            await session._bot_step(a, session.bot_actions(a), False, "")
            assert session.state["game"]["offers"][0]["to"] == sorted([b, c])
            assert session.view(d)["game"]["offers"] == []
            await session._bot_step(b, session.bot_actions(b), False, "")
            assert session.state["game"]["offers"][0]["responses"][b] == "accepted"
            assert session.view(a)["chat"][-1]["text"] == "Deal, ally."
            assert session.view(d)["chat"] == []
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.parametrize("accept", [True, False])
def test_dummy_randomly_accepts_or_rejects_affordable_offer(tmp_path, monkeypatch, accept):
    monkeypatch.setattr(botlogic.secrets, "choice", lambda options: options[1 if accept else 0])

    async def run():
        session = await setup_session(tmp_path, "builtin")
        try:
            game = session.state["game"]
            a, b = game["order"][:2]
            engine.give(game, a, {"wood": 1})
            engine.give(game, b, {"ore": 1})
            await session.command(
                a,
                {"type": "offer_trade", "to": [b], "give": {"wood": 1}, "want": {"ore": 1}},
                "offer",
            )
            await session._bot_step(b, session.bot_actions(b), False, "")
            offers = session.state["game"]["offers"]
            assert offers[0]["responses"][b] == "accepted" if accept else not offers
        finally:
            await session.close()

    asyncio.run(run())


def test_unaffordable_offer_is_rejected_without_model_inference(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Unaffordable offer should not require a model call")

    monkeypatch.setattr(botlogic, "ask_model", forbidden)

    async def run():
        session = await setup_session(tmp_path)
        try:
            game = session.state["game"]
            a, b = game["order"][:2]
            engine.give(game, a, {"wood": 1})
            if game["players"][b]["resources"]["ore"]:
                engine.pay(game, b, {"ore": game["players"][b]["resources"]["ore"]})
            await session.command(
                a,
                {"type": "offer_trade", "to": [b], "give": {"wood": 1}, "want": {"ore": 1}},
                "offer",
            )
            await session._bot_step(b, session.bot_actions(b), False, "")
            assert not session.state["game"]["offers"]
        finally:
            await session.close()

    asyncio.run(run())


def test_scheduler_wakes_model_after_visible_move_without_mention(tmp_path, monkeypatch):
    async def run():
        session = await setup_session(tmp_path)
        seen = asyncio.Event()
        observed = []
        a, b = session.state["game"]["order"][:2]

        async def respond(config, observation, actions, memory, chat_only=False, *, warmup=False):
            if warmup:
                return {"status": "active"}
            if observation["me"] == b:
                observed.append(observation)
                seen.set()
                return {
                    "choice": -1,
                    "messages": [{"text": "Let's cooperate on your next turn.", "to": [a]}],
                }
            return {"choice": -1}

        monkeypatch.setattr(botlogic, "ask_model", respond)
        try:
            session.state["events"].append(
                {"id": 1, "type": "build_road", "actor": a, "text": "built a road"}
            )
            session.scheduler = asyncio.create_task(session._bots())
            await asyncio.wait_for(seen.wait(), 2)
            assert not observed[0]["chat"]
            assert observed[0]["events"][-1]["actor"] == a
        finally:
            await session.close()

    asyncio.run(run())


def test_model_requests_can_overlap_with_configured_concurrency(tmp_path, monkeypatch):
    monkeypatch.setenv("CATAN_LLM_CONCURRENCY", "2")

    async def run():
        session = await setup_session(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        active, peak = 0, 0

        async def respond(*args, warmup=False):
            nonlocal active, peak
            if warmup:
                return {"status": "active"}
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            try:
                await release.wait()
                return {"choice": -1}
            finally:
                active -= 1

        monkeypatch.setattr(botlogic, "ask_model", respond)
        tasks = []
        try:
            for pid in session.state["game"]["order"][:2]:
                tasks.append(asyncio.create_task(session._bot_step(pid, [], True, "")))
            await asyncio.wait_for(entered.wait(), 2)
            assert peak == 2
            release.set()
            await asyncio.gather(*tasks)
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await session.close()

    asyncio.run(run())


def test_bot_to_bot_conversation_has_a_finite_budget_without_new_moves(tmp_path, monkeypatch):
    async def run():
        session = await setup_session(tmp_path)
        now = [1000.0]
        session.clock = lambda: now[0]
        monkeypatch.setattr(session, "bot_actions", lambda pid: [])
        decisions = []

        async def respond(config, observation, actions, memory, chat_only=False, *, warmup=False):
            if warmup:
                return {"status": "active"}
            decisions.append(observation["me"])
            return {"choice": -1, "messages": [{"text": "Let's negotiate.", "to": []}]}

        monkeypatch.setattr(botlogic, "ask_model", respond)
        try:
            session.scheduler = asyncio.create_task(session._bots())
            for _ in range(5):
                await asyncio.sleep(0.3)
                now[0] += 11
            assert 4 <= len(decisions) <= 8
            before = len(decisions)
            await asyncio.sleep(0.3)
            assert len(decisions) == before
        finally:
            await session.close()

    asyncio.run(run())
