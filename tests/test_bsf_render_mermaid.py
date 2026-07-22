"""bsf/render_mermaid.py: a pin is labeled only when its op has more than one -- matching the
real in-game editor (user-confirmed), which never bothers naming a node's only pin but always
names every pin once a node has several, wired or not. The edge itself is always drawn either
way; only the label is conditional."""

from pathlib import Path

from blz.desynced_toolkit.bsf.argcache import ArgCache
from blz.desynced_toolkit.bsf.decompile import decompile_dcs
from blz.desynced_toolkit.bsf.ir import BsfBehavior, BsfNode
from blz.desynced_toolkit.bsf.render_mermaid import render_mermaid
from blz.desynced_toolkit.bsf.values import IdLit, Num, Var

DATA_DIR = Path(__file__).parent / "data"


def _compare_number_demo():
    a = BsfNode(id="A", op="check_number", args={"Value": Var("x"), "Compare": Var("y")})
    a.branches["If Larger"] = "B"
    a.branches["If Smaller"] = "POP"
    # "next" (If Equal) left as a plain implicit fallthrough -- C is physically next.
    c = BsfNode(id="C", op="set_reg", args={"Value": Num(2), "Target": Var("x")})
    b = BsfNode(id="B", op="set_reg", args={"Value": Num(1), "Target": Var("x")})
    return BsfBehavior(name="CompareNumberDemo", nodes={"A": a, "B": b, "C": c}, order=["A", "C", "B"])


def test_multi_pin_op_labels_every_pin_including_implicit_fallthrough(engine):
    (mmd,) = render_mermaid(_compare_number_demo(), ArgCache(engine))
    assert "nA -->|If Larger| nB" in mmd
    assert "nA -.->|If Smaller|" in mmd
    # The real point of this test: check_number has 3 real pins (If Larger, If Smaller, If
    # Equal), so even though "If Equal" is a plain implicit fallthrough (not explicitly wired),
    # it must still be labeled -- an earlier draft left it silently unlabeled here.
    assert "nA -->|If Equal| nC" in mmd


def test_single_pin_op_edges_stay_unlabeled_but_present(engine):
    (mmd,) = render_mermaid(_compare_number_demo(), ArgCache(engine))
    # set_reg has exactly one exec pin (the generic "next") -- never labeled, matching the real
    # editor not bothering to name a node's only pin -- but the edges must still be drawn.
    assert "nC --> nB" in mmd
    assert "nC -->|" not in mmd
    pop_lines = [line for line in mmd.split("\n") if line.strip().startswith("nB -.->")]
    assert len(pop_lines) == 1
    assert "|" not in pop_lines[0]  # unlabeled -- no pin name text on this edge
    assert "pop" in pop_lines[0]  # but the dashed edge to its marker is still there


def test_exit_restart_last_draw_no_next_edge_at_all(engine):
    argcache = ArgCache(engine)
    node = BsfNode(id="n1", op="exit")
    behavior = BsfBehavior(name="ExitTest", nodes={"n1": node}, order=["n1"])
    (mmd,) = render_mermaid(behavior, argcache)
    outgoing = [line for line in mmd.split("\n") if line.strip().startswith("n1 ")]
    assert outgoing == []


def test_top_to_bottom_with_program_start_node(engine):
    """A synthetic "Program Start" node -- never part of the serialized wire data, since it's
    implicitly "whichever instruction is first" -- is always drawn feeding into the first real
    instruction, matching the real in-game editor (user-confirmed 2026-07-10). Layout stays
    top-to-bottom despite the real editor always laying a behavior out left-to-right: tried LR
    first and rendered a real 39-node behavior with it, which sprawled far too wide for a
    diagram meant to be read by scrolling a browser page (see render_mermaid.py's module
    docstring for the full reasoning) -- TD was a deliberate revert, not an oversight."""
    (mmd,) = render_mermaid(_compare_number_demo(), ArgCache(engine))
    assert "flowchart TD" in mmd
    assert '__program_start__(["Program Start"])' in mmd
    assert "__program_start__ --> nA" in mmd  # A is first in this fixture's `order`


def test_program_start_omitted_for_empty_behavior(engine):
    behavior = BsfBehavior(name="Empty", nodes={}, order=[])
    diagrams = render_mermaid(behavior, ArgCache(engine))
    assert diagrams == []


