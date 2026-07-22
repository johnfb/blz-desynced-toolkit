"""Real Lua behavior table -> BsfBehavior. The reverse of bsf/compile.py."""

from __future__ import annotations

import re

import lupa.lua54 as lupa

from .argcache import DYNAMIC_ARG_OPS, ArgCache, arg_pin_names, resolve_branch
from .ir import BsfBehavior, BsfNode, BsfParam
from .render_text import referenced_node_ids
from .values import IdLit, Num, Var, from_lua

# docs/behavior_format.md's "Hidden literal fields (make_asm)" table: op -> the plain named key
# (not part of data.instructions[op].args, so ArgCache never sees it) that op uses for its one
# configured-on-the-node value.
HIDDEN_FIELD_TABLE = {
    "call": "sub",
    "load_behavior": "sub",
    "domove": "c",
    "moveaway_range": "c",
    "scout": "c",
    "dodrop": "c",
    "dopickup": "c",
    "request_item": "c",
    "request_wait": "c",
    "for_producers_items": "c",
    "bitwise_op": "c",
    "lock_slots": "c",
    "for_signal_match": "c",
    "count_item": "c",
    "get_unit_info": "c",
    "get_unit_power_info": "c",
    "get_item_info": "c",
    "count_slots": "c",
    "notify": "txt",
    "set_signpost": "txt",
}


def _int_keys(table) -> list[int]:
    return sorted(k for k in table if isinstance(k, int))


def _sub_behaviors_table(table):
    """Both `dependencies` (standalone exported behavior) and `subs` (behavior embedded in a
    blueprint's c_behavior component) have been observed for the same concept in real corpus
    data and have not been reconciled -- treat both as the same thing."""
    keys = set(table.keys())
    if "dependencies" in keys:
        return table["dependencies"]
    if "subs" in keys:
        return table["subs"]
    return None


def _params_from_table(table) -> list[BsfParam]:
    """Slot count and names are trusted straight from the wire declaration (`parameters`'s own
    length, `pnames` for display names) -- but NOT `parameters[i]`'s truthy/falsy bit itself,
    which is only a UI-drawing hint (see `argcache.written_param_slots`'s docstring), not
    something this IR stores at all. Direction is computed fresh from usage wherever it's
    actually needed (rendering, compiling), never here."""
    keys = set(table.keys())
    if "parameters" not in keys:
        return []
    parameters = table["parameters"]
    pnames = table["pnames"] if "pnames" in keys else None
    result = []
    for i in _int_keys(parameters):
        name = pnames[i] if pnames is not None and i in set(pnames.keys()) else f"param{i}"
        result.append(BsfParam(name=name))
    return result


def _call_arg_names(table, inst, argcache: ArgCache) -> dict[int, str]:
    """Resolve display names for a call/load_behavior instruction's dynamic positional args,
    per docs/behavior_source_format.md's 3-way `sub` rule. Returns {position: name}; a position not
    resolvable (no visible pnames) gets the generic `arg{position}` fallback."""
    sub = inst["sub"] if "sub" in set(inst.keys()) else False
    target_pnames = None
    if isinstance(sub, bool):
        pass  # `false`/unset -- nothing to resolve against
    elif isinstance(sub, (int, float)) and sub == -1:
        target_pnames = table["pnames"] if "pnames" in set(table.keys()) else None
    elif isinstance(sub, (int, float)) and sub > 0:
        subs_table = _sub_behaviors_table(table)
        if subs_table is not None and int(sub) in set(subs_table.keys()):
            target = subs_table[int(sub)]
            target_pnames = target["pnames"] if "pnames" in set(target.keys()) else None
    # else: string (external saved-library id) -- genuinely unresolvable, no pnames visible

    names = {}
    for i in _int_keys(inst):
        if target_pnames is not None and i in set(target_pnames.keys()):
            names[i] = target_pnames[i]
        else:
            names[i] = f"arg{i}"
    return names


_ID_PREFIXES = ("v_", "c_", "f_", "t_")


def _slug(s: str) -> str:
    return re.sub(r"\W+", "_", s).strip("_") or "x"


