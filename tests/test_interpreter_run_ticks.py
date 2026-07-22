"""`Interpreter.run_ticks`: tick-accurate stepping honoring the real lock/unlock/wait model,
now implemented by the real dispatcher itself (`BehaviorRuntime.Activate`, a port of
`c_behavior:on_update`'s loop -- see behavior_runtime.lua): by default (no `unlock()` reached
yet) a behavior runs exactly one instruction per tick (`state.limit == 1`, the dispatcher's own
`SetStateSleep(1)` after every step); `unlock()` raises the per-activation budget to 10000,
checked fresh after every instruction so it can take effect within the same tick it runs in;
`lock()` resets it to 1; an explicit `wait(t)` stops the current tick immediately and resumes
`t` ticks later (see the sleep-semantics note in the wait test below). `run()` still just runs
to completion ignoring all of this.

`tests/data/adversarial_text_stress.dcs` -- a real, deliberately adversarial fixture the user
hand-crafted to break the BSF text pipeline (see test_bsf_text_roundtrip.py/test_bsf_ir_
roundtrip.py/test_bsf_render_mermaid.py for those regressions) -- also turned out to be a genuine
Fibonacci generator once actually run: pushes/pops a 2-element `v_color_green` memory array as a
rotating pair (`swap`-via-stack idiom) and writes each new term to its `Result` parameter, once
per loop iteration (6 real instructions: read_signal-free memory_remove/memory_remove/add/
set_reg/memory_insert/memory_insert, no `wait` of its own -- so 6 ticks per term in the default
locked state)."""

from pathlib import Path

from blz.desynced_toolkit import Interpreter
from blz.desynced_toolkit.bsf.compile import compile_behavior
from blz.desynced_toolkit.bsf.ir import BsfBehavior, BsfNode, BsfParam
from blz.desynced_toolkit.bsf.values import Num, Param

DATA_DIR = Path(__file__).parent / "data"


def test_fibonacci_fixture_via_run_ticks(engine):
    """The concrete ask this was built for: execute a batch of ticks, check the parameter,
    repeat -- rather than `run()`-to-completion, which can't observe an infinite loop's
    intermediate values at all (this fixture never terminates: no `exit`/`wait`, just an
    unconditional `next` back to the top of the loop)."""
    raw = (DATA_DIR / "adversarial_text_stress.dcs").read_text().strip()
    _, table = engine.decode_dcs(raw)
    interp = Interpreter(engine, table)

    expected = [2, 3, 5, 8, 13, 21, 34, 55]
    for want in expected:
        interp.run_ticks(6)  # 6 real instructions per loop iteration, 1 per tick (locked)
        assert interp.read_param(1).num == want


def test_literal_mem_slots_dont_grow_unboundedly(engine):
    """`state.mem` must stay at its compiled size across loop iterations. Under the old
    Python-simulated tier this guarded `Memory.literal`'s content cache (per-translation slot
    leaks); under the real machinery, constants/locals are interned exactly once by the real
    compiler (`GetFactionBehaviorAsm`'s mem image) and only `call` ever appends (popped again on
    return), so a call-free infinite loop must leave mem's size untouched no matter how long it
    runs."""
    raw = (DATA_DIR / "adversarial_text_stress.dcs").read_text().strip()
    _, table = engine.decode_dcs(raw)
    interp = Interpreter(engine, table)

    interp.run_ticks(6 * 5)
    slots_after_5 = len(interp.state.mem)
    interp.run_ticks(6 * 15)
    assert len(interp.state.mem) == slots_after_5  # unchanged after 15 more iterations
    assert interp.read_param(1).num == 17711  # 20th term -- the loop itself still computes


