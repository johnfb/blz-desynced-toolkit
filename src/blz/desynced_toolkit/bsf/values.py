"""BSF value grammar (docs/behavior_source_format.md's "Values" table): a small tagged union plus
conversion to/from the raw values a decoded/compiled behavior table actually holds. No text
rendering/parsing here on purpose -- see bsf/render_text.py and bsf/parse_text.py -- so this
module stays untouched by any future change to the surface syntax."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union

import lupa.lua54 as lupa


@dataclass(frozen=True)
class Num:
    n: int | float


@dataclass(frozen=True)
class Coord:
    x: int | float
    y: int | float
    num: int | float | None = None


@dataclass(frozen=True)
class IdLit:
    id: str
    num: int | float | None = None


@dataclass(frozen=True)
class Var:
    name: str


@dataclass(frozen=True)
class Param:
    # Raw 1-based bare positive int as decoded. Per docs/behavior_format.md, a bare positive int is
    # *unconditionally* a parameter/mem-slot reference -- even when the current behavior's own
    # `parameters` doesn't cover this slot (the "undeclared" case). Resolving the display name
    # (a real pname vs. a `param<i>`/`slot<i>(undeclared)` fallback) needs the current
    # behavior's `params` list and is a render-time concern, not a classification concern --
    # that's why `from_lua` below doesn't take a `params` argument at all.
    slot: int


@dataclass(frozen=True)
class FrameReg:
    # Raw (non-positive) int as decoded: -1/-2/-3/-4 are the symbolic Goto/Store/Visual/Signal
    # registers, any other non-positive value is a bare "@N" register.
    slot: int


@dataclass(frozen=True)
class Fr:
    # Faction (shared) register reference. The grammar allows an optional coexisting `num`
    # even though the real runtime (GetFactionBehaviorAsm) never reads one off this shape --
    # preserved for byte-exact round-tripping regardless.
    name: str
    num: int | float | None = None


@dataclass(frozen=True)
class Unknown:
    # Any value shape not (yet) enumerated above. Holds the raw lupa value/table verbatim so
    # round-tripping a real fixture stays possible even if it uses a shape this module doesn't
    # know about yet -- precedent: the `{"fr": ...}` shape was silently mishandled in the
    # decoder prototype this module replaces until caught by hand.
    raw: Any


BsfValue = Union[Num, Coord, IdLit, Var, Param, FrameReg, Fr, Unknown]


def from_lua(v) -> BsfValue:
    """Classify one raw decoded arg value (as returned by indexing a lupa instruction table)
    into the BSF value union. Only call this for an arg slot that is genuinely present (not
    None/absent) -- an absent arg is a decompile.py-level concern (it means the pin isn't
    wired to anything at all), not a value to classify here."""
    if isinstance(v, bool):
        return Unknown(v)
    if isinstance(v, (int, float)):
        return Param(v) if v > 0 else FrameReg(v)
    if isinstance(v, str):
        return Var(v)
    if lupa.lua_type(v) == "table":
        keys = set(v.keys())
        if "fr" in keys:
            return Fr(v["fr"], v["num"] if "num" in keys else None)
        if keys == {"num"}:
            return Num(v["num"])
        num = v["num"] if "num" in keys else None
        if "coord" in keys:
            c = v["coord"]
            if lupa.lua_type(c) == "table" and "x" in c.keys():
                cx, cy = c["x"], c["y"]
            else:
                cx, cy = c[1], c[2]  # legacy [x,y] array shape -- tolerated on read, never written
            return Coord(cx, cy, num)
        if "id" in keys:
            return IdLit(v["id"], num)
        if num is not None:
            return Num(num)
        return Unknown(v)
    return Unknown(v)


def to_lua(value: BsfValue, lua):
    """Build the raw wire-level value (a Python scalar, or a genuine lupa table for composites)
    `value` represents -- the inverse of `from_lua`. `lua` is the `lupa.LuaRuntime` used to
    build any composite table. Coordinates are always written in the hash-style `{x=,y=}` shape
    -- the array `[x,y]` shape `from_lua` tolerates on read is a known in-game corruption
    (docs/behavior_format.md's coordinate-literal gotcha) and is never intentionally reproduced."""
    if isinstance(value, Num):
        t = lua.table()
        t["num"] = value.n
        return t
    if isinstance(value, Coord):
        t = lua.table()
        c = lua.table()
        c["x"] = value.x
        c["y"] = value.y
        t["coord"] = c
        if value.num is not None:
            t["num"] = value.num
        return t
    if isinstance(value, IdLit):
        t = lua.table()
        t["id"] = value.id
        if value.num is not None:
            t["num"] = value.num
        return t
    if isinstance(value, Var):
        return value.name
    if isinstance(value, Param):
        return value.slot
    if isinstance(value, FrameReg):
        return value.slot
    if isinstance(value, Fr):
        t = lua.table()
        t["fr"] = value.name
        if value.num is not None:
            t["num"] = value.num
        return t
    if isinstance(value, Unknown):
        return value.raw
    raise TypeError(f"unrecognized BsfValue: {value!r}")
