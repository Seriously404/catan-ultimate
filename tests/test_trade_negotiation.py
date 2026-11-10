import asyncio
import copy

import pytest

from catan_server import engine as e
from catan_server.scenarios import validate_game
from catan_server.session import Session


def table():
    g = e.quick_setup(e.new_game(["a", "b", "c", "d"], "negotiation"))
    g["phase"] = "action"
    for p in g["players"].values():
        for r, n in p["resources"].items():
            g["bank"][r] += n
        p["resources"] = e.empty_hand()
    a, b, c, d = g["order"]
    e.give(g, a, {"wood": 3})
    for pid in (b, c, d):
        e.give(g, pid, {"ore": 2})
    return g, a, b, c, d


def offer(g, actor, targets=None, now=100):
    return e.apply(
        g,
        actor,
        {"type": "offer_trade", "to": targets, "give": {"wood": 1}, "want": {"ore": 1}},
        now=now,
    )


def respond(g, pid, kind="accept_trade", now=101):
    return e.apply(g, pid, {"type": kind, "offer": g["offers"][0]["id"]}, now=now)


def settle(g, proposer, now=105):
    return e.apply(g, proposer, {"type": "settle_trade", "offer": g["offers"][0]["id"]}, now=now)


def test_acceptance_waits_five_seconds_then_single_acceptance_settles():
    g, a, b, _, _ = table()
    g = respond(offer(g, a), b)
    assert g["players"][a]["resources"]["ore"] == 0
    g = settle(g, a, 104.999)
    assert g["offers"]
    with pytest.raises(e.RuleError, match="outstanding trades"):
        e.apply(g, a, {"type": "end_turn"}, now=104)
    with pytest.raises(e.RuleError, match="full five-second"):
        respond(g, a, "cancel_trade", 104)
    g = settle(g, a)
    assert not g["offers"]
    assert g["players"][a]["resources"]["ore"] == 1
    assert g["players"][b]["resources"]["wood"] == 1
    assert g["last_event"]["type"] == "trade_completed"


def test_multiple_acceptances_require_proposer_choice_and_exclude_late_arrivals():
    g, a, b, c, d = table()
    g = respond(respond(offer(g, a), b), c, now=102)
    g = settle(g, a)
    assert g["offers"][0]["selecting"]
    assert {x["player"] for x in e.legal_actions(g, a, now=105) if x["type"] == "select_trade"} == {
        b,
        c,
    }
    with pytest.raises(e.RuleError, match="proposer is choosing"):
        respond(g, d, now=106)
    with pytest.raises(e.RuleError, match="Only the proposer"):
        e.apply(g, b, {"type": "select_trade", "offer": 1, "player": c}, now=106)
    g = e.apply(g, a, {"type": "select_trade", "offer": 1, "player": c}, now=106)
    assert g["players"][b]["resources"]["wood"] == 0
    assert g["players"][c]["resources"]["wood"] == 1
    assert not g["offers"]


def test_no_acceptances_keeps_offer_open_until_first_later_acceptance():
    g, a, b, c, _ = table()
    g = settle(offer(g, a), a)
    assert g["offers"] and g["offers"][0]["window_closed"]
    g = respond(g, b, "reject_trade", now=106)
    assert g["offers"]
    g = respond(g, c, now=200)
    assert not g["offers"] and g["players"][c]["resources"]["wood"] == 1


def test_all_rejections_close_early_and_cannot_be_reversed():
    g, a, b, c, _ = table()
    g = respond(offer(g, a, [b, c]), b, "reject_trade")
    with pytest.raises(e.RuleError, match="already responded"):
        respond(g, b)
    g = respond(g, c, "reject_trade", 102)
    assert not g["offers"] and g["last_event"]["type"] == "trade_failed"


def test_subset_offer_privacy_and_unaddressed_player_cannot_answer():
    g, a, b, c, d = table()
    g = offer(g, a, [b, c])
    assert e.player_view(g, d)["offers"] == []
    assert e.player_view(g, b)["offers"][0]["to"] == sorted([b, c])
    assert e.visible_event(g["last_event"], d) is None
    with pytest.raises(e.RuleError, match="not addressed"):
        respond(g, d)
    assert validate_game(g)["offers"] == g["offers"]


def test_resource_changes_invalidate_acceptance_without_creating_cards():
    g, a, b, c, _ = table()
    g = respond(respond(offer(g, a), b), c, now=102)
    e.pay(g, b, {"ore": 2})
    before = copy.deepcopy(g)
    g = settle(g, a)
    assert not g["offers"] and g["players"][c]["resources"]["wood"] == 1
    for r in e.RESOURCES:
        assert sum(p["resources"][r] for p in g["players"].values()) == sum(
            p["resources"][r] for p in before["players"].values()
        )


def test_only_active_player_can_be_partner_in_an_out_of_turn_offer():
    g, a, b, c, _ = table()
    e.give(g, b, {"wood": 1})
    with pytest.raises(e.RuleError, match="involve the active"):
        offer(g, b, [c])
    assert offer(g, b, [a])["offers"][0]["responses"] == {a: "pending"}


