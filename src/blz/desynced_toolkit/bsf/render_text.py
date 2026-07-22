"""BsfBehavior -> BSF text, per docs/behavior_source_format.md's grammar (the "Values"/"Control
edges"/"Dynamic jump/label dispatch" sections in particular). No text is stored anywhere in the
IR -- everything here is a pure function of the graph, recomputed fresh every render, including
the jump->label display annotation (see `_jump_label_targets`: caching this anywhere would risk
a stale annotation surviving a label rename or a `Label=` edit)."""

from __future__ import annotations

from .argcache import ArgCache, written_param_slots
from .ir import BsfBehavior, BsfNode, BsfParam
from .values import BsfValue, Coord, Fr, FrameReg, IdLit, Num, Param, Unknown, Var

_SYMBOLIC_FRAME_REGS = {-1: "goto", -2: "store", -3: "visual", -4: "signal"}

# Characters that would corrupt the surrounding grammar if they landed unescaped in a bare
# (unquoted) token -- parens/brackets throw off `_find_close_paren`/`_split_top_level`'s nesting
# count, a comma would split the arg early, a quote would confuse `_mask_quotes`, and a raw
# newline breaks parse_text.py's one-node-per-physical-line assumption outright. Found real,
# in-the-wild-achievable cases of all of these 2026-07-10: a variable renamed to contain literal
# BSF syntax (`A)  >POP (next)`) and a multi-line `cmt` -- both silently corrupted or crashed the
# round trip before this fix (see reference_dangling_param_ref_copy_paste.md's sibling finding for
# the sequence of events).
_UNSAFE_VAR_CHARS = set('(),[]"#\\\n\r')  # '#' added with comment support: an unquoted
# variable name containing '#' would be truncated by parse_text's trailing-comment strip


def _fmt_num(n: int | float) -> str:
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    return str(n)


