"""One authoritative session, seat credentials, persistence, presence, and bot scheduling."""

import asyncio
import copy
import hashlib
import json
import logging
import math
import os
import secrets
import time
from pathlib import Path

from catan_server import botlogic, engine
from catan_server.scenarios import (
    decode_save,
    encode_save,
    scenario_edit,
    validate_event,
    validate_game,
)
from catan_server.storage import Store

COLORS = ["#f16a63", "#63aaff", "#f4cc67", "#a89aff", "#5dd6ac", "#f4a364"]
log = logging.getLogger("uvicorn.error")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def initial_session() -> dict:
    return {
        "schema": 1,
        "game_id": secrets.token_hex(8),
        "revision": 0,
        "players": [],
        "game": None,
        "chat": [],
        "events": [],
        "receipts": [],
        "paused": False,
        "pause_reason": "",
        "sandbox": None,
        "presentation_until": 0,
    }


def bot_config(value: dict) -> dict:
    provider = value.get("provider", "builtin")
    engine.require(provider in ("builtin", "ollama", "openai"), "Unknown bot provider")
    model = str(value.get("model", ""))[:128]
    endpoint = str(
        value.get("endpoint")
        or ("http://127.0.0.1:11434" if provider == "ollama" else "http://127.0.0.1:8080/v1")
    )[:512]
    engine.require(endpoint.startswith(("http://", "https://")), "Model endpoint must be HTTP(S)")
    engine.require(provider == "builtin" or bool(model), "Enter an installed model name")
    timeout = float(value.get("timeout", 8))
    engine.require(1 <= timeout <= 20, "Model timeout must be 1–20 seconds")
    return {
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "timeout": timeout,
        "api_key": str(value.get("api_key", ""))[:512],
    }


