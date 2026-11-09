# Catan Ultimate: implementation plan

## Target

### Implementation checkpoint — 2026-11-09

The playable base game now includes the 30-hex 5–6 player extension and paired turns,
single lobby, browser renderer, player trading/chat, durable checkpoints and CTN import/export,
scenario workshop, host controls, built-in bots, and integrated local-model adapters.
See [the hosting guide](./README.md) and [LLM setup](./docs/LLM.md).
Rule and multiplayer tests include complete games at every supported player count.
Further content expansions and visual polish remain later work. Real model throughput
depends on the host's installed model/hardware; adapters are tested with mocked services.

A host runs one Python server on their computer with exactly one lobby and at most one active game. Friends open its public URL, claim their own seats, and play a complete six-player-capable base game. Human and local-LLM players use the same rules engine. The browser draws the board and animations; the server alone decides whether actions are legal, updates state, and persists the game. An optional invite code controls entry to this single lobby; it is never a room selector.

The first release includes base Catan and its 5–6 player extension, public and private chat, save/export/import, reconnect and pause behavior, an isolated visual testing page at `/testing`, and an owner-only control page at `/secret`. Later releases add named expansion rule packs and configurable house rules.

## Architecture choices

| Area | Choice | Reason |
| --- | --- | --- |
| Rules | Pure Python domain package, independent of FastAPI, rendering, and LLM code | Deterministic tests and reusable rule packs |
| Server | FastAPI, Uvicorn with one worker, WebSockets for live play, HTTP for admission and save operations | One authoritative process and one active game |
| Persistence | SQLite event journal plus periodic snapshots | Recover after a crash, inspect action history, and export a compact save |
| Browser | TypeScript and Canvas/WebGL renderer, with HTML/CSS for lobby, cards, chat, accessibility, and controls | Smooth local animation and a polished interface without streaming full frames |
| Pygame | Optional Python renderer using the same board geometry and visual assets for desktop previews, asset generation, and debugging | Pygame never streams frames; only state/events cross the network |
| LLM | Local model adapter owned by the host; structured observation and action tools | Bots have player-level information and use the same validated commands as humans |

Browser rendering is a settled requirement. Game state and semantic events cross the network; no frames, pixel coordinates, or server-side animation steps do. Pygame is limited to optional local tools. Browser code owns layout, hit testing, tweening, particles, and effects; it cannot decide rules or expose hidden data.

### Core data flow

```text
Human browser ─┐
               ├─ authenticated command ─> game command queue ─> rules engine
LLM harness ───┘                                   │                    │
                                                   │             events + new state
                                                   └─ persist ─> filtered updates ─> clients
```

Every command has a seat identity, action type, payload, unique command ID, and expected state revision. The server serializes commands for the active game, checks phase/turn/ownership/resources/topology, commits events and the new revision, then broadcasts each player's allowed view. A rejected or duplicate command never changes state. The client may animate optimistically, but it reconciles with server updates. Full snapshots on join/reconnect prevent missed WebSocket events from corrupting the display. Semantic events carry enough information to animate a road placement, dice roll, card draw, or theft without transmitting animation frames. Events are filtered per seat: other players may see that a card was stolen without seeing its resource type.

## Broad implementation stages, in order

### Foundation checkpoint (2026-11-07)

Installed the Python virtual environment and frontend tooling, added dependency locks,
strict TypeScript configuration, FastAPI application composition, static frontend hosting,
a health endpoint, development proxy/run commands, and infrastructure checks. See
[the setup and file guide](./README.md). This is the prerequisite plumbing for stage 1;
lobby state, player identities, WebSockets, and persistence remain unimplemented.

### 1. Server and connection skeleton

Create the package layout, configuration, FastAPI app, single-lobby/game manager, WebSocket protocol, HTTP health endpoint, and a static frontend served by the same process. Add admission to the single lobby, optional invite code, player seat tokens, reconnect, command IDs, revision numbers, and a minimal event journal. Start with a dummy action so two browser tabs can observe the same authoritative state. Define protocol schemas and error codes now, before adding rules.

**Done when:** Two remote-capable clients can join the one lobby, send a dummy command, see the same revision, reconnect, and reject a forged or stale command. A server restart restores the session from persistence.

### 2. Board model and basic rendering

Define expansion-ready hex/vertex/edge coordinates, board tiles, ports, numbered tokens, pieces, and player colors. Render a seeded board in the browser using Canvas/WebGL. Make the map pannable by dragging and zoomable by wheel, pinch, and visible controls, with fit-to-board and sensible zoom limits. Convert pointer positions through the inverse camera transform for accurate tile/edge/vertex hit testing at any zoom. Keep camera state local to each browser; all players share the same board setup and game state but may choose different viewport positions. Build a small optional Pygame preview renderer against the same geometry and art data; avoid putting rule decisions in either renderer.