def _value_slug(v) -> str:
    """A short, human-meaningful slug for a value used to name a node after its role -- mainly a
    `label` node's own `Label` (`v_transport_route` -> `transport_route`, `v_broken[num=1]` ->
    `broken_1`, `$State` -> `State`). Not round-trip data, purely the descriptive-id source."""
    if isinstance(v, IdLit):
        base = v.id
        for p in _ID_PREFIXES:
            if base.startswith(p):
                base = base[len(p) :]
                break
        if v.num is not None:
            base = f"{base}_{v.num}"
        return _slug(base)
    if isinstance(v, Num):
        return _slug(str(v.n).replace("-", "neg"))
    if isinstance(v, Var):
        return _slug(v.name)
    return "x"


def _base_slug(node: BsfNode) -> str:
    """The role-derived base name for a node that earns a surface id (docs/behavior_source_format.md's
    decided scheme, 2026-07-20): a `label` node is named after its `Label` value (for a label the
    dispatch key genuinely is its identity), every other node after its op -- which is exactly
    what reads well at the reference site (`>engage_target (If Larger)`). NOT named after what
    jumps to it: fan-in has no single predecessor, and a predecessor-derived name would move on
    an unrelated edit -- the instability this whole change removes."""
    if node.op == "label":
        v = node.args.get("Label")
        return "label_" + _value_slug(v) if v is not None else "label"
    return node.op


def _assign_descriptive_ids(nodes: dict[str, BsfNode], order: list[str]) -> tuple[dict, list[str]]:
    """Replace the positional `n{idx}` decompile ids with role-derived ones, and mark
    `id_explicit` = "is this node actually referenced". Only referenced nodes (branch/jump
    targets) get a descriptive, surface-visible id; every other node keeps a positional id that
    render_text.py won't print. Same-base collisions among referenced nodes get an occurrence
    suffix (`set_reg`, `set_reg_2`) in wire order -- rare (most targets are labels named by a
    unique Label value), and fully wire-order-independent disambiguation is the sequenced
    canonical-decompile follow-up (todo)."""
    # A `label` node is a dispatch target by nature -- a dynamic `jump(Label=$x)` no static walk
    # resolves can still land on it -- so it always earns its descriptive id, even when nothing
    # statically references it. (lint.py exempts labels from the unreferenced-id warning for the
    # same reason.)
    should_id = referenced_node_ids(nodes) | {nid for nid, n in nodes.items() if n.op == "label"}
    used = {nid for nid in order if nid not in should_id}  # reserve the kept positional ids
    rename: dict[str, str] = {}
    explicit: dict[str, bool] = {}
    for nid in order:
        if nid not in should_id:
            rename[nid] = nid
            explicit[nid] = False
            continue
        base = _base_slug(nodes[nid])
        cand, k = base, 2
        while cand in used:
            cand = f"{base}_{k}"
            k += 1
        used.add(cand)
        rename[nid] = cand
        explicit[nid] = True

    def remap(t):
        return t if not isinstance(t, str) or t == "POP" else rename.get(t, t)

    new_nodes: dict[str, BsfNode] = {}
    new_order: list[str] = []
    for nid in order:
        node = nodes[nid]
        node.id = rename[nid]
        node.id_explicit = explicit[nid]
        node.branches = {pin: remap(t) for pin, t in node.branches.items()}
        new_nodes[node.id] = node
        new_order.append(node.id)
    return new_nodes, new_order


