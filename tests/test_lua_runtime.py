"""Confirms the lupa-backed engine correctly drives the *real* `data/instructions.lua` funcs
(add/sub/mul/div/modulo/check_number/set_reg/separate_coordinate/combine_coordinate/jump/label),
including the real `REG_INFINITE` handling in `check_number` -- see engine_stub.lua's module
docstring for the `Get`/`Set`/`InstGet`/`InstSet`/`GetStack` discovery this all depends on.
"""

from blz.desynced_toolkit.lua_runtime import LupaEngine, Memory


def _lua_len(tbl) -> int:
    """Count entries in a (string-keyed) Lua table -- `#tbl` only counts the array part."""
    n = 0
    for _ in tbl.items():
        n += 1
    return n


def test_arithmetic(engine):
    state = engine.new_state()
    comp = engine.new_comp()
    mem = Memory(engine, state)

    a = mem.literal(num=3)
    b = mem.literal(num=4)

    res = mem.var("res")
    engine.call("add", comp, state, a, b, res)
    assert mem.read(res).num == 7

    res2 = mem.var("res2")
    engine.call("sub", comp, state, a, b, res2)
    assert mem.read(res2).num == -1

    res3 = mem.var("res3")
    engine.call("mul", comp, state, a, b, res3)
    assert mem.read(res3).num == 12

    c = mem.literal(num=10)
    res4 = mem.var("res4")
    engine.call("div", comp, state, c, a, res4)
    assert mem.read(res4).num == 3  # floor division

    res5 = mem.var("res5")
    engine.call("modulo", comp, state, c, a, res5)
    assert mem.read(res5).num == 1  # 10 % 3

    neg = mem.literal(num=-1)
    res6 = mem.var("res6")
    engine.call("modulo", comp, state, neg, a, res6)
    assert mem.read(res6).num == 2  # -1 % 3: floored (Lua `%`), not truncated


def test_coordinate_roundtrip(engine):
    state = engine.new_state()
    comp = engine.new_comp()
    mem = Memory(engine, state)

    c = mem.literal(coord=(5, 9))
    x, y = mem.var("x"), mem.var("y")
    engine.call("separate_coordinate", comp, state, c, x, y)
    assert mem.read(x).num == 5
    assert mem.read(y).num == 9

    out = mem.var("out")
    engine.call("combine_coordinate", comp, state, x, y, out)
    result = mem.read(out)
    assert (result.coord.x, result.coord.y) == (5, 9)


def test_check_number_basic(engine):
    state = engine.new_state()
    comp = engine.new_comp()
    mem = Memory(engine, state)
    a, b = mem.literal(num=5), mem.literal(num=3)

    state.counter = None
    engine.call("check_number", comp, state, 100, 200, a, b)
    assert state.counter == 100  # 5 > 3 -> If Larger

    state.counter = None
    engine.call("check_number", comp, state, 100, 200, b, a)
    assert state.counter == 200  # 3 < 5 -> If Smaller

    state.counter = None
    engine.call("check_number", comp, state, 100, 200, a, a)
    assert state.counter is None  # equal -> neither branch fires


def test_check_number_reg_infinite(engine):
    """`check_number`'s REG_INFINITE special-casing -- genuinely exercised (not just a Python
    stand-in), per the "reuse real Lua logic" principle."""
    state = engine.new_state()
    comp = engine.new_comp()
    mem = Memory(engine, state)
    reg_infinite = engine.lua.globals().REG_INFINITE

    inf = mem.literal(num=reg_infinite)
    five = mem.literal(num=5)

    state.counter = None
    engine.call("check_number", comp, state, 1, 2, inf, five)
    assert state.counter == 1  # inf > 5 -> If Larger

    state.counter = None
    engine.call("check_number", comp, state, 1, 2, five, inf)
    assert state.counter == 2  # 5 < inf -> If Smaller

    state.counter = None
    inf2 = mem.literal(num=reg_infinite)
    engine.call("check_number", comp, state, 1, 2, inf, inf2)
    assert state.counter is None  # inf == inf -> neither branch fires


