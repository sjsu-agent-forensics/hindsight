# hindsight

Post-execution forensics framework for compromised LLM-based agent systems.

The demos under `demos/` are the baseline the framework builds on: a LangGraph ReAct agent
(`react-agent`) and an agent that reaches its tools over MCP (`mcp-enabled-agent`). The MCP tool
server it calls (`math-server`) lives under `apps/mcp-servers/`. These were moved here from the
earlier `agency` prototype repo. The demos use a local Ollama model (`gemma4` at `http://localhost:11434` by default; see
`hindsight.llm.get_llm`).

## Layout

A [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/): one lockfile and one
virtual environment at the root, shared by every member.

```
apps/mcp-servers/          one directory per MCP tool server, each its own project (not members)
packages/hindsight-core/   code shared by the demos (imported as `hindsight`)
demos/                     one directory per demo, each its own package
```

### Planned structure

Runnable applications go under `apps/`; libraries they share go under `packages/`. Apps never
import each other -- they communicate over MCP, HTTP, or trace records -- and depend only on
packages.

```
apps/
  agent/              LangGraph ReAct agent (MCP client + telemetry hooks)
  interposer/         MCP proxy: recorder, provenance index, policy enforcer
  forensics/          offline analysis: dependency graph, attribution, synthesis, validation
  orchestrator/       starts sessions, assigns session IDs (could fold into agent at first)
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

`mcp-enabled-agent` needs `math-server` running first (see below).

## Running the math server

`math-server` is the exception -- MCP servers are not workspace members (see below), so it takes
`--project` rather than `--package`, and needs its own one-time setup:

```bash
uv sync --project apps/mcp-servers/math
```

It serves MCP over streamable HTTP at `http://127.0.0.1:8000/mcp` (fastmcp's default host, port and
path) and runs until you stop it. Start it in its own terminal:

```bash
uv run --project apps/mcp-servers/math math-server     # Ctrl-C to quit
```

`mcp-enabled-agent` connects to that URL rather than spawning the server, so start the server
first, then run the agent from a second terminal:

```bash
uv run --package mcp-enabled-agent mcp-enabled-agent
```

Only one process can hold port 8000; if the server fails with `error while attempting to bind`,
another copy is already running.

To call it without the agent, point the `fastmcp` CLI at the running server's URL:

```bash
uv run --project apps/mcp-servers/math fastmcp call http://127.0.0.1:8000/mcp multiply '{"a": 8, "b": 12}'
```

Or point the CLI at the server file instead, which needs no running server. The CLI imports the
module, auto-detects the server object and runs its own copy over stdio -- `main()` and its HTTP
transport are bypassed, so each command is self-contained. Point it at the module, not the console
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

| package                                             | requires             |
| --------------------------------------------------- | -------------------- |
| `math-server` -> `fastmcp>=4.0.8` -> `fastmcp-slim` | `mcp>=2.0.0`         |
| `hindsight-core` -> `langchain-mcp-adapters`        | `mcp>=1.24.0,<2.0.0` |

mcp 2.x renamed `FastMCP` to `MCPServer` and dropped `mcp.server.fastmcp`, which
`langchain-mcp-adapters` imports; there is no release of it that supports mcp 2.x. Keeping both
means keeping two environments.

This costs less than it sounds like. The server runs as its own process and the agent talks to it
over HTTP, so the two never share an interpreter at runtime. The workspace was the only thing
forcing them into one resolution.

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


## Formatting

```bash
# 1. Formatting the code to PEP 257
black . --target-version py312

# 2. Formatting the comments
uv run docformatter -i -r apps demos packages
```
