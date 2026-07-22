"""Semantic diff between two independently-compiled `.dcs` strings -- normalizes away
position-dependent wire encoding (omission-vs-explicit branch targets, and raw wire/array
order) so a diff shows only genuinely meaningful changes: added/removed nodes, changed
args/hidden fields, and branch targets that now resolve somewhere structurally different.

Built because the real in-game editor's own compiler recomputes both of those (which branches
get written as an omitted vs. explicit int, and the relative wire-array order of untouched
nodes) fresh on every save, for the *whole* graph -- confirmed the hard way reviewing a user's
real re-save, where two of five apparent "changes" turned out to be pure re-serialization
artifacts of edits made elsewhere, not anything the user touched (see project memory
`feedback_resave_reencodes_unrelated_wiring` / `reference_omission_vs_false_wire_semantics`).
A raw decompiled-text diff between two real saves is exactly as unreliable as treating omission
itself as "less real" than an explicit target -- both conflate a position-dependent encoding
choice with semantic content. This module removes wire position from the comparison entirely.

Approach: reduce each `BsfBehavior` to a canonical, wire-position-independent sequence of node
*content* signatures (op + args + hidden fields, deliberately excluding branch targets, since a
target's identity is only meaningful in terms of another node -- exactly the circular thing this
exists to resolve). The sequence order is a deterministic pre-order walk from Program Start,
following declared exec pins and resolved `jump`/`label` edges, so two structurally-identical
graphs produce the identical sequence regardless of what wire positions the compiler happened
to assign this time. `difflib.SequenceMatcher` aligns the two sequences (robust to local
insertions/deletions, unlike a positional/index comparison); only once that alignment exists are
branch targets compared, by mapping each pin's resolved target through the established
node-to-node correspondence rather than comparing raw ids."""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from .argcache import ArgCache
from .ir import BsfBehavior, BsfNode
from .render_mermaid import _declared_pins, _resolve_pin_target
from .render_text import _jump_label_targets, render_hidden_value, render_value


def _canonical_order(b: BsfBehavior, argcache: ArgCache) -> list[str]:
    """Node ids in a deterministic, wire-position-independent order: pre-order DFS from Program
    Start (`b.order[0]`) following declared exec pins (in declaration order) and resolved
    `jump`/`label` edges, then any remaining unreachable/dead-code nodes appended in original
    wire order (deterministic leftover, not meant to imply anything about their relationship)."""
    if not b.order:
        return []
    jump_targets = _jump_label_targets(b.nodes)
    visited: set[str] = set()
    result: list[str] = []

    def visit(node_id: str | None) -> None:
        if node_id is None or node_id in visited or node_id not in b.nodes:
            return
        visited.add(node_id)
        result.append(node_id)
        node = b.nodes[node_id]
        for pin in _declared_pins(node, argcache):
            visit(_resolve_pin_target(node_id, pin, node, b.order))
        if node_id in jump_targets:
            visit(jump_targets[node_id])

    visit(b.order[0])
    for node_id in b.order:  # dead code unreachable from Program Start, if any
        visit(node_id)
    return result


def _node_signature(node: BsfNode, params) -> str:
    """Content-only signature -- deliberately excludes branch targets (compared separately,
    once a node correspondence exists via the sequence alignment)."""
    parts = [f"{name}={render_value(v, params)}" for name, v in node.args.items()]
    parts += [f"{name}={render_hidden_value(v)}" for name, v in node.hidden.items()]
    return f"{node.op}({', '.join(parts)})"


@dataclass
class NodeDiff:
    kind: str  # "added" / "removed" / "changed" / "branch_changed"
    old_id: str | None
    new_id: str | None
    detail: str


