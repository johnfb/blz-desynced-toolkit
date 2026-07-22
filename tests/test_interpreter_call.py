"""`call` through the REAL machinery (behavior_runtime.lua): the genuine `call` func builds the
return record / `state.stk` frame, `GetFactionBehaviorAsm` compiles the embedded dependency
(imported via the real `UploadBehavior` sub-remapping), and `c_behavior_on_end` pops the return.
The old Python-simulated interpreter tier had no `call` support at all -- these tests pin the
semantics the project has already confirmed the hard way in the format docs/memories:

- parameters are BY-REFERENCE through `state.stk` (a sub writing an output param writes the
  caller's own slot -- or frame register -- live, not on return),
- memory arrays are ONE shared table across the whole call stack (`state.arrays`, keyed by Index
  value -- reference_memory_arrays_global_across_calls),
- a sub's locals are freshly allocated per call and discarded on return (mem grows and shrinks).
"""

from blz.desynced_toolkit import Interpreter
from blz.desynced_toolkit.bsf.argcache import ArgCache
from blz.desynced_toolkit.bsf.compile import compile_behavior
from blz.desynced_toolkit.bsf.parse_text import parse_behavior


def _prog(engine, text):
    return compile_behavior(engine, parse_behavior(text, ArgCache(engine)))


def test_call_embedded_sub_in_out_params(engine):
    prog = _prog(
        engine,
        "behavior Main(Out*):\n"
        "n1: call(In=5, Result=$r, sub=1)\n"
        "n2: set_reg(Value=$r, Target=Out)  >POP (next)\n"
        "\n"
        "sub Double(In, Result*):\n"
        "n1: add(To=In, Num=In, Result=Result)  >POP (next)\n",
    )
    interp = Interpreter(engine, prog)
    interp.run()
    assert interp.read_param(1).num == 10


def test_call_params_alias_caller_slots_live(engine):
    """A frame register passed as a call argument is written THROUGH, mid-call -- the real
    `state.stk` indirection resolves the sub's param address to the caller's `@signal` wire
    address (-4), so the write lands on the owner's Signal register the moment the sub makes it,
    observable from outside while the sub is still `wait`ing. A copy-back-on-return
    implementation would fail the mid-call read."""
    prog = _prog(
        engine,
        "behavior Main():\n"
        "n1: call(Result=@signal, sub=1)\n"
        "n2: exit()\n"
        "\n"
        "sub Emit(Result*):\n"
        "n1: set_reg(Value=7, Target=Result)\n"
        "n2: wait(Time=2)\n"
        "n3: set_reg(Value=9, Target=Result)  >POP (next)\n",
    )
    interp = Interpreter(engine, prog)
    owner = interp.comp.owner
    interp.run_ticks(2)  # tick 1: call sets up the frame; tick 2: sub's first set_reg
    assert owner.GetRegister(owner, 4).num == 7  # FRAMEREG_SIGNAL, written live mid-call
    interp.run_ticks(10)
    assert owner.GetRegister(owner, 4).num == 9


def test_memory_arrays_shared_across_call_stack(engine):
    """The caller pushes onto a memory array; the sub pops from the same Index value and hands it
    back -- one `state.arrays` table for the whole stack, never windowed per call."""
    prog = _prog(
        engine,
        "behavior Main(Out*):\n"
        "n1: memory_insert(Index=v_color_green, Value=41)\n"
        "n2: call(Result=$r, sub=1)\n"
        "n3: set_reg(Value=$r, Target=Out)  >POP (next)\n"
        "\n"
        "sub PopShared(Result*):\n"
        "n1: memory_remove(Index=v_color_green, Old Value=Result)  >POP (next)\n",
    )
    interp = Interpreter(engine, prog)
    interp.run()
    assert interp.read_param(1).num == 41


def test_call_sub_locals_freed_on_return(engine):
    """A sub's locals are appended to `state.mem` for the duration of the call and trimmed on
    return (`c_behavior_on_end`'s return branch) -- repeated calls must not grow memory."""
    prog = _prog(
        engine,
        "behavior Main(Out*):\n"
        "n1: for_number(From=1, To=20, Value=$i)  >n3 (Done) >NEXT (next)\n"
        "n2: call(In=$i, Result=Out, sub=1)  >POP (next)\n"
        "n3: exit()\n"
        "\n"
        "sub Work(In, Result*):\n"
        "n1: add(To=In, Num=100, Result=$local1)\n"
        "n2: add(To=$local1, Num=0, Result=$local2)\n"
        "n3: set_reg(Value=$local2, Target=Result)  >POP (next)\n",
    )
    interp = Interpreter(engine, prog)
    interp.run()
    assert interp.read_param(1).num == 120  # last iteration: 20 + 100
    # after the loop every call frame has been popped; mem is back to Main's own compiled size
    main_asm_mem = 0
    lib = interp.comp.faction.extra_data.library
    for _, code in lib.items():
        if code.id == interp.main_id:
            # compiled mem image size for Main: recompute via the real compiler's cache
            asm = engine.lua.globals().GetFactionBehaviorAsmById(interp.comp.faction, code.id)
            main_asm_mem = len(asm.mem)
    assert main_asm_mem > 0
    assert len(interp.state.mem) == main_asm_mem
