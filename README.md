# hindsight

Post-execution forensics framework for compromised LLM-based agent systems.

The demos under `demos/` are the baseline the framework builds on: a LangGraph ReAct agent
(`react-agent`) and an agent that reaches its tools over MCP (`mcp-enabled-agent`). The MCP tool
servers it calls (`math-server`, `weather-server`) live under `apps/mcp-servers/`. These were moved
here from the earlier `agency` prototype repo. The demos use a local Ollama model (`llama3.2:3b` at
`http://localhost:11434` by default; see `hindsight.llm.get_llm`).

## Layout

A [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/): one lockfile and one
virtual environment at the root, shared by every member.

```
apps/agent/                the ReAct agent hindsight protects; all its tools go through the interposer
apps/interposer/           MCP gateway between the agent and its tool servers
apps/launcher/             runs the agent once under a hindsight session registered with the interposer
apps/mcp-servers/          one directory per MCP tool server, each its own project (not members)
packages/hindsight-core/   code shared by the apps and demos (imported as `hindsight`)
packages/provenance/       value-matching provenance index, used by the interposer (and replay)
packages/trace-schema/     trace event format shared by the interposer and the agent
demos/                     one directory per demo, each its own package
traces/                    session traces written at runtime, one directory per session (ignored)
```

`apps/agent`, `apps/interposer` and `apps/launcher` are workspace members, listed by name in the
root `pyproject.toml`.

### Planned structure

Runnable applications go under `apps/`; libraries they share go under `packages/`. Apps never
import each other -- they communicate over MCP, HTTP, or trace records -- and depend only on
packages.

```
apps/
  agent/              LangGraph ReAct agent (MCP client + telemetry hooks)
  interposer/         MCP proxy: recorder, provenance index, policy enforcer
  forensics/          offline analysis: dependency graph, attribution, synthesis, validation
  launcher/           starts sessions, assigns session IDs, runs the agent
  mcp-servers/
    math/             one directory per tool server, incl. attack/test servers
    ...
packages/
  hindsight-core/     shared basics (LLM factory, config)
  rule-engine/        rule vocabulary + evaluator, used by interposer AND forensics
  trace-schema/       session/event record format, used by agent, interposer, forensics
demos/                current baseline demos, kept as reference
```

The rule engine and trace schema are packages because more than one app depends on them: the
interposer and the forensic service must evaluate rules with the same code, and the agent,
interposer, and forensic service all read or write the same trace records.

## Setup

```bash
uv sync --all-packages
```

This creates `.venv/` at the root and installs `hindsight-core` and every demo into it as editable
installs, so edits to `hindsight` take effect in the demos immediately. Do not create a virtual
environment per demo -- a workspace resolves to a single set of dependency versions by design.
Point your editor's interpreter at `.venv/`.

## Running a demo

`uv run` uses the root environment automatically; there is nothing to activate.

```bash
uv run --package react-agent react-agent
uv run --package mcp-enabled-agent mcp-enabled-agent
```

`mcp-enabled-agent` also needs its tool servers set up, and `weather-server` running (see
below).

## Running the agent

The agent in `apps/agent` reaches its tools only through the interposer, and only within a
hindsight session, which the launcher registers for each run. It uses both tool servers: the
interposer runs `math-server` itself, as each session's own child process, so only
`weather-server` needs starting. Set both up once (see below), then start `weather-server` and the
interposer, each in its own terminal, and run the agent through the launcher:

```bash
uv run --project apps/mcp-servers/weather weather-server   # http://127.0.0.1:8001/api/mcp
uv run --package interposer interposer                     # http://127.0.0.1:8000
uv run --package launcher launcher "What's (3 + 5) x 12? What's the weather in San Jose?"
```

See the READMEs in `apps/agent`, `apps/interposer` and `apps/launcher` for options and the
interposer's checks.

## Running the tool servers

MCP servers are not workspace members (see below), so they take `--project` rather than `--package`,
and each needs its own one-time setup:

```bash
uv sync --project apps/mcp-servers/math
uv sync --project apps/mcp-servers/weather
```

The two use different transports, which is what decides whether you start them by hand:

| server           | transport | address                          | started by                          |
| ---------------- | --------- | -------------------------------- | ----------------------------------- |
| `math-server`    | stdio     | none -- stdin/stdout             | the demo, or the interposer         |
| `weather-server` | http      | `http://127.0.0.1:8001/api/mcp`  | you, beforehand                     |
| `faults-server`  | stdio     | none -- stdin/stdout             | the interposer's Phase 4 check only |

`math-server` speaks stdio, so whoever uses it spawns it as a subprocess and you never start it
yourself: `mcp-enabled-agent` directly, or the interposer once per session for `apps/agent`. Both
launch the console script out of that server's own `.venv`, which is what keeps the two
incompatible `mcp` versions apart, so the `uv sync` above is what makes it work.

`weather-server` listens on a port and runs until you stop it, so start it in its own terminal
before the demo:

```bash
uv run --project apps/mcp-servers/weather weather-server   # Ctrl-C to quit
```

Then run the agent from a second terminal:

```bash
uv run --package mcp-enabled-agent mcp-enabled-agent
```

Only one process can hold port 8001; if the server fails with `error while attempting to bind`,
another copy is already running.

To call a running HTTP server without the agent, point the `fastmcp` CLI at its URL:

```bash
uv run --project apps/mcp-servers/weather fastmcp call http://127.0.0.1:8001/api/mcp weather_san_jose '{}'
```

