"""Phase 1 of the BSF pipeline (see /home/johnfb/.claude/plans/moonlit-nibbling-sonnet.md):
real Lua table <-> BsfBehavior IR, with no text layer involved yet. Isolates "does the
graph/wire-position/branch-resolution logic work" from the text grammar (tested separately in
test_bsf_text_roundtrip.py)."""

from pathlib import Path

import pytest

from blz.desynced_toolkit.bsf.argcache import DYNAMIC_ARG_OPS, ArgCache, arg_pin_names
from blz.desynced_toolkit.bsf.compile import compile_behavior
from blz.desynced_toolkit.bsf.decompile import decompile_behavior, decompile_dcs
from blz.desynced_toolkit.bsf.ir import BsfBehavior, BsfNode, BsfParam
from blz.desynced_toolkit.bsf.values import Num, Param
from blz.desynced_toolkit.lua_util import to_py

DATA_DIR = Path(__file__).parent / "data"

REAL_DCS_FILES = [
    "observer.dcs",
    "beacon.dcs",
    "beacon2.dcs",
    "formation-hold.dcs",
    "hexat_test.dcs",
    "HexIndexOf_test_1.dcs",
    "keepvars_clear.dcs",
    "keepvars_keep.dcs",
    "deprecated_haul_to_signal.dcs",
    "mining_leader.dcs",
    "adversarial_text_stress.dcs",
]


def _strip_layout(v):
    """nx/ny (node position) and cmt (free-text node comment) are the envelope/sidecar layer
    this plan explicitly defers -- see the plan's "Deferred" section. Strip them from both
    sides before comparing so real fixtures (which do carry them) don't spuriously fail a
    round-trip check that's only about the instruction graph itself."""
    if isinstance(v, dict):
        return {k: _strip_layout(x) for k, x in v.items() if k not in ("nx", "ny", "cmt")}
    if isinstance(v, list):
        return [_strip_layout(x) for x in v]
    return v


def _strip_unused_bool_args(v, argcache: ArgCache):
    """A bare bool (true or false) at a non-exec arg slot is compiler-equivalent to that key
    being omitted entirely -- confirmed against GetFactionBehaviorAsm (data/library.lua): its
    arg-resolution loop only recognizes table/number/string val_types for a non-exec arg,
    everything else (nil OR a bare bool) falls through to the identical "unused argument" case.
    decompile.py normalizes an explicit bool at such a slot to "absent" on read (see its own
    comment, found via `deprecated_haul_to_signal.dcs`'s `have_item` node, which has the real
    game's editor writing an explicit `false` for its unwired optional "Unit" arg rather than
    omitting the key) -- so a recompile legitimately drops it even when the original wire had it
    explicitly. This mirrors that same normalization here so the round-trip check compares the
    two encodings as the compiler-equivalent forms they are, not byte-for-byte structural copies."""
    if isinstance(v, dict):
        if "op" in v:
            op = v["op"]
            if op in DYNAMIC_ARG_OPS:
                exec_positions = set()
            else:
                exec_positions = {i for i, atype, _ in arg_pin_names(op, argcache) if atype == "exec"}
            v = {
                k: x
                for k, x in v.items()
                if not (isinstance(k, int) and k not in exec_positions and isinstance(x, bool))
            }
        return {k: _strip_unused_bool_args(x, argcache) for k, x in v.items()}
    if isinstance(v, list):
        return [_strip_unused_bool_args(x, argcache) for x in v]
    return v


@pytest.mark.parametrize("fname", REAL_DCS_FILES)
def test_fixture_roundtrips_through_ir(engine, fname):
    argcache = ArgCache(engine)
    raw = (DATA_DIR / fname).read_text().strip()
    _, orig_table = engine.decode_dcs(raw)
    b = decompile_dcs(engine, raw)
    recompiled = compile_behavior(engine, b)
    got = _strip_unused_bool_args(_strip_layout(to_py(recompiled)), argcache)
    want = _strip_unused_bool_args(_strip_layout(to_py(orig_table)), argcache)
    assert got == want


