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
object. For a different server, use its advertised model ID and API root. An optional
API key can be entered later under **Admin → Configure an LLM / bot**. Local inference
does not require an OpenAI account or cloud key.

## Check a model before inviting friends

This sends one realistic six-seat observation, checks the returned move, and reports
elapsed time. Use `--players 3` to probe a smaller board. It creates no seats and does
not modify the running game:

```fish
.venv/bin/python -m catan_server.check_model --provider ollama --model your-installed-model
```

For a compatible server:

```fish
.venv/bin/python -m catan_server.check_model --provider openai --model catan-bot --endpoint http://127.0.0.1:8080/v1
```

Expect `"ok": true` and a valid command. A cold load may exceed the budget; warm the
model and try again. Choose a model/budget that actually meets your machine's response
time. The game can bound how long it waits, but cannot make a slow model infer faster.
For authenticated probe endpoints, set `CATAN_MODEL_API_KEY` in the probe terminal.

## How the harness plays

```text
Seat-filtered snapshot + legal choices + recent private memory
  -> local model produces one JSON decision
  -> harness submits its command to the normal server validator
  -> accepted event, or fresh valid fallback after rejection
```

The observation contains the board's topology/IDs, public status, the bot's own cards,
and public/private messages that seat is entitled to receive. It never contains another
seat's hand, the future development deck, host controls, or someone else's private chat.
The model receives text/JSON rather than screenshots. View filtering is shared with
humans in [engine.py](../server/catan_server/engine.py).

A decision selects an indexed legal action and may include a chat message and private
plan. It may also propose a structured player trade. No text is executed as Python.
Invalid choices or commands fall back to a freshly validated move. Mandatory rolling,
discarding, theft selection, and free-road bookkeeping can be handled by the fast
policy without an inference request. Strategic placement, building, robber decisions,
and conversation use the configured model when available.

For conversation, send a private message to the bot or mention **@BotName** in public
chat. Multi-recipient DMs are supported. Bots do not automatically reply to other bots,
preventing unbounded chat loops. A reply to a private group defaults to that group.

## Latency and failure behavior

| Setting/behavior | Default |
| --- | --- |
| Decision budget, including waiting for an inference slot | 8 seconds + 0.5 seconds scheduling margin |
| Configurable budget | 1–20 seconds |
| Chat-only request budget | At most 3 seconds + scheduling margin |
| Simultaneous model requests | 1 |
| Generated response limit | 160 tokens |
| Model context requested from Ollama | 8192 tokens |
| Recent chat/events in prompts | 8 messages / 8 events |
| Cooldown after a model failure | 30 seconds of fast fallback |

The fallback is automatic on timeout, refused connection, malformed JSON, or an invalid
move. The table remains responsive while inference runs. During cooldown the seat keeps
playing with the built-in policy, rather than repeatedly waiting for a broken endpoint.
**Admin** displays the last model error; applying a corrected bot configuration clears
the cooldown. Built-in bots are deliberately simple opponents, not expert Catan players.

If the host has enough resources for concurrent inference, `set -lx CATAN_LLM_CONCURRENCY 2`
before starting the server allows two requests. Increasing it on an already saturated
machine may increase latency. Keep model endpoints local to the host by default; only
the Catan frontend needs Internet exposure.

## Saving bot context

Model name, endpoint, provider, recent decisions/messages, and private planning notes
are stored with the seat and included in host saves. On resume, the bot gets the restored
game view and bounded recent memory. Model API secrets are kept out of exported saves;
re-enter them after importing on another machine. Model weights and engine-specific KV
caches are not embedded in `.ctn` files. If a model is unavailable after restoring, the
fallback keeps the game playable.

The provider adapters and deadline/failure paths are covered by mocked tests. A real
model daemon and model weights are host prerequisites; this repository does not download
or run model weights automatically.
