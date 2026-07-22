-- Minimal stand-ins for the engine-side primitives that data/instructions.lua needs but doesn't
-- define itself.
--
-- IMPORTANT, discovered the hard way: `Get`/`Set`/`GetNum` are NOT external engine globals --
-- instructions.lua itself aliases them to its own `InstGet`/`InstSet`/`InstGetNum` (search for
-- "Local references for shorter names" near the top of the file), which resolve every argument
-- through `GetStack` into a uniform integer addressing scheme: `state.mem[j]` for locals/literal
-- constants, `comp.owner:GetRegister(n)` for the 4 frame registers, `CallRadio(...)` for
-- shared/faction registers (not stubbed here yet). So `InstGet`/`InstSet`/`GetStack` are reused
-- UNMODIFIED from the real file -- only the things genuinely missing from this extract are
-- stubbed: the `Value` type's arithmetic operators (confirmed engine-side, not in this extract),
-- and a fake `comp`/`comp.owner`/`Tool` sufficient to drive that addressing scheme.
--
-- Compiled-arg convention this harness uses (ours -- not yet the real GetFactionBehaviorAsm
-- output, see project notes): every instruction arg is a plain Lua integer. Positive integers
-- are `state.mem[]` slots (used for BOTH local variables and literal constants -- the harness's
-- Python-side `Memory` class allocates these, mirroring what the real compiler would do).
-- Negative integers -1..-4 select a frame register (Goto/Store/Visual/Signal -- see the
-- FRAMEREG_* block below for the corrected mapping) via `comp.owner:GetRegister`.

