# Local LLM seats

The harness is bundled with [the server](../server/catan_server/session.py). You do not
need LangChain, an agent framework, or an external bot script. A local model service
does the inference; the host's Catan process supplies observations, checks commands,
and saves context. The fast built-in bot needs no model at all.

## Ollama

1. Install/start [Ollama](https://docs.ollama.com/quickstart). If it is not already
   running as a service, start it with `ollama serve` in another terminal.
2. Run `ollama list` to find the exact name of a model already installed on the host.
   Use an instruction/chat model that fits the host's memory and supports JSON output.
3. Warm that model before starting the game: `ollama run your-installed-model`.
   Replace `your-installed-model` with its actual name; leave the model service running.
4. In Catan's host lobby, choose **Add a bot → Local model · Ollama**. Enter a name,
   the model's exact name, and endpoint **http://127.0.0.1:11434**. Do not append `/api`.
5. Keep the default eight-second decision budget initially. Add the bot and start
   the game as usual. Model seats are automatically ready.

The adapter uses `/api/chat`, schema-constrained JSON, no streamed frames, a short
generation limit, and `keep_alive: "30m"`. It requests `think: false` to avoid long
reasoning output; your model/service must support that setting. These request fields
are documented in [Ollama's Chat API](https://docs.ollama.com/api/chat).

## llama.cpp or another OpenAI-compatible local server

Start your existing local chat server, using an instruction model with its proper chat
template. For a current llama.cpp build, an example is:

```fish
llama-server -m /absolute/path/to/model.gguf -c 8192 --port 8080 --alias catan-bot
```

Replace the model path with a real file. See the [official llama.cpp server guide](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
for installation, GPU options, model templates, and platform-specific commands.

In Catan choose **Local model · OpenAI-compatible API**, model **catan-bot**, endpoint
**http://127.0.0.1:8080/v1**. The harness appends `/chat/completions` and requests a JSON
object and `reasoning_effort: "none"` so reasoning models produce a final decision
within the short output budget. For a different server, use its advertised model ID
and API root. An optional API key can be entered later under
**Admin → Configure an LLM / bot**. Local inference
does not require an OpenAI account or cloud key.

## Check a model before inviting friends

This first loads a realistic six-seat observation with an untimed acknowledgement
request, then checks a timed move. It reports warm-up and decision durations separately.
Use `--players 3` to probe a smaller board. It creates no seats and does not modify
the running game:

```fish
.venv/bin/python -m catan_server.check_model --provider ollama --model your-installed-model
```

For a compatible server:

```fish
.venv/bin/python -m catan_server.check_model --provider openai --model catan-bot --endpoint http://127.0.0.1:8080/v1
```

Expect `"warmup": {"status": "active", ...}`, `"ok": true`, and a valid command.
The warm-up may take as long as the initial context load requires. Choose a model/budget
that actually meets your machine's response
time. The game can bound how long it waits, but cannot make a slow model infer faster.
For authenticated probe endpoints, set `CATAN_MODEL_API_KEY` in the probe terminal.

## How the harness plays

```text
Seat-filtered snapshot + legal choices + recent private memory
  -> first request: acknowledge active, reasoning off, no timeout
  -> local model produces one JSON decision
  -> harness submits its command to the normal server validator
  -> accepted event, or fresh valid fallback after rejection
```

The observation contains the board's topology/IDs, public status, the bot's own cards,
and public/private messages that seat is entitled to receive. It never contains another
seat's hand, the future development deck, host controls, or someone else's private chat.
The model receives text/JSON rather than screenshots. View filtering is shared with
humans in [engine.py](../server/catan_server/engine.py).

A decision selects an indexed legal action, or supplies a normal-player command.
Models can choose placements, discards, victims, development-card parameters, building,
bank trades, player offers, accept/reject responses, and a partner among acceptances.
All commands still go through the ordinary rules validator. No text is executed as Python.
Invalid commands use a fresh legal fallback and record the rejection in private memory.

Bots observe public chat, messages addressed to them, and visible game events. They may
initiate public conversation, send private/group messages, reply to other bots, negotiate,
and change informal alliances without an @mention. Each response can contain up to three
messages for different audiences, alongside one game action. `choice: -1` means wait.
The response format is `{"choice": -1, "messages": [{"text": "Let's cooperate", "to": ["seat-id"]}],
"memory": "Persistent private plan"}`; an empty `to` is public. Legacy `message` and
`recipients` fields remain supported. Invalid private recipients never become public chat.

The `memory` field updates a persistent private strategy note (up to 2400 characters),
included in future observations as `private_strategy` and retained in saves. The model
is instructed to retain useful alliances, promises, trust, and goals. This is model-managed
memory, so remembering a promise does not enforce it or guarantee cooperative behavior.

To bound chatter, proactive observations are spaced by 10 seconds per seat by default,
with at most two social decisions per visible event/human-message context. Direct human
messages can wake a bot sooner. Bot-only replies cannot create an unlimited inference loop.
A bot that chooses to wait is reconsidered when its context changes or after the interval.
Repeated messages are rate-limited to one batch per four seconds. Bots may propose at most
two offers per turn and cancel an unanswered own offer after 30 seconds. Set
`CATAN_BOT_SOCIAL_INTERVAL` to change the proactive interval (minimum one second).

Player trading uses a five-second collection window; see [trade and event semantics](./INTERACTIONS.md).
Dummies accept or reject affordable offers randomly, and reject unaffordable ones without inference.

## Latency and failure behavior

| Setting/behavior | Default |
| --- | --- |
| Initial context warm-up per seat/configuration/game | No timeout; acknowledgement only, reasoning off |
| Waiting for an inference slot | No decision timer |
| Decision budget after warm-up | 8 seconds + 0.5 seconds scheduling margin |
| Configurable budget | 1–20 seconds |
| Social/negotiation request budget | The configured decision budget |
| Simultaneous model requests | 1 |
| Generated response limit | 512 tokens (16 for warm-up) |
| Model context requested from Ollama | 16384 tokens |
| Recent chat/events in prompts | 20 messages / 12 events |
| Cooldown after a model failure | 30 seconds of fast fallback |

The fallback is automatic on timeout, refused connection, malformed JSON, or an invalid
move. The table remains responsive while inference runs. During cooldown the seat keeps
playing with the built-in policy, rather than repeatedly waiting for a broken endpoint.
**Admin** displays the last model error; applying a corrected bot configuration clears
the cooldown. Built-in bots are deliberately simple opponents, not expert Catan players.

Before the first model decision for a seat, Catan sends the same harness instructions,
private game view, legal-action menu, and recent memory as a normal decision, followed
by a request for `{"status":"active"}`. This request has no HTTP or application
timeout, disables reasoning, and generates at most 16 tokens. It is not a game move or
chat message. The table shows **Loading model…**, then **Thinking…** for the timed
decision. Warm-up happens again after a configuration change, server restart, new
game, or a failed model request. Ending the game, reconfiguring/removing the bot, or
shutting down Catan cancels pending requests. A reachable but stalled model can wait
indefinitely during warm-up until one of those actions is taken.

This primes the model server's prompt cache; it does not permanently install context
in model weights. Cache eviction, other clients, and changing game/seat observations
can still make later requests slower. Static board topology is placed before changing
seat/game/chat data in the prompt, so timestamps and different observers do not invalidate
the large board-layout prefix. Dynamic roads/buildings remain in the current observation.

## Concurrent inference with llama.cpp

Both ends must allow parallel requests. In your llama.cpp launch command, replace
`-np 1` with `-np 2`. For a modest context allocation, use `-c 32768` (16384 tokens per
slot with two separate slots) instead of reserving 131072 for one slot. Keep the Vulkan,
batching, quantized-cache, model path, endpoint, and alias options appropriate to your
machine. Continuous batching is enabled by default in the inspected llama.cpp build.

Then, in the fish terminal used to launch Catan:

```fish
source .venv/bin/activate.fish
set -lx CATAN_LLM_CONCURRENCY 2
python -m catan_server
```

Use the same endpoint/model for several seats; each keeps separate private context.
Alternatively, run separate model servers on different ports and configure seats to use
those endpoints. The Catan setting is a global cap, so increase it to cover the desired
simultaneous requests. Do not share the model endpoint with Internet players; only
Catan needs a public URL. Parallel slots consume KV-cache memory and share compute:
two requests can overlap without guaranteeing lower latency per request. Start with two,
measure actual response times, and increase only if your machine handles it.

## Saving bot context

Model name, endpoint, provider, recent decisions/messages, and private planning notes
are stored with the seat and included in host saves. On resume, the bot gets the restored
game view and bounded recent memory. Model API secrets are kept out of exported saves;
re-enter them after importing on another machine. Model weights and engine-specific KV
caches are not embedded in `.ctn` files. If a model is unavailable after restoring, the
fallback keeps the game playable.

## Troubleshooting llama.cpp / Qwen

- A 503 during startup can mean the model is still loading; wait for `/health` to
  report `{"status":"ok"}` before probing.
- The untimed acknowledgement handles the initial prompt load. Later decisions still
  include prompt processing and generation in their timeout; a changed or evicted
  prompt may be slower than a cached one. Try `--timeout 20` for those decisions.
- Empty final content with `finish_reason=length` and nonempty `reasoning_content`
  means the model spent its output allowance on reasoning. The OpenAI-compatible
  adapter now requests no reasoning; it never interprets reasoning as a game move.
  Restart Catan after updating its source. On a llama.cpp build supporting it,
  `--reasoning off` can also disable reasoning at model-server startup.
- Use the ID returned by `curl http://127.0.0.1:8080/v1/models`, or launch llama.cpp
  with `--alias qween` and enter `qween` in Catan. The displayed bot name is independent.

You can run a game with **3–6 bots and no human seats**. Unlock host controls without
joining a seat, add the bots, and start. Remove any existing human seats before
starting if you want a fully automated table. Several seats may share one model
endpoint: each seat has its own observation/memory, and the default concurrency of
one serializes inference, matching a llama.cpp server launched with `-np 1`.

The provider adapters and deadline/failure paths are covered by mocked tests. A real
model daemon and model weights are host prerequisites; this repository does not download
or run model weights automatically.
