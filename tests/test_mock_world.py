"""Mock world Phase 1 (docs/mock_world_spec.md): the engine-native sensing primitives, driven by real
game data. These call `Map.FindClosestEntity`, the real `PrepareFilterEntity`, the entity
`:MatchFilter`, and `faction:IsSeen` directly (Phase 2 wires them through instruction dispatch).

The point of every assertion here is that the *decision* comes from reused game Lua: `MatchFilter`
agrees with the real `PrepareFilterEntity` masks, and any type/faction discrimination bottoms out
in the real `FilterEntity` predicate -- the mock only supplies the entity graph and spatial math.
"""

from blz.desynced_toolkit import MockWorld
from blz.desynced_toolkit.lua_runtime import Memory


def test_spawn_uses_real_def(engine):
    w = MockWorld(engine)
    bot = w.spawn("f_bot_1m_c", "player", 0, 0)
    # `.def` IS the real data.frames table -- the crux of the whole design. ("def" is a Python
    # keyword, so reach it via item access on the lupa proxy.)
    assert bot["def"].name == "Mark V"
    assert bot["def"].movement_speed == 5
    assert bot["def"].health_points == 600
    assert bot.location.x == 0 and bot.location.y == 0


def test_get_distance_is_floored_euclidean(engine):
    # get_distance's readout is FLOORED straight-line Euclidean -- settled by the in-game
    # RangeProbe run (2026-07-19; @store equaled @signal at every offset). (6,3) was the decisive
    # in-game row (path length ~7.24 would read 7; the game said 6 = floor(6.71)); (12,5) is this
    # test's own path-length discriminator (octile would say 14), (2,2)-type floors pin floor
    # over round.
    w = MockWorld(engine)
    a = w.spawn("f_bot_1m_c", "player", 0, 0)
    b = w.spawn("f_bot_1m_c", "player", 3, 4)
    assert w.distance(a, b) == 5  # exact 5.0
    c = w.spawn("f_bot_1m_c", "player", 35, 0)
    assert w.distance(a, c) == 35  # axis-aligned
    d = w.spawn("f_bot_1m_c", "player", 12, 5)
    assert w.distance(a, d) == 13  # exact 13.0; the octile path length 14.07 would read 14
    e = w.spawn("f_bot_1m_c", "player", 6, 3)
    assert w.distance(a, e) == 6  # IN-GAME GOLDEN: floor(6.71); path length would read 7


def test_find_closest_picks_nearest_in_range(engine):
    w = MockWorld(engine)
    me = w.spawn("f_bot_1m_c", "player", 0, 0, visibility_range=40)
    near = w.spawn("f_bot_1m_c", "player", 10, 0)
    far = w.spawn("f_bot_1m_c", "player", 30, 0)
    w.spawn("f_bot_1m_c", "player", 100, 0)  # out of range

    # no mask -> any entity in range; closest wins, self excluded (compare stable .eid, since lupa
    # hands back a fresh proxy per access so `is` can't be used on the same entity)
    assert w.find_closest(me, 40).eid == near.eid
    assert w.find_closest(me, 9) is None  # nearest (near @10) now out of range
    assert w.find_closest(me, 15).eid == near.eid
    assert w.find_closest(me, 40).eid != far.eid


def test_range_gate_is_floored_euclidean(engine):
    # The gate metric was SETTLED by the in-game RangeProbe run (2026-07-19, results in
    # test_mock_world_dispatch.py's golden rows): in range R iff floor(euclid) <= R. (3,3) is
    # where the candidates separate -- euclid 4.24 floors to 4, so it first appears at Range 4
    # (Chebyshev would have admitted it at 3); (2,2) floors to 2, ruling out round/ceil Euclidean.
    w = MockWorld(engine)
    me = w.spawn("f_bot_1m_c", "player", 0, 0, visibility_range=40)
    diag = w.spawn("f_bot_1m_c", "player", 3, 3)
    assert w.find_closest(me, 4).eid == diag.eid  # floor(4.24) = 4
    assert w.find_closest(me, 3) is None  # NOT admitted at 3 (Chebyshev would have)
    corner = w.spawn("f_bot_1m_c", "player", -2, -2)
    assert w.find_closest(me, 2).eid == corner.eid  # floor(2.83) = 2 -- the magnifier square


def test_find_closest_orders_by_euclidean(engine):
    # The winner among gate-passers is the straight-line-nearest (user-observed in-game
    # 2026-07-19). Geometry chosen so every other metric disagrees: from (0,0), A=(8,7) vs
    # B=(10,3) -- Euclidean picks B (10.44 < 10.63), while Chebyshev (8 < 10) and path-length
    # (10.90 < 11.24) would both pick A.
    w = MockWorld(engine)
    me = w.spawn("f_bot_1m_c", "player", 0, 0, visibility_range=40)
    w.spawn("f_bot_1m_c", "player", 8, 7)  # A
    b = w.spawn("f_bot_1m_c", "player", 10, 3)
    assert w.find_closest(me, 40).eid == b.eid


def test_gettrust_accepts_entity_and_compare_forms(engine):
    # Real call shapes in data/instructions.lua: GetTrust(faction), GetTrust(entity), and the
    # two-arg comparison form GetTrust(ent, "ALLY") used by for_inventory_item.
    w = MockWorld(engine)
    player = w.faction("player")
    bugs = w.faction("bugs")
    w.set_trust(player, bugs, "ENEMY")
    bug = w.spawn("f_bot_1m_c", bugs, 5, 0)
    friend = w.spawn("f_bot_1m_c", player, 6, 0)

    assert player.GetTrust(player, bugs) == "ENEMY"  # faction arg
    assert player.GetTrust(player, bug) == "ENEMY"  # entity arg resolves to its faction
    assert player.GetTrust(player, bug, "ALLY") is False  # comparison form -> boolean
    assert player.GetTrust(player, friend, "ALLY") is False  # own faction is not "ALLY"
    assert bugs.GetTrust(bugs, friend, "ENEMY") is True