def test_set_reg(engine):
    state = engine.new_state()
    comp = engine.new_comp()
    mem = Memory(engine, state)
    x = mem.literal(num=42)
    y = mem.var("y")
    engine.call("set_reg", comp, state, x, y)
    assert mem.read(y).num == 42


# --- Mock-world Phase 0: the Data registries load under the stub ---------------------------------
# The default engine loads the real utilities/values/items/components/frames definition files, so
# `data.frames`/`data.components`/`data.all` are populated with genuine game defs -- the foundation
# the mock world (docs/mock_world_spec.md) and the real `FilterEntity` need. See that spec's Phase 0.


def test_data_registries_populated(engine):
    data = engine.lua.globals().data
    # Subscript, not attribute: lupa resolves `data.items`/`data.values` to its own table methods,
    # shadowing the Lua fields of the same name. Real game has hundreds of each; assert a healthy
    # floor rather than an exact churn-prone count.
    assert _lua_len(data["frames"]) > 100
    assert _lua_len(data["components"]) > 100
    assert _lua_len(data["items"]) > 50
    assert _lua_len(data["values"]) > 50


def test_data_all_merged_with_data_name(engine):
    """`data.all` unifies every registry, each def tagged with its origin registry -- the shape
    `FilterEntity`/`PrepareFilterEntity` key off (`data.all[id].data_name`)."""
    data_all = engine.lua.globals().data["all"]
    cases = {
        "c_radar": ("components", "Long-Range Radar"),
        "f_bot_1m_c": ("frames", "Mark V"),
        "obsidian": ("items", "Obsidian Chunk"),
        "v_resource": ("values", "Resource"),
    }
    for id_, (data_name, display) in cases.items():
        d = data_all[id_]
        assert d is not None, id_
        assert d.data_name == data_name, id_
        assert d.name == display, id_


def test_filter_helpers_available(engine):
    """The real entity-filter logic is reachable as globals -- the largest reuse win for the mock
    world (any radar/match/for_signal_match filter exercises the exact game predicate)."""
    g = engine.lua.globals()
    assert g.FilterEntity is not None
    assert g.PrepareFilterEntity is not None
    # PrepareFilterEntity turns a filter list into a real mask, keyed off the FF_* constants and
    # the loaded `data.all`. `v_resource` is FF_RESOURCE|FF_DROPPEDITEM in prep_filters.
    filters = engine.lua.table_from(["v_resource", 0])
    mask, rng = g.PrepareFilterEntity(filters)
    assert mask != 0


def test_frameregs_defined(engine):
    # Corrected 2026-07-19: wire -j resolves to native register j, and the true wire mapping is
    # -1 Goto .. -4 Signal (confirmed from deployed in-game-working behaviors' raw wire data --
    # see docs/behavior_format.md "Frame registers"), so the native indices are Goto=1..Signal=4.
    # An earlier version asserted the reverse, copied from the comp-reg instructions' POSITIVE
    # selector order (1 Signal..4 Goto) -- a different address space.
    g = engine.lua.globals()
    assert (g.FRAMEREG_GOTO, g.FRAMEREG_STORE, g.FRAMEREG_VISUAL, g.FRAMEREG_SIGNAL) == (
        1,
        2,
        3,
        4,
    )


def test_instructions_only_engine_skips_registries(engine):
    """`load_data_registries=False` keeps the bare instructions-only runtime: instructions load,
    but the definition registries and `data.all` stay empty."""
    bare = LupaEngine(engine.source, load_data_registries=False)
    g = bare.lua.globals()
    assert g.data.instructions.add is not None  # instructions still loaded
    assert _lua_len(g.data["all"]) == 0
    assert _lua_len(g.data["frames"]) == 0
