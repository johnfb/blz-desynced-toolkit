"""Runs the *real* `data/instructions.lua` (from the game extract) inside an embedded Lua
runtime (via ``lupa``), with just enough engine-side stand-ins (``engine_stub.lua``) to actually
call each instruction's `func`. See ``engine_stub.lua``'s module docstring for the important
discovery that shaped this design: `Get`/`Set`/`GetNum` are local aliases *within*
instructions.lua for its own `InstGet`/`InstSet`/`InstGetNum`, which this harness reuses
unmodified rather than reimplementing -- only the genuinely-external engine primitives (the
`Value` type's arithmetic, and a fake `comp`/`comp.owner`/`Tool`) are stubbed.
"""

from __future__ import annotations

from importlib import resources

import lupa.lua54 as lupa

from . import dcs_wire
from .assets import AssetSource, get_package_manifest, resolve_include


# The minimal `data/data.lua` include subset whose load populates `data.frames`/`data.components`/
# `data.items`/`data.values` -- everything `FilterEntity`/`PrepareFilterEntity` and the mock world
# need. Determined empirically (docs/mock_world_spec.md Phase 0): these load cleanly under the stub in
# this order, and the intervening real includes (actions/biomes/behaviors/puzzles) are not
# required by any of them at load time, so they're deliberately skipped. `utilities.lua` must come
# first -- it defines the recipe helpers (`CreateProductionRecipe`, ...) and `FilterEntity` that
# the later files reference. `library.lua` (added for the real-dispatcher work, in its real
# relative position) must precede components.lua AND instructions.lua: both capture load-time
# local aliases of `GetFactionBehaviorAsm`/`GetCachedBehaviorAsm` (components.lua's dispatcher
# locals; instructions.lua's "Local references for shorter names") -- load it later and those
# closures would be permanently bound to nothing.
DATA_REGISTRY_INCLUDES = (
    "utilities.lua",
    "library.lua",
    "values.lua",
    "items.lua",
    "components.lua",
    "frames.lua",
)

# Registries merged into `data.all` post-load, each def tagged with its `data_name` -- the engine's
# own post-load step, which `FilterEntity`/`PrepareFilterEntity` rely on (`data.all[id].data_name`).
DATA_ALL_REGISTRIES = ("values", "items", "components", "frames")