Use [the layout sketch](./layout.png) as the desktop composition reference. Split the viewport into a large table area on the left and a full-height chat column on the right; place chat history above a message composer and recipient selector pinned to the bottom. Center the board in the table area, not the whole browser window. Put the local player's hand and controls below the board. Arrange opponent hand groups in the remaining ring around it: lower-left, upper-left, top, upper-right, and lower-right for a six-seat game. Each group shows identity, public status, and face-down resource/development-card counts, never card faces or private messages. Leave space between those groups and the board so placement targets and animations remain clear.

Treat the viewer's seat as index zero and map the other occupied seats in turn order using `(seat_index - viewer_index + player_count) % player_count`. Place the `player_count - 1` opponents along an arc around the board, from lower-left through top to lower-right, leaving the bottom open for the local hand. For relative opponent index `i = 1..N-1`, a starting angle is `150° + 240° × (i - 1) / (N - 2)` in screen coordinates; compute positions from the board container's center and horizontal/vertical radii, then adjust them to avoid overlap with the board, hand, and chat. This keeps each player's own hand at the bottom without rotating board geometry or changing the shared setup. Animate theft from the victim's hand group toward the thief's group, while showing the card identity only to seats entitled to know it. Keep the board camera independent of these anchored UI groups: panning and zooming move the map, not the hands or chat. On narrow screens, collapse chat and opponent details into panels so the board remains usable. The same board/camera component appears in play, `/testing`, and `/secret`.

**Done when:** A shared seed produces the same board geometry; clients can select a tile/edge/vertex reliably at varied zoom and pan positions; 3–6 seats lay out without overlap; all three interfaces reuse the camera; and the table adapts to common desktop and phone widths.

### 3. Visual testing interface

Build `/testing` as a separate development sandbox with a fixture selector, state viewer, event timeline, command form, seed control, and reset button. It calls the same command handler and rules engine as the real game; fixture setup may use test-only helpers. Show legal targets, reasons for rejected moves, resource/point changes, and before/after state. Testing fixtures are isolated disposable state, not additional lobbies or live games. Require owner authentication when exposed publicly.

**Done when:** Every later rule can be demonstrated with a small fixture and a visible before/after result, and tests cannot mutate the live game accidentally.

### 4. Complete base-game rules, including 5–6 players

Implement the turn/phase state machine and full base rules in small vertical slices. Each slice adds a pure rule operation, focused automated cases, a fixture, and a visual demonstration in `/testing`.

Suggested slice order:

1. Board generation, valid number-token/desert placement, ports, seats, bank inventory, and deterministic setup seeds.
2. Initial settlement/road placement in snake order, distance rule, coastal placement, and starting resources from the second settlement.
3. Dice, tile production, robber blocking, resource shortage rules, seven/discard flow, robber relocation, eligible victims, random theft, and no-victim handling.
4. Road and settlement construction, connection rules, distance rule, piece limits, and city upgrades with piece returns.
5. Turn flow, building costs, bank trade, 4:1 trade, generic/specialized ports, and finite bank stock.
6. Player trade offers, private/public negotiation, acceptance/cancel/expiry, and atomic exchange when it is legal to trade.
7. Development-card deck, buying, hand privacy, play timing/limits, knight/road building/year of plenty/monopoly/victory-point behavior, and deck exhaustion.
8. Longest Road, Largest Army, hidden and public points, ties/transfer rules, and victory at 10 points under the chosen turn rule.
9. The 5–6 player extension's extra turn/build mechanism, six-seat board layout and component counts, and edge cases around wins during extra actions. Pin a specific published edition/rule variant in the rule-pack metadata before implementing this slice.
10. End-to-end legal-game simulation and rule conformance review against the pinned rulebook.

Represent the game as explicit phases and pending choices, not a set of loose booleans. An action can produce a pending discard, robber target, steal target, free-road placement, or trade response; the next valid command resolves it. Keep random draws injectable and record their results in events so replay is deterministic.

**Done when:** Six human-controlled seats can finish a full base game, and automated scenarios cover legal and illegal moves, bank scarcity, hidden information, special cards, scoring, and all phase transitions.

### 5. Single lobby and Internet access

Add join/ready/seat selection, game options, board seed, player count, reconnect status, and host start controls for the single lobby. Only the host can replace/reset a finished game or import a save into the current session. Serve frontend and API from the same origin. Document three host connection methods: port forwarding with a stable DNS name and HTTPS reverse proxy; a tunnel with an HTTPS public URL; or a mesh/VPN option when all players can join it. Friends need the host URL to reach the server. If an optional invite code is enabled, it only gates entry; a shareable invite URL can contain that code. The code is not a substitute for the host address.

Use a strong random seat token for each human and store only its hash server-side. A saved browser token or separately exported recovery credential reclaims that seat; knowing the invite code alone does not. Use HTTPS/WSS for Internet play, rate limits on joins/chat, request size bounds, origin checks, and seat-level authorization. Run one Uvicorn worker.

**Done when:** Players outside the host's LAN can join through a documented method, occupy only their own seats, and resume after refreshing or losing connection.

### 6. Chat and LLM seats

Add public chat and recipient-selected private messages as persisted game events, with access checks and per-recipient delivery. The host can technically access all data on the machine; the interface should not reveal private messages to other seats. Chat must never be interpreted as an executable command by itself.