def test_saved_trade_responses_and_selection_survive_validation():
    g, a, b, c, _ = table()
    g = settle(respond(respond(offer(g, a), b), c, now=102), a)
    restored = validate_game(g)
    assert restored["offers"] == g["offers"]
    bad = copy.deepcopy(g)
    bad["offers"][0]["responses"]["intruder"] = "accepted"
    with pytest.raises(e.RuleError, match="trade responses"):
        validate_game(bad)


def test_server_clock_settlement_and_racing_late_acceptances_are_atomic(tmp_path):
    async def run():
        session = Session(tmp_path)
        now = [100.0]
        session.clock = lambda: now[0]
        try:
            for name in ("One", "Two", "Three"):
                await session.host({"type": "add_bot", "name": name})
            await session.host({"type": "start", "quick": True, "seed": "race"})
            game = session.state["game"]
            game["phase"] = "action"
            a, b, c = game["order"]
            e.give(game, a, {"wood": 2})
            e.give(game, b, {"ore": 1})
            e.give(game, c, {"ore": 1})
            await session.command(
                a, {"type": "offer_trade", "give": {"wood": 1}, "want": {"ore": 1}}, "offer"
            )
            now[0] += 5
            await session.settle_trades()
            results = await asyncio.gather(
                session.command(b, {"type": "accept_trade", "offer": 1}, "accept-b"),
                session.command(c, {"type": "accept_trade", "offer": 1}, "accept-c"),
                return_exceptions=True,
            )
            assert sum(isinstance(r, e.RuleError) for r in results) == 1
            assert not session.state["game"]["offers"]
            assert sum(x["type"] == "trade_completed" for x in session.state["events"]) == 1
        finally:
            await session.close()

    asyncio.run(run())


def test_pause_freezes_window_and_resume_restores_remaining_time(tmp_path):
    async def run():
        session = Session(tmp_path)
        now = [100.0]
        session.clock = lambda: now[0]
        try:
            for name in ("One", "Two", "Three"):
                await session.host({"type": "add_bot", "name": name})
            await session.host({"type": "start", "quick": True, "seed": "paused"})
            game = session.state["game"]
            game["phase"] = "action"
            a, b = game["order"][:2]
            e.give(game, a, {"wood": 1})
            e.give(game, b, {"ore": 1})
            await session.command(
                a,
                {"type": "offer_trade", "to": [b], "give": {"wood": 1}, "want": {"ore": 1}},
                "offer",
            )
            await session.command(b, {"type": "accept_trade", "offer": 1}, "accept")
            now[0] = 102
            await session.host({"type": "pause"})
            now[0] = 200
            await session.settle_trades()
            assert session.state["game"]["offers"]
            await session.host({"type": "resume"})
            assert session.state["game"]["offers"][0]["closes_at"] == 203
            await session.settle_trades()
            assert session.state["game"]["offers"]
            now[0] = 203
            await session.settle_trades()
            assert not session.state["game"]["offers"]
        finally:
            await session.close()

    asyncio.run(run())


def test_save_import_keeps_negotiation_strategy_and_private_event_audience(tmp_path):
    async def run():
        session = Session(tmp_path / "original")
        restored = Session(tmp_path / "restored")
        try:
            for name in ("One", "Two", "Three"):
                await session.host({"type": "add_bot", "name": name})
            await session.host({"type": "start", "quick": True, "seed": "save-trade"})
            game = session.state["game"]
            game["phase"] = "action"
            a, b, outsider = game["order"]
            e.give(game, a, {"wood": 1})
            e.give(game, b, {"ore": 1})
            next(p for p in session.state["players"] if p["id"] == a)["strategy"] = (
                "Trust my ally until they lead."
            )
            await session.command(
                a,
                {"type": "offer_trade", "to": [b], "give": {"wood": 1}, "want": {"ore": 1}},
                "offer",
            )
            await session.command(b, {"type": "accept_trade", "offer": 1}, "accept")
            await restored.restore(await session.export())
            assert restored.view(a)["private_strategy"] == "Trust my ally until they lead."
            assert restored.view(outsider)["private_strategy"] == ""
            assert restored.view(outsider)["events"] == []
            assert restored.view(outsider)["game"]["offers"] == []
            assert restored.view(a)["game"]["offers"][0]["responses"][b] == "accepted"
            assert restored.state["events"][-1]["effects"] == []
            assert restored.state["events"][-1]["audience"] == [a, b]
        finally:
            await session.close()
            await restored.close()

    asyncio.run(run())


def test_sandbox_trades_settle_on_timer_while_live_game_is_paused(tmp_path):
    async def run():
        session = Session(tmp_path)
        try:
            game, a, b, _, _ = table()
            session.state["sandbox"] = respond(offer(game, a), b)
            session.state["paused"] = True
            session.clock = lambda: 105
            await session.settle_trades()
            assert session.state["sandbox"]["offers"] == []
            assert session.state["sandbox"]["players"][b]["resources"]["wood"] == 1
            assert session.state["game"] is None
        finally:
            await session.close()

    asyncio.run(run())
