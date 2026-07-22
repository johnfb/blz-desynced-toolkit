# Desynced Behavior Source Format

This documents the schema a behavior clipboard string (`.dcs`, and entries in
`data.behaviors`) decodes into, and that you must produce to encode a new one.
It is the missing layer between [`instructions_index.md`](instructions_index.md)
(what each instruction does and its argument order) and the codec (string ⟷
transport).

**Tooling note:** this doc was originally written against `dsc_codec.py`, a
now-retired standalone script that rendered the decoded structure as a Python
`dict`/`list` with 0-based instruction/arg indices (a JSON/Python-convenience
choice, not something intrinsic to the format). That tool's logic now lives in
`src/desynced_toolkit/dcs_wire.py`, which decodes/encodes **genuine Lua tables**
(1-based, via `lupa`) instead — matching exactly what the game's own
`Tool.GetClipboard()`/`Tool.SetClipboard()` would hand a real Lua caller, with
no shifting in either direction. Everything below describing the *actual wire
format* (register/slot addressing, branch resolution, hidden literals,
var-args) is still accurate; only examples showing 0-based Python dict keys
should be read as "shift by one to get the real 1-based Lua key" if you're
using `dcs_wire.py` directly.

Reverse-engineered from `data/library.lua`'s `GetFactionBehaviorAsm` (the
function that compiles this exact source form into runtime bytecode) and
cross-checked line-for-line against `observer.dcs`. This is the *source* form —
the same shape the in-game visual editor saves — not the compiled bytecode;
the game recompiles it from this form every time it's loaded (cached by
revision), so you never need to hand-produce bytecode.

## Top-level envelope

A behavior decodes to a dict whose keys are 0-based instruction indices (as
rendered by `dsc_codec.py`; Lua itself stores it as a 1-based array), plus a
few sibling metadata keys:

```jsonc
{
  "0": { "op": "unlock" },
  "1": { "0": { "num": 1 }, "op": "wait" },
  // ... more instructions ...
  "name": "Observer"
}
```

- `name` — display name shown in the library UI.
- `type` — `"C"` for a behavior/code entry (`"B"` is used for blueprints
  elsewhere in the library and is unrelated to instructions).
- `parameters` (optional) — present only if this behavior is meant to be
  called as a sub-behavior (via the `call`/`load_behavior` instructions).
  An array, one entry per parameter slot; a truthy entry marks that slot as
  an output parameter, falsy/`false` marks it as input. See "Parameters"
  below.
- `pnames` (optional) — parallel array of display names for `parameters`.
- `keepvars` (optional bool) — if set, local-variable memory slots persist
  across a behavior *restart* instead of resetting to their initial values
  (`data/library.lua:32`: `fill_lvs = not code.keepvars`). Corresponds to
  the in-game Program editor's "Variables" options-popup toggle
  ("Clear variables when behavior restarts" vs. "Keep variable values
  across restarts", `ui/Program.lua:781`) — genuinely output-changing, not
  cosmetic: e.g. a counter that increments on every restart vs. one that
  resets to the same value every time. Found completely unhandled by this
  project's BSF text layer (`render_text.py`/`parse_text.py` silently
  dropped it despite `decompile.py`/`compile.py` handling it correctly) via
  a user-constructed test 2026-07-09 — see `behavior_source_format.md`'s
  "Three real gaps closed during implementation" for the fix.
- `keeparrays` (optional string) — a **separate** field controlling a
  **separate** in-game toggle, "Memory Arrays" (`ui/Program.lua:783`, its
  own dropdown, independent of `keepvars`'s "Variables" one — easy to
  conflate the two, don't). Genuinely 3-state, not boolean — confirmed both
  from the real editor's own dropdown (three mutually-exclusive radio-style
  options, not a checkbox) and from two distinct engine code paths (see
  below), and user-confirmed 2026-07-09 after being asked directly whether
  it should be modeled as a bool instead. The key to the naming: "restart"
  here means the `restart`/POP *instruction* (an in-behavior control-flow
  event), while "start"/"startup" means the *component's* behavior actually
  (re)starting via `SetBehavior` — an explicit stop-then-start, triggered by
  editing/saving the behavior's code, swapping which behavior a component
  runs, or the component being freshly placed/loaded. These are two
  different events both colloquially called "restart"; the three states are:
  - absent/nil (default) = "Clear memory arrays when behavior restarts" —
    cleared on every ordinary in-behavior `restart`/POP.
  - `"startup"` = "Clear memory arrays on behavior start (including code
    changes)" — an ordinary in-behavior `restart`/POP does *not* clear
    arrays in this state (the non-obvious part, given the string's own
    name), only a genuine `SetBehavior`-level start does.
  - `"store"` = "Keep memory arrays until behavior is switched" — survives
    both an ordinary restart and a `SetBehavior`-level start, only clearing
    when the component is loaded with a genuinely different behavior/library.

  Confirmed by two distinct engine code paths, not just the popup's own
  three option strings: `c_behavior_on_end` (ordinary in-behavior restart,
  `data/components.lua:4019`) does a plain truthy check
  (`if not asm.code.keeparrays then state.arrays = nil end`) — so
  `"startup"` and `"store"` are indistinguishable *here*, both skip the
  clear; `SetBehavior` (a genuine component-level start,
  `data/library.lua:153`) checks specifically `== "store"` — so `"startup"`
  does *not* hit this branch and loses its arrays here even though it
  survived the earlier ordinary restart, while `"store"` does. Also found
  completely unhandled anywhere in this project (not just the BSF text
  layer — no code, no doc) in the same pass that found `keepvars`'s gap.
