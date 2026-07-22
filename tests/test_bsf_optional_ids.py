"""Optional + descriptive node ids (docs/behavior_source_format.md's "Node-id readability overhaul",
user 2026-07-20). The decompiler emits an `id:` prefix only for a node something actually
references (a real branch/jump target), and names those by role -- a `label` node after its
`Label` value, every other node after its op. A fallthrough-only node carries no surface id at
all. The parser accepts id-less lines (synthesizing a hidden internal id) and full round-trips
are unaffected (compiled tables are position-derived, not id-derived)."""

from pathlib import Path

import pytest

from blz.desynced_toolkit.bsf.argcache import ArgCache
from blz.desynced_toolkit.bsf.compile import compile_behavior
from blz.desynced_toolkit.bsf.decompile import decompile_dcs
from blz.desynced_toolkit.bsf.parse_text import BsfParseError, parse_behavior
from blz.desynced_toolkit.bsf.render_text import referenced_node_ids, render_behavior
from blz.desynced_toolkit.lua_util import to_py

DATA_DIR = Path(__file__).parent / "data"

# real behaviors with a mix of fallthrough-only nodes and genuine jump/branch targets
_REAL = ["mining_leader.dcs", "observer.dcs", "beacon.dcs", "formation-hold.dcs"]


@pytest.fixture(scope="module")
def argcache(engine):
    return ArgCache(engine)


@pytest.mark.parametrize("fname", _REAL)
def test_id_explicit_iff_referenced_or_label(engine, argcache, fname):
    """The core invariant: after decompile, a node's id is surface-visible exactly when something
    references it -- plus `label` nodes, which always keep their id (a dispatch target by nature).
    This is what makes most non-label instructions carry no id at all."""
    b = decompile_dcs(engine, (DATA_DIR / fname).read_text().strip())

    def check(bb):
        referenced = referenced_node_ids(bb.nodes)
        for nid, node in bb.nodes.items():
            assert node.id_explicit == (nid in referenced or node.op == "label"), (fname, nid, node.op)
        for sub in bb.subs:
            check(sub)

    check(b)


def test_mining_leader_has_both_bare_and_named_nodes(engine, argcache):
    """A real behavior really does end up mostly id-less: the whole point is that the unstable
    `n27` ids are gone from the bulk of the listing."""
    b = decompile_dcs(engine, (DATA_DIR / "mining_leader.dcs").read_text().strip())
    bare = [n for n in b.nodes.values() if not n.id_explicit]
    named = [n for n in b.nodes.values() if n.id_explicit]
    assert bare and named
    assert len(bare) > len(named)  # the majority carry no id


def test_label_nodes_named_after_their_label_value(engine, argcache):
    b = decompile_dcs(engine, (DATA_DIR / "mining_leader.dcs").read_text().strip())
    label_ids = {n.id for n in b.nodes.values() if n.op == "label" and n.id_explicit}
    # every referenced label is named `label_<slug of its Label>`, not `n<pos>`
    assert label_ids
    assert all(lid.startswith("label_") for lid in label_ids)
    # the v_broken num-family (bare / [num=1] / [num=10]) stays distinct, num folded into the id
    assert "label_broken" in label_ids
    assert "label_broken_1" in label_ids


@pytest.mark.parametrize("fname", _REAL)
def test_every_rendered_branch_target_shows_its_id(engine, argcache, fname):
    """A `>id (Pin)` note must always name a node whose own line prints that `id:` -- otherwise
    a reference would dangle. Guaranteed because targets are exactly the referenced set."""
    import re

    b = decompile_dcs(engine, (DATA_DIR / fname).read_text().strip())
    text = render_behavior(b, argcache)
    shown_ids = set()
    targets = set()
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(("behavior ", "sub ", "desc:", "keepvars", "keeparrays")):
            continue
        # an `id:` prefix is a bare identifier before the first `(`, containing a `:`
        head = s.split("(", 1)[0]
        if ":" in head:
            shown_ids.add(head.split(":", 1)[0].strip())
        for tgt, pin in re.findall(r">\s*(\S+?)\s*\(([^)]*)\)", s):
            if tgt not in ("POP", "NEXT") and pin != "jump→label":
                targets.add(tgt)
    assert targets <= shown_ids, (fname, targets - shown_ids)


def test_idless_lines_parse_and_roundtrip(engine, argcache):
    """Hand-authored text mixing id-less lines with a named jump target parses, and re-rendering
    then recompiling is table-equivalent -- the synthesized ids for id-less lines never leak
    into the wire (positions are recomputed)."""
    text = (
        "behavior T():\n\n"
        "set_reg(Value=1, Target=$x)\n"  # id-less, fallthrough
        "loop: for_number(From=1, To=3, Value=$N)  >POP (Done) >NEXT (next)\n"  # named target
        "set_reg(Value=$N, Target=$x)  >loop (next)\n"  # id-less, branches back to the named node
    )
    b = parse_behavior(text, argcache)
    first, mid, last = b.order
    assert b.nodes[mid].id == "loop" and b.nodes[mid].id_explicit
    assert not b.nodes[first].id_explicit and not b.nodes[last].id_explicit
    assert b.nodes[last].branches["next"] == "loop"

    text2 = render_behavior(b, argcache)
    assert "loop:" in text2
    # the two id-less lines re-render bare (no `:` before their `(`)
    bare_lines = [ln for ln in text2.split("\n") if ln.startswith("set_reg(")]
    assert len(bare_lines) == 2
    b2 = parse_behavior(text2, argcache)
    assert to_py(compile_behavior(engine, b, argcache)) == to_py(compile_behavior(engine, b2, argcache))


