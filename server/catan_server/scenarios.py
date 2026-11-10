"""Validated, portable scenarios and bounded, checksummed CTN saves."""

import copy
import hashlib
import json
import math
import time
import zlib
from collections import Counter

from catan_server.board import RESOURCES
from catan_server.engine import (
    DEVELOPMENTS,
    PHASES,
    RuleError,
    new_game,
    offer_recipients,
    other_end,
    pieces,
    require,
    rescore,
    resource_bundle,
)

MAX_SAVE = 8 * 1024 * 1024


def validate_game(raw: dict, ids: list[str] | None = None) -> dict:
    """Rebuild trusted topology; only accept supported finite component state."""
    require(isinstance(raw, dict) and raw.get("schema") == 1, "Unsupported scenario version")
    require(raw.get("ruleset") == "base-paired-2025", "Unsupported ruleset")
    old_ids = raw.get("order")
    require(
        isinstance(old_ids, list) and all(isinstance(p, str) for p in old_ids), "Missing turn order"
    )
    require(len(old_ids) == len(set(old_ids)) and 3 <= len(old_ids) <= 6, "Invalid seats")
    if ids is not None:
        require(len(ids) == len(old_ids), "Scenario and lobby must have the same player count")
        mapping = dict(zip(old_ids, ids, strict=True))

        def remap(value):
            if isinstance(value, str):
                return mapping.get(value, value)
            if isinstance(value, dict):
                return {mapping.get(k, k): remap(v) for k, v in value.items()}
            if isinstance(value, list):
                return [remap(v) for v in value]
            return value

        raw = remap(raw)
    else:
        ids = old_ids
    require(isinstance(raw.get("seed"), str) and len(raw["seed"]) <= 128, "Invalid seed")
    g = new_game(ids, raw["seed"], raw.get("target", 10))
    g["order"] = ids.copy()
    require(raw.get("expanded") == g["expanded"], "Player count and board expansion disagree")
    for category in ("tiles", "vertices", "edges"):
        require(
            set(raw["board"][category]) == set(g["board"][category]),
            "Board topology does not match player count",
        )
    for tid, tile in g["board"]["tiles"].items():
        source = raw["board"]["tiles"][tid]
        require(source["resource"] in (*RESOURCES, "desert"), "Invalid terrain")
        number = source["number"]
        require(
            type(number) is int
            and (
                number == 0 if source["resource"] == "desert" else 2 <= number <= 12 and number != 7
            ),
            "Invalid number token",
        )
        tile.update(resource=source["resource"], number=number)
    for vid, vertex in g["board"]["vertices"].items():
        b = raw["board"]["vertices"][vid].get("building")
        require(
            b is None
            or isinstance(b, dict)
            and b.get("owner") in ids
            and type(b.get("level")) is int
            and b["level"] in (1, 2),
            "Invalid building",
        )
        vertex["building"] = {"owner": b["owner"], "level": b["level"]} if b else None
    for eid, edge in g["board"]["edges"].items():
        source = raw["board"]["edges"][eid]
        require(source.get("owner") is None or source["owner"] in ids, "Invalid road owner")
        require(source.get("port") in (None, "any", *RESOURCES), "Invalid port")
        require(not source.get("port") or len(edge["tiles"]) == 1, "Ports must be coastal")
        edge.update(owner=source.get("owner"), port=source.get("port"))
    for vid, vertex in g["board"]["vertices"].items():
        if vertex["building"]:
            require(
                not any(
                    g["board"]["vertices"][other_end(g["board"]["edges"][e], vid)]["building"]
                    for e in vertex["edges"]
                ),
                "Buildings violate the distance rule",
            )
    for key in ("turn", "primary", "setup_index", "free_roads"):
        value = raw.get(key, g[key])
        require(type(value) is int and 0 <= value <= 1000000, f"Invalid {key}")
        g[key] = value
    require(
        g["primary"] < len(ids) and g["setup_index"] <= len(ids) * 2 and g["free_roads"] <= 2,
        "Invalid phase counters",
    )
    for key in (
        "phase",
        "active",
        "return_phase",
        "robber",
        "setup_vertex",
        "secondary",
        "dev_played",
    ):
        g[key] = raw.get(key, g[key])
    require(g["phase"] in PHASES and g["active"] in ids, "Invalid phase or active player")
    opening = copy.deepcopy(
        raw.get("opening", {"round": 0, "pending": [], "rolls": {}, "history": []})
    )
    require(
        isinstance(opening, dict)
        and type(opening.get("round")) is int
        and 0 <= opening["round"] <= 10000
        and isinstance(opening.get("pending"), list)
        and all(isinstance(p, str) and p in ids for p in opening["pending"])
        and len(opening["pending"]) == len(set(opening["pending"]))
        and isinstance(opening.get("rolls"), dict)
        and all(
            p in ids and p not in opening["pending"] and type(n) is int and 2 <= n <= 12
            for p, n in opening["rolls"].items()
        )
        and isinstance(opening.get("history"), list)
        and len(opening["history"]) <= 10000,
        "Invalid opening rolls",
    )
    for roll in opening["history"]:
        require(
            isinstance(roll, dict)
            and roll.get("player") in ids
            and type(roll.get("round")) is int
            and 1 <= roll["round"] <= opening["round"]
            and isinstance(roll.get("dice"), list)
            and len(roll["dice"]) == 2
            and all(type(n) is int and 1 <= n <= 6 for n in roll["dice"]),
            "Invalid opening history",
        )
    g["opening"] = opening
    require(g["return_phase"] in ("action", "roll"), "Invalid return phase")
    require(type(g["secondary"]) is bool and type(g["dev_played"]) is bool, "Invalid turn flags")
    require(not g["secondary"] or g["expanded"], "Paired turns require the expansion")
    require(g["robber"] in g["board"]["tiles"], "Invalid robber location")
    require(
        g["setup_vertex"] is None or g["setup_vertex"] in g["board"]["vertices"],
        "Invalid setup vertex",
    )
    if g["phase"].startswith("setup"):
        require(g["setup_index"] < 2 * len(ids), "Setup is already complete")
        sequence = ids + ids[::-1]
        require(
            g["active"] == sequence[g["setup_index"]], "Setup player does not match snake order"
        )
        if g["phase"] == "setup_road":
            require(
                g["setup_vertex"] is not None
                and g["board"]["vertices"][g["setup_vertex"]]["building"]
                == {"owner": g["active"], "level": 1},
                "Setup road needs a new settlement",
            )
    elif g["phase"] == "opening_roll":
        require(
            opening["round"] > 0
            and bool(opening["pending"])
            and g["active"] == opening["pending"][0],
            "Invalid opening player",
        )
    else:
        expected = ids[(g["primary"] + (3 if g["secondary"] else 0)) % len(ids)]
        require(g["active"] == expected, "Active player does not match turn markers")
    require(not (g["phase"] == "roll" and g["secondary"]), "Player 2 cannot roll")
    for pid in ids:
        source = raw["players"][pid]
        g["players"][pid]["resources"] = resource_bundle(source["resources"])
        dev = source.get("dev", [])
        require(isinstance(dev, list) and len(dev) <= 34, "Invalid development hand")
        for c in dev:
            require(
                c.get("type") in DEVELOPMENTS
                and type(c.get("bought")) is int
                and -1 <= c["bought"] <= g["turn"],
                "Invalid development card",
            )
        g["players"][pid]["dev"] = [{"type": c["type"], "bought": c["bought"]} for c in dev]
        knights = source.get("knights", 0)
        require(type(knights) is int and 0 <= knights <= 20, "Invalid knight count")
        g["players"][pid]["knights"] = knights
        played = source.get("played", ["knight"] * knights)
        require(
            isinstance(played, list)
            and len(played) <= 34
            and all(c in DEVELOPMENTS and c != "victory_point" for c in played),
            "Invalid played cards",
        )
        g["players"][pid]["played"] = played.copy()
        built = pieces(g, pid)
        require(
            built["road"] <= 15 and built["settlement"] <= 5 and built["city"] <= 4,
            "Component limit exceeded",
        )
    capacity = 24 if g["expanded"] else 19
    g["bank"] = {r: capacity - sum(g["players"][p]["resources"][r] for p in ids) for r in RESOURCES}
    require(min(g["bank"].values()) >= 0, "Resource hands exceed the bank's finite supply")
    for key in ("deck", "played_cards"):
        require(
            isinstance(raw.get(key), list) and all(c in DEVELOPMENTS for c in raw[key]),
            "Invalid development deck",
        )
        g[key] = raw[key].copy()
    counts = Counter(
        g["deck"] + g["played_cards"] + [c["type"] for p in ids for c in g["players"][p]["dev"]]
    )
    expected_cards = dict(
        zip(DEVELOPMENTS, (20, 3, 3, 3, 5) if g["expanded"] else (14, 2, 2, 2, 5), strict=True)
    )
    require(counts == Counter(expected_cards), "Development cards must conserve the complete deck")
    attributed = Counter(c for p in ids for c in g["players"][p]["played"])
    require(
        not (attributed - Counter(g["played_cards"])), "Played card attribution exceeds the deck"
    )
    require(
        sum(g["players"][p]["knights"] for p in ids) == g["played_cards"].count("knight"),
        "Played knights and army counts disagree",
    )
    for key in ("longest_road", "largest_army"):
        require(raw.get(key) is None or raw[key] in ids, "Unknown award holder")
        g[key] = raw.get(key)
    g["dice"] = raw.get("dice", [])
    require(
        isinstance(g["dice"], list)
        and len(g["dice"]) in (0, 2)
        and all(type(n) is int and 1 <= n <= 6 for n in g["dice"]),
        "Invalid dice",
    )
    discards = raw.get("discards", {})
    require(
        isinstance(discards, dict)
        and all(
            p in ids and type(n) is int and 0 < n <= sum(g["players"][p]["resources"].values())
            for p, n in discards.items()
        ),
        "Invalid discard obligations",
    )
    g["discards"] = discards.copy()
    require(
        bool(discards) == (g["phase"] == "discard"), "Discard phase needs outstanding obligations"
    )
    g["victims"] = []
    if g["phase"] == "steal":
        g["victims"] = sorted(
            {
                b["owner"]
                for v in g["board"]["tiles"][g["robber"]]["vertices"]
                if (b := g["board"]["vertices"][v]["building"])
                and b["owner"] != g["active"]
                and sum(g["players"][b["owner"]]["resources"].values())
            }
        )
        require(bool(g["victims"]), "No eligible victim")
    g["steal_target"] = raw.get("steal_target") if g["phase"] == "steal" else None
    require(
        g["steal_target"] is None or g["steal_target"] in g["victims"], "Invalid selected victim"
    )
    require(g["phase"] != "free_roads" or g["free_roads"] > 0, "No free roads remain")
    rigged = raw.get("rigged_dice", [])
    require(
        isinstance(rigged, list)
        and len(rigged) <= 1
        and all(
            isinstance(pair, list)
            and len(pair) == 2
            and all(type(n) is int and 1 <= n <= 6 for n in pair)
            for pair in rigged
        ),
        "Invalid dice queue",
    )
    g["rigged_dice"] = copy.deepcopy(rigged)
    offers = raw.get("offers", [])
    require(isinstance(offers, list) and len(offers) <= 20, "Invalid trade offers")
    require(
        not offers or g["phase"] == "action" and not g["secondary"],
        "Trade offers require the normal action phase",
    )
    for offer in offers:
        targets = offer.get("to") or []
        if isinstance(targets, str):
            targets = [targets]
        require(
            type(offer.get("id")) is int
            and offer["id"] > 0
            and offer.get("from") in ids
            and isinstance(targets, list)
            and all(isinstance(p, str) and p in ids and p != offer["from"] for p in targets),
            "Invalid trade offer identity",
        )
        give, want = resource_bundle(offer["give"]), resource_bundle(offer["want"])
        require(
            sum(give.values()) + sum(want.values()) > 0
            and (bool(targets) or sum(give.values()) > 0 and sum(want.values()) > 0)
            and not any(give[r] and want[r] for r in RESOURCES),
            "Invalid trade exchange",
        )
        restored_offer = {
            "id": offer["id"],
            "from": offer["from"],
            "to": sorted(set(targets)),
            "give": give,
            "want": want,
        }
        created = offer.get("created_at", time.time())
        closes = offer.get("closes_at", created + 5)
        require(
            all(type(v) in (float, int) and math.isfinite(v) and v >= 0 for v in (created, closes))
            and closes >= created + 5 - 0.000001,
            "Invalid trade window",
        )
        eligible = offer_recipients(g, restored_offer)
        responses = offer.get("responses", dict.fromkeys(eligible, "pending"))
        require(
            isinstance(responses, dict)
            and set(responses) == set(eligible)
            and all(v in ("pending", "accepted", "rejected") for v in responses.values()),
            "Invalid trade responses",
        )
        require(
            all(type(offer.get(k, False)) is bool for k in ("window_closed", "selecting")),
            "Invalid trade status",
        )
        restored_offer.update(
            created_at=created,
            closes_at=closes,
            responses=responses.copy(),
            window_closed=offer.get("window_closed", False),
            selecting=offer.get("selecting", False),
        )
        require(
            not restored_offer["selecting"]
            or (
                restored_offer["window_closed"]
                and sum(v == "accepted" for v in responses.values()) > 1
            ),
            "Invalid trade selection",
        )
        g["offers"].append(restored_offer)
    require(len({o["id"] for o in g["offers"]}) == len(g["offers"]), "Duplicate offer IDs")
    next_offer = raw.get("next_offer", 1)
    require(type(next_offer) is int and 1 <= next_offer <= 1000000000, "Invalid offer counter")
    g["next_offer"] = max(next_offer, max((o["id"] for o in g["offers"]), default=0) + 1)
    g["modified"] = bool(raw.get("modified", True))
    g["last_event"] = {"type": "restore", "text": "Scenario/save restored"}
    if g["phase"] == "finished":
        g["phase"] = "action"
    rescore(g)
    return g