- **Both fields are consulted only for the outermost behavior actually
  assigned to the component — never for a `call`ed sub-behavior**, traced
  end-to-end through the real runtime (not inferred): `call`'s own `func`
  (~line 402) allocates a brand-new memory region via
  `Tool.NewRegisterObject(...)` on *every single invocation*, seeded from
  the callee's static compiled template, and unconditionally discards it on
  return (`c_behavior_on_end`'s `returns`-stack branch, `data/components.lua`
  ~line 4006) — `keepvars` is never read anywhere in this path, so a
  subroutine's own locals are always fresh per-call regardless of what its
  own `keepvars` says. `keepvars`/`keeparrays` are read in exactly one
  runtime location, `c_behavior_on_end`'s third branch (`data/components.lua`
  ~line 4013), reached only when *both* the block stack and the call-return
  stack are completely empty — i.e. only at the true outermost Program
  Start. `restart`, even fired from deep inside a nested `call`, always
  unwinds the *entire* call stack first (`InstUnrollReturns`,
  `data/instructions.lua` ~line 190, resets `state.revid` to the bottommost/
  outermost return record) before that branch runs, and `exit` falls back to
  `state.main_id` (the component's top-level assigned behavior id) if
  needed — so it's always the outer program's own flags in play, never a
  sub's. A sub-behavior's own `keepvars`/`keeparrays`, even if set on its own
  saved definition, are therefore dead data at runtime.
- **Memory arrays themselves are global across the entire call stack within one
  execution — genuinely shared between a behavior and every subroutine it
  calls — which is a separate fact from what `keeparrays` governs, and easy to
  conflate with the "locals are fresh per call" point directly above. Don't.**
  Confirmed 2026-07-12 by reading `memory_get`/`memory_set` (`data/instructions.lua:7292`
  onward) against `call`'s own `func` again with this specific question in mind: arrays
  live in `state.arrays`, a single flat table keyed by whatever value you pass as the
  "Index" argument (its `.id`, an entity's `.key`, or a coordinate string) — addressed by
  explicit key, not by stack position. `call`'s `func` (the same block cited above)
  extends `state.mem` and pushes a new `state.stk` window for the callee's *named*
  locals/parameters, but never references `state.arrays` anywhere — no allocation, no
  windowing, no save/restore, no clearing. So within one run, however many `call`s deep,
  `state.arrays` is just one shared table the whole time: a subroutine reading/writing an
  array under the same Index a caller (or a sibling call) uses is reading/writing the
  *exact same* array, not an isolated copy — a real, deliberate side-channel if used on
  purpose, and a real silent-collision risk if not. `keeparrays` is unrelated to this
  sharing — it only governs whether `state.arrays` gets cleared across a `restart`/
  component-(re)start event (and, per the point above, only the outermost behavior's
  setting is ever consulted for that); it says nothing about — and has no effect on —
  sharing across `call` boundaries within a single run, which is unconditional and always
  on.
- `dependencies` (optional) — present when this behavior `call`s a
  sub-behavior that is *embedded* rather than referenced by saved-library
  id (see `call`'s `sub` field under "Hidden literal fields" below). A
  flat JSON list, one entry per embedded sub-behavior, each shaped exactly
  like this same top-level envelope (its own instruction dict, `name`,
  `parameters`, `pnames`, ...). Confirmed via `hexat_test.dcs`: pasting the
  top-level behavior into the library reconstructs both it *and* the
  embedded sub-behavior as a separate library entry — that reconstruction
  is driven entirely by `dependencies`.

Every example this doc has hand-built or examined closely so far
(`observer.dcs`, the first `hexat.dcs` drafts) happens to be a small,
flat, non-parameterized behavior, where `name` plus the instruction list
is enough. Don't over-generalize from that: it's a property of those
*simple* examples, not of top-level/main-program behaviors in general —
real, more elaborate behaviors commonly do have `parameters` (to be
callable, or just to receive/expose values), and `dependencies` (to embed
sub-behaviors) frequently, as `hexat_test.dcs` (the "HexAt Test" harness,
itself parameterized, calling an embedded `HexAt` sub-behavior) already
demonstrates.

## Instruction record shape

Each instruction is a dict:

```jsonc
{ "op": "<instruction id>", "0": <arg0>, "1": <arg1>, ..., "next": <int|false>, "cmt": "<note>", "nx": <float>, "ny": <float> }
```

- **`op`** — the instruction id, exactly as it appears in `data.instructions`
  and in `instructions_index.md` (e.g. `"scan"`, `"set_reg"`, `"domove"`).
- **Numbered keys `"0"`, `"1"`, ...** — one per entry in that instruction's
  `args` list in `instructions_index.md`, **in the same order**, regardless
  of whether the entry is `in`, `out`, or `exec`. Omit a key entirely to
  leave that argument unset/unused (fine for optional/`(extra param)`
  entries; usually required for anything the description calls mandatory).
- **`next`** (optional) — where execution goes after this instruction
  finishes, *if it doesn't take one of its own `exec` branches*. See
  "Branch and fall-through resolution" below. Not present on every
  instruction — only needed when you want it to differ from the default.
- **`cmt`** (optional) — a free-text note attached to the node in the visual
  editor. Purely cosmetic; every instruction can have one (separate from the
  dedicated `cmt`/"Comment" instruction, which is a freestanding note node).
- **`nx`, `ny`** (optional) — canvas (x, y) position of the node in the
  visual editor. Purely cosmetic layout data; omit them and the editor will
  just place the node at the origin when you open it — the behavior still
  runs correctly.

## Argument value encoding

What you put in a numbered slot depends on what that argument represents:

| Kind | Encoding | Example |
|---|---|---|
| Literal number | `{ "num": N }` | `{ "num": 10 }` |
| Literal item/frame/component/tech/signal id | `{ "id": "item_xxx" }` | `{ "id": "v_enemy_faction" }` |
| Literal coordinate | `{ "coord": { "x": x, "y": y } }` | `{ "coord": { "x": 5, "y": -2 } }` |
| Frame register | plain negative int `-1` to `-4` | `-4` (see table below) |
| Local variable | any string | `"A"`, `"CNT"`, `"Self"` |
| Behavior parameter | plain positive int `1..N` | only valid if `parameters` has ≥N entries |
| Faction (shared) register | `{ "fr": <name> }` (name, resolved at runtime — not a fixed slot, see "Faction (shared) registers" below) | `{ "fr": "some_key" }` |
| `exec` branch target | plain int = **target's rendered dict key + 1** (see "Branch and fall-through resolution" — these values are never adjusted to the 0-based keys used elsewhere in this format); omit = fall through to the next instruction (an explicit wire the compiler encoded compactly because it matched physical position — not a lesser or undecided connection, see "Branch and fall-through resolution" below); `false` = stop, take no further action on this path | `11` jumps to dict key `"10"` |

Local variables are the easy path for anything you don't need to persist
outside the behavior: pick any string name, use it consistently as both an
`out` target and later `in` source, and the game allocates the storage slot
for you when it compiles the behavior. There's no fixed register table to
manage by hand.

**⚠️ Coordinate literal shape, confirmed the hard way:** an earlier draft of
this doc claimed a positional list `[x, y]`. That is wrong — a hand-authored
`{ "coord": [0, 0] }` loaded in-game as a visibly corrupt value (rendered
like a garbled entity reference), because Lua distinguishes an array table
(`{[1]=x, [2]=y}`) from a hash table (`{x=x, y=y}`), and the coordinate
reader looks for `.x`/`.y` fields specifically. `dsc_codec.py` happily
encodes and round-trips a JSON list into a Lua array table — it has no
instruction-semantics layer to know that's wrong for this slot — so this
bug is invisible to the codec's own encode→decode round-trip and only surfaces once
the game itself tries to read the value. Confirmed fix: re-setting the same
coordinate in the in-game register editor and re-exporting produced
`{ "coord": { "x": 0, "y": 0 } }`. Always use the keyed-object form.

### Composite values and the `num` field

Every value in this format is really a pair: an optional "data" part —
**coordinate, entity, or item id, mutually exclusive** — and a separate
numeric `num` part, whose meaning depends on context (e.g. `domove`'s Target
uses it as the arrival range; `item`-typed values use it as a count).
Confirmed in the in-game register editor: setting a coordinate/entity/item
on a value clears whichever of the other two was previously set — a value
can never hold, say, a coordinate *and* an entity at once. `add`/`sub` in
`data/instructions.lua` are thin Lua wrappers over the engine-native `+`/`-`
operators on this Value type (`Set(comp, state, res, Get(left) + Get(right))`)
— the operator implementation itself lives in the engine, not in this Lua
extract, so its exact semantics aren't derivable from source and had to be
confirmed by direct testing (a single-instruction test rig: an `add`/`sub`
node with 3 registers wired to `To`/`From`, `Num`, and `Result`, values set
by hand). Confirmed rules, by operand shape:

- **number ⊕ number** (neither side has data): ordinary scalar arithmetic on
  `num`. Nothing surprising.
- **coordinate ⊕ coordinate**: both parts genuinely combine — coordinates
  add/subtract component-wise, **and** the `num` fields add/subtract too.
  Confirmed via `formation-hold.dcs`'s own `AnchorPos + Offset`:
  `add(To={coord:(17,50), num:0}, Num={coord:(-6,2), num:1})` →
  `{coord:(11,52), num:1}` (`17-6,50+2` and `0+1`).
- **coordinate ⊕ bare number** (other side has no data): the bare number
  broadcasts onto **both** coordinate components, as if it were `(n,n)` —
  e.g. `add(To={coord:(10,100), num:5}, Num={num:3})` → `{coord:(13,103),
  num:5}`. Order/slot doesn't matter for which side "wins" the coordinate
  shape (confirmed by swapping `To`/`Num`), and `Subtract` respects
  `From - Num` direction either way (`From={num:3}, Num={coord:(10,100),
  num:5}` → `{coord:(-7,-97), num:5}`, i.e. `(3,3) - (10,100)`). In all of
  these, **the coordinate operand's own `num` survives untouched** — the
  bare number's value is fully consumed into the coordinate math and does
  *not* additionally add into the result's `num`. This is the load-bearing
  rule behind `formation-hold.dcs`'s Tolerance-preload trick (§ below).
- **entity/item ⊕ bare number**: the entity/item reference passes through
  into `Result` unchanged, and — unlike the coordinate case — the `num`
  fields **add normally** (e.g. `entity, num:5` + `num:3` → same entity,
  `num:8`).
- **entity/item ⊕ entity/item**: `Result` inherits the `To`/`From` (first
  slot's) reference, not `Num`'s; `num` fields still add/subtract normally.
- **coordinate ⊕ entity/item**: not constructible at all (see the
  mutual-exclusion note above), so this combination never has to be
  reasoned about.

So the `num` field is *not* uniformly "always adds" or "always preserved" —
it depends on whether the data-bearing operand is a coordinate (num
preserved, bare number consumed spatially) versus an entity/item (num adds
normally, bare number has nowhere spatial to go).

Practical implication for `formation-hold.dcs`'s Tolerance-preload trick
(setting `num` on `Offset` once, so it rides along through every later `add`
into `Spot`): this relies on the "coordinate ⊕ bare number" rule above, and
holds because `Offset`/`AnchorPos`/`Spot` are always plain coordinates,
never entities — if any of them ever became entity-typed, the `num` would
start adding instead of being preserved, corrupting the intended Tolerance
value.

**`mul`/`div` confirmed 2026-07-11 to follow the identical rules** (a real
in-game test: `mul(To=coord(10,20)[num=5], Num=3)` → `coord(30,60)[num=5]`;
`div(From=coord(90,60)[num=7], Num=3)` → `coord(30,20)[num=7]` — bare-number
broadcast onto both components, `num` preserved untouched, exactly like
`add`/`sub`; `mul(To=v_resource[num=5], Num=3)` → `v_resource[num=15]`;
`div(From=v_resource[num=90], Num=3)` → `v_resource[num=30]` — id passes
through unchanged, `num` scales normally, same shape as `add`/`sub`'s
"adds/subtracts normally" rule extended to multiply/divide). Not derivable
from source (same engine-native `Value` operator overload mechanism as
`add`/`sub`, `data/instructions.lua:1867-1888`), so this was a genuine
open question, not just an unverified formality — now closed.

### Reserved sentinel numbers: `REG_INFINITE` and `REG_NOT`

**Confirmed 2026-07-11** via a real in-game test — a behavior with five
plain `set_reg(Value=N, Target=Result)` calls for `N` = empty/0/1/
`-2147483647`/`-2147483648`. Two things were checked, at two different
layers, and are easy to conflate:

- The **`Value` input pin's own widget in the editor**, left unconfigured
  (the "empty" case), displays `""` — the widget's own "nothing entered
  here" state, genuinely `nil`/`false` at that point, per the display
  formatter below.
- The **`Result` parameter's UI display, read back after actually running**
  all five steps, showed `"0"`/`"0"`/`"1"`/`"≠"`/`"∞"` — i.e. the "empty"
  and explicit-`0` cases are **indistinguishable once compiled and run**:
  an omitted `Value` argument resolves through `Get()`'s own
  `Tool.NewRegisterObject()` fallback to a real `num=0` (`NewValue`'s own
  `num = num or 0` default), not `nil` — so the register it gets copied
  into ends up holding a genuine zero, identical to the explicit-`0` case.
  The blankness is an editor-time-only distinction; it never survives into
  an actual runtime value.

Cross-checked three independent ways against `ui/RegisterSelection.lua`'s
own display and input-parsing code (the same widget renders both an
argument's input pin and a plain register display, which is why both cases
above go through the identical formatter):

- The display formatter itself: `self.input.text = (not reg_num and "") or
  (reg_num == REG_INFINITE and "∞") or (reg_num == REG_NOT and "≠") or
  tostring(reg_num)` — pinning down exactly which two numbers get the
  special symbols, and confirming the blank-string case is specifically
  `not reg_num` (Lua nil/false), not some third distinct sentinel.
- The text-input clamp boundary: `math.max(math.min(tonumber(...),
  2147483647), REG_NOT+1)` — ordinary typed numbers clamp to
  `[REG_NOT+1, 2147483647]`, i.e. both sentinels sit just outside the
  normal user-typeable range, with `REG_NOT` the smaller-magnitude one.
- The dedicated UI buttons' own tooltips: "Set infinite" / "Set not equal -
  Double click to apply" (`ui/RegisterSelection.lua`'s `infbtn`/`notbtn`).

**`REG_INFINITE = -2147483648`, `REG_NOT = -2147483647`** — both INT32-scale
values, and notably **not** what `engine_stub.lua` originally guessed
(`math.maxinteger`/`math.mininteger`, Lua's own 64-bit extremes): the real
sentinels are far smaller in magnitude than 64-bit-int bounds, and
`REG_NOT` is the *smaller-magnitude* one of the pair, not the larger — a
guess based on "one should be a very large positive number, the other a
very large negative number" gets the relative ordering backwards. This
also retroactively confirms every real behavior this project has reviewed
that writes the literal `-2147483648` into a `Range`/`Amount`-style
argument (Mining Leader's `Check Emergency` sub, Fendersons Transport's
`dopickup`) was genuinely writing `REG_INFINITE`, not just "a very negative
number that happens to trip some fallback" — see `data/instructions.lua`'s
own many `== REG_INFINITE`/`== REG_NOT` special-cases (`for_signal_match`,
`for_number`, `check_number`, `combine_coordinate`, `get_distance`, item/
resource-count instructions) for how pervasively the real engine treats
these as meaningful, checkable sentinel values rather than "just very big
numbers." `engine_stub.lua` has been corrected to match.

**A second, related behavior — dangling entity references — settled 2026-07-14 by a targeted
differential test (mechanism native/engine-side, not traceable in Lua source):** when an
entity referenced by a register, variable, or memory-array slot is destroyed, the `entity`
part reads back gone (blank) and the **`num` is preserved verbatim** — a reference stored with
a deliberate `num=42` payload read back as blank+42, a no-payload copy read back as 0, and a
memory-array slot behaved identically to a register. An earlier direct-observation claim that
the `num` reads back `REG_INFINITE` (recorded here 2026-07-11) **did not reproduce and is
refuted for stored values** — that ∞ most plausibly came from a different mechanism (e.g.
`get_distance` against a dead target returning ∞-as-distance), not from the stored reference.
Practical consequences: death-detection by checking the entity part works; a num payload
riding on an entity reference survives the target's death, so a `check_number` against a
dangling ref compares its real, preserved num — never assume ∞ appears. `is_empty` on a
dangling ref with `num≠0` reports NOT empty (the num dominates), while an entity-only
(`num=0`) dangling ref reports **empty** (measured in-game, v3 probe) — so on the current
build, `is_empty` works as a death-detector for plain entity references. The next release
inverts exactly that ("Is Empty will no longer match a target reference that has been
destroyed"): those liveness checks will silently stop detecting death and must migrate to the
new destroyed-reference facilities (Target Type Switch's 'Destroyed Object' pin, Signal
Filter's 'Destroyed References' mode).
Eager-clear-on-destroy vs. lazy-resolve-on-read for the entity field itself remains
observationally indistinguishable from inside a behavior, and no longer matters practically
now that "num survives, entity blanks, all storage classes alike" is established.
`GetRegister`/`GetRegisterData` are native (no Lua definition in this extract — confirmed by
grep), so all of this is empirical, from the differential test.

### Frame registers

Exactly 4 exist (hardcoded bound in `library.lua`), addressed as negative
integers. **Corrected 2026-07-19 — an earlier version of this table was
exactly reversed:**

| Value | Register |
|---|---|
| `-1` | Goto |
| `-2` | Store |
| `-3` | Visual |
| `-4` | Signal |

Confirmed from deployed, in-game-working behaviors' raw wire data (not from
source reading): `library/magnifier_signal.dcs`'s drone-invitation broadcast
— live-confirmed in-game, drones respond to it — writes `@signal` as wire
`-4`, and `library/miner_drone.dcs`'s travel write — live-confirmed, the
drone moves — puts `@goto` at wire `-1`. `desynced_toolkit.bsf` has always
used this mapping (`values.py`/`parse_text.py`/`render_text.py`), which is
why BSF-authored behaviors worked in-game while this table sat wrong.

**The trap that produced the original error:** `data/instructions.lua`'s
`GetRegisterOrComponentRegister` really does map `1 → FRAMEREG_SIGNAL,
2 → VISUAL, 3 → STORE, 4 → GOTO` — but those are the **positive selector
numbers** used by the component-register instructions ("Set to Component"
etc.), a *different address space* from the negative wire encoding. The wire
address `-j` resolves via `InstGet` to native register index `j`, and the
native indices run Goto=1..Signal=4 — i.e. `native index = 5 − selector
number`. Reading the selector mapping and assuming the wire followed the
same order is exactly how the reversed table got written.

(`observer.dcs` clears Visual and Signal at reset: `set_reg` with target
`-3` and `-4`.)

### Faction (shared) registers

Unlike the 4 fixed frame registers (per-entity) or local variables (per-behavior),
a faction register is shared, named storage visible to every behavior running
on any entity owned by that faction — and it's also the backing store for the
Radio Transmitter/Receiver component pair. Confirmed against
`ui/Faction.lua`, `data/library.lua`, and `data/instructions.lua`:

- **Storage.** The first time a faction touches a faction register, the game
  creates a hidden, bodyless entity (`Map.CreateEntity(faction, "f_empty")`)
  and attaches a `c_radio_storage` component to it, cached at
  `faction.extra_data.radio_storage`. That component's own register array
  *is* the set of faction registers. Three parallel tables in its
  `extra_data` track bookkeeping: `names` (register name string → register
  index), `bands` (index → a "band" value, e.g. `{id="v_letter_R", num=1}`,
  shown in-game as `R1`), and `conns` (index → reference count, so a deleted
  register's slot can be reused). Managed in-game from the Faction tab's
  "Manage Faction Registers" button (`ui/Faction.lua`); create/rename/remove/
  rebind-band all go through `FactionAction.FactionRegister`.
- **Bands = radio channels.** Radio Transmitter and Radio Receiver components
  each have their own "Band" register; connecting either one
  (`RadioConnect`/`RadioDisconnect` in `data/components.lua`) links its value
  register to whichever faction register currently has a matching band,
  auto-creating one if no band matches yet. So a Transmitter "broadcasting on
  band R1" is really just writing into the faction register whose band
  happens to be `R1` — any Receiver on the same band, *and* any behavior
  referencing that faction register directly by name, all see the same value.
- **Referencing one directly from a behavior** (no physical Transmitter/
  Receiver needed): the Program editor's argument popup has a "Read/Write
  Faction Register" section listing the faction's current register names;
  picking one sets the argument to `{ "fr": "<name>" }`, exactly like the
  literal-value table above. **The name must already exist for the faction**
  (created via the UI, or already used elsewhere) — unlike a local variable,
  a bare `fr` name doesn't self-declare a new register on first use.
- **Compile-time encoding is by name, not index.** `GetFactionBehaviorAsm`
  (`data/library.lua`) can't bake in a fixed register index, since a
  register's index can change (renamed/reassigned as others are added or
  removed). Instead it assigns each distinct `fr` name found in the behavior
  a synthetic negative address `-99 - n` (a *fourth* address range, disjoint
  from frame registers `-1..-4`, parameters `1..N`, and local-variable/
  constant mem slots `>N`), and records the mapping in `asm.fregs[n] = name`.
- **Runtime resolution re-looks-up the name every access.** In
  `data/instructions.lua`, any resolved address `<= -100` routes through
  `CallRadio`: it reads the name back out of the compiled `fregs` table, then
  re-resolves *that name* to whatever index it currently has via
  `radio_storage.extra_data.names`, then reads/writes the `c_radio_storage`
  component's register at that index. This is why renaming or reordering a
  faction's registers in the UI doesn't break existing behaviors that
  reference them — they're bound by name, not by a baked-in slot number, and
  are re-resolved fresh every time they're touched.
- **Write guard.** If a faction register is currently link-driven by a Radio
  Transmitter (`RegisterIsLink` returns true for its index), a behavior's
  direct `fr`-write is silently dropped rather than fighting the transmitter
  for the value. Reads are never affected by this — only writes.

### Parameters (sub-behaviors only)

If you're writing a reusable sub-behavior meant to be invoked via `call`
(id `call`, name "Call") or `load_behavior` (name "Load Behavior") rather
than run as a bot's main program, declare top-level `parameters` (and
optionally `pnames`) — one entry per input/output the sub-behavior exposes.
Inside that behavior's own instructions, reference parameter *N* with the
plain positive integer `N` (1-based) instead of a variable name or frame
register. Callers then supply/receive those parameters as extra trailing
arguments on the `call`/`load_behavior` node, one per declared parameter,
in order (see "Variable-length argument instructions" below) — this part
of the UI/library plumbing (`ui/Program.lua`) is more involved than a
single-behavior use case needs; if you only need one bot's own program,
skip `parameters` entirely.

## Branch and fall-through resolution

Every "what happens next" value — whether it's the instruction-level `next`
field or the value in an `exec`-typed argument slot — resolves the same way:

- **omitted / not present** → fall through to the next instruction in
  sequence (dict key + 1). **This is not "nothing was decided, so it
  defaults arbitrarily" — it's a real, explicit wire the visual editor's
  compiler doesn't spell out as an integer, because doing so would
  be redundant with position** (user-confirmed from how the real editor
  behaves, not inferred from the wire bytes alone): the compiler only
  omits `next`/an `exec` arg when there genuinely is a connection to
  whatever instruction it placed immediately next; a pin left truly
  unconnected in the editor gets `false` written explicitly and
  unconditionally, **regardless of what happens to physically follow it**.
  There is no real-data case of "nothing was wired, but the next slot
  happens to be filled anyway" — that case is always `false`, never
  omission.

  **Real exception to "omission is always a deliberate wire," confirmed
  in-game 2026-07-09:** that guarantee only holds for a pin that existed in
  the instruction's own schema (`data.instructions[op].args`) *at the time
  the behavior was compiled*. `domove`/`dodrop`'s "Path Blocked" exec pin
  did not exist when an old behavior predating it was authored — it was
  added to the instruction in a later game update. Confirmed directly: the
  in-game editor, opening that old behavior today, shows "Path Blocked"
  visually wired to the same destination as the plain fallthrough — i.e.
  the *current* editor cannot tell "this pin was deliberately left
  unconnected" apart from "this pin didn't exist yet when the behavior was
  last saved," and doesn't try to. The compiled *effect* is identical
  either way (both resolve to plain fallthrough — see above), so this
  doesn't change any runtime-semantics claim already made here, but it does
  mean **the causal story "the original author chose not to wire this" is
  not a safe inference from an old behavior's own omission** — instruction
  schemas gain new pins over time, and old saved data never had the
  argument slot to omit deliberately in the first place. There is no way to
  distinguish the two cases from the wire alone without independently
  knowing which game version the behavior was last compiled under.

  A follow-up real test (same day) sharpens this further: deliberately
  wiring "Path Blocked" to the physically-next instruction in the *current*
  editor **also** compiles to an omitted key — the exact same
  representation as the schema-absence case above. So two genuinely
  different, real authorial situations — "this pin didn't exist when the
  behavior was authored" and "this pin existed and was deliberately wired
  to whatever already follows by default" — are indistinguishable from the
  wire alone; only "left genuinely untouched" is ruled out as a source of
  omission (that's always `false`, per the rule above). Practically:
  deliberately wiring a pin to whatever already follows by default is a
  no-op, both in compiled effect and in the wire's own encoding — there was
  never a reason to do it other than making the pin visibly "look" wired
  in the editor.
- **an integer** → jump to the instruction whose rendered dict key is
  **that integer minus 1** (see off-by-one warning below)
- **`false`** (or a value whose `-1` target is beyond the end of the
  instruction list) → stop *this path* — **not the same as stopping the
  behavior**: inside a loop/`sequence`/other block context it just advances
  that block (next iteration/next pin, see "Block-type instructions"
  below); only at the true outermost level does it fall back to Program
  Start, and even then without yielding the tick (see "Stopping a
  behavior" below, which covers a real crash mode this causes). This is
  what a genuinely unconnected pin always compiles to — not omission.

Instructions with more than one `exec` argument (e.g. `check_number`'s
"If Larger" / "If Smaller") branch based on which condition the instruction's
own logic decides is true at runtime — you supply a target (or leave it to
fall through, or set `false`) for each, and only the one that actually
matches fires. Instructions with zero `exec` args just use the top-level
`next` field (or its default) unconditionally.

### The top-level `next` field's real display name/existence: `exec_arg`

`data.instructions[op]` carries a field sibling to `args`, `exec_arg`, that
this doc had not documented until a user caught the visual editor showing
something other than a generic "next" pin for `check_number` specifically.
Confirmed directly from `data/instructions.lua`, three real shapes:

- **absent** (the common case) — the top-level `next` field is the generic
  "next" pin every reader already assumes.
- **a `{1, "Name", desc}` table** — names the top-level pin for real, e.g.
  `check_number`'s `exec_arg = { 1, "If Equal", "Where to continue if the
  numerical values are the same" }`. This is the exact mechanism behind the
  documented "check_number's Equal outcome isn't a numbered arg slot" gotcha
  elsewhere in this doc — now with its real display name sourced from live
  data instead of a generic placeholder. A handful of other comparison-style
  instructions declare the same `{1, "If Equal", ...}` shape.
- **`false`** — this op has **no top-level `next` pin at all**, and the real
  visual editor draws none: `exit`, `restart`, and `last` (Break) all
  declare `exec_arg = false`, matching that it's genuinely nonsensical to
  wire a continuation after any of them (`exit`'s own `func` never even
  consults `next`; see "Stopping a behavior" below). A `next` value sitting
  in the wire data for one of these ops regardless is inert, never read —
  tooling should not display it as if it were a real, meaningful wire.

### ⚠️ Off-by-one: jump values are raw 1-based Lua positions, not dict keys

`dsc_codec.py` renumbers the *instruction list itself* from Lua's native
1-based array down to the 0-based dict keys shown throughout this doc (see
"Top-level envelope" above) — but a jump/`next` value is just a plain
integer sitting in an argument slot, indistinguishable to the codec from any
other number (it has no instruction-metadata layer, per `CLAUDE.md`). It is
**never adjusted**, so it stays a raw 1-based Lua position. Concretely: a
jump value of `11` targets rendered dict key `"10"`, not `"11"`.

Proof from `observer.dcs`'s four identically-shaped `scan` fallback blocks —
each one's "No Result" target really lands on the *next* scan attempt only
if you subtract 1:

```
"2":  scan(v_enemy_faction) -> A, No Result -> 11   // true target: key "10"
"10": scan(v_damaged)       -> A, No Result -> 14   // true target: key "13"
"13": scan(v_infected)      -> A, No Result -> 17   // true target: key "16"
"16": scan(v_droppeditem)   -> A, No Result -> 20   // true target: key "19"
"19": label "Random walk if we can move"
```

Reading the raw values as direct dict keys would skip every scan but the
first (each "No Result" would land one instruction *past* the next scan
call, on that scan's post-processing step, with a stale/unset `A`). Reading
them as `value - 1` chains through all four attempts in sequence into the
random-walk fallback, consistent with each label's own name (one per scan type).

**When decoding:** to find what dict key an `exec`/`next` integer targets,
subtract 1. **When hand-authoring/encoding:** to jump to dict key `K`,
write `K + 1` in the argument slot.

## Computed jumps: the `jump`/`label` instruction pair

Everything above (`next`, `exec` slots) is a **compile-time-fixed** branch —
the target dict key is baked into the encoded value. There's a separate
pair of instructions for a **runtime-computed** target, confirmed by reading
`data/instructions.lua` (`label` ~line 522, `jump` ~line 534) and validated
in-game (`hexat.dcs`/`hexat2.dcs`, a `HexAt` test behavior with a 6-way
computed dispatch on a side index `k`):

- `label` (`op: "label"`) takes one `in` arg, "Label identifier" (`{"0":
  <value>}`) — a no-op marker at runtime, but its value is what `jump`
  matches against.
- `jump` (`op: "jump"`) takes one `in` arg, also "Label identifier". At
  runtime it reads that value, then linearly scans *every* instruction in
  the compiled behavior for a `label` whose own value is equal, and jumps
  there — the position of the matching `label` in the instruction list is
  irrelevant, it can be anywhere, in any order.
- If no `label` matches, `jump` falls through to its own `next` (default:
  the following instruction) — so a `jump` can have a `next`/`false` too,
  as a "no matching state" fallback.

Because the match is a runtime value comparison rather than a fixed dict
key, this is the only way to encode a genuine computed/indirect jump (e.g.
"jump to the label named by this variable"), as opposed to a chain of
`check_number`/`compare_register` branches. Confirmed working with plain
numeric label ids (`{"num": 0}` .. `{"num": 5}`, matched against a variable
holding a computed integer 0-5) — the game correctly dispatched to the
matching branch. Per direct user experience writing other behaviors: a
common idiom is to drive this with an **id-typed value** (item/signal id)
rather than a bare number, as a readable state discriminator for a
parameter-driven state machine (jump to the label representing "current
state"); this doc doesn't yet have a worked example of that form.

Don't reach for `jump`/`label` for a branch whose target is always the same
dict position — a plain `next: K+1` is simpler and is what the in-game
editor itself produces when you ask it to converge several branches on the
same following instruction (confirmed: cleaning up a `hexat.dcs` draft that
used `jump`-to-a-shared-"done"-label for every branch, the game's own
re-export collapsed it to a plain `next` on each branch instead, keeping
`jump`/`label` reserved solely for the one genuinely dynamic dispatch).

## Stopping a behavior: `exit`/`restart` vs. `next: false`

`next: false` (or falling off the end of the instruction list) only ends
*the current instruction's own* execution path for this tick — it does
**not** halt the behavior, and it does **not** yield the tick either. At the
top level (outside any Loop/Sequence-style block), running out of
instructions this way is the *implicit default*: flow falls back to the
Program Start node, same destination `restart` jumps to explicitly (see
below) — but, critically, without yielding. Under the default Locked mode
(one instruction per tick) that's invisible, since only one instruction runs
regardless. But under `unlock` (see that instruction's own `explain` text),
which runs instructions back-to-back until something actually yields, this
implicit jump-to-start is not a yield point, so a behavior with no
`wait`/`exit` anywhere spins through the whole program repeatedly within the
same tick and trips the engine's safety limit ("If more than 1000
instructions are executed in one tick then the behavior controller will
crash at that location" — this is exactly what happened to an early
`hexat.dcs` draft: 51 instructions, `unlock`, no `wait`/`exit`, `next: false`
on the last instruction, and it still hit the crash).

Two dedicated instructions, per their `func`s in `data/instructions.lua`,
and corrected here from an earlier wrong read of that source (verified
in-game):

- **`exit`** (~line 459, zero args) — **actually stops the behavior.**
  Nothing more executes on this or any later tick until something else
  explicitly restarts it — it is a genuine halt, not "yield and resume next
  tick" (an earlier version of this doc guessed the latter from the source
  alone, since `state.counter` gets reset to `1` and the func `return`s
  `true`; that guess was wrong — confirmed against real behavior in-game).
  This is what actually fixed the `unlock` crash above: unlike the implicit
  fall-off-the-end path, `exit` really yields *and stays stopped*, so there
  is no next-tick re-entry to spin on.
- **`restart`** (~line 482, zero args) — forces program flow immediately
  back to the **Program Start** node, unconditionally. This matters
  specifically from *inside* a block-style instruction (Loop, Sequence,
  etc.) — those maintain their own internal notion of "what happens when
  this block's body falls off the end" (e.g. next loop iteration, next
  sequence step), which is *not* the same as jumping to the true program
  start. `restart` overrides that and forces the real start regardless of
  what block(s) you're nested in — it's the explicit-instruction form of
  the same implicit jump a flat, non-block program takes for free when it
  falls off the end. Note `restart`'s func does not `return true`, so
  (unlike `exit`) it does not yield either — reaching it under `unlock` with
  no other yield point would still spin.

Practical rule of thumb: **any `unlock`ed behavior needs a reachable
`wait` or `exit` on every path** — `restart` and plain fall-off-the-end
both loop back to the start without yielding, so neither one alone
prevents the 1000-instruction crash.

**1.0.18055 update, confirmed from source (not yet re-tested in-game):** hitting the
step limit no longer stops the behavior — the changelog's "Instead of stopping a
behavior on a crash (i.e. exceeding unlock step limit or call depth), keep it paused
so the failure can be inspected in the behavior editor" (1.0.17919) is now literally
what `unlock`'s own `explain` text says ("the behavior will abort or continue the next
tick", not "crash"), and `unlock` itself gained two configured hidden-literal options
(a custom step-limit `n`, default still 1000, and a `c` mode selecting "Pause on
limit" vs "Continue next tick") — a `Rework behavior unlocking` addition (1.0.17919/
1.0.17933) that also **resets unlock state and waits 1 tick whenever an unlocked
behavior ends and restarts**, i.e. every POP-to-empty auto-restart now costs a real
tick it didn't before. The deployed behaviors in `library/` all have explicit `wait` hubs, so this
extra tick is expected to be benign, but hasn't been confirmed against a live run.
`n`/`c` are packed into one signed int only at `make_asm` (ASM-compile) time — same
save-format shape as `memory_sift`'s `c`/`u` pair — and aren't yet captured by BSF's
`HIDDEN_FIELD_TABLE` (a documented gap, `bsf/decompile.py`), so a hand-edited
non-default `unlock` step limit/mode won't round-trip through BSF text today; no
checked-in fixture currently sets either.

## Block-type instructions: what `next: false` means *inside* one

The "falls back to Program Start" story above is only true at the outermost
level. Confirmed from `data/instructions.lua`'s `sequence` (~line 3722) and
`last`/Break (~line 438), and empirically from `hexat_test.dcs` (a `HexAt`
sub-behavior with a nested nested loop + sequence, called from a 6×T-range
test harness, matched by hand against `hex_expansion_math.md`'s formulas
for all 92 `(R, T)` combinations with zero mismatches): several instructions
push a **block context** (`BeginBlock`, stored on `state.blocks`) around
their own body. Inside that context, a `next: false` (or a dead-ended
fall-through) doesn't restart the whole program — it pops back to the
*enclosing* block, which decides what happens next on its own terms:

- **`sequence`** (args: `First`/`Second`/`Third`/`Fourth` exec, all
  optional, plus `Last` exec) chains whichever of `First..Fourth` are
  wired, in order — each one runs to its own dead end, then control
  returns to `sequence` to start the next wired one, and finally jumps to
  `Last`. A pin wired **`false`** (genuinely unconnected in the editor) is
  skipped — not pushed onto the internal step list at all (the
  func's own `if exec_first then ...` guard; this is also why unconnected
  stages cost zero ticks). An ***omitted*** pin is **not** skipped: per the
  universal omission rule above, `GetFactionBehaviorAsm` resolves it to the
  physically-next instruction at compile time (`val == nil → inst_idx + 1`),
  so the func receives a real target and that pin becomes a real step —
  `hexat_test.dcs`'s own `sequence` relies on exactly this (its `First` pin
  is omitted and correctly runs the X-computation chain that physically
  follows). An earlier version of this bullet said the omitted case was
  skipped — wrong, and contradicted by that same in-game-validated fixture.
  This is the confirmed idiom for "run these two
  independent calculations, then continue once both are done" — e.g.
  `hexat_test.dcs`'s `HexAt` computes X (`First`) then Y (`Second`/`Third`)
  then combines them (`Last`), with a `next: false` ending each of the
  first three legs.
- **Loops** (`for_number` and everything else `instructions_index.md` tags
  `*(loop)*`) use the same mechanism for their body: a `next: false` (or
  running off the end of the loop body) advances to the *next iteration*,
  not a program restart. Confirmed with a nested loop in
  `hexat_test.dcs`: the inner `for_number` (looping `T`) has its own
  `Done` exec explicitly set to `false` — and this correctly falls back to
  advancing the *outer* `for_number` (looping `R`) to its next iteration,
  rather than crashing or halting the behavior, because the inner loop's
  block sits nested inside the outer loop's block.
- **`last`** (Break, ~line 438) is the explicit-break instruction for
  this same stack: it looks up the *innermost* entry on `state.blocks` and
  invokes that block type's own `.last` handler (for `sequence`, this
  jumps straight to `Last`, skipping any remaining pins).

Net effect: "does `next: false` stop the behavior?" doesn't have a single
answer — it depends on block nesting depth at that point. It only reaches
the true top-level fall-off-the-end behavior (described above) once it has
popped through every enclosing block.

### `for_number`'s `Step` auto-direction

`for_number`'s `Step` arg (`instructions_index.md`: "use -1 or 1 based on
inputs if left empty") auto-detects direction from `From`/`To` when
omitted, confirmed by an artifact in a captured in-game log of the HexAt
test run and in `HexIndexOf_test_1.dcs`'s own harness: for `R == 0`, the inner loop is
built as `for_number(0, 6*R - 1, ...)` = `for_number(0, -1, ...)` with
`Step` omitted — since `From (0) > To (-1)`, it silently runs with an
implicit `Step = -1`, iterating `T = 0` then `T = -1` (two iterations, not
zero). This is correct/documented behavior, not a bug — but it's easy to
trip over: any omitted-`Step` loop whose bound expression can invert
sign (as `6*R - 1` does at `R = 0`) will silently reverse direction and
run at least once, rather than running zero times the way a fixed-step
loop with `From > To` normally would.

### `check_number`'s "equal" case

A confirmed idiom from `hexat_test.dcs`'s own `R == 0` guard. The in-game
editor shows `check_number` with three labeled pins — **If Larger**, **If
Smaller**, **Equal** — but only `If Larger`/`If Smaller` get their own
numbered argument slots (`"0"`/`"1"`); `Equal` isn't a separate slot at
all, it's carried by the instruction's ordinary top-level `next` field (or
plain fallthrough if that's also omitted). To test for equality, point
*both* `If Larger` and `If Smaller` at the same "not equal, do the
general-case work" target, and let `next`/fallthrough be the "equal" case.
Concretely, `HexAt`'s guard is `check_number(Value=R, Compare=<omitted,
defaults to 0>, If Larger -> general case, If Smaller -> general case)`,
falling through (no explicit `next`) straight into `Result = Origin` — i.e.
the `R == 0` branch is exactly `Equal`, just encoded as a fallthrough
rather than a numbered slot.

### ⚠️ Gotcha: an omitted exec arg never consults the top-level `next`

Found while hand-authoring `HexIndexOf_test_1.dcs`'s cube-rounding
correction cascade (three chained `>` comparisons where the "false"
destination for the first two isn't the physically-next instruction).
`If Larger`, `If Smaller`, and `Equal` (via `next`, see above) are three
independent slots, each resolving its own omission the same way any
instruction's output does when left unwired — falling through to the
physically-next instruction. Setting the top-level `next` field does
**not** also cover an omitted `If Smaller` (or `If Larger`); it only ever
supplies the `Equal` pin.

Concretely, for a plain `value > compare` test where "false" (smaller-or-
equal) needs to jump somewhere other than dict-key+1: setting `If Larger`
explicitly and leaving `If Smaller` omitted, while also setting the
top-level `next` to your intended "false" target, **does not work** — the
omitted `If Smaller` still resolves to dict-key+1, ignoring your `next`.
You must wire `If Smaller` explicitly to the same target, in addition to
`next` (needed separately to also catch the equal case), or restructure so
the false destination genuinely is the physically-next instruction (as the
`check_number`'s-implicit-equal idiom above does deliberately). All three
outcomes (`If Larger`, `If Smaller`, top-level `next`) are independent
slots with independent omission behavior — never assume setting one
implicitly covers another.

## Hidden literal fields (`make_asm`)

A few instructions take one configured value that isn't a normal wired
argument — it's set once on the node itself (a dropdown, text box, or
sub-behavior picker in the visual editor) and shows up as a plain named
field alongside `op`, not a numbered key. Enumerated from every `make_asm`
in `data/instructions.lua` (field name, and the default if omitted):

| Instruction id | Field | Default | Meaning |
|---|---|---|---|
| `call` | `sub` | `false` | id of the library behavior to call, **or** a 1-based index into the top-level `dependencies` array for an embedded sub-behavior — see below |
| `load_behavior` | `sub` | `false` | id of the library behavior to load remotely |
| `domove` | `c` | `1` | `1` = Synchronous, `2` = Asynchronous |
| `moveaway_range` | `c` | `1` | movement mode, as above |
| `scout` | `c` | `1` | movement mode, as above |
| `dodrop` | `c` | `2` | drop mode |
| `dopickup` | `c` | `2` | pickup mode |
| `request_item` | `c` | `2` | request mode |
| `request_wait` | `c` | `2` | wait mode |
| `for_producers_items` | `c` | `1` | iteration mode |
| `bitwise_op` | `c` | `1` | selected bitwise operator |
| `lock_slots` | `c` | `1` | lock mode |
| `for_signal_match` | `c` | `1` | match mode |
| `count_item` | `c` | `1` | count mode |
| `get_unit_info` | `c` | `1` | which info field to read |
| `get_unit_power_info` | `c` | `1` | which info field to read |
| `get_item_info` | `c` | `1` | which info field to read |
| `count_slots` | `c` | `1` | count mode |
| `notify` | `txt` | `false` | notification text |
| `set_signpost` | `txt` | `false` | signpost text |

For the numeric `c` fields, check that instruction's `node_ui` in
`data/instructions.lua` (or just try it in the in-game editor) to see what
each integer value corresponds to — the meaning is UI-label-specific and
not worth duplicating here.

### `call`'s `sub`: saved-library id vs. embedded dependency index

`call`'s `sub` field takes two genuinely different shapes depending on
whether the target sub-behavior has been saved to the faction's library or
is just embedded/private to this one `.dcs`:

- A **string** — the id of an existing saved-library behavior (the
  `data/library.lua` runtime resolves this via `GetFactionBehaviorAsmById`).
- A **small integer** — a **1-based index into the top-level
  `dependencies` array** (see "Top-level envelope" above), for a
  sub-behavior that only exists embedded in this `.dcs`, never saved
  separately. Confirmed from `data/library.lua`'s
  `PackLibraryItemToCompactedItem` (the export path): `depnum = #dependencies
  + 1; dependencies[depnum] = item` — the *n*-th embedded dependency is
  assigned `sub = n` (1-based Lua), which is `dependencies[n-1]` in
  `dsc_codec.py`'s 0-based JSON rendering. `-1` is a reserved sentinel
  ("reference to outer") for a behavior that calls **itself** recursively,
  distinct from a real dependency index; the legacy runtime format instead
  used `sub == 0` for the same self-call case (`call`'s own `func`, and a
  comment in `UnpackCompactedItemToLibraryTable`, both note this).

Concretely, in `hexat_test.dcs`, the harness's `call` node has `"sub": 1`
and the top-level `dependencies` array has exactly one entry (the `HexAt`
sub-behavior) — `dependencies[0]` in JSON, i.e. Lua-array slot 1. There's
only one dependency in this example so index-vs-something-else can't be
fully disambiguated from this case alone, but it matches
`PackLibraryItemToCompactedItem`'s numbering exactly, and is the natural
reading given `dependencies` is documented as a plain array.

## Variable-length argument instructions (`var_args`)

`call`, `load_behavior`, `build`, and `produce` accept extra trailing
positional args beyond their fixed `args` list — one per parameter the
target sub-behavior (for `call`/`load_behavior`) or blueprint (for
`build`/`produce`) declares. Encode each the same way as any other `in`/`out`
argument (literal, register, or variable); the count and in/out-ness must
match the target's own `parameters`/`params` definition. This is an advanced
case — most hand-written behaviors won't need it.

## Worked example

Annotated opening instructions of `observer.dcs` (now at `tests/data/observer.dcs`
— decode it yourself with
`LupaEngine(source).decode_dcs(open("tests/data/observer.dcs").read())` to see
the rest as a genuine Lua table — the 0-based dict keys below are the retired
`dsc_codec.py`'s rendering; real Lua keys are one higher):

```jsonc
"0": { "op": "unlock" },                          // run multiple instructions per tick
"1": { "0": { "num": 1 }, "op": "wait" },         // wait 1 tick (Time = literal 1)
"2": {                                            // scan: Filter1, Filter2, Filter3, Result(out), No Result(exec)
  "0": { "id": "v_enemy_faction" },               //   Filter1 = enemy faction
  "3": "A",                                       //   Result -> local var "A"
  "4": 11,                                        //   No Result -> jump to instruction 11 - 1 = key "10" (next scan attempt)
  "op": "scan"
},
"3": { "0": { "id": "v_enemy_faction" }, "1": -3, "op": "set_reg" },  // Visual <- enemy faction id (status icon)
"4": { "0": "A", "1": -4, "op": "set_reg" },      // Signal <- found enemy (var A): the Observer telemetry broadcast
"5": {                                            // check_number: IfLarger(exec), IfSmaller(exec), Value(in), Compare(in)
  "0": 8,                                         //   If Larger -> jump to 8 - 1 = key "7" (add)
  "2": "CNT", "3": { "num": 0 },                  //   comparing CNT > 0
  "op": "check_number"                            //   (If Smaller omitted -> falls through to 6)
},
"6": { "0": "A", "next": false, "op": "ping" },   // ping the enemy (var A); next=false, stop this path
"7": { "0": "CNT", "1": { "num": 1 }, "2": "CNT", "op": "add" },  // CNT = CNT + 1
"8": {                                            // check_number: CNT vs 10
  "1": false,                                     //   If Smaller -> false, stop
  "2": "CNT", "3": { "num": 10 }, "op": "check_number"
                                                   //   (If Larger omitted -> falls through to 9)
},
"9": { "1": "CNT", "next": false, "op": "set_reg" } // reset CNT (Value omitted = clear), next=false
```

Note how `scan`'s and `check_number`'s numbered keys skip straight to the
`out`/`exec` slots (`"3"`, `"4"`, or `"0"`, `"1"`) when the earlier `in`
slots (`Filter2`, `Filter3`) are left unused — the positions are fixed by
`instructions_index.md`'s `args` order, not by how many you fill in.

## Recommended authoring workflow

**Don't author at this wire level directly** — that's what BSF exists for
(`behavior_source_format.md`, implemented in `desynced_toolkit.bsf`): it
handles the position re-indexing, off-by-one branch encoding, and
`make_asm` hidden fields described in this document mechanically, validates
strictly (unknown ops/args/pins/ids, duplicates — with line numbers and
suggestions), and lints the legal-but-suspicious tier. The normal loop:

1. `python -m desynced_toolkit.bsf decompile` an existing behavior close to
   what you want (or start a BSF file from scratch).
2. Edit the text.
3. `compile` (parse validation + lint warnings run automatically), then
   `semantic-diff` the result against the original to confirm exactly the
   intended changes and nothing else.
4. Optionally run the result through `Interpreter` against the real
   instruction semantics before spending an in-game test on it.
5. Load it in-game (paste into the behavior library) to confirm it opens
   without error in the visual editor and runs as expected.

The raw-table route (decode with `LupaEngine.decode_dcs()`, edit the Lua
table per this document, re-encode) remains available for work *on the wire
format itself* — just remember that `exec`/`next` targets are absolute
positions (inserting an instruction shifts every index after it), the
off-by-one (a jump to Lua key `K` is encoded as `K + 1`), and that an
immediate decode-and-diff of your own re-encoded output is the only
correctness check outside the game.
