"""Probe a local model with a realistic private game view, without touching a live table."""

import argparse
import asyncio
import json
import os
import time

from catan_server import botlogic, engine
from catan_server.session import bot_config


async def probe(config: dict, players: int = 6) -> dict:
    game = engine.quick_setup(
        engine.new_game([f"probe-seat-{i:05}" for i in range(players)], "probe")
    )
    actor = game["active"]
    game["phase"] = "action"
    for resource in engine.RESOURCES:
        engine.give(game, actor, {resource: min(2, game["bank"][resource])})
    actions = engine.legal_actions(game, actor)[:80]
    observation = {
        "me": actor,
        "game": engine.player_view(game, actor),
        "chat": [],
        "events": [],
        "players": [{"id": p, "name": p} for p in game["order"]],
    }
    started = time.monotonic()
    result = await botlogic.ask_model(config, observation, actions, [])
    choice = result.get("choice")
    command = result.get("command")
    if command is None:
        engine.require(
            type(choice) is int and 0 <= choice < len(actions), "Model choice is out of range"
        )
        command = actions[choice]
    engine.apply(game, actor, command)
    return {
        "ok": True,
        "seconds": round(time.monotonic() - started, 2),
        "command": command,
        "message": str(result.get("message", ""))[:500],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check model JSON decisions and warm response time"
    )
    parser.add_argument("--provider", choices=["ollama", "openai"], default="ollama")
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--timeout", type=float, default=8)
    parser.add_argument("--players", type=int, choices=[3, 4, 5, 6], default=6)
    args = parser.parse_args()
    try:
        config = bot_config({**vars(args), "api_key": os.getenv("CATAN_MODEL_API_KEY", "")})
        print(json.dumps(asyncio.run(probe(config, args.players)), indent=2))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "hint": "Start/warm the model, check its name and endpoint, and retry.",
                },
                indent=2,
            )
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
