# blz-desynced-toolkit

Python tooling for reading and writing [Desynced](https://store.steampowered.com/app/1450900/Desynced/) (by The Desynced Team) behavior `.dcs` clipboard strings — the base62/zlib/MessagePack-variant wire format the game uses for its copy/paste clipboard.

## What this is

Desynced programs its robots and buildings through an in-game visual flow-graph editor. This package gives that same data a text-based, version-controllable form outside the game:

- **`dcs_wire`** — codec for the `.dcs` clipboard string format itself (base62 + optional zlib + a custom MessagePack variant), decoding to/from genuine Lua tables.
- **BSF** (`bsf/`) — a graph-native text representation of a behavior (nodes with explicit branch edges, not tree-structured pseudocode — real user behaviors routinely use indirect-goto-style `jump`/`label` dispatch that tree syntax can't express). Bidirectional: decompile a `.dcs` string to BSF text, hand-edit it, recompile back to a `.dcs` string. Includes validation/lint, Mermaid diagram rendering, and a wire-position-independent semantic diff (the game's own editor re-encodes untouched nodes on every save, so a raw text diff overreports).
- **A `lupa`-backed runtime** (`lua_runtime.py`, `interpreter.py`, `mock_world.py`) — executes behaviors through the actual, unmodified game Lua (`data/instructions.lua`, `data/library.lua`, and friends) rather than a hand-reimplemented-in-Python interpreter. Includes a steppable mock world for testing multi-unit behaviors (sensing, movement) without the game running.

Everything past the wire-bytes layer works with real Lua tables (1-based, via `lupa`), not a Python dict standing in for one.

## Why this exists

The goal is to let outside tools — including LLM coding agents — read and edit Desynced behaviors directly, the way they would work with source code. Desynced's own clipboard copy/paste is the only externally-exposed read/write channel for behaviors and requires no game-side mod, so reading and writing its `.dcs` wire format directly is the most direct path to that goal.

The BSF text format exists because Desynced behaviors are genuine graphs — arbitrary-fan-in control edges, arbitrary-fan-out data edges, confirmed against real user behaviors — and a tree-structured pseudocode representation (tried first, later abandoned) is a lossy fit for that.

Hand-reimplementing Desynced's instruction semantics in Python was the source of most bugs found while building this — subtle register/composite-value rules, branch encoding, block-stack behavior, and so on are easy to misread from Lua source alone and easy to get wrong by hand. Running the real game Lua through `lupa` sidesteps that: the runtime's decisions are the game's own, not a re-derivation of them.

## Requirements

- Python >= 3.14
- [`uv`](https://docs.astral.sh/uv/)
- A local extract of the Desynced game data assets (the Lua scripts that define instructions, frames, components, etc). This package does not include or fetch it — bring your own copy of the game's asset files.

## Setup

```sh
uv sync
```

This installs `lupa` and the dev dependencies into a gitignored `.venv/`.

The runtime and CLI need the game data extract. By default they look for a sibling directory named `desynced-game-data` next to this repo (i.e. `../desynced-game-data/` relative to wherever this repo is checked out). If your copy lives elsewhere, point at it with the `DESYNCED_GAME_DATA` environment variable or the CLI's `--game-data` flag. Either a directory extract or the game's `main.zip` directly (read via `zipfile`, no extraction needed) works.

## Usage

### CLI

The `bsf` subpackage is runnable as a module and reads stdin / writes stdout, built to sit in a shell pipeline against the game's own clipboard:

```sh
# decompile: .dcs string -> BSF text
python -m blz.desynced_toolkit.bsf decompile < mybehavior.dcs > mybehavior.bsf

# hand-edit mybehavior.bsf, then compile back: BSF text -> .dcs string
python -m blz.desynced_toolkit.bsf compile < mybehavior.bsf > mybehavior.dcs

# wire-position-independent diff between two .dcs saves
python -m blz.desynced_toolkit.bsf semantic-diff a.dcs b.dcs

# lint a BSF file or .dcs string for legal-but-suspicious constructs
python -m blz.desynced_toolkit.bsf lint < mybehavior.bsf

# look up an internal game id <-> in-game display name
python -m blz.desynced_toolkit.bsf ids radar
```

Add `--annotate` to `decompile` to inline `#` comments with in-game display names for correlating BSF text against the visual editor.

On a machine with clipboard access to the game (e.g. a shared clipboard between a Windows gaming host and this environment), this chains directly against the game's own copy/paste:

```sh
cb -o | python -m blz.desynced_toolkit.bsf decompile > mybehavior.bsf
# ...edit mybehavior.bsf...
python -m blz.desynced_toolkit.bsf compile < mybehavior.bsf | cb -i
```

(`cb` above is a placeholder for whatever clipboard read/write command is available on your system, e.g. `xclip -selection clipboard`.)

### Library

```python
from blz.desynced_toolkit import dcs_wire
from blz.desynced_toolkit.bsf import decompile, compile, render_text, parse_text

table = dcs_wire.decode_dcs(clipboard_string)
ir = decompile.decompile(table)
text = render_text.render(ir)
# ...edit text...
ir2 = parse_text.parse(text)
table2 = compile.compile(ir2)
clipboard_string2 = dcs_wire.encode_dcs(table2)
```

For running behaviors against the real game Lua:

```python
from blz.desynced_toolkit.lua_runtime import LupaEngine

engine = LupaEngine()  # loads the real Data registry + instructions.lua
table = engine.decode_dcs(clipboard_string)
```

See `docs/behavior_source_format.md` for the BSF grammar and `docs/behavior_format.md` for the underlying wire format's register/branch/stopping semantics — read both before hand-authoring or editing BSF text.

## Documentation

- `docs/behavior_format.md` — the wire format: register/slot addressing, branch and fall-through resolution, stopping semantics.
- `docs/behavior_source_format.md` — the BSF grammar and design rationale.
- `docs/instructions_index.md` — auto-generated reference of every instruction in `data.instructions` (regenerate with `uv run python scripts/generate_instructions_index.py > docs/instructions_index.md` after a game data update).
- `docs/mock_world_spec.md` — design for the steppable mock world used to test multi-unit behaviors.

## Development

```sh
uv run pytest tests/        # test suite (skips cleanly if the game data extract isn't found)
uv run ruff check .         # lint
uv run ruff format .        # format
uv run mypy                 # type-check
```

Tests read fixtures only from `tests/data/`.

## License

MIT — see [LICENSE](LICENSE).
