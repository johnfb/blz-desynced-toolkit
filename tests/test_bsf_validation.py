"""Strict parse/compile validation. A probe demonstrated that every
case here was either accepted silently (compiling into a *wrong* behavior with no
error at any stage) or rejected with a context-free/misleading error. The silent cases were the
motivation -- see the review notes in docs/behavior_source_format.md's "Validation" section:
  - a typo'd exec pin name compiled with that pin silently unwired
  - a forgotten `$` sigil silently turned a variable into an id literal
  - a duplicate node id silently clobbered its predecessor (and corrupted `order`)
  - a duplicate arg name silently dropped the first value
"""

import pytest

from blz.desynced_toolkit.bsf.argcache import ArgCache
from blz.desynced_toolkit.bsf.compile import BsfCompileError, compile_behavior
from blz.desynced_toolkit.bsf.ir import BsfBehavior, BsfNode
from blz.desynced_toolkit.bsf.parse_text import BsfParseError, parse_behavior
from blz.desynced_toolkit.bsf.render_text import render_behavior
from blz.desynced_toolkit.bsf.values import Num, Var


@pytest.fixture(scope="module")
def argcache(engine):
    return ArgCache(engine)


def _parse(text, argcache):
    return parse_behavior(text, argcache)


def test_typoed_pin_name_rejected_with_valid_pins_listed(argcache):
    with pytest.raises(BsfParseError, match=r"no exec pin named 'Smaller'.*If Smaller"):
        _parse(
            "behavior T():\n\n"
            "n1: check_number(Value=$A, Compare=5)  >n3 (Smaller)\n"
            "n2: set_reg(Value=1, Target=$B)\n"
            "n3: set_reg(Value=2, Target=$B)\n",
            argcache,
        )


def test_unknown_op_rejected_with_suggestion(argcache):
    with pytest.raises(BsfParseError, match=r"unknown instruction op 'set_regg'.*set_reg"):
        _parse("behavior T():\n\nn1: set_regg(Value=1, Target=$B)\n", argcache)


def test_missing_sigil_rejected_as_unknown_id(argcache):
    with pytest.raises(BsfParseError, match=r"unknown identifier 'ScanResult'.*\$ScanResult"):
        _parse("behavior T():\n\nn1: set_reg(Value=ScanResult, Target=$B)\n", argcache)


def test_misspelled_game_id_gets_suggestion(argcache):
    with pytest.raises(BsfParseError, match=r"unknown identifier 'v_resourse'.*v_resource"):
        _parse("behavior T():\n\nn1: set_reg(Value=v_resourse, Target=$B)\n", argcache)


def test_known_game_ids_accepted(argcache):
    b = _parse("behavior T():\n\nn1: set_reg(Value=v_resource, Target=$B)\n", argcache)
    assert b.nodes["n1"].args["Value"].id == "v_resource"


def test_duplicate_node_id_rejected(argcache):
    with pytest.raises(BsfParseError, match=r"line 4: duplicate node id 'n1'"):
        _parse(
            "behavior T():\n\nn1: set_reg(Value=1, Target=$B)\nn1: set_reg(Value=2, Target=$C)\n",
            argcache,
        )


def test_duplicate_arg_name_rejected_with_suffix_hint(argcache):
    with pytest.raises(BsfParseError, match=r"duplicate argument 'Unit'.*Unit2"):
        _parse("behavior T():\n\nn1: is_same_grid(Unit=$A, Unit=$B)\n", argcache)


def test_typoed_arg_name_rejected_with_valid_names(argcache):
    with pytest.raises(BsfParseError, match=r"no argument named 'Valu'.*Value"):
        _parse("behavior T():\n\nn1: set_reg(Valu=1, Target=$B)\n", argcache)


