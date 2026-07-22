"""Mock world Phase 2 (docs/mock_world_spec.md): the Interpreter's op dispatch now drives the world /
sensing ops end-to-end -- get_location, get_closest_entity, read_signal, value_type, match, and the
block-producing sensing loops (for_entities_in_range). Each behavior is authored in BSF, compiled by
the real compiler, and run through the real instruction funcs over a MockWorld-backed component
(comp.owner is a genuine mock entity). The dispatch itself is metadata-driven, so these also guard
the generic marshaller (in/out/exec + hidden make_asm args)."""

from pathlib import Path

import pytest

from blz.desynced_toolkit import Interpreter, MockWorld
from blz.desynced_toolkit.bsf.argcache import ArgCache
from blz.desynced_toolkit.bsf.compile import compile_behavior
from blz.desynced_toolkit.bsf.parse_text import parse_behavior

DATA_DIR = Path(__file__).parent / "data"


def _compile(engine, text):
    b = parse_behavior(text, ArgCache(engine))
    return compile_behavior(engine, b)


def _run(engine, w, owner, text, params=None):
    comp = w.add_component(owner, "c_behavior")
    prog = _compile(engine, text)
    interp = Interpreter(engine, prog, params=params, comp=comp)
    interp.run()
    return interp


def test_get_location_self(engine):
    w = MockWorld(engine)
    owner = w.spawn("f_bot_1m_c", "player", 7, 3)
    interp = _run(
        engine,
        w,
        owner,
        "behavior T(Out*):\nn1: get_location(Coord=Out)\n",  # Unit omitted -> self
    )
    coord = interp.read_param(1).coord
    assert (coord.x, coord.y) == (7, 3)


def test_get_closest_entity_finds_enemy(engine):
    w = MockWorld(engine)
    player = w.faction("player")
    bugs = w.faction("bugs")
    w.set_trust(player, bugs, "ENEMY")
    owner = w.spawn("f_bot_1m_c", player, 0, 0, visibility_range=40)
    w.spawn("f_bot_1m_c", player, 3, 0)  # friendly, closer -- must be skipped
    enemy = w.spawn("f_bot_1m_c", bugs, 12, 0)
    w.spawn("f_bot_1m_c", bugs, 30, 0)  # farther enemy

    interp = _run(
        engine,
        w,
        owner,
        "behavior T(Out*):\nn1: get_closest_entity(Filter=v_enemy_faction, Output=Out)\n",
    )
    assert interp.read_param(1).entity.eid == enemy.eid


def test_distance_metrics_through_real_funcs(engine):
    """Pins the settled distance semantics through the REAL instruction funcs (not just the
    Map primitives): get_closest_entity's winner is the straight-line-nearest (Euclidean), and
    get_distance reads back floored straight-line Euclidean -- settled by the in-game RangeProbe
    run 2026-07-19; see world.lua's distance-model note."""
    w = MockWorld(engine)
    player = w.faction("player")
    bugs = w.faction("bugs")
    w.set_trust(player, bugs, "ENEMY")
    owner = w.spawn("f_bot_1m_c", player, 0, 0, visibility_range=40)
    w.spawn("f_bot_1m_c", bugs, 8, 7)  # Chebyshev/path-length would pick this one
    b = w.spawn("f_bot_1m_c", bugs, 10, 3)  # Euclidean-nearest (10.44 < 10.63)

    interp = _run(
        engine,
        w,
        owner,
        "behavior T(Out*, Dist*):\n"
        "n1: get_closest_entity(Filter=v_enemy_faction, Output=Out)\n"
        "n2: get_distance(Target=Out, Distance=Dist)\n",
    )
    assert interp.read_param(1).entity.eid == b.eid
    # get_distance to (10,3): floor(10.44) = 10 (the path length 11.24 would read 11 -- ruled
    # out by the in-game probe's (6,3) row)
    assert interp.read_param(2).num == 10


