"""BSF text -> BsfBehavior. The reverse of bsf/render_text.py -- the genuinely new direction
(docs/behavior_source_format.md's own "Status" section: this never existed before this pipeline).

Needs `ArgCache` (unlike an earlier draft, which needed none at all): resolving a branch note's
top-level "next" pin back to the structural `branches["next"]` key requires knowing the op's
real per-op display name for it (`argcache.next_pin_name` -- e.g. `check_number`'s "If Equal"
is not literally the text "next"). Everything else here still only builds the abstract,
named-arg IR: knowing an op's real argument *positions* remains exclusively a bsf/compile.py
concern.

Five real gaps in the base grammar (docs/behavior_source_format.md as written) are worth flagging
up front, all resolved with minimal, explicitly-flagged extensions rather than blocking on a
grammar change -- see render_text.py's `render_hidden_value`/`render_node`/`_render_into`
docstrings for the symmetric render-side notes:
  - parameter direction has no surface syntax at all -- resolved with a trailing `*` marking a
    parameter written to somewhere in the behavior's own body (computed fresh from usage by
    `argcache.written_param_slots` whenever it's needed, not stored anywhere -- the wire
    format's own `parameters[i]` bit is a UI-drawing hint, not a runtime distinction, user-
    confirmed). This module only ever strips a trailing `*` on read, never stores or trusts it.
  - make_asm "hidden literal fields" (call's `sub`, domove's `c`, notify's `txt`, the universal
    `cmt`) have no surface syntax at all -- resolved by treating them as ordinary lowercase-
    named `name=value` pairs in the same arg list, with a quoted-string literal form (also not
    in the base grammar) for their string-valued cases.
  - the top-level "next" pin's real per-op name/existence (`data.instructions[op].exec_arg`,
    e.g. `check_number`'s "If Equal", or no pin at all for `exit`/`restart`/`last`) isn't
    something the grammar's generic `(next)` fallback captures -- resolved by resolving a
    parsed pin name against `argcache.next_pin_name(op)` and mapping it back to the structural
    "next" key when it matches, rather than storing it under the display name verbatim.
  - `keepvars` (a behavior-level wire field controlling whether local-variable memory persists
    across runs -- a genuine, first-order semantic difference, not cosmetic) had no surface
    syntax at all and was silently dropped by both this module and render_text.py until a
    real user-constructed test (two behaviors, identical instructions/params/vars, differing
    only in this field) caught it 2026-07-09 -- none of the six real fixtures this pipeline is
    validated against happen to set it, so no prior round-trip test exercised the path.
    Resolved with an optional `keepvars: true` line directly under the header, symmetric with
    `desc:`, parsed only for the literal `true` case since `false` is never rendered.
  - `keeparrays` is a sibling wire field to `keepvars` (same missed-until-the-same-fix history)
    controlling a *separate* real editor toggle ("Memory Arrays", independent of the
    "Variables" one `keepvars` covers -- confirmed against `ui/Program.lua`'s options popup, two
    distinct dropdowns). Unlike `keepvars` it's a 3-state string, not a bool: absent (default),
    `"startup"`, or `"store"` -- see `ir.py`'s `BsfBehavior.keeparrays` docstring for what each
    means in-game. Resolved the same way, one more optional `keeparrays: "startup"` /
    `keeparrays: "store"` line under the header.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from .argcache import DYNAMIC_ARG_OPS, ArgCache
from .decompile import HIDDEN_FIELD_TABLE
from .ir import BsfBehavior, BsfNode, BsfParam
from .values import BsfValue, Coord, Fr, FrameReg, IdLit, Num, Param, Var


class BsfParseError(SyntaxError):
    """Parse/validation failure with the 1-based source line attached. Every rejection carries
    enough context to fix the text without reading this module: the offending line, what was
    expected, and a did-you-mean suggestion where a candidate set exists. Validation here is
    deliberately strict -- every case it rejects was demonstrated (2026-07-14 probe, see
    tests/test_bsf_validation.py) to otherwise compile *silently* into a wrong behavior:
    a typo'd exec pin silently unwired, a forgotten `$` sigil silently became an id literal,
    a duplicate node id / arg name silently clobbered its predecessor."""

    def __init__(self, msg: str, line_no: int | None = None, line: str | None = None):
        loc = f"line {line_no}: " if line_no is not None else ""
        tail = f"\n    {line.strip()}" if line else ""
        super().__init__(f"{loc}{msg}{tail}")
        self.line_no = line_no


def _suggest(name: str, candidates) -> str:
    matches = difflib.get_close_matches(name, list(candidates), n=1)
    return f" -- did you mean {matches[0]!r}?" if matches else ""


_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
_SLOT_UNDECLARED_RE = re.compile(r"^slot(\d+)\(undeclared\)$")
_HEADER_RE = re.compile(r"^(behavior|sub)\s+(.+)\((.*)\):\s*$")
# A sub-behavior stored in its own file instead of embedded inline (bsf/library.py's by-name
# library store) -- no parens/params/colon, just a name and a quoted relative path. Deliberately
# a distinct line shape from `_HEADER_RE` (no trailing `:`) so a plain text scan can tell inline
# from referenced without attempting a header parse first.
_SUB_REF_RE = re.compile(r'^sub\s+(.+?)\s+from\s+"(.*)"\s*$')
_DESC_RE = re.compile(r'^desc:\s*"(.*)"\s*$')
_KEEPVARS_RE = re.compile(r"^keepvars:\s*true\s*$")
_KEEPARRAYS_RE = re.compile(r'^keeparrays:\s*"(startup|store)"\s*$')
_BRANCH_RE = re.compile(r">\s*(\S+?)\s*\(([^)]*)\)")
_SYMBOLIC_FRAME_REGS = {"goto": -1, "store": -2, "visual": -3, "signal": -4}
# Prefix for the internal id synthesized for an id-less (fallthrough-only) node line. Reserved:
# rejected as an author-written node id or branch target, so a fallthrough-only node stays
# genuinely un-referenceable (its synthesized id is deterministic and would otherwise be a
# fragile thing to point at -- exactly the unstable-reference hazard optional ids remove).
_RESERVED_ID_PREFIX = "__n"


def _unescape_string(s: str) -> str:
    """Reverses render_text.py's `_escape_string` -- `\\\\`/`\\"`/`\\n`/`\\r` are the only
    escapes that side ever produces, so anything else following a backslash is left as-is rather
    than erroring (forward-compatible with a hand-typed escape this module doesn't know about
    yet, matching this pipeline's general preference for permissive parsing over a hard error on
    text a human edited by hand)."""
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "r":
                out.append("\r")
                i += 2
                continue
            if nxt in ('"', "\\"):
                out.append(nxt)
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _mask_quotes(s: str) -> str:
    """Return a same-length version of `s` with every `"..."` span's contents (including the
    quote characters themselves) replaced by a neutral placeholder. Used only to decide *where*
    to split/stop structural scanning below -- callers always slice/return substrings of the
    original `s`, never the mask. Needed because a hidden field's free-text value (`cmt`, `txt`)
    can itself contain `(`, `)`, `,`, or `=` -- a real bug caught while round-tripping
    `beacon.dcs`'s own comments, which contain exactly this."""
    out = list(s)
    in_quotes = False
    for i, c in enumerate(s):
        if c == '"' and (i == 0 or s[i - 1] != "\\"):
            in_quotes = not in_quotes
            out[i] = "Q"
        elif in_quotes:
            out[i] = "x"
    return "".join(out)


