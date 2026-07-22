"""Live-Lua-backed lookup of each op's real argument list (type/name/desc), and the shared
3-way exec-branch resolution rule. `data.instructions[op].args` (via a `LupaEngine`) is the
sole authoritative source for this -- never `docs/instructions_index.md`, which is auto-generated
FROM this same data for humans and is not meant to be parsed by tooling."""

from __future__ import annotations

import contextlib
import re

import lupa.lua54 as lupa

from ..assets import get_package_manifest, resolve_include
from ..lua_util import to_py
from .values import Param

# ops whose argument count/shape is dynamic (depends on the *target* sub-behavior's own
# declared parameters, not a fixed data.instructions[op].args list -- confirmed
# `data.instructions.call.args` is absent) and therefore need bespoke handling
# wherever ArgCache-driven position/type lookup would otherwise apply.
DYNAMIC_ARG_OPS = {"call", "load_behavior"}


def arg_type_name_desc(argdef):
    """An arg definition entry is either a positional list (`{type, name, desc}`) or a dict
    with the same fields keyed 1/2/3 -- both real shapes seen in `data.instructions`."""
    if isinstance(argdef, list):
        return (argdef + [None, None, None])[:3]
    if isinstance(argdef, dict):
        return argdef.get(1), argdef.get(2), argdef.get(3)
    return None, None, None


class ArgCache:
    """Caches each op's `data.instructions[op].args` list (converted to plain Python), keyed by
    op name, so repeated lookups across many instructions don't re-walk the Lua table."""

    def __init__(self, engine):
        self.engine = engine
        self._cache = {}
        self._next_pin_cache = {}

    def get(self, op):
        if op not in self._cache:
            d = self.engine.data.instructions[op]
            args = to_py(d.args) if d is not None and d.args is not None else []
            self._cache[op] = args if isinstance(args, list) else []
        return self._cache[op]

    def next_pin_name(self, op: str) -> str | None:
        """The display name for op's top-level `next` continuation pin -- or `None` if this op
        has no such pin at all, meaning it must never be shown as a branch note or a diagram
        edge, regardless of whatever value the wire happens to carry there.

        Sourced from `data.instructions[op].exec_arg`, a real field this project had not
        previously read (found via user correction, not guessed) -- separate from `args`,
        confirmed three-way from real data: `false` (`exit`/`restart`/`last`: the real in-game
        editor draws no pin at all here, since wiring anything after them is nonsensical --
        `exit`'s own `func` never even consults `next`); a `{1, "Name", desc}` table naming the
        top-level pin for real (e.g. `check_number`'s `{1, "If Equal", ...}` -- the exact same
        "top-level `next` secretly carries a real semantic outcome" fact
        docs/behavior_format.md's check_number gotcha already documents, now sourced from live data
        instead of a generic placeholder); or absent entirely, the common case, which keeps the
        generic "next"."""
        if op not in self._next_pin_cache:
            d = self.engine.data.instructions[op]
            ea = d.exec_arg if d is not None else None
            if ea is False:
                name = None
            elif ea is not None and lupa.lua_type(ea) == "table":
                keys = set(ea.keys())
                name = ea[2] if 2 in keys else "next"
            else:
                name = "next"
            self._next_pin_cache[op] = name
        return self._next_pin_cache[op]

    def op_exists(self, op: str) -> bool:
        return self.engine.data.instructions[op] is not None

    def display_name(self, op: str) -> str | None:
        """The instruction's visual-editor display name (`data.instructions[op].name`, e.g.
        `set_reg` -> "Copy", `check_number` -> "Compare Number") -- what the user actually sees
        on the node in-game, and therefore the vocabulary any user-facing reference to a node
        should be phrased in (BSF op ids and node ids are tooling-internal)."""
        d = self.engine.data.instructions[op]
        name = d.name if d is not None else None
        return name if isinstance(name, str) else None

    def all_ops(self) -> list[str]:
        """Every op id in the live `data.instructions` table -- used only for typo suggestions."""
        return [k for k in self.engine.data.instructions if isinstance(k, str)]

    def exec_pin_names(self, op: str) -> list[str]:
        """Every branch-pin display name this op can carry in BSF text: the declared exec args
        (occurrence-suffixed, in declaration order) plus the top-level next pin's display name
        last (which may be the literal generic "next"), or nothing at all for an op whose
        `exec_arg` is `false` and that declares no exec args (`exit`/`restart`/`last`)."""
        pins = [name for _, atype, name in arg_pin_names(op, self) if atype == "exec"]
        next_pin = self.next_pin_name(op)
        if next_pin is not None:
            pins.append(next_pin)
        return pins

    def value_arg_names(self, op: str) -> set[str]:
        return {name for _, atype, name in arg_pin_names(op, self) if atype != "exec"}

    def known_ids(self) -> frozenset[str]:
        """Every registered game id (`v_*` values, `c_*` components, `f_*` frames, items, techs,
        ...) -- the accept-set a bare identifier in BSF text is validated against, so a forgotten
        `$` sigil or a misspelled id fails loudly at parse time instead of silently compiling to
        a wrong id literal. Scanned once per engine from the Data package's own include files
        (the stubbed Lua runtime only executes `instructions.lua`, so the real registries are
        never populated in-process) and cached on the engine, which tests share session-wide."""
        return frozenset(self.id_display_names())

    def id_display_names(self) -> dict[str, str | None]:
        """id -> the in-game display name from its registration's own `name = "..."` field
        (`v_resource` -> "Resource", `c_radar` -> "Long-Range Radar"), or None when the
        registration doesn't declare one. Same scan and cache as `known_ids` -- internal ids are
        exactly as invisible in the game's UI as node ids are, and some are entirely opaque
        (`c_adv_miner` displays as "Laser Mining Tool"), so any user-facing mention of an id
        needs this translation available."""
        cached = getattr(self.engine, "_known_ids_cache", None)
        if cached is None:
            cached = _scan_registrations(self.engine.source, self.engine.package_id)
            self.engine._known_ids_cache = cached
        return cached

    def id_display_name(self, id_: str) -> str | None:
        return self.id_display_names().get(id_)