def _disconnected_behavior():
    """Two genuinely separate chains: A->B (the real program, reached from Program Start), and
    a labeled section Search->Done, reachable only via a *dynamic* `jump(Label=$State)` from B --
    the real shape a state-machine-style behavior takes (see Mining Leader V3.2, project memory).
    The jump target is a variable specifically so `_jump_label_targets` can't resolve it -- a
    literal jump target would create a real connecting edge instead (see the "static jump"
    test below), which would defeat the point of this fixture."""
    a = BsfNode(id="A", op="set_reg", args={"Value": Num(1), "Target": Var("x")})
    b = BsfNode(id="B", op="jump", args={"Label": Var("State")})
    # A real author always wires the "no state matched" fallback explicitly to POP rather than
    # leaving it to an implicit fallthrough onto whatever happens to sit physically next (see
    # Mining Leader V3.2's own `n14: jump(Label=State)  >POP (next)`, project memory) -- without
    # this, B's omitted "next" would implicitly connect to L purely because L is physically next
    # in `order`, defeating the point of this fixture.
    b.branches["next"] = "POP"
    label = BsfNode(id="L", op="label", args={"Label": IdLit("v_search")}, hidden={"cmt": "Search"})
    done = BsfNode(id="D", op="last")
    return BsfBehavior(
        name="TwoComponents",
        nodes={"A": a, "B": b, "L": label, "D": done},
        order=["A", "B", "L", "D"],
    )


def test_disconnected_components_render_as_separate_diagrams(engine):
    argcache = ArgCache(engine)
    diagrams = render_mermaid(_disconnected_behavior(), argcache)
    assert len(diagrams) == 2

    primary = next(d for d in diagrams if "Program Start" in d)
    other = next(d for d in diagrams if d is not primary)

    assert "nA" in primary and "nB" in primary
    assert "nL" not in primary and "nD" not in primary  # the two chains stay fully separate

    assert "nL" in other and "nD" in other
    assert "nA" not in other and "nB" not in other
    assert "Program Start" not in other  # only the primary component gets the synthetic marker


def test_non_primary_component_titled_from_its_own_label(engine):
    """Only the primary component's title comes from the behavior's own name -- every other
    component is titled from its own entry label's `cmt` (or Label value as a fallback), giving
    a human-meaningful title instead of an arbitrary index (user-confirmed design, 2026-07-10)."""
    argcache = ArgCache(engine)
    diagrams = render_mermaid(_disconnected_behavior(), argcache)
    other = next(d for d in diagrams if "Program Start" not in d)
    assert other.startswith("%% Search")  # from the label node's own cmt="Search"


def test_static_jump_to_label_connects_components_that_share_it(engine):
    """A statically-resolved (literal) jump->label edge IS a real connection for component
    purposes -- contrast with `_disconnected_behavior` above, where the jump target is a
    variable specifically so it *doesn't* connect. Every branch here is wired explicitly
    (never relying on an omitted/implicit-fallthrough-to-physically-next edge, and C is placed
    non-adjacent to B in `order`), so the *only* possible connection between B and C is the
    jump->label edge itself -- isolating the claim instead of confounding it with adjacency."""
    a = BsfNode(id="A", op="set_reg", args={"Value": Num(1), "Target": Var("x")})
    a.branches["next"] = "B"
    b = BsfNode(id="B", op="jump", args={"Label": IdLit("v_target")})
    b.branches["next"] = "POP"
    c = BsfNode(id="C", op="label", args={"Label": IdLit("v_target")})
    c.branches["next"] = "POP"
    behavior = BsfBehavior(name="StaticJumpTest", nodes={"A": a, "C": c, "B": b}, order=["A", "C", "B"])

    argcache = ArgCache(engine)
    diagrams = render_mermaid(behavior, argcache)
    assert len(diagrams) == 1  # not 2 -- the static jump->label edge unions them
    assert "resolved jump" in diagrams[0]


def test_components_are_disjoint_and_cover_every_node(engine):
    """Regression test for a real bug found 2026-07-10: the forward-reachability walk only
    checked its own local `member_set` to avoid re-visiting, never the *global* `visited` set
    from earlier components -- so a later component's walk that reached an already-claimed node
    (e.g. a section's own return-to-Begin jump) would absorb it and re-walk that entire earlier
    component all over again, producing overlapping components whose sizes summed to more than
    `len(b.order)`. Uses the real Mining Leader V3.2 fixture, whose Search/Emergency/Travel-to-
    target/Monitor-mine sections all jump back to Begin -- exactly the shape that triggered it."""
    argcache = ArgCache(engine)
    raw = (DATA_DIR / "mining_leader.dcs").read_text().strip()
    b = decompile_dcs(engine, raw)

    for connect in (True, False):
        diagrams = render_mermaid(b, argcache, connect_resolved_jumps=connect)
        seen: set[str] = set()
        total_nodes = 0
        for d in diagrams:
            node_ids = {line.split('["')[0].strip()[1:] for line in d.split("\n") if line.strip().startswith("n") and '["' in line}
            assert seen.isdisjoint(node_ids), f"overlap with connect_resolved_jumps={connect}"
            seen |= node_ids
            total_nodes += len(node_ids)
        assert total_nodes == len(b.order), f"lost or duplicated nodes with connect_resolved_jumps={connect}"


