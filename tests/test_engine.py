"""Conformance scenarios and complete simulated games, including the larger island."""

import copy
import json
import random
from collections import Counter

import pytest

from catan_server import engine as e
from catan_server.board import RESOURCES
from catan_server.botlogic import choose_builtin
from catan_server.scenarios import decode_save, encode_save, validate_game


def game(n=3, setup=True):
    g = e.new_game([f"p{i}" for i in range(n)], "test-island", deck_rng=random.Random("deck"))
    return e.quick_setup(g) if setup else g


def action_game(n=3):
    g = game(n)
    g["phase"] = "action"
    return g


def clear_hands(g):
    for p in g["players"]:
        e.pay(g, p, g["players"][p]["resources"].copy())


def test_public_board_seed_does_not_control_private_deck(monkeypatch):
    shuffled = []

    class PrivateRandom:
        def shuffle(self, deck):
            shuffled.append(True)
            deck.reverse()

    monkeypatch.setattr(e.random, "SystemRandom", PrivateRandom)
    g = e.new_game(["a", "b", "c"], "public-seed")
    assert shuffled == [True] and len(g["deck"]) == 25
    assert "deck" not in e.player_view(g, "a")


def card(g, pid, kind):
    g["deck"].remove(kind)
    g["players"][pid]["dev"].append({"type": kind, "bought": -1})


@pytest.mark.parametrize("n", [3, 4, 5, 6])
def test_board_and_component_counts(n):
    g = game(n, False)
    expanded = n > 4
    board = g["board"]
    assert len(board["tiles"]) == (30 if expanded else 19)
    assert len(board["vertices"]) == (80 if expanded else 54)
    assert len(board["edges"]) == (109 if expanded else 72)
    assert sorted(Counter(t["r"] for t in board["tiles"].values()).values()) == (
        [3, 3, 4, 4, 5, 5, 6] if expanded else [3, 3, 4, 4, 5]
    )
    assert Counter(t["resource"] for t in board["tiles"].values()) == dict(
        zip(
            (*RESOURCES, "desert"),
            (6, 5, 6, 6, 5, 2) if expanded else (4, 3, 4, 4, 3, 1),
            strict=True,
        )
    )
    assert sum(bool(edge["port"]) for edge in board["edges"].values()) == (11 if expanded else 9)
    assert sum(t["number"] != 0 for t in board["tiles"].values()) == (28 if expanded else 18)
    assert len(g["deck"]) == (34 if expanded else 25)
    assert set(g["bank"].values()) == {24 if expanded else 19}
    for edge in board["edges"].values():
        if len(edge["tiles"]) == 2:
            assert not all(board["tiles"][t]["number"] in (6, 8) for t in edge["tiles"])


@pytest.mark.parametrize("n", [3, 5, 6])
def test_setup_snakes_and_only_second_settlement_pays(n):
    g = game(n, False)
    sequence = g["order"] + g["order"][::-1]
    for i, pid in enumerate(sequence):
        assert g["active"] == pid
        a = e.legal_actions(g, pid)[0]
        vid = a["vertex"]
        before = sum(g["players"][pid]["resources"].values())
        g = e.apply(g, pid, a)
        expected = (
            sum(
                g["board"]["tiles"][t]["resource"] != "desert"
                for t in g["board"]["vertices"][vid]["tiles"]
            )
            if i >= n
            else 0
        )
        assert sum(g["players"][pid]["resources"].values()) == before + expected
        g = e.apply(g, pid, e.legal_actions(g, pid)[0])
    assert g["phase"] == "roll"
    assert all(e.pieces(g, p) == {"road": 2, "settlement": 2, "city": 0} for p in sequence)


def test_illegal_move_is_atomic_and_distance_rule_is_enforced():
    g = game(3, False)
    pid = g["active"]
    first = e.legal_actions(g, pid)[0]
    g = e.apply(g, pid, first)
    before = json.dumps(g, sort_keys=True)
    with pytest.raises(e.RuleError):
        e.apply(g, pid, {"type": "build_settlement", "vertex": first["vertex"]})
    assert json.dumps(g, sort_keys=True) == before
    for edge in g["board"]["vertices"][first["vertex"]]["edges"]:
        neighbor = e.other_end(g["board"]["edges"][edge], first["vertex"])
        assert neighbor not in e.settlement_sites(g, pid, True)


def test_city_cost_piece_return_and_bank_conservation():
    g = action_game()
    pid = g["active"]
    clear_hands(g)
    e.give(g, pid, {"wheat": 2, "ore": 3})
    vertex = next(
        v
        for v, d in g["board"]["vertices"].items()
        if d["building"] and d["building"]["owner"] == pid
    )
    result = e.apply(g, pid, {"type": "build_city", "vertex": vertex})
    assert e.pieces(result, pid)["settlement"] == 1
    assert e.pieces(result, pid)["city"] == 1
    assert not sum(result["players"][pid]["resources"].values())
    assert set(result["bank"].values()) == {19}


