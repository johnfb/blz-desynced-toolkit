# Behavior Source Format (BSF)

A textual, human/LLM-readable representation for Desynced behaviors, designed
to be decompiled *from* and compiled back *to* the real instruction-table
shape `dcs_wire.py` already decodes/encodes (see `behavior_format.md` for
that underlying wire format — this document is the layer above it).

This format's name is **BSF** (Behavior Source Format) — settled on
2026-07-07, formalizing this doc's own working title rather than inventing
new vocabulary. Referred to as "this format" or "BSF" interchangeably below.

## Why this format, not tree-structured pseudocode

An earlier direction (a Python-subset `AstCompiler`, removed from the tree
2026-07-14 once BSF fully superseded it — git history only) targeted
Python-like syntax (`if`/`else`, `for`). Empirical analysis of 319 real user-created behaviors
(see project memory / `scripts/analyze_corpus.py`) found this is a poor fit
for a large share of real cases: 20.7% use `jump`/`label` computed dispatch,
which is indirect goto by construction and cannot be expressed as structured
control flow at all, and a further meaningful fraction have control-flow
merge points that don't correspond to simple `if`/`else` reconvergence (a
proper reducibility check would be needed to give an exact number — not yet
done). This format is graph-native instead: every instruction is a node with
a stable identity, and every real control edge — however it converges or
loops — is either explicit or a well-defined implicit default, never forced
into a shape it doesn't have.

This is the primary **read/edit** representation (dense, precise, round-trip
to the real table). A secondary Mermaid rendering (see "Visualization"
below) is generated on demand from the same underlying graph for
at-a-glance structural review — the two are not competing formats, they
serve different purposes off the same data.

## Grammar overview

```
behavior := header instruction+ sub_behavior*

header := "behavior" NAME "(" param_list? ")" ":" header_attr*
header_attr := "desc:" STRING               -- attrs accepted in any order, each at most once
             | "keepvars:" "true"            -- see "keepvars/keeparrays" below; omitted
                                              -- entirely when false (the common case), never
                                              -- rendered as "keepvars: false"
             | "keeparrays:" ("\"startup\"" | "\"store\"")  -- see below; omitted when absent

param_list := param ("," param)*
param := NAME "*"?                 -- from pnames[i], or "param<i>" if absent; trailing "*" is
                                    -- a DISPLAY-ONLY marker for "written to somewhere in this
                                    -- behavior's own body", recomputed fresh on every render
                                    -- and ignored (stripped, not stored) on parse -- see
                                    -- "Parameter direction" below

instruction := (NODE_ID ":")? OP "(" arg_list? ")" branch_note* cmt_block? ";"?
             -- the "NODE_ID :" prefix is OPTIONAL: emitted (and required) only for a node
             -- something actually references (a branch/jump target); a node reached solely by
             -- positional fallthrough is written bare, as just "OP(...)". See "Optional node
             -- ids" below.
             -- A node is a single physical line UNLESS it wraps its arg list across lines or
             -- carries a cmt_block, in which case it MUST end with ";" (the terminator -- the
             -- parser scans for it and never counts indentation). See "Multi-line nodes and
             -- the cmt block" below.
cmt_block := "cmt" "=" TRIPLE_QUOTED    -- a free-form node comment, rendered on its own line(s)
                                         -- under the branch notes (mirrors the in-game
                                         -- under-node comment); distinct from a "#" comment,
                                         -- which is non-structural and dropped on parse
arg_list := arg ("," arg)*
arg := ARGNAME "=" value
     | HIDDEN_FIELD_NAME "=" (NUMBER | BOOL | STRING)  -- make_asm hidden literal field, see
                                                        -- "Hidden literal fields" below

value := NUMBER
       | "coord" "(" NUMBER "," NUMBER ")" ("[" "num" "=" NUMBER "]")?
       | IDENTIFIER ("[" "num" "=" NUMBER "]")?      -- id-typed literal, optionally with num
       | "$" NAME                                    -- variable
       | NAME                                        -- resolved parameter name
       | "@" ("goto" | "store" | "visual" | "signal" | NUMBER)  -- frame register; symbolic name when N is 1-4, else bare @N
       | "fr" "(" NAME ")" ("[" "num" "=" NUMBER "]")?  -- faction (shared) register, resolved by name at runtime

branch_note := ">" (NODE_ID | "POP" | "NEXT") "(" PINNAME ")"
             -- "NEXT" = explicit fall-to-physically-next. An op with 2+ exec pins must
             -- write EVERY pin; a single-pin op may omit its note for plain fallthrough.
             -- See "Explicit-pin rule" below.

sub_behavior := "sub" NAME "(" param_list? ")" ":" instruction+
              -- one per bundled dependencies[]/subs[] entry
```

`#` starts a comment (full-line, or trailing after the branch notes) —
non-structural, dropped on parse, never emitted by the default render; see
"Comments, annotated rendering" below. Blank lines between instructions are
allowed and carry no structure (a block ends only at the next
`behavior`/`sub` header).

`NODE_ID` is an arbitrary, stable identifier — not a number, not required to
be sequential, not tied to the node's position in the listing. See "Node
identity vs. wire position" below for why, and for how it's assigned, and
"Optional node ids" directly below for when it appears at all.

### Optional node ids

