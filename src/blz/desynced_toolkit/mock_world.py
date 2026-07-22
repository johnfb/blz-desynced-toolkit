"""Python facade over ``world.lua`` -- the mock world for behavior testing (docs/mock_world_spec.md).

Phases 1-3: construct factions/entities/tiles, expose the engine-native sensing primitives
(``Map.FindClosestEntity``, ``:MatchFilter``, ``faction:IsSeen``) that the real instruction funcs
call, and step the whole world tick by tick (``attach_behavior`` + ``step``: every attached
behavior's interpreter advances, then movement resolves, then deferred callbacks drain).

Doctrine (CLAUDE.md): world *state* lives in Lua tables whose ``.def`` is the real
``data.frames``/``data.components`` def; Python only *orchestrates* (spawn, mutate, assert). Nothing
here reimplements an instruction or a filter decision -- those stay in the reused game Lua.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

import lupa.lua54 as lupa

from .interpreter import Interpreter
from .lua_runtime import LupaEngine


@dataclass
class DebugPrint:
    """One captured debug_print: the world tick it fired on, the printing entity's ``eid`` (None
    when unattributable), and the printed register Value (a live Lua table -- read ``.num``/
    ``.coord``/``.id``/``.entity`` off it)."""

    tick: int
    eid: int | None
    value: object


_WORLD_LUA = resources.files(__package__).joinpath("world.lua").read_text(encoding="utf-8")


def _array_values(table) -> list:
    """A Lua array-shaped table's values in index order (1..N), skipping any non-integer sibling
    keys -- mirrors `bsf/decompile.py`'s `_int_keys` helper."""
    return [table[k] for k in sorted(k for k in table if isinstance(k, int))]


