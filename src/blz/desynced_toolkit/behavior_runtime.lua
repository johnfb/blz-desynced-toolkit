-- The behavior runtime: installs and executes behaviors through the REAL game machinery.
--
-- This replaced the Python Interpreter's earlier simulated tier (its own block stack, per-
-- instruction arg translation, a CurrentAsm shim for `jump`) wholesale. Everything semantic now
-- runs as genuine, unmodified game Lua from the extract:
--
--   * compilation   -- `GetFactionBehaviorAsm` (data/library.lua): real memory interning, real
--                      branch/fallthrough encoding, real `make_asm` hidden args
--   * installation  -- `UploadBehavior` (data/library.lua): the real import step -- unpacks
--                      embedded `dependencies` (remapping `call.sub` indices and the `-1`
--                      self-reference exactly as the game does), content-hash dedup into
--                      `comp.faction.extra_data.library`, then `SetBehavior` (state init,
--                      parameter registers, activation)
--   * addressing    -- real `GetStack`/`InstGet`/`InstSet` over `state.stk` (parameters are
--                      component registers; sub-behavior params genuinely alias caller slots)
--   * block stack   -- real `InstBeginBlock` + `state.blocks`
--   * call/return   -- the real `call` func + `c_behavior_on_end`'s return-record pop
--   * dead ends     -- `c_behavior_on_end` (extracted below from the real dispatcher's own
--                      upvalue): loop advance, sub return, or Program Start restart
--
-- `BehaviorRuntime.Activate` is a port of the dispatch LOOP inside `c_behavior:on_update`
-- (data/components.lua) -- the ~30 mechanical lines that fetch an instruction row, call its
-- func, and honor `state.limit` -- with the debug/breakpoint paths dropped and ONE deliberate
-- harness deviation, marked below: a top-level fall-off restart reports "restart" instead of
-- silently running the next pass, so a test harness can terminate. The real engine restarts
-- forever without yielding -- `exit` is the only genuine halt (docs/behavior_format.md "Stopping a
-- behavior"). Everything the loop delegates to is the real function, not a port.
--
-- Requires the Data registries (components.lua defines c_behavior/c_behavior_on_end) and
-- data/library.lua loaded first -- see lua_runtime.py's load order.

BehaviorRuntime = {}

-- `c_behavior_on_end` is a file-local of components.lua, reachable only as an upvalue of the
-- real `c_behavior:on_update`. Same technique as the earlier BeginBlock patch (read-only here).
local c_behavior_on_end
do
	local on_update = data.components.c_behavior and data.components.c_behavior.on_update
	assert(on_update, "behavior_runtime.lua needs the Data registries loaded (c_behavior missing)")
	local i = 1
	while true do
		local name, val = debug.getupvalue(on_update, i)
		assert(name, "c_behavior_on_end upvalue not found on c_behavior:on_update")
		if name == "c_behavior_on_end" then
			c_behavior_on_end = val
			break
		end
		i = i + 1
	end
end

-- Install `prog` (a decoded/compiled behavior table, the `Tool.GetClipboard` 'C' shape) into
-- comp's faction library and start it on `comp`, via the real `UploadBehavior`. The behavior
-- table is deep-copied first because the real import mutates it in place (sub remapping,
-- id/rev/type stamping) -- callers hand in tables they may reuse. Returns the assigned library
-- id (state.main_id).
function BehaviorRuntime.Install(comp, prog)
	UploadBehavior(comp, Tool.Copy(prog))
	local state = comp.extra_data
	return state and state.main_id
end

-- The current activation's component, exposed so leaf funcs that print (debug_print's global
-- `print`) can be attributed. See the print-sink block below.
BehaviorRuntime.current_comp = nil

-- One activation: run instructions until the component yields. Returns why it stopped:
--   "step"    -- locked (limit 1): ran one instruction, slept 1 tick (the real dispatcher's own
--                SetStateSleep(1) idiom -- which also pins wait semantics: sleep N = resume N
--                ticks later, NOT N skipped ticks, since locked mode is one instruction per tick)
--   "waiting" -- an instruction func returned true: the comp waits on component state (an
--                explicit `wait`'s sleep, a sync move in flight, `exit`'s forever-wait). The
--                harness decides which by inspecting sleep/movement wake conditions.
--   "restart" -- HARNESS DEVIATION: the behavior fell off the end at top level (empty block and
--                return stacks) and c_behavior_on_end took its restart branch. Local variables
--                are already cleared per the real non-keepvars rule and the counter sits at
--                Program Start, but we stop instead of running the next pass.
--   "limit"   -- unlocked behavior exceeded the per-tick instruction budget: the real InstError
--                path ran (notification + exit).
--   "no_asm"  -- behavior missing/modified in the library (the real dispatcher's
--                restart_changed_code path; should not happen under the harness).
function BehaviorRuntime.Activate(comp, cause)
	local state, data_instructions = comp.extra_data, data.instructions
	local revid, step = state.revid, 1
	local asm = GetFactionBehaviorAsm(comp, revid)
	if not asm then return "no_asm" end
	BehaviorRuntime.current_comp = comp
	while true do
		local lastcounter = state.counter
		local inst = asm[lastcounter]

		while not inst do
			local blocks, returns = state.blocks, state.returns
			local at_top = (not blocks or #blocks == 0) and (not returns or #returns == 0)
			asm = c_behavior_on_end(comp, state, asm)
			-- at_top means on_end had only its restart branch left (or nil for a 0-instruction
			-- behavior -- also one completed pass); either way the harness stops here
			if at_top then BehaviorRuntime.current_comp = nil return "restart" end
			if not asm then BehaviorRuntime.current_comp = nil return "no_asm" end
			revid = state.revid -- refresh on return of call
			lastcounter = state.counter
			inst = asm[lastcounter]
		end

		state.counter = inst[2]
		state.lastcounter = lastcounter
		local res = data_instructions[inst[1]].func(comp, state, cause, table.unpack(inst, 3))

		-- a func returning true means the instruction put the component into a waiting state;
		-- the harness re-activates it when that state resolves
		if res == true then BehaviorRuntime.current_comp = nil return "waiting" end

		local step_limit = (state.limit or 1)
		if step >= step_limit then
			if step_limit == 1 then
				comp:SetStateSleep(1)
				BehaviorRuntime.current_comp = nil
				return "step"
			end
			InstError(comp, state, "Unlocked behavior exceeded instruction limit for a single step")
			BehaviorRuntime.current_comp = nil
			return "limit"
		end

		local new_revid = state.revid
		if new_revid ~= revid then -- a call/return switched behaviors; re-fetch its asm
			revid = new_revid
			asm = GetFactionBehaviorAsm(comp, revid)
			if not asm then BehaviorRuntime.current_comp = nil return "no_asm" end
		end
		step = step + 1
	end
end

-- debug_print capture: the real func's body is `print("[DEBUGPRINT]", reg)` on the GLOBAL print.
-- Route that through an optional sink so a harness (MockWorld's per-tick print stream, a pytest
-- assertion) can capture the printed register values with attribution, instead of scraping
-- stdout. With no sink installed, prints pass through to the original print unchanged.
-- The sink receives (comp, ...printed values...); comp is nil for prints outside an activation.
local real_print = print
function print(...)
	local sink = BehaviorRuntime.print_sink
	if sink then
		sink(BehaviorRuntime.current_comp, ...)
	else
		real_print(...)
	end
end