def _diff_nodes(old: BsfBehavior, new: BsfBehavior, argcache: ArgCache) -> list[NodeDiff]:
    old_order = _canonical_order(old, argcache)
    new_order = _canonical_order(new, argcache)
    old_sigs = [_node_signature(old.nodes[nid], old.params) for nid in old_order]
    new_sigs = [_node_signature(new.nodes[nid], new.params) for nid in new_order]

    sm = difflib.SequenceMatcher(a=old_sigs, b=new_sigs, autojunk=False)
    old_to_new: dict[str, str] = {}
    diffs: list[NodeDiff] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                old_to_new[old_order[i1 + k]] = new_order[j1 + k]
        elif tag == "replace":
            # Best-effort local pairing within the replace block: report as "changed" rather
            # than a remove+add when both sides have a node at this position.
            n = min(i2 - i1, j2 - j1)
            for k in range(n):
                oid, nid = old_order[i1 + k], new_order[j1 + k]
                old_to_new[oid] = nid
                diffs.append(NodeDiff("changed", oid, nid, f"{old_sigs[i1 + k]!r} -> {new_sigs[j1 + k]!r}"))
            for k in range(n, i2 - i1):
                diffs.append(NodeDiff("removed", old_order[i1 + k], None, old_sigs[i1 + k]))
            for k in range(n, j2 - j1):
                diffs.append(NodeDiff("added", None, new_order[j1 + k], new_sigs[j1 + k]))
        elif tag == "delete":
            for k in range(i1, i2):
                diffs.append(NodeDiff("removed", old_order[k], None, old_sigs[k]))
        elif tag == "insert":
            for k in range(j1, j2):
                diffs.append(NodeDiff("added", None, new_order[k], new_sigs[k]))

    for oid, nid in old_to_new.items():
        onode, nnode = old.nodes[oid], new.nodes[nid]
        if onode.op != nnode.op:
            continue  # already reported as "changed" above; target semantics not comparable
        for pin in _declared_pins(onode, argcache):
            otarget = _resolve_pin_target(oid, pin, onode, old.order)
            ntarget = _resolve_pin_target(nid, pin, nnode, new.order)
            if (otarget is None) != (ntarget is None):
                diffs.append(NodeDiff(
                    "branch_changed", oid, nid,
                    f"pin {pin!r}: {'POP' if otarget is None else otarget} -> "
                    f"{'POP' if ntarget is None else ntarget}",
                ))
            elif otarget is not None and ntarget is not None:
                expected_new = old_to_new.get(otarget)
                if expected_new != ntarget:
                    diffs.append(NodeDiff(
                        "branch_changed", oid, nid,
                        f"pin {pin!r} now resolves to a different node "
                        f"(expected the match of {otarget!r}, got {ntarget!r})",
                    ))
    return diffs


def _diff_meta(old: BsfBehavior, new: BsfBehavior) -> list[str]:
    lines = []
    if old.name != new.name:
        lines.append(f"name: {old.name!r} -> {new.name!r}")
    old_params = [p.name for p in old.params]
    new_params = [p.name for p in new.params]
    if old_params != new_params:
        lines.append(f"params: {old_params!r} -> {new_params!r}")
    if old.desc != new.desc:
        lines.append(f"desc: {old.desc!r} -> {new.desc!r}")
    if old.keepvars != new.keepvars:
        lines.append(f"keepvars: {old.keepvars!r} -> {new.keepvars!r}")
    if old.keeparrays != new.keeparrays:
        lines.append(f"keeparrays: {old.keeparrays!r} -> {new.keeparrays!r}")
    return lines


def _format_behavior_diff(label: str, old: BsfBehavior, new: BsfBehavior, argcache: ArgCache) -> list[str]:
    out: list[str] = []
    meta = _diff_meta(old, new)
    node_diffs = _diff_nodes(old, new, argcache)

    if not meta and not node_diffs:
        return []

    out.append(f"=== {label} ===")
    for line in meta:
        out.append(f"  {line}")
    for d in node_diffs:
        if d.kind == "added":
            out.append(f"  + [{d.new_id}] {d.detail}")
        elif d.kind == "removed":
            out.append(f"  - [{d.old_id}] {d.detail}")
        elif d.kind == "changed":
            out.append(f"  ~ [{d.old_id} -> {d.new_id}] {d.detail}")
        elif d.kind == "branch_changed":
            out.append(f"  ~ [{d.old_id} -> {d.new_id}] {d.detail}")
    return out


def semantic_diff_behaviors(old: BsfBehavior, new: BsfBehavior, argcache: ArgCache) -> str:
    """Human-readable semantic diff between two decompiled behaviors, recursing into
    sub-behaviors (matched by name, falling back to position for unnamed/colliding names).
    Returns an empty string if nothing meaningful differs."""
    sections = _format_behavior_diff("top-level", old, new, argcache)

    old_subs_by_name: dict[str, list[BsfBehavior]] = {}
    for s in old.subs:
        old_subs_by_name.setdefault(s.name, []).append(s)
    used_old: set[int] = set()

    for new_idx, new_sub in enumerate(new.subs):
        candidates = old_subs_by_name.get(new_sub.name, [])
        old_sub = None
        for cand in candidates:
            if id(cand) not in used_old:
                old_sub = cand
                used_old.add(id(cand))
                break
        if old_sub is None:
            if new_idx < len(old.subs) and id(old.subs[new_idx]) not in used_old:
                old_sub = old.subs[new_idx]
                used_old.add(id(old_sub))
        if old_sub is None:
            sections.append(f"=== sub {new_sub.name!r} ===\n  + entire sub-behavior added")
            continue
        sections.extend(_format_behavior_diff(f"sub {new_sub.name!r}", old_sub, new_sub, argcache))

    for old_sub in old.subs:
        if id(old_sub) not in used_old:
            sections.append(f"=== sub {old_sub.name!r} ===\n  - entire sub-behavior removed")

    return "\n".join(sections)


def semantic_diff_dcs(engine, old_dcs: str, new_dcs: str) -> str:
    """`.dcs` string pair -> human-readable semantic diff, in one call."""
    from .decompile import decompile_dcs

    argcache = ArgCache(engine)
    old = decompile_dcs(engine, old_dcs)
    new = decompile_dcs(engine, new_dcs)
    return semantic_diff_behaviors(old, new, argcache)