def test_branch_to_unknown_node_rejected(argcache):
    with pytest.raises(BsfParseError, match=r"node 'n1' pin 'If Smaller' targets unknown node 'n9'"):
        _parse(
            "behavior T():\n\n"
            "n1: check_number(Value=$A, Compare=5)  >NEXT (If Larger) >n9 (If Smaller) >NEXT (If Equal)\n"
            "n2: set_reg(Value=1, Target=$B)\n",
            argcache,
        )


# -- multi-exec-pin ops require every pin written (the loop-Done-omission class) -------------


def test_multi_pin_op_with_missing_pins_rejected(argcache):
    with pytest.raises(BsfParseError, match=r"every one must be written.*missing: 'Done'"):
        _parse(
            "behavior T():\n\n"
            "n1: for_number(From=1, To=3, Value=$N)\n"
            "n2: set_reg(Value=$N, Target=$B)  >n1 (next)\n",
            argcache,
        )


def test_multi_pin_op_fully_written_accepted_and_next_is_fallthrough(argcache):
    b = _parse(
        "behavior T():\n\n"
        "n1: for_number(From=1, To=3, Value=$N)  >POP (Done) >NEXT (next)\n"
        "n2: set_reg(Value=$N, Target=$B)  >n1 (next)\n",
        argcache,
    )
    # NEXT = explicit fallthrough = structurally absent, exactly like single-pin omission
    assert "next" not in b.nodes["n1"].branches
    assert b.nodes["n1"].branches["Done"] == "POP"


def test_single_pin_op_accepts_explicit_next_token(argcache):
    b = _parse("behavior T():\n\nn1: set_reg(Value=1, Target=$B)  >NEXT (next)\nn2: unlock()\n", argcache)
    assert "next" not in b.nodes["n1"].branches


def test_render_emits_next_token_for_all_multi_pin_ops(engine, argcache):
    n1 = BsfNode(id="n1", op="check_number", args={"Value": Num(1), "Compare": Num(2)})
    n1.branches["If Smaller"] = "POP"
    n2 = BsfNode(id="n2", op="set_reg", args={"Value": Num(1), "Target": Var("B")})
    b = BsfBehavior(name="T", nodes={"n1": n1, "n2": n2}, order=["n1", "n2"])
    text = render_behavior(b, argcache)
    lines = text.split("\n")
    n1_line = lines[lines.index("n1:") + 1]  # id on its own line; instruction is the line below
    assert ">NEXT (If Larger)" in n1_line
    assert ">POP (If Smaller)" in n1_line
    assert ">NEXT (If Equal)" in n1_line
    # and the rendered text passes its own completeness rule
    parse_behavior(text, argcache)


def test_pop_and_next_reserved_as_node_ids(argcache):
    with pytest.raises(BsfParseError, match=r"'POP' is a reserved"):
        _parse("behavior T():\n\nPOP: set_reg(Value=1, Target=$B)\n", argcache)


def test_blank_lines_between_nodes_allowed(argcache):
    b = _parse(
        "behavior T():\n\n"
        "n1: set_reg(Value=1, Target=$B)\n"
        "\n"
        "n2: set_reg(Value=2, Target=$C)\n",
        argcache,
    )
    assert b.order == ["n1", "n2"]


def test_header_attributes_accepted_in_any_order(argcache):
    b = _parse(
        'behavior T():\n  keepvars: true\n  desc: "hello"\n\nn1: set_reg(Value=1, Target=$B)\n',
        argcache,
    )
    assert b.keepvars is True and b.desc == "hello"


def test_malformed_header_attribute_rejected(argcache):
    with pytest.raises(BsfParseError, match=r"malformed keepvars"):
        _parse("behavior T():\n  keepvars: yes\n\nn1: set_reg(Value=1, Target=$B)\n", argcache)


def test_pin_wired_twice_rejected(argcache):
    with pytest.raises(BsfParseError, match=r"pin 'If Smaller' wired twice"):
        _parse(
            "behavior T():\n\n"
            "n1: check_number(Value=$A, Compare=5)  >n2 (If Smaller) >POP (If Smaller)\n"
            "n2: set_reg(Value=1, Target=$B)\n",
            argcache,
        )


