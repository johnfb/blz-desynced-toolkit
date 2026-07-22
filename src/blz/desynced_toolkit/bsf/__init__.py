"""BSF (Behavior Source Format) -- see docs/behavior_source_format.md for the grammar this package
implements. Public API: decode a real .dcs string straight to BSF text, and compile BSF text
straight back to a .dcs string."""

from __future__ import annotations

from .argcache import ArgCache
from .compile import compile_behavior, compile_dcs
from .decompile import decompile_behavior, decompile_dcs
from .ir import BsfBehavior, BsfNode, BsfParam
from .library import ImportReport, export_dcs, import_dcs
from .lint import lint_behavior
from .parse_text import parse_behavior
from .render_mermaid import render_mermaid
from .render_text import render_behavior
from .semantic_diff import semantic_diff_behaviors, semantic_diff_dcs

__all__ = [
    "BsfBehavior",
    "BsfNode",
    "BsfParam",
    "ImportReport",
    "compile_behavior",
    "compile_dcs",
    "decompile_behavior",
    "decompile_dcs",
    "export_dcs",
    "import_dcs",
    "parse_behavior",
    "render_behavior",
    "render_mermaid",
    "lint_behavior",
    "semantic_diff_behaviors",
    "semantic_diff_dcs",
    "dcs_to_bsf",
    "bsf_to_dcs",
]


def dcs_to_bsf(engine, dcs_str: str) -> str:
    """Real .dcs clipboard string -> BSF text, in one call."""
    return render_behavior(decompile_dcs(engine, dcs_str), ArgCache(engine))


def bsf_to_dcs(engine, bsf_text: str, type_char: str = "C") -> str:
    """BSF text -> a real .dcs clipboard string, in one call."""
    return compile_dcs(engine, parse_behavior(bsf_text, ArgCache(engine)), type_char)
