# Catan Ultimate

A single-session Catan server with a browser frontend, planned for up to six human or
local-LLM players. See [the implementation plan](./PLAN.md) and
[the layout sketch](./layout.png).

**Current state:** project plumbing only. A placeholder page and health endpoint work.
There is no lobby, gameplay, WebSocket endpoint, persistence, LLM connection, testing
interface, or admin interface yet. `/testing` and `/secret` return 404.

## Requirements

- Python 3.12 or newer, with venv/pip support.
- Node.js 22.12 or newer and npm 10 or newer.
- Linux or macOS for the commands below. They work in fish and bash; npm runs its
  scripts independently of your interactive shell. Windows launch commands are not
  configured yet.

## First setup

The local environment and dependencies have already been installed on this checkout.
On a fresh checkout, run these commands from the project root:

```fish
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps --editable .
npm ci
```

The editable install lets Python import `catan_server` directly from the source folder;
changing its code does not require reinstalling it. `requirements.lock` includes both
runtime dependencies and development tools. `npm ci` installs exactly the versions in
`package-lock.json`, including the frontend workspace.

### Optional fish activation

```fish
source .venv/bin/activate.fish
```

This makes `python` and `pip` refer to the local environment in that terminal. Use
`deactivate` to undo it. Activation is **not required** for any npm command below:
the Python commands explicitly use `.venv/bin/python`.

## Everyday commands

| Command | What it does |
| --- | --- |
| `npm run dev` | Starts the Python API on `127.0.0.1:8000` and the live-reloading frontend on `127.0.0.1:5173`. Open **http://127.0.0.1:5173**. Ctrl+C stops both processes. |
| `npm run build` | Checks TypeScript and builds browser assets into `frontend/dist/`. |
| `.venv/bin/python -m catan_server` | Serves the existing frontend build and API from one Python process on port 8000. Open **http://127.0.0.1:8000** locally. Listens on all network interfaces. |
| `npm run check` | Runs Python lint/format checks, infrastructure tests, and both TypeScript checks. |
| `npm run dev:server` | Runs only the Python development server, with source reload. |
| `npm run dev:frontend` | Runs only the frontend development server. |

For the one-server mode, first run `npm run build`, then `.venv/bin/python -m catan_server`. Rebuild after
frontend changes and restart the server if frontend routes/assets were added after
startup. Internet exposure and player authentication are later implementation steps;
listening on all interfaces does not itself create a public URL.

## How the pieces connect

During development:

```text
Browser -> Vite (:5173) -> /api/* and future /ws/* proxy -> FastAPI (:8000)
                       -> frontend TypeScript/CSS with live reload
```

When hosting the built application:

```text
Browser -> FastAPI (:8000) -> /api/* and future WebSocket routes
                         -> generated frontend/dist/ files
```

Browser code uses relative API paths such as `/api/health`; it never hard-codes the
host's address. Vite's proxy keeps those paths working in development without adding
a permissive cross-origin policy. The WebSocket proxy is prepared for later work;
there is no WebSocket implementation yet. Node is a build/development tool; players
only need a browser, and the running built server only needs Python.

FastAPI also provides API documentation at `/docs` and `/redoc`, with the machine-readable
schema at `/openapi.json`. The only application API endpoint currently listed is the
health check.

## File guide

### Infrequently changed foundation

These files should stay out of normal feature work. They may still change for dependency
upgrades, deployment changes, or a new platform; none is promised to be immutable.