def validate_event(event: dict, ids: list[str]) -> dict:
    require(
        isinstance(event, dict)
        and isinstance(event.get("type"), str)
        and isinstance(event.get("text"), str)
        and len(event["text"]) <= 1000,
        "Invalid saved game event",
    )
    clean = {
        k: event[k]
        for k in (
            "type",
            "text",
            "actor",
            "edge",
            "vertex",
            "tile",
            "victim",
            "resource",
            "proposer",
            "partner",
            "active",
            "phase",
            "from_tile",
            "card",
            "next_roller",
            "first_player",
            "response",
        )
        if k in event
    }
    require(
        all(isinstance(v, str) and len(v) <= 1000 for v in clean.values()), "Invalid event fields"
    )
    audience = event.get("audience", [])
    require(
        isinstance(audience, list) and len(audience) <= 6 and all(p in ids for p in audience),
        "Invalid event audience",
    )
    clean["audience"] = audience.copy()
    for k in ("at", "starts_at", "duration_ms", "turn", "schema", "offer", "card_count"):
        if k in event:
            value = event[k]
            require(
                type(value) in (int, float) and math.isfinite(value) and value >= 0,
                "Invalid event timing",
            )
            clean[k] = value
    require(clean.get("duration_ms", 0) <= 20000, "Invalid presentation duration")
    awards = event.get("awards", [])
    require(
        isinstance(awards, list)
        and len(awards) <= 2
        and all(
            isinstance(a, dict)
            and a.get("card") in ("longest_road", "largest_army")
            and a.get("from") in [None, *ids]
            and a.get("to") in [None, *ids]
            for a in awards
        ),
        "Invalid award event",
    )
    clean["awards"] = [{k: a.get(k) for k in ("card", "from", "to")} for a in awards]
    for k in ("give", "want"):
        if k in event:
            clean[k] = resource_bundle(event[k])
    if "dice" in event:
        require(
            isinstance(event["dice"], list)
            and len(event["dice"]) == 2
            and all(type(n) is int and 1 <= n <= 6 for n in event["dice"]),
            "Invalid event dice",
        )
        clean["dice"] = event["dice"].copy()
    effects = event.get("effects", [])
    require(isinstance(effects, list) and len(effects) <= 12, "Invalid event effects")
    clean["effects"] = []
    for effect in effects:
        require(
            isinstance(effect, dict)
            and effect.get("player") in ids
            and effect.get("kind") in ("resources", "development_cards"),
            "Invalid card effect",
        )
        item = {"kind": effect["kind"], "player": effect["player"]}
        for k in ("count", "gained", "spent"):
            if k in effect:
                require(type(effect[k]) is int and -120 <= effect[k] <= 120, "Invalid card count")
                item[k] = effect[k]
        if "resources" in effect:
            values = effect["resources"]
            require(
                isinstance(values, dict)
                and not set(values) - set(RESOURCES)
                and all(type(n) is int and -120 <= n <= 120 for n in values.values()),
                "Invalid resource effect",
            )
            item["resources"] = values.copy()
        clean["effects"].append(item)
    return clean


