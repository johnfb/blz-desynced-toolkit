"""Mock world Phase 3: movement resolution + multi-entity stepping (`MockWorld.step`). The
behaviors run through the real dispatcher (behavior_runtime.lua) and the real `domove` func; the
mock supplies only `RequestStateMove`/`MoveTo`/the per-tick tile advance (world.lua's movement
section documents the model and its provenance). The golden end-to-end differential against the
real in-game circuit log lives in test_movement_circuit_golden.py; these are the unit-level
pieces."""

from blz.desynced_toolkit import Interpreter, MockWorld
from blz.desynced_toolkit.bsf.argcache import ArgCache
from blz.desynced_toolkit.bsf.compile import compile_behavior
from blz.desynced_toolkit.bsf.parse_text import parse_behavior


def _prog(engine, text):
    return compile_behavior(engine, parse_behavior(text, ArgCache(engine)))


def _loc(e):
    return (int(e.location.x), int(e.location.y))


def test_sync_domove_walks_to_coord_and_behavior_resumes(engine):
    """A sync Move Unit holds the behavior in the waiting state until arrival, then execution
    continues -- and the trip takes the measured time: 10 orthogonal tiles at movement_speed 2
    (0.4 tiles/tick) = 25 ticks of walking."""
    w = MockWorld(engine)
    e = w.spawn("f_bot_1s_adw", "player", 0, 0)  # Engineer, movement_speed = 2
    interp = w.attach_behavior(
        e,
        _prog(
            engine,
            "behavior T(Done*):\n"
            "n1: unlock()\n"
            "n2: domove(Target=coord(10, 0))  >POP (Path Blocked) >NEXT (next)\n"
            "n3: set_reg(Value=1, Target=Done)\n"
            "n4: exit()\n",
        ),
    )
    w.step(23)
    assert _loc(e) != (10, 0)  # still under way
    assert interp.read_param(1).num == 0
    w.step(10)
    assert _loc(e) == (10, 0)
    assert interp.read_param(1).num == 1  # behavior resumed past the domove after arrival


def test_path_shape_is_diagonal_first_then_straight(engine):
    """The step-direction rule read off the golden circuit log: diagonal while both axes differ,
    then straight -- never interleaved rasterization."""
    w = MockWorld(engine)
    e = w.spawn("f_bot_1s_adw", "player", 0, 0)
    w.attach_behavior(
        e,
        _prog(
            engine,
            "behavior T():\n"
            "n1: unlock()\n"
            "n2: domove(Target=coord(5, 9))  >POP (Path Blocked) >POP (next)\n",
        ),
    )
    seen = [_loc(e)]
    for _ in range(60):
        w.step(1)
        if seen[-1] != _loc(e):
            seen.append(_loc(e))
    expected = [(i, i) for i in range(1, 6)] + [(5, y) for y in range(6, 10)]
    assert seen[1:] == expected


def test_goto_register_drives_movement_without_domove(engine):
    """A non-empty GOTO frame register is a persistent native move-to
    (reference_goto_register_semantics) -- no behavior needed at all, and the register stays set
    after arrival."""
    w = MockWorld(engine)
    e = w.spawn("f_bot_1s_adw", "player", 0, 0)
    e.SetRegister(e, 1, engine.new_value(0, coord=(4, 0)))  # FRAMEREG_GOTO = 1 (wire -1)
    w.step(30)
    assert _loc(e) == (4, 0)
    assert e.registers[1].coord.x == 4  # register untouched by arrival


def test_blocked_step_takes_path_blocked_pin(engine):
    """With the mock's no-pathfinding model, a blocked immediate next step makes
    RequestStateMove report repeat_blocked, and the real domove func takes its Path Blocked
    exec pin."""
    w = MockWorld(engine)
    e = w.spawn("f_bot_1s_adw", "player", 0, 0)
    w.spawn("f_bot_1m_c", "player", 1, 0)  # ground blocker directly on the path
    interp = w.attach_behavior(
        e,
        _prog(
            engine,
            "behavior T(Out*):\n"
            "n1: unlock()\n"
            "n2: domove(Target=coord(3, 0))  >n4 (Path Blocked) >NEXT (next)\n"
            "n3: set_reg(Value=1, Target=Out)  >POP (next)\n"
            "n4: set_reg(Value=2, Target=Out)  >POP (next)\n",
        ),
    )
    w.step(5)
    assert interp.read_param(1).num == 2
    assert _loc(e) == (0, 0)
    assert bool(e.state_path_blocked)


def test_flyer_ignores_ground_blocking_and_stacks(engine):
    """Flying units ignore landscape blocking and ground occupancy, and may share a tile
    (user-confirmed tile model). f_flyer_m is slot_type 'flyer'."""
    w = MockWorld(engine)
    f = w.spawn("f_flyer_m", "player", 0, 0)
    assert bool(f.flying)
    w.spawn("f_bot_1m_c", "player", 1, 0)  # ground unit on the straight path
    w.set_tile(2, 0, landscape_blocked=True)
    f.MoveTo(f, engine.lua.table(x=3, y=0))
    w.step(40)
    assert _loc(f) == (3, 0)  # flew straight over both blockers


def test_simulation_tick_and_print_capture(engine):
    """simulation_tick reads the world tick (Map.GetTick), and debug_print lands in
    MockWorld.prints with tick + entity attribution instead of stdout."""
    w = MockWorld(engine)
    e = w.spawn("f_bot_1m_c", "player", 0, 0)
    w.attach_behavior(
        e,
        _prog(
            engine,
            "behavior T():\n"
            "n1: unlock()\n"
            "n2: simulation_tick(Tick=$t)\n"
            "n3: debug_print(Print Value=$t)\n"
            "n4: exit()\n",
        ),
    )
    w.step(3)
    assert len(w.prints) == 1
    p = w.prints[0]
    assert p.eid == e.eid
    assert p.tick == 1  # unlock ran the whole straight line on the first tick
    assert p.value.num == 1  # and simulation_tick read that same tick