# `data.values.v_foo = {...}` / `data.items.metalore = {...}` style direct registrations, and
# the `RegisterComponent("c_foo", ...)`/`RegisterFrame("f_foo", ...)` builder style -- the two
# registration shapes observed across the whole Data package.
_DATA_ID_RE = re.compile(r"\bdata\.[a-z_]+\.([A-Za-z_][A-Za-z0-9_]*)\s*=")
_REGISTER_ID_RE = re.compile(r'Register(?:Component|Frame)\(\s*"([^"]+)"')
_INCLUDE_RE = re.compile(r'"([^"]+\.lua)"')
_NAME_FIELD_RE = re.compile(r'\bname\s*=\s*"([^"]*)"')


def _scan_registrations(source, package_id: str = "Data") -> dict[str, str | None]:
    """id -> display name (or None). The display name is the first `name = "..."` field between
    an id's registration and the next registration in the same file -- registrations are
    sequential table constructions, so that window is the entry's own body (a derived
    registration that inherits its name yields None; acceptable, the fallback is
    'no translation available')."""
    pm = get_package_manifest(source, package_id)
    entry_text = source.read_text(pm.entry)
    ids: dict[str, str | None] = {}
    for include in _INCLUDE_RE.findall(entry_text):
        path = resolve_include(pm.entry_dir, include)
        if not source.exists(path):
            continue
        text = source.read_text(path)
        matches = sorted(
            [
                (m.start(), m.group(1))
                for pat in (_DATA_ID_RE, _REGISTER_ID_RE)
                for m in pat.finditer(text)
            ]
        )
        for idx, (pos, id_) in enumerate(matches):
            end = matches[idx + 1][0] if idx + 1 < len(matches) else len(text)
            nm = _NAME_FIELD_RE.search(text, pos, end)
            # first registration wins on duplicates only if it carried a name
            if id_ not in ids or ids[id_] is None:
                ids[id_] = nm.group(1) if nm else ids.get(id_)
    return ids


