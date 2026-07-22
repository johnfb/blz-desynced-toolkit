"""Multi-line nodes terminated by `;`, and the `cmt` triple-quoted block (behavior_source_format
.md, decided 2026-07-20). A node stays a single bare line unless it wraps its args across lines
or carries a cmt block, in which case it must end with `;` -- the parser scans for the terminator
and never counts indentation, so whitespace stays non-semantic. `cmt` renders as a triple-quoted
block under the node's branch notes, mirroring the in-game under-node comment."""

import pytest

from blz.desynced_toolkit.bsf.argcache import ArgCache
from blz.desynced_toolkit.bsf.compile import compile_behavior
from blz.desynced_toolkit.bsf.ir import BsfBehavior, BsfNode
from blz.desynced_toolkit.bsf.parse_text import BsfParseError, parse_behavior
from blz.desynced_toolkit.bsf.render_text import render_behavior
from blz.desynced_toolkit.bsf.values import Num, Var
from blz.desynced_toolkit.lua_util import to_py


@pytest.fixture(scope="module")
def argcache(engine):
    return ArgCache(engine)


def _cmt_of(behavior, idx=0):
    return behavior.nodes[behavior.order[idx]].hidden.get("cmt")


def test_single_line_cmt_renders_as_compact_block(engine, argcache):
    n = BsfNode(
        id="n1",
        op="set_reg",
        args={"Value": Num(1), "Target": Var("B")},
        hidden={"cmt": "Reset State"},
    )
    b = BsfBehavior(name="T", nodes={"n1": n}, order=["n1"])
    text = render_behavior(b, argcache)
    assert 'cmt="""Reset State""";' in text
    assert _cmt_of(parse_behavior(text, argcache)) == "Reset State"


def test_multiline_cmt_roundtrips_exactly(engine, argcache):
    body = "First line of the note.\nSecond line, with detail.\nThird."
    n = BsfNode(
        id="n1", op="set_reg", args={"Value": Num(1), "Target": Var("B")}, hidden={"cmt": body}
    )
    b = BsfBehavior(name="T", nodes={"n1": n}, order=["n1"])
    text = render_behavior(b, argcache)
    # expanded block: opening cmt=""" then flush-left content then closing """;
    assert 'cmt="""\nFirst line of the note.\n' in text
    assert text.rstrip().endswith('""";')
    b2 = parse_behavior(text, argcache)
    assert _cmt_of(b2) == body
    assert to_py(compile_behavior(engine, b, argcache)) == to_py(
        compile_behavior(engine, b2, argcache)
    )


def test_cmt_block_preserves_hash_and_semicolon_content(engine, argcache):
    """A `#` or `;` inside the cmt body is literal content, not a comment / terminator -- the
    whole reason the block form (not `#`-prefixed lines) was chosen for cmt."""
    body = "see #4; then retry; done"
    n = BsfNode(
        id="n1", op="set_reg", args={"Value": Num(1), "Target": Var("B")}, hidden={"cmt": body}
    )
    b = BsfBehavior(name="T", nodes={"n1": n}, order=["n1"])
    text = render_behavior(b, argcache)
    assert _cmt_of(parse_behavior(text, argcache)) == body


def test_cmt_containing_triple_quote_falls_back_to_inline(engine, argcache):
    """A body that itself contains a triple-quote can't be block-quoted -- it falls back to the
    inline single-quoted `cmt="..."` arg form, which still round-trips."""
    body = 'he said """hi""" loudly'
    n = BsfNode(
        id="n1", op="set_reg", args={"Value": Num(1), "Target": Var("B")}, hidden={"cmt": body}
    )
    b = BsfBehavior(name="T", nodes={"n1": n}, order=["n1"])
    text = render_behavior(b, argcache)
    assert 'cmt="""' not in text  # not block form
    assert _cmt_of(parse_behavior(text, argcache)) == body