def test_frame_register_wire_mapping_signal(engine):
    """A wire write to @signal must land on the same slot the real read_signal func reads
    (ent:GetRegister(FRAMEREG_SIGNAL)). Guards the corrected frame-register wire mapping
    (-1 Goto .. -4 Signal -- docs/behavior_format.md "Frame registers"): under the old reversed
    FRAMEREG constants this cross-wired, @signal writes landing on the Goto slot."""
    w = MockWorld(engine)
    owner = w.spawn("f_bot_1m_c", "player", 0, 0, visibility_range=15)
    interp = _run(
        engine,
        w,
        owner,
        "behavior T(Out*):\n"
        "n1: set_reg(Value=7, Target=@signal)\n"
        "n2: get_self(Unit Reference=$me)\n"
        "n3: read_signal(Unit=$me, Result=Out)\n",  # read_signal has no self-default: nil Unit
    )  # writes nil through Set (the Init-nil path)
    assert interp.read_param(1).num == 7
    assert owner.registers[4].num == 7  # FRAMEREG_SIGNAL = 4 = wire -4
    assert owner.registers[1] is None  # nothing leaked onto Goto (wire -1)


def test_is_passable_through_real_func(engine):
    """is_passable end-to-end over the mock tile model: open tile -> Passable, landscape-blocked
    tile -> Impassable, entity-occupied tile -> Impassable (visible-tile path: landscape + entity
    blocking merge -- docs/mock_world_spec.md's tile model). Also regression-guards Map.CountTiles'
    numeric (x, y, ...) call shape, which the real func uses directly."""
    w = MockWorld(engine)
    owner = w.spawn("f_bot_1m_c", "player", 0, 0, visibility_range=15)
    w.set_tile(2, 0, landscape_blocked=True)
    w.spawn("f_bot_1m_c", "player", 3, 0)  # occupies (3,0)

    def probe(x, y):
        interp = _run(
            engine,
            w,
            owner,
            "behavior T(Out*):\n"
            # the top-level `next` pin is is_passable's undiscovered-tile outcome (neither exec
            # pin fires); unreachable here since the mock has no fog model (all discovered)
            f"n1: is_passable(Coordinate=coord({x}, {y}))  >n2 (Impassable) >n3 (Passable) >POP (next)\n"
            "n2: set_reg(Value=1, Target=Out)  >POP (next)\n"
            "n3: set_reg(Value=2, Target=Out)  >POP (next)\n",
        )
        return interp.read_param(1).num

    assert probe(1, 0) == 2  # open tile: passable
    assert probe(2, 0) == 1  # landscape-blocked
    assert probe(3, 0) == 1  # occupied by an entity


def test_read_signal_of_found_unit(engine):
    w = MockWorld(engine)
    owner = w.spawn("f_bot_1m_c", "player", 0, 0, visibility_range=40)
    mate = w.spawn("f_bot_1m_c", "player", 8, 0)
    mate.SetRegister(mate, 4, engine.new_value(num=42))  # FRAMEREG_SIGNAL = 4 (wire -4)

    interp = _run(
        engine,
        w,
        owner,
        "behavior T(Out*):\n"
        "n1: get_closest_entity(Filter=v_bot, Output=$u)\n"
        "n2: read_signal(Unit=$u, Result=Out)\n",
    )
    assert interp.read_param(1).num == 42


def test_match_failed_exec_branch(engine):
    """match sets state.counter to the Failed pin when the unit doesn't match -- exercises the
    generic exec-branch resolution (self is player, so v_enemy_faction fails)."""
    w = MockWorld(engine)
    owner = w.spawn("f_bot_1m_c", "player", 0, 0)
    interp = _run(
        engine,
        w,
        owner,
        "behavior T(Out*):\n"
        "n1: match(Filter=v_enemy_faction)  >n3 (Failed) >NEXT (next)\n"
        "n2: set_reg(Value=1, Target=Out)  >POP (next)\n"
        "n3: set_reg(Value=2, Target=Out)  >POP (next)\n",
    )
    assert interp.read_param(1).num == 2  # took the Failed branch


def test_value_type_coord_branch(engine):
    """value_type dispatches on the value's type (a hidden-arg-free multi-exec op). A coord input
    takes the Coord pin."""
    w = MockWorld(engine)
    owner = w.spawn("f_bot_1m_c", "player", 0, 0)
    interp = _run(
        engine,
        w,
        owner,
        "behavior T(In, Out*):\n"
        "n1: value_type(Data=In)  >POP (Item) >POP (Unit) >POP (Component) "
        ">POP (Tech) >POP (Value) >n3 (Coord) >POP (No Match)\n"
        "n2: set_reg(Value=1, Target=Out)  >POP (next)\n"
        "n3: set_reg(Value=2, Target=Out)  >POP (next)\n",
        params={1: (5, 9)},  # a coord
    )
    assert interp.read_param(2).num == 2  # took the Coord branch


