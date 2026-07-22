"""BsfBehavior -> Mermaid flowchart(s), for at-a-glance structural review (secondary to the BSF
text listing -- see docs/behavior_source_format.md's "Visualization" section). Renders one
behavior/sub level's own nodes as one diagram *per component* (see below); call again on each
`b.subs[i]` for a sub-behavior's own diagram(s).

Design, settled after several rejected drafts (each user-reviewed against a real rendered
diagram or a real behavior, not just described in prose):

- **Every declared exec pin gets a real, labeled edge -- connected or not.** An early draft
  only drew edges for pins actually present in `node.branches`; a node with an unconnected pin
  (or a pin never even added to the dict by hand-built IR) then looked identical to a node that
  doesn't have that pin at all, which is genuinely misleading -- you can't tell "this op
  doesn't have an If Smaller pin" from "this op has one and it's just not wired." Fixed by
  walking `data.instructions[op].args` (`arg_pin_names`, hence needing `argcache` as a real
  parameter now) rather than only `node.branches`.
- **A `POP` pin (see `argcache.resolve_branch`/`ir.py`'s `Branch` type for the rename from the
  misleading "STOP") routes to its own small, local marker node, not one shared terminal.** The
  very first draft sent every `POP` edge in the whole diagram into one shared node; on a real
  13-node graph this created several long edges all converging on one point -- pure visual
  noise for information that (per docs/behavior_source_format.md's "Loop-type instructions" section)
  isn't even trying to say anything more specific than "this pin doesn't go anywhere in *this*
  graph." A dedicated small marker per (node, pin) keeps each such edge short and local, with no
  implied relationship between unrelated dead ends.
- **Not attempted at all: labeling what a `POP` resolves to** (continue which loop, return to a
  caller, restart). Per `docs/behavior_source_format.md`'s "Loop-type instructions" section this is
  genuinely undecidable in general and would misrepresent resolved-but-uninteresting cases as
  needing a computed label just as much as truly ambiguous ones -- a marker just means "this pin
  pops," nothing more, matching the same restraint the text listing already applies.
- **The top-level "next" pin's own label is real, per-op data, not a generic placeholder.**
  `data.instructions[op].exec_arg` (a real field this project had not previously read, found via
  user correction): `false` means the op has no next pin at all -- `exit`/`restart`/`last` are
  never given one here either, matching the real editor exactly, since it's nonsensical to wire
  anything after them; a `{1, "Name", desc}` table names it for real (`check_number`'s `{1, "If
  Equal", ...}`); absent keeps the generic "next" (`argcache.next_pin_name`).
- **A pin is only ever labeled if the op has more than one -- and then *every* one of them is,
  unconditionally, including a plain implicit fallthrough.** Matches the real in-game editor
  (user-confirmed): it labels every pin when a node has several, and never labels a pin that's
  the only one a node has -- there's nothing to disambiguate. An earlier draft only labeled a
  pin when it happened to be wired explicitly or to a pop, leaving `check_number`'s "If Equal"
  silently unlabeled whenever it was left as a plain fallthrough -- wrong, since "If Equal" is
  one of three real pins on that node and needed disambiguating regardless of how it's wired.
  The edge itself is still always drawn either way -- an unlabeled pin isn't an invisible one,
  it just doesn't need a name next to it.
- **A plain implicit fallthrough is exactly as real and explicit an edge as any other one --
  never treated as a lesser or different kind of connection.** User-confirmed 2026-07-10:
  "physically next" is an artifact of the *wire encoding*, not of the behavior graph itself --
  omitting a `next`/exec value that happens to match the physically-next position is a compact
  encoding of a real, deliberate wire (see `argcache.resolve_branch`'s docstring and
  `docs/behavior_format.md`'s "Branch and fall-through resolution"), not "no decision was made." So
  this module never distinguishes "implicit fallthrough" from "explicit target" in anything that
  matters structurally (e.g. component membership below) -- only in the text listing, where
  adjacency is the actual visual cue a reader uses, is the distinction meaningful at all.
- **Default layout is top-to-bottom, despite the real in-game editor always laying a behavior
  out left-to-right** (every node's input pins on its left edge, output/exec pins on its right --
  user-confirmed 2026-07-10). Tried `flowchart LR` first, matching the real tool exactly, and
  rendered a real behavior with it (a 39-node graph, project memory/artifact from that session) --
  the result was strictly worse for this medium: Mermaid's own auto-layout has no equivalent of
  the real editor's own subgraph-bounding-box collision avoidance, so an LR render of anything
  with real branching sprawls very wide, and a Mermaid diagram is normally read by scrolling a
  browser page -- vertical scrolling is comfortable there, horizontal scrolling is not, unlike
  the real editor's own pan/zoom canvas. Exposed as a real `direction` parameter rather than
  hardcoded either way, since which reads better can depend on the specific behavior's shape.
- **A synthetic "Program Start" node feeds the first instruction, in the primary component
  only.** The real editor always draws this node, even though it's never part of the serialized
  wire data at all (implicit: "the first instruction" needs no explicit marker to encode).
  Mermaid's flowchart nodes are plain boxes with no named-port concept (unlike Graphviz's
  `record` shape), so the real editor's *per-node* left-input/right-output pin convention can't
  be replicated within a single node's box here regardless of direction.
- **One diagram per component, found by forward reachability from Program Start, not undirected
  connectivity.** Raised and refined 2026-07-10, directly motivated by a real behavior (Mining
  Leader V3.2, project memory) coming back as a *single* component under a first (rejected)
  undirected-connectivity draft: every one of its labeled state-machine sections
  (Search/Emergency/Travel-to-target/Monitor-mine), despite each being reachable only via an
  unresolvable *dynamic* `jump(Label=$State)`, also has its own *static* `jump` back to the
  Begin label when it finishes -- and undirected connectivity treats that return edge as
  sufficient to weld the whole graph into one piece, defeating the entire point. Forward
  reachability fixes this: starting from `b.order[0]`, walk only statically-known edges (every
  ordinary branch target, explicit or implicit-fallthrough -- see above -- plus, if
  `connect_resolved_jumps` is on, a resolved `jump->label` edge); everything reached this way is
  the primary component. Any remaining unvisited node starts a *new* component at the next
  not-yet-visited position in `b.order` -- which, per user-confirmed design, will be a `label`
  unless it's genuinely dead code (nothing reaches it at all, static or dynamic) worth showing
  as its own tiny honest component rather than silently dropping. An edge whose target lands
  outside the current component (e.g. Emergency's own return-to-Begin jump) is drawn to a small
  local "external reference" marker instead of either a broken edge or a false merge.
- **`connect_resolved_jumps` (default `True`) controls whether a *resolved* `jump->label` edge
  pulls its target into the same component, or is always treated as a component boundary --
  parameterized rather than fixed, since which reads better depends on the specific behavior.**
  With it on, sections statically reachable from each other via `jump`/`label` (not just
  Program Start) merge into one diagram (e.g. Search merging with Emergency in Mining Leader
  V3.2, since Search's own scan-found-an-enemy path statically jumps straight to Emergency).
  With it off, every `label` is its own component regardless of whether some other `jump`
  happens to resolve to it -- maximal splitting, one section per diagram, at the cost of more
  external-reference markers where sections used to be silently merged."""