| File | Purpose |
| --- | --- |
| [pyproject.toml](./pyproject.toml) | Defines the Python package, supported Python floor, runtime/dev dependencies, source location, pytest discovery, and Ruff lint/format rules. |
| [requirements.lock](./requirements.lock) | Exact installed Python dependency versions, including transitive dependencies. Reinstall from it; regenerate deliberately when dependencies change. |
| [package.json](./package.json) | Root npm command menu and frontend workspace registration. `concurrently` runs/stops both development servers. Python hosting always uses one worker. |
| [package-lock.json](./package-lock.json) | Generated lock of the complete npm dependency tree. `npm ci` uses it for repeatable installs. |
| [frontend/package.json](./frontend/package.json) | Declares TypeScript, Vite, Node type definitions, and frontend check/build commands. No UI framework is installed. |
| [frontend/index.html](./frontend/index.html) | Browser document with the `#app` mount point, mobile viewport metadata, and TypeScript entry script. Vite replaces that script reference when building. |
| [frontend/vite.config.ts](./frontend/vite.config.ts) | Fixes the frontend root, development port, and API/WebSocket proxy. Vite builds into `frontend/dist/`. |
| [frontend/tsconfig.json](./frontend/tsconfig.json) | Strict browser TypeScript checks, DOM types, bundler imports, and source/output locations. Vite emits the browser files; TypeScript checks them. |
| [frontend/tsconfig.node.json](./frontend/tsconfig.node.json) | Separately checks the Vite configuration, which runs in Node rather than the browser. |
| [frontend/src/main.ts](./frontend/src/main.ts) | Loads global CSS, finds `#app`, and calls the application's mount function. Future UI modules should enter through `app.ts`. |
| [server/catan_server/__init__.py](./server/catan_server/__init__.py) | Marks the importable Python server package. |
| [server/catan_server/__main__.py](./server/catan_server/__main__.py) | Direct Python hosting entry point. Starts one Uvicorn worker on port 8000, serving the built frontend and API. Avoids wrapping the hosted server in npm so terminal shutdown signals reach it directly. |
| [server/catan_server/app.py](./server/catan_server/app.py) | Application factory: registers the API, serves only the built frontend directory, and gives a helpful 503 at `/` if no build exists. Uses paths relative to the source file, not the current working directory. Future authenticated routes must be registered before the static mount. |
| [.gitignore](./.gitignore) | Excludes environments, installed packages, build output, caches, local secrets/data, and exported saves from Git. |
| [.editorconfig](./.editorconfig) | Consistent UTF-8, LF line endings, whitespace, and indentation (four spaces for Python, two otherwise). |
| [.gitattributes](./.gitattributes) | Normalizes text line endings and marks PNG files as binary. |
| [.vscode/tasks.json](./.vscode/tasks.json) | Adds a **Build frontend** task to VS Code, running the same `npm run build` command. |

### Deliberate future edit points

These are minimal entry points needed to run the foundation. New features will extend
or replace them; they are not completed game features.

| File | Purpose |
| --- | --- |
| [frontend/src/app.ts](./frontend/src/app.ts) | Temporary application content. Later owns initial UI composition and delegates to lobby/board/chat modules. |
| [frontend/src/global.css](./frontend/src/global.css) | Small browser-wide baseline: sizing, fonts, margins, and form font inheritance. Future component styling belongs beside the components. |
| [server/catan_server/api.py](./server/catan_server/api.py) | Collects application routes. Currently only `/api/health`, returning `{"status":"ok"}`. Later registers HTTP feature routers under `/api` and WebSocket routers under `/ws`. |
| [tests/test_app.py](./tests/test_app.py) | Checks API/static routing, operation without a frontend build, and rejection of paths outside the build directory. Future feature tests go in separate files. |
| [PLAN.md](./PLAN.md) | Product requirements, architecture, and staged implementation plan. |
| [layout.png](./layout.png) | User-provided visual reference, not a game asset or served frontend file. |
| [README.md](./README.md) | Setup instructions, commands, and this file guide. |

### Generated local folders

| Folder | Contents |
| --- | --- |
| `.venv/` | Isolated Python interpreter links, installed libraries, command-line tools, and fish's `activate.fish`. Recreate from the setup commands; do not edit inside it. |
| `node_modules/` | Installed frontend/build tools. Recreate with `npm ci`. |
| `frontend/dist/` | Browser-ready HTML/JS/CSS produced by `npm run build`. Never edit generated files; rebuild their sources. |
| `server/*.egg-info/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/` | Editable-package metadata and tool caches, recreated automatically. |

No blank gameplay/expansion/bot modules have been created yet. Those will be added as
small, testable steps without filling the foundation with speculative code.

## Dependency maintenance

Python dependencies are declared in `pyproject.toml`. After an intentional dependency
change, install `.[dev]` into the local environment, verify it, then refresh the lock:

```fish
.venv/bin/python -m pip install --editable '.[dev]'
.venv/bin/python -m pip freeze --exclude catan-ultimate > requirements.lock
```

The Python lock is an environment snapshot, generated on Linux with Python 3.14; it
is not a tested compatibility matrix for every supported Python version/platform.
The Python version requirement describes the source's minimum; validate dependencies
when adding another runtime/platform. For npm, intentional installs/updates update
`package-lock.json` automatically. Do not manually edit either lock file.