def encode_save(session: dict) -> bytes:
    raw = json.dumps(session, separators=(",", ":"), ensure_ascii=False).encode()
    require(len(raw) <= MAX_SAVE, "Save exceeds size limit")
    return b"CTN1" + hashlib.sha256(raw).digest() + zlib.compress(raw, 9)


def decode_save(data: bytes) -> dict:
    require(len(data) <= 2 * 1024 * 1024 and data[:4] == b"CTN1", "Not a supported .ctn file")
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(data[36:], MAX_SAVE + 1)
        require(
            len(raw) <= MAX_SAVE and decoder.eof and not decoder.unused_data,
            "Invalid/oversized compressed save",
        )
        require(hashlib.sha256(raw).digest() == data[4:36], "Save checksum does not match")
        result = json.loads(raw)
        require(isinstance(result, dict) and result.get("schema") == 1, "Unsupported save schema")
        return result
    except (zlib.error, UnicodeError, json.JSONDecodeError) as exc:
        raise RuleError("Corrupt save") from exc


def scenario_edit(g: dict, command: dict) -> dict:
    g = copy.deepcopy(g)
    kind = command.get("edit")
    if kind == "resources":
        require(command.get("player") in g["players"], "Unknown player")
        g["players"][command["player"]]["resources"] = resource_bundle(command["resources"])
    elif kind == "piece":
        pid = command.get("player") or None
        require(pid is None or pid in g["players"], "Unknown player")
        target = command.get("target")
        if target in g["board"]["vertices"]:
            level = command.get("level", 1)
            require(type(level) is int and level in (1, 2), "Building level must be 1 or 2")
            g["board"]["vertices"][target]["building"] = (
                {"owner": pid, "level": level} if pid else None
            )
        elif target in g["board"]["edges"]:
            g["board"]["edges"][target]["owner"] = pid
        else:
            raise RuleError("Select a vertex or edge")
    elif kind == "tile":
        tile = command.get("target")
        require(tile in g["board"]["tiles"], "Choose a hex")
        g["board"]["tiles"][tile].update(resource=command["resource"], number=command["number"])
    elif kind == "turn":
        require(command.get("player") in g["order"], "Unknown player")
        g.update(
            primary=g["order"].index(command["player"]),
            active=command["player"],
            secondary=False,
            phase="roll" if command.get("roll", False) else "action",
            turn=g["turn"] + 1,
            dev_played=False,
            discards={},
            victims=[],
            offers=[],
        )
    else:
        raise RuleError("Unknown scenario edit")
    g["modified"] = True
    return validate_game(g)