from __future__ import annotations

import html

from .argcache import ArgCache, arg_pin_names
from .ir import BsfBehavior
from .render_text import _jump_label_targets, render_value

_PROGRAM_START_ID = "__program_start__"


def _next_in_order(node_id: str, order: list[str]) -> str | None:
    idx = order.index(node_id)
    return order[idx + 1] if idx + 1 < len(order) else None


def _declared_pins(node, argcache: ArgCache) -> list[str]:
    """The full set of exec pin names an op declares, per `data.instructions[op].args`/
    `exec_arg` -- not just whatever keys happen to be present in `node.branches`. A pin's key
    can be legitimately absent (real decompiled data always populates every declared exec key
    per decompile.py's own invariant, but hand-built IR isn't guaranteed to), and an absent key
    still means a real, resolved edge (see module docstring) -- iterating `node.branches`
    directly would silently miss that."""
    pins = [pin for _, atype, pin in arg_pin_names(node.op, argcache) if atype == "exec"]
    if argcache.next_pin_name(node.op) is not None:
        pins.append("next")
    return pins


def _resolve_pin_target(node_id: str, pin: str, node, order: list[str]) -> str | None:
    """The real target node id for one pin, or None for POP/no target. Implicit fallthrough
    (an absent/None branch value resolving to the physically-next node) is resolved here exactly
    like an explicit target -- see module docstring on why that's not a distinction worth
    keeping past this point."""
    target = node.branches.get(pin)
    if target is None:
        return _next_in_order(node_id, order)
    if target == "POP":
        return None
    return target


