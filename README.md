# Catan Ultimate

A self-hosted, single-lobby Catan game for **3–6 humans and/or bots**. Python owns the
rules and saves; each browser draws the board and animations locally. No frame streaming.

## Play now

Dependencies are already installed in this checkout. From the project folder:

```fish
npm run build
.venv/bin/python -m catan_server
```

Open **http://localhost:8000**. The server prints a **host controls key**, also saved
in [data/host-key.txt](./data/host-key.txt). Keep that key private.

1. Enter your name and **Join / recover seat**.
2. Click **Host key** and paste the printed key to unlock game setup.
3. Have friends join the same address, or use **Add a bot** to fill seats. Minimum
   three seats; maximum six. Built-in bots need no model installation.
4. Each human clicks **Ready to play**. Choose normal placements, quick placements,
   or a scenario start, then **Start game**.
5. Select highlighted intersections/edges to build. Roll, trade, use cards, and end
   turns with the buttons below the board. Drag to pan, scroll/pinch to zoom, or **Fit**.

**Download your Seat key** once joined. It restores your identity after changing
browsers, addresses, or hosts. Clearing browser storage loses the stored copy.

Optional port/data folder:

```fish
.venv/bin/python -m catan_server --port 8000 --data-dir ./data
```

Activation is optional; these commands explicitly use the environment. In fish:

```fish
source .venv/bin/activate.fish
```

## Friends connecting over the Internet

The host's `localhost` address only works on the host's machine. Everyone needs a URL
that reaches that computer. There is one lobby, so there is no room-selection service.

### Quick HTTPS URL without port forwarding