def arg_pin_names(op: str, argcache: ArgCache) -> list[tuple[int, str, str]]:
    """Returns `[(position, atype, pin_name), ...]` for op's declared args, in position order.
    A few real ops declare two or more args with the *same* display name at different positions
    (e.g. `for_signal_match` has an "in" Signal at position 1 and an "out" Signal at position 3;
    `for_entities_in_range` declares THREE args all literally named "Filter" -- positions 2, 3,
    4, distinguished only by description text "Filter to check"/"Second Filter"/"Third Filter")
    -- BSF represents every arg as a unique `Name=value` pair, so the 2nd+ occurrence of a given
    base name is disambiguated with a suffix.

    The suffix is the occurrence COUNT (1st occurrence: bare name; 2nd: `Name2`; 3rd: `Name3`;
    ...), not the argument's raw wire position -- fixed 2026-07-10 after a real, confusing
    misreading it caused: `for_entities_in_range`'s *second* declared Filter sits at wire
    position 3, so suffixing by position rendered it as `Filter3`, which reads exactly like "the
    third Filter" to a human -- it's the second. This led directly to a wrong "the filter chain
    is broken, Filter3 is silently dropped" conclusion about a real user behavior that was
    actually correct as written (see the user's own real-world confirmation, project memory).
    Occurrence-based counting makes the display name match how a human actually reads "first
    Filter, second Filter, third Filter" in order, independent of wherever they happen to sit in
    the op's own positional argument list.

    Both decompile.py and compile.py call this (never duplicate the rule inline), so the same op
    always gets the same name<->position mapping in both directions without needing to store
    anything extra in the IR."""
    seen: dict[str, int] = {}
    result = []
    for i, argdef in enumerate(argcache.get(op), start=1):
        atype, aname, _ = arg_type_name_desc(argdef)
        base = aname or f"arg{i}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        name = base if count == 1 else f"{base}{count}"
        result.append((i, atype, name))
    return result


def call_arg_positions(b, node) -> dict[str, int]:
    """Resolve a call/load_behavior node's arg names back to their real wire positions, by
    re-deriving the same target-pnames lookup decompile.py's `_call_arg_names` used to build
    those names in the first place -- never cached, recomputed fresh here from the current `b`.
    Shared by compile.py (to place args back at their real position) and `written_param_slots`
    below (to check whether a call argument lines up with one of the *target*'s own written
    slots)."""
    sub = node.hidden.get("sub")
    target_params = None
    if isinstance(sub, (int, float)) and not isinstance(sub, bool):
        if sub == -1:
            target_params = b.params
        elif sub > 0 and int(sub) - 1 < len(b.subs):
            target_params = b.subs[int(sub) - 1].params

    positions: dict[str, int] = {}
    if target_params:
        for i, p in enumerate(target_params, start=1):
            positions[p.name] = i
    for name in node.args:
        if name not in positions and name.startswith("arg"):
            with contextlib.suppress(ValueError):
                positions[name] = int(name[3:])
    return positions


def _direct_written_slots(nodes: dict, argcache: ArgCache) -> set[int]:
    """Base case: parameter slots used as an "out"-typed argument directly within `nodes`,
    ignoring `call`/`load_behavior` entirely (their own arg directions come from the *target*'s
    params, not from ArgCache -- see `written_param_slots` for the passthrough case)."""
    written = set()
    for node in nodes.values():
        if node.op in DYNAMIC_ARG_OPS:
            continue
        for _, atype, name in arg_pin_names(node.op, argcache):
            if atype == "out":
                v = node.args.get(name)
                if isinstance(v, Param):
                    written.add(v.slot)
    return written


