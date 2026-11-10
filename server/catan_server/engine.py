"""Authoritative rules. Commands are atomic: failures leave the input unchanged."""

import copy
import itertools
import random
import time
from collections import Counter

from catan_server.board import RESOURCES, make_board

COSTS = {
    "road": {"wood": 1, "brick": 1},
    "settlement": {"wood": 1, "brick": 1, "wool": 1, "wheat": 1},
    "city": {"ore": 3, "wheat": 2},
    "development": {"ore": 1, "wool": 1, "wheat": 1},
}
DEVELOPMENTS = ("knight", "road_building", "year_of_plenty", "monopoly", "victory_point")
PHASES = {
    "opening_roll",
    "setup_settlement",
    "setup_road",
    "roll",
    "action",
    "discard",
    "robber",
    "steal",
    "free_roads",
    "finished",
}


class RuleError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuleError(message)


def empty_hand() -> dict:
    return dict.fromkeys(RESOURCES, 0)


def resource_bundle(value: dict) -> dict:
    require(isinstance(value, dict) and not set(value) - set(RESOURCES), "Unknown resource")
    require(
        all(type(n) is int and 0 <= n <= 120 for n in value.values()),
        "Resource quantities must be non-negative integers",
    )
    return {r: value.get(r, 0) for r in RESOURCES}


def new_game(
    ids: list[str],
    seed: str = "",
    target: int = 10,
    *,
    deck_rng: random.Random | None = None,
    opening: bool = True,
) -> dict:
    require(3 <= len(ids) <= 6 and len(set(ids)) == len(ids), "A game needs 3–6 unique seats")
    require(type(target) is int and 3 <= target <= 30, "Victory target must be 3–30")
    rng = random.Random(seed)
    expanded = len(ids) > 4
    board = make_board(expanded, rng)
    order = ids.copy()
    deck = [
        card
        for card, count in zip(
            DEVELOPMENTS, (20, 3, 3, 3, 5) if expanded else (14, 2, 2, 2, 5), strict=True
        )
        for _ in range(count)
    ]
    # The public board seed must NEVER determine the hidden development deck.
    # A separate injected generator is only for reproducible engine tests.
    (deck_rng or random.SystemRandom()).shuffle(deck)
    return {
        "schema": 1,
        "ruleset": "base-paired-2025",
        "seed": seed,
        "expanded": expanded,
        "target": target,
        "board": board,
        "order": order,
        "players": {
            p: {"resources": empty_hand(), "dev": [], "knights": 0, "played": []} for p in ids
        },
        "bank": dict.fromkeys(RESOURCES, 24 if expanded else 19),
        "deck": deck,
        "played_cards": [],
        "phase": "opening_roll" if opening else "setup_settlement",
        "opening": {"round": 1, "pending": ids.copy(), "rolls": {}, "history": []},
        "active": order[0],
        "primary": 0,
        "secondary": False,
        "turn": 0,
        "dev_played": False,
        "setup_index": 0,
        "setup_vertex": None,
        "return_phase": "action",
        "free_roads": 0,
        "discards": {},
        "victims": [],
        "steal_target": None,
        "dice": [],
        "robber": next(t for t, v in board["tiles"].items() if v["resource"] == "desert"),
        "longest_road": None,
        "largest_army": None,
        "road_lengths": dict.fromkeys(ids, 0),
        "winner": None,
        "offers": [],
        "next_offer": 1,
        "rigged_dice": [],
        "modified": target != 10,
        "last_event": {"type": "start", "text": "Place starting settlements"},
    }


def other_end(edge: dict, vertex: str) -> str:
    return edge["b"] if edge["a"] == vertex else edge["a"]


def pieces(g: dict, pid: str) -> dict:
    buildings = [
        v["building"]
        for v in g["board"]["vertices"].values()
        if v["building"] and v["building"]["owner"] == pid
    ]
    return {
        "road": sum(e["owner"] == pid for e in g["board"]["edges"].values()),
        "settlement": sum(b["level"] == 1 for b in buildings),
        "city": sum(b["level"] == 2 for b in buildings),
    }