Implement a bot harness as a player client inside the host process, with an option for a separately launched local process to connect using a dedicated bot seat token. The harness exposes a text/JSON view of exactly the filtered state its seat is allowed to know: board IDs and topology, public pieces/points, whose turn it is, its own cards/resources, chat addressed to it, current pending choice, and legal action templates with parameters. This is the LLM's specialized frontend; it does not need screenshots or a simulated mouse.

For each relevant state change or addressed chat message, the harness calls the local model endpoint with that observation and a concise rules/tool schema. The model chooses one structured tool call, such as `build_road(edge_id)`, `offer_trade(...)`, `send_message(recipients, text)`, or `end_turn()`. The harness translates it to the same authenticated command a human UI sends. The server validates it and returns the accepted event or a precise rejection. On rejection, the harness sends the reason and fresh legal options back to the model, with a retry limit. Parameterized legal-action templates avoid enumerating every possible trade amount. Tool names and input schemas are versioned with the ruleset; model text is never executed as code.

Run local inference in a background worker/task with timeouts, cancellation, and bounded retries so it cannot block networking. Allow the bot to speak when addressed, even outside its turn, but gate game actions by phase. Add a deterministic fallback policy for failed/slow inference, and show a clear bot-waiting state to humans. Save model endpoint/configuration, prompt version, a bounded conversation transcript or summary, and pending tool state so a resumed bot can continue. Keep secrets and other players' hands out of its observation.

**Done when:** A local-model bot can take a complete turn, negotiate in chat, recover after an invalid action, and resume a paused game without extra hidden information.

### 7. Save, export, import, disconnect, and recovery

Persist every accepted action and periodically snapshot the game. On human disconnect, atomically save and pause the game; show the remaining humans a choice to wait for reconnection or end the session. LLM seats have no vote. Define what happens if the host process stops: on restart, restore the paused game and let seats reconnect. Import replaces the current inactive/ended session only with host authorization: validate the file and compatible rules, stage its lobby state, then wait for all human seat tokens and all LLM adapters to be present before resuming.

Use `.ctn` as a versioned compact archive: magic/version header, compressed structured state, rule-pack IDs and versions, event/revision checkpoint, public/private messages, bot continuation data, and a checksum. Prefer a widely supported encoding such as MessagePack with Zstandard compression; do not use pickle or execute embedded content. Record hashes of seat tokens, not raw credentials. Bound decompressed size, validate every field and cross-reference, and migrate only known schema versions. Export at an atomic revision and verify export→import→export equivalence in tests.

**Done when:** A game can be exported, moved to a new host, imported, rejoined by the same humans and bots, and resumed with identical rules state and private views. Disconnection and crash recovery are demonstrated.

### 8. Polish, accessibility, and release hardening

Finish the lobby and board visual design, motion and sound preferences, keyboard controls, readable colors, mobile interaction, action confirmations, clear rejection messages, and player help. Run multi-client browser sessions, latency/reconnect simulations, long-running game simulations, and host installation checks. Package a one-command launch path with database backups and clear tunnel/port-forward instructions.

**Done when:** A non-developer host can start a session and six geographically separate players can complete and save a game without opening development tools.

## Expansion and custom-rule design

Define a `Ruleset` as a pinned bundle of rule packs, component definitions, board topology, setup policy, phases/actions, scoring, and serialization version. Base game and 5–6 player extension are the first packs. Use explicit capability and conflict declarations so the single lobby can reject unsupported combinations before a game starts. Future packs may add terrain, edges, ships, commodities, pieces, cards, hazards, and phase transitions; avoid hard-coding the board to 19 hexes or the player count to four. Rule packs receive typed hooks/events but cannot bypass the central command validator or hidden-information filter.

The named later candidates are Seafarers, Traders & Barbarians, Explorers & Pirates, and Cities & Knights. Treat each as its own project after the base release; define compatibility and variant policies from the particular rule editions selected. House rules use versioned configuration and explicit hooks. If arbitrary user code is ever supported, it must be a separately trusted host-installed extension, not content uploaded by a browser or save file.

## `/secret` owner controls

Build this after the core server, but wire it through explicit privileged commands and audit events. It may inspect state, edit resources/pieces, force phases, queue a dice result, create custom events, and manage test scenarios. Reuse the zoomable board/camera and allow the owner to select pieces or locations visually. The path name is not authentication: require a separate owner credential, protect WebSocket and HTTP actions, use CSRF protection for browser requests, and prevent ordinary seat tokens from using owner commands. Mark the game as modified after an admin action and record who changed what, so save/replay remains understandable. Keep dangerous development-only actions out of regular game commands.

## First implementation contract

Before writing gameplay rules, settle and document: (1) the base rulebook edition and 5–6 player rule variant; (2) the first Internet exposure method to document and test; and (3) the host operating systems. The browser renderer, single lobby, and state-only network protocol are settled. A rough layout sketch can guide the art and spacing in stage 2 without changing the architecture.