def written_param_slots(b, argcache: ArgCache, _in_progress: frozenset | None = None) -> set[int]:
    """Which of `b`'s own declared parameter slots are ever written to -- directly (an "out"-
    typed argument somewhere in `b`'s own body) or transitively, by being passed into a
    `call`/`load_behavior` node at a position the *target* sub-behavior itself writes.
    Computed fresh from actual usage every call, never stored on the IR (same never-cache
    policy as the `jump->label` annotation) -- this is BSF's real signal for a parameter's
    direction. The wire format's own `parameters[i]` bit is, user-confirmed, only a UI hint for
    which side of a `call` node's box a pin is drawn on in the visual editor; the runtime
    evaluation itself doesn't distinguish in/out at all.

    Resolved via fixpoint iteration (bounded by `len(b.params)` passes) rather than a single
    top-down walk, so it doesn't matter whether a `call` target is defined "before" or "after"
    the call site in `b.subs`, and a `sub=-1` recursive self-call works by treating the
    in-progress `written` set as its own target. `_in_progress` (by `id(b)`) guards against a
    genuine call cycle between *different* sub-behaviors (architecturally unlikely -- real
    `dependencies` arrays are built by embedding each called sub in turn -- but not provably
    impossible for a hand-edited one) by treating a revisit as contributing nothing new, rather
    than recursing forever.

    A `sub` that's an external (string) saved-library id can't be resolved this way --
    genuinely unknowable without that library's own definition, the same unresolvable-ness
    `decompile.py`'s own `_call_arg_names` already accepts for a call's arg *naming* in that
    case."""
    _in_progress = _in_progress or frozenset()
    if id(b) in _in_progress:
        return set()
    in_progress = _in_progress | {id(b)}

    written = _direct_written_slots(b.nodes, argcache)
    changed = True
    while changed:
        changed = False
        for node in b.nodes.values():
            if node.op not in DYNAMIC_ARG_OPS:
                continue
            sub = node.hidden.get("sub")
            if isinstance(sub, (int, float)) and not isinstance(sub, bool) and sub == -1:
                target_written = written  # self -- use the in-progress set directly
            elif (
                isinstance(sub, (int, float))
                and not isinstance(sub, bool)
                and sub > 0
                and int(sub) - 1 < len(b.subs)
            ):
                target_written = written_param_slots(b.subs[int(sub) - 1], argcache, in_progress)
            else:
                continue
            positions = call_arg_positions(b, node)
            for name, value in node.args.items():
                if (
                    isinstance(value, Param)
                    and positions.get(name) in target_written
                    and value.slot not in written
                ):
                    written.add(value.slot)
                    changed = True
    return written


def resolve_branch(val, idx, insts):
    """Resolve one exec-typed slot's raw wire value (the top-level `next` field, or a numbered
    exec arg like check_number's "If Larger") to the value `BsfNode.branches[pin]` should hold,
    per docs/behavior_source_format.md's "Control edges" 3-way rule:

    - explicit int wire position -> returned as-is (an int); the caller maps this to the
      destination node's NODE_ID once the full idx->id table is built.
    - explicit `False` -> "POP" (a real, meaningful authorial choice: pop the current context
      frame -- the innermost active loop iteration or call invocation -- and resume via
      whatever that frame's own continuation logic does; if no frame remains, the engine
      automatically pushes a fresh one and restarts from Program Start, not a separate case,
      just the unremarkable consequence of popping with nothing left. Never a bare "halt" --
      that's `exit`, a genuinely distinct, always-explicit instruction).
    - omitted entirely (val is None) -> `None` if the physically-next instruction exists.
      **This is not "no decision was made, defaulting arbitrarily" -- it's a real, explicit
      wire the real editor's compiler just didn't need to spell out as an int, because it's
      redundant with position** (user-confirmed, not assumed): the compiler only ever omits
      `next` when there genuinely is a connection to the node it placed immediately after this
      one; a pin left truly unconnected in the editor gets explicit `false` written
      unconditionally, regardless of what happens to physically follow. So this branch is
      never reached for a "nothing was wired, but something happens to follow anyway" case --
      that case doesn't exist in real data, because it would have `false` written, which the
      check above already caught. If the physically-next instruction does *not* exist (true
      end of the array), falls through to "POP" below -- falling off the true end is a real
      dead end too, handled identically to an explicit `False` by the real dispatcher.
      BSF renders no annotation for the omitted case (see docs/behavior_source_format.md's "Control
      edges"), and -- a deliberate choice for BSF's own text editability, not a claim about
      what the original wire was -- treats it as following physical order on a reorder, the
      same way any sequential-code language would treat an unannotated fallthrough; it does
      not try to preserve "this specific wire went to this specific node" across a reorder.
      See docs/behavior_source_format.md's "Node identity vs. wire position" for that trade-off.

    `insts` is the raw idx->instruction-dict mapping for the *current* behavior/sub being
    decompiled, used only to check whether idx+1 exists."""
    if type(val) is int:
        return val
    if val is False:
        return "POP"
    if idx + 1 in insts:
        return None
    return "POP"