def settlement_sites(g: dict, pid: str, setup: bool = False) -> list[str]:
    board = g["board"]
    if pieces(g, pid)["settlement"] >= 5:
        return []
    return [
        vid
        for vid, v in board["vertices"].items()
        if not v["building"]
        and not any(
            board["vertices"][other_end(board["edges"][eid], vid)]["building"] for eid in v["edges"]
        )
        and (setup or any(board["edges"][eid]["owner"] == pid for eid in v["edges"]))
    ]


def road_sites(g: dict, pid: str, setup_vertex: str | None = None) -> list[str]:
    b = g["board"]
    if pieces(g, pid)["road"] >= 15:
        return []
    result = []
    for eid, e in b["edges"].items():
        if e["owner"]:
            continue
        if setup_vertex:
            if setup_vertex in (e["a"], e["b"]):
                result.append(eid)
            continue
        for vid in (e["a"], e["b"]):
            v = b["vertices"][vid]
            if v["building"]:
                connected = v["building"]["owner"] == pid
            else:
                connected = any(b["edges"][adj]["owner"] == pid for adj in v["edges"])
            if connected:
                result.append(eid)
                break
    return result


def trade_ratios(g: dict, pid: str) -> dict:
    ratios = dict.fromkeys(RESOURCES, 4)
    b = g["board"]
    for e in b["edges"].values():
        if e["port"] and any(
            b["vertices"][v]["building"] and b["vertices"][v]["building"]["owner"] == pid
            for v in (e["a"], e["b"])
        ):
            for r in RESOURCES:
                if e["port"] == "any":
                    ratios[r] = min(ratios[r], 3)
                elif e["port"] == r:
                    ratios[r] = 2
    return ratios


def longest_path(g: dict, pid: str) -> int:
    b = g["board"]
    owned = {eid for eid, e in b["edges"].items() if e["owner"] == pid}

    def walk(vid: str, used: frozenset) -> int:
        building = b["vertices"][vid]["building"]
        if used and building and building["owner"] != pid:
            return len(used)
        return max(
            [len(used)]
            + [
                walk(other_end(b["edges"][eid], vid), used | {eid})
                for eid in b["vertices"][vid]["edges"]
                if eid in owned - used
            ]
        )

    starts = {v for eid in owned for v in (b["edges"][eid]["a"], b["edges"][eid]["b"])}
    return max((walk(v, frozenset()) for v in starts), default=0)


def award(values: dict, incumbent: str | None, threshold: int) -> str | None:
    best = max(values.values())
    if best < threshold:
        return None
    leaders = [p for p, n in values.items() if n == best]
    return incumbent if incumbent in leaders else leaders[0] if len(leaders) == 1 else None


def points(g: dict, pid: str, private: bool = False) -> int:
    built = pieces(g, pid)
    value = built["settlement"] + 2 * built["city"]
    value += 2 * (g["longest_road"] == pid) + 2 * (g["largest_army"] == pid)
    if private or g["winner"]:
        value += sum(c["type"] == "victory_point" for c in g["players"][pid]["dev"])
    return value


def rescore(g: dict) -> None:
    g["road_lengths"] = {p: longest_path(g, p) for p in g["order"]}
    g["longest_road"] = award(g["road_lengths"], g["longest_road"], 5)
    g["largest_army"] = award(
        {p: g["players"][p]["knights"] for p in g["order"]}, g["largest_army"], 3
    )
    if g["phase"] not in ("opening_roll", "setup_settlement", "setup_road", "finished"):
        if points(g, g["active"], True) >= g["target"]:
            g["winner"], g["phase"] = g["active"], "finished"
            g["offers"] = []


def can_pay(g: dict, pid: str, cost: dict) -> bool:
    return all(g["players"][pid]["resources"][r] >= n for r, n in cost.items())


def pay(g: dict, pid: str, cost: dict) -> None:
    require(can_pay(g, pid, cost), "Not enough resources")
    for r, n in cost.items():
        g["players"][pid]["resources"][r] -= n
        g["bank"][r] += n


