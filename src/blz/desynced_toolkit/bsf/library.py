"""On-disk library store: a directory of BSF files, one per named behavior, where a shared
sub-behavior lives in its own file and every caller references it by name instead of embedding
its own copy (docs/behavior_source_format.md's "Sub-behaviors by reference"). Mirrors the game's
own by-reference saved-library semantics -- editing a shared sub in-game updates every caller --
which plain per-clipboard-export BSF/`.dcs` files can't represent at all (a `dependencies[]` array
is always a full, disconnected copy the moment it's exported, see the "Local behavior-library
storage" motivation in the consuming repo's todo.md).

`import_dcs` is the decompile-and-split direction (fresh clipboard export -> library files),
`export_dcs` is the reverse (library files -> a real `.dcs` string, resolving references back
into an embedded `dependencies[]` array via `parse_behavior`'s own `base_dir` support -- no
special-casing needed on the compile side at all)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .argcache import ArgCache
from .compile import compile_dcs
from .decompile import decompile_dcs
from .ir import BsfBehavior
from .parse_text import parse_behavior
from .render_text import render_behavior
from .semantic_diff import semantic_diff_behaviors

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(name: str) -> str:
    """Library filename stem for a behavior's declared name -- kebab-case, e.g. 'Async Radar Set'
    -> 'async-radar-set'. Not round-trip data (the file's own `behavior`/`sub` header keeps the
    real name); purely the on-disk join key alongside it."""
    return _SLUG_RE.sub("-", name.strip().lower()).strip("-") or "x"


@dataclass
class ImportReport:
    """What `import_dcs` did, for the CLI (or a caller) to summarize. Every path is relative to
    the library directory that was imported into."""

    behavior_path: Path
    written: list[Path] = field(default_factory=list)
    updated: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    diffs: dict[Path, str] = field(default_factory=dict)
    # sub file -> other library files (not this import's own top-level/sub files) whose own
    # `from` reference now points at content that changed underneath them -- informational only;
    # nothing here can be fixed automatically without a fresh clipboard export of that caller too.
    stale_callers: dict[Path, list[Path]] = field(default_factory=dict)


def _write_if_changed(
    path: Path, text: str, argcache: ArgCache, library_dir: Path, report: ImportReport
) -> None:
    if path.exists():
        old_text = path.read_text()
        if old_text == text:
            report.unchanged.append(path)
            return
        old = parse_behavior(old_text, argcache, base_dir=library_dir)
        new = parse_behavior(text, argcache, base_dir=library_dir)
        diff = semantic_diff_behaviors(old, new, argcache)
        report.updated.append(path)
        if diff:
            report.diffs[path] = diff
    else:
        report.written.append(path)
    path.write_text(text)


def _extract_subs(
    b: BsfBehavior, library_dir: Path, argcache: ArgCache, report: ImportReport
) -> dict[str, str]:
    """Recursively writes every sub of `b` out to its own file (nested subs first, so a parent's
    reference line always points at an already-materialized file) and returns the {name: path}
    mapping `render_behavior`'s `sub_refs` needs to reference them instead of inlining."""
    sub_refs: dict[str, str] = {}
    for sub in b.subs:
        nested_refs = _extract_subs(sub, library_dir, argcache, report)
        rel = f"{slug(sub.name)}.bsf"
        sub_path = library_dir / rel
        sub_text = render_behavior(sub, argcache, sub_refs=nested_refs)
        _write_if_changed(sub_path, sub_text, argcache, library_dir, report)
        sub_refs[sub.name] = rel
    return sub_refs


def _find_stale_callers(
    library_dir: Path, sub_rel_path: Path, exclude: set[Path]
) -> list[Path]:
    needle = f'from "{sub_rel_path.name}"'
    callers = []
    for f in sorted(library_dir.glob("*.bsf")):
        if f in exclude:
            continue
        if needle in f.read_text():
            callers.append(f)
    return callers


def import_dcs(
    engine, dcs_text: str, library_dir: Path, argcache: ArgCache, name: str | None = None
) -> ImportReport:
    """Decompiles a real `.dcs` clipboard export and writes it into `library_dir` as BSF text,
    splitting every embedded sub-behavior out into its own `<slug>.bsf` file (recursively) with a
    reference left in place of the inline copy. Re-importing a behavior whose shared sub was
    edited in-game updates that one sub file (report.updated) instead of only the caller that
    happened to re-export it -- `report.stale_callers` flags any *other* library file still
    referencing it, since nothing here can refresh those without their own fresh export."""
    library_dir.mkdir(parents=True, exist_ok=True)
    behavior = decompile_dcs(engine, dcs_text)
    out_path = library_dir / f"{name or slug(behavior.name)}.bsf"

    report = ImportReport(behavior_path=out_path)
    sub_refs = _extract_subs(behavior, library_dir, argcache, report)
    text = render_behavior(behavior, argcache, sub_refs=sub_refs)
    _write_if_changed(out_path, text, argcache, library_dir, report)

    # Check every updated path, including out_path itself: a behavior imported here as the
    # top-level target can *also* be referenced as a sub elsewhere (e.g. "Async Radar Set" is
    # both its own standalone library entry and a shared sub of Observer/Mining Leader) --
    # skipping out_path would silently miss exactly that case. `exclude` still keeps a file from
    # flagging itself or a sibling written in this same import as "stale".
    written_this_import = set(report.written) | {out_path}
    for updated_path in report.updated:
        callers = _find_stale_callers(library_dir, updated_path, exclude=written_this_import)
        if callers:
            report.stale_callers[updated_path] = callers
    return report


def export_dcs(library_dir: Path, name: str, engine, argcache: ArgCache) -> str:
    """Reads `<library_dir>/<name>.bsf` (or a bare filename), resolving any `from` sub-references
    against `library_dir` (recursively, via `parse_behavior`'s `base_dir`), and compiles the fully
    resolved behavior straight to a `.dcs` string -- no reference-aware logic needed in
    `compile.py` at all, since by the time it runs every sub is already a plain embedded
    `BsfBehavior` like any other decompiled-from-`.dcs` bundle."""
    path = library_dir / name if name.endswith(".bsf") else library_dir / f"{name}.bsf"
    if not path.exists():
        raise FileNotFoundError(f"no such library behavior: {path}")
    behavior = parse_behavior(path.read_text(), argcache, base_dir=library_dir)
    return compile_dcs(engine, behavior, "C")