def _components(
    b: BsfBehavior, argcache: ArgCache, jump_targets: dict[str, str], connect_resolved_jumps: bool
) -> list[list[str]]:
    """Components by forward reachability from each new entry point, not undirected
    connectivity -- see module docstring for why undirected connectivity is the wrong tool here.
    The first entry point is always `b.order[0]` (Program Start's own target); every subsequent
    one is the next not-yet-visited node encountered walking `b.order` in sequence, which will
    be a `label` unless it's genuinely unreachable dead code. Returns components in that same
    discovery order, each internally ordered to match `b.order`."""
    visited: set[str] = set()
    components: list[list[str]] = []

    for start in b.order:
        if start in visited:
            continue
        stack = [start]
        member_set: set[str] = set()
        while stack:
            node_id = stack.pop()
            # Check the *global* `visited` set too, not just this walk's own `member_set` --
            # otherwise a later component's walk that happens to reach an already-claimed node
            # (e.g. a section's own return-to-Begin jump reaching back into the primary
            # component) would absorb it and then re-walk that entire earlier component all
            # over again from there, producing overlapping, oversized "components" that aren't
            # disjoint at all. A node belonging to an earlier component is a real edge target
            # (rendered as an external reference in `_render_component`), never grounds to
            # re-claim it into this one.
            if node_id in member_set or node_id in visited:
                continue
            member_set.add(node_id)
            node = b.nodes[node_id]
            for pin in _declared_pins(node, argcache):
                target = _resolve_pin_target(node_id, pin, node, b.order)
                if target is not None and target not in member_set:
                    stack.append(target)
            if connect_resolved_jumps and node_id in jump_targets:
                target = jump_targets[node_id]
                if target not in member_set:
                    stack.append(target)
        visited |= member_set
        components.append([nid for nid in b.order if nid in member_set])

    return components


def _component_title(component: list[str], b: BsfBehavior) -> str:
    """Non-primary components can only be reached via a `jump` targeting a `label` at their own
    start (see module docstring) -- use that label's own `cmt`/`Label` value as a human-meaningful
    title. Falls back to the entry node's own id for the (authorable, if unusual) dead-code case
    of a component with no real label at its start."""
    entry = b.nodes[component[0]]
    if entry.op == "label":
        cmt = entry.hidden.get("cmt")
        if cmt:
            return str(cmt)
        label_val = entry.args.get("Label")
        if label_val is not None:
            return render_value(label_val, b.params)
    return component[0]