Or point the CLI at a server file instead, which needs no running server -- this is the only way
to poke at `math-server`, since it has no address of its own. The CLI imports the module,
auto-detects the server object and runs its own copy over stdio, bypassing `main()` and whatever
transport it configures, so each command is self-contained. Point it at the module, not the console
script:

```bash
# list the tools the server exposes
uv run --project apps/mcp-servers/math fastmcp list apps/mcp-servers/math/src/math_server/main.py

# call one directly
uv run --project apps/mcp-servers/math fastmcp call     apps/mcp-servers/math/src/math_server/main.py multiply '{"a": 8, "b": 12}'

# full report (tools, resources, prompts, capabilities)
uv run --project apps/mcp-servers/math fastmcp inspect apps/mcp-servers/math/src/math_server/main.py

# browser-based MCP Inspector (needs node/npx)
uv run --project apps/mcp-servers/math fastmcp dev apps/mcp-servers/math/src/math_server/main.py
```

Appending an explicit `:server` object suffix to the path fails -- auto-detection is the working
form.

## The MCP server exception

`apps/mcp-servers/*` is listed under `exclude` in the root `[tool.uv.workspace]`, and each server
there has its own `.venv`. This is deliberate, and the one place the single-environment rule does
not hold.

The two sides need incompatible majors of `mcp`, and a workspace resolves to a single set of
versions:

| package                                                   | requires             |
| --------------------------------------------------------- | -------------------- |
| `math-server`, `weather-server` -> `fastmcp>=4.0.8`       | `mcp>=2.0.0`         |
| `hindsight-core` -> `langchain-mcp-adapters`              | `mcp>=1.24.0,<2.0.0` |

mcp 2.x renamed `FastMCP` to `MCPServer` and dropped `mcp.server.fastmcp`, which
`langchain-mcp-adapters` imports; there is no release of it that supports mcp 2.x. Keeping both
means keeping two environments.

This costs less than it sounds like. Each server runs as its own process out of its own `.venv`,
so the two never share an interpreter at runtime -- whether the agent reaches it over HTTP or spawns
it over stdio. For a stdio server this is why the client must launch that server's console script by
path rather than a bare `python`: a bare `python` would resolve to the workspace environment, where
`fastmcp` is not installed and cannot be. The workspace was the only thing forcing them into one
resolution.

Two consequences worth knowing:

- MCP servers cannot depend on `hindsight-core`, because that would pull `langchain-mcp-adapters`
  and its `mcp<2` cap back into the server's environment. Shared code has to reach it some other
  way.
- `uv sync --all-packages` does not touch it. Set it up separately:

```bash
uv sync --project apps/mcp-servers/<server-name>
```

Server directories take a short name (`apps/mcp-servers/math/`) since the parent already says they
are servers, but the package keeps a `-server` suffix (`math-server`, module `math_server`). A
module named after its directory would often shadow the standard library -- `math`, `email`,
`calendar` -- and break every import of it in that environment.

## Managing dependencies

Add a dependency to the package that actually imports it. Either form updates that package's
`pyproject.toml` and the root `uv.lock`:

```bash
uv add --package <demo-name> <dependency>     # from anywhere in the workspace
cd demos/<demo-name> && uv add <dependency>   # equivalent
```

Dependencies needed by more than one demo belong in the shared package instead, and the demos
inherit them:

```bash
uv add --package hindsight-core <dependency>
```

Note that `uv remove` re-syncs the environment to only the target package's dependencies, which
can uninstall others. Follow it with `uv sync --all-packages`.

## Adding a demo

```bash
uv init --package demos/<demo-name>           # scaffold the package
uv add --package <demo-name> hindsight-core      # depend on the shared package
uv sync --all-packages                        # install it into the root .venv
```

`members = ["packages/*", "demos/*"]` in the root `pyproject.toml` picks it up with no further
registration. Run the commands from the repo root; `uv init` detects the surrounding workspace and
git repo, so it writes only `pyproject.toml`, `README.md`, and `src/<demo_name>/__init__.py` -- no
nested `.git`, `.venv`, or `.python-version`.

Use a hyphenated `<demo-name>`; uv derives the importable module name by replacing hyphens with
underscores (`demos/react-agent` -> `src/react_agent/`). The `uv add` step also records
`hindsight-core = { workspace = true }` under `[tool.uv.sources]`, which is what makes it resolve to
the local package rather than PyPI.

The scaffold points the console script at `__init__.py`:

```toml
[project.scripts]
<demo-name> = "<demo_name>:main"
```

The existing demos keep `__init__.py` empty and put the entry point in `main.py` instead, so move
the generated `main()` there and update the script to match:

```toml
[project.scripts]
<demo-name> = "<demo_name>.main:main"
```

Then confirm it runs:

```bash
uv run --package <demo-name> <demo-name>
```

## Testing

Tests use pytest, one suite per project, each in that project's `tests/` folder with `pytest` in
its `dev` dependency group. Run a workspace member's tests with `--package` and an MCP server's
with `--project`, since servers have their own environments (see "The MCP server exception"):

```bash
uv run --package interposer pytest apps/interposer          # add -m "not slow" to skip process tests
uv run --package trace-schema pytest packages/trace-schema
uv run --package provenance pytest packages/provenance
```

The interposer's tests start everything they need on free ports; only its process tests need
`uv sync --project apps/mcp-servers/faults` first, and skip without it. See
`apps/interposer/README.md` for what each test file covers.

## Formatting

```bash
# 1. Formatting the code to PEP 257
black . --target-version py312

# 2. Formatting the comments
uv run docformatter -i -r apps demos packages
```
