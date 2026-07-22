"""The BSF intermediate representation: a behavior as a graph of nodes with named args and
explicit/implicit branch targets, matching docs/behavior_source_format.md's grammar directly (not a
pre-rendered string, and not tied to wire positions -- see "Node identity vs. wire position" in
the spec for why). `bsf/decompile.py` builds this from a real Lua table, `bsf/compile.py` turns
it back into one; `bsf/render_text.py`/`bsf/render_mermaid.py` render it; `bsf/parse_text.py`
builds it from BSF text."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .values import BsfValue

# A branch target: an explicit destination node id, "POP" (explicit `false` or falling off the
# true end of the instruction array -- both are real dead ends, rendered identically per the
# spec), or None for a plain implicit fallthrough (no branch_note at all in BSF text). "POP" is
# named for what it actually does -- pop the current context frame (the innermost active loop
# iteration or call invocation), never a bare "halt" -- not "STOP", which this format used
# until user correction: the auto-restart-from-Program-Start behavior when the frame stack is
# completely empty isn't a separate third case either, just the unremarkable, automatic
# consequence of popping with nothing left. Recomputed fresh at compile time from the current
# `order` every time -- never cached/baked in anywhere.
Branch = str | Literal["POP"] | None


@dataclass
class BsfParam:
    name: str  # pnames[i] if present, else "param{i}"
    # No direction field here on purpose. The wire format's own `parameters[i]` bit turned out
    # (user-confirmed, not guessed) to be a UI-drawing hint for the visual editor -- which side
    # of a `call` node's box a pin is drawn on -- not a distinction the runtime evaluation
    # itself makes. For a format meant for editing and refactoring, trusting a stored bit that
    # can silently go stale after an edit is the wrong call; `argcache.written_param_slots`
    # computes "is this slot ever written to" fresh from the actual node bodies every time it's
    # needed (render_text.py's `*` display, compile.py's regenerated `parameters[i]`), the same
    # never-cache policy already used for the `jump->label` annotation.


@dataclass
class BsfNode:
    id: str
    op: str
    args: dict[str, BsfValue] = field(default_factory=dict)
    # make_asm "hidden literal" fields (docs/behavior_format.md's "Hidden literal fields" table:
    # call/sub, domove/c, notify/txt, dodrop/c, etc.) -- plain named keys on the instruction
    # table that are NOT part of data.instructions[op].args, so ArgCache never sees them. Raw
    # values (already resolved for `call`'s `sub`, see decompile.py), re-emitted verbatim.
    hidden: dict[str, object] = field(default_factory=dict)
    # Keyed by the real exec pin name (e.g. "If Larger"), or "next" for the top-level field.
    branches: dict[str, Branch] = field(default_factory=dict)
    # Whether this node's `id` is a surface-visible, meaningful name -- render_text.py emits the
    # `id:` prefix only when this is True, so a node reached solely by positional fallthrough (no
    # pin/jump targets it) carries no id in the text at all (docs/behavior_source_format.md's "optional
    # node ids", user 2026-07-18/20: stops references anchoring on an unstable id, and cuts diff
    # churn). Every node still has an internal `id` for graph edges regardless -- this only
    # governs the text surface. Default True so hand-built IR (tests, tooling) keeps showing its
    # chosen ids; `decompile.py` sets it to "is this node an actual branch/jump target", and
    # `parse_text.py` sets it True for an author-written id, False for a synthesized one on an
    # id-less line. `lint.py` warns on a True-but-never-referenced id (a declared-yet-unwired
    # anchor -- use `cmt` for a human note instead).
    id_explicit: bool = True


@dataclass
class BsfBehavior:
    name: str
    params: list[BsfParam] = field(default_factory=list)
    desc: str | None = None
    keepvars: bool = False
    # Sibling wire field to `keepvars`, and just as semantic (not cosmetic) -- confirmed against
    # `ui/Program.lua`'s own options popup, which presents these as two independent toggles
    # ("Variables" and "Memory Arrays"). Unlike `keepvars` (plain bool), this is a 3-state
    # string: `None` (default, wire-absent) = "Clear memory arrays when behavior restarts";
    # `"startup"` = "Clear memory arrays on startup (including code change)" -- i.e. arrays DO
    # persist across an ordinary restart, they only clear on a fresh game load/code edit, the
    # opposite of what the string alone suggests; `"store"` = "Keep memory arrays until behavior
    # is switched" (survives even a restart or code change, only clears when a different
    # behavior/library is loaded in). No other string values are ever written by the real editor.
    keeparrays: str | None = None
    nodes: dict[str, BsfNode] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)  # node ids in source/emission order
    subs: list["BsfBehavior"] = field(default_factory=list)