def test_hand_authored_block_and_compact_and_inline_all_parse(engine, argcache):
    expanded = 'behavior T():\n\nset_reg(Value=1, Target=$B)\n  cmt="""\nhello\nworld\n""";\n'
    compact = 'behavior T():\n\nset_reg(Value=1, Target=$B)  cmt="""hello world""";\n'
    inline = 'behavior T():\n\nset_reg(Value=1, Target=$B, cmt="hello world")\n'
    assert _cmt_of(parse_behavior(expanded, argcache)) == "hello\nworld"
    assert _cmt_of(parse_behavior(compact, argcache)) == "hello world"
    assert _cmt_of(parse_behavior(inline, argcache)) == "hello world"


def test_cmt_block_coexists_with_branch_notes(engine, argcache):
    text = (
        "behavior T():\n\n"
        "check_number(Value=$A, Compare=5)  >hit (If Larger) >NEXT (If Smaller) >NEXT (If Equal)\n"
        '  cmt="""compare against the standoff band""";\n'
        "hit: exit()\n"
    )
    b = parse_behavior(text, argcache)
    first = b.order[0]
    assert b.nodes[first].branches["If Larger"] == "hit"
    assert b.nodes[first].hidden["cmt"] == "compare against the standoff band"


def test_multiline_node_without_terminator_is_rejected(engine, argcache):
    with pytest.raises(BsfParseError, match=r"must end with ';'"):
        parse_behavior(
            "behavior T():\n\n"
            "set_reg(Value=1, Target=$B)\n"
            '  cmt="""a note with no terminator"""\n'
            "set_reg(Value=2, Target=$C)\n",
            argcache,
        )


def test_content_after_terminator_is_rejected(engine, argcache):
    with pytest.raises(BsfParseError, match=r"content after ';' terminator"):
        parse_behavior(
            'behavior T():\n\nset_reg(Value=1, Target=$B)  cmt="""x""";  set_reg(Value=2, Target=$C)\n',
            argcache,
        )


def test_cmt_specified_twice_is_rejected(engine, argcache):
    with pytest.raises(BsfParseError, match=r"cmt specified"):
        parse_behavior(
            'behavior T():\n\nset_reg(Value=1, Target=$B, cmt="inline")  cmt="""block""";\n',
            argcache,
        )


def test_wrapped_args_across_lines_with_terminator(engine, argcache):
    """A node may wrap its arg list across physical lines (paren stays open); being multi-line it
    ends with `;`. Indentation is irrelevant to the parser."""
    text = "behavior T():\n\nset_reg(Value=1,\n        Target=$B);\nexit()\n"
    b = parse_behavior(text, argcache)
    assert b.nodes[b.order[0]].args["Value"] == Num(1)
    assert b.nodes[b.order[0]].args["Target"] == Var("B")


def test_branch_notes_wrapped_onto_continuation_lines(engine, argcache):
    text = (
        "behavior T():\n\n"
        "check_number(Value=$A, Compare=5)\n"
        "  >hit (If Larger)\n"
        "  >NEXT (If Smaller)\n"
        "  >NEXT (If Equal);\n"
        "hit: exit()\n"
    )
    b = parse_behavior(text, argcache)
    assert b.nodes[b.order[0]].branches["If Larger"] == "hit"


def test_all_cmt_fixtures_use_block_form_after_render(engine, argcache):
    """A real behavior with cmt fields now renders them as blocks (the whole point of the
    change), and still round-trips."""
    from pathlib import Path

    from blz.desynced_toolkit.bsf.decompile import decompile_dcs

    raw = (Path(__file__).parent / "data" / "mining_leader.dcs").read_text().strip()
    b = decompile_dcs(engine, raw)
    text = render_behavior(b, argcache)
    assert 'cmt="""' in text
    b2 = parse_behavior(text, argcache)
    assert to_py(compile_behavior(engine, b, argcache)) == to_py(
        compile_behavior(engine, b2, argcache)
    )
