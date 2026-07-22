-- Mock world for behavior testing -- Phase 1: engine-native primitives.
--
-- See docs/mock_world_spec.md. This file supplies the small, countable set of genuinely engine-native
-- (C++-side) leaves that the real instruction funcs and FilterEntity/PrepareFilterEntity call but
-- that engine_stub.lua does not provide: the spatial entity registry (Map.FindClosestEntity /
-- GetDistance / GetEntityAt), per-tile terrain reads, and the Entity/Faction/Component metatable
-- methods (:MatchFilter, faction:IsSeen/:IsVisible/:GetTrust/:GetPowerGridIndexAt, register banks).
-- EVERYTHING above this line -- every instruction func, FilterEntity, the arg getters -- is reused
-- unchanged; this is only the mocked leaves (CLAUDE.md's "reuse the real Lua, both data and logic").
--
-- Loaded after the Data registries + instructions.lua, so `data.*`, FilterEntity, the FF_*/FRAMEREG_*
-- constants (from engine_stub.lua), and Map.GetSettings all already exist. Map is EXTENDED here
-- (Map.GetSettings stays as the stub defined it); World is new.
--
-- State lives in Lua, orchestration in Python (mock_world.py). World.Reset() clears it so a
-- session-scoped engine can host many independent MockWorld instances.

World = { registry = {}, factions = {}, deferred = {}, tiles = {}, next_id = 1, tick = 0 }

function World.Reset()
	World.registry = {}
	World.factions = {}
	World.deferred = {}
	World.tiles = {}
	World.next_id = 1
	World.tick = 0
end

-- The simulation tick counter (`simulation_tick`'s func reads it). MockWorld.step advances it
-- once per world tick; the absolute value is arbitrary (the real game's is a huge running count),
-- only deltas mean anything.
function Map.GetTick()
	return World.tick
end

--------------------------------------------------------------------------------------------------
-- Geometry helpers
--------------------------------------------------------------------------------------------------

-- Resolve an entity OR a coordinate value to integer tile x,y. Entities carry `.location` ({x=,y=});
-- a coordinate is a plain `{x=,y=}` (the shape InstGetCoord / a Value's `.coord` hands back). Extra
-- args (some real Map.* signatures take a threshold/radius after the target) are ignored.
local function xy_of(target)
	-- non-table (nil, or a bare number when a caller uses an (x, y, ...) signature like
	-- Map.CountTiles) -> nil, so those callers' own numeric fallback can take over; indexing a
	-- number would otherwise error before the fallback ran
	if type(target) ~= "table" then return nil end
	local loc = target.location
	if loc ~= nil then return loc.x, loc.y end
	if target.x ~= nil then return target.x, target.y end
	-- a component: fall back to its owner entity's location
	if target.owner ~= nil and target.owner.location ~= nil then
		return target.owner.location.x, target.owner.location.y
	end
	return nil
end

-- Distance model -- SETTLED in-game 2026-07-19 by the RangeProbe run (tests/data/range_probe.bsf;
-- its measured numbers are the golden rows in test_mock_world_dispatch.py). One distance function
-- explains everything:
--
-- * Map.GetDistance (the get_distance readout) is FLOORED STRAIGHT-LINE EUCLIDEAN:
--   floor(sqrt(dx^2 + dy^2)). Measured per-offset readouts (3,0)=3 (2,2)=2 (3,2)=3 (3,3)=4
--   (4,3)=5 (6,3)=6 match it exactly. (6,3)=6 is the decisive row: the unobstructed 8-connected
--   PATH LENGTH there is 6 + 3*(sqrt(2)-1) ~ 7.24, so any path-length model -- an earlier working
--   hypothesis -- would have read 7; floor(sqrt(45)) = 6. Floor (not round/ceil) is pinned by
--   (2,2): 2.83 -> 2. Movement COST still accumulates ~sqrt(2) per diagonal step (the measured
--   movement model) -- that is a property of motion, not of this readout.
-- * Range GATES (Map.FindClosestEntity / Map.GetEntitiesInRange, below) are exactly
--   `GetDistance(a, b) <= range`: the measured minimal detecting Range equaled the GetDistance
--   readout at every offset (user-reported identical @signal/@store), i.e. gate and readout are
--   one function. Chebyshev was ruled out by (3,3)/(4,3), octile by (6,3), round/ceil Euclidean
--   by (2,2)/(3,2). The magnifier's confirmed 5x5 coverage at range 2
--   (blight_magnifier_mining.md) is the floor artifact of this circular gate at small radius
--   (corner 2*sqrt(2) ~ 2.83 floors to 2), not a square metric.
-- * "Closest" ordering among gate-passers (get_closest_entity's winner) is Euclidean
--   (user-observed in-game). The mock orders by the UNROUNDED value -- a modeling refinement the
--   probe can't distinguish from ordering-by-floored-value + some tie-break; revisit only if a
--   tie case ever matters.
--
-- The faction-vision bubble uses this same function too (GetDistance <= visibility_range) --
-- user eyeball observation (2026-07-19): the on-screen vision shape looks identical to the
-- sensing shape. Observation-grade, not probe-measured.
-- For a multi-tile entity the real engine measures to the closest tile (see
-- reference_get_distance_closest_tile) -- first-version mock entities are single-tile, so the
-- distinction does not yet arise.

-- Unrounded Euclidean: the "closest" ordering value.
local function euclid(a, b)
	local ax, ay = xy_of(a)
	local bx, by = xy_of(b)
	if ax == nil or bx == nil then return math.huge end
	local dx, dy = ax - bx, ay - by
	return math.sqrt(dx * dx + dy * dy)
end

function Map.GetDistance(a, b)
	local d = euclid(a, b)
	if d == math.huge then return REG_INFINITE end
	return math.floor(d)
end

-- The range gate IS the readout (see the distance-model note): in range iff GetDistance <= range.
local gate_distance = Map.GetDistance

--------------------------------------------------------------------------------------------------
-- Tiles (see docs/mock_world_spec.md "Tile model"). A tile is a tiny record; defaults are open valley,
-- no blight, passable. Authored per-tile as test input via World.SetTile.
--------------------------------------------------------------------------------------------------

local DEFAULT_TILE = { plateau_delta = -1, blight_delta = -1, landscape_blocked = false }

function World:tile(x, y)
	local row = self.tiles[x]
	if row == nil then return DEFAULT_TILE end
	return row[y] or DEFAULT_TILE
end

function World.SetTile(x, y, fields)
	local row = World.tiles[x]
	if row == nil then row = {} World.tiles[x] = row end
	local t = row[y]
	if t == nil then t = { plateau_delta = -1, blight_delta = -1, landscape_blocked = false } row[y] = t end
	if fields.plateau_delta ~= nil then t.plateau_delta = fields.plateau_delta end
	if fields.blight_delta ~= nil then t.blight_delta = fields.blight_delta end
	if fields.landscape_blocked ~= nil then t.landscape_blocked = fields.landscape_blocked end
	return t
end

-- Signed deltas: >= 0 means on-plateau / in-blight (matches FilterEntity's v_plateau/v_blight tests
-- and the check_altitude/check_blightness instructions). Called three ways in the real funcs:
-- `(entity)`, `(entity, threshold)`, and `(x, y, threshold)` -- so accept an entity/coord OR a
-- literal x,y pair; trailing threshold args are ignored (first-version tiles have no sub-threshold).
local function delta_xy(a, b)
	if type(a) == "number" and type(b) == "number" then return a, b end
	return xy_of(a)
end

function Map.GetPlateauDelta(a, b)
	local x, y = delta_xy(a, b)
	if x == nil then return -1 end
	return World:tile(x, y).plateau_delta
end

function Map.GetBlightnessDelta(a, b)
	local x, y = delta_xy(a, b)
	if x == nil then return -1 end
	return World:tile(x, y).blight_delta
end

function Map.GetTileData(x, y)
	local t = World:tile(x, y)
	-- The engine exposes continuous noise fields here; the mock only tracks what gameplay consumes
	-- via the deltas above, so these are nominal. Widen only if a test needs raw GetTileData.
	return { blightness = t.blight_delta, elevation = t.plateau_delta, richness = 0, variation = 0 }
end

--------------------------------------------------------------------------------------------------
-- Factions
--------------------------------------------------------------------------------------------------

local Faction = {}
Faction.__index = Faction

function World.MakeFaction(name, is_world)
	local f = World.factions[name]
	if f then return f end
	f = setmetatable({
		name = name,
		meta_type = "faction", -- GetFactionBehaviorAsm's faction-or-comp dispatch keys on this
		is_world_faction = is_world or false,
		trust = {},
		-- The saved-behavior store the real GetFactionBehaviorAsm/SetBehavior (data/library.lua)
		-- compile from; MockWorld.attach_behavior imports into it (real per-faction shape).
		extra_data = { library = {} },
	}, Faction)
	World.factions[name] = f
	return f
end

-- `InstError`'s user notification path; the mock has no UI. The error still stops the behavior
-- (InstError falls through to the real `exit` func), and the notification closure is recorded so
-- a test can at least assert that an instruction error happened.
function Faction:RunUI(...)
	World.last_run_ui = ...
end

-- Trust is symmetric here (the game's can differ per direction, but the first-version mock has no
-- one-sided-trust test). Levels are the engine's own strings: "ALLY" / "ENEMY" / "NEUTRAL".
function World.SetTrust(a, b, level)
	a.trust[b] = level
	b.trust[a] = level
end

-- Real call shapes in data/instructions.lua, all three of which must work here:
--   faction:GetTrust(other_faction)        -> "ALLY"/"ENEMY"/"NEUTRAL"  (gettrust's dispatch)
--   faction:GetTrust(entity)               -> same, via the entity's own faction (transfer checks)
--   faction:GetTrust(entity, "ALLY")       -> boolean comparison form (for_inventory_item's
--                                             `comp.faction:GetTrust(ent, "ALLY")`)
-- The "OWN" return for self is a mock invention (the engine's own-faction return value is
-- unobserved); real consumers only ever dispatch on ALLY/ENEMY/NEUTRAL, so anything else falls
-- through their branches, which "OWN" reproduces safely.
function Faction:GetTrust(other, compare)
	local f = other
	if f ~= nil and f.faction ~= nil then f = f.faction end -- an entity: resolve to its faction
	local level
	if f == self then level = "OWN" else level = self.trust[f] or "NEUTRAL" end
	if compare ~= nil then return level == compare end
	return level
end

-- First-version vision model (docs/mock_world_spec.md Phase 1): an entity is "seen" if it is on this
-- faction's own side, or lies within the visibility_range of ANY of this faction's entities.
-- The per-entity bubble uses the SAME one distance function as everything else
-- (GetDistance <= visibility_range, i.e. a floored-Euclidean disc): user eyeball observation
-- (2026-07-19) says the on-screen vision shape looks identical to the sensing shape -- an
-- observation, not a probe measurement, so fringe tiles (where floor(dist) == vis but the
-- unrounded distance exceeds it) are modeled-in but only eyeball-confirmed. Still not
-- fog-of-war-accurate (no discovery history) -- the squad's premise only needs the vision lock
-- to be real, not pixel-perfect.
function Faction:IsSeen(e)
	if e == nil then return false end
	if e.faction == self then return true end
	for _, w in pairs(World.registry) do
		if w.exists and w.faction == self and (w.visibility_range or 0) > 0 then
			if Map.GetDistance(w, e) <= w.visibility_range then return true end
		end
	end
	return false
end

-- Called two ways in the real funcs: `faction:IsVisible(x, y)` (is_passable) and `faction:IsVisible(e)`.
function Faction:IsVisible(a, b)
	if b ~= nil then
		-- coordinate form: visible if within any own entity's visibility_range (same
		-- floored-Euclidean disc as IsSeen above)
		for _, w in pairs(World.registry) do
			if w.exists and w.faction == self and (w.visibility_range or 0) > 0 then
				if Map.GetDistance(w, { x = a, y = b }) <= w.visibility_range then return true end
			end
		end
		return false
	end
	return self:IsSeen(a)
end

-- Broad-phase signal scan (`for_signal_match`): return own-faction entities that currently hold a
-- non-empty signal register value. The real for_signal_match func re-filters these precisely (by
-- signal id, or an embedded-entity MatchFilter), so this only supplies the candidate set.
function Faction:GetEntitiesWithRegister(reg_index, signal, include_flag)
	local out = {}
	for _, e in pairs(World.registry) do
		if e.exists and e.faction == self then
			local r = e.registers[reg_index]
			if r ~= nil and not (r.num == 0 and r.id == nil and r.entity == nil and r.coord == nil) then
				out[#out + 1] = e
			end
		end
	end
	return out
end

-- Fog-of-war "has this tile been discovered". First-version mock has no fog model -- everything is
-- discovered (check_altitude/check_blightness gate on this).
function Faction:IsDiscovered(coord)
	return true
end

-- Power grid: first-version mock has no grid model, so nil (v_in_powergrid -> false, and the
-- autobase same-grid remote-write path stays inert). An entity/coord may carry a `.power_grid`
-- field to opt into a simple explicit model.
function Faction:GetPowerGridIndexAt(target)
	if target == nil then return nil end
	if target.power_grid ~= nil then return target.power_grid end
	if target.owner ~= nil then return target.owner.power_grid end
	return nil
end

--------------------------------------------------------------------------------------------------
-- Entities
--------------------------------------------------------------------------------------------------

-- Frametype bits for MatchFilter, derived from the real def. The FF_* layout is engine_stub.lua's
-- own self-consistent scheme (documented there): only PrepareFilterEntity (producer) and this
-- consumer share it, so they need only agree with each other. Discriminations FilterEntity makes
-- more finely (bot vs building by movement_speed, scattered-resource by name) ride on FF_OPERATING /
-- FF_DROPPEDITEM and are settled by the real FilterEntity predicate afterward, not here.
local function frametype_bits(e)
	if e.is_construction then return FF_CONSTRUCTION end
	local edef = e.def
	local t = edef.type
	if t == "Resource" then return FF_RESOURCE end
	if t == "DroppedItem" then return FF_DROPPEDITEM end
	if t == "Foundation" then return FF_FOUNDATION end
	return FF_OPERATING
end

-- Entity's faction bit RELATIVE to the querying faction -- the relational half of a filter mask
-- (own/enemy/ally/neutral/world). Matches how PrepareFilterEntity narrows the faction bits and how
-- FilterEntity's own faction predicates read (e.faction.is_world_faction, GetTrust).
local function relative_faction_bit(ef, qf)
	if ef == qf then return FF_OWNFACTION end
	if ef.is_world_faction then return FF_WORLDFACTION end
	local trust = ef:GetTrust(qf)
	if trust == "ALLY" then return FF_ALLYFACTION end
	if trust == "ENEMY" then return FF_ENEMYFACTION end
	return FF_NEUTRALFACTION
end

local Entity = {}
Entity.__index = Entity
-- Marker for engine_stub's `coerce`: in the REAL engine an entity is USERDATA, and code like
-- for_entities_in_range's `.next` (`if type(elem) == "table" then Set(out, elem) else
-- Set(out, { entity = elem })`) relies on that to tell a raw entity from a value-shaped wrapper
-- table. Mock entities are Lua tables, so they'd fall into the wrong branch and get field-stripped
-- (the `.id` frame-id field misread as an id literal -- found 2026-07-19 via a loop-output entity
-- read-back). The marker lets coerce wrap a bare mock entity as { entity = v }, matching what the
-- native register conversion does with entity userdata.
Entity.__is_entity = true

-- Frame registers (Goto=1, Store=2, Visual=3, Signal=4 -- the corrected wire mapping, see
-- engine_stub.lua's FRAMEREG_* block) -- same bank the engine_stub Owner models,
-- reachable from InstGet as `comp.owner:GetRegister(1..4)`. Values are coerced through the global
-- Tool.NewRegisterObject (engine_stub) so downstream `.num`/`.coord`/`.id`/`.entity` reads work.
function Entity:GetRegister(n) return self.registers[n] or NewValue(0) end
function Entity:SetRegister(n, v) self.registers[n] = Tool.NewRegisterObject(v) end
function Entity:GetRegisterNum(n) return (self.registers[n] or NewValue(0)).num end
function Entity:GetRegisterCoord(n) local r = self.registers[n] return r and r.coord end
function Entity:GetRegisterId(n) local r = self.registers[n] return r and r.id end
function Entity:GetRegisterEntity(n) local r = self.registers[n] return r and r.entity end

function Entity:GetLocationXY() return self.location.x, self.location.y end
function Entity:GetRangeSquaredTo(other)
	local ax, ay = self.location.x, self.location.y
	local bx, by = xy_of(other)
	if bx == nil then return math.huge end
	local dx, dy = ax - bx, ay - by
	return dx * dx + dy * dy
end

function Entity:CountItem(id) return self.inventory[id] or 0 end

-- Component lookup. The real native signature (seen in `UpdateEntityBehaviorState`,
-- data/library.lua: `e:FindComponent("c_behavior", true, i)`) takes an id, a flag, and a 1-based
-- occurrence index; the flag is modeled as "match by base_id family too" (so the scan finds
-- c_integrated_behavior when asked for c_behavior -- consistent with what that caller counts;
-- the engine's exact flag meaning is unverified, flagged as a modeling choice).
function Entity:FindComponent(id, by_base, index)
	local n = 0
	for _, c in ipairs(self.components) do
		if c.id == id or (by_base and c.def.base_id == id) then
			n = n + 1
			if n >= (index or 1) then return c end
		end
	end
	return nil
end

-- Adjacency (`ent:IsTouching(comp)`): true when the two entities occupy neighboring tiles
-- (8-connected) or the same tile. Used by the remote-register adjacency gate.
function Entity:IsTouching(comp)
	local other = comp.owner or comp
	local ox, oy = xy_of(other)
	if ox == nil then return false end
	return math.abs(self.location.x - ox) <= 1 and math.abs(self.location.y - oy) <= 1
end

-- mask = PrepareFilterEntity(...)'s combined frametype|faction integer; `faction` is the querying
-- faction. Split the mask: low bits (& FF_ALL) are the frametype constraint, high bits the faction
-- constraint. A 0 mask (PrepareFilterEntity found an impossible combination) matches nothing.
function Entity:MatchFilter(mask, faction)
	if mask == 0 then return false end -- PrepareFilterEntity returns 0 for an impossible combo
	local ft = mask & FF_ALL
	local fc = mask & ~FF_ALL
	-- A 0 sub-part means "unconstrained on that axis": PrepareFilterEntity always leaves both parts
	-- populated (frametype starts FF_ALL, faction starts all-bits), but some internal FilterEntity
	-- calls pass a hand-written faction-only constant (e.g. FF_ENEMYFACTION) with no frametype bits.
	if ft ~= 0 and (frametype_bits(self) & ft) == 0 then return false end
	if fc ~= 0 and (relative_faction_bit(self.faction, faction) & fc) == 0 then return false end
	return true
end

--------------------------------------------------------------------------------------------------
-- Movement (Phase 3). Model, in one place:
--
-- * Positions are integer tiles; a unit accumulates fractional progress internally and teleports
--   one 8-connected tile at a time (user-confirmed; reference_tile_occupancy_model).
-- * Per-tick progress = def.movement_speed / TICKS_PER_SECOND tiles (movement_speed is
--   tiles-per-SECOND at face value -- reference_movement_speed_model); a diagonal step costs
--   sqrt(2) tiles of progress (measured in-game 2026-07-18: diagonal steps averaged 2.5*sqrt(2)
--   ticks against 2.5 orthogonal at speed 2).
-- * Step direction: diagonal while both axes differ, then straight -- read directly off the
--   golden movement-circuit log (every leg is N diagonal steps then M straight, e.g.
--   (-9,60)->(-4,55) five diagonals then (-4,54)..(-4,51) four straight; never interleaved).
--   No pathfinding: the mock walks the unobstructed octile line and BLOCKS if a step is blocked,
--   it never routes around (a real-engine divergence, flagged; fine for flat test worlds).
-- * ARRIVAL (MEASURED in-game 2026-07-20 by the arrival probe -- tests/test_arrival_probe.py
--   diffs this model against the real run, tests/data/arrival_probe_ingame.log): arrived iff
--   `Map.GetDistance(unit, target) <= tolerance`, tolerance = the `range` argument, floored at 1
--   for an entity target (its tile can't be entered by a ground unit). Confirmed exactly: the
--   coordinate cases reproduce tile-for-tile; the entity cases reproduce the gate (readouts
--   1/2/5 at range 0/2/5, the range-0 floor to 1), diverging only in the stop tile because the
--   in-game target was a 3x3 building (closest-tile approach the mock's point pathing doesn't
--   replicate -- the separate pathfinding divergence noted above, not an arrival difference).
-- * Flying units ignore landscape blocking, ground occupancy, and (later) terrain modifiers.
-- * Effective speed = base movement_speed only, for now: speed modules, pavement/blight terrain
--   modifiers, and the unpowered penalty are explicitly later refinements (docs/mock_world_spec.md's
--   tick-step section) -- the first-version world is flat, unpaved, unblighted, powered.
--------------------------------------------------------------------------------------------------

local SQRT2 = math.sqrt(2)

local function target_is_entity(target)
	local mt = getmetatable(target)
	return mt ~= nil and rawget(mt, "__is_entity") ~= nil
end

local function arrival_tolerance(target, range)
	local tol = math.max(range or 0, 0)
	if target_is_entity(target) and tol < 1 then tol = 1 end
	return tol
end

local function arrived(ent, goal)
	return Map.GetDistance(ent, goal.target) <= arrival_tolerance(goal.target, goal.range)
end

-- The non-flying occupant of tile (x, y), excluding `exclude` -- the ground layer's one-per-tile
-- invariant (flyers stack and never occupy; user-confirmed). Shared by movement blocking and
-- Map.CountTiles' entity-blocked count.
local function ground_occupant(x, y, exclude)
	for _, o in pairs(World.registry) do
		if o ~= exclude and o.exists and not o.flying and o.location.x == x and o.location.y == y then
			return o
		end
	end
	return nil
end

-- Whether `e` (a ground unit) can step onto tile (x, y): landscape must be open and the ground
-- layer unoccupied. Flyers skip this check entirely (they stack and ignore landscape).
local function ground_step_blocked(e, x, y)
	if World:tile(x, y).landscape_blocked then return true end
	return ground_occupant(x, y, e) ~= nil
end

-- The next 8-connected step toward (tx, ty): diagonal-first (see the model note above).
local function next_step(e, tx, ty)
	local dx = tx - e.location.x
	local dy = ty - e.location.y
	local sx = dx > 0 and 1 or dx < 0 and -1 or 0
	local sy = dy > 0 and 1 or dy < 0 and -1 or 0
	local cost = (sx ~= 0 and sy ~= 0) and SQRT2 or 1
	return sx, sy, cost
end

-- Record an ASYNC move goal on the entity (domove's c=2 path and remote moves: `ent:MoveTo`).
-- No component waits on it; completion just clears it.
function Entity:MoveTo(target, range)
	local goal = { target = target, range = range or 0 }
	if arrived(self, goal) then
		self.move_goal, self.is_moving = nil, false
		return
	end
	self.move_goal = goal
	self.move_progress = self.move_progress or 0
	self.is_moving = true
end

-- Advance one entity by one tick of movement toward `goal`. Returns "moving", "arrived" or
-- "blocked". Progress accumulates every tick INCLUDING the one the goal was issued on (the
-- phase that reproduces the golden circuit log's step timing).
local function step_entity_toward(e, goal)
	local speed = (e.def.movement_speed or 0)
	if speed <= 0 then return "blocked" end
	e.move_progress = (e.move_progress or 0) + speed / TICKS_PER_SECOND
	while true do
		if arrived(e, goal) then return "arrived" end
		local tx, ty = xy_of(goal.target)
		if tx == nil then return "blocked" end -- target gone (dangling entity ref)
		local sx, sy, cost = next_step(e, tx, ty)
		if e.move_progress < cost then return "moving" end
		local nx, ny = e.location.x + sx, e.location.y + sy
		if not e.flying and ground_step_blocked(e, nx, ny) then
			e.state_path_blocked = true
			return "blocked"
		end
		e.move_progress = e.move_progress - cost
		e.location.x, e.location.y = nx, ny
		e.state_path_blocked = false
	end
end

--------------------------------------------------------------------------------------------------
-- Components (a behavior's `comp`). Minimal here; behavior attachment is Phase 3.
--------------------------------------------------------------------------------------------------

local Component = {}
-- `has_extra_data` mirrors the engine's native computed property (`comp.has_extra_data and
-- comp.extra_data` is the real dispatcher's idiom) -- live via __index, same as engine_stub's
-- bare comp.
Component.__index = function(t, k)
	if k == "has_extra_data" then return rawget(t, "extra_data") ~= nil end
	return Component[k]
end

function Component:GetRegister(n) return self.registers[n] or NewValue(0) end
function Component:SetRegister(n, v) if v == nil then self.registers[n] = nil else self.registers[n] = Tool.NewRegisterObject(v) end end
function Component:GetRegisterNum(n) return (self.registers[n] or NewValue(0)).num end
function Component:GetRegisterCoord(n) local r = self.registers[n] return r and r.coord end
function Component:GetRegisterId(n) local r = self.registers[n] return r and r.id end
function Component:GetRegisterEntity(n) local r = self.registers[n] return r and r.entity end
function Component:SetStateSleep(t) self.sleep = t or 1 end
-- Activation lifecycle (the engine's own component activity flag, flipped by SetBehavior/exit).
function Component:Activate() self.is_active = true end
function Component:Shutdown() self.is_active = false end

-- The SYNC move request (domove's default path and the scout/dropoff funcs). Two real call
-- shapes: `(target, range)` with an entity/coord target, and a bare `(x, y)` pair. Returns
-- `(need_move, repeat_blocked)`; while need_move is true the calling component is in the waiting
-- state (`waiting_move`) and MockWorld.step's movement phase wakes it on arrival or blockage --
-- the mock half of the engine's "activated again once that finishes" contract.
function Component:RequestStateMove(a, b)
	local target, range
	if type(a) == "number" then
		target, range = { x = a, y = math.floor(b or 0) }, 0
	else
		target, range = a, b or 0
	end
	local e = self.owner
	local goal = { target = target, range = range, wake_comp = self }
	if arrived(e, goal) then
		e.move_goal, e.is_moving, self.waiting_move = nil, false, false
		return false, false
	end
	-- No pathfinding: an immediately-blocked next step reports repeat_blocked (domove then takes
	-- its Path Blocked pin), rather than routing around. Flagged in the movement model note.
	local tx, ty = xy_of(target)
	if tx ~= nil and not e.flying then
		local sx, sy = next_step(e, tx, ty)
		if ground_step_blocked(e, e.location.x + sx, e.location.y + sy) then
			e.state_path_blocked = true
			return true, true
		end
	end
	e.move_goal = goal
	e.move_progress = e.move_progress or 0
	e.is_moving = true
	self.waiting_move = true
	return true, false
end

-- One world tick of movement for every entity (MockWorld.step's movement phase, run after the
-- behavior phase). Explicit move goals (RequestStateMove/MoveTo) take priority; otherwise a
-- non-empty GOTO frame register drives a persistent native move-to (reference_goto_register_
-- semantics: distinct from domove, re-evaluated every tick, the register is never cleared --
-- which also makes an entity target self-tracking). Which of the two the real engine prefers
-- when both are set is unmeasured; explicit-first is the mock's modeling choice.
function World.StepMovement()
	for _, e in pairs(World.registry) do
		if e.exists then
			local goal, transient = e.move_goal, nil
			if goal == nil then
				local reg = e.registers[FRAMEREG_GOTO]
				local target = reg and (reg.entity or reg.coord)
				if target ~= nil then
					transient = { target = target, range = math.max(reg.num or 0, 0) }
					goal = transient
					e.move_progress = e.move_progress or 0
				end
			end
			if goal ~= nil then
				local result = step_entity_toward(e, goal)
				if result == "moving" then
					e.is_moving = true
				else
					e.is_moving = false
					if transient == nil then e.move_goal = nil end -- @goto re-derives next tick
					local comp = goal.wake_comp
					if comp then comp.waiting_move = false end -- reactivates next tick
				end
			end
		end
	end
end

function Component:FindComponent(id) return self.owner:FindComponent(id) end

--------------------------------------------------------------------------------------------------
-- Spawning
--------------------------------------------------------------------------------------------------

-- Build an entity table whose `.def` IS the real data.frames[def_id] (or any data.all def), so
-- movement_speed / visibility_range / socket layout / type all come from real data. `faction` is a
-- faction table (World.MakeFaction). `overrides` may set location and any state flag / field.
function World.Spawn(def_id, faction, x, y, overrides)
	local def = data.frames[def_id] or data.all[def_id]
	assert(def, "World.Spawn: no such frame/def id: " .. tostring(def_id))
	overrides = overrides or {}

	local e = setmetatable({
		eid = World.next_id, -- stable registry id (lupa makes a fresh proxy per access, so Python
		                     -- identity `is` fails on the same entity; compare `.eid`)
		id = def_id,
		def = def,
		visual_def = {}, -- explorable_race lives here in the real data; empty is the common case
		faction = faction,
		location = { x = x or 0, y = y or 0 },
		registers = {},
		components = {},
		inventory = {},
		exists = true,
		is_construction = false,
		-- The PHYSICS flying notion (shares tiles, ignores landscape/terrain) -- an explicit
		-- per-entity boolean derived from the frame, per the spec's tile-model note: size
		-- "Drone", the drone/flyer/satellite slot_types, and the rare Flyer frame flag coincide
		-- for every frame that matters. Distinct from FilterEntity's v_is_flying (cost_modifier
		-- == 0), which stays the real predicate. Overridable per spawn like any field.
		flying = def.size == "Drone"
			or def.slot_type == "drone"
			or def.slot_type == "flyer"
			or def.slot_type == "satellite",
		move_goal = nil,
		move_progress = 0,
		health = def.health_points or 100,
		max_health = def.health_points or 100,
		visibility_range = def.visibility_range or 0,
		-- FilterEntity state flags; all default falsey. A test flips whichever it exercises.
		is_damaged = false,
		state_custom_1 = false, -- infected
		state_unpowered = false,
		powered_down = false,
		is_moving = false,
		state_path_blocked = false,
		state_idle = false,
		state_emergency = false,
		state_broken = false,
		has_extra_data = false,
		lootable = false,
	}, Entity)

	for k, v in pairs(overrides) do e[k] = v end

	World.registry[World.next_id] = e
	World.next_id = World.next_id + 1
	return e
end

-- Attach a component (by real data.components id) to an entity, giving it its own register bank and
-- owner/faction back-references. Returns the component.
function World.AddComponent(entity, comp_id, register_count)
	local def = data.components[comp_id] or data.all[comp_id]
	assert(def, "World.AddComponent: no such component id: " .. tostring(comp_id))
	local c = setmetatable({
		id = comp_id,
		def = def,
		owner = entity,
		faction = entity.faction,
		registers = {},
		register_count = 0,
		is_active = false,
		sleep = 0,
	}, Component)
	entity.components[#entity.components + 1] = c
	return c
end

--------------------------------------------------------------------------------------------------
-- Map spatial primitives over the registry
--------------------------------------------------------------------------------------------------

-- Closest entity to `owner` within `range` for which `pred(e)` is truthy, applying the broad-phase
-- `filter` mask (MatchFilter) first exactly as the real callers rely on (get_closest_entity's pred
-- only calls FilterEntity, trusting FindClosestEntity to have masked by frametype/faction already).
-- The owner is excluded -- radar returns OTHER units, never self. The range gate is floored
-- Euclidean and "closest" ordering among gate-passers is Euclidean -- both settled in-game, see
-- the distance-model note above.
function Map.FindClosestEntity(owner, range, pred, filter)
	local best, best_d = nil, nil
	for _, e in pairs(World.registry) do
		if e ~= owner and e.exists then
			local d = euclid(owner, e)
			if gate_distance(owner, e) <= range then
				if (not filter or e:MatchFilter(filter, owner.faction)) and (not pred or pred(e)) then
					if best_d == nil or d < best_d then best, best_d = e, d end
				end
			end
		end
	end
	return best
end

-- All entities within `range` of `center` (entity or coord), optionally masked by a filter and
-- faction. Backs get_entities_in_range / the for_entities_in_range block driver and FilterEntity's
-- own internal Map.GetEntitiesInRange calls. Floored-Euclidean gate, same as FindClosestEntity.
function Map.GetEntitiesInRange(center, range, filter, faction)
	local out = {}
	for _, e in pairs(World.registry) do
		if e.exists and gate_distance(center, e) <= range then
			if not filter or e:MatchFilter(filter, faction or (center.faction)) then
				out[#out + 1] = e
			end
		end
	end
	return out
end

-- Ground occupancy: at most one non-flying entity per tile (first-version invariant). Returns the
-- first entity found on the tile.
function Map.GetEntityAt(x, y)
	for _, e in pairs(World.registry) do
		if e.exists and e.location.x == x and e.location.y == y then return e end
	end
	return nil
end

-- Deferred callbacks (EntityAction/Undock/Destroy etc. schedule onto here); MockWorld.step drains
-- them once behaviors have advanced (Phase 3). Exposed for the Python facade to flush.
function Map.Defer(fn)
	World.deferred[#World.deferred + 1] = fn
end

function World.DrainDeferred()
	local q = World.deferred
	World.deferred = {}
	for _, fn in ipairs(q) do fn() end
	return #q
end

-- blocked_landscape, blocked_entity, area_total, area_passable -- the 4 returns is_passable /
-- construction logic read. First arg may be a coord pair or an entity (then the radius follows).
function Map.CountTiles(a, b, layer, single)
	local x, y = xy_of(a)
	if x == nil then x, y = a, b end -- called as (x, y, ...)
	local t = World:tile(x, y)
	local bl = t.landscape_blocked and 1 or 0
	-- entity blocking is the GROUND layer only (flyers stack, never block -- same rule movement
	-- uses via ground_occupant)
	local be = ground_occupant(x, y, nil) and 1 or 0
	local passable = (bl == 0 and be == 0) and 1 or 0
	return bl, be, 1, passable
end