def test_reorder_moves_implicit_fallthrough_but_not_explicit_target(engine):
    """docs/behavior_source_format.md's "Node identity vs. wire position": reordering nodes in
    `order` is supposed to change what an *omitted* branch's physically-next target is (that's
    the whole point of decoupling node identity from wire position), while an *explicit*
    target keeps pointing at the same node regardless of where it now sits. Three set_reg
    nodes, distinguished by their literal Value, wired A->(implicit)->B->(implicit)->C->
    (explicit)->A."""
    argcache = ArgCache(engine)

    def node(node_id, marker, next_branch):
        n = BsfNode(id=node_id, op="set_reg")
        n.args["Value"] = Num(marker)
        n.args["Target"] = Param(1)
        n.branches["next"] = next_branch
        return n

    a = node("A", 1, None)  # implicit fallthrough
    b_node = node("B", 2, None)  # implicit fallthrough
    c = node("C", 3, "A")  # explicit, always points at A

    def marker_at(prog, pos):
        return prog[pos][1]["num"]

    def compile_with_order(order):
        behavior = BsfBehavior(name="Reorder", nodes={"A": a, "B": b_node, "C": c}, order=order)
        return compile_behavior(engine, behavior, argcache)

    orig = compile_with_order(["A", "B", "C"])
    assert marker_at(orig, 1) == 1  # A
    assert marker_at(orig, 2) == 2  # B -- A's implicit fallthrough target
    assert set(orig[1].keys()) == {"op", 1, 2}  # A: no explicit "next" key at all
    assert orig[3]["next"] == 1  # C's explicit target -- A's position

    reordered = compile_with_order(["B", "A", "C"])
    pos_a = 2  # A is now second
    assert marker_at(reordered, pos_a) == 1
    assert marker_at(reordered, pos_a + 1) == 3  # A's implicit fallthrough now reaches C, not B
    assert set(reordered[pos_a].keys()) == {"op", 1, 2}  # still no explicit "next" key
    assert reordered[3]["next"] == pos_a  # C's explicit target tracked A to its new position


def test_sub_behavior_node_id_namespaces_are_independent(engine):
    """Two different `subs` entries reusing the same node id "n1" must not cross-contaminate --
    each _compile_one call builds its own local id->position map (see compile.py)."""
    argcache = ArgCache(engine)

    def one_node_behavior(name, marker):
        n = BsfNode(id="n1", op="set_reg")
        n.args["Value"] = Num(marker)
        n.args["Target"] = Param(1)
        return BsfBehavior(name=name, nodes={"n1": n}, order=["n1"])

    top = one_node_behavior("Top", 100)
    top.subs = [one_node_behavior("Sub1", 1), one_node_behavior("Sub2", 2)]

    prog = compile_behavior(engine, top, argcache)
    assert prog[1][1]["num"] == 100
    assert prog["dependencies"][1][1][1]["num"] == 1
    assert prog["dependencies"][2][1][1]["num"] == 2


@pytest.mark.parametrize("sub_value", [-1, "some_external_library_id"])
def test_call_hidden_sub_field_roundtrips(engine, sub_value):
    """call's `sub` field in its two shapes not exercised by the real fixtures (which only use
    a positive dependency-index int): -1 (recursive self-call) and a string (external
    saved-library id, genuinely unresolvable -- falls back to arg1/arg2 naming)."""
    argcache = ArgCache(engine)
    call_node = BsfNode(id="n1", op="call")
    call_node.hidden["sub"] = sub_value
    if sub_value == -1:
        # Self-recursive: arg name resolves against this behavior's own declared params.
        call_node.args["X"] = Num(5)
        params = [BsfParam(name="X")]
    else:
        # External string id: no pnames visible anywhere, generic arg1 fallback.
        call_node.args["arg1"] = Num(7)
        params = []

    behavior = BsfBehavior(name="CallTest", params=params, nodes={"n1": call_node}, order=["n1"])
    compiled = compile_behavior(engine, behavior, argcache)
    assert compiled[1]["sub"] == sub_value
    assert compiled[1][1]["num"] == (5 if sub_value == -1 else 7)

    roundtripped = decompile_behavior(engine, compiled, argcache)
    assert roundtripped.nodes["n1"].hidden["sub"] == sub_value
    assert roundtripped.nodes["n1"].args == call_node.args


