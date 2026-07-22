# Mock World for Behavior Testing — Design Spec

**Status:** Phases 0–3 done — the first usable version exists. Phase 3 (2026-07-19) went further
than planned: rather than extending the Python op dispatch, the whole simulated interpreter tier
was replaced by the **real game machinery** (`GetFactionBehaviorAsm`/`UploadBehavior` from
`data/library.lua`, the real `c_behavior:on_update` dispatch loop, real
`InstBeginBlock`/`call`/return — see `behavior_runtime.lua`), which closed the previously-deferred
block-stack-reuse item as a side effect and made `call` work for the first time. Movement +
`MockWorld.step` landed on top, and the golden differential acceptance test below passes (exact
tile sequence; totals/deltas within the documented tolerance). Phase 4 (combat) not started. The
arrival-tolerance probe (`tests/data/arrival_probe.bsf`) has now been **run in-game (2026-07-20)
and confirms the arrival model** — `test_arrival_probe.py` is a golden differential against the
real log (`tests/data/arrival_probe_ingame.log`). Work state lives in `todo.md`
(§ `desynced_toolkit` / BSF infrastructure), not here.

## Goal

Extend `desynced_toolkit`'s `Interpreter` so a behavior can be run against a **mock world**: a
populated, steppable environment with mock entities the behavior can sense, command, and be
affected by. Enough fidelity to write **automated tests for multi-unit behaviors** — the immediate
target is the combat squad (`combat_squad_spec.md`): a Captain, its gunners, and a hostile, stepped
forward tick by tick, asserting the coordination logic (membership scan, RALLY assembly gate,
PATROL heal-gate, command broadcast, gunner focus-fire dispatch) behaves as designed.

This is **not** a reimplementation of the game world. It is the smallest mock that lets the *real*
instruction funcs run without erroring on a missing entity graph. The scope for the first usable
version is **sensing + movement**, not combat/damage resolution (see "Phasing").

## Governing principle (why this is even tractable)