class MockWorld:
    """A populated, steppable mock world sharing one :class:`LupaEngine`.

    Loads ``world.lua`` into the engine's Lua runtime and resets the registry, so a single
    session-scoped engine can host many independent ``MockWorld`` instances (one per test).
    """

    def __init__(self, engine: LupaEngine) -> None:
        if engine.data.frames is None or engine.data.frames.f_bot_1m_c is None:
            raise RuntimeError(
                "MockWorld needs the Data registries loaded "
                "(LupaEngine(..., load_data_registries=True), the default)"
            )
        self.engine = engine
        self.lua = engine.lua
        self.lua.execute(_WORLD_LUA)
        self._World = self.lua.globals().World
        self._Map = self.lua.globals().Map
        self._World.Reset()
        self.interpreters: list[Interpreter] = []
        #: every debug_print fired while stepping this world, in order (see DebugPrint)
        self.prints: list[DebugPrint] = []
        self._install_print_sink()

    def _install_print_sink(self) -> None:
        """Route the real debug_print func's global `print` into `self.prints` with tick/entity
        attribution (behavior_runtime.lua's sink; one sink per engine, so the most recently
        constructed MockWorld on a shared engine owns it -- fine for one-world-per-test use)."""
        tool_copy = self.engine.lua.globals().Tool.Copy

        def sink(comp, *args):
            # The real func prints ("[DEBUGPRINT]", reg) -- the register value is the last arg.
            # SNAPSHOT it (deep copy; entity refs survive as references): the printed table is a
            # live register box that the behavior keeps Init()-ing in place, so storing the raw
            # reference would make every captured print retroactively show the final value.
            value = args[-1] if args else None
            if value is not None and lupa.lua_type(value) == "table":
                value = tool_copy(value)
            eid = None
            if comp is not None and comp.owner is not None:
                eid = comp.owner.eid
            self.prints.append(DebugPrint(tick=self.tick, eid=eid, value=value))

        self.engine.lua.globals().BehaviorRuntime.print_sink = sink

    # -- stepping -------------------------------------------------------------------------------

    @property
    def tick(self) -> int:
        return int(self._World.tick)

    def attach_behavior(self, entity, prog, params: dict[int, object] | None = None):
        """Attach a Behavior Controller (`c_behavior`) running ``prog`` (a decoded/compiled
        behavior table, or a raw `.dcs` string) to ``entity``. The behavior starts on the next
        `step`. Returns the Interpreter (its ``comp`` is the attached component)."""
        if isinstance(prog, str):
            type_char, prog = self.engine.decode_dcs(prog.strip())
            if type_char != "C":
                raise ValueError(f"not a behavior clipboard string (type {type_char!r})")
        comp = self.add_component(entity, "c_behavior")
        return self._attach_behavior_to_component(comp, prog, params)

    def _attach_behavior_to_component(
        self, comp, prog, params: dict[int, object] | None = None
    ) -> Interpreter:
        """Install ``prog`` onto an already-attached component (blueprint loading: the
        behavior-hosting component -- `c_behavior`/`c_integrated_behavior`/`c_autobase` -- is
        already in the blueprint's own `components` list, so this skips `attach_behavior`'s
        own `add_component`)."""
        interp = Interpreter(self.engine, prog, params=params, comp=comp)
        self.interpreters.append(interp)
        return interp

    def load_blueprint(self, prog, faction: str = "player", x: int = 0, y: int = 0):
        """Spawn every building of a blueprint (a decoded/compiled `'B'` table, or a raw `.dcs`
        string) into the world at origin ``(x, y)``. A `multi` blueprint (several buildings, e.g.
        the Magnifier lattice) spawns one entity per entry at ``(x + bp.x, y + bp.y)``; a
        single-building blueprint spawns one entity at ``(x, y)``.

        Each building's `components` list is attached for real (`add_component`), including
        sockets and hidden/inherent components (the Integrated Behavior Controller, etc). A
        component whose real `data.components[id].base_id == "c_behavior"` (the whole
        Behavior-Controller family: `c_behavior`/`c_integrated_behavior`/`c_autobase`) carries its
        program as a 1-based index into the blueprint's own `dependencies` array (confirmed
        against `data/library.lua`'s `UnpackCompactedItemToLibraryTable`/`iblueprintcomponents` --
        the same convention `call`'s `sub` field uses) -- that program is installed on the
        component via `attach_behavior`'s own machinery.

        Initial `regs` are applied as real register writes: a plain integer key is a frame
        register (`FRAMEREG_GOTO`..`FRAMEREG_SIGNAL`, 1..4, on the entity itself); a `"N|M"`
        string key is component #N's (1-based position in `components`) register M -- these are
        the same slots a `call`'s `params=` would write, so a behavior-hosting component's own
        register-shaped `regs` entries double as its startup parameters.

        Returns the list of spawned entities, in building order (`multi` order, or a single-entry
        list for a non-multi blueprint)."""
        if isinstance(prog, str):
            type_char, table = self.engine.decode_dcs(prog.strip())
            if type_char != "B":
                raise ValueError(f"not a blueprint clipboard string (type {type_char!r})")
        else:
            table = prog
        keys = set(table.keys())
        dependencies = table["dependencies"] if "dependencies" in keys else None
        buildings = _array_values(table["multi"]) if "multi" in keys else [table]
        fac = self.faction(faction) if isinstance(faction, str) else faction
        return [self._spawn_blueprint_building(bp, dependencies, fac, x, y) for bp in buildings]

    def _spawn_blueprint_building(self, bp, dependencies, fac, ox: int, oy: int):
        keys = set(bp.keys())
        bx = int(bp["x"]) if "x" in keys else 0
        by = int(bp["y"]) if "y" in keys else 0
        overrides = {}
        if "powered_down" in keys:
            overrides["powered_down"] = bool(bp["powered_down"])
        entity = self.spawn(bp["frame"], fac, ox + bx, oy + by, **overrides)

        added_components = []
        if "components" in keys:
            for c in _array_values(bp["components"]):
                comp = self.add_component(entity, c[1])
                added_components.append(comp)
                payload = c[3] if len(c) >= 3 else None
                if payload is not None and dependencies is not None:
                    comp_def = self.engine.data.components[c[1]]
                    if comp_def is not None and comp_def.base_id == "c_behavior":
                        sub_prog = dependencies[int(payload)]
                        if sub_prog is not None:
                            self._attach_behavior_to_component(comp, sub_prog)

        if "regs" in keys:
            for key, value in bp["regs"].items():
                if isinstance(key, str) and "|" in key:
                    comp_idx_s, reg_idx_s = key.split("|", 1)
                    comp = added_components[int(comp_idx_s) - 1]
                    comp.SetRegister(comp, int(reg_idx_s), value)
                else:
                    entity.SetRegister(entity, int(key), value)
        return entity

    def step(self, n: int = 1) -> None:
        """Advance the world ``n`` ticks. Per tick (docs/mock_world_spec.md's tick order): the tick
        counter advances, every attached behavior's interpreter runs one tick, movement resolves
        (waking components whose sync move completed or blocked), deferred callbacks drain."""
        for _ in range(n):
            self._World.tick = self._World.tick + 1
            for interp in self.interpreters:
                interp.run_ticks(1)
            self._World.StepMovement()
            self._World.DrainDeferred()

    # -- factions -------------------------------------------------------------------------------

    def faction(self, name: str, is_world: bool = False):
        """Get-or-create a faction table. Same name returns the same table."""
        return self._World.MakeFaction(name, is_world)

    def set_trust(self, a, b, level: str) -> None:
        """Set symmetric trust between two factions. `level` is "ALLY"/"ENEMY"/"NEUTRAL"."""
        self._World.SetTrust(a, b, level)

    # -- entities -------------------------------------------------------------------------------

    def spawn(
        self,
        def_id: str,
        faction: str = "player",
        x: int = 0,
        y: int = 0,
        **overrides,
    ):
        """Spawn an entity of real frame/def ``def_id``. ``faction`` may be a name (get-or-created)
        or a faction table. ``overrides`` set location or any state field (e.g.
        ``visibility_range=40``, ``is_damaged=True``, ``is_construction=True``)."""
        fac = self.faction(faction) if isinstance(faction, str) else faction
        ov = self.lua.table_from(overrides) if overrides else None
        return self._World.Spawn(def_id, fac, x, y, ov)

    def add_component(self, entity, comp_id: str):
        """Attach a real component ``comp_id`` to ``entity``; returns the component table."""
        return self._World.AddComponent(entity, comp_id)

    @property
    def entities(self):
        """Live list of spawned entity tables."""
        return [e for _, e in self._World.registry.items()]

    # -- tiles ----------------------------------------------------------------------------------

    def set_tile(
        self,
        x: int,
        y: int,
        *,
        plateau_delta: float | None = None,
        blight_delta: float | None = None,
        landscape_blocked: bool | None = None,
    ) -> None:
        """Author a tile's terrain. Signed deltas: >= 0 means on-plateau / in-blight. Unspecified
        fields keep their open-valley / no-blight / passable defaults."""
        fields = {}
        if plateau_delta is not None:
            fields["plateau_delta"] = plateau_delta
        if blight_delta is not None:
            fields["blight_delta"] = blight_delta
        if landscape_blocked is not None:
            fields["landscape_blocked"] = landscape_blocked
        self._World.SetTile(x, y, self.lua.table_from(fields))

    # -- sensing primitives (direct access; Phase 2 wires these through instruction dispatch) ---

    def distance(self, a, b) -> int:
        """``Map.GetDistance`` between two entities/coords: floored straight-line Euclidean
        (settled in-game by the RangeProbe run; see world.lua's distance-model note)."""
        return int(self._Map.GetDistance(a, b))

    def find_closest(self, owner, range_: int, filter_mask=None):
        """``Map.FindClosestEntity`` with a bare filter mask and no fine predicate -- for testing
        the broad-phase spatial + MatchFilter path directly. A falsy ``filter_mask`` (including the
        ``0`` PrepareFilterEntity returns for an impossible combination) is passed as *no mask*
        (matches any entity in range); build a real mask via :meth:`prepare_filter`."""
        return self._Map.FindClosestEntity(owner, range_, None, filter_mask or None)

    def prepare_filter(self, *filters):
        """Run the real ``PrepareFilterEntity`` over ``(id, num, id, num, ...)`` filter pairs,
        returning ``(mask, override_range)``. Feed the mask to :meth:`find_closest`/MatchFilter."""
        tbl = self.lua.table_from(list(filters))
        return self.lua.globals().PrepareFilterEntity(tbl)

    def is_seen(self, faction, entity) -> bool:
        return bool(faction.IsSeen(faction, entity))