def test_matchfilter_enemy_faction_agrees_with_prepare(engine):
    w = MockWorld(engine)
    player = w.faction("player")
    bugs = w.faction("bugs")
    w.set_trust(player, bugs, "ENEMY")

    me = w.spawn("f_bot_1m_c", player, 0, 0, visibility_range=50)
    enemy = w.spawn("f_bot_1m_c", bugs, 20, 0)
    ally = w.spawn("f_bot_1m_c", player, 5, 0)

    mask, _ = w.prepare_filter("v_enemy_faction", 0)
    # broad-phase find_closest with the enemy mask must skip the friendly and land on the bug
    assert w.find_closest(me, 50, mask).eid == enemy.eid
    assert enemy.MatchFilter(enemy, mask, player) is True
    assert ally.MatchFilter(ally, mask, player) is False


def test_matchfilter_resource_vs_droppeditem_frametypes(engine):
    w = MockWorld(engine)
    world = w.faction("world", is_world=True)
    me = w.spawn("f_bot_1m_c", "player", 0, 0, visibility_range=50)

    # A resource node: pick a real Resource-type frame from the data if present; otherwise fake the
    # type via override so the frametype bit is FF_RESOURCE.
    node = w.spawn("f_bot_1m_c", world, 10, 0)
    node["def"] = _resource_like_def(engine)
    player = w.faction("player")

    assert me["def"].name == "Mark V"  # a real owner exists in the world
    mineable_mask, _ = w.prepare_filter("v_mineable", 0)  # FF_RESOURCE only
    assert node.MatchFilter(node, mineable_mask, player) is True

    dropped_mask, _ = w.prepare_filter("v_droppeditem", 0)  # FF_DROPPEDITEM only
    assert node.MatchFilter(node, dropped_mask, player) is False


def test_is_seen_vision_model(engine):
    w = MockWorld(engine)
    player = w.faction("player")
    bugs = w.faction("bugs")

    w.spawn(
        "f_bot_1m_c", player, 0, 0, visibility_range=15
    )  # establishes the player faction's vision bubble
    close_enemy = w.spawn("f_bot_1m_c", bugs, 10, 0)
    far_enemy = w.spawn("f_bot_1m_c", bugs, 30, 0)

    assert w.is_seen(player, close_enemy) is True  # within scout's vision
    assert w.is_seen(player, far_enemy) is False  # beyond it
    # The vision bubble is the same floored-Euclidean disc as the range gate (user eyeball
    # observation 2026-07-19 -- the on-screen shape looks identical to the sensing shape):
    # a fringe tile at (15,4), distance 15.52, floors to 15 <= vis and IS seen.
    fringe = w.spawn("f_bot_1m_c", bugs, 15, 4)
    assert w.is_seen(player, fringe) is True
    just_out = w.spawn("f_bot_1m_c", bugs, 15, 6)  # 16.16 -> floor 16 > 15
    assert w.is_seen(player, just_out) is False
    # own-faction entities are always seen regardless of range
    own = w.spawn("f_bot_1m_c", player, 500, 500)
    assert w.is_seen(player, own) is True


def test_worlds_are_isolated(engine):
    w1 = MockWorld(engine)
    w1.spawn("f_bot_1m_c", "player", 0, 0)
    assert len(w1.entities) == 1
    w2 = MockWorld(engine)  # Reset() on construction clears the shared registry
    assert len(w2.entities) == 0


def test_real_get_closest_entity_runs_over_the_mock(engine):
    """The Phase 1 acceptance shape: the *real* `get_closest_entity` func (unmodified game Lua)
    executes over the mock and returns the right entity. This exercises the whole reused chain --
    PrepareFilterEntity, Map.FindClosestEntity, MatchFilter, FilterEntity, Set -- proving the
    mocked leaves satisfy the real func's contract, not just my direct primitive calls."""
    w = MockWorld(engine)
    player = w.faction("player")
    bugs = w.faction("bugs")
    w.set_trust(player, bugs, "ENEMY")

    me = w.spawn("f_bot_1m_c", player, 0, 0, visibility_range=40)
    comp = w.add_component(me, "c_behavior")  # comp.owner is the mock entity

    w.spawn("f_bot_1m_c", player, 5, 0)  # friendly, closer -- must be skipped
    near_enemy = w.spawn("f_bot_1m_c", bugs, 12, 0)
    w.spawn("f_bot_1m_c", bugs, 25, 0)  # enemy, farther
    w.spawn("f_bot_1m_c", bugs, 100, 0)  # enemy, out of vision range

    state = engine.new_state()
    mem = Memory(engine, state)
    f1 = mem.literal(id_="v_enemy_faction")
    out = mem.var("out")

    engine.call("get_closest_entity", comp, state, f1, None, None, out)

    result = mem.read(out).entity
    assert result is not None
    assert result.eid == near_enemy.eid


def _resource_like_def(engine):
    """A minimal Resource-typed def so frametype_bits -> FF_RESOURCE without needing to know a
    specific resource frame id. Backed by a genuine Lua table, not a Python dict."""
    tbl = engine.lua.table()
    tbl.type = "Resource"
    tbl.name = "Test Ore"
    return tbl