def _escape_string(s: str) -> str:
    """Used for both hidden free-text values (`cmt`/`sub`/`c`/`txt`) and a quoted variable name --
    same escape scheme, same quoted-string surface form. Order matters: backslash must be escaped
    before anything that itself introduces a backslash (the quote/newline/CR escapes below), or
    those would be double-escaped on the next pass. `\\r`/`\\n` are kept as distinct escapes
    (not folded into one) so a real `\r\n` round-trips byte-exact -- an earlier version collapsed
    both to `\\n`, which round-tripped fine structurally but silently changed a comment's actual
    line-ending bytes, caught by the fixture round-trip test comparing compiled wire tables."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def _var_needs_quoting(name: str) -> bool:
    if not name or name != name.strip():
        return True
    return any(ch in _UNSAFE_VAR_CHARS for ch in name)


def _param_name(slot: int, params: list[BsfParam]) -> str:
    """3-tier resolution per docs/behavior_source_format.md's Values table: a real pname when the
    slot is covered by `params`, else the generic `param<i>`/`slot<i>(undeclared)` fallback --
    `BsfParam.name` already carries the resolved pname-or-param<i> distinction from decompile
    time, so only the "not covered at all" case needs handling here."""
    if 1 <= slot <= len(params):
        return params[slot - 1].name
    return f"slot{slot}(undeclared)"


def render_value(v: BsfValue, params: list[BsfParam]) -> str:
    if isinstance(v, Num):
        return _fmt_num(v.n)
    if isinstance(v, Coord):
        base = f"coord({_fmt_num(v.x)}, {_fmt_num(v.y)})"
        return f"{base}[num={_fmt_num(v.num)}]" if v.num is not None else base
    if isinstance(v, IdLit):
        return f"{v.id}[num={_fmt_num(v.num)}]" if v.num is not None else v.id
    if isinstance(v, Var):
        if _var_needs_quoting(v.name):
            return '$"' + _escape_string(v.name) + '"'
        return f"${v.name}"
    if isinstance(v, Param):
        return _param_name(v.slot, params)
    if isinstance(v, FrameReg):
        sym = _SYMBOLIC_FRAME_REGS.get(v.slot)
        return f"@{sym}" if sym else f"@{-v.slot}"
    if isinstance(v, Fr):
        base = f"fr({v.name})"
        return f"{base}[num={_fmt_num(v.num)}]" if v.num is not None else base
    if isinstance(v, Unknown):
        # Rare escape hatch (docs/behavior_source_format.md doesn't define surface syntax for a
        # value shape it doesn't enumerate) -- not guaranteed round-trippable through
        # parse_text.py. None of the 6 real fixtures this pipeline is validated against hit
        # this path; flagged here rather than silently producing unparseable-looking output.
        return f"!unknown({v.raw!r})"
    raise TypeError(f"unrecognized BsfValue: {v!r}")


def _literal_key(v: BsfValue):
    # IdLit's `num` is a real, distinguishing part of the value here, not incidental -- a real
    # user behavior (Mining Leader V3.2) uses one label id (`v_broken`) with different `num`
    # suffixes (bare, `[num=1]`, `[num=10]`) as three genuinely different jump targets, reusing
    # one visual-editor label icon as a "family" of sub-entry-points. Keying on `v.id` alone
    # (found 2026-07-09) silently conflated all three into one dict entry, so every jump to any
    # of them resolved to whichever label happened to be inserted last.
    if isinstance(v, IdLit):
        return ("id", v.id, v.num)
    if isinstance(v, Num):
        return ("num", v.n)
    return None


def referenced_node_ids(nodes: dict[str, BsfNode]) -> set[str]:
    """Every node id that some *other* edge actually points at: a real branch/pin target
    (`>id (Pin)`), or the resolved destination of a static `jump->label`. This is the set that
    earns a surface-visible id under docs/behavior_source_format.md's "optional node ids" rule -- a
    node reached only by positional fallthrough is not in it. Shared by `decompile.py` (to set
    each node's `id_explicit`) and `lint.py` (to flag a declared id nothing references). A `"POP"`
    branch value and a `None` fallthrough are not references; a dangling target (not in `nodes`)
    is ignored here (parse/compile reject it elsewhere)."""
    refs: set[str] = set()
    for node in nodes.values():
        for target in node.branches.values():
            if isinstance(target, str) and target != "POP" and target in nodes:
                refs.add(target)
    refs.update(_jump_label_targets(nodes).values())
    return refs


def _jump_label_targets(nodes: dict[str, BsfNode]) -> dict[str, str]:
    """Best-effort static jump->label resolution, scoped to one behavior/sub's own node set
    (never across a sub-behavior boundary). Only attempted when a `jump`'s `Label` arg is a
    literal (IdLit/Num) -- a variable/parameter/register Label genuinely can't be resolved
    without running the program, per the spec."""
    label_defs: dict[tuple, str] = {}
    for node in nodes.values():
        if node.op == "label" and "Label" in node.args:
            key = _literal_key(node.args["Label"])
            if key is not None:
                label_defs[key] = node.id
    targets = {}
    for node in nodes.values():
        if node.op == "jump" and "Label" in node.args:
            key = _literal_key(node.args["Label"])
            if key is not None and key in label_defs:
                targets[node.id] = label_defs[key]
    return targets


def render_hidden_value(v: object) -> str:
    """docs/behavior_source_format.md's grammar has no surface syntax at all for make_asm's "hidden
    literal fields" (call's `sub`, domove's `c`, notify's `txt`, the universal `cmt`) -- a real
    gap, not a deliberate omission (see decompile.py's HIDDEN_FIELD_TABLE; 2 of the 6 real
    fixtures this pipeline is validated against need `call`'s `sub` to round-trip at all).
    Minimal, explicitly-flagged extension used here: render hidden fields as ordinary
    lowercase-named `name=value` pairs in the same arg list, with a quoted-string literal form
    (undefined elsewhere in the grammar) for string values (`sub`'s external-library-id case,
    `txt`/`cmt`'s free text) -- simpler and more robust than trying to resolve `call`'s target
    to a bare name token (the one illustrative example in the spec does this, but that requires
    cross-referencing sibling `sub` blocks not yet parsed at that point in a single top-to-bottom
    pass; deferred, noted for reconsideration).

    The quoted string escapes backslash/quote/newline (`_escape_string`) -- a first version of
    this only escaped `"`, so a `cmt` containing a literal newline (an ordinary thing to type in
    the real editor's comment box) split one node across multiple physical lines and crashed
    parse_text.py outright (`SyntaxError: unmatched '('`), found 2026-07-10 building a
    deliberately adversarial test fixture."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return _fmt_num(v)
    if isinstance(v, str):
        return '"' + _escape_string(v) + '"'
    return repr(v)


def _render_cmt_block(s: str) -> str:
    """A node comment as a triple-quoted block (docs/behavior_source_format.md's cmt decision,
    2026-07-20): rendered under the node's branch notes to mirror the in-game "comment under the
    node". A body with newlines gets the expanded `\"\"\"\\n...\\n\"\"\"` form (content flush-left,
    since indentation inside the quotes would be part of the comment); a single-line body stays
    compact. The parser strips exactly one leading and one trailing newline, so both forms
    round-trip the exact string."""
    if "\n" in s:
        return f'cmt="""\n{s}\n"""'
    return f'cmt="""{s}"""'


def render_node(
    node: BsfNode, params: list[BsfParam], jump_targets: dict[str, str], argcache: ArgCache
) -> str:
    # A `cmt` renders as a triple-quoted block under the node UNLESS its body contains `"""`
    # itself (unrepresentable in that form) or isn't a plain string -- those fall back to the
    # inline, single-quoted, escaped `cmt="..."` arg form, which still round-trips.
    cmt_val = node.hidden.get("cmt")
    cmt_as_block = isinstance(cmt_val, str) and '"""' not in cmt_val

    parts = [f"{name}={render_value(v, params)}" for name, v in node.args.items()]
    for name, v in node.hidden.items():
        if name == "cmt" and cmt_as_block:
            continue  # emitted as a block after the branch notes below
        parts.append(f"{name}={render_hidden_value(v)}")
    args_str = ", ".join(parts)
    notes = []
    # data.instructions[op].exec_arg (via next_pin_name): `false` means this op has no
    # top-level "next" pin at all (exit/restart/last -- the real editor draws no pin, wiring
    # anything after them is nonsensical), a table names it for real (check_number's "If
    # Equal"), absent keeps the generic "next". Never guessed -- read straight from live data.
    next_pin = argcache.next_pin_name(node.op)
    all_pins = argcache.exec_pin_names(node.op)
    if len(all_pins) >= 2:
        # An op with 2+ exec pins renders EVERY pin, with the explicit `NEXT` token for
        # plain fall-to-physically-next -- the "one visible pin looked complete, so the
        # second one got forgotten" mistake (a loop's Done pin, twice; see
        # feedback_bsf_loop_done_pin_omission) is only preventable if absence is never
        # legal for these ops. Single-pin ops keep compact omission: with one pin there is
        # nothing to forget, and the text stays close to how sequential code reads.
        for pin in all_pins:
            key = "next" if pin == next_pin else pin
            target = node.branches.get(key)
            notes.append(f">{'NEXT' if target is None else target} ({pin})")
    else:
        for pin, target in node.branches.items():
            if target is None:
                continue
            display_pin: str | None = pin
            if pin == "next":
                display_pin = next_pin
                if display_pin is None:
                    continue
            notes.append(f">{target} ({display_pin})")
    if node.id in jump_targets:
        notes.append(f">{jump_targets[node.id]} (jump→label)")
    # The `id:` prefix appears only for a node something actually references (id_explicit) --
    # every branch/jump target has it set, so a `>id (Pin)` note above always names a node whose
    # own line still shows that id. A fallthrough-only node renders bare, as just `op(args)`.
    line = f"{node.op}({args_str})"
    if notes:
        line += "  " + " ".join(notes)
    if cmt_as_block:
        assert isinstance(cmt_val, str)  # implied by cmt_as_block, restated for mypy narrowing
        # A node with a cmt block spans lines, so it must be `;`-terminated (the parser scans for
        # `;`, keeping indentation/blank lines non-semantic).
        line += "\n  " + _render_cmt_block(cmt_val) + ";"
    if node.id_explicit:
        # The id sits on its own line ABOVE the instruction (docs/behavior_source_format.md's
        # id-on-own-line, user 2026-07-20), so every instruction body starts at column 0 and
        # they line up regardless of how long the ids are. A referenced/target node reads like an
        # assembly label -- `>engage_target (If Larger)` jumps to the `engage_target:` line.
        line = f"{node.id}:\n{line}"
    return line


def _naive_pretty_op(op: str) -> str:
    return op.replace("_", " ").title()


def _id_name_obvious(id_: str, name: str) -> bool:
    """Whether the display name is derivable from the id by inspection (`v_resource` ->
    "Resource", `v_arrow_up` -> "Arrow Up") -- those need no annotation; `c_radar` ->
    "Long-Range Radar" or `v_enemy_faction` -> "Enemy" do."""
    base = id_
    for prefix in ("v_", "c_", "f_", "t_"):
        if base.startswith(prefix):
            base = base[len(prefix) :]
            break
    return base.replace("_", " ").lower() == name.lower()


def _annotation_parts(node: BsfNode, argcache: ArgCache) -> list[str]:
    parts = []
    display = argcache.display_name(node.op)
    if display and display != _naive_pretty_op(node.op):
        parts.append(display)
    seen = set()
    for v in node.args.values():
        if isinstance(v, IdLit) and v.id not in seen:
            seen.add(v.id)
            name = argcache.id_display_name(v.id)
            if name and not _id_name_obvious(v.id, name):
                parts.append(f'{v.id}="{name}"')
    return parts


def _render_into(
    b: BsfBehavior, lines: list[str], keyword: str, argcache: ArgCache, annotate: bool = False
) -> None:
    # docs/behavior_source_format.md's `param := NAME` has no room for a parameter's direction at
    # all -- another real gap (see render_hidden_value's docstring for the sibling one).
    # Minimal extension: a trailing `*` marks a parameter written to somewhere in this
    # behavior's own body. Computed fresh here from `written_param_slots`, never stored on
    # `BsfParam` -- the wire's own `parameters[i]` bit turned out to be a UI-drawing hint (which
    # side of a `call` node a pin is drawn on), not a runtime distinction, so trusting a stored
    # bit here would let it silently go stale after an edit that adds or removes a write.
    written = written_param_slots(b, argcache)
    params_str = ", ".join(
        p.name + ("*" if (i + 1) in written else "") for i, p in enumerate(b.params)
    )
    lines.append(f"{keyword} {b.name}({params_str}):")
    if b.desc:
        # Escaped like every other quoted string in the grammar -- an earlier version
        # interpolated raw, and a desc containing a `"` only round-tripped because the parse
        # regex happens to be greedy; a desc containing a newline broke the line structure.
        lines.append(f'  desc: "{_escape_string(b.desc)}"')
    if b.keepvars:
        lines.append("  keepvars: true")
    if b.keeparrays:
        lines.append(f'  keeparrays: "{b.keeparrays}"')
    lines.append("")
    jump_targets = _jump_label_targets(b.nodes)
    for node_id in b.order:
        node = b.nodes[node_id]
        if annotate and node.op == "label" and lines[-1] != "":
            # visual sectioning: label nodes head the behavior's own named sections
            lines.append("")
        line = render_node(node, b.params, jump_targets, argcache)
        if annotate:
            # The in-game vocabulary for everything on this line that isn't derivable by
            # inspection: the instruction's visual display name (`set_reg` is displayed as
            # "Copy"; `set_number` as "Set Number" needs no note) and each opaque id
            # literal's display name (`c_radar` is shown as "Long-Range Radar";
            # `v_resource` as "Resource" needs no note). Comments are dropped by parse, so
            # annotated output still round-trips.
            parts = _annotation_parts(node, argcache)
            if parts:
                ann = f"  # {' · '.join(parts)}"
                # keep the annotation on the op line -- which is the 2nd physical line when the id
                # sits on its own line above it, the 1st otherwise -- ahead of any cmt block
                phys = line.split("\n")
                op_idx = 1 if node.id_explicit else 0
                phys[op_idx] += ann
                line = "\n".join(phys)
        lines.append(line)
    for sub in b.subs:
        lines.append("")
        _render_into(sub, lines, keyword="sub", argcache=argcache, annotate=annotate)


def render_behavior(b: BsfBehavior, argcache: ArgCache, annotate: bool = False) -> str:
    """`annotate=True` adds non-structural `#` comments for human correlation with the in-game
    editor: each instruction's visual display name where it differs from the op id, and a blank
    line before each label section. Annotated output parses identically (comments are
    stripped); the default render never emits comments, keeping diffs stable."""
    lines: list[str] = []
    _render_into(b, lines, keyword="behavior", argcache=argcache, annotate=annotate)
    return "\n".join(lines)