-- Confirmed 2026-07-11 via a real in-game test (a behavior with plain `set_reg(Value=N,
-- Target=Result)` calls for N = 0, 1, -2147483647, -2147483648, read back from the Result
-- parameter's UI display) cross-checked against `ui/RegisterSelection.lua`'s own display/parse
-- code three independent ways: the display formatter itself (`self.input.text = (not reg_num
-- and "") or (reg_num == REG_INFINITE and "\xe2\x88\x9e") or (reg_num == REG_NOT and "\xe2\x89\xa0") or tostring(reg_num)`,
-- confirming empty/0/1 as plain text and only these two exact values get the special symbols),
-- the text-input clamp boundary (`math.max(math.min(tonumber(...), 2147483647), REG_NOT+1)` --
-- ordinary typed numbers clamp to [REG_NOT+1, 2147483647], i.e. these two sentinels sit just
-- outside the normal typeable range), and the button tooltips ("Set infinite"/"Set not equal").
-- These are INT32-scale values, not Lua's own 64-bit math.maxinteger/mininteger -- a previous
-- version of this file guessed the latter (right shape, wrong magnitude and, for REG_NOT, wrong
-- sign relative to REG_INFINITE: REG_NOT is the smaller-magnitude sentinel, not the larger).
-- This also retroactively confirms every real behavior reviewed this project that uses the
-- literal `-2147483648` for a "Range"/"Amount" argument (Mining Leader's `Check Emergency`,
-- Fendersons Transport's `dopickup`) was genuinely writing REG_INFINITE, not just "a very large
-- negative number that happens to trip some fallback."
REG_INFINITE = -2147483648
REG_NOT = -2147483647

Value = {}
-- `.is_empty` is a NATIVE computed property of the real register object, consulted by ~15
-- instruction funcs (`is_empty`'s own branch, `value_type`'s early return, GetSeenEntityOrSelf's
-- guard, combine_coordinate, ...). The stub never modeled it until 2026-07-19, so every such read
-- silently got nil (= "has a value") -- found when the `is_empty` instruction took its Has Value
-- pin on a genuinely empty register. Semantics mirrored from the settled in-game behavior
-- (docs/behavior_format.md "dangling entity references"): empty <=> num == 0 AND no data part, where a
-- dangling entity reference (target destroyed, `exists == false`) counts as blank -- on the
-- current build an entity-only dangling ref IS empty (the next game release inverts exactly this;
-- revisit then). Implemented via __index so it stays live as the boxed value is Init()-ed in place.
Value.__index = function(t, k)
	if k == "is_empty" then
		local e = rawget(t, "entity")
		if e ~= nil and e.exists == false then e = nil end
		return (rawget(t, "num") or 0) == 0
			and rawget(t, "coord") == nil
			and rawget(t, "id") == nil
			and e == nil
			and rawget(t, "item") == nil
	end
	return rawget(Value, k)
end

local function is_value(v)
	return type(v) == "table" and getmetatable(v) == Value
end

-- Field layout confirmed from direct field access in InstGet{Coord,Id,Entity} (`state.mem[j].coord`
-- /`.id`/`.entity`) and InstSet callers constructing raw tables directly (e.g. combine_coordinate's
-- `Set(comp, state, out_coord, { coord = { new_x, new_y } })`, an ARRAY-style coord -- as opposed
-- to the hash-style `{x=,y=}` this doc's own source-literal convention uses). `coord` is normalized
-- to hash style `{x=,y=}` on the way in so downstream `.coord.x`/`.coord.y` access (as
-- separate_coordinate does) works regardless of which shape a caller constructed.
local function normalize_coord(c)
	if c == nil then return nil end
	if c.x ~= nil then return c end
	return { x = c[1], y = c[2] }
end

function NewValue(num, coord, id, entity, item)
	return setmetatable({ num = num or 0, coord = normalize_coord(coord), id = id, entity = entity, item = item }, Value)
end

-- Always builds a FRESH Value, even when `v` is already one -- confirmed against real callers'
-- own comments (set_number's func: "Tool.NewRegisterObject(Get(comp, state, val)) -- copy to
-- avoid changing from"), i.e. `Tool.NewRegisterObject`/`coerce` are relied on elsewhere in
-- data/instructions.lua specifically to decouple a stored copy from its source register. A first
-- version of this stub short-circuited `is_value(v) -> return v` (same reference), which is a
-- real bug: e.g. `memory_insert` pushing a variable's current value onto an array, followed by
-- that same variable's slot later being overwritten in place via `Value:Init()` (any subsequent
-- `set_reg`/`add`/`memory_remove` into it), would silently corrupt the already-pushed array
-- entry too, since it was the same Lua table, not an independent copy. Caught 2026-07-10 building
-- a Fibonacci-via-memory-array test fixture that reuses a pushed value: the sequence came out as
-- powers of 2 instead of Fibonacci numbers, because the "old" value got overwritten in place
-- before the "new" value's own push read it back.
local function coerce(v)
	if is_value(v) then return NewValue(v.num, v.coord, v.id, v.entity, v.item) end
	if type(v) == "number" then return NewValue(v) end
	if type(v) == "table" then
		-- a bare mock-world entity (world.lua marks its Entity metatable): the real engine's
		-- entities are userdata and the native register conversion stores one as `.entity`;
		-- without this, the generic branch below would misread the entity's own `.id` (its frame
		-- id) as an id-literal value and drop the entity itself
		local mt = getmetatable(v)
		if mt ~= nil and rawget(mt, "__is_entity") then
			return NewValue(0, nil, nil, v)
		end
		-- a raw table (not a Value) built directly by some instruction func, e.g.
		-- combine_coordinate's `{ coord = { new_x, new_y } }` -- same fields, not yet a Value
		return NewValue(v.num, v.coord, v.id, v.entity, v.item)
	end
	error("cannot coerce to Value: " .. tostring(v))
end

-- The real register object's `:Clear()` -- called by the dispatcher's restart path
-- (`c_behavior_on_end`'s `mem[i]:Clear()` over `asm.lvs`, the local-variable slots of a
-- non-`keepvars` behavior) to blank locals when a behavior falls back to Program Start.
function Value:Clear()
	self.num, self.coord, self.id, self.entity, self.item = 0, nil, nil, nil, nil
	return self
end

-- `state.mem[j]` slots are mutable "register object" boxes: InstSet calls `:Init(val)` on the
-- EXISTING slot object to overwrite its contents in place (rather than replacing the table
-- entry), so other references to the same slot see the update without needing to re-fetch it.
function Value:Init(val)
	-- nil clears to a genuinely empty value -- a real, reachable path: e.g. read_signal's func
	-- does `Set(comp, state, res, ent and ent:GetRegister(FRAMEREG_SIGNAL) or nil)`, so the native
	-- Init must accept nil. (Found 2026-07-19 via a read_signal-with-no-Unit test erroring here.)
	if val == nil then
		self.num, self.coord, self.id, self.entity, self.item = 0, nil, nil, nil, nil
		return self
	end
	val = coerce(val)
	self.num, self.coord, self.id, self.entity, self.item = val.num, val.coord, val.id, val.entity, val.item
	return self
end

-- Confirmed rules (add/sub AND mul/div, each empirically tested in-game -- see
-- docs/behavior_format.md's "Composite values and the `num` field"): coordinate+coordinate combines
-- both parts; coordinate+bare-number broadcasts the number onto both axes and preserves the
-- coordinate's own `num`; entity/item+bare-number adds `num` normally, the reference passes
-- through. modulo's composite behavior is NOT separately confirmed -- this stub applies the
-- same broadcast logic to it as the best available guess; treat that part as unverified.
local function combine(a, b, numOp, coordOp)
	a, b = coerce(a), coerce(b)
	local a_data, b_data = a.coord or a.id or a.entity or a.item, b.coord or b.id or b.entity or b.item
	if a.coord and b.coord then
		return NewValue(numOp(a.num, b.num), { x = coordOp(a.coord.x, b.coord.x), y = coordOp(a.coord.y, b.coord.y) })
	elseif a.coord and not b_data then
		return NewValue(a.num, { x = coordOp(a.coord.x, b.num), y = coordOp(a.coord.y, b.num) })
	elseif b.coord and not a_data then
		return NewValue(b.num, { x = coordOp(a.num, b.coord.x), y = coordOp(a.num, b.coord.y) })
	elseif a.id or a.entity or a.item then
		return NewValue(numOp(a.num, b.num), nil, a.id, a.entity, a.item)
	elseif b.id or b.entity or b.item then
		return NewValue(numOp(a.num, b.num), nil, b.id, b.entity, b.item)
	else
		return NewValue(numOp(a.num, b.num))
	end
end

-- Needed for `jump`'s func, which compares two freshly-`Get()`'d Value objects with plain `==`
-- (`label == Get(comp, state, asm[i][3])`) -- without this, two distinct Value tables holding the
-- same literal number would never compare equal (Lua's default table `==` is identity, not
-- value, comparison). Field-wise equality (unconfirmed against real engine semantics, but the
-- only sensible reading of "same label id").
Value.__eq = function(a, b)
	if a.num ~= b.num then return false end
	if (a.coord == nil) ~= (b.coord == nil) then return false end
	if a.coord and (a.coord.x ~= b.coord.x or a.coord.y ~= b.coord.y) then return false end
	return a.id == b.id and a.entity == b.entity and a.item == b.item
end

Value.__add = function(a, b) return combine(a, b, function(x, y) return x + y end, function(x, y) return x + y end) end
Value.__sub = function(a, b) return combine(a, b, function(x, y) return x - y end, function(x, y) return x - y end) end
Value.__mul = function(a, b) return combine(a, b, function(x, y) return x * y end, function(x, y) return x * y end) end
Value.__idiv = function(a, b) return combine(a, b, function(x, y) return x // y end, function(x, y) return x // y end) end
Value.__mod = function(a, b) return combine(a, b, function(x, y) return x % y end, function(x, y) return x % y end) end

Tool = {}
function Tool.NewRegisterObject(v)
	if v == nil then return NewValue(0) end
	return coerce(v)
end

-- Engine-native content hash, used by the library-import path (`LibraryHashItem`/
-- `LibraryPerformImport`, data/library.lua) purely as a table key for same-content dedup -- the
-- real hash's numeric value is never compared against anything external, so any deterministic,
-- content-equal => key-equal mapping works. This stub returns a canonical string serialization
-- (sorted keys, type-tagged scalars) AS the hash; strings are perfectly good table keys, and the
-- serialization is trivially collision-free. Behavior/blueprint tables are pure data (no
-- functions/userdata/cycles), which is all this ever hashes.
local function hash_serialize(v, out)
	if type(v) ~= "table" then
		out[#out + 1] = type(v):sub(1, 1) .. tostring(v)
		return
	end
	out[#out + 1] = "{"
	local keys = {}
	for k in pairs(v) do keys[#keys + 1] = k end
	table.sort(keys, function(a, b)
		local ta, tb = type(a), type(b)
		if ta ~= tb then return ta < tb end
		if ta == "number" then return a < b end
		return tostring(a) < tostring(b)
	end)
	for _, k in ipairs(keys) do
		hash_serialize(k, out)
		out[#out + 1] = "="
		hash_serialize(v[k], out)
		out[#out + 1] = ";"
	end
	out[#out + 1] = "}"
end

function Tool.Hash(v)
	local out = {}
	hash_serialize(v, out)
	return table.concat(out)
end

-- Engine-native deep copy. Used by instruction funcs to snapshot values (for_signal_match's
-- numeric filter modes) and -- load-bearing since the real-dispatcher work -- by `SetBehavior`
-- (data/library.lua) as `Tool.Copy(asm.mem)`: the compiled asm's initial memory image is COPIED
-- into each running state, so this must be a genuinely deep copy -- a shallow one (this stub's
-- first version) would hand every run the cached compile's own Value boxes, and the first
-- in-place `:Init()` would corrupt the shared compile for every later run. Entities are the one
-- reference type that must NOT be copied (identity is their meaning; the real engine's entities
-- are userdata and survive its native deep copy as references) -- recognized via the mock
-- Entity metatable marker, same as `coerce` below. No cycle handling: nothing this harness
-- copies is cyclic (asm.mem is a flat Value array; a Value's fields bottom out immediately).
function Tool.Copy(v)
	if type(v) ~= "table" then return v end
	local mt = getmetatable(v)
	if mt ~= nil and rawget(mt, "__is_entity") then return v end
	local c = {}
	for k, val in pairs(v) do c[k] = Tool.Copy(val) end
	return setmetatable(c, mt)
end

-- Frame registers (Goto=1, Store=2, Visual=3, Signal=4 -- see the FRAMEREG_* block below; InstGet
-- resolves wire address j, `-99 <= j <= 0`, via `comp.owner:GetRegister(-j)`) backed by a plain
-- array on a fake `owner` entity.
local Owner = {}
Owner.__index = Owner
function Owner:GetRegister(n) return self.registers[n] or NewValue(0) end
function Owner:SetRegister(n, v) self.registers[n] = coerce(v) end
function Owner:GetRegisterNum(n) return (self.registers[n] or NewValue(0)).num end
function Owner:GetRegisterCoord(n) local r = self.registers[n] return r and r.coord end
function Owner:GetRegisterId(n) local r = self.registers[n] return r and r.id end
function Owner:GetRegisterEntity(n) local r = self.registers[n] return r and r.entity end
-- `UpdateEntityBehaviorState` (data/library.lua, reached via the real `exit` func) scans the
-- owner's components by base id + occurrence index; the bare fake owner has none.
function Owner:FindComponent(id, by_base, index) return nil end

-- The bare stand-in faction a `NewComp` component belongs to. Real behavior execution reaches the
-- faction for two things outside the mock world: `comp.faction.extra_data.library` (the saved-
-- behavior store `GetFactionBehaviorAsm`/`SetBehavior` compile from -- per-comp here, so parallel
-- bare interpreters never share ids) and `faction:RunUI(...)` (`InstError`'s user notification --
-- a no-op here; the error still stops the behavior via the real `exit` func).
local StubFaction = {}
StubFaction.__index = StubFaction
function StubFaction:RunUI(...) end

-- `wait`'s func (data/instructions.lua) calls `comp:SetStateSleep(t)` and returns `true` when it
-- actually slept (t > 0) -- the real per-tick dispatcher reads this to know to stop running this
-- component for the rest of the current tick. `t` defaults to 1 when omitted (a handful of real
-- callers in data/components.lua use `comp:SetStateSleep()` with no argument at all).
--
-- `has_extra_data` is a NATIVE computed property of real components (`comp.has_extra_data and
-- comp.extra_data` is the dispatcher's own idiom), modeled with an __index function so it stays
-- live as `SetBehavior` assigns `comp.extra_data`.
local CompMeta = {}
CompMeta.__index = function(t, k)
	if k == "has_extra_data" then return rawget(t, "extra_data") ~= nil end
	return CompMeta[k]
end
function CompMeta:SetStateSleep(t)
	self.sleep = t or 1
end
-- Component registers (a behavior's PARAMETERS live here -- `state.stk = #parameters` makes
-- GetStack route addresses 1..#parameters to `comp:GetRegister(j)`, see data/library.lua's
-- SetBehavior). Same bank shape as the Owner's frame registers above.
function CompMeta:GetRegister(n) return self.registers[n] or NewValue(0) end
function CompMeta:SetRegister(n, v) if v == nil then self.registers[n] = nil else self.registers[n] = coerce(v) end end
function CompMeta:GetRegisterNum(n) return (self.registers[n] or NewValue(0)).num end
function CompMeta:GetRegisterCoord(n) local r = self.registers[n] return r and r.coord end
function CompMeta:GetRegisterId(n) local r = self.registers[n] return r and r.id end
function CompMeta:GetRegisterEntity(n) local r = self.registers[n] return r and r.entity end
-- Activation lifecycle (the engine's own component activity flag, flipped by SetBehavior/exit).
function CompMeta:Activate() self.is_active = true end
function CompMeta:Shutdown() self.is_active = false end

function NewComp()
	return setmetatable({
		owner = setmetatable({ registers = {} }, Owner),
		faction = setmetatable({ meta_type = "faction", extra_data = { library = {} } }, StubFaction),
		def = { name = "stub component" },
		registers = {},
		register_count = 0,
		is_active = false,
		sleep = 0,
	}, CompMeta)
end

-- `state.stk = 0` (a plain number, not a table) is GetStack's simplest case: a flat, top-level
-- behavior with no sub-behavior call stack. Under this, GetStack(state, i) resolves any i > 0 to
-- `(i, true)` i.e. `state.mem[i]` (locals + literal constants, uniformly), and any i <= 0 falls
-- through unchanged to InstGet/InstSet's own register-address branches.
--
-- `limit` defaults to 1 -- data.instructions.unlock/.lock's own `explain` text: "By default,
-- behaviors will run one instruction per tick" (limit=1); `unlock` raises it to 10000, `lock`
-- resets it back to 1 (see data/instructions.lua:497-514).
function NewState()
	return { counter = nil, stk = 0, mem = {}, revid = 1, limit = 1 }
end

-- `unlock`'s func checks `Map.GetSettings().block_unlocked_behaviors` (a server-side setting
-- that can disable the instruction entirely) before touching `state.limit` -- stubbed to always
-- report "not blocked" since this harness has no map/server-settings concept.
Map = {}
function Map.GetSettings()
	-- `blight_threshold` (default 0.1, per data/map_settings.lua's `or 0.1` fallback) is read at
	-- load time by a few blight-terraformer component defs in components.lua
	-- (`Map.GetSettings().blight_threshold - 0.3`), so it must be a real number for the registry
	-- load to succeed; its value only feeds a cosmetic `terraforming_target` field.
	return { block_unlocked_behaviors = false, blight_threshold = 0.1 }
end

-- The save object -- the library-import path stores its global behavior-id counter on it
-- (`Map.GetSave().library_id_count`, data/library.lua's LibraryPerformImport). One table per
-- engine: ids stay unique across every faction/world sharing the runtime, exactly like one
-- running save.
Map.save = {}
function Map.GetSave()
	return Map.save
end

-- Deferred-callback queue (`Map.Defer` schedules engine work for end-of-tick -- SetBehavior's
-- event-listener spawn rides it). This bare queue is never drained automatically; the mock world
-- (world.lua, loaded on top) REPLACES Defer with its own World-owned queue that MockWorld.step
-- drains every tick. Kept here so the bare (no-world) runtime can at least accept the calls.
Map.deferred = {}
function Map.Defer(fn)
	Map.deferred[#Map.deferred + 1] = fn
end

-- The shared global table every data/*.lua file populates (data.instructions, data.components,
-- data.frames, data.items, data.values, data.all, ...). The engine pre-creates every one of
-- these registry tables; model that with an auto-vivifying `__index` so any `data.X.y = ...` at
-- load time works and any `data.X` read returns a (possibly empty) table -- exactly as the real
-- engine presents them. `LupaEngine` loads `instructions.lua` alone by default, or the fuller
-- Data-registry subset (utilities/values/items/components/frames) when asked, which is what
-- actually populates `data.frames`/`data.components`/`data.all` for the mock world.
data = setmetatable({}, {
	__index = function(t, k)
		local sub = {}
		rawset(t, k, sub)
		return sub
	end,
})

-- Only top-level (non-func-body) call in data/instructions.lua that needs a real stand-in when
-- loading instructions.lua ALONE. When the Data-registry subset is loaded first, components.lua
-- redefines `Comp` and `Comp:RegisterComponent` for real (registering into data.components), and
-- this fallback is harmlessly overwritten.
Comp = {}
function Comp:RegisterComponent(...) end

-- Engine-native constants the Data-registry files reference at load time (all genuinely C++-side,
-- not defined anywhere in this Lua extract).
--
-- FF_* are the frametype/faction bit flags PrepareFilterEntity (data/utilities.lua) combines into
-- a mask, later tested by the mock's `MatchFilter`. Their exact engine bit assignment is NOT
-- reverse-engineered here and does not need to be: FilterEntity keys entity predicates off its own
-- `FilterStringToNum` table, not these bits, so the only consumers of the FF_ masks are
-- PrepareFilterEntity (producer) and the mock MatchFilter (consumer) -- they just have to agree.
-- This layout is chosen self-consistent with the two constraints the real Lua imposes: (1)
-- `FF_ALL` must contain every frametype bit, since PrepareFilterEntity clears bits arithmetically
-- (`FF_ALL - FF_FOUNDATION`, `FF_ALL - (FF_WALL|FF_GATE)`); (2) every faction flag must be
-- numerically `> FF_ALL`, since PrepareFilterEntity uses `prepf > FF_ALL` to decide a mask is a
-- faction (not frametype) filter. Frametype bits sit below FF_ALL, faction bits above it.
FF_OPERATING    = 1
FF_FOUNDATION   = 2
FF_CONSTRUCTION = 4
FF_DROPPEDITEM  = 8
FF_RESOURCE     = 16
FF_WALL         = 32
FF_GATE         = 64
FF_ALL          = 127 -- OR of every frametype bit above
FF_OWNFACTION     = 128
FF_ENEMYFACTION   = 256
FF_NEUTRALFACTION = 512
FF_ALLYFACTION    = 1024
FF_WORLDFACTION   = 2048

-- Frame register indices. InstGet resolves a wire address -j to `comp.owner:GetRegister(j)`, and
-- the TRUE wire mapping is -1 Goto, -2 Store, -3 Visual, -4 Signal (corrected 2026-07-19 --
-- confirmed from deployed, in-game-working behaviors: magnifier_signal's drone-invitation
-- broadcast writes @signal as wire -4, miner_drone's travel write puts @goto at wire -1; matches
-- bsf/values.py's mapping). So the native register indices these constants must equal are
-- Goto=1..Signal=4 -- the REVERSE of an earlier guess here (Signal=1..Goto=4), which had copied
-- GetRegisterOrComponentRegister's selector order: that function maps the comp-reg instructions'
-- POSITIVE selector numbers (1 Signal, 2 Visual, 3 Store, 4 Goto), a different address space from
-- the negative wire encoding (native index = 5 - selector). With these values, a wire -4 write and
-- read_signal's `ent:GetRegister(FRAMEREG_SIGNAL)` land on the same slot, as in the real engine.
FRAMEREG_GOTO   = 1
FRAMEREG_STORE  = 2
FRAMEREG_VISUAL = 3
FRAMEREG_SIGNAL = 4
FRAMEREG_COUNT  = 4

-- data/components.lua's portable-radar re-arm timing quirk uses this fixed rate (=5, confirmed
-- in-game -- see reference_portable_radar_tickspersecond_quirk memory).
TICKS_PER_SECOND = 5

-- Action-dispatcher namespaces. The Data files DEFINE handlers into these at load time
-- (`function FactionAction.DoFactionCount(...)`, `function Delay.EjectDropPod(...)`, ...), so they
-- only need to exist as empty tables to be assigned into; none are invoked at load.
FactionAction = FactionAction or {}
EntityAction  = EntityAction or {}
UIMsg         = UIMsg or {}
Delay         = Delay or {}

-- NOTE: `GetFactionBehaviorAsm`/`GetCachedBehaviorAsm`/`GetFactionBehaviorAsmById` are NOT
-- stubbed here (an earlier version had a no-op/CurrentAsm scheme): the REAL data/library.lua is
-- loaded before components.lua and instructions.lua (`lua_runtime.py`), because both capture
-- load-time local aliases of these globals (components.lua's dispatcher locals, instructions.lua's
-- "Local references for shorter names" block) -- a stub loaded first would be permanently baked
-- into those closures.
