"""CLI for round-tripping a real .dcs clipboard string through BSF text -- see
docs/behavior_source_format.md for the grammar. Installed as the `desynced-bsf` console script
(see `[project.scripts]` in pyproject.toml); `python -m blz.desynced_toolkit.bsf` also works and
is equivalent. Meant to sit directly in a shell pipeline with the game's own clipboard, e.g.
(with `cb` a wrapper script for `xclip -selection clipboard`):

    cb -o | desynced-bsf decompile > mybehavior.bsf
    # ...edit mybehavior.bsf by hand...
    desynced-bsf compile < mybehavior.bsf | cb -i

Only handles a top-level behavior/program clipboard item (.dcs type char 'C', the "Copy
Program" action in the in-game editor) -- a blueprint ('B', with components/frames around it)
decodes to a different table shape and is rejected with an error (decompile_dcs checks the
type char); use LupaEngine.decode_dcs directly to inspect one.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from blz.desynced_toolkit import LupaEngine, open_asset_source
from blz.desynced_toolkit.bsf import (
    ArgCache,
    compile_dcs,
    decompile_dcs,
    export_dcs,
    import_dcs,
    lint_behavior,
    parse_behavior,
    render_behavior,
    semantic_diff_dcs,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
DEFAULT_GAME_DATA_DIR = REPO_ROOT.parent / "desynced-game-data"


def _make_engine(game_data: str | None) -> LupaEngine:
    game_data_dir = game_data or os.environ.get("DESYNCED_GAME_DATA", str(DEFAULT_GAME_DATA_DIR))
    if not os.path.exists(game_data_dir):
        print(
            f"error: game data extract not found at {game_data_dir}\n"
            "(set --game-data or the DESYNCED_GAME_DATA env var)",
            file=sys.stderr,
        )
        sys.exit(1)
    return LupaEngine(open_asset_source(game_data_dir))


def _prog_name() -> str:
    # `python -m` invocation sets argv[0] to the module's full file path; the console
    # script sets it to the installed script name -- show whichever form actually runs.
    name = Path(sys.argv[0]).name
    return "python -m blz.desynced_toolkit.bsf" if name == "__main__.py" else name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=_prog_name())
    parser.add_argument(
        "--game-data",
        help="path to the game data extract (default: sibling desynced-game-data dir, or $DESYNCED_GAME_DATA)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_decompile = sub.add_parser("decompile", help="stdin: .dcs string -> stdout: BSF text")
    p_decompile.add_argument("--input", type=argparse.FileType("r"), default=sys.stdin)
    p_decompile.add_argument("--output", type=argparse.FileType("w"), default=sys.stdout)
    p_decompile.add_argument(
        "--annotate",
        action="store_true",
        help="add non-structural '#' comments: each instruction's in-game display name "
        "(set_reg is displayed as 'Copy') and blank lines before label sections -- for "
        "correlating BSF text with what the user sees in the visual editor",
    )

    p_compile = sub.add_parser("compile", help="stdin: BSF text -> stdout: .dcs string")
    p_compile.add_argument("--input", type=argparse.FileType("r"), default=sys.stdin)
    p_compile.add_argument("--output", type=argparse.FileType("w"), default=sys.stdout)
    p_compile.add_argument(
        "--type", default="C", help="wire type char to encode (default: C, a behavior/program)"
    )

    p_diff = sub.add_parser(
        "semantic-diff",
        help="two .dcs files -> stdout: human-readable diff, ignoring wire-position-only encoding differences",
    )
    p_diff.add_argument("old", type=argparse.FileType("r"), help="path to the earlier .dcs file")
    p_diff.add_argument("new", type=argparse.FileType("r"), help="path to the later .dcs file")

    p_ids = sub.add_parser(
        "ids",
        help="look up game ids by internal id or in-game display name (case-insensitive "
        'substring), e.g. `ids radar` finds c_radar ("Long-Range Radar")',
    )
    p_ids.add_argument("query", help="substring to match against ids and display names")

    p_import = sub.add_parser(
        "import",
        help="stdin: .dcs string -> writes/updates BSF files under a library directory, "
        "splitting embedded sub-behaviors out into their own referenceable files",
    )
    p_import.add_argument("library_dir", type=Path, help="library directory (created if missing)")
    p_import.add_argument("--input", type=argparse.FileType("r"), default=sys.stdin)
    p_import.add_argument(
        "--name",
        help="output filename stem for the top-level behavior (default: derived from its "
        "declared name)",
    )

    p_export = sub.add_parser(
        "export",
        help="library directory + behavior name -> stdout: .dcs string, resolving 'from' "
        "sub-references back into an embedded dependencies array",
    )
    p_export.add_argument("library_dir", type=Path, help="library directory")
    p_export.add_argument("name", help="behavior filename stem (without .bsf) to export")
    p_export.add_argument("--output", type=argparse.FileType("w"), default=sys.stdout)

    p_lint = sub.add_parser(
        "lint",
        help="stdin: BSF text or .dcs string -> warnings for legal-but-suspicious constructs "
        "(unreachable nodes, literal jumps with no matching label, undeclared parameter slots)",
    )
    p_lint.add_argument("--input", type=argparse.FileType("r"), default=sys.stdin)

    args = parser.parse_args(argv)
    engine = _make_engine(args.game_data)

    if args.command == "decompile":
        dcs_str = args.input.read().strip()
        try:
            behavior = decompile_dcs(engine, dcs_str)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        bsf_text = render_behavior(behavior, ArgCache(engine), annotate=args.annotate)
        args.output.write(bsf_text)
    elif args.command == "compile":
        bsf_text = args.input.read()
        argcache = ArgCache(engine)
        try:
            behavior = parse_behavior(bsf_text, argcache)
        except SyntaxError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        # lint warnings on every compile (stderr, non-fatal): the compile step is the natural
        # moment to catch a suspicious-but-legal construct before it reaches the game
        for w in lint_behavior(behavior, argcache):
            print(f"warning: {w}", file=sys.stderr)
        dcs_str = compile_dcs(engine, behavior, args.type)
        args.output.write(dcs_str)
        args.output.write("\n")
    elif args.command == "import":
        dcs_str = args.input.read().strip()
        argcache = ArgCache(engine)
        try:
            report = import_dcs(engine, dcs_str, args.library_dir, argcache, name=args.name)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        for p in report.written:
            print(f"created {p}")
        for p in report.updated:
            print(f"updated {p}")
            if p in report.diffs:
                print(report.diffs[p])
        for p in report.unchanged:
            print(f"unchanged {p}")
        for sub_path, callers in report.stale_callers.items():
            for c in callers:
                print(
                    f"warning: {c} references {sub_path.name}, which just changed -- "
                    f"it may need a fresh re-import of its own to pick that up",
                    file=sys.stderr,
                )
    elif args.command == "export":
        argcache = ArgCache(engine)
        try:
            dcs_str = export_dcs(args.library_dir, args.name, engine, argcache)
        except (FileNotFoundError, SyntaxError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        args.output.write(dcs_str)
        args.output.write("\n")
    elif args.command == "ids":
        q = args.query.lower()
        names = ArgCache(engine).id_display_names()
        hits = sorted(
            (id_, name)
            for id_, name in names.items()
            if q in id_.lower() or (name and q in name.lower())
        )
        for id_, name in hits:
            print(f"{id_}\t{name or '(no display name)'}")
        if not hits:
            print(f"no ids matching {args.query!r}", file=sys.stderr)
            return 1
    elif args.command == "lint":
        raw = args.input.read().strip()
        argcache = ArgCache(engine)
        try:
            # autodetect: a .dcs string is one long token starting with "DS"; BSF text
            # always contains a "behavior ...:" header line
            if raw.startswith("DS") and "\n" not in raw and "(" not in raw:
                behavior = decompile_dcs(engine, raw)
            else:
                behavior = parse_behavior(raw, argcache)
        except (SyntaxError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        warnings = lint_behavior(behavior, argcache)
        for w in warnings:
            print(f"warning: {w}")
        if not warnings:
            print("clean: no warnings")
    elif args.command == "semantic-diff":
        old_dcs = args.old.read().strip()
        new_dcs = args.new.read().strip()
        diff = semantic_diff_dcs(engine, old_dcs, new_dcs)
        print(diff if diff else "(no semantic differences)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