def _behavior_with_a_return_edge():
    """A -> B (primary, reached from Program Start), and a separate section L (a `label`,
    reachable only via a dynamic jump from B) that itself jumps back to A when done -- the same
    "section returns to the main loop" shape every labeled section in Mining Leader V3.2 has."""
    a = BsfNode(id="A", op="set_reg", args={"Value": Num(1), "Target": Var("x")})
    b = BsfNode(id="B", op="jump", args={"Label": Var("State")})
    b.branches["next"] = "POP"
    label = BsfNode(id="L", op="label", args={"Label": IdLit("v_search")}, hidden={"cmt": "Search"})
    ret = BsfNode(id="R", op="jump", args={"Label": IdLit("v_begin")})
    ret.branches["next"] = "A"  # explicit return edge back into the primary component
    return BsfBehavior(name="ReturnEdgeTest", nodes={"A": a, "B": b, "L": label, "R": ret}, order=["A", "B", "L", "R"])


def test_external_reference_marker_for_edge_leaving_a_component(engine):
    argcache = ArgCache(engine)
    diagrams = render_mermaid(_behavior_with_a_return_edge(), argcache)
    assert len(diagrams) == 2
    other = next(d for d in diagrams if "Program Start" not in d)
    # R's "next" points back at A, which lives in the primary component -- must render as a
    # local external-reference marker naming the real target ("A", not the doubled "nA"), never
    # a broken edge into a node this diagram never defines and never a merge into one component.
    assert 'ref1(["↗ A"]):::refMarker' in other
    assert "nA" not in other  # A itself is never pulled into this component
    assert "nR -.-> ref1" in other


def test_connect_resolved_jumps_toggle(engine):
    """With `connect_resolved_jumps=True` (default), a resolved jump->label edge merges its
    target into the same component as its source. With it off, every `label` is its own
    component regardless -- real, user-requested parameterization, not just an internal
    implementation detail (2026-07-10)."""
    a = BsfNode(id="A", op="set_reg", args={"Value": Num(1), "Target": Var("x")})
    a.branches["next"] = "B"
    b = BsfNode(id="B", op="jump", args={"Label": IdLit("v_target")})
    b.branches["next"] = "POP"
    c = BsfNode(id="C", op="label", args={"Label": IdLit("v_target")})
    c.branches["next"] = "POP"
    behavior = BsfBehavior(name="ToggleTest", nodes={"A": a, "C": c, "B": b}, order=["A", "C", "B"])

    argcache = ArgCache(engine)
    assert len(render_mermaid(behavior, argcache, connect_resolved_jumps=True)) == 1
    assert len(render_mermaid(behavior, argcache, connect_resolved_jumps=False)) == 2


def test_direction_parameter(engine):
    (mmd,) = render_mermaid(_compare_number_demo(), ArgCache(engine), direction="LR")
    assert "flowchart LR" in mmd
    assert "flowchart TD" not in mmd


def test_adversarial_fixture_html_escaped_not_raw(engine):
    """Regression test for a real bug found 2026-07-10 building this deliberately adversarial
    fixture (a variable renamed to an HTML/XSS payload, `<b onmouseover="alert('xss')">...`): an
    earlier version of `_render_component` only replaced `"` with `'` in a node's label, leaving
    the actual `<b onmouseover=...>` tag intact in the generated .mmd source -- a real
    unescaped-HTML-in-generated-diagram issue for a pipeline whose own established workflow
    renders .mmd through a real mermaid.js instance inlined into a browser-viewed Artifact (see
    project memory). `html.escape` neutralizes the tag AND correctly protects the label's own
    quote delimiters, so this checks both that the raw tag never appears and that its escaped
    form does."""
    argcache = ArgCache(engine)
    raw = (DATA_DIR / "adversarial_text_stress.dcs").read_text().strip()
    b = decompile_dcs(engine, raw)
    diagrams = render_mermaid(b, argcache)
    combined = "\n".join(diagrams)
    assert "<b onmouseover=" not in combined
    assert "&lt;b onmouseover=" in combined