def _find_close_paren(s: str, start: int) -> int:
    """`s[start]` is the character right after an already-consumed opening `(` (depth 1).
    Returns the index of its matching `)`."""
    masked = _mask_quotes(s)
    depth = 1
    i = start
    while i < len(s):
        if masked[i] == "(":
            depth += 1
        elif masked[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise SyntaxError(f"unmatched '(' in: {s!r}")


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split on `sep`, but only where it appears outside any `(...)`/`[...]` nesting or a
    quoted string -- a value like `coord(-5, 6)[num=3]` has commas of its own that a naive
    split would misparse, and so can a `cmt="..."` free-text value."""
    if not s.strip():
        return []
    masked = _mask_quotes(s)
    parts, depth, start = [], 0, 0
    for i, c in enumerate(masked):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(s[start:i])
            start = i + 1
    parts.append(s[start:])
    return parts


def _split_first_top_level_eq(s: str) -> tuple[str, str]:
    masked = _mask_quotes(s)
    depth = 0
    for i, c in enumerate(masked):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "=" and depth == 0:
            return s[:i].strip(), s[i + 1 :].strip()
    raise SyntaxError(f"expected '=' in arg: {s!r}")


def _strip_trailing_comment(line: str) -> str:
    """Removes a trailing `# ...` comment (quote-aware: a `#` inside a quoted string is
    content, not a comment). Comments are never structural: parse drops them, the default
    render never emits them, and the annotated render mode (`render_behavior(...,
    annotate=True)`) emits them freely -- so annotated output stays parseable. Deliberately
    stripped *before* branch-note scanning: a comment containing `>foo (bar)` must not parse
    as a branch note."""
    masked = _mask_quotes(line)
    idx = masked.find("#")
    return line[:idx].rstrip() if idx >= 0 else line


_CMT_OPEN_RE = re.compile(r'cmt\s*=\s*"""')


def _scan_delims(text: str) -> tuple[int, bool, bool, int | None]:
    """Bracket/quote/comment-aware scan of (possibly multi-line) node text. Returns
    (paren_depth, in_triple_quote, in_single_string, first_top_level_semicolon_index). A
    triple-quoted block is opaque literal content (only a closing triple-quote ends it); a
    `"..."` single string honours backslash escapes (matching render_text's `_escape_string`); a
    `#` outside any quote is a comment to end of that physical line (so a `;` inside a comment is
    never mistaken for the terminator, while a `#` inside a triple-quoted cmt body stays content).
    `depth` counts `()`/`[]`; the semicolon index is the first `;` at depth 0 outside any
    quote/comment -- the node terminator."""
    i, n = 0, len(text)
    depth = 0
    in_tri = in_str = False
    semi = None
    while i < n:
        if in_tri:
            if text.startswith('"""', i):
                in_tri = False
                i += 3
                continue
            i += 1
            continue
        if in_str:
            if text[i] == "\\" and i + 1 < n:
                i += 2
                continue
            if text[i] == '"':
                in_str = False
            i += 1
            continue
        if text[i] == "#":
            nl = text.find("\n", i)
            if nl < 0:
                break
            i = nl
            continue
        if text.startswith('"""', i):
            in_tri = True
            i += 3
            continue
        c = text[i]
        if c == '"':
            in_str = True
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == ";" and depth == 0 and semi is None:
            semi = i
        i += 1
    return depth, in_tri, in_str, semi


def _starts_continuation(line: str) -> bool:
    """Whether a physical line continues the previous node rather than starting a new one. Only a
    branch note (`>...`) or a cmt block (`cmt=...`) may open a continuation line -- neither can
    begin a node-start line (`[id:] op(`), so this stays unambiguous."""
    s = line.strip()
    return s.startswith(">") or bool(re.match(r"cmt\s*=", s))


def _consume_node(lines: list[str], start: int) -> tuple[str, int]:
    """Group physical lines[start:] into one logical node's text. A node is a single physical line
    unless it opens an unbalanced paren or quote (arg wrap / cmt block) or its next meaningful line
    carries more branch notes or a cmt block; a node spanning multiple lines MUST end with `;` --
    the parser scans for the terminator and never counts indentation, keeping whitespace
    non-semantic (docs/behavior_source_format.md, 2026-07-20). Returns (logical_text, next_line_index)."""
    buf = [lines[start]]
    i = start + 1
    while True:
        depth, in_tri, in_str, semi = _scan_delims("\n".join(buf))
        if in_tri or in_str or depth > 0:
            if i >= len(lines):
                raise BsfParseError(
                    "unterminated node (unbalanced '(' or unclosed '\"'/'\"\"\"' quote)",
                    start + 1,
                    lines[start],
                )
            buf.append(lines[i])
            i += 1
            continue
        if semi is not None:
            break
        # balanced with no ';': continue only if the next meaningful line is a continuation
        j = i
        while j < len(lines) and (lines[j].strip() == "" or lines[j].strip().startswith("#")):
            j += 1
        if j < len(lines) and _starts_continuation(lines[j]):
            buf.append(lines[j])
            i = j + 1
            continue
        break
    text = "\n".join(buf)
    if len(buf) > 1 and _scan_delims(text)[3] is None:
        raise BsfParseError(
            "a node spanning multiple lines must end with ';'", start + 1, lines[start]
        )
    return text, i


def _find_cmt_block(post: str, line_no, line) -> tuple[int, str, int] | None:
    """Locate a top-level triple-quoted `cmt=` block in the post-close-paren region. Returns
    (start_index, raw_content, end_index) or None. Skips over branch-note parens, single strings,
    and comments so only a genuine top-level cmt block matches."""
    i, n = 0, len(post)
    depth = 0
    in_str = False
    while i < n:
        if in_str:
            if post[i] == "\\" and i + 1 < n:
                i += 2
                continue
            if post[i] == '"':
                in_str = False
            i += 1
            continue
        if post[i] == "#":
            nl = post.find("\n", i)
            if nl < 0:
                break
            i = nl
            continue
        if depth == 0:
            m = _CMT_OPEN_RE.match(post, i)
            if m:
                content_start = m.end()
                close = post.find('"""', content_start)
                if close < 0:
                    raise BsfParseError('unterminated cmt block (no closing """)', line_no, line)
                return i, post[content_start:close], close + 3
        if post.startswith('"""', i):  # a bare """ with no cmt= (malformed) -- skip its span
            end = post.find('"""', i + 3)
            i = (end + 3) if end >= 0 else n
            continue
        c = post[i]
        if c == '"':
            in_str = True
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        i += 1
    return None


def _split_post_paren(post: str, line_no, line) -> tuple[str, str | None]:
    """Split the region after a node's `op(...)` close paren into (branch_notes_str, cmt): strip
    the optional trailing `;` terminator (nothing but whitespace/comment may follow it) and pull
    out an optional triple-quoted `cmt=` block. `cmt` is None when absent; a block cmt has exactly
    one leading and one trailing newline removed (the expanded-block render form), so both the
    compact and expanded forms round-trip the exact string."""
    _, _, _, semi = _scan_delims(post)
    if semi is not None:
        tail = _strip_trailing_comment(post[semi + 1 :])
        if tail.strip():
            raise BsfParseError(
                f"unexpected content after ';' terminator: {tail.strip()!r}", line_no, line
            )
        post = post[:semi]
    block = _find_cmt_block(post, line_no, line)
    if block is None:
        return post, None
    lo, content, hi = block
    remainder = post[:lo] + post[hi:]
    if _find_cmt_block(remainder, line_no, line) is not None:
        raise BsfParseError("cmt specified more than once on one node", line_no, line)
    if content.startswith("\n"):
        content = content[1:]
    if content.endswith("\n"):
        content = content[:-1]
    return remainder, content


def _bare_id_decl(line: str) -> str | None:
    """A line that is just `NODE_ID:` -- an identifier terminated by a colon, nothing but an
    optional trailing comment after -- declares the id for the FOLLOWING instruction (the
    id-on-own-line layout, so instruction bodies align at column 0). Returns the id, or None if
    the line isn't that shape. Unambiguous against the two things it could be confused with: an
    inline `id: op(...)` has an `op(...)` after the colon (so a `(` is present), and a `label(...)`
    op has `(` with no bare colon -- neither matches `identifier :` with nothing following."""
    s = _strip_trailing_comment(line).strip()
    if not s.endswith(":") or "(" in s:
        return None
    candidate = s[:-1].strip()
    if candidate and " " not in candidate and ":" not in candidate:
        return candidate
    return None


def _parse_number(s: str) -> int | float:
    s = s.strip()
    return float(s) if "." in s else int(s)


def _split_num_suffix(s: str) -> tuple[str, int | float | None]:
    if s.endswith("]") and "[num=" in s:
        base, _, rest = s.rpartition("[num=")
        return base, _parse_number(rest[:-1])
    return s, None


def parse_value(s: str, params: list[BsfParam]) -> BsfValue:
    s = s.strip()
    if s.startswith("$"):
        rest = s[1:]
        # render_text.py quotes a variable name that contains grammar-significant characters
        # (parens, commas, brackets, quotes, a raw newline) as `$"escaped name"` instead of the
        # bare `$name` form -- see `_var_needs_quoting`'s sibling docstring in render_text.py for
        # why a bare name can't safely carry those characters.
        if len(rest) >= 2 and rest.startswith('"') and rest.endswith('"'):
            return Var(_unescape_string(rest[1:-1]))
        return Var(rest)
    if s.startswith("@"):
        rest = s[1:]
        if rest in _SYMBOLIC_FRAME_REGS:
            return FrameReg(_SYMBOLIC_FRAME_REGS[rest])
        return FrameReg(-int(rest))
    if s.startswith("fr("):
        close = _find_close_paren(s, 3)
        name = s[3:close]
        remainder = s[close + 1 :]
        num = _parse_number(remainder[5:-1]) if remainder.startswith("[num=") else None
        return Fr(name, num)
    if s.startswith("coord("):
        close = _find_close_paren(s, 6)
        x_str, y_str = _split_top_level(s[6:close], ",")
        remainder = s[close + 1 :]
        num = _parse_number(remainder[5:-1]) if remainder.startswith("[num=") else None
        return Coord(_parse_number(x_str), _parse_number(y_str), num)
    m = _SLOT_UNDECLARED_RE.match(s)
    if m:
        return Param(int(m.group(1)))
    if _NUM_RE.match(s):
        return Num(_parse_number(s))
    base, num = _split_num_suffix(s)
    for i, p in enumerate(params, start=1):
        if p.name == base:
            return Param(i)
    return IdLit(base, num)


def _parse_hidden_value(s: str) -> object:
    s = s.strip()
    if s == "False":
        return False
    if s == "True":
        return True
    if s.startswith('"') and s.endswith('"'):
        return _unescape_string(s[1:-1])
    if _NUM_RE.match(s):
        return _parse_number(s)
    return s


def _parse_branch_notes(
    s: str, op: str, argcache: ArgCache, line_no: int | None = None, line: str | None = None
) -> dict[str, str]:
    next_pin = argcache.next_pin_name(op)
    # For a dynamic-arg op (call/load_behavior) the declared exec-arg list is empty by
    # construction, so the only valid pin is its own next pin -- exec_pin_names covers both
    # cases uniformly.
    valid_pins = argcache.exec_pin_names(op)
    branches = {}
    seen_pins: set[str] = set()
    for target, pin in _BRANCH_RE.findall(s):
        pin = pin.strip()
        if pin == "jump→label":
            continue  # display-only annotation, recomputed fresh on every render, not real data
        if pin not in valid_pins:
            raise BsfParseError(
                f"op {op!r} has no exec pin named {pin!r}; valid pins: "
                f"{', '.join(repr(p) for p in valid_pins) or '(none)'}{_suggest(pin, valid_pins)}",
                line_no,
                line,
            )
        if pin in seen_pins:
            raise BsfParseError(f"pin {pin!r} wired twice on one node", line_no, line)
        seen_pins.add(pin)
        # `NEXT` is the explicit spelling of plain fall-to-physically-next (structurally:
        # the key stays absent, same as omission) -- required for 2+ exec-pin ops (below),
        # accepted anywhere.
        if target == "NEXT":
            continue
        # A pin whose rendered name matches this op's real "next"-pin display name (e.g.
        # check_number's "If Equal") maps back to the structural "next" key -- everything else
        # (a real declared exec arg's own name, e.g. "If Larger") is already its own key.
        key = "next" if (next_pin is not None and pin == next_pin) else pin
        branches[key] = "POP" if target == "POP" else target

    if len(valid_pins) >= 2:
        # Ops with 2+ exec pins require every pin spelled out (a target node id, `POP`, or
        # `NEXT`) -- an omitted pin here has repeatedly meant "the author forgot this pin
        # exists" (a loop's Done pin, twice), and with one pin visibly wired the text reads
        # complete. render_text.py emits all of them symmetrically.
        missing = [p for p in valid_pins if p not in seen_pins]
        if missing:
            raise BsfParseError(
                f"op {op!r} has {len(valid_pins)} exec pins and every one must be written "
                f"(>node_id, >POP, or >NEXT); missing: {', '.join(repr(p) for p in missing)}",
                line_no,
                line,
            )
    return branches


def parse_node(
    line: str,
    params: list[BsfParam],
    argcache: ArgCache,
    line_no: int | None = None,
    known_ids: frozenset[str] | None = None,
    auto_id: str = "__auto",
) -> BsfNode:
    # An `id:` prefix is optional (docs/behavior_source_format.md's optional node ids): a line that
    # starts straight with `op(...)` -- no `:` before the first `(` -- is a node reached only by
    # fallthrough, carrying no surface id. It still gets a synthesized internal id (`auto_id`,
    # made unique per behavior by the caller) so graph edges and `order` work, with id_explicit
    # False so render_text.py won't print it back. A `:` before the `(` means an author-written
    # id (id_explicit True).
    paren_pos = line.find("(")
    if paren_pos >= 0 and ":" in line[:paren_pos]:
        node_id, rest = line.split(":", 1)
        node_id = node_id.strip()
        id_explicit = True
    else:
        node_id, rest, id_explicit = auto_id, line, False
    rest = rest.strip()
    op_end = rest.find("(")
    if op_end < 0:
        raise BsfParseError(
            "expected a node line ('op(...)' or 'id: op(...)'; no '(' found)", line_no, line
        )
    op = rest[:op_end].strip()
    if not argcache.op_exists(op):
        raise BsfParseError(
            f"unknown instruction op {op!r}{_suggest(op, argcache.all_ops())}", line_no, line
        )
    after = rest[op_end + 1 :]
    close = _find_close_paren(after, 0)
    arg_list_str = after[:close]
    # After the op's close paren: branch notes, an optional `cmt="""..."""` block, and (for a
    # multi-line node) the `;` terminator. Split those apart before touching the args.
    branch_notes_str, block_cmt = _split_post_paren(after[close + 1 :], line_no, line)

    node = BsfNode(id=node_id, op=op, id_explicit=id_explicit)
    branch_notes_str = _strip_trailing_comment(branch_notes_str)
    hidden_fields = {f for o, f in HIDDEN_FIELD_TABLE.items() if o == op} | {"cmt"}
    # A dynamic-arg op's value-arg names come from the *target* sub's own parameters, which may
    # not even be parsed yet -- name validation for those happens at compile time instead.
    valid_arg_names = None if op in DYNAMIC_ARG_OPS else argcache.value_arg_names(op)
    for arg_str in _split_top_level(arg_list_str, ","):
        name, value_str = _split_first_top_level_eq(arg_str)
        if name in node.args or name in node.hidden:
            raise BsfParseError(
                f"duplicate argument {name!r} -- a repeated declared pin name must use its "
                f"occurrence suffix ({name}2, {name}3, ...)",
                line_no,
                line,
            )
        if name in hidden_fields:
            node.hidden[name] = _parse_hidden_value(value_str)
            continue
        if valid_arg_names is not None and name not in valid_arg_names:
            valid = sorted(valid_arg_names | (hidden_fields - {"cmt"})) + ["cmt"]
            raise BsfParseError(
                f"op {op!r} has no argument named {name!r}; valid: "
                f"{', '.join(valid)}{_suggest(name, valid)}",
                line_no,
                line,
            )
        value = parse_value(value_str, params)
        if known_ids is not None and isinstance(value, IdLit) and value.id not in known_ids:
            raise BsfParseError(
                f"unknown identifier {value.id!r} (not a registered game id; for a local "
                f"variable write '${value.id}'){_suggest(value.id, known_ids)}",
                line_no,
                line,
            )
        node.args[name] = value
    if block_cmt is not None:
        if "cmt" in node.hidden:
            raise BsfParseError(
                "cmt specified both inline and as a block on one node", line_no, line
            )
        node.hidden["cmt"] = block_cmt
    node.branches.update(_parse_branch_notes(branch_notes_str, op, argcache, line_no, line))
    return node


def _parse_params(params_str: str) -> list[BsfParam]:
    """The trailing `*` (see render_text.py's `_render_into`) is display-only, recomputed fresh
    from actual usage on every render -- accepted and stripped here, not stored on `BsfParam`,
    so a hand-edit that adds/removes a write to a parameter without remembering to update `*`
    still compiles to the correct `parameters[i]` bit rather than a stale hand-typed one."""
    result = []
    for p in _split_top_level(params_str, ","):
        p = p.strip()
        if not p:
            continue
        result.append(BsfParam(name=p[:-1] if p.endswith("*") else p))
    return result


# Header attribute lines (`desc:`/`keepvars:`/`keeparrays:`), accepted in any order between the
# header and the first node -- an earlier version required this exact order and misparsed an
# out-of-order attribute as a node line, dying with an unrelated "substring not found".
_ATTR_KEYWORDS = ("desc", "keepvars", "keeparrays")


def _parse_one(
    lines: list[str], i: int, keyword: str, argcache: ArgCache
) -> tuple[BsfBehavior, int]:
    m = _HEADER_RE.match(lines[i])
    if not m or m.group(1) != keyword:
        raise BsfParseError(f"expected a {keyword!r} header here", i + 1, lines[i])
    name = m.group(2).strip()
    params = _parse_params(m.group(3))
    i += 1

    attrs: dict[str, object] = {}
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "" or stripped.startswith("#"):
            i += 1
            continue
        key = stripped.split(":", 1)[0].strip() if ":" in stripped else None
        if key not in _ATTR_KEYWORDS:
            break
        if key in attrs:
            raise BsfParseError(f"duplicate {key!r} attribute", i + 1, lines[i])
        dm = _DESC_RE.match(stripped)
        if key == "desc":
            if not dm:
                raise BsfParseError('malformed desc line (expected: desc: "...")', i + 1, lines[i])
            attrs["desc"] = _unescape_string(dm.group(1))
        elif key == "keepvars":
            if not _KEEPVARS_RE.match(stripped):
                raise BsfParseError(
                    "malformed keepvars line (expected: keepvars: true)", i + 1, lines[i]
                )
            attrs["keepvars"] = True
        else:
            km = _KEEPARRAYS_RE.match(stripped)
            if not km:
                raise BsfParseError(
                    'malformed keeparrays line (expected: keeparrays: "startup" or "store")',
                    i + 1,
                    lines[i],
                )
            attrs["keeparrays"] = km.group(1)
        i += 1

    known_ids = argcache.known_ids()
    nodes: dict[str, BsfNode] = {}
    node_lines: dict[str, int] = {}
    order: list[str] = []
    auto_counter = 0
    pending_id: str | None = None  # an id declared on its own line, awaiting its instruction
    pending_line = 0
    # Blank lines between nodes are allowed (grouping aids readability; headers are unambiguous,
    # so blanks carry no structure) -- a block ends only at the next behavior/sub header or EOF.
    # An earlier version silently ended the node list at the first blank line, so a stray blank
    # made every following node die with a baffling "expected 'sub' header".
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "" or stripped.startswith("#"):
            i += 1
            continue
        if stripped.startswith(("sub ", "behavior ")):
            break
        # A bare `NODE_ID:` line declares the id for the next instruction (id-on-own-line).
        decl = _bare_id_decl(lines[i])
        if decl is not None:
            if pending_id is not None:
                raise BsfParseError(
                    f"node id {pending_id!r} declared on its own line, but another id {decl!r} "
                    f"follows before any instruction",
                    pending_line,
                    lines[pending_line - 1],
                )
            pending_id = decl
            pending_line = i + 1
            i += 1
            continue
        # Fresh synthesized id for a possibly-id-less line (parse_node uses it only when the line
        # carries no explicit `id:`); kept clear of any author id already parsed.
        auto_counter += 1
        auto_id = f"{_RESERVED_ID_PREFIX}{auto_counter}"
        while auto_id in nodes:
            auto_counter += 1
            auto_id = f"{_RESERVED_ID_PREFIX}{auto_counter}"
        # A node may span several physical lines (wrapped args, or a trailing cmt block); group
        # them into one logical node text, terminated by `;` when multi-line.
        node_text, next_i = _consume_node(lines, i)
        node = parse_node(
            node_text, params, argcache, line_no=i + 1, known_ids=known_ids, auto_id=auto_id
        )
        if pending_id is not None:
            if node.id_explicit:
                raise BsfParseError(
                    f"node has both a preceding id line {pending_id!r} and an inline id "
                    f"{node.id!r} -- use one or the other",
                    i + 1,
                    lines[i],
                )
            node.id = pending_id
            node.id_explicit = True
            pending_id = None
        if node.id in ("POP", "NEXT"):
            raise BsfParseError(
                f"{node.id!r} is a reserved branch-target keyword and cannot be a node id",
                i + 1,
                lines[i],
            )
        if node.id in _ATTR_KEYWORDS:
            raise BsfParseError(
                f"{node.id!r} is reserved for a header attribute and cannot be a node id "
                f"(header attributes must appear before the first node)",
                i + 1,
                lines[i],
            )
        if node.id_explicit and node.id.startswith(_RESERVED_ID_PREFIX):
            raise BsfParseError(
                f"node id {node.id!r} uses the reserved internal-id prefix {_RESERVED_ID_PREFIX!r} "
                f"(synthesized for id-less lines) -- choose another name",
                i + 1,
                lines[i],
            )
        if node.id in nodes:
            raise BsfParseError(f"duplicate node id {node.id!r}", i + 1, lines[i])
        nodes[node.id] = node
        node_lines[node.id] = i + 1
        order.append(node.id)
        i = next_i

    if pending_id is not None:
        raise BsfParseError(
            f"node id {pending_id!r} declared on its own line, but no instruction follows",
            pending_line,
            lines[pending_line - 1],
        )

    for node in nodes.values():
        for pin, target in node.branches.items():
            if target is None or target == "POP":
                continue
            if target.startswith(_RESERVED_ID_PREFIX):
                # A fallthrough-only node has no referenceable surface id -- `__nN` is the
                # internal synthesized id, deliberately not addressable. Give the intended
                # target an explicit id instead.
                raise BsfParseError(
                    f"node {node.id!r} pin {pin!r} targets {target!r}, which uses the reserved "
                    f"internal-id prefix {_RESERVED_ID_PREFIX!r}; a fallthrough-only node has no "
                    f"referenceable id -- give the target node an explicit id",
                    node_lines[node.id],
                )
            if target not in nodes:
                raise BsfParseError(
                    f"node {node.id!r} pin {pin!r} targets unknown node {target!r}"
                    f"{_suggest(target, nodes)}",
                    node_lines[node.id],
                )

    desc = attrs.get("desc")
    assert desc is None or isinstance(desc, str)
    keepvars = attrs.get("keepvars", False)
    assert isinstance(keepvars, bool)
    keeparrays = attrs.get("keeparrays")
    assert keeparrays is None or isinstance(keeparrays, str)

    return BsfBehavior(
        name=name,
        params=params,
        desc=desc,
        keepvars=keepvars,
        keeparrays=keeparrays,
        nodes=nodes,
        order=order,
    ), i


def _resolve_sub_ref(
    name: str, rel_path: str, argcache: ArgCache, base_dir: Path | None, visited: frozenset[Path]
) -> BsfBehavior:
    if base_dir is None:
        raise BsfParseError(
            f"sub {name!r} references file {rel_path!r}, but this text has no base directory to "
            f"resolve it against -- parse from a real file path (or pass base_dir explicitly), "
            f"e.g. via bsf/library.py's import/export"
        )
    target = (base_dir / rel_path).resolve()
    if target in visited:
        raise BsfParseError(f"circular sub reference: {rel_path!r} (via {name!r})")
    if not target.exists():
        raise BsfParseError(f"sub {name!r} references file {rel_path!r}, which doesn't exist ({target})")
    resolved = _parse_behavior_impl(
        target.read_text(), argcache, base_dir=target.parent, visited=visited | {target}
    )
    if resolved.name != name:
        raise BsfParseError(
            f"sub reference declares {name!r} but {rel_path!r} defines {resolved.name!r} -- "
            f"the reference is stale, update the name or the path"
        )
    return resolved


def _parse_behavior_impl(
    text: str, argcache: ArgCache, base_dir: Path | None, visited: frozenset[Path]
) -> BsfBehavior:
    lines = text.split("\n")
    i = 0
    while i < len(lines) and (lines[i].strip() == "" or lines[i].strip().startswith("#")):
        i += 1
    if i >= len(lines):
        raise BsfParseError("empty input (expected a 'behavior' header)")
    behavior, i = _parse_one(lines, i, keyword="behavior", argcache=argcache)

    subs = []
    while i < len(lines):
        while i < len(lines) and (lines[i].strip() == "" or lines[i].strip().startswith("#")):
            i += 1
        if i >= len(lines):
            break
        ref = _SUB_REF_RE.match(lines[i].strip())
        if ref:
            subs.append(_resolve_sub_ref(ref.group(1), ref.group(2), argcache, base_dir, visited))
            i += 1
            continue
        sub, i = _parse_one(lines, i, keyword="sub", argcache=argcache)
        subs.append(sub)
    behavior.subs = subs
    return behavior


def parse_behavior(text: str, argcache: ArgCache, base_dir: Path | str | None = None) -> BsfBehavior:
    """base_dir: directory a `sub NAME from "path"` reference resolves relative paths against
    (bsf/library.py's by-name library store, docs/behavior_source_format.md's "Sub-behaviors by
    reference" section). Omit for text with no such references -- it's an error to encounter one
    without a base_dir."""
    resolved_base = Path(base_dir) if base_dir is not None else None
    return _parse_behavior_impl(text, argcache, base_dir=resolved_base, visited=frozenset())