def test_parse_errors_carry_line_numbers(argcache):
    with pytest.raises(BsfParseError, match=r"line 3:"):
        _parse("behavior T():\n\nn1: set_regg(Value=1)\n", argcache)


def test_desc_with_quotes_and_newline_roundtrips(engine, argcache):
    b = BsfBehavior(
        name="T",
        desc='say "hi" then\nstop',
        nodes={"n1": BsfNode(id="n1", op="set_reg", args={"Value": Num(1), "Target": Var("B")})},
        order=["n1"],
    )
    text = render_behavior(b, argcache)
    b2 = parse_behavior(text, argcache)
    assert b2.desc == b.desc


# -- IR-level (hand-built, bypassing the parser) --------------------------------------------


def _one_node_behavior(node):
    return BsfBehavior(name="T", nodes={node.id: node}, order=[node.id])


def test_compile_rejects_unknown_branch_pin_on_ir(engine, argcache):
    n = BsfNode(id="n1", op="check_number", args={"Value": Num(1), "Compare": Num(2)})
    n.branches["Smaller"] = "POP"  # typo'd key -- was silently dropped before
    with pytest.raises(BsfCompileError, match=r"unknown branch pin 'Smaller'"):
        compile_behavior(engine, _one_node_behavior(n), argcache)


def test_compile_rejects_unknown_op_on_ir(engine, argcache):
    n = BsfNode(id="n1", op="set_regg", args={})
    with pytest.raises(BsfCompileError, match=r"unknown instruction op 'set_regg'"):
        compile_behavior(engine, _one_node_behavior(n), argcache)


def test_compile_rejects_unknown_arg_on_ir(engine, argcache):
    n = BsfNode(id="n1", op="set_reg", args={"Valu": Num(1)})
    with pytest.raises(BsfCompileError, match=r"unknown argument 'Valu'"):
        compile_behavior(engine, _one_node_behavior(n), argcache)


def test_compile_rejects_branch_to_unknown_node_on_ir(engine, argcache):
    n = BsfNode(id="n1", op="set_reg", args={"Value": Num(1), "Target": Var("B")})
    n.branches["next"] = "n9"
    with pytest.raises(BsfCompileError, match=r"node 'n1' pin 'next'.*unknown node 'n9'"):
        compile_behavior(engine, _one_node_behavior(n), argcache)


# -- lint: legal-but-suspicious ---------------------------------------------------------------


def test_lint_flags_unreachable_node(engine, argcache):
    from blz.desynced_toolkit.bsf.lint import lint_behavior
    from blz.desynced_toolkit.bsf.parse_text import parse_behavior

    b = parse_behavior(
        "behavior T():\n\n"
        "n1: set_reg(Value=1, Target=$B)  >POP (next)\n"
        "n2: set_reg(Value=2, Target=$C)\n",  # n1 POPs; nothing reaches n2
        argcache,
    )
    warnings = lint_behavior(b, argcache)
    assert any("'n2' is unreachable" in w for w in warnings)


def test_lint_label_sections_are_not_unreachable(engine, argcache):
    from blz.desynced_toolkit.bsf.lint import lint_behavior
    from blz.desynced_toolkit.bsf.parse_text import parse_behavior

    # computed dispatch: the label-headed section is only reachable via jump(Label=$State),
    # which no static walk resolves -- must NOT be flagged. (The set_reg is fallthrough-only, so
    # it carries no id -- an unreferenced id would be a separate, legitimate warning.)
    b = parse_behavior(
        "behavior T():\n\n"
        "jump(Label=$State)  >POP (next)\n"
        "n2: label(Label=v_arrow_up)\n"
        "set_reg(Value=1, Target=$B)  >POP (next)\n",
        argcache,
    )
    assert lint_behavior(b, argcache) == []