class Session:
    def __init__(self, directory: Path):
        self.store = Store(directory)
        self.state = self.store.load() or initial_session()
        if self.state["game"] and not self.state["game"]["winner"]:
            self.state.update(paused=True, pause_reason="disconnect")
        self.lock = asyncio.Lock()
        self.sockets = {}
        self.connected = {}
        self.bot_tasks = {}
        self.thinking = set()
        self.warming = set()
        self.bot_errors = {}
        self.bot_cooldowns = {}
        self.bot_chat_seen = {}
        self.bot_warmed = {}
        self.clock = time.time
        self.action_delay = max(0, min(5, float(os.getenv("CATAN_ACTION_DELAY", "0.8"))))
        self.bot_social_interval = max(1, float(os.getenv("CATAN_BOT_SOCIAL_INTERVAL", "10")))
        self.bot_social = {}
        self.bot_next_social = {}
        self.bot_offer_counts = {}
        self.bot_next_message = {}
        self.bot_waiting = {}
        if self.state["game"]:
            for offer in self.state["game"]["offers"]:
                offer["to"] = (
                    [offer["to"]] if isinstance(offer.get("to"), str) else offer.get("to") or []
                )
                offer.setdefault(
                    "responses",
                    dict.fromkeys(engine.offer_recipients(self.state["game"], offer), "pending"),
                )
                offer.setdefault("window_closed", False)
                offer.setdefault("selecting", False)
                if not offer["window_closed"]:
                    offer.update(created_at=self.clock(), closes_at=self.clock() + 5)
            if self.state["paused"]:
                self.state["trade_paused_at"] = self.clock()
        self.inference = asyncio.Semaphore(max(1, int(os.getenv("CATAN_LLM_CONCURRENCY", "1"))))
        self.scheduler = None

    async def start(self) -> None:
        log.info(
            "Host controls key: %s (also in %s/host-key.txt)",
            self.store.host_key,
            self.store.directory,
        )
        self.scheduler = asyncio.create_task(self._bots())

    async def close(self) -> None:
        tasks = [*self.bot_tasks.values()]
        if self.scheduler:
            tasks.append(self.scheduler)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.state["game"]:
            self.archive(self.state, "autosave.ctn")
        self.store.close()

    def archive(self, state: dict, name: str) -> None:
        self.store.checkpoint(name, encode_save(self.save_payload(state)))

    @staticmethod
    def save_payload(state: dict) -> dict:
        candidate = copy.deepcopy(state)
        candidate["sandbox"] = None
        candidate["receipts"] = []
        for p in candidate["players"]:
            p.get("config", {}).pop("api_key", None)
        return candidate

    def is_owner(self, key: str) -> bool:
        return bool(key) and secrets.compare_digest(self.store.host_key, key)

    def identify(self, token: str) -> str | None:
        if not token:
            return None
        hashed = digest(token)
        return next(
            (
                p["id"]
                for p in self.state["players"]
                if secrets.compare_digest(p["token_hash"], hashed)
            ),
            None,
        )

    def online(self, player: dict) -> bool:
        return bool(player["bot"] or self.connected.get(player["id"]))

    def paused(self) -> bool:
        return bool(
            self.state["paused"]
            or self.state["game"]
            and not self.state["game"]["winner"]
            and any(not self.online(p) for p in self.state["players"])
        )

    def view(self, pid: str | None, owner: bool = False) -> dict:
        s = self.state
        result = {
            "game_id": s.get("game_id", "legacy"),
            "revision": s["revision"],
            "me": pid,
            "owner": owner,
            "paused": self.paused(),
            "server_time": self.clock(),
            "presentation_until": s.get("presentation_until", 0),
            "pause_reason": s["pause_reason"],
            "players": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "color": p["color"],
                    "bot": p["bot"],
                    "ready": p["ready"],
                    "online": self.online(p),
                    "thinking": p["id"] in self.thinking,
                    "warming": p["id"] in self.warming,
                    "provider": p.get("config", {}).get("provider", "human"),
                }
                for p in s["players"]
            ],
            "chat": [m for m in s["chat"] if not m["to"] or pid in (m["from"], *m["to"])],
            "events": [],
            "game": engine.player_view(s["game"], pid, now=self.clock()) if s["game"] else None,
        }
        seat = next((p for p in s["players"] if p["id"] == pid), {})
        result["private_strategy"] = seat.get("strategy", "")
        for raw in s["events"][-60:]:
            event = engine.visible_event(raw, pid)
            if event is not None:
                result["events"].append(event)
        if owner:
            result.update(
                raw_game=copy.deepcopy(s["game"]),
                sandbox=copy.deepcopy(s["sandbox"]),
                sandbox_actions={
                    p: engine.legal_actions(s["sandbox"], p) for p in s["sandbox"]["order"]
                }
                if s["sandbox"]
                else {},
                bot_errors=self.bot_errors.copy(),
                sandbox_ratios={
                    p: engine.trade_ratios(s["sandbox"], p) for p in s["sandbox"]["order"]
                }
                if s["sandbox"]
                else {},
                bot_configs={
                    p["id"]: {k: v for k, v in p.get("config", {}).items() if k != "api_key"}
                    for p in s["players"]
                    if p["bot"]
                },
            )
        if owner and result["sandbox"]:
            for p, data in result["sandbox"]["players"].items():
                data.update(
                    points=engine.points(s["sandbox"], p, True),
                    pieces=engine.pieces(s["sandbox"], p),
                    resource_count=sum(data["resources"].values()),
                    dev_count=len(data["dev"]),
                )
        return result

    def commit(self, state: dict, actor: str, action: str) -> None:
        if state.get("paused") and not self.state.get("paused"):
            state["trade_paused_at"] = self.clock()
        elif not state.get("paused") and self.state.get("paused"):
            elapsed = max(0, self.clock() - self.state.get("trade_paused_at", self.clock()))
            for offer in (state.get("game") or {}).get("offers", []):
                if not offer.get("window_closed"):
                    offer["created_at"] += elapsed
                    offer["closes_at"] += elapsed
            state.pop("trade_paused_at", None)
        state["revision"] = self.state["revision"] + 1
        self.store.commit(state, actor, action)
        self.state = state

    def record_game_event(self, state: dict, before: dict) -> None:
        event = state["game"]["last_event"].copy()
        event.update(id=self.state["revision"] + 1, at=self.clock(), schema=1, effects=[])
        for pid, data in state["game"]["players"].items():
            delta = {
                r: data["resources"][r] - before["players"][pid]["resources"][r]
                for r in engine.RESOURCES
            }
            if any(delta.values()):
                event["effects"].append(
                    {
                        "kind": "resources",
                        "player": pid,
                        "resources": delta,
                        "count": sum(delta.values()),
                        "gained": sum(max(0, n) for n in delta.values()),
                        "spent": sum(max(0, -n) for n in delta.values()),
                    }
                )
            cards = len(data["dev"]) - len(before["players"][pid]["dev"])
            if cards:
                event["effects"].append(
                    {"kind": "development_cards", "player": pid, "count": cards}
                )
        paced = event["type"] in {
            "opening_roll",
            "inspect_steal",
            "roll",
            "build_road",
            "build_settlement",
            "build_city",
            "move_robber",
            "steal",
            "bank_trade",
            "trade_completed",
            "buy_dev",
            "play_dev",
            "discard",
            "end_turn",
        }
        event["awards"] = [
            {"card": card, "from": before[card], "to": state["game"][card]}
            for card in ("longest_road", "largest_army")
            if before[card] != state["game"][card]
        ]
        timings = {
            "roll": 2.4,
            "opening_roll": 2.4,
            "play_dev": 2.0,
            "inspect_steal": 0.7,
            "steal": 1.2,
            "build_road": 1.2,
            "build_settlement": 1.2,
            "build_city": 1.2,
            "buy_dev": 1.1,
            "trade_completed": 1.2,
            "bank_trade": 1.2,
        }
        duration = (
            (
                max(self.action_delay, timings.get(event["type"], self.action_delay))
                + len(event["awards"]) * 1.8
            )
            if paced and self.action_delay
            else 0
        )
        event["duration_ms"] = round(duration * 1000)
        event.update(
            turn=state["game"]["turn"], phase=state["game"]["phase"], active=state["game"]["active"]
        )
        event["next_roller"] = engine.next_roller(state["game"])
        if event["type"] in ("roll", "opening_roll"):
            event["dice"] = state["game"]["dice"].copy()
        if event["type"] == "move_robber":
            event["from_tile"] = before["robber"]
        if event["type"] in ("steal", "inspect_steal"):
            event["card_count"] = sum(before["players"][event["victim"]]["resources"].values())
        event["starts_at"] = max(self.clock(), state.get("presentation_until", 0))
        if duration:
            state["presentation_until"] = event["starts_at"] + duration
        state["game"]["last_event"] = event
        state["events"] = (state["events"] + [event])[-100:]

    async def settle_trades(self) -> None:
        changed = False
        async with self.lock:
            for key in ("game", "sandbox"):
                game = self.state[key]
                if not game or game["winner"] or key == "game" and self.paused():
                    continue
                for offer_id in [o["id"] for o in game["offers"]]:
                    game = self.state[key]
                    offer = next((o for o in game["offers"] if o["id"] == offer_id), None)
                    if not offer:
                        continue
                    due = not offer["window_closed"] and self.clock() >= offer["closes_at"]
                    invalid = not engine.can_pay(game, offer["from"], offer["give"]) or any(
                        answer == "accepted" and not engine.can_pay(game, p, offer["want"])
                        for p, answer in offer["responses"].items()
                    )
                    if not due and not invalid:
                        continue
                    candidate = copy.deepcopy(self.state)
                    candidate[key] = engine.apply(
                        game,
                        offer["from"],
                        {"type": "settle_trade", "offer": offer_id},
                        now=self.clock(),
                    )
                    if key == "game":
                        self.record_game_event(candidate, game)
                    self.commit(candidate, offer["from"], f"{key}_settle_trade")
                    changed = True
        if changed:
            await self.broadcast()

    async def broadcast(self) -> None:
        cached = {}

        async def send(ws, credentials):
            if credentials not in cached:
                cached[credentials] = {"type": "state", "state": self.view(*credentials)}
            try:
                await asyncio.wait_for(ws.send_json(cached[credentials]), 2)
            except (Exception, asyncio.CancelledError):
                # The receive loop owns removal/pause handling.
                try:
                    await ws.close()
                except Exception:
                    pass

        await asyncio.gather(*(send(ws, pair) for ws, pair in list(self.sockets.items())))

    async def connect(self, ws, pid: str | None, owner: bool) -> None:
        async with self.lock:
            engine.require(len(self.sockets) < 24, "Too many browser connections")
            self.sockets[ws] = (pid, owner)
            if pid:
                self.connected.setdefault(pid, set()).add(ws)
            if self.state["pause_reason"] == "disconnect" and all(
                self.online(p) for p in self.state["players"]
            ):
                candidate = copy.deepcopy(self.state)
                candidate.update(paused=False, pause_reason="")
                self.commit(candidate, pid or "host", "reconnected")
        await self.broadcast()

    async def disconnect(self, ws) -> None:
        async with self.lock:
            pair = self.sockets.pop(ws, None)
            if pair:
                pid = pair[0]
                if pid in self.connected:
                    self.connected[pid].discard(ws)
                if (
                    pid
                    and not self.connected.get(pid)
                    and self.state["game"]
                    and not self.state["game"]["winner"]
                ):
                    candidate = copy.deepcopy(self.state)
                    candidate.update(paused=True, pause_reason="disconnect")
                    self.archive(candidate, "autosave.ctn")
                    self.commit(candidate, pid, "disconnect_autosave")
        await self.broadcast()

    async def join(self, name: str, token: str, invite: str) -> dict:
        async with self.lock:
            if token and (pid := self.identify(token)):
                return {"token": token, "player": pid}
            code = os.getenv("CATAN_INVITE_CODE", "")
            engine.require(
                not code or secrets.compare_digest(code, invite), "Incorrect invite code"
            )
            engine.require(
                self.state["game"] is None,
                "Game started. Reconnect with your seat token or ask the host for recovery.",
            )
            engine.require(len(self.state["players"]) < 6, "All six seats are occupied")
            name = name.strip()
            engine.require(1 <= len(name) <= 32, "Choose a name of 1–32 characters")
            engine.require(
                not any(p["name"].casefold() == name.casefold() for p in self.state["players"]),
                "That name is already in use",
            )
            token = secrets.token_urlsafe(32)
            pid = secrets.token_hex(8)
            candidate = copy.deepcopy(self.state)
            used = {p["color"] for p in candidate["players"]}
            candidate["players"].append(
                {
                    "id": pid,
                    "name": name,
                    "color": next(c for c in COLORS if c not in used),
                    "token_hash": digest(token),
                    "bot": False,
                    "ready": False,
                    "memory": [],
                }
            )
            self.commit(candidate, pid, "join")
        await self.broadcast()
        return {"token": token, "player": pid}

    def _message(self, state: dict, pid: str, c: dict) -> None:
        text = c.get("text", "").strip()
        recipients = c.get("to", [])
        engine.require(
            isinstance(text, str) and 0 < len(text) <= 1200, "Messages must be 1–1200 characters"
        )
        ids = {p["id"] for p in state["players"]}
        engine.require(
            isinstance(recipients, list) and all(p in ids for p in recipients),
            "Unknown message recipient",
        )
        state["chat"].append(
            {
                "id": secrets.token_hex(6),
                "from": pid,
                "to": sorted(set(recipients)),
                "text": text,
                "at": self.clock(),
            }
        )
        state["chat"] = state["chat"][-400:]

    async def command(
        self, pid: str, c: dict, command_id: str, revision: int | None = None
    ) -> dict:
        async with self.lock:
            engine.require(pid in {p["id"] for p in self.state["players"]}, "Unknown player")
            engine.require(
                isinstance(command_id, str) and 1 <= len(command_id) <= 100, "Command ID required"
            )
            signature = digest(json.dumps(c, sort_keys=True))
            receipt = next(
                (r for r in self.state["receipts"] if r["id"] == command_id and r["actor"] == pid),
                None,
            )
            if receipt:
                engine.require(
                    receipt["signature"] == signature, "Command ID was reused with another action"
                )
                return {"revision": self.state["revision"], "duplicate": True}
            if revision is not None and revision != self.state["revision"]:
                raise engine.RuleError("State changed; wait for the latest update and try again")
            candidate = copy.deepcopy(self.state)
            kind = c.get("type")
            if kind == "chat":
                self._message(candidate, pid, c)
            elif kind == "ready":
                engine.require(candidate["game"] is None, "A game is already running")
                next(p for p in candidate["players"] if p["id"] == pid)["ready"] = bool(
                    c.get("ready")
                )
            elif kind == "save":
                pass  # Every commit is an atomic durable checkpoint.
            elif kind == "end_session":
                engine.require(self.paused(), "Pause the session before voting to end it")
                p = next(p for p in candidate["players"] if p["id"] == pid)
                engine.require(not p["bot"], "Bots do not vote")
                p["end_vote"] = True
                if all(
                    p.get("end_vote")
                    for p in candidate["players"]
                    if not p["bot"] and self.online(p)
                ):
                    self.archive(self.state, "last-game.ctn")
                    candidate.update(game=None, paused=False, pause_reason="")
                    for p in candidate["players"]:
                        p.update(ready=p["bot"], end_vote=False)
            else:
                engine.require(candidate["game"] is not None, "Start a game first")
                engine.require(
                    not self.paused(),
                    "Game paused. All human seats must reconnect, or the host may end the session.",
                )
                engine.require(kind != "settle_trade", "Trade settlement is managed by the server")
                if kind not in (
                    "accept_trade",
                    "reject_trade",
                    "select_trade",
                    "cancel_trade",
                    "offer_trade",
                ):
                    engine.require(
                        self.clock() >= candidate.get("presentation_until", 0),
                        "Previous action is still being presented; try again shortly",
                    )
                before = candidate["game"]
                candidate["game"] = engine.apply(before, pid, c, now=self.clock())
                self.record_game_event(candidate, before)
            candidate["receipts"] = (
                candidate["receipts"] + [{"id": command_id, "actor": pid, "signature": signature}]
            )[-300:]
            self.commit(candidate, pid, str(kind))
        await self.broadcast()
        return {"revision": self.state["revision"]}

    async def host(self, c: dict) -> dict:
        response = {}
        async with self.lock:
            candidate = copy.deepcopy(self.state)
            kind = c.get("type")
            ids = [p["id"] for p in candidate["players"]]
            if kind == "add_bot":
                engine.require(
                    candidate["game"] is None and len(ids) < 6,
                    "Add bots to an open lobby with a free seat",
                )
                name = str(c.get("name", "Bot")).strip()[:32]
                engine.require(
                    name
                    and not any(
                        p["name"].casefold() == name.casefold() for p in candidate["players"]
                    ),
                    "Choose a unique bot name",
                )
                used = {p["color"] for p in candidate["players"]}
                candidate["players"].append(
                    {
                        "id": secrets.token_hex(8),
                        "name": name,
                        "color": next(color for color in COLORS if color not in used),
                        "bot": True,
                        "ready": True,
                        "token_hash": digest(secrets.token_urlsafe(32)),
                        "config": bot_config(c),
                        "memory": [],
                    }
                )
            elif kind == "configure_bot":
                p = next(
                    (p for p in candidate["players"] if p["id"] == c.get("player") and p["bot"]),
                    None,
                )
                engine.require(p is not None, "Unknown bot")
                p["config"] = bot_config(c)
            elif kind == "remove_player":
                engine.require(candidate["game"] is None, "End the game before removing a seat")
                candidate["players"] = [
                    p for p in candidate["players"] if p["id"] != c.get("player")
                ]
            elif kind == "recover_seat":
                p = next(
                    (
                        p
                        for p in candidate["players"]
                        if p["id"] == c.get("player") and not p["bot"]
                    ),
                    None,
                )
                engine.require(p is not None, "Unknown human seat")
                token = secrets.token_urlsafe(32)
                p["token_hash"] = digest(token)
                response["token"] = token
                response["player"] = p["id"]
            elif kind in ("start", "test_create"):
                engine.require(
                    kind == "test_create" or candidate["game"] is None, "End the current game first"
                )
                engine.require(
                    3 <= len(ids) <= 6, "Add 3–6 players first (built-in bots can fill seats)"
                )
                if kind == "start":
                    engine.require(
                        all(p["ready"] and self.online(p) for p in candidate["players"]),
                        "All players must be connected and ready",
                    )
                seed = str(c.get("seed") or secrets.token_hex(4))[:128]
                game = engine.new_game(ids, seed, c.get("target", 10))
                if c.get("quick") or kind == "test_create":
                    game = engine.quick_setup(game)
                if c.get("scenario") or kind == "test_create":
                    for pid in ids:
                        for r in engine.RESOURCES:
                            amount = min(2, game["bank"][r])
                            engine.give(game, pid, {r: amount})
                    game["modified"] = True
                if kind == "test_create":
                    human = next(
                        (p["id"] for p in candidate["players"] if not p["bot"]), game["active"]
                    )
                    game.update(active=human, primary=game["order"].index(human), phase="roll")
                    fixture = c.get("fixture", "development")
                    if fixture == "development":
                        for card in engine.DEVELOPMENTS[:-1]:
                            game["deck"].remove(card)
                            game["players"][human]["dev"].append({"type": card, "bought": -1})
                    elif fixture == "robber":
                        game["rigged_dice"] = [[3, 4]]
                    elif fixture == "late_game":
                        for v in game["board"]["vertices"].values():
                            if v["building"] and v["building"]["owner"] == human:
                                v["building"]["level"] = 2
                        for _ in range(5):
                            game["deck"].remove("victory_point")
                            game["players"][human]["dev"].append(
                                {"type": "victory_point", "bought": -1}
                            )
                    game = validate_game(game)
                candidate["sandbox" if kind == "test_create" else "game"] = game
                if kind == "start":
                    candidate.update(
                        paused=False, pause_reason="", events=[], game_id=secrets.token_hex(8)
                    )
            elif kind in ("load_scenario", "test_load"):
                game = validate_game(c["game"], ids)
                game["modified"] = True
                if kind == "load_scenario":
                    engine.require(
                        candidate["game"] is None, "End the current game before loading a scenario"
                    )
                    candidate.update(game=game, paused=False, pause_reason="", events=[])
                else:
                    candidate["sandbox"] = game
            elif kind == "publish_test":
                engine.require(
                    candidate["game"] is None and candidate["sandbox"] is not None,
                    "End the current game and create a scenario first",
                )
                candidate.update(
                    game=validate_game(candidate["sandbox"], ids),
                    paused=False,
                    pause_reason="",
                    events=[],
                )
                candidate["game"]["modified"] = True
            elif kind in ("edit", "test_edit"):
                key = "sandbox" if kind == "test_edit" else "game"
                engine.require(candidate[key] is not None, "Create a game/scenario first")
                engine.require(
                    key == "sandbox" or self.paused(), "Pause the live game before editing"
                )
                candidate[key] = scenario_edit(candidate[key], c)
            elif kind in ("replace_game", "test_replace"):
                key = "sandbox" if kind == "test_replace" else "game"
                engine.require(
                    key == "sandbox" or self.paused(), "Pause before replacing live state"
                )
                candidate[key] = validate_game(c["game"], ids)
                candidate[key]["modified"] = True
            elif kind in ("test_command", "admin_command"):
                key = "sandbox" if kind == "test_command" else "game"
                engine.require(candidate[key] is not None, "Create a game/scenario first")
                before = candidate[key]
                candidate[key] = engine.apply(
                    before, c.get("player"), c["command"], now=self.clock()
                )
                candidate[key]["modified"] = True
                if key == "game":
                    self.record_game_event(candidate, before)
            elif kind == "rig_dice":
                engine.require(candidate["game"] is not None, "Start a game first")
                dice = c.get("dice")
                engine.require(
                    isinstance(dice, list)
                    and len(dice) == 2
                    and all(type(n) is int and 1 <= n <= 6 for n in dice),
                    "Enter two dice values, each 1–6",
                )
                candidate["game"]["rigged_dice"] = [dice]
                candidate["game"]["modified"] = True
            elif kind == "event":
                text = str(c.get("text", ""))[:500]
                candidate["events"] = (
                    candidate["events"]
                    + [
                        {
                            "id": self.state["revision"] + 1,
                            "type": "custom",
                            "text": text,
                            "actor": "host",
                        }
                    ]
                )[-100:]
            elif kind == "pause":
                candidate.update(paused=True, pause_reason="host")
            elif kind == "resume":
                engine.require(
                    all(self.online(p) for p in candidate["players"]),
                    "All human seats must reconnect",
                )
                candidate.update(paused=False, pause_reason="")
            elif kind == "end_game":
                if self.state["game"]:
                    self.archive(self.state, "last-game.ctn")
                candidate.update(
                    game=None,
                    paused=False,
                    pause_reason="",
                    events=[],
                    game_id=secrets.token_hex(8),
                )
                for p in candidate["players"]:
                    p.update(ready=p["bot"], end_vote=False)
            else:
                raise engine.RuleError("Unknown host action")
            self.commit(candidate, "host", str(kind))
        if kind == "recover_seat":
            for ws, pair in list(self.sockets.items()):
                if pair[0] == c.get("player"):
                    await ws.close(code=1012)
        if kind == "configure_bot":
            task = self.bot_tasks.get(c.get("player"))
            if task and not task.done():
                task.cancel()
            self.bot_warmed.pop(c.get("player"), None)
            self.bot_cooldowns.pop(c.get("player"), None)
            self.bot_errors.pop(c.get("player"), None)
        if kind in ("end_game", "remove_player"):
            for pid, task in self.bot_tasks.items():
                if kind == "end_game" or pid == c.get("player"):
                    task.cancel()
                    self.bot_warmed.pop(pid, None)
        await self.broadcast()
        return response

    async def export(self) -> bytes:
        async with self.lock:
            if not self.state["game"]:
                path = self.store.directory / "last-game.ctn"
                engine.require(path.is_file(), "There is no current or archived game to export")
                return path.read_bytes()
            return encode_save(self.save_payload(self.state))

    async def restore(self, data: bytes) -> None:
        candidate = decode_save(data)
        players = candidate.get("players")
        engine.require(isinstance(players, list) and 3 <= len(players) <= 6, "Save needs 3–6 seats")
        ids = []
        for p in players:
            engine.require(
                isinstance(p, dict) and isinstance(p.get("id"), str) and len(p["id"]) <= 64,
                "Invalid seat",
            )
            engine.require(
                isinstance(p.get("name"), str) and 1 <= len(p["name"]) <= 32, "Invalid name"
            )
            engine.require(
                p.get("color") in COLORS
                and isinstance(p.get("token_hash"), str)
                and len(p["token_hash"]) == 64
                and type(p.get("bot")) is bool,
                "Invalid seat credentials",
            )
            if p["bot"]:
                p["config"] = bot_config(p.get("config", {}))
            p["ready"] = True
            p["memory"] = p.get("memory", [])[-12:]
            p["strategy"] = str(p.get("strategy", ""))[:2400]
            ids.append(p["id"])
        engine.require(len(set(ids)) == len(ids), "Duplicate seats")
        game = validate_game(candidate["game"]) if candidate.get("game") else None
        if game:
            for offer in game["offers"]:
                if not offer["window_closed"]:
                    offer.update(created_at=self.clock(), closes_at=self.clock() + 5)
        engine.require(
            game is not None and set(game["players"]) == set(ids), "Seat list and game disagree"
        )
        restored = initial_session()
        restored.update(players=players, game=game, paused=True, pause_reason="disconnect")
        restored["trade_paused_at"] = self.clock()
        for event in candidate.get("events", [])[-100:]:
            restored["events"].append(validate_event(event, ids))
        # Validate chat independently; never accept arbitrary executable serialized objects.
        for m in candidate.get("chat", [])[-400:]:
            timestamp = m.get("at", 0)
            engine.require(
                type(timestamp) in (int, float) and math.isfinite(timestamp) and timestamp >= 0,
                "Invalid message timestamp",
            )
            engine.require(
                m.get("from") in ids
                and isinstance(m.get("to"), list)
                and all(p in ids for p in m["to"])
                and isinstance(m.get("text"), str)
                and len(m["text"]) <= 1200,
                "Invalid saved chat",
            )
            restored["chat"].append(
                {
                    "id": str(m.get("id", ""))[:64],
                    "from": m["from"],
                    "to": m["to"],
                    "text": m["text"],
                    "at": float(timestamp),
                }
            )
        async with self.lock:
            engine.require(self.state["game"] is None, "End the current game before importing")
            for index, event in enumerate(restored["events"]):
                event["id"] = self.state["revision"] - len(restored["events"]) + index + 1
            self.commit(restored, "host", "import")
        # Old sockets must authenticate against the imported seat hashes again.
        for ws in list(self.sockets):
            await ws.close(code=1012)
        await self.broadcast()

    def bot_actions(self, pid: str) -> list[dict]:
        game = self.state["game"]
        actions = engine.legal_actions(game, pid, now=self.clock())
        if not game:
            return []
        key = (self.state["game_id"], game["turn"], pid)
        result = []
        for action in actions:
            kind = action["type"]
            if kind == "offer_trade" and self.bot_offer_counts.get(key, 0) >= 2:
                continue
            if kind == "cancel_trade":
                offer = next(o for o in game["offers"] if o["id"] == action["offer"])
                if self.clock() < offer["created_at"] + 30:
                    continue
            if self.clock() < self.state.get("presentation_until", 0) and kind not in (
                "accept_trade",
                "reject_trade",
                "select_trade",
                "offer_trade",
                "cancel_trade",
            ):
                continue
            result.append(action)
        return result

    async def _bots(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            await self.settle_trades()
            game = self.state["game"]
            if game and game["winner"]:
                continue
            if game:
                self.bot_offer_counts = {
                    k: v
                    for k, v in self.bot_offer_counts.items()
                    if k[:2] == (self.state["game_id"], game["turn"])
                }
            for p in self.state["players"]:
                pid = p["id"]
                if not p["bot"] or pid in self.bot_tasks and not self.bot_tasks[pid].done():
                    continue
                actions = [] if self.paused() else self.bot_actions(pid)
                view = self.view(pid)
                incoming = [m for m in view["chat"] if m["from"] != pid]
                latest = incoming[-1]["id"] if incoming else ""
                last_event = view["events"][-1]["id"] if view["events"] else 0
                waiting_event, waiting_chat, retry_at = self.bot_waiting.get(pid, (None, None, 0))
                if (last_event, latest) == (
                    waiting_event,
                    waiting_chat,
                ) and self.clock() < retry_at:
                    actions = []
                humans = {s["id"] for s in self.state["players"] if not s["bot"]}
                human_message = next(
                    (m["id"] for m in reversed(incoming) if m["from"] in humans), ""
                )
                event = next(
                    (e["id"] for e in reversed(view["events"]) if e.get("actor") != pid), 0
                )
                signal = (self.state["game_id"], event, human_message)
                old_signal, count = self.bot_social.get(pid, (None, 0))
                if signal != old_signal:
                    count = 0
                direct = bool(
                    incoming
                    and (
                        pid in incoming[-1]["to"]
                        or f"@{p['name']}".casefold() in incoming[-1]["text"].casefold()
                    )
                )
                changed = signal != old_signal or latest != self.bot_chat_seen.get(pid, "")
                social = (
                    p["config"]["provider"] != "builtin"
                    and changed
                    and count < 2
                    and (
                        self.clock() >= self.bot_next_social.get(pid, 0)
                        or direct
                        and incoming[-1]["from"] in humans
                    )
                )
                # Dummies only answer humans; they must not create automatic bot reply loops.
                if p["config"]["provider"] == "builtin":
                    social = (
                        direct
                        and bool(latest)
                        and incoming[-1]["from"] in humans
                        and latest != self.bot_chat_seen.get(pid)
                    )
                if actions or social:
                    if social:
                        self.bot_social[pid] = (signal, count + 1)
                        self.bot_next_social[pid] = self.clock() + self.bot_social_interval
                    self.bot_tasks[pid] = asyncio.create_task(
                        self._bot_step(pid, actions, social, latest)
                    )

    async def _bot_step(self, pid: str, actions: list[dict], chat_due: bool, latest: str) -> None:
        p = next((p for p in self.state["players"] if p["id"] == pid), None)
        if not p:
            return
        config = p["config"]
        generation = self.state["game_id"]
        decision, action = {}, None

        def fallback(options):
            if not options or not self.state["game"]:
                return None
            g = copy.deepcopy(self.state["game"])
            g["view_ratios"] = engine.trade_ratios(g, pid)
            return botlogic.choose_builtin(g, pid, options)

        action = fallback(actions)
        # An unaffordable offer can be rejected immediately without inference.
        only_reject = any(
            a["type"] == "reject_trade"
            and not any(b["type"] == "accept_trade" and b["offer"] == a["offer"] for b in actions)
            for a in actions
        )
        if only_reject:
            action = next(
                a
                for a in actions
                if a["type"] == "reject_trade"
                and not any(
                    b["type"] == "accept_trade" and b["offer"] == a["offer"] for b in actions
                )
            )
        use_model = (
            config["provider"] != "builtin" and (bool(actions) or chat_due) and not only_reject
        )
        use_model = use_model and time.monotonic() >= self.bot_cooldowns.get(pid, 0)
        if use_model:
            self.thinking.add(pid)
            await self.broadcast()
            try:
                async with self.inference:
                    if generation != self.state["game_id"] or self.paused() and not chat_due:
                        return
                    ranked = self.rank_actions(actions, action)
                    warm_key = (generation, tuple(sorted(config.items())))
                    if self.bot_warmed.get(pid) != warm_key:
                        self.warming.add(pid)
                        await self.broadcast()
                        try:
                            await botlogic.ask_model(
                                config,
                                self.view(pid),
                                ranked,
                                p.get("memory", []),
                                not actions,
                                warmup=True,
                            )
                        finally:
                            self.warming.discard(pid)
                        self.bot_warmed[pid] = warm_key
                        await self.broadcast()
                    if generation != self.state["game_id"] or self.paused() and not chat_due:
                        return
                    actions = [] if self.paused() else self.bot_actions(pid)
                    action = fallback(actions)
                    if not actions and not chat_due:
                        return
                    ranked = self.rank_actions(actions, action)
                    observation = self.view(pid)
                    incoming = [m for m in observation["chat"] if m["from"] != pid]
                    latest = incoming[-1]["id"] if incoming else ""
                    decision = await botlogic.ask_model(
                        config, observation, ranked, p.get("memory", []), not actions
                    )
                choice = decision.get("choice")
                action = (
                    ranked[choice] if type(choice) is int and 0 <= choice < len(ranked) else None
                )
                proposed = decision.get("command")
                if isinstance(proposed, dict) and proposed.get("type") in {
                    "offer_trade",
                    "accept_trade",
                    "reject_trade",
                    "select_trade",
                    "cancel_trade",
                    "roll",
                    "opening_roll",
                    "inspect_steal",
                    "end_turn",
                    "build_road",
                    "build_settlement",
                    "build_city",
                    "buy_dev",
                    "play_dev",
                    "move_robber",
                    "steal",
                    "discard",
                    "bank_trade",
                }:
                    action = proposed
                self.bot_errors.pop(pid, None)
            except Exception as exc:
                self.bot_warmed.pop(pid, None)
                self.bot_cooldowns[pid] = time.monotonic() + 30
                self.bot_errors[pid] = (
                    f"{type(exc).__name__}: {str(exc)[:160]} — using fast fallback"
                )
                action = fallback(self.bot_actions(pid))
            finally:
                self.thinking.discard(pid)
        if latest:
            self.bot_chat_seen[pid] = latest
        try:
            if generation != self.state["game_id"] or not any(
                x["id"] == pid for x in self.state["players"]
            ):
                return
            messages = decision.get("messages", [])
            if not isinstance(messages, list):
                messages = []
            if decision.get("message") and not messages:
                recipients = decision.get("recipients")
                incoming = next((m for m in self.state["chat"] if m["id"] == latest), None)
                if not recipients:
                    recipients = (
                        list({incoming["from"], *incoming["to"]} - {pid})
                        if incoming and incoming["to"]
                        else []
                    )
                messages = [{"text": decision["message"], "to": recipients}]
            allowed = {s["id"] for s in self.state["players"]}
            sent = []
            if self.clock() >= self.bot_next_message.get(pid, 0):
                for message in messages[:3]:
                    if not isinstance(message, dict):
                        continue
                    recipients, text = message.get("to", []), message.get("text", "")
                    # Invalid private recipients must never silently turn into a public message.
                    if (
                        not isinstance(text, str)
                        or not text.strip()
                        or not isinstance(recipients, list)
                        or not all(isinstance(r, str) and r in allowed for r in recipients)
                    ):
                        continue
                    await self.command(
                        pid,
                        {"type": "chat", "text": text[:1200], "to": recipients},
                        secrets.token_hex(12),
                    )
                    sent.append({"text": text[:1200], "to": recipients})
                if sent:
                    self.bot_next_message[pid] = self.clock() + 4
            if not sent and chat_due and config["provider"] == "builtin":
                incoming = next((m for m in self.state["chat"] if m["id"] == latest), None)
                await self.command(
                    pid,
                    {
                        "type": "chat",
                        "text": "Send me a trade offer and I'll consider it.",
                        "to": list({incoming["from"], *incoming["to"]} - {pid})
                        if incoming and incoming["to"]
                        else [],
                    },
                    secrets.token_hex(12),
                )
            action_result = "waited"
            if action and not self.paused() and generation == self.state["game_id"]:
                if action["type"] == "offer_trade":
                    current = self.state["game"]
                    key = (generation, current["turn"], pid)
                    if self.bot_offer_counts.get(key, 0) >= 2:
                        action = None
                if action:
                    try:
                        await self.command(pid, action, secrets.token_hex(12))
                        action_result = "accepted"
                        if action["type"] == "offer_trade":
                            self.bot_offer_counts[key] = self.bot_offer_counts.get(key, 0) + 1
                    except engine.RuleError as exc:
                        action_result = f"Rejected: {exc}"
                        fresh = self.bot_actions(pid)
                        replacement = fallback(fresh)
                        if replacement and replacement["type"] != "offer_trade":
                            await self.command(pid, replacement, secrets.token_hex(12))
                            action = replacement
                            action_result += "; used a legal fallback"
            if use_model and action is None:
                view = self.view(pid)
                event_id = view["events"][-1]["id"] if view["events"] else 0
                self.bot_waiting[pid] = (event_id, latest, self.clock() + self.bot_social_interval)
            if use_model:
                async with self.lock:
                    if generation != self.state["game_id"]:
                        return
                    candidate = copy.deepcopy(self.state)
                    seat = next((s for s in candidate["players"] if s["id"] == pid), None)
                    if seat:
                        memory = decision.get("memory")
                        if isinstance(memory, str) and memory.strip():
                            seat["strategy"] = memory[:2400]
                        seat["memory"] = (
                            seat.get("memory", [])
                            + [
                                {
                                    "result": action_result,
                                    "action": action,
                                    "messages": sent,
                                    "plan": seat.get("strategy", "")[:1200],
                                }
                            ]
                        )[-12:]
                        self.commit(candidate, pid, "bot_context")
        except (engine.RuleError, TypeError, ValueError, KeyError):
            pass  # A concurrent action may have invalidated this decision.
        finally:
            await self.broadcast()

    @staticmethod
    def rank_actions(actions: list[dict], preferred: dict | None) -> list[dict]:
        # Always retain offer responses and partner choices before trimming large build menus.
        priorities = {
            "reject_trade": 0,
            "accept_trade": 0,
            "select_trade": 0,
            "cancel_trade": 1,
            "offer_trade": 2,
        }
        return sorted(actions, key=lambda a: (priorities.get(a["type"], 3), a != preferred))[:100]