def give(g: dict, pid: str, bundle: dict) -> None:
    require(all(g["bank"][r] >= n for r, n in bundle.items()), "The bank lacks those resources")
    for r, n in bundle.items():
        g["bank"][r] -= n
        g["players"][pid]["resources"][r] += n


def produce(g: dict, number: int) -> None:
    demand = {r: Counter() for r in RESOURCES}
    for tid, tile in g["board"]["tiles"].items():
        if tile["number"] == number and tid != g["robber"] and tile["resource"] in RESOURCES:
            for vid in tile["vertices"]:
                building = g["board"]["vertices"][vid]["building"]
                if building:
                    demand[tile["resource"]][building["owner"]] += building["level"]
    for r, players in demand.items():
        if sum(players.values()) <= g["bank"][r]:
            for p, n in players.items():
                give(g, p, {r: n})
        elif len(players) == 1:
            give(g, next(iter(players)), {r: g["bank"][r]})


def legal_actions(g: dict | None, pid: str | None, *, now: float | None = None) -> list[dict]:
    if not g or pid not in g["players"] or g["winner"]:
        return []
    phase = g["phase"]
    if phase == "opening_roll":
        return [{"type": "opening_roll"}] if pid == g["active"] else []
    if phase == "discard" and pid in g["discards"]:
        # One suggested bundle; arbitrary bundles of the required size are also accepted.
        hand = g["players"][pid]["resources"].copy()
        bundle = empty_hand()
        for _ in range(g["discards"][pid]):
            r = max(RESOURCES, key=lambda r: hand[r])
            bundle[r] += 1
            hand[r] -= 1
        return [{"type": "discard", "resources": bundle}]
    if pid != g["active"]:
        if phase != "action" or g["secondary"]:
            return []
        return trade_actions(g, pid, now) + suggested_trades(g, pid)
    if phase == "setup_settlement":
        return [{"type": "build_settlement", "vertex": v} for v in settlement_sites(g, pid, True)]
    if phase == "setup_road":
        return [{"type": "build_road", "edge": e} for e in road_sites(g, pid, g["setup_vertex"])]
    if phase == "robber":
        return [{"type": "move_robber", "tile": t} for t in g["board"]["tiles"] if t != g["robber"]]
    if phase == "steal":
        if g.get("steal_target"):
            return [{"type": "steal", "victim": g["steal_target"]}]
        return [{"type": "inspect_steal", "victim": p} for p in g["victims"]]
    if phase == "free_roads":
        return [{"type": "build_road", "edge": e} for e in road_sites(g, pid)]
    if phase not in ("roll", "action"):
        return []
    result = (
        [{"type": "roll"}] if phase == "roll" else ([] if g["offers"] else [{"type": "end_turn"}])
    )
    if not g["dev_played"]:
        usable = {c["type"] for c in g["players"][pid]["dev"] if c["bought"] < g["turn"]}
        for card in usable - {"victory_point"}:
            if card == "monopoly":
                result += [{"type": "play_dev", "card": card, "resource": r} for r in RESOURCES]
            elif card == "year_of_plenty":
                count = min(2, sum(g["bank"].values()))
                for combo in itertools.combinations_with_replacement(RESOURCES, count):
                    bundle = dict(Counter(combo))
                    if all(g["bank"][r] >= n for r, n in bundle.items()):
                        result.append({"type": "play_dev", "card": card, "resources": bundle})
            elif card != "road_building" or road_sites(g, pid):
                if g["offers"] and card in ("knight", "road_building"):
                    continue
                result.append({"type": "play_dev", "card": card})
    if phase == "roll":
        return result
    for kind, key, sites in (
        ("road", "edge", road_sites(g, pid)),
        ("settlement", "vertex", settlement_sites(g, pid)),
        (
            "city",
            "vertex",
            [
                v
                for v, data in g["board"]["vertices"].items()
                if data["building"] == {"owner": pid, "level": 1}
            ]
            if pieces(g, pid)["city"] < 4
            else [],
        ),
    ):
        if can_pay(g, pid, COSTS[kind]):
            result += [{"type": f"build_{kind}", key: site} for site in sites]
    if g["deck"] and can_pay(g, pid, COSTS["development"]):
        result.append({"type": "buy_dev"})
    ratios = trade_ratios(g, pid)
    for r in RESOURCES:
        if g["players"][pid]["resources"][r] >= ratios[r]:
            result += [
                {"type": "bank_trade", "give": r, "receive": s}
                for s in RESOURCES
                if s != r and g["bank"][s]
            ]
    if not g["secondary"]:
        result += trade_actions(g, pid, now) + suggested_trades(g, pid)
    return result


