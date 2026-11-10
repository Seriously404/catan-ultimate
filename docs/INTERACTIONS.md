# Trade negotiation and presentation events

## Player offers

Normal base-game trade eligibility remains in force: player trades happen during the
primary player's action phase and must involve that active player. Both parties give
resources; gifts and trading the same resource in both directions remain disallowed.
Alliances and promises are conversation, with no formal team membership or enforcement.

`offer_trade` accepts `to: []` for all eligible players or a list of selected seat IDs.
Legacy single-string recipients are accepted and normalized to lists. Nonactive players
may propose only to the active player. Addressed offers and responses are visible only
to their participants. Completed trades expose participants and card counts to everyone,
while private terms remain private.

Each offer stores a server timestamp, five-second deadline, and a response per recipient:
`pending`, `accepted`, or `rejected`.

1. `accept_trade` registers interest during the five-second window. No cards move early.
2. At the deadline, one acceptance completes the exchange automatically. Two or more
   acceptances leave it awaiting `select_trade` from the proposer, with `offer` and `player`.
   Later arrivals cannot join that choice. Selecting anyone else is rejected.
3. With no acceptances at the deadline, the offer stays open. Its first later acceptance
   completes the exchange. The server serializes competing requests, so there is one winner.
4. A recipient can `reject_trade` while pending; rejection is final. All rejections close
   the offer immediately. The proposer can `cancel_trade` after the collection window.
5. Balances are checked again at settlement. An acceptance that can no longer be paid
   becomes a rejection; an unaffordable proposer closes the offer. Resources are exchanged
   atomically and are not reserved when someone expresses interest.

The proposer/active player cannot end the turn while offers remain unresolved. Knight
and Road Building phase changes also wait until offers resolve, preventing a phase
transition from invalidating the timer. Other legal building/bank actions may continue,
subject to resource checks. Dummies respond on the scheduler's next tick: unaffordable
means reject; otherwise a random accept/reject. Model seats evaluate offers through
their normal legal-action menu, including off-turn responses.

Pausing freezes collection windows. Resuming restores their remaining time. A save
import or process restart grants a fresh five seconds to unfinished collection windows.
Accepted/rejected responses and pending partner choices survive save validation.
Legacy saved offers acquire the new fields. Waiting for responses does not hold the
server lock or block chat. The host can end a game to cancel outstanding inference.

## Animation contract for the next UI pass

Every accepted game action produces a durable semantic event with:

- `schema`, monotonically increasing `id` (the commit revision), and `at` (server epoch time).
- `type`, actor, board target IDs, and applicable partner/victim/proposer IDs.
- Resulting turn, phase, and active player; dice rolls include their own dice values.
- `effects`: resource/card changes by seat, including counts gained/spent. Exact resource
  deltas are visible only to entitled seats; theft does not expose its resource to observers.
- `starts_at` and `duration_ms`, allowing effects to be queued rather than played together.

State snapshots include `server_time` and `presentation_until`. Normal gameplay actions
wait for the preceding presentation interval; chat and trade responses remain usable.
Bots honor that interval. `CATAN_ACTION_DELAY` controls its duration in seconds (default
0.8, range 0–5); unit rule tests disable it and pacing tests enable it explicitly.
The workshop remains immediate for testing. The server does not stream interpolation
steps or wait for browser acknowledgements.

The browser queues events in order and deduplicates them across HTTP/WebSocket updates.
New/reconnected viewers start from a snapshot instead of replaying an old animation
backlog. Board coordinates and camera transforms stay client-side. The next visual
pass can replace effects and animate card counters using these deltas, without changing
the command protocol. Current snapshots still render current counters immediately;
delayed visual counters and polished card/dice/road effects belong to that pass.
