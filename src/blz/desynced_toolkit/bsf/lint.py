"""Legal-but-suspicious checks on a parsed/decompiled behavior -- things strict parsing
deliberately accepts (each has real, valid uses) but that are usually a mistake when they
appear unintentionally. Complements parse_text/compile's hard validation: those reject text
that cannot mean what was written; lint flags text that probably doesn't mean what was meant.

Run automatically (to stderr) by the CLI's `compile` path and on demand via the `lint`
subcommand -- built as part of the 2026-07-14 agent-ergonomics review, the same reasoning as
LLVM's verifier: the producer/consumer of this text is usually an agent iterating on tool
output, so every mistake caught locally is a game-load-and-observe cycle saved."""

from __future__ import annotations

from .argcache import ArgCache
from .ir import BsfBehavior
from .render_mermaid import _declared_pins, _resolve_pin_target
from .render_text import _jump_label_targets, _literal_key, referenced_node_ids
from .values import IdLit, Num, Param


def _forward_reachable(b: BsfBehavior, argcache: ArgCache, roots: set[str]) -> set[str]:
    jump_targets = _jump_label_targets(b.nodes)
    seen: set[str] = set()
    stack = [r for r in roots if r in b.nodes]
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        node = b.nodes[node_id]
        targets = [
            _resolve_pin_target(node_id, pin, node, b.order) for pin in _declared_pins(node, argcache)
        ]
        if node_id in jump_targets:
            targets.append(jump_targets[node_id])
        stack.extend(t for t in targets if t is not None and t not in seen)
    return seen


def _lint_one(b: BsfBehavior, argcache: ArgCache, prefix: str, warnings: list[str]) -> None:
    def warn(msg: str) -> None:
        warnings.append(f"{prefix}{msg}")

    # How a warning names a node. An id-less (fallthrough-only) node has only a hidden synthesized
    # id (`__nN`) that appears nowhere the user can see, so referencing it that way would be
    # useless -- describe it by op + its 1-based position in the listing instead. A node with a
    # real surface id is named by it.
    _pos = {nid: i + 1 for i, nid in enumerate(b.order)}

    def ref(nid: str) -> str:
        node = b.nodes[nid]
        return f"node {nid!r}" if node.id_explicit else f"the {node.op}() at listing position {_pos[nid]}"

    if b.order:
        # Roots: Program Start plus every label -- a label is reachable through computed
        # `jump(Label=$var)` dispatch that no static walk can resolve, so a label-headed
        # section is never "unreachable" (this mirrors render_mermaid's component logic:
        # such sections are separate components, not dead code).
        roots = {b.order[0]} | {n.id for n in b.nodes.values() if n.op == "label"}
        unreachable = [nid for nid in b.order if nid not in _forward_reachable(b, argcache, roots)]
        for nid in unreachable:
            warn(f"{ref(nid)} is unreachable (not reached from Program Start or any label)")

    # A literal jump whose (id, num) matches no label in this same behavior restarts the
    # program at runtime (jump falls back to Program Start on no match) -- almost always a
    # typo'd/renamed label rather than an intentional restart-via-jump.
    label_keys = {
        _literal_key(n.args["Label"])
        for n in b.nodes.values()
        if n.op == "label" and "Label" in n.args
    } - {None}
    for n in b.nodes.values():
        if n.op != "jump" or "Label" not in n.args:
            continue
        v = n.args["Label"]
        if not isinstance(v, (IdLit, Num)):
            continue  # computed dispatch -- genuinely unresolvable statically, not suspicious
        if _literal_key(v) not in label_keys:
            warn(f"{ref(n.id)} jumps to a literal label with no matching label node in this behavior")

    # A constant-Label jump always dispatches to its label -- its top-level `next` can never
    # fire, so a wired/fallthrough `next` there is a dead edge cluttering the visual editor
    # (user style rule, 2026-07-15: constant jumps always POP their next; a dynamic
    # `jump(Label=$var)`'s next is the real no-match path and is exempt).
    for n in b.nodes.values():
        if n.op != "jump":
            continue
        v = n.args.get("Label")
        if not isinstance(v, (IdLit, Num)):
            continue
        if _resolve_pin_target(n.id, "next", n, b.order) is not None:
            warn(
                f"{ref(n.id)}: constant jump never falls through -- its 'next' is a dead "
                f"edge; write >POP (next)"
            )

    # A node carrying a surface id that nothing references is either dead bookkeeping or a wiring
    # mistake -- once ids exist only where something targets them (optional node ids), a declared
    # yet-unreferenced id is anomalous. Two exemptions: the Program Start entry node (naming the
    # entry is legitimate, not a dangling anchor), and `label` nodes (a dispatch target by nature,
    # reachable via a dynamic `jump(Label=$x)` no static walk resolves). For a plain human note on
    # a node, use `cmt`, not an unwired id.
    if b.order:
        referenced = referenced_node_ids(b.nodes)
        entry = b.order[0]
        for nid in b.order:
            node = b.nodes[nid]
            if node.id_explicit and nid != entry and node.op != "label" and nid not in referenced:
                warn(
                    f"node {nid!r} has an id but nothing references it -- give the intended "
                    f"target the id, or drop it (use cmt for a human note)"
                )

    # A parameter-slot reference beyond the declared parameter list resolves to empty/0 at
    # runtime with no error -- the dangling-ref-via-copy-paste hazard (see
    # reference_dangling_param_ref_copy_paste): legal wire data, almost never intended.
    for n in b.nodes.values():
        for name, v in n.args.items():
            if isinstance(v, Param) and v.slot > len(b.params):
                warn(
                    f"{ref(n.id)} arg {name!r} references undeclared parameter slot {v.slot} "
                    f"(behavior declares {len(b.params)}) -- resolves to empty/0 at runtime"
                )


def lint_behavior(b: BsfBehavior, argcache: ArgCache) -> list[str]:
    """Returns human-readable warnings, empty when clean. Recurses into embedded subs."""
    warnings: list[str] = []
    _lint_one(b, argcache, "", warnings)
    for sub in b.subs:
        _lint_one(sub, argcache, f"sub {sub.name!r}: ", warnings)
    return warnings