def apply(
    g: dict, pid: str, command: dict, rng: random.Random | None = None, *, now: float | None = None
) -> dict:
    require(isinstance(command, dict), "Command must be an object")
    require(pid in g["players"], "Unknown player")
    require(not g["winner"], "The game is over")
    rng = rng or random.SystemRandom()
    g = copy.deepcopy(g)
    kind = command.get("type")
    phase = g["phase"]
    event = {"type": str(kind), "actor": pid, "text": str(kind).replace("_", " ")}
    g["last_event"] = event
    if kind in (
        "offer_trade",
        "accept_trade",
        "reject_trade",
        "select_trade",
        "cancel_trade",
        "settle_trade",
    ):
        _trade(g, pid, command, time.time() if now is None else now)
        return g
    require(kind == "discard" or pid == g["active"], "It is not your turn")
    if kind == "opening_roll":
        require(phase == "opening_roll", "Opening rolls have already finished")
        dice = (
            g["rigged_dice"].pop(0) if g["rigged_dice"] else [rng.randint(1, 6), rng.randint(1, 6)]
        )
        g["dice"] = dice
        opening = g["opening"]
        opening["rolls"][pid] = sum(dice)
        opening["history"].append({"round": opening["round"], "player": pid, "dice": dice.copy()})
        opening["pending"].remove(pid)
        event.update(dice=dice.copy(), text=f"rolled {sum(dice)} for first player")
        if not opening["pending"]:
            highest = max(opening["rolls"].values())
            leaders = [p for p in g["order"] if opening["rolls"].get(p) == highest]
            if len(leaders) == 1:
                first = g["order"].index(leaders[0])
                g["order"] = g["order"][first:] + g["order"][:first]
                g["active"], g["phase"] = leaders[0], "setup_settlement"
                event["first_player"] = leaders[0]
            else:
                opening.update(round=opening["round"] + 1, rolls={}, pending=leaders)
                g["active"] = leaders[0]
                event["text"] += "; highest players reroll the tie"
        else:
            g["active"] = opening["pending"][0]
    elif kind == "discard":
        require(phase == "discard" and pid in g["discards"], "No discard is required")
        bundle = resource_bundle(command.get("resources"))
        require(sum(bundle.values()) == g["discards"][pid], "Discard exactly half, rounded down")
        pay(g, pid, bundle)
        del g["discards"][pid]
        if not g["discards"]:
            g["phase"] = "robber"
    elif kind == "build_settlement":
        setup = phase == "setup_settlement"
        require(setup or phase == "action", "Cannot build a settlement in this phase")
        vid = command.get("vertex")
        require(
            vid in settlement_sites(g, pid, setup),
            "Settlement violates distance or connection rules",
        )
        if not setup:
            pay(g, pid, COSTS["settlement"])
        g["board"]["vertices"][vid]["building"] = {"owner": pid, "level": 1}
        event["vertex"] = vid
        if setup:
            if g["setup_index"] >= len(g["order"]):
                income = Counter(
                    g["board"]["tiles"][t]["resource"] for t in g["board"]["vertices"][vid]["tiles"]
                )
                give(g, pid, {r: income[r] for r in RESOURCES})
            g["setup_vertex"], g["phase"] = vid, "setup_road"
    elif kind == "build_road":
        require(phase in ("setup_road", "action", "free_roads"), "Cannot build a road now")
        eid = command.get("edge")
        require(
            eid in road_sites(g, pid, g["setup_vertex"] if phase == "setup_road" else None),
            "Road must connect to your network without passing through an opponent",
        )
        if phase == "action":
            pay(g, pid, COSTS["road"])
        g["board"]["edges"][eid]["owner"] = pid
        event["edge"] = eid
        if phase == "setup_road":
            g["setup_index"] += 1
            sequence = g["order"] + g["order"][::-1]
            if g["setup_index"] == len(sequence):
                g["phase"], g["active"] = "roll", g["order"][0]
            else:
                g["phase"], g["active"] = "setup_settlement", sequence[g["setup_index"]]
        elif phase == "free_roads":
            g["free_roads"] -= 1
            if not g["free_roads"] or not road_sites(g, pid):
                g["phase"] = g["return_phase"]
    elif kind == "build_city":
        require(phase == "action", "Cannot upgrade now")
        vid = command.get("vertex")
        require(
            vid in g["board"]["vertices"]
            and g["board"]["vertices"][vid]["building"] == {"owner": pid, "level": 1},
            "Upgrade one of your settlements",
        )
        require(pieces(g, pid)["city"] < 4, "All four city pieces are in use")
        pay(g, pid, COSTS["city"])
        g["board"]["vertices"][vid]["building"]["level"] = 2
        event["vertex"] = vid
    elif kind == "roll":
        require(phase == "roll", "Dice may be rolled only once by player 1")
        g["dice"] = (
            g["rigged_dice"].pop(0) if g["rigged_dice"] else [rng.randint(1, 6) for _ in range(2)]
        )
        event["text"] = f"rolled {sum(g['dice'])}"
        if sum(g["dice"]) == 7:
            g["discards"] = {
                p: sum(v["resources"].values()) // 2
                for p, v in g["players"].items()
                if sum(v["resources"].values()) > 7
            }
            g["return_phase"] = "action"
            g["phase"] = "discard" if g["discards"] else "robber"
        else:
            produce(g, sum(g["dice"]))
            g["phase"] = "action"
    elif kind == "move_robber":
        require(phase == "robber", "The robber cannot move now")
        tile = command.get("tile")
        require(tile in g["board"]["tiles"] and tile != g["robber"], "Choose a different land hex")
        g["robber"] = tile
        g["steal_target"] = None
        event["tile"] = tile
        g["victims"] = sorted(
            {
                b["owner"]
                for v in g["board"]["tiles"][tile]["vertices"]
                if (b := g["board"]["vertices"][v]["building"])
                and b["owner"] != pid
                and sum(g["players"][b["owner"]]["resources"].values())
            }
        )
        g["phase"] = "steal" if g["victims"] else g["return_phase"]
    elif kind == "inspect_steal":
        victim = command.get("victim")
        require(phase == "steal" and victim in g["victims"], "Choose an eligible victim")
        g["steal_target"] = victim
        event.update(victim=victim, text="is choosing a face-down card")
    elif kind == "steal":
        victim = command.get("victim")
        require(phase == "steal" and victim in g["victims"], "Choose an eligible victim")
        cards = [r for r, n in g["players"][victim]["resources"].items() for _ in range(n)]
        resource = rng.choice(cards)
        g["players"][victim]["resources"][resource] -= 1
        g["players"][pid]["resources"][resource] += 1
        event.update({"victim": victim, "resource": resource, "text": "stole a card"})
        g["phase"], g["victims"] = g["return_phase"], []
        g["steal_target"] = None
    elif kind == "bank_trade":
        require(phase == "action", "Trade during the action phase")
        r, s = command.get("give"), command.get("receive")
        require(r in RESOURCES and s in RESOURCES and r != s, "Choose two different resources")
        require(g["bank"][s] > 0, "Bank is out of that resource")
        ratio = trade_ratios(g, pid)[r]
        pay(g, pid, {r: ratio})
        give(g, pid, {s: 1})
        event["text"] = f"traded {ratio} {r} for 1 {s} with the bank"
    elif kind == "buy_dev":
        require(phase == "action" and bool(g["deck"]), "Cannot buy a development card now")
        pay(g, pid, COSTS["development"])
        g["players"][pid]["dev"].append({"type": g["deck"].pop(), "bought": g["turn"]})
    elif kind == "play_dev":
        require(
            phase in ("roll", "action") and not g["dev_played"],
            "Play one development card per turn",
        )
        card = command.get("card")
        require(
            not g["offers"] or card not in ("knight", "road_building"),
            "Resolve or cancel trades before entering a different phase",
        )
        require(
            card in DEVELOPMENTS and card != "victory_point", "Victory points count automatically"
        )
        cards = g["players"][pid]["dev"]
        index = next(
            (i for i, c in enumerate(cards) if c["type"] == card and c["bought"] < g["turn"]), None
        )
        require(index is not None, "You cannot play a card bought during this turn")
        cards.pop(index)
        g["played_cards"].append(card)
        g["players"][pid].setdefault("played", []).append(card)
        g["dev_played"] = True
        event["text"] = f"played {card.replace('_', ' ')}"
        event["card"] = card
        if card == "knight":
            g["players"][pid]["knights"] += 1
            g["return_phase"], g["phase"] = phase, "robber"
        elif card == "road_building":
            require(bool(road_sites(g, pid)), "No free road can be placed")
            g["return_phase"], g["phase"], g["free_roads"] = phase, "free_roads", 2
        elif card == "year_of_plenty":
            bundle = resource_bundle(command.get("resources"))
            require(
                sum(bundle.values()) == min(2, sum(g["bank"].values())),
                "Choose two available resources",
            )
            give(g, pid, bundle)
        elif card == "monopoly":
            r = command.get("resource")
            require(r in RESOURCES, "Choose a resource")
            event["resource"] = r
            for p, data in g["players"].items():
                if p != pid:
                    g["players"][pid]["resources"][r] += data["resources"][r]
                    data["resources"][r] = 0
    elif kind == "end_turn":
        require(phase == "action", "Resolve the current phase before ending the turn")
        require(not g["offers"], "Resolve or cancel outstanding trades before ending the turn")
        _next_turn(g)
    else:
        raise RuleError("Unknown command")
    rescore(g)
    return g