def test_production_city_robber_and_resource_shortage():
    g = game(3, False)
    tile = next(t for t in g["board"]["tiles"] if t != g["robber"])
    t = g["board"]["tiles"][tile]
    resource = t["resource"]
    # Prevent other matching-number hexes from contributing.
    for tid, item in g["board"]["tiles"].items():
        if tid != tile:
            item["number"] = 0
    a, b = t["vertices"][0], t["vertices"][3]
    g["board"]["vertices"][a]["building"] = {"owner": "p0", "level": 2}
    e.produce(g, t["number"])
    assert g["players"]["p0"]["resources"][resource] == 2
    g["robber"] = tile
    e.produce(g, t["number"])
    assert g["players"]["p0"]["resources"][resource] == 2
    g["robber"] = next(tid for tid in g["board"]["tiles"] if tid != tile)
    g["bank"][resource] = 1
    e.produce(g, t["number"])
    assert g["players"]["p0"]["resources"][resource] == 3
    g["bank"][resource] = 1
    g["board"]["vertices"][b]["building"] = {"owner": "p1", "level": 1}
    e.produce(g, t["number"])
    assert g["bank"][resource] == 1  # Multiple claimants: nobody receives a partial payout.


def test_seven_discard_move_steal_and_private_event():
    g = game()
    clear_hands(g)
    actor = g["active"]
    victim = next(p for p in g["order"] if p != actor)
    e.give(g, actor, {"wood": 9})
    e.give(g, victim, {"brick": 3})
    g["rigged_dice"] = [[3, 4]]
    g = e.apply(g, actor, {"type": "roll"})
    assert g["phase"] == "discard" and g["discards"][actor] == 4
    with pytest.raises(e.RuleError):
        e.apply(g, actor, {"type": "discard", "resources": {"wood": 3}})
    g = e.apply(g, actor, {"type": "discard", "resources": {"wood": 4}})
    tile = next(
        t
        for t, data in g["board"]["tiles"].items()
        if t != g["robber"]
        and any(
            g["board"]["vertices"][v]["building"]
            and g["board"]["vertices"][v]["building"]["owner"] == victim
            for v in data["vertices"]
        )
    )
    g = e.apply(g, actor, {"type": "move_robber", "tile": tile})
    assert victim in g["victims"]
    g = e.apply(g, actor, {"type": "steal", "victim": victim}, random.Random(0))
    assert g["phase"] == "action"
    assert g["players"][actor]["resources"]["brick"] == 1
    spectator = next(p for p in g["order"] if p not in (actor, victim))
    assert "resource" not in e.player_view(g, spectator)["last_event"]
    assert e.player_view(g, victim)["last_event"]["resource"] == "brick"
    assert "resources" not in e.player_view(g, spectator)["players"][actor]
    assert "deck" not in e.player_view(g, actor)


@pytest.mark.parametrize("n", [5, 6])
def test_paired_turn_markers_and_player_two_restrictions(n):
    g = action_game(n)
    order = g["order"].copy()
    primary = g["active"]
    g = e.apply(g, primary, {"type": "end_turn"})
    assert g["active"] == order[3] and g["secondary"] and g["phase"] == "action"
    with pytest.raises(e.RuleError):
        e.apply(g, g["active"], {"type": "roll"})
    with pytest.raises(e.RuleError):
        e.apply(g, g["active"], {"type": "offer_trade", "give": {"wood": 1}, "want": {"ore": 1}})
    # A card bought as player 2 becomes eligible at their next player-1 turn.
    pid = g["active"]
    clear_hands(g)
    e.give(g, pid, e.COSTS["development"])
    g["deck"].remove("knight")
    g["deck"].append("knight")
    g = e.apply(g, pid, {"type": "buy_dev"})
    assert not any(a.get("card") == "knight" for a in e.legal_actions(g, pid))
    while True:
        g = e.apply(g, g["active"], {"type": "end_turn"})
        if g["phase"] == "roll":
            if g["active"] == pid:
                break
            g["phase"] = "action"
    assert any(a.get("card") == "knight" for a in e.legal_actions(g, pid))


@pytest.mark.parametrize("kind", ["knight", "road_building", "year_of_plenty", "monopoly"])
def test_development_effects_before_dice_and_one_per_turn(kind):
    g = game()
    pid = g["active"]
    card(g, pid, kind)
    card(g, pid, "knight")
    if kind == "year_of_plenty":
        cmd = {"type": "play_dev", "card": kind, "resources": {"ore": 2}}
    elif kind == "monopoly":
        clear_hands(g)
        for p in g["order"]:
            if p != pid:
                e.give(g, p, {"wool": 2})
        cmd = {"type": "play_dev", "card": kind, "resource": "wool"}
    else:
        cmd = {"type": "play_dev", "card": kind}
    g = e.apply(g, pid, cmd)
    if kind == "knight":
        assert g["phase"] == "robber" and g["players"][pid]["knights"] == 1
    elif kind == "road_building":
        while g["phase"] == "free_roads":
            g = e.apply(g, pid, e.legal_actions(g, pid)[0])
        assert e.pieces(g, pid)["road"] == 4
    elif kind == "monopoly":
        assert g["players"][pid]["resources"]["wool"] == 4
    else:
        assert g["players"][pid]["resources"]["ore"] >= 2
    assert not any(a["type"] == "play_dev" for a in e.legal_actions(g, pid))
    assert g["return_phase"] == "roll" or g["phase"] == "roll"