def test_for_entities_in_range_counts_bots(engine):
    """for_entities_in_range is a block-producing loop: its func builds the iterator via real
    Map.FindClosestEntity + FilterEntity, and the Python block driver runs .next/.last. Counts the
    other bots in range (owner excluded)."""
    w = MockWorld(engine)
    owner = w.spawn("f_bot_1m_c", "player", 0, 0, visibility_range=50)
    for x in (5, 10, 15):
        w.spawn("f_bot_1m_c", "player", x, 0)

    interp = _run(
        engine,
        w,
        owner,
        "behavior T(Count*, Last*):\n"
        "n1: set_reg(Value=0, Target=Count)\n"
        "n2: for_entities_in_range(Range=50, Filter=v_bot, Unit=$u)  >n5 (Done) >NEXT (next)\n"
        "n3: add(To=Count, Num=1, Result=Count)\n"
        "n4: set_reg(Value=$u, Target=Last)  >POP (next)\n"
        "n5: exit()\n",
    )
    assert interp.read_param(1).num == 3
    # The loop's Unit output must carry the ENTITY, not a field-stripped rendering of it --
    # guards the coerce fix for bare mock-entity tables (the real .next passes a raw entity,
    # which is userdata in the real engine but a table here; without the metatable marker the
    # entity's own frame-id field got misread as an id literal and the entity itself dropped).
    last = interp.read_param(2)
    assert last.entity is not None
    assert last.id is None


def test_for_signal_match_membership_scan(engine):
    """for_signal_match is the other block-producing sensing loop; it enumerates own-faction units
    broadcasting a signal via the real GetEntitiesWithRegister + the func's own id match. Counts the
    two units broadcasting v_transport_route, skipping the one on v_idle and the signal-less owner."""
    w = MockWorld(engine)
    owner = w.spawn("f_bot_1m_c", "player", 0, 0, visibility_range=50)
    for x in (5, 10):
        m = w.spawn("f_bot_1m_c", "player", x, 0)
        m.SetRegister(m, 4, engine.new_value(0, None, "v_transport_route"))  # FRAMEREG_SIGNAL = 4
    odd = w.spawn("f_bot_1m_c", "player", 7, 0)
    odd.SetRegister(odd, 4, engine.new_value(0, None, "v_idle"))

    interp = _run(
        engine,
        w,
        owner,
        "behavior T(Count*):\n"
        "n1: set_reg(Value=0, Target=Count)\n"
        "n2: for_signal_match(Signal=v_transport_route, Unit=$u, Signal2=$s)  "
        ">n4 (Done) >NEXT (next)\n"
        "n3: add(To=Count, Num=1, Result=Count)  >POP (next)\n"
        "n4: exit()\n",
    )
    assert interp.read_param(1).num == 2


# The RangeProbe instrument (tests/data/range_probe.bsf, compiled copy alongside): sweeps
# Loop Units (Range) with Range=1..15 and reports the smallest detecting Range on @signal plus the
# get_distance readout on @store. BOTH columns are GOLDEN in-game results (user-run 2026-07-19;
# @store was identical to @signal at every offset), which settled the whole distance model as one
# function: get_distance = floor(straight-line Euclidean), and the range gate is exactly
# `get_distance <= R`. (3,3)/(4,3) rule out Chebyshev, (6,3) rules out path-length/octile (7.24
# would read 7; the game said 6), (2,2)/(3,2) rule out round/ceil Euclidean.
@pytest.mark.parametrize(
    "offset,min_range,distance",
    [
        ((3, 0), 3, 3),
        ((2, 2), 2, 2),
        ((3, 2), 3, 3),
        ((3, 3), 4, 4),
        ((4, 3), 5, 5),
        ((6, 3), 6, 6),
    ],
)
def test_range_probe_fixture_against_mock_model(engine, offset, min_range, distance):
    _, prog = engine.decode_dcs((DATA_DIR / "range_probe.dcs").read_text().strip())
    w = MockWorld(engine)
    owner = w.spawn("f_bot_1m_c", "player", 0, 0, visibility_range=15)
    comp = w.add_component(owner, "c_behavior")
    w.spawn("f_bot_1m_b", "player", offset[0], offset[1])  # the probe target (a Hauler)
    # Probe param = Hauler frame id (params are component registers under the real dispatcher)
    interp = Interpreter(engine, prog, params={1: engine.new_value(0, id_="f_bot_1m_b")}, comp=comp)
    interp.run()
    assert owner.registers[4].num == min_range  # @signal (wire -4)
    assert owner.registers[2].num == distance  # @store (wire -2)