def _next_turn(g: dict) -> None:
    g["turn"] += 1
    g["dev_played"], g["offers"] = False, []
    if g["expanded"] and not g["secondary"]:
        g["secondary"] = True
        g["active"], g["phase"] = g["order"][(g["primary"] + 3) % len(g["order"])], "action"
    else:
        g["secondary"] = False
        g["primary"] = (g["primary"] + 1) % len(g["order"])
        g["active"], g["phase"] = g["order"][g["primary"]], "roll"


def offer_recipients(g: dict, offer: dict) -> list[str]:
    targets = offer.get("to") or []
    if isinstance(targets, str):
        targets = [targets]
    return [
        p
        for p in g["order"]
        if p != offer["from"]
        and (not targets or p in targets)
        and g["active"] in (p, offer["from"])
    ]


def trade_actions(g: dict, pid: str, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    actions = []
    for o in g["offers"]:
        responses = o.get("responses", {})
        if pid == o["from"]:
            if now >= o.get("closes_at", 0):
                actions.append({"type": "cancel_trade", "offer": o["id"]})
                accepted = [
                    p
                    for p, answer in responses.items()
                    if answer == "accepted" and can_pay(g, p, o["want"])
                ]
                if len(accepted) > 1:
                    actions += [
                        {"type": "select_trade", "offer": o["id"], "player": p} for p in accepted
                    ]
        elif pid in offer_recipients(g, o) and responses.get(pid, "pending") == "pending":
            if o.get("selecting"):
                continue
            actions.append({"type": "reject_trade", "offer": o["id"]})
            if can_pay(g, pid, o["want"]) and can_pay(g, o["from"], o["give"]):
                actions.append({"type": "accept_trade", "offer": o["id"]})
    return actions


def suggested_trades(g: dict, pid: str) -> list[dict]:
    if any(o["from"] == pid for o in g["offers"]):
        return []
    hand = g["players"][pid]["resources"]
    return [
        {
            "type": "offer_trade",
            "to": [] if pid == g["active"] else [g["active"]],
            "give": {r: 1},
            "want": {s: 1},
        }
        for r in RESOURCES
        if hand[r] > 1
        for s in RESOURCES
        if s != r and hand[s] == 0
    ]


def _exchange(g: dict, offer: dict, partner: str) -> None:
    require(
        can_pay(g, partner, offer["want"]) and can_pay(g, offer["from"], offer["give"]),
        "Resources changed; offer can no longer be accepted",
    )
    for r in RESOURCES:
        delta = offer["give"][r] - offer["want"][r]
        g["players"][partner]["resources"][r] += delta
        g["players"][offer["from"]]["resources"][r] -= delta
    g["offers"].remove(offer)
    g["last_event"].update(
        type="trade_completed",
        proposer=offer["from"],
        partner=partner,
        give=offer["give"],
        want=offer["want"],
        text="completed a player trade",
    )


def _resolve_offer(g: dict, offer: dict, now: float) -> None:
    responses = offer["responses"]
    for p, answer in responses.items():
        if answer == "accepted" and not can_pay(g, p, offer["want"]):
            responses[p] = "rejected"
    if not can_pay(g, offer["from"], offer["give"]) or all(
        v == "rejected" for v in responses.values()
    ):
        g["offers"].remove(offer)
        g["last_event"].update(type="trade_failed", text="trade closed: rejected or unaffordable")
        return
    if now < offer["closes_at"]:
        return
    offer["window_closed"] = True
    accepted = [p for p, answer in responses.items() if answer == "accepted"]
    if len(accepted) == 1:
        _exchange(g, offer, accepted[0])
    elif len(accepted) > 1:
        offer["selecting"] = True
        g["last_event"].update(type="trade_selection", text="proposer must choose a trade partner")
    else:
        g["last_event"].update(type="trade_open", text="trade open for the first acceptance")


def _trade(g: dict, pid: str, c: dict, now: float) -> None:
    require(
        g["phase"] == "action" and not g["secondary"],
        "Player trades are only allowed in player 1's action phase",
    )
    if c["type"] == "offer_trade":
        targets = c.get("to") or []
        if isinstance(targets, str):
            targets = [targets]
        require(
            isinstance(targets, list)
            and all(isinstance(p, str) and p in g["players"] and p != pid for p in targets),
            "Choose different valid players",
        )
        require(
            pid == g["active"] or targets == [g["active"]], "Trades must involve the active player"
        )
        give_bundle, want = resource_bundle(c.get("give")), resource_bundle(c.get("want"))
        require(
            sum(give_bundle.values()) + sum(want.values()) > 0
            and (bool(targets) or sum(give_bundle.values()) > 0 and sum(want.values()) > 0),
            "Choose specific recipients for a gift; public offers must exchange resources",
        )
        require(
            not any(give_bundle[r] and want[r] for r in RESOURCES),
            "Cannot exchange the same resource on both sides",
        )
        require(can_pay(g, pid, give_bundle), "You cannot offer cards you do not own")
        require(len(g["offers"]) < 20, "Cancel an existing offer first")
        offer = {
            "id": g["next_offer"],
            "from": pid,
            "to": sorted(set(targets)),
            "give": give_bundle,
            "want": want,
            "created_at": now,
            "closes_at": now + 5,
            "responses": {},
            "window_closed": False,
            "selecting": False,
        }
        offer["responses"] = dict.fromkeys(offer_recipients(g, offer), "pending")
        g["offers"].append(offer)
        g["next_offer"] += 1
    else:
        offer = next((o for o in g["offers"] if o["id"] == c.get("offer")), None)
        require(offer is not None, "Offer has expired")
        g["last_event"].update(offer=offer["id"])
        if c["type"] == "cancel_trade":
            require(
                pid in (offer["from"], g["active"]), "Only the proposer or active player can cancel"
            )
            require(now >= offer["closes_at"], "Give players the full five-second response window")
            g["offers"].remove(offer)
        elif c["type"] == "settle_trade":
            require(pid == offer["from"], "Only the proposer can settle an offer")
            _resolve_offer(g, offer, now)
        elif c["type"] == "select_trade":
            require(
                pid == offer["from"] and now >= offer["closes_at"],
                "Only the proposer may select after five seconds",
            )
            require(
                offer["responses"].get(c.get("player")) == "accepted", "Choose an accepting player"
            )
            _exchange(g, offer, c["player"])
        else:
            require(
                pid in offer_recipients(g, offer),
                "Offer is not addressed to you",
            )
            require(offer["responses"].get(pid) == "pending", "You have already responded")
            require(
                not offer.get("selecting"), "The proposer is choosing among existing acceptances"
            )
            if now >= offer["closes_at"] and not offer["window_closed"]:
                _resolve_offer(g, offer, now)
                require(
                    offer in g["offers"] and not offer.get("selecting"),
                    "Offer already has an accepted partner",
                )
            if c["type"] == "accept_trade":
                require(
                    can_pay(g, pid, offer["want"]) and can_pay(g, offer["from"], offer["give"]),
                    "Resources changed; offer can no longer be accepted",
                )
            offer["responses"][pid] = "accepted" if c["type"] == "accept_trade" else "rejected"
            g["last_event"]["response"] = offer["responses"][pid]
            _resolve_offer(g, offer, now)
    g["last_event"].update(
        offer=offer["id"], audience=[offer["from"], *offer["responses"]] if offer["to"] else []
    )


def next_roller(g: dict) -> str:
    if g["phase"] == "opening_roll" or g["phase"] == "roll":
        return g["active"]
    if g["phase"].startswith("setup"):
        return g["order"][0]
    return g["order"][(g["primary"] + 1) % len(g["order"])]


def visible_event(event: dict, pid: str | None) -> dict | None:
    if event.get("audience") and pid not in event["audience"]:
        if event.get("type") != "trade_completed":
            return None
        event = copy.deepcopy(event)
        event.pop("give", None)
        event.pop("want", None)
        event.pop("audience", None)
    event = copy.deepcopy(event)
    if event.get("type") == "steal" and pid not in (event.get("actor"), event.get("victim")):
        event.pop("resource", None)
    for effect in event.get("effects", []):
        public_payment = event.get("type") in {
            "build_road",
            "build_settlement",
            "build_city",
            "buy_dev",
            "bank_trade",
            "roll",
            "play_dev",
        }
        participant = event.get("type") == "trade_completed" and pid in (
            event.get("proposer"),
            event.get("partner"),
        )
        if (
            effect.get("player") != pid
            and not public_payment
            and not participant
            and not (
                event.get("type") == "steal" and pid in (event.get("actor"), event.get("victim"))
            )
        ):
            effect.pop("resources", None)
    return event


def player_view(g: dict, pid: str | None, *, now: float | None = None) -> dict:
    view = copy.deepcopy(g)
    view["deck_count"] = len(view.pop("deck"))
    view.pop("rigged_dice", None)
    for p, data in view["players"].items():
        data["resource_count"] = sum(data["resources"].values())
        data["dev_count"] = len(data["dev"])
        data.pop("points", None)
        data["pieces"] = pieces(g, p)
        if p != pid and not g["winner"]:
            data.pop("resources")
            data.pop("dev")
    view["last_event"] = visible_event(view["last_event"], pid) or {
        "type": "private_action",
        "text": "",
    }
    view["offers"] = [
        o
        for o in view["offers"]
        if not o["to"] or pid == o["from"] or pid in offer_recipients(g, o)
    ]
    view["legal"] = legal_actions(g, pid, now=now)
    view["ratios"] = trade_ratios(g, pid) if pid in g["players"] else {}
    return view


def quick_setup(g: dict) -> dict:
    """Legal automatic setup for scenarios, tests, and impatient hosts."""
    opening_rng = random.Random(g["seed"] + ":opening")
    while g["phase"] == "opening_roll" or g["phase"].startswith("setup"):
        options = legal_actions(g, g["active"])
        require(bool(options), "No starting placement is possible")
        if g["phase"] == "setup_settlement":
            options.sort(key=lambda a: vertex_value(g, a["vertex"]), reverse=True)
        g = apply(g, g["active"], options[0], opening_rng)
    return g


def vertex_value(g: dict, vid: str) -> float:
    tiles = [g["board"]["tiles"][t] for t in g["board"]["vertices"][vid]["tiles"]]
    return (
        sum(6 - abs(7 - t["number"]) for t in tiles if t["number"])
        + len({t["resource"] for t in tiles}) * 0.7
    )