def test_ports_and_atomic_player_trade():
    g = action_game()
    a, b = g["order"][:2]
    clear_hands(g)
    edge = next(edge for edge in g["board"]["edges"].values() if edge["port"] == "wood")
    g["board"]["vertices"][edge["a"]]["building"] = {"owner": a, "level": 1}
    assert e.trade_ratios(g, a)["wood"] == 2
    e.give(g, a, {"wood": 4})
    e.give(g, b, {"ore": 2})
    g = e.apply(g, a, {"type": "bank_trade", "give": "wood", "receive": "wheat"})
    g = e.apply(g, a, {"type": "offer_trade", "to": b, "give": {"wood": 1}, "want": {"ore": 1}})
    g = e.apply(g, b, {"type": "accept_trade", "offer": g["offers"][0]["id"]})
    assert g["players"][a]["resources"]["ore"] == 1
    assert g["players"][b]["resources"]["wood"] == 1
    with pytest.raises(e.RuleError):
        e.apply(g, b, {"type": "offer_trade", "to": a, "give": {"ore": 1}, "want": {"ore": 1}})


def test_longest_road_cycle_block_and_ties():
    g = game(3, False)
    tile = next(iter(g["board"]["tiles"].values()))
    corners = set(tile["vertices"])
    for edge in g["board"]["edges"].values():
        if edge["a"] in corners and edge["b"] in corners:
            edge["owner"] = "p0"
    assert e.longest_path(g, "p0") == 6
    e.rescore(g)
    assert g["longest_road"] == "p0"
    vertices = tile["vertices"]
    g["board"]["vertices"][vertices[0]]["building"] = {"owner": "p1", "level": 1}
    g["board"]["vertices"][vertices[3]]["building"] = {"owner": "p2", "level": 1}
    assert e.longest_path(g, "p0") == 3
    e.rescore(g)
    assert g["longest_road"] is None
    assert e.award({"p0": 5, "p1": 5}, "p0", 5) == "p0"
    assert e.award({"p0": 5, "p1": 5}, None, 5) is None


def test_hidden_victory_points_and_secondary_turn_win():
    g = action_game(6)
    secondary = g["order"][3]
    # A configurable scenario target of 3: two starting settlements + a hidden point.
    g["target"] = 3
    card(g, secondary, "victory_point")
    assert e.player_view(g, g["active"])["players"][secondary]["points"] == 2
    e.rescore(g)
    assert g["winner"] is None
    g = e.apply(g, g["active"], {"type": "end_turn"})
    assert g["winner"] == secondary and g["phase"] == "finished"


@pytest.mark.parametrize("n", [3, 5, 6])
def test_scenario_roundtrip_preserves_turn_order_bank_and_cards(n):
    g = game(n)
    restored = validate_game(g)
    for field in ("order", "players", "bank", "deck", "board", "active", "phase"):
        assert restored[field] == g[field]
    session = {"schema": 1, "game": g}
    encoded = encode_save(session)
    assert decode_save(encoded) == session
    damaged = bytearray(encoded)
    damaged[10] ^= 1
    with pytest.raises(e.RuleError):
        decode_save(bytes(damaged))
    bad = copy.deepcopy(g)
    bad["players"][g["active"]]["resources"]["ore"] = 100
    with pytest.raises(e.RuleError):
        validate_game(bad)


@pytest.mark.parametrize("pending", ["knight", "road_building", "seven"])
def test_save_in_the_middle_of_a_pending_choice(pending):
    g = game(6)
    pid = g["active"]
    if pending == "seven":
        e.give(g, pid, {"wood": 8})
        g["rigged_dice"] = [[3, 4]]
        g = e.apply(g, pid, {"type": "roll"})
    else:
        card(g, pid, pending)
        g = e.apply(g, pid, {"type": "play_dev", "card": pending})
    restored = validate_game(g)
    for key in ("phase", "active", "return_phase", "free_roads", "discards", "players", "order"):
        assert restored[key] == g[key]
    assert e.legal_actions(restored, pid) == e.legal_actions(g, pid)


@pytest.mark.parametrize("n", [3, 4, 5, 6])
def test_complete_games_conserve_resources_and_finish_at_ten(n):
    g = game(n, False)
    rng = random.Random(n)
    for _ in range(4000):
        if g["winner"]:
            assert e.points(g, g["winner"], True) >= 10
            return
        pid = next(iter(g["discards"])) if g["phase"] == "discard" else g["active"]
        actions = e.legal_actions(g, pid)
        assert actions, f"No valid action in {g['phase']}"
        g["view_ratios"] = e.trade_ratios(g, pid)
        g = e.apply(g, pid, choose_builtin(g, pid, actions), rng)
        for r in RESOURCES:
            assert g["bank"][r] >= 0
            assert g["bank"][r] + sum(p["resources"][r] for p in g["players"].values()) == (
                24 if n > 4 else 19
            )
    pytest.fail(f"Game did not finish: {[e.points(g, p, True) for p in g['order']]}")