def test_undeclared_parameter_slot_bare_int(engine):
    """A bare positive int arg value is *always* a parameter/mem-slot reference (never a plain
    number -- docs/behavior_format.md), even when the behavior's own `parameters` table doesn't
    cover that slot at all (a real, expected case for partial-selection clipboard fragments).
    `from_lua`'s classification doesn't depend on `parameters` existing -- confirm decompile
    produces `Param(5)` regardless, and it round-trips back to the same bare int."""
    lua = engine.lua
    t = lua.table()
    inst = lua.table()
    inst["op"] = "set_reg"
    inst[1] = 5  # bare int -- a slot/parameter reference, not present in any `parameters` table
    t[1] = inst
    t["name"] = "NoParams"
    # deliberately no t["parameters"] at all

    b = decompile_behavior(engine, t)
    assert b.params == []
    assert b.nodes["n1"].args["Value"] == Param(5)

    recompiled = compile_behavior(engine, b)
    assert recompiled[1][1] == 5
    assert set(recompiled.keys()) == {1, "name"}  # no parameters/pnames emitted


def test_written_param_slots_propagates_through_call(engine):
    """A parameter only ever passed *through* a `call` to a callee's own written slot must
    still count as written by the caller -- this is exactly the real case `hexat_test.dcs`'s
    top-level harness hits (its own `Result` param is never directly used as an "out" arg
    anywhere in the harness itself, only passed to HexAt's own `Result`), which is what first
    caught this needing fixpoint propagation rather than a single direct-usage scan."""
    argcache = ArgCache(engine)

    # Callee: Out is genuinely written (set_reg's Target is an "out" arg); In is only read.
    callee = BsfBehavior(
        name="Callee",
        params=[BsfParam(name="In"), BsfParam(name="Out")],
        nodes={"n1": BsfNode(id="n1", op="set_reg", args={"Value": Param(1), "Target": Param(2)})},
        order=["n1"],
    )
    call_node = BsfNode(id="n1", op="call")
    call_node.hidden["sub"] = 1
    call_node.args = {"In": Num(1), "Out": Param(1)}
    caller = BsfBehavior(
        name="Caller",
        params=[BsfParam(name="Result")],
        nodes={"n1": call_node},
        order=["n1"],
        subs=[callee],
    )

    compiled = compile_behavior(engine, caller, argcache)
    assert list(compiled["parameters"].values()) == [True]  # Result -- written only via passthrough


def test_written_param_slots_self_recursive_call_does_not_infinite_loop(engine):
    """`sub=-1` (recursive self-call) must resolve against the in-progress computation of the
    behavior's own written set, not recurse forever."""
    argcache = ArgCache(engine)
    call_node = BsfNode(id="n2", op="call")
    call_node.hidden["sub"] = -1
    call_node.args = {"Out": Param(1)}
    writer = BsfNode(id="n1", op="set_reg", args={"Value": Num(1), "Target": Param(2)})
    behavior = BsfBehavior(
        name="SelfRecursive",
        params=[BsfParam(name="A"), BsfParam(name="Out")],
        nodes={"n1": writer, "n2": call_node},
        order=["n1", "n2"],
    )
    compiled = compile_behavior(engine, behavior, argcache)
    assert list(compiled["parameters"].values()) == [True, True]


def test_decompile_dcs_rejects_non_behavior_type_char(engine):
    """A blueprint ('B') decodes to a {frame, components, ...} shape, not a behavior --
    decompile_behavior would silently render it as an empty behavior (dropping every
    frame/component), so decompile_dcs must reject any wire type other than 'C' loudly."""
    lua = engine.lua
    t = lua.table()
    t["frame"] = "f_bot_1m"
    blueprint_dcs = engine.encode_dcs("B", t)
    with pytest.raises(ValueError, match="wire type 'B'"):
        decompile_dcs(engine, blueprint_dcs)
