"""Regenerates `docs/instructions_index.md` by reading the real `data/instructions.lua` (via
`LupaEngine`, no reimplementation of instruction metadata in Python) -- run this any time the
extract updates, per CLAUDE.md's `docs/instructions_index.md` entry and `todo.md`'s "next game
release" checklist.

Usage: `uv run python scripts/generate_instructions_index.py > docs/instructions_index.md`
(resolves the game-data extract the same way tests/the CLI do: DESYNCED_GAME_DATA env var, or
the sibling `../desynced-game-data` directory).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from blz.desynced_toolkit import LupaEngine, open_asset_source
from blz.desynced_toolkit.lua_util import to_py

REPO_ROOT = Path(__file__).resolve().parent.parent

FILTER_LEGEND = [
    ("any", "Any value, including negative numbers"),
    ("data", "Any data value (no numbers)"),
    ("entity", "Entity register only (no literal selection in UI)"),
    ("posnum", "Positive number only"),
    ("num", "Number (may allow negative/infinite/NOT depending on field)"),
    ("coord", "Coordinate value"),
    ("coord_num", "Number or coordinate"),
    ("item", "Item type only"),
    ("item_num", "Item type or number"),
    ("comp", "Component item only"),
    ("comp_num", "Component item or number"),
    ("frame", "Frame (unit/building) type only"),
    ("frame_num", "Frame type or number"),
    ("frame_item", "Frame or item type"),
    ("radar", "Broad filter: number, item, frame, or entity-filter tag"),
    ("resource_num", "Resource tag or number"),
    ("tech", "Technology entry"),
    ("Space", "Space/reserved slot in argument list (no value)"),
]

# The 1.0.17971 category overhaul (upcoming-changes.md), in the order the changelog lists them.
CATEGORY_ORDER = [
    "Flow",
    "Logic",
    "Loops",
    "Values",
    "Units",
    "Movement",
    "Inventory",
    "Logistics",
    "Components",
    "Production",
    "Communication",
    "World",
    "Memory",
]
LEGACY_BUCKET = "Legacy / Deprecated"

_CONVERT_TARGET_RE = re.compile(r"inst\.op\s*(?:,[^=]*)?=\s*'([a-zA-Z_][a-zA-Z0-9_]*)'")
# Anchored to column 0: a genuine top-level `data.instructions.X = ` declaration, never an
# internal reference like `data.instructions.combine_register.func(...)` inside some other
# entry's own function body (those are always indented) -- without this anchor, an internal
# reference occurring textually before that entry's own `convert = function(...)` truncates the
# search window too early and silently drops the real convert target (found via combine_coordinate
# and separate_coordinate, whose `func` bodies both reference another op by full name before their
# own `convert` field).
_NEXT_TOP_LEVEL_RE = re.compile(r"^data\.instructions\.[a-zA-Z_]", re.MULTILINE)


def _lua_len(tbl) -> int:
    n = 0
    for _ in tbl.items():
        n += 1
    return n


def _int_keys(tbl) -> list[int]:
    return sorted(k for k in tbl.keys() if isinstance(k, int))


def _str_or_none(v) -> str | None:
    return v if isinstance(v, str) else None


def _resolve_engine() -> LupaEngine:
    game_data = os.environ.get("DESYNCED_GAME_DATA", str(REPO_ROOT.parent / "desynced-game-data"))
    if not os.path.exists(game_data):
        raise SystemExit(f"game data extract not found at {game_data} (set DESYNCED_GAME_DATA)")
    src = open_asset_source(game_data)
    # Bare instructions-only runtime is enough -- this script only reads data.instructions.
    return LupaEngine(src, load_data_registries=False)


def _convert_target(source_text: str, op: str) -> str | None:
    """Best-effort: the op id a legacy `convert` function migrates this instruction to, scraped
    from `data.instructions.<op> = { ... convert = function(inst) inst.op[, ...] = '<target>' ...
    end, ... }` in the raw source (the same regex-over-source-text approach argcache.py's own
    `_scan_registrations` already uses for id->display-name lookups -- no separate Lua parse)."""
    marker = f"data.instructions.{op} = "
    start = source_text.find(marker)
    if start < 0:
        marker = f"data.instructions.{op} =\n"
        start = source_text.find(marker)
    if start < 0:
        return None
    # The instruction's own table body ends at the next top-level `data.instructions.` entry.
    next_match = _NEXT_TOP_LEVEL_RE.search(source_text, start + len(marker))
    body = source_text[start : next_match.start() if next_match else start + 4000]
    conv_idx = body.find("convert = function")
    if conv_idx < 0:
        return None
    m = _CONVERT_TARGET_RE.search(body, conv_idx)
    return m.group(1) if m else None


def _arg_line(argdef) -> str:
    if isinstance(argdef, list):
        atype = argdef[0] if len(argdef) > 0 else None
        name = argdef[1] if len(argdef) > 1 else None
        desc = argdef[2] if len(argdef) > 2 else None
        filt = argdef[3] if len(argdef) > 3 else None
        expanded = argdef[4] if len(argdef) > 4 else None
    else:
        atype = argdef.get(1)
        name = argdef.get(2)
        desc = argdef.get(3)
        filt = argdef.get(4)
        expanded = argdef.get(5)
    parts = [f"- **{atype}**"]
    if isinstance(name, str) and name:
        parts.append(f" {name}")
    if isinstance(desc, str) and desc:
        parts.append(f" — {desc}")
    if isinstance(filt, str) and filt:
        parts.append(f" `[{filt}]`")
    if expanded is True:
        parts.append(" *(extra param)*")
    return "".join(parts)


def _collect(engine: LupaEngine) -> list[dict]:
    instructions = engine.lua.globals().data.instructions
    from blz.desynced_toolkit import assets

    source_text = engine.source.read_text(
        assets.resolve_include(
            assets.get_package_manifest(engine.source, "Data").entry_dir,
            "instructions.lua",
        )
    )

    entries = []
    for op in instructions.keys():
        if not isinstance(op, str) or op == "nop":
            continue
        d = instructions[op]
        name = _str_or_none(d.name)
        category = _str_or_none(d.category)
        has_convert = d.convert is not None
        args_raw = d.args
        args = (
            [to_py(args_raw[i]) for i in _int_keys(args_raw)] if args_raw is not None else []
        )
        entries.append(
            {
                "op": op,
                "name": name,
                "desc": _str_or_none(d.desc),
                "category": category,
                "is_loop": d["next"] is not None,
                "is_hidden_literal": d.make_asm is not None,
                "is_legacy": has_convert or category is None,
                "convert_target": _convert_target(source_text, op) if has_convert else None,
                "args": args,
            }
        )
    return entries


def _render_entry(e: dict) -> list[str]:
    lines = []
    title = e["name"] or f"`{e['op']}`"
    modifiers = []
    if e["is_loop"]:
        modifiers.append("*(loop)*")
    if e["is_hidden_literal"]:
        modifiers.append("*(hidden literal)*")
    if e["is_legacy"]:
        modifiers.append("*(legacy/deprecated)*")
    header = f"### {title} (`{e['op']}`)"
    if modifiers:
        header += " " + " ".join(modifiers)
    lines.append(header)
    lines.append("")
    if e["desc"]:
        lines.append(e["desc"])
        lines.append("")
    if e["convert_target"]:
        lines.append(f"*Auto-converts to* `{e['convert_target']}` *on next in-game save.*")
        lines.append("")
    if e["args"]:
        for argdef in e["args"]:
            lines.append(_arg_line(argdef))
        lines.append("")
    return lines


def generate() -> str:
    engine = _resolve_engine()
    entries = _collect(engine)

    by_category: dict[str, list[dict]] = {}
    for e in entries:
        cat = e["category"] or LEGACY_BUCKET
        by_category.setdefault(cat, []).append(e)
    for cat in by_category:
        by_category[cat].sort(key=lambda e: (e["name"] or e["op"]).lower())

    ordered_cats = [c for c in CATEGORY_ORDER if c in by_category]
    ordered_cats += sorted(c for c in by_category if c not in CATEGORY_ORDER and c != LEGACY_BUCKET)
    if LEGACY_BUCKET in by_category:
        ordered_cats.append(LEGACY_BUCKET)

    total = len(entries)
    lines = [
        "# Desynced Behavior Instruction Index",
        "",
        "Auto-generated reference of every entry in `data.instructions` (`data/instructions.lua`), "
        "for writing new behaviors in the visual programming editor. Regenerate with "
        "`uv run python scripts/generate_instructions_index.py > docs/instructions_index.md` any time "
        "the extract updates.",
        "",
        f"**Total instructions:** {total} (excludes the internal `nop` placeholder used for "
        "deleted instructions). The **Legacy / Deprecated** category groups every instruction with "
        "no `category` field in source -- hidden from the in-game instruction search, kept only "
        "so old saved behaviors still load; most carry a `convert` that auto-migrates them to a "
        "current op the first time the behavior is opened and re-saved in-game.",
        "",
        "## How to read this",
        "",
        "Each instruction is listed as:",
        "",
        "```",
        "### Name (`id`)",
        "Description",
        "- in/out/exec  Label — arg description [filter] (extra param)",
        "```",
        "",
        "- **in** — input value consumed by the instruction (register, literal, or parameter)",
        "- **out** — output value the instruction writes to a register",
        "- **exec** — an execution branch (a wire out of the node to the next instruction(s)); "
        "instructions with more than one `exec` arg branch based on a condition; instructions "
        "with zero `exec` args fall through to the next instruction in sequence",
        "- **(extra param)** — this arg slot is a UI-only literal expander (`expanded=true` in "
        "source), typically shown as an additional inline field rather than a pluggable wire",
        "- **(loop)** — instruction implements `next`/`last` and behaves as a loop/iterator "
        '(e.g. "for each") rather than running once',
        "- **(hidden literal)** — instruction has a `make_asm`/custom `node_ui` and takes a "
        "configured value (dropdown, text field, sub-behavior picker, etc.) baked into the "
        "instruction node itself, not listed as an `args` entry",
        "- **(legacy/deprecated)** — no `category` field in source, so the in-game instruction "
        "search never surfaces it; only reachable via old saved data",
        "",
        "### Filter legend (input value-type restrictions)",
        "",
    ]
    for tag, desc in FILTER_LEGEND:
        lines.append(f"- `{tag}` — {desc}")
    lines.append("")
    lines.append("## Categories")
    lines.append("")
    for cat in ordered_cats:
        anchor = cat.lower().replace(" / ", "--").replace(" ", "-")
        lines.append(f"- [{cat}](#{anchor}) ({len(by_category[cat])})")
    lines.append("")

    for cat in ordered_cats:
        lines.append(f"## {cat}")
        lines.append("")
        for e in by_category[cat]:
            lines.extend(_render_entry(e))

    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    sys.stdout.write(generate())