def decompile_behavior(engine, table, argcache: ArgCache | None = None) -> BsfBehavior:
    """Decompile one real Lua behavior table (as returned by dcs_wire.decode_dcs, or one entry
    of a `dependencies`/`subs` array) into a BsfBehavior graph. Recurses into embedded
    sub-behaviors automatically -- each gets its own independent NODE_ID namespace."""
    argcache = argcache or ArgCache(engine)
    keys = set(table.keys())

    insts = {}
    for idx in _int_keys(table):
        v = table[idx]
        if lupa.lua_type(v) == "table":
            insts[idx] = v

    idx_to_id = {idx: f"n{idx}" for idx in insts}

    nodes: dict[str, BsfNode] = {}
    order: list[str] = []
    for idx in sorted(insts):
        inst = insts[idx]
        inst_keys = set(inst.keys())
        op = inst["op"]
        node_id = idx_to_id[idx]
        node = BsfNode(id=node_id, op=op)

        if op in DYNAMIC_ARG_OPS:
            names = _call_arg_names(table, inst, argcache)
            for i in _int_keys(inst):
                if not isinstance(inst[i], bool):  # see the bool-is-really-absent note below
                    node.args[names[i]] = from_lua(inst[i])
        else:
            for i, atype, pin in arg_pin_names(op, argcache):
                if atype == "exec":
                    # Always resolve, whether the key is present or not -- an absent exec key
                    # still needs the omitted/falls-off-the-end distinction from resolve_branch,
                    # not just a skip (a value arg with an absent key genuinely has nothing to
                    # record, but an exec pin's *absence* is itself meaningful).
                    target = resolve_branch(inst[i] if i in inst_keys else None, idx, insts)
                    node.branches[pin] = idx_to_id[target] if isinstance(target, int) else target
                elif i in inst_keys and not isinstance(inst[i], bool):
                    # A bare bool (true or false) at a non-exec arg slot is NOT a real value --
                    # confirmed against GetFactionBehaviorAsm (data/library.lua ~line 75): its
                    # arg-resolution loop only recognizes table/number/string val_types for a
                    # non-exec arg; anything else (nil OR a bare bool) falls through to the same
                    # `else: asmarg = false` ("unused argument") branch. So an omitted key and an
                    # explicit `false` (or, for that matter, `true`) compile to byte-identical
                    # results here -- unlike the exec-arg case just above, where omission and
                    # explicit `false` are NOT interchangeable (see resolve_branch). Found via a
                    # real fixture (`have_item`'s optional "Unit" arg, explicitly `false` rather
                    # than omitted) that the BSF text layer couldn't round-trip: rendered via the
                    # generic `Unknown` escape hatch (no text syntax, so reparsing failed) before
                    # this fix -- treating it as absent instead is both round-trippable and, per
                    # the compiler equivalence just cited, the more correct classification anyway.
                    node.args[pin] = from_lua(inst[i])

        next_target = resolve_branch(inst["next"] if "next" in inst_keys else None, idx, insts)
        node.branches["next"] = (
            idx_to_id[next_target] if isinstance(next_target, int) else next_target
        )

        hidden_field = HIDDEN_FIELD_TABLE.get(op)
        if hidden_field is not None and hidden_field in inst_keys:
            node.hidden[hidden_field] = inst[hidden_field]
        if "cmt" in inst_keys:
            node.hidden["cmt"] = inst["cmt"]

        nodes[node_id] = node
        order.append(node_id)

    nodes, order = _assign_descriptive_ids(nodes, order)

    subs = []
    subs_table = _sub_behaviors_table(table)
    if subs_table is not None:
        for i in _int_keys(subs_table):
            subs.append(decompile_behavior(engine, subs_table[i], argcache))

    return BsfBehavior(
        name=table["name"] if "name" in keys else "",
        params=_params_from_table(table),
        # Real wire-level field after all -- the original 6 fixtures just never happened to set
        # it (same story as keepvars/keeparrays); found via `deprecated_haul_to_signal.dcs`,
        # which has a real top-level `desc` sibling to `name`. render_text.py/parse_text.py
        # already fully supported `desc` from the start (built directly off the spec's grammar,
        # which always had a `desc:` line) -- only this read was ever missing.
        desc=table["desc"] if "desc" in keys else None,
        keepvars=bool(table["keepvars"]) if "keepvars" in keys else False,
        keeparrays=table["keeparrays"] if "keeparrays" in keys else None,
        nodes=nodes,
        order=order,
        subs=subs,
    )


def decompile_dcs(engine, dcs_str: str) -> BsfBehavior:
    type_char, table = engine.decode_dcs(dcs_str)
    if type_char != "C":
        # A blueprint ('B') decodes to a {frame, components, ...} shape, not a behavior --
        # decompile_behavior would silently render it as an empty behavior plus whatever
        # `dependencies` it carries, dropping every frame/component. Fail loudly instead;
        # use LupaEngine.decode_dcs directly to inspect a non-'C' string.
        raise ValueError(
            f"not a behavior clipboard string: wire type {type_char!r} (expected 'C'); "
            "blueprints and other item types are not decompilable to BSF"
        )
    return decompile_behavior(engine, table)