def test_idless_line_can_still_carry_branch_notes(engine, argcache):
    b = parse_behavior(
        "behavior T():\n\n"
        "check_number(Value=$A, Compare=5)  >done (If Larger) >NEXT (If Smaller) >NEXT (If Equal)\n"
        "set_reg(Value=1, Target=$B)\n"
        "done: exit()\n",
        argcache,
    )
    first = b.order[0]
    assert not b.nodes[first].id_explicit  # the check_number line has no id...
    assert b.nodes[first].branches["If Larger"] == "done"  # ...but its branch still wires up
    assert b.nodes["done"].id_explicit


def test_render_puts_id_on_its_own_line(engine, argcache):
    """The decompiler emits a node's id on the line ABOVE its instruction, so instruction bodies
    line up at column 0 (docs/behavior_source_format.md's id-on-own-line, user 2026-07-20)."""
    b = decompile_dcs(engine, (DATA_DIR / "mining_leader.dcs").read_text().strip())
    text = render_behavior(b, argcache)
    lines = text.split("\n")
    # a referenced label appears as a bare `id:` line immediately followed by its op line
    idx = lines.index("label_arrow_up:")
    assert lines[idx + 1].startswith("label(")
    # id-less nodes have no id line -- their op sits at column 0 directly
    assert any(ln.startswith("unlock()") for ln in lines)


def test_parse_own_line_id_form(engine, argcache):
    text = (
        "behavior T():\n\n"
        "loop:\n"
        "for_number(From=1, To=3, Value=$N)  >POP (Done) >NEXT (next)\n"
        "set_reg(Value=$N, Target=$B)  >loop (next)\n"
    )
    b = parse_behavior(text, argcache)
    assert b.order[0] == "loop"
    assert b.nodes["loop"].op == "for_number" and b.nodes["loop"].id_explicit
    assert b.nodes[b.order[1]].branches["next"] == "loop"


def test_own_line_and_inline_id_forms_compile_identically(engine, argcache):
    own_line = "behavior T():\n\nhit:\nexit()\ncheck_number(Value=$A, Compare=5)  >hit (If Larger) >NEXT (If Smaller) >NEXT (If Equal)\n"
    inline = "behavior T():\n\nhit: exit()\ncheck_number(Value=$A, Compare=5)  >hit (If Larger) >NEXT (If Smaller) >NEXT (If Equal)\n"
    a = parse_behavior(own_line, argcache)
    c = parse_behavior(inline, argcache)
    assert to_py(compile_behavior(engine, a, argcache)) == to_py(compile_behavior(engine, c, argcache))


@pytest.mark.parametrize("fname", _REAL)
def test_own_line_render_roundtrips(engine, argcache, fname):
    b = decompile_dcs(engine, (DATA_DIR / fname).read_text().strip())
    text = render_behavior(b, argcache)
    b2 = parse_behavior(text, argcache)
    assert to_py(compile_behavior(engine, b, argcache)) == to_py(compile_behavior(engine, b2, argcache))


def test_dangling_id_declaration_is_rejected(engine, argcache):
    with pytest.raises(BsfParseError, match=r"no instruction follows"):
        parse_behavior("behavior T():\n\nset_reg(Value=1, Target=$B)\nlonely:\n", argcache)


def test_two_id_declarations_in_a_row_rejected(engine, argcache):
    with pytest.raises(BsfParseError, match=r"another id .* follows before any instruction"):
        parse_behavior("behavior T():\n\nfoo:\nbar:\nexit()\n", argcache)


def test_own_line_and_inline_id_both_rejected(engine, argcache):
    with pytest.raises(BsfParseError, match=r"both a preceding id line .* and an inline id"):
        parse_behavior("behavior T():\n\nfoo:\nbar: exit()\n", argcache)


def test_branch_to_a_synthesized_id_is_rejected(engine, argcache):
    """A synthesized id (`__n1`) is never visible and deterministic, so pointing at one would be
    a fragile coupling to an internal detail -- the reserved `__n` prefix is rejected outright,
    directing the author to give the intended target an explicit id."""
    with pytest.raises(BsfParseError, match=r"reserved internal-id prefix"):
        parse_behavior(
            "behavior T():\n\n"
            "set_reg(Value=1, Target=$B)  >__n1 (next)\n"
            "set_reg(Value=2, Target=$C)\n",
            argcache,
        )

    with pytest.raises(BsfParseError, match=r"reserved internal-id prefix"):
        parse_behavior("behavior T():\n\n__n5: set_reg(Value=1, Target=$B)\n", argcache)
