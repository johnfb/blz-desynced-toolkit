"""Runs a behavior given as a genuine Lua table (the shape `dcs_wire.decode_dcs()` hands back,
and `bsf.compile_behavior` produces directly) through the REAL game behavior machinery -- see
`behavior_runtime.lua` for the full inventory. In short: the real `GetFactionBehaviorAsm`
(data/library.lua) compiles it, the real `UploadBehavior`/`SetBehavior` install and start it on a
component, and a port of the real `c_behavior:on_update` dispatch loop executes it, so block
stacks (`InstBeginBlock`), `call`/return (`state.returns` + genuine by-reference parameters via
`state.stk`), `jump` label scans, memory interning, and every branch decision are the game's own
code. This class is only *activation scheduling*: deciding on which tick the component wakes,
exactly the part that is engine-native C++ in the real game.

An earlier version of this module simulated the block stack, `sequence`/`for_number` driving, and
per-instruction argument translation in Python (with `call` unsupported); that tier is gone --
git history only.

Parameters are component registers (the real model: `SetBehavior` sets `state.stk = #parameters`,
so the real `GetStack` routes addresses 1..N to `comp:GetRegister(n)`). `params=` writes them
before the first tick; `read_param` reads them back.

Timing model (all from the real dispatcher's own source, data/components.lua): a locked behavior
(the default) runs one instruction per activation and re-arms with `SetStateSleep(1)` -- which
pins the sleep semantics as "resume N ticks later": `wait(1)` is exactly the locked default, so
`wait(N)`/`SetStateSleep(N)` at tick T resumes at tick T+N. (An earlier `run_ticks` treated
sleep N as "skip N full ticks", i.e. resume at T+N+1 -- off by one, corrected when the real
dispatcher was adopted.)

One DELIBERATE deviation from real engine semantics (documented so nobody "fixes" tests into
infinite loops, or mistakes this for the game's behavior): a dead end that pops through the entire
block and call stacks ends the run (`BehaviorRuntime.Activate` returns "restart" instead of
executing the next pass). The real engine instead falls back to Program Start without yielding
and keeps going forever -- `exit` is the only genuine halt (docs/behavior_format.md "Stopping a
behavior"; `reference_stop_deadend_semantics`). A test harness needs termination, so top-level
fall-off means "one full pass completed" here; note the real restart bookkeeping (clearing
non-keepvars locals) HAS already run when this fires, because it is the real `c_behavior_on_end`
that decided.
"""

from __future__ import annotations

import lupa.lua54 as lupa

from .lua_runtime import LupaEngine


def _is_table(v) -> bool:
    return lupa.lua_type(v) == "table"


class Interpreter:
    def __init__(
        self,
        engine: LupaEngine,
        prog,
        params: dict[int, object] | None = None,
        comp=None,
    ) -> None:
        self.engine = engine
        runtime = engine.lua.globals().BehaviorRuntime
        if runtime is None:
            raise RuntimeError(
                "Interpreter needs the Data registries loaded "
                "(LupaEngine(..., load_data_registries=True), the default)"
            )
        self._activate = runtime.Activate
        # `comp` defaults to the bare engine stub (fake owner/faction). Pass a MockWorld
        # component (comp.owner a real mock entity) to run sensing/movement ops against a world.
        self.comp = comp if comp is not None else engine.new_comp()
        self.main_id = runtime.Install(self.comp, prog)
        if self.main_id is None:
            raise RuntimeError("behavior failed to install (not a valid 'C' program table?)")
        self.state = self.comp.extra_data
        for idx, value in (params or {}).items():
            self.comp.SetRegister(self.comp, idx, self._value_for(value))
        self._finished = False

    def _value_for(self, py_value):
        if _is_table(py_value):
            return py_value  # already a Lua value (a register object / Value table)
        if isinstance(py_value, tuple):
            return self.engine.new_value(0, coord=py_value)
        return self.engine.new_value(py_value)

    # -- wake conditions ------------------------------------------------------------------------

    def _waiting_on_move(self) -> bool:
        """True while a sync move issued by this comp is still resolving (the mock world's
        RequestStateMove marks the comp; its movement step clears the mark and re-arms sleep on
        arrival). Always False on the bare stub comp -- it has no movement."""
        return bool(self.comp["waiting_move"])

    def _dispatch(self, status: str) -> bool:
        """Handle one Activate() result; returns True when the run is over."""
        if status == "restart":
            self._finished = True
            return True
        if status == "limit":
            raise RuntimeError(
                "unlocked behavior exceeded the per-tick instruction limit (real InstError ran)"
            )
        if status == "no_asm":
            raise RuntimeError("behavior disappeared from the faction library mid-run")
        # waiting on component state: an armed sleep or a pending sync move will wake it; with
        # neither, nothing ever will (`exit`'s forever-wait) -- the run is over
        if status == "waiting" and (self.comp.sleep or 0) <= 0 and not self._waiting_on_move():
            self._finished = True
            return True
        return False

    # -- driving --------------------------------------------------------------------------------

    def run(self, max_steps: int = 20000) -> None:
        """Runs to completion (or `exit`), ignoring tick/lock-state timing entirely -- sleeps are
        discarded and every activation happens back-to-back. A behavior genuinely blocked on
        world state (a sync move with no `MockWorld.step` driving movement) will spin its
        repeat-instruction until `max_steps`. For tick-accurate stepping use `run_ticks`."""
        steps = 0
        while not self._finished and self.comp.is_active:
            self.comp.sleep = 0
            if self._dispatch(self._activate(self.comp, None)):
                return
            steps += 1
            if steps > max_steps:
                raise RuntimeError("runaway")

    def run_ticks(self, n: int, max_steps: int = 100000) -> None:
        """Simulates `n` real game ticks, honoring the real lock/unlock/wait model exactly as the
        real dispatcher implements it (data/components.lua): a locked behavior executes one
        instruction per tick (each activation ends in the dispatcher's own `SetStateSleep(1)`);
        `unlock` raises the per-activation budget to 10000 (taking effect within the same tick);
        an explicit `wait(t)` yields the tick and resumes `t` ticks later; a sync move yields
        until the mock world's movement step wakes the component."""
        steps = 0
        for _ in range(n):
            if self._finished or not self.comp.is_active:
                return
            sleep = self.comp.sleep or 0
            if sleep > 0:
                sleep -= 1
                self.comp.sleep = sleep
                if sleep > 0:
                    continue  # still sleeping through this tick
                # slept out: this is the resume tick
            elif self._waiting_on_move():
                continue  # movement wake pending; MockWorld.step's movement phase re-arms sleep
            if self._dispatch(self._activate(self.comp, None)):
                return
            steps += 1
            if steps > max_steps:
                raise RuntimeError("runaway")

    def read_param(self, idx: int):
        return self.comp.GetRegister(self.comp, idx)