def test_memory_array_push_is_an_independent_copy(engine):
    """Regression test for a real engine_stub.lua bug found building the fixture above: `coerce`
    used to return the SAME Lua table when given an already-constructed Value, instead of a copy
    -- so `memory_insert` (which snapshots a value via `Tool.NewRegisterObject`, per that
    instruction's own real func) stored a live reference to the source variable's mem slot rather
    than an independent copy. Overwriting the variable afterward silently corrupted the
    already-pushed array entry too. This produced powers of 2 instead of Fibonacci numbers before
    the fix (see `engine_stub.lua`'s `coerce` docstring for the full story)."""
    comp = engine.new_comp()
    state = engine.new_state()
    state.mem[1] = engine.new_value(0, id_="v_test_array")  # array key
    state.mem[2] = engine.new_value(1)  # source variable, starts at 1
    state.mem[3] = engine.new_value(0)  # scratch out slot

    engine.call("memory_insert", comp, state, 1, 2)  # push the source variable's current value
    state.mem[2]["num"] = 999  # then mutate the SOURCE slot in place, same as a later
    # memory_remove's `Value:Init()` call would do to a variable reused across loop iterations

    engine.call("memory_remove", comp, state, 1, 3)  # pop the array's only entry back out
    assert state.mem[3].num == 1  # must be the ORIGINAL pushed value, not 999 -- the bug stored
    # the exact same Lua table `memory_insert` read from slot 2, so mutating slot 2 afterward
    # silently changed what the array "contained" too


def test_wait_pauses_the_tick_it_runs_on_and_counts_down(engine):
    n1 = BsfNode(id="n1", op="set_reg", args={"Value": Num(1), "Target": Param(1)})
    n2 = BsfNode(id="n2", op="wait", args={"Time": Num(2)})
    n3 = BsfNode(id="n3", op="set_reg", args={"Value": Num(2), "Target": Param(1)})
    behavior = BsfBehavior(
        name="WaitTest",
        params=[BsfParam(name="Result")],
        nodes={"n1": n1, "n2": n2, "n3": n3},
        order=["n1", "n2", "n3"],
    )
    interp = Interpreter(engine, compile_behavior(engine, behavior))

    # Sleep semantics, pinned by the real dispatcher's own source (data/components.lua): a locked
    # behavior re-arms with SetStateSleep(1) after every instruction, and locked mode is one
    # instruction per tick -- so sleep N means "resume N ticks later", and wait(1) is exactly the
    # locked default's implicit wait. (The old Python-simulated tier read wait(t) as "skip t full
    # ticks", i.e. resume at T+t+1 -- off by one; wait(1) would have been SLOWER than no wait at
    # all, contradicting the dispatcher code.)
    interp.run_ticks(1)
    assert interp.read_param(1).num == 1  # tick 1: n1
    interp.run_ticks(1)
    assert interp.read_param(1).num == 1  # tick 2: n2 (wait 2) -- sleep = 2, stops immediately
    interp.run_ticks(1)
    assert interp.read_param(1).num == 1  # tick 3: sleeping (sleep 2 -> 1)
    interp.run_ticks(1)
    assert interp.read_param(1).num == 2  # tick 4: sleep hits 0 -- the resume tick; n3 runs


def test_default_locked_one_instruction_per_tick_unlock_takes_effect_same_tick(engine):
    locked_nodes = {
        f"n{i}": BsfNode(id=f"n{i}", op="set_reg", args={"Value": Num(i), "Target": Param(1)})
        for i in range(1, 4)
    }
    locked = BsfBehavior(
        name="LockedTest",
        params=[BsfParam(name="Result")],
        nodes=locked_nodes,
        order=[f"n{i}" for i in range(1, 4)],
    )
    interp = Interpreter(engine, compile_behavior(engine, locked))
    interp.run_ticks(1)
    assert interp.read_param(1).num == 1  # default state.limit == 1: only the first instruction
    interp.run_ticks(1)
    assert interp.read_param(1).num == 2

    unlock_nodes = {"n0": BsfNode(id="n0", op="unlock")}
    for i in range(1, 6):
        unlock_nodes[f"n{i}"] = BsfNode(
            id=f"n{i}", op="set_reg", args={"Value": Num(i), "Target": Param(1)}
        )
    unlocked = BsfBehavior(
        name="UnlockTest",
        params=[BsfParam(name="Result")],
        nodes=unlock_nodes,
        order=["n0"] + [f"n{i}" for i in range(1, 6)],
    )
    interp2 = Interpreter(engine, compile_behavior(engine, unlocked))
    interp2.run_ticks(1)
    # unlock() itself raises state.limit within the SAME tick it runs in (checked fresh after
    # every instruction, not just at the top of the tick) -- so all 5 following set_regs also
    # execute in this one tick, not just the first.
    assert interp2.read_param(1).num == 5