def test_lint_flags_unreferenced_declared_id(engine, argcache):
    from blz.desynced_toolkit.bsf.lint import lint_behavior
    from blz.desynced_toolkit.bsf.parse_text import parse_behavior

    # `stray` is given an id but nothing branches/jumps to it -- flagged. The entry node and the
    # target `hit` are not (entry is exempt; `hit` is referenced).
    b = parse_behavior(
        "behavior T():\n\n"
        "check_number(Value=$A, Compare=5)  >hit (If Larger) >NEXT (If Smaller) >NEXT (If Equal)\n"
        "stray: set_reg(Value=1, Target=$B)  >POP (next)\n"
        "hit: exit()\n",
        argcache,
    )
    warnings = lint_behavior(b, argcache)
    assert any("'stray' has an id but nothing references it" in w for w in warnings)
    assert not any("'hit'" in w for w in warnings)


def test_lint_does_not_flag_entry_or_label_ids(engine, argcache):
    from blz.desynced_toolkit.bsf.lint import lint_behavior
    from blz.desynced_toolkit.bsf.parse_text import parse_behavior

    # entry node named `start`, and a label reached only dynamically -- neither is a dangling id
    b = parse_behavior(
        "behavior T():\n\n"
        "start: jump(Label=$State)  >POP (next)\n"
        "spot: label(Label=v_arrow_up)\n"
        "set_reg(Value=1, Target=$B)  >POP (next)\n",
        argcache,
    )
    assert not any("nothing references it" in w for w in lint_behavior(b, argcache))


def test_lint_flags_literal_jump_without_matching_label(engine, argcache):
    from blz.desynced_toolkit.bsf.lint import lint_behavior
    from blz.desynced_toolkit.bsf.parse_text import parse_behavior

    b = parse_behavior(
        "behavior T():\n\n"
        "n1: label(Label=v_arrow_up[num=1])\n"
        "n2: jump(Label=v_arrow_up[num=2])\n",  # (id, num) mismatch: num=2 has no label
        argcache,
    )
    warnings = lint_behavior(b, argcache)
    assert any("no matching label" in w for w in warnings)


def test_lint_flags_undeclared_param_slot(engine, argcache):
    from blz.desynced_toolkit.bsf.ir import BsfBehavior, BsfNode
    from blz.desynced_toolkit.bsf.lint import lint_behavior
    from blz.desynced_toolkit.bsf.values import Param

    n = BsfNode(id="n1", op="set_reg", args={"Value": Param(5), "Target": Var("B")})
    n.branches["next"] = "POP"
    b = BsfBehavior(name="T", nodes={"n1": n}, order=["n1"])  # declares zero params
    warnings = lint_behavior(b, argcache)
    assert any("undeclared parameter slot 5" in w for w in warnings)


# -- comments and annotated rendering ---------------------------------------------------------


def test_full_line_and_trailing_comments_ignored(argcache):
    b = _parse(
        "# top comment\n"
        "behavior T():\n"
        "# after header\n\n"
        "n1: set_reg(Value=1, Target=$B)  # trailing note\n"
        "# between nodes\n"
        "n2: set_reg(Value=v_resource, Target=$C)  >n1 (next)  # note with >fake (Pin)\n",
        argcache,
    )
    assert b.order == ["n1", "n2"]
    assert b.nodes["n2"].branches["next"] == "n1"


def test_hash_inside_quoted_cmt_is_content_not_comment(argcache):
    b = _parse('behavior T():\n\nn1: set_reg(Value=1, Target=$B, cmt="see #4")\n', argcache)
    assert b.nodes["n1"].hidden["cmt"] == "see #4"


