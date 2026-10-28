---
name: python-coding
description: Conventions for writing and reviewing Python code in this repo, including the rule
  that functions and methods used only within their own file or class are prefixed with an
  underscore. Use when writing, refactoring, or reviewing Python under apps/, packages/, or demos/.
---

# Python coding

For docstrings, comments, type hints, and formatting, also follow the `python-documentation`
skill.

## Private functions and methods take a leading underscore

A function or method's name says who is allowed to call it. If nothing outside its own file
(for a module-level function) or its own class (for a method) uses it, prefix it with a single
underscore.

- **Module-level function** used only within its module -> `_name`.
- **Method** called only from within its own class -> `_name`.
- **Anything imported or called from another file** -> no underscore. That includes functions
  re-exported in a package's `__init__.py`.

```python
# graph.py -- only build_graph() is used outside this file
def _should_continue(state: State) -> str:
    ...

def build_graph() -> CompiledStateGraph:
    ...
    graph.add_conditional_edges("generator", _should_continue)
```

```python
class Recorder:
    def flush(self) -> None:          # called by the interposer
        for event in self._pending():
            self._write(event)

    def _pending(self) -> list[Event]:   # only used inside Recorder
        ...

    def _write(self, event: Event) -> None:
        ...
```

### Exceptions

Do not add an underscore when the name is used by something other than a Python import:

- **Entry points** referenced from `[project.scripts]` in `pyproject.toml` (`main`).
- **Framework-registered functions** whose name becomes externally visible. An MCP tool registered
  with `@server.tool()` is exposed under its function name, so `add` must stay `add` -- renaming
  it to `_add` renames the tool the agent sees.
- **Nested functions** defined inside another function (such as the `generator_node` closure
  returned by `generator_node_factory`). They are already local to their scope.
- **Dunder methods** (`__init__`, `__repr__`, ...) and overrides of a base class's public methods.

### Related rules

- **Use a single underscore, not two.** A double leading underscore triggers name mangling; use it
  only when you deliberately need to avoid name clashes in subclasses.
- **Tests may call private functions** in the module they test. That does not make the function
  public.
- **When a private function gains a caller in another file, rename it** to drop the underscore in
  the same change. Do not import `_name` across files.
- **When a public function loses its last outside caller, make it private** or delete it.

## General practices

- **Small functions with one job.** If a function needs a comment heading each section, split it.
- **Pass dependencies in; don't reach for globals.** Take clients, stores, and configuration as
  parameters so code can be tested and so a session's state cannot leak into another.
- **Fail loudly and specifically.** Raise a specific exception with a message that says what was
  wrong; never swallow exceptions with a bare `except:` or `except Exception: pass`.
- **Keep runtime and offline code apart.** Code on the interposer's enforcement path must be
  deterministic and must not call a model. Analysis that needs a model belongs in the forensic
  service.
- **Treat agent and tool content as untrusted data.** Never `eval`, `exec`, format into shell
  commands, or otherwise execute content taken from tool responses, tool descriptors, or agent
  output.
- **Prefer the standard library and existing dependencies** before adding a new package, and add
  dependencies to the package that imports them (see the root `README.md`).
- **Apps never import other apps.** Shared code goes in a package under `packages/`.