The `Interpreter` already never reimplements an instruction. Its per-op dispatch in `_step()` is
pure arg-marshalling — resolve each argument to a `state.mem[]` slot or frame-register address, then
call the genuine `data.instructions[op].func(comp, state, cause, ...)`. Control flow (branches,
block stack) is driven in Python; *decisions* (`check_number`, `jump`, `for_number`'s advance) are
delegated back to the real funcs.

So the reason world instructions don't run today is **not** a control-flow gap. It is that their
func bodies call engine-native primitives (`Map.FindClosestEntity`, `comp.faction:IsSeen`,
`comp:RequestStateMove`, …) and traverse an entity graph that `engine_stub.lua` doesn't provide.
The entire task is: **supply those primitives and that graph, backed by real game data, and let the
unchanged instruction funcs run.** Same doctrine as the rest of the project (CLAUDE.md's "reuse the
real Lua, both data and logic") — this mocks only the genuinely-external leaves, never a decision the
game already makes.

## What is reusable game Lua vs. what must be mocked

Established by tracing the func bodies of the squad's instructions (`get_closest_entity`,
`for_entities_in_range`, `for_signal_match`, `read_signal`, `get_distance`, `get_health`, `match`,
`domove`, `set_comp_reg`, `get_location`) in `data/instructions.lua`, and `FilterEntity`/
`PrepareFilterEntity` in `data/utilities.lua`.

### Reused unchanged (the point — do not touch)

- **Every instruction `func` body.**
- The argument getters `Get`/`GetNum`/`GetId`/`GetEntity`/`GetCoord` — all local aliases for the
  `InstGet*` functions defined in `instructions.lua` itself (the same reuse `engine_stub.lua`
  already documents for `Get`/`GetNum`). `GetId`/`GetEntity`/`GetCoord` are not yet exposed as
  globals to the harness.
- **`FilterEntity` / `PrepareFilterEntity`** (`data/utilities.lua`) — the real radar/match/signal
  filtering logic, including the whole `FilterStringToNum` table (`v_enemy_faction`,
  `v_own_faction`, `v_damaged`, `v_infected`, `v_bot`, `v_building`, …). This is the largest reuse
  win: entity filtering stays real, so any test using a radar/`match`/`for_signal_match` filter
  exercises the exact game predicate.
- The entity/faction helpers already inside `instructions.lua`: `GetSeenEntityOrSelf`,
  `GetFactionEntityOrSelf`, `GetAdjacentFactionEntityOrSelf`.

### The mock surface (genuinely engine-native, C++ — small and countable)

| Kind | Primitives | Mock responsibility |
|---|---|---|
| **Map / global** | `Map.FindClosestEntity(owner, range, pred, filter)`, `Map.GetDistance(a,b)`, `Map.GetEntityAt(x,y)`, `Map.Defer(fn)`; tile reads: `Map.GetTileData(x,y)`, `Map.GetPlateauDelta`/`Map.GetBlightnessDelta`, `Map.CountTiles` | spatial iteration + distance over the entity registry; per-tile terrain state (see "Tile model" below) |
| **Entity fields** | `.location`, `.faction`, `.def`, `.visual_def`, `.health`/`.max_health`, `.visibility_range`, `.exists`, `.is_construction`, `.is_damaged`, `.powered_down`, `.state_custom_1`, … | plain fields on the mock entity table |
| **Entity methods** | `:GetRegister(n)`/`:SetRegister(n,v)` (already present on the stub's `Owner`), `:MatchFilter(mask, faction)`, `:MoveTo(target, range)`, `:IsTouching(comp)`, `:FindComponent(id)`, `:CountItem(id)`, `:GetLocationXY()`, `:LookAt(t)` | methods on the entity/component metatables |
| **Component methods** | `comp:RequestStateMove(target, range) -> need_move, repeat_blocked`, component-register `:GetRegister`/`:SetRegister`, `GetComponentFromIndex` | movement resolution + per-component register banks |
| **Faction methods** | `faction:IsSeen(e)`, `faction:IsVisible(e)`, `faction:GetPowerGridIndexAt(e)`, `faction:GetTrust(other)`, and `==` identity | vision / trust / power-grid model |
| **Constants** | `FRAMEREG_SIGNAL/VISUAL/STORE/GOTO`, the `FF_*` frametype flags | globals in the stub |

The two load-bearing primitives are **`Map.FindClosestEntity`** (all sensing routes through it —
`get_closest_entity`, `for_entities_in_range`) and **`comp:RequestStateMove`** (all movement). Get
those two faithful and most of the squad is testable.

Note `FilterEntity`, though reused, bottoms out in rich entity reads (`e.def.type`,
`e.faction.is_world_faction`, `e.visual_def.explorable_race`, `e:GetRegisterId(FRAMEREG_GOTO)`,
state flags). This is why a faithful mock entity must carry the **real** definition table as its
`.def` — see below.

## Architecture

**World state lives in Lua** (tables + metatables), **orchestrated from Python.** Rejected the
alternative (Python entity objects handed to Lua via lupa) because `FilterEntity` and the
instruction funcs do heavy Lua-side field access and pass `Value` objects around; a Python/Lua
object boundary at every `e.def`/`e.faction` access would be constant friction and a faithfulness
risk. Python owns *orchestration* (spawn, step, mutate between steps, assert); Lua owns *state*.

```
MockWorld (Python facade)                  world.lua (new Lua module, loaded after the Data package)
  .spawn(def_id, faction, x, y) ─────────► World.Spawn: builds an entity table whose .def IS the
  .attach_behavior(entity, dcs_or_bsf)     real data.frames[def_id] (components' .def from
  .step(n_ticks)                           data.components); Entity/Component/Faction metatables
  .query(...) / .entities                  provide the engine-native methods above.
  entity.get_register(...) etc.            Map.FindClosestEntity/GetDistance iterate World.registry.
```

The entity's `.def` being the real `data.frames[...]` / `data.components[...]` table is the crux:
`movement_speed`, default `visibility_range`, socket layout, per-component `register_count`, and
every filter-by-type decision then come from real data, not invented numbers.

### A world tick (`MockWorld.step`)

Ordered per step:

1. **Advance each behavior-carrying entity's `Interpreter` by one tick** — the existing
   `run_ticks(1)` already honors the real `lock`/`unlock`/`wait` per-tick budget model.
2. **Resolve movement.** For each entity with a pending `RequestStateMove` goal or a
   `FRAMEREG_GOTO` target, step it toward the goal. **Positions are integer tiles: to behaviors a
   unit is *always* at an exact integer coordinate and teleports tile-to-tile** — the smooth
   in-game motion is animation only (user-confirmed; see the `reference_tile_occupancy_model`
   memory). So the entity `.location` every instruction func reads must stay integer; the mock
   accumulates fractional sub-tile progress *internally* and advances the logical coordinate one
   whole tile at a time when that progress crosses a tile boundary — it never exposes a fraction.
   Per-tick progress is `effective_speed / TICKS_PER_SECOND` tiles: **`def.movement_speed` is
   tiles-per-*second* stored at face value** (confirmed against the stat-display code and user — the
   *opposite* of the per-tick power convention; see `reference_movement_speed_model`), so it divides
   by `TICKS_PER_SECOND` (=5) and is typically fractional (3/5 = 0.6 tile/tick → a unit sits on a
   tile for a couple ticks, then jumps one tile). **Measured in-game 2026-07-18** (an Engineer,
   base `movement_speed = 2`, driven around a closed waypoint circuit by a logging behavior that
   printed each location change with a game-tick stamp): the model holds end-to-end, and the
   sub-tile progress accumulates along the *geometric path* — every observed step was a single
   tile to one of the 8 neighbors (never a multi-tile jump, so the mock must step diagonally too,
   not axis-by-axis), orthogonal steps took alternating 2/3 ticks (mean 2.57 ≈ 1/(2/5) = 2.5)
   while diagonal steps took 3/4 ticks (mean 3.35 ≈ 2.5·√2) — i.e. **a diagonal step costs ≈√2
   tiles of progress** (√2 vs. exactly 1.4 is below this measurement's resolution). Over the whole
   circuit: 63.3 Euclidean tiles in 157 ticks → 2.02 tiles/s, reproducing the base speed within
   1%, where Manhattan (2.39) and Chebyshev (1.75) metrics are clearly wrong. The same log pins
   `TICKS_PER_SECOND` empirically: 157 ticks over 31.40 s of wall clock = 5.000 ticks/s.
   Effective speed = base `movement_speed` + speed
   modules + terrain (flying units ignore terrain; pavement adds a bonus; blight slows units
   unless the faction or unit has a blight shield — see the Tile model section; being unpowered
   subtracts a large penalty except on units with no base power draw). The first-version mock is flat and
   powered, so effective = base + modules; terrain/pavement and the unpowered penalty are later
   refinements (the unpowered penalty is what justifies the squad Power Provider, so it lands with
   combat). This discrete tile advance is what makes `need_move` eventually return `false`, so
   arrival-gated logic (the RALLY assembly gate, `domove`'s "wait then repeat" loop) becomes
   testable over ticks rather than resolving instantly.
3. **Drain `Map.Defer` callbacks.**
4. *(Deferred — combat phase, not in first version.)* Resolve weapon `on_update` / damage.

Each behavior-carrying entity gets its own `Interpreter` instance (its own `state`/`comp`/`Memory`)
but shares the one `LupaEngine` and the one Lua world registry, so their sensing/commands interact.

## Tile model: biomes, terrain, passability (game-data survey 2026-07-18)

What the Lua layer actually exposes about a tile, surveyed for the mock. Upshot: **biome-linked
gameplay is real (blight damage/slow, plateau wind boost, blight-only machinery — see the effects
bullet), but it is all mediated by the continuous per-tile fields and their threshold deltas; no
discrete biome id per tile exists in the Lua layer, and ground passability is a single
engine-native bit** — so the mock's tile can still be a tiny record, not a terrain system.

- **The `data.biomes` table itself is render-only.** `data/biomes.lua` is texture blending driven
  by continuous per-tile noise fields (blightness / elevation / richness / variation + world
  height); nothing gameplay-side reads `data.biomes`, and gameplay never sees a biome *name* —
  every biome-correlated effect below keys off the fields/deltas directly. `data/cliffs.lua` is
  likewise just visual meshes, and `data/landfeatures.lua` is worldgen spawn selection over the
  same fields.
- **Continuous per-tile fields.** `Map.GetTileData(x, y)` → `.blightness` / `.elevation` /
  `.richness` / `.variation` (engine-native; consumed by explorable selection in
  `data/explorables.lua`), plus `Map.GetHeight`, `Map.GetWaterHeight`, `Map.GetPlateauHeight`, and
  the `Map.GetSettings()` thresholds (`plateau_level`, `blight_threshold`).
- **Threshold deltas are the form gameplay actually consumes.** `Map.GetPlateauDelta(…)` and
  `Map.GetBlightnessDelta(…)` (accept an entity or x,y; sign ≥ 0 means on-plateau / in-blight).
  Consumers: the instructions `check_altitude` ("Check Altitude") and `check_blightness` ("Check
  Blightness") — both visibility-gated like `is_passable` below — the filter values
  `v_plateau`/`v_valley`/`v_blight`/`v_not_blight` inside the real `FilterEntity` (already live
  since Phase 0), and plenty of component logic (blight power gating, solar-on-plateau,
  blight-halved work time). A mock tile therefore wants **signed plateau/blight deltas (or just
  booleans)**, not the raw noise fields, unless a test specifically needs `GetTileData`.
- **Biome-linked gameplay effects (user-enumerated 2026-07-18, mechanisms verified in source).**
  All key off the deltas above, mostly the blight one:
  - **Unprotected units in blight take damage and are slowed.** Protection is two flags the Lua
    layer *does* expose: `faction.has_blight_shield` (set by tech `on_unlock` — e.g. Blight
    Protection — and always-on for the alien/bugs/anomaly factions) and per-entity
    `entity.has_blight_shield` (the equippable `c_blight_shield` "Blight Shield" component). The
    damage/slow application itself is engine-native (the codex documents the damage; the slow is
    user-confirmed) — for the mock, the slow is one of the terrain speed modifiers in the
    effective-speed formula, gated on blight delta ≥ 0 and neither flag set; the damage belongs to
    the combat phase. The UI *refuses* manual move orders, target lock-on, and build placement
    into blight for unshielded factions (`LocationBlockedByBlight`, `ui/utilities.lua`) — but
    that gate is **UI-only (user-confirmed 2026-07-18)**: a *behavior* can move an unshielded
    unit into blight where the equivalent direct player order is refused (the unit just takes
    the damage/slow), and the native pathfinder itself will sometimes route a path *through*
    blight. So the mock's movement resolution must treat blight as fully passable terrain with
    consequences, never as blocking — only `landscape_blocked` blocks.
  - **Wind turbines double on the plateau**: `c_wind_turbine:on_update` doubles `max_power` when
    `Map.GetPlateauDelta(comp, -1) >= -0.1` and zeroes it during a dust storm — real component
    Lua that would run as-is in the mock if spawned.
  - **Blight-only machinery**: components with `requires_blight` refuse to work outside blight
    ("Must be placed inside the blight", with a dust storm counting as blight for this check),
    and `is_blight_boost` components halve their work time inside it. Placement gating for
    blight-race buildings rides the same checks plus the UI gate above.
  - **Blightness is mutable at runtime**: the terraformer family calls the engine-native
    `Map.StartTerraforming(owner, range, rate)` / `Map.StopTerraforming(id)` — "Purifying
    Terraformer" (`c_terraformer`, rate −0.001 toward `blight_threshold − 0.3`) vs. "Alien
    Terraformer" (`c_blight_terraformer`, +0.001 toward `blight_threshold + 0.3`). So a mock
    blight field is legitimately *static test input* only as long as no terraformer is in play.
- **Passability = one landscape bit + entity occupancy.** `Map.CountTiles(x, y, 0, true)` returns
  `blocked_landscape, blocked_entity` (its 3rd/4th returns are area counts — construction logic
  uses `select(4, Map.CountTiles(e, 1)) > 0` as "any passable tile adjacent"). The one instruction
  consumer is `is_passable` ("Is Passable"), whose semantics are worth reproducing exactly: on a
  *visible* tile it merges landscape + entity blocking; on a merely *discovered* tile it uses
  landscape + last-**seen**-entity blocking only; on an undiscovered tile **neither exec pin fires**
  (`state.counter` untouched → plain fallthrough to next). *What makes* landscape blocked (water
  under `water_height`, cliff slope, …) is engine-native and not recoverable from Lua — the mock
  should take a per-tile `landscape_blocked` boolean as authored test input and route `is_passable`,
  movement blocking, and the ground occupancy layer through it. The user-stated fact that some tile
  types are impassable to non-flyers then falls out naturally: `landscape_blocked` gates **ground
  units only**; flyers ignore it (consistent with flyers also ignoring terrain speed modifiers and
  ground occupancy).
- **"Flying" is two different notions — don't conflate them.** (a) The *filter* notion: the real
  `FilterEntity` computes `v_is_flying` ("Flying") as `e.def.cost_modifier == 0` and
  `v_is_grounded` as `~= 0` — a pure data convention (every Drone-size frame sets
  `cost_modifier = 0`). The mock inherits this for free by running the real `FilterEntity`, but it
  means mock entity defs must carry a faithful `cost_modifier` or Flying/Grounded filters silently
  misclassify. (b) The *physics* notion (ignores `landscape_blocked`, shares tiles, no terrain
  speed modifiers): engine-native, and its real test is unknown — a `Flyer` frame flag exists but
  only on `f_flyer_m` (plus `Space` on satellites), while the visually-flying logistics drones
  don't carry it. For the mock, make "flies" an explicit per-entity boolean derived from the frame
  (size `"Drone"`, `slot_type` drone/flyer/satellite, and the `Flyer` flag coincide for every frame
  that matters); flag it as a modeling choice to revisit only if an in-game test ever needs the
  engine's exact rule.

## Phasing (first usable version = Phases 0–3, sensing + movement)

**Phase 0 — spike: load the Data package under the stub. DONE (2026-07-18).** The minimal include
subset turned out to be exactly the expected `utilities → values → items → components → frames`
(confirmed empirically: the intervening real includes `library/actions/biomes/behaviors/puzzles`
are *not* needed at load). `Frame:RegisterFrame`/`Comp:RegisterComponent` and the recipe helpers
are all defined *inside* those files — no stubs needed for them; the only genuinely-missing
load-time surface was six engine constants/tables. `LupaEngine(load_data_registries=True)` (the
new default) loads that subset before `instructions.lua` and then builds `data.all` (merge of
values/items/components/frames, each def tagged `data_name`) exactly as the engine's post-load step
does. Populated: `data.frames` (177), `data.components` (310), `data.items` (96), `data.values`
(114), `data.all` (697); `FilterEntity`/`PrepareFilterEntity` live. Stubs added to
`engine_stub.lua`: the `FF_*` bit flags (layout self-consistent for PrepareFilterEntity↔the future
mock `MatchFilter`, deliberately *not* reverse-engineered to the engine's exact bits — FilterEntity
keys off its own `FilterStringToNum`, so only those two agree-with-each-other consumers exist),
`FRAMEREG_*`, `TICKS_PER_SECOND`, `blight_threshold` in `Map.GetSettings`, empty
`FactionAction`/`EntityAction`/`UIMsg`/`Delay` handler tables, a no-op `GetFactionBehaviorAsm`, and
an auto-vivifying `data` table. Cost ~29 ms per construction (once per session test fixture).
Covered by new `test_lua_runtime.py` tests; `load_data_registries=False` preserves the old
instructions-only runtime. The predicted long tail of load-time engine calls (see "risks" below)
did not materialize.

**Phase 1 — engine-native primitives in `world.lua`.** Implement the mock-surface table above as
Lua metatables over `World.registry`:
- `Map.FindClosestEntity` = iterate registry, distance-filter by `range`, run the caller's
  predicate (which calls the real `FilterEntity`), keep the closest — matching how the real funcs
  use it.
- `:MatchFilter` = the `FF_*` bitmask test against the prepared filter mask.
- `faction:IsSeen(e)` / `:IsVisible(e)` = start with a simple model (seen if within any
  own-faction entity's `visibility_range`); the squad's whole premise is the vision lock (§1.1 of
  the squad spec), so this must be honest but need not be pixel-accurate initially.
- Define `FRAMEREG_*` and `FF_*` globals — cross-check `FRAMEREG_*` values against
  `ui/RegisterSelection.lua` and `behavior_format.md`, and the `FF_*` flags against
  `data/utilities.lua`'s own definitions.

**Phase 2 — extend the interpreter op dispatch.** Add arg-marshalling arms for the world ops the
squad uses: `get_location`, `get_distance`, `get_health`, `get_closest_entity`, `read_signal`,
`set_comp_reg`/`get_comp_reg`, `value_type`, `modulo`, `check_bit`/`bitwise_op`, `match`, and the
`domove` family. Each arm is a few lines like the existing ones (resolve arg slots, call
`engine.call`). Fold in the block-producing world loops `for_entities_in_range`/`for_signal_match`
— these return `BeginBlock`, so they reuse the existing loop-block driver, the same tier as
`for_number`. **Side benefit:** this alone unblocks the `library/hexat.dcs` unit-Origin path, whose
`value_type`/`get_location`/`modulo` are exactly the currently-unhandled ops (see
`hex_expansion_math.md`, "Deployed copy").

**Phase 3 — movement + multi-entity stepping. DONE (2026-07-19), with a scope expansion:** the
user chose to satisfy the fixture's `call` requirement by adopting the **real behavior machinery**
outright instead of extending the Python-simulated tier — real compiler (`GetFactionBehaviorAsm`),
real import (`UploadBehavior`, including `call.sub` dependency remapping), and a port of the real
`c_behavior:on_update` dispatch loop delegating every dead end to the real `c_behavior_on_end`
(`behavior_runtime.lua`; the Python `Interpreter` is now only activation scheduling). That closed
todo.md's separate block-stack-reuse item, corrected the `wait` sleep semantics off the
dispatcher's own source (sleep N = resume N ticks later, not N skipped ticks), and gave `call`
by-reference parameters/shared arrays for free (pinned in `test_interpreter_call.py`). Movement
landed as designed below (`world.lua`'s movement section documents each rule's provenance; the
step-direction rule — diagonal while both axes differ, then straight — was read directly off the
golden log's legs). The acceptance test passes: exact tile sequence (56/56 records), per-step
deltas within ±1 except ±2 exactly on direction-change steps, four of five clean legs
tick-exact, closed-circuit total 157+3 with the excess decomposed in
`test_movement_circuit_golden.py`'s docstring (+1 uniform print-phase offset; ~+2 pre-log
residual sub-tile progress the real Engineer carried into the log — its first two intervals are
2,2 against the 2,3 steady state; real leg 3 finishing in 27 < ⌈27.68⌉ ticks proves the real
engine carries fractional progress across move orders, which the mock reproduces). Original plan
follows; the fixture description remains accurate.

Implement `RequestStateMove`/`MoveTo`/`@goto`
resolution in the tick loop and `MockWorld.step(n)` driving all interpreters plus movement together.
First real test targets: a `for_signal_match` membership scan resolving a live roster, and a
`get_closest_entity`-driven RALLY broadcast. **Golden differential fixture (user-designated):**
the 2026-07-18 movement-measurement behavior and its real in-game debug log are checked in as
`tests/data/movement_circuit_test.dcs` / `movement_circuit_test_ingame.log` — the behavior walks
an Engineer around the six `HexAt` R=1 ring corners, printing each location change with a
Simulation Tick stamp. Phase 3's acceptance test: run the same `.dcs` in the mock (Engineer def,
origin `(-14, 51)`, start on the NW corner `(-19, 60)`) and diff the mock's print stream against
the real log — same tile sequence, same per-step tick deltas (modulo the poll-aliasing jitter of
±1 tick on individual steps; the closed-circuit total of 157 ticks should match near-exactly).
This exercises the whole stack at once: `domove`/arrival re-issue, the Euclidean sub-tile
accumulation, 8-connected stepping, `simulation_tick`, `value_type`/`get_location`/`modulo`
(the `HexAt` unit-Origin path `Interpreter` can't currently drive), `sequence` cascade polling,
and `wait`.

**Phase 4 (deferred, not first version) — combat fidelity.** Optionally run the real weapon
`c_turret:on_update` (`data/components.lua`) plus a minimal HP-decrement/damage model, so ENGAGE
focus-fire, the retreat latch, and victory detection are exercisable end to end. Deferred by
decision: the sensing/rally half is the larger correctness risk and needs no damage model.

## Target test shape

```python
w = MockWorld(engine)
cap = w.spawn("f_bot_1m_c", faction="player", x=0, y=0)
cap.visibility_range = 40
cap.attach_behavior("library/squad-captain.dcs", params={...})

g1 = w.spawn("f_bot_1m_b", faction="player", x=2, y=0)
g1.attach_behavior("library/squad-gunner.dcs", params={"Captain": cap})

enemy = w.spawn("f_larva1", faction="bugs", x=35, y=0)  # "Larva" (real frame id -- an earlier
                                                        # draft wrote the nonexistent f_bug_larva)

w.step(20)
assert cap.get_register("SIGNAL").entity is enemy            # Captain broadcast ENGAGE
assert g1.weapon_component().get_register(1).entity is enemy # gunner focus-fired it
```

## Open items / risks

- ~~**Phase 0 feasibility is the primary risk.**~~ **Resolved (2026-07-18):** loading the minimal
  subset needed only six small constant/table stubs, no long tail. See the Phase 0 note above.
- **`faction:IsSeen`/`IsVisible` fidelity — the top vision risk, and it has a load-bearing
  *collective* question.** The squad design turns entirely on the vision lock, and specifically on
  vision being *shared across the faction*: the turret target gate is `owner_faction:IsVisible(e)`
  (a faction-level query — `c_turret` target selection, `components.lua`), so the whole Captain
  concept assumes an unarmed high-`visibility_range` Captain seeing an enemy lets a *different*
  gunner fire on it even when that gunner can't see the enemy itself. Confirm this **spotter case**
  in-game before investing in the mock (Captain in vision range of enemy + gunner out of its own
  vision → does the gunner fire? walk Captain away → does it stop?); it validates the architecture
  *and* tells the mock exactly what `IsVisible` should compute (union of members' vision bubbles,
  radius = `visibility_range` in tiles at face value). Too-simple a model could make a test pass for
  the wrong reason. The related-but-distinct *sensing* question is now **answered (source +
  in-game observation, 2026-07-18)**: instruction sensing is strictly the sensing unit's own
  radius and never consults faction vision. `get_closest_entity` ("Closest Unit") searches
  `math.min(override_range or visibility_range, visibility_range)` — a Max Range filter can only
  *shrink* the radius — and `for_entities_in_range` ("Loop Units in Range") clamps its Range arg
  to `visibility_range` (Infinite = exactly `visibility_range`). Observed live: a Scout
  (visibility 10) parked next to an alien Observer building (visibility 80) does **not** return
  an enemy nest that the building plainly reveals on screen — faction vision feeds the player's
  view and (per the spec's premise, still to confirm) weapon targeting, not sensing instructions.
  The radar *component* path is different again: `c_portable_radar:on_update` scans with the
  radar's own `range` field (Scout Radar 30, Small Radar 40), deliberately beyond vision — it
  even reveals the found entity's area afterward — so radar-register reads (the Async Radar
  path) are the only way a behavior senses past its own visibility bubble. Mock consequence:
  `Map.FindClosestEntity`'s mock needs no faction-vision gating for the instruction callers, and
  a mocked radar must use the component def's `range`, not `visibility_range`.
- **`RequestStateMove` arrival tolerance — settled 2026-07-20 (in-game ArrivalProbe run).** The
  per-tile advance was already settled empirically (see the tick-step note); *when* `need_move`
  flips `false` is now measured too. The mock's model (`world.lua`: arrived ⟺
  `get_distance(unit, target) ≤ range`, floored at 1 for an entity target whose tile can't be
  entered) is **confirmed exactly** by `tests/data/arrival_probe.bsf` run in-game: it sync-moves
  to a coordinate at ranges 0/2/5 and to a bound Target unit at ranges 0/2/5, printing case
  marker + `get_distance` readout + stop coordinate per case. The real log
  (`tests/data/arrival_probe_ingame.log`, a 3x3 Command Center as the entity target) gives
  distance readouts 0/2/5 (coordinate) and 1/2/5 (entity, range-0 floored to 1) — the model's
  predictions. `tests/test_arrival_probe.py` is now a golden differential: the coordinate cases
  reproduce the log tile-for-tile, the entity cases reproduce its arrival gate (the stop tile
  differs only because the mock uses a point target where the game approached a 3x3 footprint —
  the separate, already-flagged pathfinding divergence). The RALLY gate's testability is
  unblocked.
- **Distance metrics — settled 2026-07-19 (in-game RangeProbe run + user observations).**
  - **`Map.FindClosestEntity`'s range gate is floored Euclidean**: in range `R` ⟺
    `floor(dist) ≤ R`, equivalently `dist < R+1`. Settled by the in-game RangeProbe run
    (`tests/data/range_probe.bsf` — sweeps `for_entities_in_range` Range 1..15 and reports the
    minimal detecting Range on `@signal`; measured results are the golden rows in
    `test_mock_world_dispatch.py`): offsets (3,0)/(2,2)/(3,2)/(3,3)/(4,3)/(6,3) gave
    3/2/3/**4**/**5**/**6** — exactly `floor(Euclidean)`, while (3,3)/(4,3) rule out Chebyshev,
    (6,3) rules out floored-octile, and (2,2)/(3,2) rule out round/ceil/real-valued Euclidean.
    The Blight Magnifier's confirmed 5×5 square at `range = 2` (`blight_magnifier_mining.md`) is
    the **floor artifact of this circular gate at small radius** (the corner sits at 2√2 ≈ 2.83,
    which floors to 2) — it briefly read as Chebyshev evidence; the user's "everything is some
    form of Euclidean" hypothesis was right. Every sensing instruction routed through the native
    function inherits this gate.
  - **"Closest" selection among gate-passers is Euclidean** (user-observed in-game 2026-07-19) —
    the winner is the straight-line-nearest candidate.
  - **`Map.GetDistance` (the `get_distance` readout) is that same function — floored
    straight-line Euclidean, and the gate is literally `get_distance ≤ R`.** The probe's `@store`
    readouts were **identical to the minimal detecting ranges at every offset** (user-reported),
    so gate and readout are one function. The (6,3) row settled the readout's *metric*: an
    unobstructed-grid-path-length model (octile ≈ 7.24 there — a working hypothesis mid-probe)
    would have read 7; the game read 6 = `floor(6.71)`. Floor (not round) is pinned by (2,2):
    2.83 → 2. The distinction that survives: **movement cost** still accumulates ≈√2 per
    diagonal step (the measured movement model) — a property of motion, not of the distance
    readout. Everything pinned by tests (`test_mock_world.py`, `test_mock_world_dispatch.py`;
    the probe's golden rows carry both columns).
  - **The faction-vision bubble looks like the same shape** (user eyeball observation
    2026-07-19): the on-screen vision area appears identical in shape to the sensing area, so
    the mock's `IsSeen`/`IsVisible` use the same one function (`GetDistance ≤ visibility_range`
    — a floored-Euclidean disc, fringe tiles included). Observation-grade provenance, not
    probe-measured — a RangeProbe-style vision instrument would be needed only if fringe-tile
    vision precision ever becomes load-bearing (e.g. the Captain's vision-lock band tuning).
  - Real `get_distance` on a multi-tile entity means closest-tile, and center-tile after a
    `get_location` (rounds up on ties) — see the project memory on this. The mock should reproduce
    at least the single-tile-entity case exactly and document any multi-tile simplification.
- **Tile occupancy (ground-layer only).** Only one ground unit *or* building occupies a tile, but
  **multiple flyers can share a tile** (user-confirmed) — so occupancy constrains the ground layer
  only. The first sensing+movement version can skip occupancy (a lone unit approaching an enemy
  never contends for a tile), but multi-unit **RALLY** geometry needs it for *ground* squads — the
  anti-scatter gate gathers them onto *adjacent* tiles, not stacked on the rally point — while an
  all-flyer squad can genuinely converge on one tile. Model it before asserting rally-assembly
  behavior for ground units. Ties to the integer-tile teleport model in the movement step above
  (`reference_tile_occupancy_model`).
- ~~**Reusing the real block stack.**~~ **Resolved (2026-07-19), folded into Phase 3:** the real
  `InstBeginBlock`/`GetFactionBehaviorAsm`/`c_behavior_on_end` machinery now runs everything (see
  the Phase 3 note and `behavior_runtime.lua`); the world loops ride the real driver.
- **Combat (Phase 4)** is explicitly out of the first version; revisit once sensing/movement tests
  exist and it is clear how much of ENGAGE they already cover.
