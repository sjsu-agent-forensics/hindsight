---
name: python-documentation
description: Write or revise Python docstrings, comments, and type hints in this repo, then format
  them. Use when adding or changing Python code under apps/, packages/, or demos/, when asked to
  document code, or when reviewing code for documentation quality.
---

# Python documentation

Documentation here serves two readers: teammates extending the framework, and anyone auditing a
forensic result who needs to know what the code assumed. Write for both.

## Docstrings

Follow PEP 257. `docformatter` (run below) enforces the layout, so focus on content.

- **Every module, public class, and public function gets a docstring.** Private helpers (`_name`)
  need one only when their behavior is not obvious from the name and signature.
- **The first line is a one-sentence summary** in the imperative mood ("Build the state graph",
  not "Builds" or "This function builds"), ending with a period.
- **The body says what the signature cannot**: behavior on edge cases, side effects, what is
  returned when something fails, and why the function exists if that is not obvious. Do not
  restate the parameter types already in the type hints.
- **Use Google-style sections only when they add information:** `Args:` when a parameter's meaning
  or valid range is not clear from its name and type, `Returns:` when the return value has
  structure or special cases, `Raises:` for every exception a caller is expected to handle.

```python
def stream_agent_chat(graph: CompiledStateGraph, messages: list) -> Any | None:
    """Run the graph on the given messages, streaming output as it is produced.

    Returns the final graph state once execution completes, or None if the graph does
    not finish.
    """
```

```python
def load_policy(store: PolicyStore, version: str | None = None) -> Policy:
    """Load a compiled policy from the store.

    Args:
        version: Policy version to load. If None, loads the latest validated version.

    Raises:
        PolicyNotFoundError: No validated policy exists at the requested version.
    """
```

## Comments

- **Explain why, not what.** The code already says what it does. Comment the constraint, the
  reason a simpler approach does not work, or the external behavior being worked around.
- **Link the reason when it lives elsewhere** -- an issue, a library bug, a README section.
- **No commented-out code.** Delete it; git keeps the history.
- **Keep comments true.** When changing code, update or remove the comments that describe it.

```python
# fastmcp's default host, port and path; mcp-enabled-agent connects to this URL.
server.run(transport="http")
```

## Type hints

- Annotate every public function's parameters and return type, including `-> None`.
- Prefer built-in generics and `|` unions (`list[str]`, `str | None`) -- the project targets
  Python 3.12.
- Use `TypedDict`, dataclasses, or Pydantic models for structured data rather than `dict[str, Any]`
  so the structure documents itself.

## Project-specific expectations

Hindsight is a security tool, so some assumptions must be written down where the code relies on
them:

- **Trust boundaries.** When a function handles agent-produced or tool-produced content, say that
  the content is untrusted and what the function does (or does not) do to it. Recorded agent
  content never authorizes actions.
- **Observed vs. inferred.** When a function produces analysis results, document whether each field
  is observed directly from a trace or inferred (for example, by a model-based analyzer).
- **Failure statuses.** Document what happens on missing records, invalid input, or timeouts. These
  must surface as explicit incomplete, failed, or indeterminate results, never as "no violation".
- **Determinism.** Code on the runtime enforcement path must be deterministic and model-free; say
  so in its module docstring so later changes do not quietly add model calls.

## Package and app READMEs

Each package or app under `apps/`, `packages/`, or `demos/` has a `README.md`. Keep it to: what it
is, how to run it (if runnable), and what it exports (if a library) -- see
`packages/hindsight-core/README.md`. Cross-cutting setup belongs in the root `README.md`.

## Format after documenting

After adding or changing comments and docstrings, run the formatting commands from the root
`README.md`, from the repo root, in this order:

```bash
# 1. Formatting the code to PEP 257
black . --target-version py312

# 2. Formatting the comments
uv run docformatter -i -r apps demos packages
```

Run `black` first, then `docformatter`. If `black` is not on your PATH, it is in the workspace
`dev` dependency group and can be run as `uv run black . --target-version py312`. Review the diff
afterwards: `docformatter` rewraps docstrings, which can break hand-formatted lists or examples.