A node's `NODE_ID :` prefix is **optional**, and the decompiler emits it only
when some other edge actually references that node — a branch/pin target
(`>id (Pin)`), or a resolved static `jump→label` destination. A node reached
solely by positional fallthrough carries **no id at all**; it renders as a
bare `OP(...)` line. The parser accepts an id in either position: a bare `NODE_ID:` line **above**
the instruction (the decompiler's own output — see below), or the inline
`NODE_ID: OP(...)` form on one line; a line starting straight with `OP(` is
id-less and gets a hidden internal id (`__nN`, reserved — not addressable as a
branch target) so graph edges still work.

**The decompiler puts the id on its own line, above the instruction**, so
every instruction body starts at column 0 and they line up regardless of id
length (user, 2026-07-20). A referenced node then reads like an assembly
label — `>engage_target (If Larger)` jumps to the `engage_target:` line:

```
engage_target:
check_number(Value=$dist, Compare=$standoff)  >attack (If Larger)  >POP (If Smaller)  >NEXT (If Equal)
```

A bare `NODE_ID:` line is unambiguous — it is neither an inline `id: op(...)`
(which has an `op(...)` after the colon) nor a `label(...)` op (which has `(`,
no bare colon), the two things the loose word "label" might suggest it
collides with. It binds the next instruction; a declaration with no
instruction after it, two in a row, or one paired with an inline id on the
same node are all parse errors. Rationale (user, 2026-07-18/20):
the decompiler-assigned id is unstable across re-exports (the editor reorders
untouched nodes on every save), so most instructions carrying one at all was
both diff noise and an invitation to anchor references on the unstable token.
With ids only on genuine targets, the bulk of a listing has none, and what a
reference *can* name is exactly a meaningful destination.

### Multi-line nodes and the cmt block

A node's text may span multiple physical lines — a wrapped arg list, branch
notes on their own lines, or a `cmt` block — and any node that does **must end
with `;`**. A node written entirely on one line stays bare (newline-terminated,
no `;`), so every pre-existing single-line behavior parses unchanged. The
terminator's real purpose is to let indentation and blank lines stay **purely
cosmetic**: the parser scans for `;` (quote/bracket/comment-aware) and never
counts whitespace, so the renderer is free to indent a continuation line for
readability without that indentation meaning anything. A multi-line node with
no `;` is a hard parse error, as is content after the `;`.

A **node comment** (`cmt`) renders as a triple-quoted block on its own line
under the branch notes:

```
engage: check_number(Value=$dist, Compare=$standoff)  >attack (If Larger)  >flee (If Smaller)  >NEXT (If Equal)
  cmt="""
  Inside the standoff band — break off; gunners are fragile without the shield.
  """;
```

A single-line comment renders compact (`cmt="""hold the line""";`). The body is
literal text between the delimiters (exactly one leading and one trailing
newline stripped, so both forms round-trip the exact string); a `#` or `;`
inside it is content, not a comment or terminator — the whole reason a
triple-quoted block was chosen over `#`-prefixed lines, which would collide
with the non-structural `#` comment. `cmt` is still accepted inline as an
ordinary `cmt="..."` arg (single-quoted, escaped), and a body that itself
contains a triple-quote falls back to that inline form automatically (it can't
be block-quoted).

**Descriptive ids for the nodes that keep one.** A referenced node is named
after its own **role**, not after what jumps to it (naming by predecessor
breaks under fan-in and moves on unrelated edits — the instability this
removes): a `label` node takes its `Label` value (`v_transport_route` →
`label_transport_route`, `v_broken[num=1]` → `label_broken_1`), every other
node its op (`set_reg`, `jump`). This reads well at the reference site
(`>label_broken_1 (jump→label)`) and is stable across the editor's node
reordering. Same-base collisions among targets get an occurrence suffix
(`set_reg`, `set_reg_2`) — rare, since most targets are labels named by a
unique `Label`; fully wire-order-independent disambiguation is a sequenced
follow-up (see `todo.md`'s canonical-decompile item). Hand-authored ids are
free-form as always — this describes only what the decompiler *emits*.

## Six real gaps closed during implementation

The grammar above as originally specified left things genuinely unaddressed
— not stylistic choices, but information the compiler needs to correctly
regenerate the real wire table. All six were found and closed while
building `desynced_toolkit.bsf` (see the implementation plan referenced in
"Status" below); documented here rather than left implicit in the code.

**Parameter direction.** The base grammar's `param := NAME` has no way to
express a parameter's direction at all, and — corrected mid-implementation,
user-confirmed rather than assumed — the obvious fix (surface the wire's own
`parameters[i]` truthy/falsy bit as `NAME`/`NAME*`) is the wrong model to
begin with. Passing a parameter into a sub-behavior is pass-by-reference: it
can be read-only, written-only, both, or left untouched, and `parameters[i]`
doesn't capture that — it's a UI hint for which side of a `call` node's box
the visual editor draws the pin on, not a distinction the runtime evaluation
itself makes. For a format meant for *editing and refactoring* behaviors,
trusting a stored bit that can silently go stale the moment someone adds or
removes a write during an edit is exactly the wrong call.

So direction isn't stored on the IR at all (`BsfParam` is just a name).
`argcache.written_param_slots` computes, fresh every time it's needed,
whether a parameter slot is ever used as an "out"-typed argument anywhere in
the behavior's own body — including *transitively*, by being passed into a
`call`/`load_behavior` node at a position the target sub-behavior itself
writes (resolved by fixpoint iteration, so it doesn't matter whether the
target is defined before or after the call site, and a `sub=-1` recursive
self-call resolves against its own in-progress result rather than
recursing forever). `render_text.py` uses this to decide the trailing `*`
purely for display; `compile.py` uses the same fresh computation to
regenerate `parameters[i]` — never the value a user did or didn't type in
the text. Concretely: if you edit BSF text to add a write to a
previously-read-only parameter but don't bother updating its `*`, the
compiled `.dcs` still gets the correct bit; the `*` itself is just telling
you what's already true, not asking you to keep it in sync by hand. A
parameter only ever passed through to an *external* (string saved-library
id) call's own parameter can't be resolved this way — genuinely unknowable
without that library's own definition, the same unresolvable-ness `call`'s
own arg *naming* already accepts in that case (see "`call`/sub-behaviors"
below).

**Hidden literal fields.** `behavior_format.md`'s "Hidden literal fields
(`make_asm`)" table (`call`'s `sub`, `domove`'s `c`, `notify`'s `txt`, the
universal `cmt` free-text node comment, etc.) — plain named keys on the
instruction table that are *not* part of `data.instructions[op].args` — had
no BSF surface syntax at all. Two of the six real fixtures this pipeline is
validated against (`hexat_test.dcs`, `HexIndexOf_test_1.dcs`) use `call`,
making this a required-for-round-tripping gap, not an edge case. Resolved by
treating a hidden field as an ordinary `ARGNAME=value` pair in the same arg
list, using the field's real lowercase name (`sub`, `c`, `txt`, `cmt`) as
`ARGNAME`, with a quoted-string literal form (`"..."`, backslash-escaped
internal quotes) added *only* for a hidden field's value — general `value`
syntax elsewhere in the grammar still has no string-literal form, since no
real in/out arg needs one. A rejected alternative, closer to this doc's own
one worked `call` example (`call(ScanRuins, $A, $B, framereg_result=$C)`,
which shows the resolved target name as a bare first token rather than
`sub=N`): resolving `call`'s target to a name this way requires the parser to
already know every sibling `sub` block's own name, which a single top-to-
bottom text pass doesn't have yet when it reaches the `call` site — deferred,
not implemented, in favor of the simpler and immediately-round-trippable
`sub=N`/`sub="external_id"` form.

**`keepvars`/`keeparrays`.** Two behavior-level (not per-node) wire fields —
`behavior_format.md`'s "Top-level envelope" documents both. `keepvars`
(bool): when set, this behavior's local-variable memory slots persist
across a restart instead of resetting. `keeparrays` (a *separate*,
3-state string field, corresponding to a *separate* in-game "Memory
Arrays" toggle independent of `keepvars`'s "Variables" one — see
`behavior_format.md` for the full 3-state meaning): controls whether
memory-array contents persist across a restart, a code change, or neither.
Both are genuine, first-order semantic differences (an accumulating
counter vs. one that resets to the same value every run) — not cosmetic or
structural, so both belong in the primary text representation, not only
the envelope layer. Both were missed completely in the original grammar
and, worse, in the original implementation: `decompile.py`/`compile.py`
(the table-level IR layer) handled `keepvars` correctly from the start,
but `render_text.py` silently never printed it and `parse_text.py`
silently never parsed it — so two real behaviors differing *only* in
`keepvars` decompiled to byte-for-byte identical BSF text, and a
decompile → render → parse → compile round trip through the text layer
silently dropped it. `keeparrays` was worse still: found completely
unhandled anywhere in this project at any layer, not just the text one —
first noticed and documented here at all as a side effect of chasing down
the `keepvars` gap. Found 2026-07-09 by a user-constructed test built for
exactly this purpose (two hand-authored behaviors, same
instructions/params/vars, differing only in `keepvars`, specifically to
check whether this pipeline could see the difference) — not caught earlier
because none of the six real fixtures this pipeline is validated against
happen to set either field, so no round-trip test exercised either path at
all. Fixed by adding both to the header production (see "Grammar
overview" above): optional `keepvars: true` / `keeparrays: "startup"` /
`keeparrays: "store"` lines directly under the `behavior`/`sub` header
line, symmetric with the existing `desc:` line, emitted only when set
(never `keepvars: false` — false is the overwhelming
common case and stays silent, matching how `desc` is only printed when
present). A new fixture exercising this (one of the two behaviors from the
finding above) was added to the round-trip test suite so this class of gap
can't reopen silently again.

**A bare bool at a non-exec value arg slot isn't a real value — it's
compiler-equivalent to that slot being absent.** Found 2026-07-09 via a
different real-world test: the user hand-rebuilt a small real behavior
(`tests/data/deprecated_haul_to_signal.dcs`, which uses the deprecated
`for_signal` instruction) in the current in-game editor, specifically to
compare how this pipeline sees the two versions. `have_item`'s optional
`Unit` arg is explicitly written as `false` on the wire rather than omitted
— the grammar's `value` production has no case for a bare bool at all, so
this fell into the `Unknown` escape hatch, which has no surface syntax and
silently broke a full text round trip. Confirmed against the real
compiler (`GetFactionBehaviorAsm`, `data/library.lua`): its arg-resolution
loop only recognizes `table`/`number`/`string` `val_type`s for a non-exec
arg — a `nil` (omitted) and a bare bool (`true` *or* `false`) both fall
through to the identical `else` branch ("unused argument"). So, unlike the
exec-arg case (where omission and explicit `false` are genuinely different,
see "Control edges" below), a bare bool at a value-typed slot is not a
distinct authorial state at all — `decompile.py` now treats it exactly like
an absent key, which is both correct (per the compiler equivalence just
cited) and round-trippable, at the cost of not literally reproducing
whichever of the two byte-identical-in-effect encodings the original wire
happened to use.

**The top-level `desc` field is real — the original 6 fixtures just never
set it.** `render_text.py`/`parse_text.py` already fully supported a
`desc:` line under the header from the very start (built directly off this
doc's own grammar, which always had one) — only `decompile.py`'s read of it
was ever missing, hardcoded to `None` with a comment claiming no real
fixture had it. `deprecated_haul_to_signal.dcs` has a real top-level `desc`
sibling to `name` and was the first fixture to prove that comment wrong.

**The real wire format's instruction key *is* the flat array position**
(`behavior_format.md`'s top-level envelope — a dict/array keyed by 1-based
Lua position, which is exactly what `next`/exec-arg branch targets number).
An earlier draft of this doc reused that same raw position as source-level
`INDEX`, which silently inherits the wire format's worst editing property:
inserting one node shifts every index after it, and every branch target that
pointed past the insertion has to be rewritten by hand. Confirmed as a real
problem, not a hypothetical one — this is the same "royal pain" a flat
numbered array is in *any* language, and this format doesn't need to import
that pain just because the wire format has it.

The fix is to decouple two things that were previously conflated:

- **Node ID** (`NODE_ID` in the grammar) — an arbitrary, stable, freely
  user-renameable symbol. Assigned once when a node is created (by the
  decompiler, e.g. `n` + a counter, or a friendlier op-derived name) and
  never reused or renumbered afterward. This is what every reference to a
  node uses: `branch_note` targets, a resolved `jump→label` annotation, a
  `call` target's node within its own sub-behavior graph.
- **Wire position** — purely a compiler-output detail, recomputed from
  scratch every time BSF is compiled back to the real instruction table.
  Never hand-maintained, never shown as something the user needs to keep
  consistent.

This generalizes something BSF already does in one narrow spot: `jump`/
`label` dispatch (see below) already resolves a *name* dynamically instead of
a fixed address. The proposal here is to make every node's identity work
that way, not just the ones reached via explicit `jump`/`label`.

This isn't a novel idea for this format specifically — it's the standard
answer wherever a stable identity needs to survive edits that a flat position
can't:

- **Assemblers/linkers** — symbolic labels resolved to addresses in a
  separate pass; inserting an instruction never requires renumbering source.
- **SSA-form compiler IRs** (LLVM IR, MLIR) — a value's name (`%7`) is
  independent of its print position; the printed number is a display
  convenience the printer recomputes, never the value's real identity.
- **Sea-of-Nodes IR** (Cliff Click, HotSpot C2) — control and data modeled
  as one explicit graph for the same reason BSF is graph-native: tree/CFG
  structure is a poor fit for real control flow.
- **DOT/Graphviz** — node names are arbitrary identifiers; position (`pos=`)
  is an optional attribute, not topology.
- **Node-graph visual tools** (Blender shader/geometry nodes, Unreal
  Blueprint/Material Editor, Houdini) — GUID-per-node, referenced by id
  everywhere, position stored as a separate sidecar attribute. The closest
  real-world analog to Desynced's own visual editor, and further validation
  for the position policy in "Document envelope" below.
- **Binary Ninja's IL layers** — explicitly separates an instruction's real
  address from its IL instruction index, for exactly this renumbering-pain
  reason, in a decompiler context directly analogous to this one.

One thing source order (top-to-bottom in the listing) still legitimately
controls: implicit-fallthrough adjacency (see "Control edges" below) is
genuinely about "the physically next instruction," and that's still a real,
load-bearing property. Reordering a node in BSF is just moving a line in the
text — same as reordering statements in any textual language — never a
renumbering exercise, because nothing else references a node by its line
position.

**A deliberate trade-off, worth being explicit about now that it's been
raised directly:** in real decoded data, an omitted `next`/exec-arg value is
always a genuine, specific wire (see "Control edges" above) — the visual
editor's compiler only omits it when there's a real connection to whatever
it placed next, never for "nothing was wired." BSF does not attempt to
preserve *which specific node* that original wire pointed to across a
reorder — an unannotated pin's target is recomputed fresh from whatever is
currently physically next, exactly like `jump`/`label` resolves a name
fresh rather than a fixed address, and exactly like moving a line in any
ordinary sequential-code language changes what runs after it. This was a
live design question (project memory, 2026-07-09) — the alternative (make
every real connection an explicit, stable node reference regardless of
whether it happens to compile compactly, so reordering can never silently
change what an unannotated line flows into) was considered and rejected:
it would touch decompile/compile/render behavior for comparatively little
practical benefit, since the same information is already lost the moment
BSF text is rendered (an adjacency-redundant connection renders with no
annotation either way) and re-parsed. Treating an unannotated pin as
"follows physical order, recomputed on every compile" is the simpler,
already-tested model, consistent with how every other unannotated
fallthrough in this format already works.

## Values

| Real value shape | Source syntax | Notes |
|---|---|---|
| number literal `{num=N}` | `N` | plain. A bare (unwrapped) int is never this shape — see the parameter/slot row below |
| coordinate `{coord={x=,y=}}` | `coord(x, y)` | never the `[x,y]` array form — see `behavior_format.md`'s corruption gotcha |
| id-typed literal `{id="x"}` (item/frame/component/tech/value id) | bare identifier, e.g. `c_behavior`, `v_enemy_faction`, `c_radar` | safe to leave bare — real ids never collide with variable-name convention (below). The syntax does not distinguish which *kind* of id it is (item/frame/component/tech/value) — that's inherent to the underlying value shape itself, not something this format could disambiguate further; look the id up in the real data files (or `instructions_index.md`) if its category matters |
| local variable (string arg value, e.g. `"A"`) | `$A` | `$`-prefixed to stay unambiguous against bare identifiers, even though real behaviors only ever seem to use short letter names |
| parameter (bare positive int) | the real name from `pnames[i]` when `i` is covered by this behavior's own declared `parameters`, else `param<i>` if `parameters` exists but is unnamed, else `slot<i>(undeclared)` | a bare positive int is **only ever** a mem-slot/parameter reference, never a plain number by itself (see the literal row above) — confirmed by direct comparison of the same real behavior copied two different ways (see project memory, 2026-07-06): a node-*selection* clipboard copy (Ctrl+A/Ctrl+C on nodes) can carry a bare int referencing a slot with no `parameters`/`pnames` table at all, while the identical value in a full Library-export copy of the same behavior is accompanied by `parameters={1:true}, pnames={1:"Result"}` and correctly resolves as a named parameter. The `slot<i>(undeclared)` fallback is a real, expected case for partial-selection fragments, not just a defensive placeholder — this is precisely the shape a "copy a portion of a behavior for review/editing" workflow will routinely produce |
| frame register (small negative int) | `@goto` / `@store` / `@visual` / `@signal` for -1/-2/-3/-4; bare `@N` for any other negative value | confirmed via `ui/Skin.lua:680`'s `data.frame_regs` array order (`{Goto, Store, Visual, Signal}`, 1-based) cross-anchored against `ui/FrameView.lua:2413`'s `i == -FRAMEREG_GOTO` comparison — not a guess. See https://wiki.desyncedgame.com/Registers for what each register does at the gameplay level (async goto/store/visual-icon/signal-emit) |
| faction (shared) register `{fr="name"}` | `fr(name)` | shared, named storage visible to every behavior the faction runs, and the backing store for Radio Transmitter/Receiver "band" links — resolved by *name* every access, never a fixed slot index (renaming/reordering a faction's registers doesn't break code that references them). See `behavior_format.md`'s "Faction (shared) registers" section for the full compile-time (`-99-n` synthetic address + `asm.fregs` name table) and runtime (`CallRadio`, name→index re-lookup) mechanism, including the write-guard when a register is currently link-driven by a Transmitter. The name must already exist for the target faction — unlike `$var`, `fr(name)` does not self-declare a new register |
| entity/other runtime-only value | not directly literal-izable; only appears as a register read, never a source literal | n/a |

### Composite values (`num` plus a coord/id/entity)

Per `behavior_format.md`'s composite-value semantics: `coord`/`entity`/`id`
are mutually exclusive on one value, but `num` is a **separate** field that
coexists with any of them (`{coord={x=,y=}, num=N}` is a real, valid, and
fairly common shape — e.g. a coordinate paired with an associated
distance/priority, or an item/component id paired with a quantity). This
format renders that pairing with a `[num=N]` suffix on the base value:

```
coord(-5, 6)[num=3]      -- coordinate (-5, 6) carrying num=3
c_radar[num=10]          -- component id c_radar carrying num=10
```

A bare `N` (no brackets) is reserved for a value that is *only* a number
(the common case: `{num=N}` alone, nothing else set). The bracket form is
only used when a coord/id/entity is present *and* carries a `num` alongside
it — the two are visually distinct on purpose, since they come from
different real value shapes and collapsing them would lose information.

## Control edges

Every exec-typed argument slot (the top-level `next`, plus per-instruction
slots like `check_number`'s "If Larger"/"If Smaller") independently resolves
to one of three cases, per `behavior_format.md`'s documented semantics:

1. **Explicit target** (a raw wire position in the decoded table, resolved to
   the destination node's `NODE_ID` — see "Node identity vs. wire position"
   above) → rendered as `>NODE_ID (PinName)`, using the instruction's real
   pin name from `data.instructions[op].args` (e.g. `If Larger`, `Done`) for
   a slot arg. The top-level field's own `PinName` is **not always the
   literal text "next"** — `data.instructions[op].exec_arg` (see
   `behavior_format.md`'s "The top-level `next` field's real display
   name/existence" section) names it for real when present (e.g.
   `check_number`'s "If Equal"), and its being explicitly `false` means the
   op has **no top-level pin at all** (`exit`/`restart`/`last`) — the real
   visual editor draws none, and BSF now doesn't either, rather than
   rendering an annotation for a pin that doesn't exist and explaining it
   away after the fact (an earlier draft did exactly that for `exit`,
   caught by direct user feedback). `argcache.next_pin_name(op)` is the
   single source of truth both `render_text.py`/`render_mermaid.py` (render
   side) and `parse_text.py` (mapping a parsed pin name back to the
   structural `branches["next"]` key) use for this — this is an intentional
   wire the original author made — always shown, when the pin exists.
2. **Explicit `false`** → rendered as `>POP (PinName)`. This is a real,
   meaningful authorial choice and must stay visually distinct from omission
   — collapsing this into "no annotation" was a real bug caught while
   building the corpus analysis tooling (see project memory) and must not be
   repeated here. Named `POP`, not "STOP" (an earlier, misleading name this
   section carried, corrected by direct user feedback) — a behavior never
   truly halts except via the explicit `exit` instruction or an
   outside/external action, and what actually happens here is one single
   mechanism, not three: **pop the current context frame** (the innermost
   active loop iteration or `call` invocation). Whatever remains on the
   frame stack after that pop decides what happens next on its own — an
   enclosing loop continues iterating, an enclosing `call` returns to its
   caller — and if popping empties the stack entirely, the engine
   automatically pushes a fresh frame and restarts from Program Start (the
   same effect as the `restart` instruction, itself distinct from `exit`;
   see `behavior_format.md`'s "Stopping a behavior" section). That's not a
   separate third case to remember, just the unremarkable consequence of
   popping with nothing left. This is deliberately **not** something BSF
   tooling tries to resolve or label further (see "Loop-type instructions"
   below) — `POP` is rendered exactly as the wire format has it and left at
   that.
3. **Omitted entirely (nil)** → falls through to the physically next
   instruction. **Not "no decision was made"** — in real decoded data this
   always represents a genuine wire the visual editor's own compiler chose
   not to spell out as an integer, since doing so would be redundant with
   position (user-confirmed real-editor behavior; see `behavior_format.md`'s
   "Branch and fall-through resolution" for the full detail, including that
   a truly unconnected pin always compiles to explicit `false`/`POP`,
   unconditionally, never to omission). How BSF text spells this case
   depends on how many exec pins the op has — see "Explicit-pin rule"
   directly below. Deliberately, fallthrough does **not** try to preserve
   "this specific wire went to this specific node" across a reorder the way
   an explicit target does; see "Node identity vs. wire position" below for
   that trade-off and why it's intentional.

### Explicit-pin rule and the `NEXT` token

For an op with **two or more** exec pins (declared exec args plus the
top-level pin when it exists — `check_number`'s three, every loop's
body+`Done` pair, `sequence`'s stages, `switch`'s cases), **every pin must be
written**, as one of `>NODE_ID (Pin)`, `>POP (Pin)`, or `>NEXT (Pin)` —
`NEXT` being the explicit spelling of plain fall-to-physically-next
(structurally identical to omission; the wire still gets the compact
omitted encoding where position allows). The renderer emits all of them and
the parser rejects a multi-pin op with any pin missing, naming the missing
pins.

Rationale (adopted 2026-07-14, from the agent-ergonomics review): with one
pin written and others omitted, the line *reads* complete — omitting a
loop's `Done` pin (which lands control on the loop's own body, an off-by-one
plus a silent infinite restart) was hit twice in real authoring before this
rule. It also surfaced pins omission had kept entirely invisible
(`switch`'s `Default`, `sequence`'s `First`). This is the same design
conclusion LLVM IR reached for machine-edited control flow (mandatory
explicit terminators); BSF keeps plain omission for single-pin ops only,
where there is nothing to forget and sequential code reads best bare.
`NEXT` is accepted (never required) on single-pin ops too. `POP` and `NEXT`
are reserved words and cannot be node ids.

### Comments, annotated rendering, and referring to nodes

`#` starts a comment: a full line, or trailing after a node's branch notes
(quote-aware — a `#` inside a quoted string is content). Comments are
non-structural: parse drops them, the default render never emits them
(keeping diffs stable), and `render_behavior(..., annotate=True)` (CLI:
`decompile --annotate`) uses them for human-correlation aids — each
instruction's **visual-editor display name** where it differs from the op
id (`set_reg` displays as "Copy", `check_number` as "Compare Number";
sourced live from `data.instructions[op].name` via
`ArgCache.display_name`), each **opaque id literal's in-game display name**
(`c_radar` is shown as "Long-Range Radar", `v_broken` as "Heavily Damaged";
scanned from the registrations' own `name` fields via
`ArgCache.id_display_names` — ids whose name is derivable by inspection,
like `v_resource` → "Resource", stay unannotated), plus a blank line before
each `label` section. `python -m desynced_toolkit.bsf ids <query>` looks the
mapping up in either direction (substring match on id or display name).

The reason annotate mode exists: node ids are decompiler-generated —
invisible in the game's own editor and unstable across re-exports — and
internal ids like `c_adv_miner` are exactly as invisible (the user sees
"Laser Mining Tool"), so a statement addressed to a *person* must never
anchor on either bare form. Anchor on what the editor shows: display names
(instruction and id), argument values, `cmt`, enclosing label section, plus
a short BSF excerpt for wiring discussions. (Node ids and internal ids
remain the right spelling *within* BSF text itself.)

### Validation (strict by design)

`parse_text.py` rejects, with line numbers and did-you-mean suggestions:
unknown op names, unknown argument names (checked against the op's live
`data.instructions` declaration), unknown exec pin names, duplicate node
ids / argument names / pins, branch targets naming no node, malformed or
duplicated header attributes (`desc`/`keepvars`/`keeparrays`, accepted in
any order), and **bare identifiers that are not registered game ids**
(checked against an accept-set scanned from the Data package's own include
files — so a forgotten `$` sigil or a misspelled `v_`/`c_`/item/frame/tech
id fails at parse time instead of silently compiling to a wrong id
literal). `compile.py` applies the same checks to hand-built IR
(`BsfCompileError`), including branch-pin keys that match no declared pin —
previously *silently dropped*, the worst failure mode the 2026-07-14 review
probe found. Blank lines between nodes are allowed and carry no structure
(a block ends only at the next `behavior`/`sub` header).

`lint.py` (also run automatically on every CLI `compile`, warnings to
stderr; standalone via `python -m desynced_toolkit.bsf lint`) flags the
legal-but-suspicious tier: nodes unreachable from Program Start or any
label (labels are never "unreachable" — computed `jump` dispatch can reach
them), a literal `jump` whose `(id, num)` matches no `label` in the same
behavior (falls back to Program Start at runtime — usually a typo, not an
intended restart), references to undeclared parameter slots (the
dangling-ref-via-copy-paste hazard, which resolves to empty/0 in-game with
no error), and **a declared node id that nothing references** (once ids
appear only on genuine targets — see "Optional node ids" — an unwired id is
anomalous; the Program Start entry node and `label` nodes are exempt, and a
plain human note belongs in `cmt`, not a dangling id).

## Dynamic `jump`/`label` dispatch

**`jump`'s target is not statically known in the general case, and that is
the normal, intended way to use it, not an edge case.** The real
`data.instructions.jump` implementation's own `explain` text says so
directly: *"Jumps can be dynamic and passed via parameter or variable"* —
and its `func` confirms this isn't just descriptive copy: it resolves the
`Label` argument's actual value at the moment the instruction executes,
linearly searches the current behavior's compiled instruction list for a
`label` whose own resolved value matches, and jumps there if found. **If no
`label` matches, the function returns without setting `state.counter` at
all, which means execution falls through via the instruction's ordinary
`next` field** — the same implicit/explicit `next` resolution every other
instruction gets (see "Control edges" above). This is a real, named idiom
(confirmed directly by a user of this project): jump to a value representing
a state; if no `label` for that state exists yet, `next` runs the
initialization code for it.

Both `jump` and `label`'s `Label` argument are ordinary `in`-typed values,
rendered with the normal value rules above — so a `Label=` can be a literal
(`v_transport_route`), a variable (`$State`), a parameter, or a frame
register, with no special grammar needed:

```
n12: jump(Label=$State)
n13: label(Label=$State)     -- unrelated code here does NOT get a resolved edge
```

**Static resolution at decompile time is only ever an opportunistic, best-effort
annotation** — attempted *only* when a `jump`'s `Label` value is a literal
(never for a variable/parameter/register value, where the actual target
depends on runtime program state this decompiler has no visibility into).
When it succeeds, both the raw value and the resolved edge are shown:

```
n12: jump(Label=v_transport_route)  >n3 (jump→label)
```

The raw `Label=` value is kept even when resolution succeeds (needed to
round-trip an edit — renaming a label means updating both the `jump` and
`label` instructions, which only makes sense if the actual value stays
visible and editable). When `Label` is dynamic, no `jump→label` annotation
is shown at all — but the instruction's separate `next` edge (the real
"no matching label" path) is still rendered normally, exactly as it would be
for any other instruction. A `jump` with a dynamic target and no explicit
`next` wired is not "unresolved" or missing information — it's fully and
correctly described by its raw `Label=` value plus its ordinary implicit
fallthrough, the same as everything else in this format.

**A literal `Label`'s `num` is a real, distinguishing part of the target,
not incidental.** Found via a real user behavior (Mining Leader V3.2, added
to `tests/data/` 2026-07-09): it reuses one label id (`v_broken`) with three
different `num` values (bare, `[num=1]`, `[num=10]`) as three genuinely
distinct jump destinations — a real, load-bearing idiom for getting more
distinct entry points than there are visual-editor label icons to choose
from. The static-resolution matching key must be `(id, num)` together;
keying on `id` alone (an early implementation bug, since fixed) silently
conflates every same-id-different-`num` label into one, resolving every
jump among them to whichever label happened to be inserted last.

## `call` / sub-behaviors

`call`'s argument list is not statically shaped by `data.instructions.call`
the way other instructions are — its arg count and per-slot in/out-ness
depend entirely on the *target* sub-behavior's own declared parameters (this
is a real, confirmed quirk — both real mods examined this project read
special-case `call` in their own instruction-arg-walking code for exactly
this reason, see project memory). `sub` resolves three ways, and each is
rendered differently based on what parameter-name information is actually
available:

| `sub` value | Meaning | Argument naming |
|---|---|---|
| positive int | 1-based index into this clipboard bundle's `dependencies[]`/`subs[]` | resolved against that bundled sub-behavior's own `pnames` — real names available, since the whole bundle is visible to the decompiler at once |
| `-1` | recursive self-call | resolved against the *current* behavior's own `pnames` |
| string | external saved-library id, not bundled in this clipboard string | **not resolvable** — no `pnames` visible to the decompiler at all; falls back to generic `arg1`, `arg2`, ... — this is an honest limitation, not a bug to fix later, since the data genuinely isn't present in the string being decompiled |

Rendered call syntax names the target directly instead of a raw numeric
index, e.g.:

```
n7: call(ScanRuins, $A, $B, framereg_result=$C)
```

where `ScanRuins` is the bundled sub-behavior's own declared name (from its
`name` field), not `sub=2`.

## Sub-behaviors (`dependencies[]` / `subs[]`)

Both field names have been observed for the same concept in real corpus
data — `dependencies` at a clipboard's top level for a standalone exported
behavior, `subs` for a behavior embedded inside a blueprint's `c_behavior`
component `extra_data` (see project memory, 2026-07-06). Whether this is a
live-editor-vs-wire-format distinction or a real inconsistency has **not
been reconciled** — treat both as the same concept (an array of embedded
sub-behavior tables) until checked directly. Each bundled sub-behavior
renders as its own `sub NAME(...):` block using its own real `name`, with its
own independent `NODE_ID` namespace (matching how the real table addresses
it as a separate array — a `call` target's node IDs are only ever meaningful
within that specific sub-behavior's own graph, never the caller's).

## Common idioms

Patterns confirmed independently in two real, unrelated user-authored
behaviors (`Mining Leader V4.0`'s `Async Radar` sub, `Fendersons Transport
V2.0`'s `Async Transit` sub — reviewed 2026-07-10/11) rather than designed
in the abstract — worth reaching for by name instead of re-deriving from
scratch each time a new behavior needs the same shape.

### Async progress/timeout via a shared `call` sub

**Problem:** a top-level jump/label state machine needs to keep doing
something over many ticks (move toward a target, poll a slow sensor) without
blocking the rest of the loop (enemy checks, health checks, etc. still need
to run every tick), and needs to give up after some bounded number of
attempts rather than getting stuck forever.

**Shape:** one `call`-based sub, parameterized by what the *caller* wants to
happen on each of up to three outcomes — conventionally named something like
`Finish Value` / `Continue Value` / `Timeout Value` — plus an in/out
`Progress count` parameter the caller owns and persists (typically the same
register the caller already uses for a different bookkeeping purpose
elsewhere, e.g. a search-retry counter). The sub:

1. Does one unit of real work (one step toward a target; one poll of a
   slow-changing resource).
2. Detects whether that step actually made progress (`Async Transit`:
   distance-to-target changed since last call, using the target's own `num`
   field as scratch storage to remember "last measured distance" across
   calls; `Async Radar`: a scheduled tick has elapsed).
3. If finished outright, returns `Finish Value`.
4. If no progress for some threshold number of calls (`Async Transit`: 100,
   "approx 20 seconds" per its own comment), resets `Progress count` and
   returns `Timeout Value` — letting the caller give up and try something
   else instead of waiting forever.
5. Otherwise increments `Progress count` and returns `Continue Value`.

The caller writes the sub's `Result` directly into its own dispatch register
(`@visual` in both real examples — the same register already driving the
top-level `jump(Label=@visual)` dispatch, so the sub's return value *is* the
next state, no separate translation step needed) and loops back through
`Begin` every tick regardless of outcome. **`Continue Value` is almost
always just a self-reference to the current state** (a bare self-reference, not
a distinct label) — both real examples use this trick, since "keep doing
what you were doing" doesn't need its own name.

**When it's *not* worth it:** if the two things being unified only differ in
which literal value gets passed to an already-shared sub call (e.g. two
3-node `label`/`call`/`jump` sections whose only difference is which
`Finish Value`/`Timeout Value` literal they pass), collapsing them into one
shared label via a memory-array "what to do next" stack usually isn't a net
win — the push/pop plumbing needed at every entry point can cost more
instructions than the small duplication it removes. This idiom pays off when
the *duplicated logic itself* is substantial (a whole search loop, not just
which two labels get passed into an already-existing sub call).

## Loop-type instructions (`for_number`, `for_component`, `for_signal_match`, etc.)

**Correction, caught by direct user feedback — an earlier draft of this
document treated loop instructions as needing "block-body extent detection,"
which was a misunderstanding.** They don't need any special handling at all
for their own pins: every `for_*` instruction (`for_number`, `for_component`
— "loop over equipped components" — `for_signal`/`for_signal_match`,
`for_entities_in_range`, `for_inventory_item`, and others) has exactly the
same shape as any other multi-pin instruction. Checking the real
`data.instructions.for_number` definition confirms this precisely: its only
*declared* `exec` arg is `Done` ("Finished loop") — the per-iteration path
is the instruction's ordinary top-level `next`, exactly like `check_number`'s
"Equal" case (see "Control edges" above). Both pins render with the existing
rules, no changes needed:

```
n4: for_component(Filter=c_radar, Component=$C)  >n9 (Done)
```

**The real subtlety is elsewhere: what an unconnected pin means once you're
inside a loop's iteration.** Per direct user correction: control reached via
the per-iteration pin is not a bounded "block" of instructions at all — it's
free to jump, branch, and call like anything else, and the loop is not
considered done, no matter where control flow wanders (even to a `label`
positioned before the loop), until either an explicit `break`/`last`
instruction runs, or control reaches a pin with nothing wired to it at all.
Checked against the real dispatcher to confirm precisely (`data/instructions.lua`'s
`InstBeginBlock`, `data/components.lua`'s `c_behavior_on_end`): a loop
instruction's `func` pushes onto a real runtime stack (`state.blocks`) when
it begins; a dead end — an explicit `false` on any pin, *or* falling off the
true end of the instruction array (both are handled identically, confirmed
by `interpreter.py`'s own fix for exactly this equivalence) — pops the
innermost still-active loop off that stack and re-invokes *its own* `next`
handler to decide whether to continue iterating or finish (call `last`, jump
to `Done`). Only once nothing remains on the block stack does a dead end
fall back to a `call`-return (if inside a called sub-behavior) or a genuine
top-level restart. **An omitted (not `false`) exec arg is unaffected by any
of this** — it's a normal implicit fallthrough to the physically next
instruction regardless of loop context, confirmed by the same dispatcher
code (only a real dead end triggers the block-stack check at all).

This means a `false` pin's real meaning (continue the innermost enclosing
loop / return to a caller / restart from Program Start — never a bare
"halt," see "Control edges" above) is a **path-dependent runtime property**
(which loops are active on `state.blocks`, which `call` frames are on
`state.returns`, at the moment that specific `false` executes) — not a fixed
property of the instruction's position in the flat array.

**⚠️ Authoring gotcha, hit twice for real (`MinerDrone`, then a throwaway
test behavior one turn after fixing it there — see
`reference_bsf_duplicate_arg_name_silent_clobber`-adjacent memory for the
pattern): never omit a loop's `Done` pin when hand-authoring BSF, even
though it "should" be safe to omit whenever the loop's per-iteration body
happens to sit physically right after it — which is the natural, common way
to write a loop, making this the *default* way to get it wrong, not an edge
case.** Omission always resolves to "physically next `NODE_ID` after this
instruction" — which is the body's own first instruction, never "after the
loop as a whole," regardless of how many instructions the body actually
spans. Landing there when the loop is genuinely `Done` means: (1) the body
executes one extra, spurious time with no valid per-iteration value, and
(2) whatever `POP`/dead-end already ends that body now fires with the
loop's own block frame *already popped* (that's what `Done` firing means),
so it falls through to the *next* enclosing scope instead — at top level,
a full Program Start restart, silently looping forever once per tick if the
behavior is locked (no `unlock()`), never tripping any instruction-count
safety cap since only unlocked mode has one. Always give `Done` its own
explicit target — even a bare `exit()` node that does nothing else — never
leave it to fall through into the body.

**Decided against building any static resolution of what a pop's
destination actually is, even as an optional nicety — not merely
deferred.** An earlier pass floated the reachability-heuristic idea sketched
above (follow every edge reachable from a loop's per-iteration pin, not
crossing an explicit `break`/`last` or a nested loop's own `Done`; any
`false` found there means "continue this loop"). On reflection this isn't
something to build: determining which outcome a dead end produces is
equivalent to the halting problem in general — whether/when a dead end is
even reached, for a graph with computed `jump`/`label` dispatch, is
undecidable — and the actual outcome can differ per input/per run even
where reachable in principle. A static label would be actively misleading
in exactly the non-obvious cases where it would matter most, and adds
nothing in the obvious cases (a reader who knows the plain rule above
already gets the right answer straight from the graph topology, the same
way a human author already relies on it when wiring a `POP` inside a loop
on purpose — see `blight_magnifier_mining.md`'s `MinerDrone` for a real
example of exactly this idiom). `POP` is rendered exactly as the wire
format has it — visually distinct from omission, nothing more, and never
labeled with a guess at where it leads — and left at that; this is the
complete, correct representation, not a partial one waiting on a "next
step." (This is about the *destination* specifically — whether a pop is
even visually present as an edge at all is a separate question, answered
differently for the text listing vs. the Mermaid diagram; see
"Visualization" below.)

## Document envelope (round-tripping beyond a bare behavior)

The grammar above covers one thing: a single behavior's instruction graph.
A real Desynced clipboard payload can be more than that — a unit/frame with
a `c_behavior` component, a full blueprint bundling several components,
register bandings (`bands`/`conns`, `behavior_format.md` ~line 223), embedded
sub-behaviors under `dependencies`/`subs`. Round-tripping *any* valid
encoding, not just a bare behavior, means adding a layer above this grammar
rather than growing the grammar itself to cover every possible container
shape:

- **Envelope layer.** A thin wrapper mirroring the real clipboard payload
  1:1 — type tag (`B`/`C`), frame/component data, register bandings,
  embedded sub-behaviors — sitting around the BSF instruction graph(s) it
  contains. Any field not currently being edited round-trips as an **opaque
  pass-through blob**, keyed by field name and not reinterpreted — this is
  what makes "round-trip any valid encoding" tractable without first
  modeling every field the game might ever put in a blueprint. Not yet
  designed in detail; a real next step, not started.
- **Node position (`nx`/`ny`).** Resolved: stored as a **sidecar table**
  (`node_id -> (x, y)`) attached to the envelope, not part of instruction
  syntax — the same move DOT/Graphviz makes with `pos=`, and every visual
  node-graph tool (Blender, Houdini, Unreal Blueprint) makes with view-state
  vs. graph topology. This only works because node identity is
  stable across edits (see "Node identity vs. wire position" above) — a
  positional index couldn't anchor a sidecar table this way, since it
  shifts on every insertion. Write-back policy: untouched nodes keep their
  stored position verbatim; newly-added nodes have no entry, and the editor
  auto-lays those out — same as it already does today for any node with no
  `nx`/`ny` at all.

## Visualization (secondary, generated on demand)

The same node/edge graph this format describes can be rendered as a Mermaid
flowchart for at-a-glance structural review (`desynced_toolkit.bsf.render_mermaid`,
see "Status" below) — useful when reviewing a behavior's overall shape
matters more than editing a specific instruction, or to sanity-check that an
edit didn't change the shape unexpectedly. This is not an alternate
source-of-truth format; it's generated from the same underlying graph as the
primary listing above, never edited directly, and carries no information the
listing doesn't already have.

Mermaid's flowchart syntax has no record/port concept the way Graphviz's
`record` shape does (a node with several named sub-points, wired
individually via `node:portname`) — every Mermaid node is one box, and every
edge attaches to the box as a whole. Showing "this node has N distinct
declared outputs" therefore means drawing N separate edges out of the node
rather than one node with N labeled ports, which changes what's worth
drawing compared to the text listing:

- **Every declared exec pin gets a real, labeled edge — wired or not.**
  Walked from the op's real `data.instructions[op].args` (the same source
  `ArgCache` uses elsewhere), not just whatever happens to be present in a
  node's own branch data, so a node's full pin set is always visible even
  when a pin isn't wired to anything. A node with an unwired pin and a node
  that doesn't have that pin at all must not look identical.
- **An unwired pin's edge goes to its own small, local marker node — never
  one shared terminal.** An earlier draft sent every `POP` edge in a
  diagram into a single shared node; on a real graph with several such
  pins this produced long edges converging from all over the diagram onto
  one point, pure noise unrelated to any real structure. A dedicated small
  marker per (node, pin) keeps each such edge short and local, with no
  implied relationship between otherwise-unrelated dead ends.
- **The marker never tries to say what the pop resolves to** — same
  restraint as the text listing (see "Loop-type instructions" above): it
  means "this pin pops," nothing more.
- **A plain implicit fallthrough still gets a real (unlabeled) edge to the
  physically-next node**, since a Mermaid layout has no "physically next
  line" for the reader to infer flow from the way the text form does — this
  is the one case genuinely unique to text, where adjacency itself carries
  the information Mermaid has no equivalent for.
- **Layout direction is `flowchart TD`, top-to-bottom — despite the real
  in-game editor always laying a behavior out left-to-right** (every node's
  input pins on its left edge, output/exec pins on its right — user-confirmed
  2026-07-10). This was tried the other way first: `flowchart LR`, matching
  the real editor exactly, rendered against a real 39-node behavior (see
  project memory for the artifact from that session) — and the result was
  worse for this medium. The real editor's own layout algorithm looks at
  each disconnected subgraph's bounding box and pushes every *other*
  subgraph's nodes outside it — genuine 2D collision avoidance Mermaid's
  automatic (dagre) layout has no equivalent of — so an LR render of
  anything with real branching sprawls very wide. A Mermaid diagram is
  normally read by scrolling a browser page, where vertical scrolling is
  comfortable and horizontal scrolling is not, unlike the real editor's own
  pan/zoom canvas. So TD is a deliberate mismatch with the source tool's own
  convention, not an oversight — right call for the real editor, wrong call
  for a diagram meant to be read top-to-bottom in a browser.
- **A synthetic "Program Start" node feeds the first instruction, in the
  primary component only** (see below for what "primary" means here). The
  real editor always draws this node, even though it's never part of the
  serialized wire data — there's nothing to encode, since "the first
  instruction" is implicit in wire position (see "Node identity vs. wire
  position" above). This part of the real editor's convention carries over
  regardless of overall direction.
- **One diagram per component, found by *forward reachability from Program
  Start*, not undirected connectivity — a real, built feature, not just a
  proposal.** First tried as plain undirected connected-components (any
  edge unions two nodes, direction ignored) — rendered against a real
  behavior (Mining Leader V3.2, project memory) and it came back as a
  *single* component covering the whole graph, uselessly. The reason: every
  one of its labeled state-machine sections (Search/Emergency/Travel-to-
  target/Monitor-mine) is reachable from the main loop only via an
  unresolvable *dynamic* `jump(Label=$State)`, but each one *also* has its
  own *static* `jump` back to the main loop's own label when it finishes —
  and undirected connectivity treats that return edge as sufficient to weld
  the entire graph into one piece. User-confirmed correction that fixed
  this: "physically next" (an implicit fallthrough) is an artifact of the
  *wire encoding* — a compact way of writing a real, deliberate edge — not
  a different or lesser kind of connection than an explicit target; the
  actual fix needed was computing reachability in the right *direction*,
  not changing how any individual edge is weighted. Forward reachability
  from `b.order[0]` (following every statically-known edge — ordinary
  branch targets, explicit or implicit-fallthrough, treated identically per
  the point just made) finds the true primary component; the first
  not-yet-visited node encountered continuing through `b.order` after that
  starts a new component, which — per user-confirmed design — will be a
  `label` unless it's genuinely dead code (nothing reaches it at all,
  static or dynamic), rendered as its own small, honest component rather
  than silently dropped.
- **An edge whose target lands outside the current component renders as a
  small local "external reference" marker** (e.g. `↗ n11`), never a broken
  edge into a node the diagram doesn't define and never a false merge back
  into the component it left. This is exactly how a section's own
  return-to-Begin jump is drawn once components stopped being merged by it.
- **`connect_resolved_jumps` (default `True`) is a real parameter, not a
  fixed internal choice** — user-requested 2026-07-10 alongside `direction`
  ("parameterize the render so different behaviors can be rendered slightly
  different depending on how they come out"). It controls whether a
  *resolved* `jump→label` edge pulls its target into the same component
  (default — e.g. Search merges with Emergency in Mining Leader V3.2, since
  Search's own enemy-detection path statically jumps straight into it) or
  is always treated as a component boundary regardless of resolvability
  (maximal splitting — one section per diagram, more external-reference
  markers where sections used to merge). `direction` similarly exposes the
  Mermaid flowchart direction (`"TD"`/`"LR"`/etc.) as a real parameter
  rather than hardcoding the TD-vs-LR call from above — which reads better
  can depend on the specific behavior's shape.

## Status

**As of 2026-07-08, this is a real, working, bidirectional pipeline, not just
a specification** — `desynced_toolkit.bsf` (`src/desynced_toolkit/bsf/`)
implements decode → decompile → BSF text (`decompile.py`/`render_text.py`),
BSF text → parse → compile → encode (`parse_text.py`/`compile.py`), and
Mermaid rendering (`render_mermaid.py`), built per the implementation plan at
the time (`/home/johnfb/.claude/plans/moonlit-nibbling-sonnet.md` in that
session's environment — reference kept here since the plan predates this
doc update). `scripts/render_examples.py`, the original one-way prototype
this replaces, is now a thin CLI wrapper over the real package.

Validated against all 9 real `.dcs` fixtures in `tests/data/`
(`observer.dcs`, `beacon.dcs`, `beacon2.dcs`, `formation-hold.dcs`,
`hexat_test.dcs`, `HexIndexOf_test_1.dcs`, covering embedded
`dependencies`/multi-level sub-behaviors, declared `parameters`/`pnames`
including output parameters, and real `call` usage; plus `keepvars_clear.dcs`/
`keepvars_keep.dcs`, added 2026-07-09, covering `keepvars`/`keeparrays` and a
declared parameter with no custom `pnames` entry at all; plus
`deprecated_haul_to_signal.dcs`, added 2026-07-09, covering a deprecated
instruction (`for_signal`), a bare-bool value arg, and a real top-level
`desc` field) two ways:
`tests/test_bsf_ir_roundtrip.py` (decode → decompile → compile, table-level
equality against the original, modulo the still-deferred `nx`/`ny`/`cmt`
sidecar fields) and `tests/test_bsf_text_roundtrip.py` (the same, with the
BSF text layer in between: decompile → render → parse → compile). A genuine
end-to-end exercise (`tests/test_bsf_end_to_end.py`) decodes `hexat_test.dcs`,
renders it to BSF text, makes a deliberate hand-edit exercising node
reordering specifically (not just a literal-value tweak), reparses,
recompiles, re-encodes, re-decodes, and confirms both that the edit's exact
intended effect landed and that nothing else changed — including that the
untouched embedded `HexAt` sub-behavior still executes correctly through the
real interpreter against closed-form reference math.

This closed every gap this section used to list here: the bare-value surface
syntax (`5`/`$A`/`x`, not `num:5`/`var:A`/`id:x`), the `fr(name)` bare
syntax, the node-ID/wire-position split (`decompile.py` assigns stable
`n<idx>` ids and never re-derives a branch target from a cached position —
see "Node identity vs. wire position" above), and the reverse (text → table)
direction, which didn't exist in any form before this.

**Six real grammar gaps were found and closed while implementing this, not
anticipated by the original spec text** — all are now part of the grammar
itself (see "Six real gaps closed during implementation" above, right after
the grammar block): parameter direction (a display-only `NAME*` computed
fresh from real usage — including transitively through `call` — never
trusted from the wire's own `parameters[i]` bit, which turned out to be a
UI-drawing hint rather than a runtime distinction), hidden `make_asm`
literal fields (`sub`/`c`/`txt`/`cmt` as ordinary `name=value` pairs, with a
quoted-string literal form added for their string-valued cases), and
`keepvars`/`keeparrays` (two independent behavior-level persistence flags,
found 2026-07-09 via a user-constructed test specifically designed to check
whether this pipeline could distinguish two behaviors differing only in
these fields — it initially couldn't: `render_text.py`/`parse_text.py` had
silently dropped `keepvars` despite the table layer handling it correctly,
and `keeparrays` was unhandled at every layer, found only as a side effect
of chasing the first gap down), a bare bool at a non-exec value arg slot
(compiler-equivalent to that slot being absent, not a distinct value —
found via `have_item`'s optional `Unit` arg in a real deprecated-instruction
behavior the user hand-rebuilt in the current editor specifically to
compare against this pipeline), and the top-level `desc` field (fully
supported by the text layer from the start, only `decompile.py`'s read of
it was ever missing — the original 6 fixtures just never set it). All are
required for round-tripping real behaviors, not optional polish — 2 of the
original 6 fixtures need the hidden-field syntax for their `call` nodes,
several need parameter direction for declared output parameters, and
`keepvars`/`keeparrays`/`desc` are exactly the kind of
behavior-changing-but-textually-invisible field a fixture-based test suite
structurally cannot catch unless a fixture happens to set them — true of
the original 6 (none do), which is exactly why these gaps went unnoticed
until the user hand-constructed real behaviors specifically to exercise
them; three such fixtures (`keepvars_clear.dcs`/`keepvars_keep.dcs`/
`deprecated_haul_to_signal.dcs`) are now permanently in the suite so this
class of gap can't reopen silently again.

**Still not done, deliberately or genuinely deferred (see "Deferred" in the
implementation plan referenced above for the fuller reasoning):**
- The envelope/sidecar layer — `nx`/`ny` node positions and full
  blueprint/component wrapping beyond a bare behavior plus its
  `dependencies`. The pipeline round-trips the instruction graph itself;
  position/comment-adjacent fields outside that (`nx`/`ny` specifically —
  `cmt` *is* handled, via the hidden-field mechanism above) aren't modeled
  yet.
- **Not planned at all, not merely postponed:** any static resolution of
  what a `POP`/dead-end's destination actually is inside a loop's dynamic
  scope (see "Loop-type instructions" above for why — genuinely undecidable
  in general, and actively misleading in exactly the cases where it would
  matter). This is separate from whether a pop is visually present at all,
  which the pipeline does handle — see "Visualization" above.
- Rewriting `hex_expansion_math.md`'s revised `HexIndexOf` directly in BSF —
  explicitly separate, later follow-on work, not part of the pipeline build
  above. (The `AstCompiler` whose role this migration retires was removed
  from the tree 2026-07-14; the revised design's last executable form lives
  in git history — see `todo.md`'s Hex Expansion item.)