class LupaEngine:
    def __init__(
        self,
        source: AssetSource,
        package_id: str = "Data",
        load_data_registries: bool = True,
    ) -> None:
        self.source = source
        self.package_id = package_id
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        stub = (
            resources.files(__package__)
            .joinpath("engine_stub.lua")
            .read_text(encoding="utf-8")
        )
        self.lua.execute(stub)

        pm = get_package_manifest(source, package_id)
        # Load the real Data-registry definition files (frames/components/items/values) before
        # instructions.lua, matching the game's own load order, so `data.frames`/`data.components`/
        # `data.all` are populated with real defs. Off only for callers that explicitly want the
        # bare instructions-only runtime.
        if load_data_registries:
            for include in DATA_REGISTRY_INCLUDES:
                path = resolve_include(pm.entry_dir, include)
                self.lua.execute(source.read_text(path), include)
            self._build_data_all()
        else:
            # Bare instructions-only mode still gets the real library.lua (it defines only
            # functions and depends on nothing at load) so instructions.lua's load-time local
            # aliases of GetCachedBehaviorAsm/GetFactionBehaviorAsm bind the real ones.
            path = resolve_include(pm.entry_dir, "library.lua")
            self.lua.execute(source.read_text(path), "library.lua")

        instructions_path = resolve_include(pm.entry_dir, "instructions.lua")
        self.lua.execute(source.read_text(instructions_path))

        # The behavior runtime (real GetFactionBehaviorAsm compilation + a port of the real
        # c_behavior:on_update dispatch loop -- see behavior_runtime.lua). Needs the Data
        # registries: it extracts `c_behavior_on_end` from the real c_behavior component def.
        # Without registries the Interpreter is unavailable (BehaviorRuntime stays nil).
        if load_data_registries:
            runtime_src = (
                resources.files(__package__)
                .joinpath("behavior_runtime.lua")
                .read_text(encoding="utf-8")
            )
            self.lua.execute(runtime_src, "behavior_runtime.lua")

        self.data = self.lua.globals().data
        self._new_state = self.lua.globals().NewState
        self._new_comp = self.lua.globals().NewComp
        self._new_value = self.lua.globals().NewValue
        self._table = self.lua.table
        # `Get`/`GetNum` are local aliases *inside* instructions.lua for these (see
        # engine_stub.lua's docstring) -- the globals are what's reachable from outside.
        self._inst_get = self.lua.globals().InstGet
        self._inst_get_num = self.lua.globals().InstGetNum

        # `Tool.GetClipboard()`/`Tool.SetClipboard(item, type)` (`ui/Library.lua`) are the real,
        # engine-native functions that turn a `.dcs` clipboard string into the Lua `item` table
        # (and back) -- confirmed nowhere in this Lua extract, so `dcs_wire.py` is what actually
        # backs that missing logic here. The real signatures read/write the OS clipboard with no
        # string argument; OS clipboard access is out of scope here, so these are exposed as
        # plain, explicitly-named Python methods (taking/returning the string directly) rather
        # than bound onto `Tool.GetClipboard`/`SetClipboard` under a misleadingly-identical
        # calling convention.

    def _build_data_all(self) -> None:
        """Replicate the engine's post-load merge: `data.all[id] = def` for every registered
        definition, each tagged with `def.data_name = <registry>`. Ids are registry-namespaced
        (`f_`/`c_`/`v_`/item names), so there are no cross-registry collisions."""
        self.lua.execute(
            """
            for _, name in ipairs({ "%s" }) do
              for id, def in pairs(data[name]) do
                def.data_name = name
                data.all[id] = def
              end
            end
            """
            % '", "'.join(DATA_ALL_REGISTRIES)
        )

    def decode_dcs(self, s: str):
        """`.dcs` clipboard string -> `(type_char, lua_table)`, matching the shape
        `Tool.GetClipboard()` would hand a real Lua caller."""
        return dcs_wire.decode_dcs(self.lua, s)

    def encode_dcs(self, type_char: str, obj) -> str:
        """`(type_char, lua_table)` -> `.dcs` clipboard string, matching what
        `Tool.SetClipboard(item, type)` would produce."""
        return dcs_wire.encode_dcs(type_char, obj)

    def new_state(self):
        return self._new_state()

    def new_comp(self):
        return self._new_comp()

    def new_value(
        self, num: int = 0, coord: tuple[int, int] | None = None, id_: str | None = None
    ):
        lua_coord = self._table(x=coord[0], y=coord[1]) if coord is not None else None
        return self._new_value(num, lua_coord, id_)

    def get_value(self, comp, state, arg):
        return self._inst_get(comp, state, arg)

    def get_num(self, comp, state, arg) -> int:
        return self._inst_get_num(comp, state, arg)

    def call(self, op: str, comp, state, *args):
        """Calls data.instructions[op].func(comp, state, cause=None, *args) directly."""
        instr = self.data.instructions[op]
        if instr is None:
            raise KeyError(f"no such instruction: {op}")
        return instr.func(comp, state, None, *args)


class Memory:
    """Allocates `state.mem[]` slots for literals and named local variables. Mirrors (a simple
    version of) what the real compiler (`GetFactionBehaviorAsm`, not yet reused here) would do
    when interning a behavior's locals/constants into its flat runtime memory array."""

    def __init__(self, engine: LupaEngine, state) -> None:
        self.engine = engine
        self.state = state
        self._next_slot = 1
        self._vars: dict[str, int] = {}
        self._literals: dict[tuple, int] = {}

    def _alloc(self) -> int:
        slot = self._next_slot
        self._next_slot += 1
        return slot

    def literal(
        self, num: int = 0, coord: tuple[int, int] | None = None, id_: str | None = None
    ) -> int:
        """Cached by content (num, coord, id_) -- a literal slot is only ever read (`Get`), never
        `Set`/`:Init()`'d in place the way a named variable's slot is (only `var()` slots are ever
        used as an output/target), so sharing one slot across every occurrence of the same literal
        is safe: nothing durably aliases it without going through `coerce`'s own copy first (see
        `engine_stub.lua`'s `coerce` docstring). Without this, a long-lived loop re-executing the
        same literal-valued instruction every iteration (e.g. `memory_insert`'s `Index=v_foo` arg,
        re-translated fresh on every `_step()` call) leaked one new mem slot per iteration
        forever -- harmless correctness-wise, unbounded memory growth otherwise. Found reviewing
        the `adversarial_text_stress.dcs` Fibonacci fixture, whose loop runs indefinitely."""
        key = (num, coord, id_)
        cached = self._literals.get(key)
        if cached is not None:
            return cached
        slot = self._alloc()
        self.state.mem[slot] = self.engine.new_value(num, coord, id_)
        self._literals[key] = slot
        return slot

    def var(self, name: str) -> int:
        if name not in self._vars:
            slot = self._alloc()
            self.state.mem[slot] = self.engine.new_value(0)
            self._vars[name] = slot
        return self._vars[name]

    def read(self, slot_or_name: int | str):
        slot = self.var(slot_or_name) if isinstance(slot_or_name, str) else slot_or_name
        return self.state.mem[slot]