Install [cloudflared](https://developers.cloudflare.com/tunnel/downloads/), keep the
Python server running, and run this in a **second terminal**:

```fish
cloudflared tunnel --url http://localhost:8000
```

Share the printed `https://….trycloudflare.com` address. This is a temporary tunnel;
keep both terminals running. Its hostname changes after restarting the tunnel, so use
seat recovery keys when changing addresses. For repeat sessions, a named tunnel gives
a stable address. See [Cloudflare's Quick Tunnel instructions](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

Alternatively, use the host's LAN IP on the same network, or forward the chosen TCP
port on the router and place an HTTPS reverse proxy in front of it. Port forwarding
requires a reachable public IP; a tunnel is useful when the ISP uses CGNAT. The browser
chooses `wss://` automatically on HTTPS. Share the built Python server, not Vite's dev server.
The app does not configure your router or start a tunnel automatically.

Optional admission code, using fish:

```fish
set -lx CATAN_INVITE_CODE "your-shared-code"
.venv/bin/python -m catan_server
```

An admission code does not replace the URL or a player's private seat credential.

## What's playable

- Snake-order initial settlements/roads and resources from the second settlement.
- Production, finite resource bank, seven/discards, robber blocking, victim selection,
  and uniformly random theft.
- Roads, settlements, city upgrades, distance/network rules, and piece limits.
- Player offers/counteroffers, bank trading, generic ports, and resource-specific ports.
- All five development-card types, purchase timing, one card per turn, and pre-roll play.
- Longest Road (including branches, loops, interruptions, and ties), Largest Army,
  hidden victory points, and automatic wins during the appropriate player's turn.
- Public chat, multi-recipient private messages, reconnect credentials, pause/recovery,
  portable saves, scenario editing, and integrated bot seats.

### The actual 5–6 player extension

Five or six seats automatically select the expanded island and components:

| Component | 3–4 seats | 5–6 seats |
| --- | ---: | ---: |
| Land hexes | 19 | **30** |
| Hex row lengths | 3–4–5–4–3 | **3–4–5–6–5–4–3** |
| Deserts | 1 | 2 |
| Number tokens | 18 | 28 |
| Ports | 9 | 11 |
| Cards of each resource | 19 | 24 |
| Development cards | 25 | 34 |

The expanded terrain distribution is six forests/pastures/fields, five hills/mountains,
and two deserts. The extra ports are generic and wool. Each seat still has 15 roads,
five settlements, and four cities.

This implements the **2025 paired-player turn sequence**: player 1 rolls and takes a
normal action phase; the third seat clockwise then gets player 2's action phase, with
bank trading but no player trading. Both may build and play an eligible development
card, and either may win in their own phase. Markers then advance one seat.

Reference: [official 2025 5–6 rulebook](https://www.catan.com/sites/default/files/2025-03/CN3082%20CATAN%20%E2%80%93%205-6%20Rulebook%202025%20reduced.pdf).
Boards use a seeded variable terrain layout and balanced randomized number tokens
(no neighboring 6/8 tokens), rather than the illustrated fixed beginner layout. The
first player is selected randomly. Later content expansions remain future work.
The board seed does not determine the private development deck or dice/theft outcomes;
those use a separate system random source.

## Scenarios and games already in progress

For a fast scenario start, choose **Scenario · placements + extra resources** in the
lobby. This creates legal starting pieces and adds resources. Custom targets and edited
games are marked **modified**.

For precise positions:

1. Populate the lobby with the intended number of seats, including bots if desired.
2. Open **Workshop** at `/testing`, unlock host controls, choose a fixture, and click
   **New sandbox**. Fixtures include economy/trading, development cards, seven/robber,
   and a nine-point late game.
3. Select intersections, edges, or hexes on the map. Set owners/building levels,
   terrain/numbers, resource hands, and the active player using the editor.
4. Test moves with the action buttons or **Test any rule command**. Tests use the real
   engine against an isolated sandbox; they do not alter the live table.
5. **Export scenario JSON** to keep it, or **Start sandbox as game** when the live game
   has ended. Players then open the normal table. A loaded game waits for human seats
   to be connected.

Scenario JSON remaps its seat IDs to the current lobby in order, so it can be reused
with different friends. Imports validate topology, component limits, resource supply,
development deck conservation, phases, and references. Resource banks are recalculated
from the supplied hands. Unsupported expansion/ruleset IDs are rejected.

`/secret` is the live **Admin** panel: pause/resume, edit the live board, restore a human
seat, configure bots, set the next dice, post announcements, or submit validated commands
as another seat. Raw state JSON supports more involved custom scenarios. Live board/state
editing requires a pause. Host changes mark the game modified; the owner credential,
not the URL spelling, authorizes these operations. Neither panel executes uploaded code.

## Saves and reconnects

- Every accepted change is committed atomically to [data/session.sqlite3](./data/session.sqlite3).
- Human disconnects pause the game and produce [data/autosave.ctn](./data/autosave.ctn).
  Reopening the browser with the same seat key reconnects automatically. Remaining
  humans can wait, or vote to end the session. Bots do not vote.
- **Export save** downloads a checksummed, compressed `.ctn` file. Complete exports are
  **host-only** because they include private hands, private chat, and bot memories.
  Other players can request a server checkpoint. Do not share complete saves with
  players during a competitive game.
- Ending the game preserves [data/last-game.ctn](./data/last-game.ctn). Export in the
  lobby retrieves this archive if no game is currently running.
- To load a save, end the current game and use **Import .ctn save** in the host's lobby.
  Original human credentials reclaim their seats. Bots are restored internally. All
  human seats must reconnect before play resumes. The host can explicitly issue a
  replacement credential from Admin if someone's key was lost.
- On server restart, the SQLite checkpoint restores automatically. Keep the whole
  [data/](./data/) folder for host identity and local recovery; export `.ctn` files to
  manage long-term saves yourself.

The file format is `CTN1 + SHA-256(uncompressed JSON) + zlib(JSON)`, with schema version
1, bounded decompression, and validated state. It uses no pickle. Model API secrets and
the host key are excluded from exports; seat **hashes**, not original tokens, are saved.
Public/private chat is capped at the most recent 400 messages; bot decision context is
bounded. The host machine can inspect its own complete state.

## LLM players

No separate agent framework is needed: **the harness runs inside the server**. Start a
local model service, add an Ollama or OpenAI-compatible bot in the lobby, and enter its
model name and endpoint. Full setup, latency controls, privacy, and troubleshooting are
in [docs/LLM.md](./docs/LLM.md). Built-in bots can be used immediately without a model.

## Development and checks

```fish
npm run dev
npm run check
```

Open **http://localhost:5173** for live reload. FastAPI listens on port 8000; Vite proxies
`/api` and `/ws`. Build before running the single-process hosted version. Normal browser
clients need no Python, Node, or Pygame installation. For multi-seat testing in one
browser, open `/?profile=alice`, `/?profile=bob`, etc. Each profile stores a separate seat
key; keep its query parameter when returning to the table.

Fresh checkout (Python 3.12+, Node 22.12+, npm 10+; Linux/macOS launch commands):

```fish
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps --editable .
npm ci
npm run build
```

Python dependencies are pinned in [requirements.lock](./requirements.lock); npm dependencies
are pinned in [package-lock.json](./package-lock.json). The Python lock was generated on
Linux/Python 3.14. Other operating systems/runtimes need their own compatibility check.

## Code map

| File | Responsibility |
| --- | --- |
| [board.py](./server/catan_server/board.py) | Base/expanded topology, terrain, number tokens, and ports. |
| [engine.py](./server/catan_server/engine.py) | Phases, legal actions, resource economy, cards, trading, awards, and player views. |
| [session.py](./server/catan_server/session.py) | One lobby, credentials, ordered commands, presence, checkpoints, owner controls, bot scheduler. |
| [scenarios.py](./server/catan_server/scenarios.py) | Scenario validation/remapping, editing, and CTN encoding/decoding. |
| [storage.py](./server/catan_server/storage.py) | Atomic SQLite snapshot/journal and checkpoint file writes. |
| [botlogic.py](./server/catan_server/botlogic.py) | Fast fallback policy and bounded local-model adapters. |
| [api.py](./server/catan_server/api.py) | HTTP endpoints and authenticated state WebSocket. |
| [app.py](./server/catan_server/app.py), [__main__.py](./server/catan_server/__main__.py) | Lifecycle, request boundaries, frontend hosting, and CLI. |
| [application.ts](./frontend/src/application.ts), [ui.ts](./frontend/src/ui.ts) | Browser interaction/state and interface markup. |
| [board.ts](./frontend/src/board.ts) | Shared Canvas renderer, local camera, hit testing, and placement animation. |
| [types.ts](./frontend/src/types.ts), [global.css](./frontend/src/global.css) | Client contracts and responsive table styling. |
| [main.ts](./frontend/src/main.ts), [app.ts](./frontend/src/app.ts), [index.html](./frontend/index.html) | Stable browser entry points. |
| [vite.config.ts](./frontend/vite.config.ts), [tsconfig.json](./frontend/tsconfig.json), [tsconfig.node.json](./frontend/tsconfig.node.json) | Browser build/proxy and type checks. |
| [pyproject.toml](./pyproject.toml), [package.json](./package.json), [frontend/package.json](./frontend/package.json) | Dependency declarations and commands. |
| [tests/](./tests/) | Rule scenarios, complete simulated games, multiplayer/privacy/recovery tests, and mocked model adapters. |

No expansion beyond the base 5–6 extension is enabled yet. The rules are independent
of HTTP/rendering and identify their ruleset/version explicitly, leaving future packs
and house-rule interfaces separate from the browser and transport.
