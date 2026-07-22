"""BsfBehavior -> real Lua table (the "assembler" pass). The reverse of bsf/decompile.py."""

from __future__ import annotations

from .argcache import DYNAMIC_ARG_OPS, ArgCache, arg_pin_names, call_arg_positions, written_param_slots
from .ir import BsfBehavior
from .values import to_lua


class BsfCompileError(ValueError):
    """IR-level validation failure. parse_text.py already rejects most of these for text input;
    these checks exist so hand-built IR (tests, future tooling) fails just as loudly instead of
    silently compiling wrong -- notably an unknown branch-pin key, which an earlier version
    never consulted (the compile loop iterates declared pins and checks membership, so a
    typo'd key was dropped without a trace; demonstrated 2026-07-14, see
    tests/test_bsf_validation.py)."""


def _resolve_branch_target(target, positions: dict[str, int], is_last: bool):
    """`target` is what BsfNode.branches[pin] holds: a NODE_ID, "POP", or None. Returns the
    raw wire value to write (an int position, or False), or the sentinel `_OMIT` meaning "don't
    set this key at all".

    The implicit-fallthrough case (`None`) never needs an explicit value -- the engine's own
    dispatcher advances to the physically next instruction on its own when a key is
    absent. `"POP"` is ambiguous by construction (docs/behavior_source_format.md's decompiler
    collapses an explicit `false` and "fell off the true end of the array" into the same `POP`
    representation, since the real dispatcher treats them identically -- see `resolve_branch`)
    -- when this node is genuinely the last one in the compiled order, omitting the key
    reproduces that same "fell off the end" case exactly (and matches how real .dcs files are
    usually encoded); anywhere else in the middle of the array, `false` must be written
    explicitly, since a next instruction physically following would otherwise change
    the meaning to "fall through to it"."""
    if target is None:
        return _OMIT
    if target == "POP":
        return _OMIT if is_last else False
    if target not in positions:
        raise BsfCompileError(f"branch targets unknown node {target!r}")
    return positions[target]


_OMIT = object()


def _compile_one(engine, b: BsfBehavior, argcache: ArgCache, lua) -> "lupa._LuaTable":
    # Fresh id->position map, local to this call -- never threaded into a recursive `subs` call,
    # so nested sub-behaviors never leak into each other's (or the parent's) node-id namespace.
    positions = {node_id: i for i, node_id in enumerate(b.order, start=1)}

    last_pos = len(b.order)

    prog = lua.table()
    for node_id, pos in positions.items():
        node = b.nodes[node_id]
        is_last = pos == last_pos
        t = lua.table()
        t["op"] = node.op

        if not argcache.op_exists(node.op):
            raise BsfCompileError(f"node {node.id!r}: unknown instruction op {node.op!r}")

        declared_exec: set[str] = set()
        if node.op in DYNAMIC_ARG_OPS:
            arg_positions = call_arg_positions(b, node)
            for name, value in node.args.items():
                if name not in arg_positions:
                    raise BsfCompileError(
                        f"node {node.id!r} ({node.op}): argument {name!r} matches no parameter "
                        f"of the call target (known: {', '.join(sorted(arg_positions)) or 'none'})"
                    )
                t[arg_positions[name]] = to_lua(value, lua)
        else:
            arg_pos = {}
            for i, atype, pin in arg_pin_names(node.op, argcache):
                if atype != "exec":
                    arg_pos[pin] = i
                    continue
                declared_exec.add(pin)
                if pin in node.branches:
                    try:
                        resolved = _resolve_branch_target(node.branches[pin], positions, is_last)
                    except BsfCompileError as e:
                        raise BsfCompileError(f"node {node.id!r} pin {pin!r}: {e}") from None
                    if resolved is not _OMIT:
                        t[i] = resolved
            for name, value in node.args.items():
                if name not in arg_pos:
                    raise BsfCompileError(
                        f"node {node.id!r} ({node.op}): unknown argument {name!r} "
                        f"(valid: {', '.join(sorted(arg_pos)) or 'none'})"
                    )
                t[arg_pos[name]] = to_lua(value, lua)

        for pin in node.branches:
            if pin != "next" and pin not in declared_exec:
                raise BsfCompileError(
                    f"node {node.id!r} ({node.op}): unknown branch pin {pin!r} "
                    f"(valid: {', '.join(sorted(declared_exec | {'next'}))})"
                )

        try:
            next_resolved = _resolve_branch_target(node.branches.get("next"), positions, is_last)
        except BsfCompileError as e:
            raise BsfCompileError(f"node {node.id!r} pin 'next': {e}") from None
        if next_resolved is not _OMIT:
            t["next"] = next_resolved

        for field_name, value in node.hidden.items():
            t[field_name] = value

        prog[pos] = t

    prog["name"] = b.name

    if b.desc:
        prog["desc"] = b.desc

    if b.params:
        written = written_param_slots(b, argcache)
        parameters = lua.table()
        for i, p in enumerate(b.params, start=1):
            parameters[i] = i in written
        prog["parameters"] = parameters
        # pnames is only written back if some name is a genuine custom display name -- a slot
        # whose name is exactly decompile.py's own `param{i}` fallback format round-trips as
        # "wire never had pnames at all" instead of manufacturing one, matching real fixtures
        # (many real behaviors declare `parameters` with no `pnames` at all). An inherent, small
        # ambiguity: a param a user genuinely named literally "param1" is indistinguishable from
        # the fallback and won't round-trip its pnames entry either -- accepted, not fixable
        # without storing "was pnames present" on the IR, which the IR deliberately doesn't do.
        if any(p.name != f"param{i}" for i, p in enumerate(b.params, start=1)):
            pnames = lua.table()
            for i, p in enumerate(b.params, start=1):
                pnames[i] = p.name
            prog["pnames"] = pnames

    if b.keepvars:
        prog["keepvars"] = True

    if b.keeparrays:
        prog["keeparrays"] = b.keeparrays

    if b.subs:
        deps = lua.table()
        for i, sub in enumerate(b.subs, start=1):
            deps[i] = _compile_one(engine, sub, argcache, lua)
        prog["dependencies"] = deps

    return prog


def compile_behavior(engine, b: BsfBehavior, argcache: ArgCache | None = None):
    argcache = argcache or ArgCache(engine)
    return _compile_one(engine, b, argcache, engine.lua)


def compile_dcs(engine, b: BsfBehavior, type_char: str = "C") -> str:
    table = compile_behavior(engine, b)
    return engine.encode_dcs(type_char, table)