def test_annotated_render_parses_to_same_compile(engine, argcache):
    from pathlib import Path

    from blz.desynced_toolkit.bsf.decompile import decompile_dcs
    from blz.desynced_toolkit.lua_util import to_py

    raw = (Path(__file__).parent / "data" / "mining_leader.dcs").read_text().strip()
    b1 = decompile_dcs(engine, raw)
    annotated = render_behavior(b1, argcache, annotate=True)
    assert "# Copy" in annotated  # set_reg's in-game display name
    b2 = parse_behavior(annotated, argcache)
    assert to_py(compile_behavior(engine, b1, argcache)) == to_py(compile_behavior(engine, b2, argcache))


def test_id_display_names_scanned_from_registrations(argcache):
    names = argcache.id_display_names()
    assert names["c_radar"] == "Long-Range Radar"
    assert names["c_adv_miner"] == "Laser Mining Tool"
    assert names["v_resource"] == "Resource"


def test_annotate_translates_opaque_ids_but_not_obvious_ones(engine, argcache):
    from blz.desynced_toolkit.bsf.parse_text import parse_behavior

    b = parse_behavior(
        "behavior T():\n\n"
        "n1: set_reg(Value=c_radar, Target=$A)\n"
        "n2: set_reg(Value=v_resource, Target=$B)\n",
        argcache,
    )
    text = render_behavior(b, argcache, annotate=True)
    lines = text.split("\n")
    # id on its own line; the annotation rides on the instruction line below it
    n1_line = lines[lines.index("n1:") + 1]
    n2_line = lines[lines.index("n2:") + 1]
    assert 'c_radar="Long-Range Radar"' in n1_line  # opaque: annotated
    assert 'v_resource="Resource"' not in n2_line  # derivable by inspection: quiet


def test_cli_subcommands_smoke(engine, tmp_path):
    """Every CLI subcommand at least runs -- guards against an import falling out of
    __main__.py (semantic_diff_dcs did exactly that during the lint rework, caught live)."""
    import os
    from pathlib import Path

    from blz.desynced_toolkit.bsf.__main__ import main

    dcs = (Path(__file__).parent / "data" / "beacon.dcs").read_text().strip()
    src = tmp_path / "b.dcs"
    src.write_text(dcs)
    bsf_out = tmp_path / "b.bsf"
    dcs_out = tmp_path / "b2.dcs"
    gd = ["--game-data", os.environ.get("DESYNCED_GAME_DATA", str(Path(__file__).parent.parent.parent / "desynced-game-data"))]
    assert main(gd + ["decompile", "--input", str(src), "--output", str(bsf_out)]) == 0
    assert main(gd + ["compile", "--input", str(bsf_out), "--output", str(dcs_out)]) == 0
    assert main(gd + ["lint", "--input", str(src)]) == 0
    assert main(gd + ["semantic-diff", str(src), str(dcs_out)]) == 0
    assert main(gd + ["ids", "radar"]) == 0


def test_lint_flags_constant_jump_with_dead_next_edge(engine, argcache):
    from blz.desynced_toolkit.bsf.lint import lint_behavior
    from blz.desynced_toolkit.bsf.parse_text import parse_behavior

    b = parse_behavior(
        "behavior T():\n\n"
        "n1: label(Label=v_arrow_up)\n"
        "n2: jump(Label=v_arrow_up)\n"  # implicit fallthrough to n3 = dead edge
        "n3: set_reg(Value=1, Target=$B)  >POP (next)\n",
        argcache,
    )
    warnings = lint_behavior(b, argcache)
    assert any("dead edge" in w for w in warnings)

    # explicit POP: clean; dynamic label: exempt (next is the real no-match path)
    b2 = parse_behavior(
        "behavior T():\n\n"
        "n1: label(Label=v_arrow_up)\n"
        "n2: jump(Label=v_arrow_up)  >POP (next)\n"
        "n3: jump(Label=$State)\n"
        "n4: set_reg(Value=1, Target=$B)  >POP (next)\n",
        argcache,
    )
    assert not any("dead edge" in w for w in lint_behavior(b2, argcache))