def _render_component(
    component: list[str],
    b: BsfBehavior,
    argcache: ArgCache,
    jump_targets: dict[str, str],
    title: str,
    is_primary: bool,
    direction: str,
) -> str:
    member_set = set(component)
    lines = [f"%% {title}", f"flowchart {direction}"]

    node_lines = []
    edge_lines = []
    marker_lines = []
    pop_count = 0
    ref_count = 0

    if is_primary:
        node_lines.append(f'  {_PROGRAM_START_ID}(["Program Start"])')
        edge_lines.append(f"  {_PROGRAM_START_ID} --> n{component[0]}")

    for node_id in component:
        node = b.nodes[node_id]
        args_str = ", ".join(f"{name}={render_value(v, b.params)}" for name, v in node.args.items())
        # HTML-escaped, not just quote-substituted (a prior version only did `"`->`'`, which
        # left a raw HTML tag in e.g. a maliciously-renamed variable's value intact in the
        # generated .mmd source -- found 2026-07-10 building an adversarial test fixture whose
        # whole point was to check for exactly this. Mermaid's own quoted-label syntax already
        # expects HTML entities for special characters, so this is also the *correct* escaping,
        # not just the safe one -- it doubles as protecting the label's own `"..."` delimiters.
        label = html.escape(f"{node_id}: {node.op}({args_str})")
        node_lines.append(f'  n{node_id}["{label}"]')

        pins = [
            (pin, node.branches.get(pin))
            for _, atype, pin in arg_pin_names(node.op, argcache)
            if atype == "exec"
        ]
        next_pin = argcache.next_pin_name(node.op)
        if next_pin is not None:
            pins.append((next_pin, node.branches.get("next")))
        show_labels = len(pins) > 1

        for pin, _ in pins:
            edge_label = pin if show_labels else None
            target = _resolve_pin_target(node_id, pin, node, b.order)
            if target is None:
                pop_count += 1
                marker_id = f"pop{pop_count}"
                marker_lines.append(f'  {marker_id}((" ")):::popMarker')
                arrow = f"-.->|{edge_label}|" if edge_label else "-.->"
                edge_lines.append(f"  n{node_id} {arrow} {marker_id}")
            elif target in member_set:
                arrow = f"-->|{edge_label}|" if edge_label else "-->"
                edge_lines.append(f"  n{node_id} {arrow} n{target}")
            else:
                # Resolves to a real node, just not one in *this* diagram -- e.g. a section's
                # own return-to-Begin jump. A small local marker naming the real target, never a
                # broken edge into a node this diagram doesn't define and never a false merge.
                ref_count += 1
                ref_id = f"ref{ref_count}"
                # The label text is for a human to read, not a Mermaid node id -- `target` is
                # already the real node id (e.g. "n11"); the "n" prefix used for Mermaid's own
                # internal ids elsewhere (`n{node_id}`) doesn't belong here too.
                marker_lines.append(f'  {ref_id}(["↗ {target}"]):::refMarker')
                arrow = f"-.->|{edge_label}|" if edge_label else "-.->"
                edge_lines.append(f"  n{node_id} {arrow} {ref_id}")

        if node_id in jump_targets:
            jt = jump_targets[node_id]
            if jt in member_set:
                edge_lines.append(f"  n{node_id} -.->|resolved jump| n{jt}")
            else:
                ref_count += 1
                ref_id = f"ref{ref_count}"
                marker_lines.append(f'  {ref_id}(["↗ {jt}"]):::refMarker')
                edge_lines.append(f"  n{node_id} -.->|resolved jump| {ref_id}")

    lines.extend(node_lines)
    lines.extend(marker_lines)
    lines.extend(edge_lines)
    if any(":::popMarker" in ln for ln in marker_lines):
        lines.append("  classDef popMarker fill:#888,stroke:#888,stroke-width:0px")
    if any(":::refMarker" in ln for ln in marker_lines):
        lines.append("  classDef refMarker fill:#3a4a5c,stroke:#5fb3d9,stroke-width:1px")
    return "\n".join(lines)


def render_mermaid(
    b: BsfBehavior,
    argcache: ArgCache,
    title: str | None = None,
    direction: str = "TD",
    connect_resolved_jumps: bool = True,
) -> list[str]:
    """One Mermaid `flowchart` block per component of `b` -- see module docstring for the full
    reasoning on why components are found by forward reachability rather than a single combined
    diagram or undirected connectivity. Empty behavior -> empty list.

    `direction`: any real Mermaid flowchart direction ("TD", "LR", "BT", "RL") -- default TD,
    a deliberate mismatch with the real editor's own LR convention (see module docstring).
    `connect_resolved_jumps`: whether a statically-resolved `jump->label` edge merges its
    target into the same component (default) or is always treated as a component boundary."""
    jump_targets = _jump_label_targets(b.nodes)
    components = _components(b, argcache, jump_targets, connect_resolved_jumps)
    if not components:
        return []

    primary = components[0] if b.order else None
    diagrams = []
    for component in components:
        is_primary = component is primary
        comp_title = (title or b.name) if is_primary else _component_title(component, b)
        diagrams.append(
            _render_component(
                component, b, argcache, jump_targets, comp_title, is_primary, direction
            )
        )
    return diagrams
