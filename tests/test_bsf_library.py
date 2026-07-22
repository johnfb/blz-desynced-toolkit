"""bsf/library.py: the on-disk by-name library store (docs/behavior_source_format.md's
"Sub-behaviors by reference"). Covers the two directions (import_dcs splits, export_dcs joins)
plus the reference grammar's own edge cases (missing base_dir, cycles, stale name)."""

from pathlib import Path

import pytest

from blz.desynced_toolkit.bsf import (
    ImportReport,
    export_dcs,
    import_dcs,
    semantic_diff_dcs,
)
from blz.desynced_toolkit.bsf.argcache import ArgCache
from blz.desynced_toolkit.bsf.ir import BsfBehavior, BsfNode, BsfParam
from blz.desynced_toolkit.bsf.library import _extract_subs, _find_stale_callers, _write_if_changed
from blz.desynced_toolkit.bsf.parse_text import BsfParseError, parse_behavior
from blz.desynced_toolkit.bsf.render_text import render_behavior
from blz.desynced_toolkit.bsf.values import Num, Param

DATA = Path(__file__).parent / "data"


@pytest.fixture
def argcache(engine):
    return ArgCache(engine)


def _behavior_with_sub(sub_body_value: int) -> BsfBehavior:
    """A minimal Caller->Shared call graph, parameterized so two calls can build a "Shared" sub
    with different content (standing in for two exports taken before/after an in-game edit)."""
    shared = BsfBehavior(
        name="Shared",
        params=[BsfParam(name="Out")],
        nodes={
            "n1": BsfNode(
                id="n1",
                op="set_reg",
                args={"Value": Num(sub_body_value), "Target": Param(1)},
            )
        },
        order=["n1"],
    )
    call_node = BsfNode(id="n1", op="call")
    call_node.hidden["sub"] = 1
    caller = BsfBehavior(
        name="Caller",
        params=[],
        nodes={"n1": call_node},
        order=["n1"],
        subs=[shared],
    )
    return caller


def test_import_splits_real_fixture_and_export_roundtrips(engine, argcache, tmp_path):
    dcs = (DATA / "mining_leader.dcs").read_text().strip()
    report = import_dcs(engine, dcs, tmp_path, argcache, name="mining-leader")

    sub_path = tmp_path / "check-emergency.bsf"
    assert sub_path in report.written
    assert report.behavior_path == tmp_path / "mining-leader.bsf"
    assert 'sub Check Emergency from "check-emergency.bsf"' in report.behavior_path.read_text()
    # the sub file itself is a normal, independently-parseable behavior document
    assert sub_path.read_text().startswith("behavior Check Emergency(")

    recompiled = export_dcs(tmp_path, "mining-leader", engine, argcache)
    assert semantic_diff_dcs(engine, dcs, recompiled) == ""


def test_reimport_identical_dcs_reports_everything_unchanged(engine, argcache, tmp_path):
    dcs = (DATA / "mining_leader.dcs").read_text().strip()
    import_dcs(engine, dcs, tmp_path, argcache, name="mining-leader")
    report2 = import_dcs(engine, dcs, tmp_path, argcache, name="mining-leader")

    assert report2.written == []
    assert report2.updated == []
    assert tmp_path / "mining-leader.bsf" in report2.unchanged
    assert tmp_path / "check-emergency.bsf" in report2.unchanged


def test_reimport_with_edited_shared_sub_updates_and_flags_stale_caller(engine, argcache, tmp_path):
    caller_a = _behavior_with_sub(1)
    caller_a.name = "CallerA"
    text_a = render_behavior(caller_a, argcache, sub_refs={"Shared": "shared.bsf"})
    (tmp_path / "shared.bsf").write_text(render_behavior(caller_a.subs[0], argcache))
    (tmp_path / "caller-a.bsf").write_text(text_a)

    # CallerB is imported fresh via decompile-shaped IR (hand-built here, standing in for a real
    # .dcs whose embedded "Shared" sub was edited in-game since CallerA was last exported).
    caller_b = _behavior_with_sub(2)
    caller_b.name = "CallerB"
    report = ImportReport(behavior_path=tmp_path / "caller-b.bsf")
    sub_refs = _extract_subs(caller_b, tmp_path, argcache, report)
    _write_if_changed(
        report.behavior_path, render_behavior(caller_b, argcache, sub_refs=sub_refs), argcache,
        tmp_path, report,
    )
    # import_dcs's own post-pass, replicated here since this test drives the private helpers
    # directly instead of a full decompile (no real .dcs stands in for "sub edited in-game").
    for updated_path in report.updated:
        if updated_path == report.behavior_path:
            continue
        callers = _find_stale_callers(tmp_path, updated_path, exclude={report.behavior_path})
        if callers:
            report.stale_callers[updated_path] = callers

    assert tmp_path / "shared.bsf" in report.updated
    assert (tmp_path / "shared.bsf") in report.diffs
    assert report.stale_callers.get(tmp_path / "shared.bsf") == [tmp_path / "caller-a.bsf"]


def test_export_missing_behavior_raises(engine, argcache, tmp_path):
    with pytest.raises(FileNotFoundError):
        export_dcs(tmp_path, "nope", engine, argcache)


def test_parse_reference_without_base_dir_raises(argcache):
    text = 'behavior Caller():\n\ncall(sub=1)\n\nsub Shared from "shared.bsf"\n'
    with pytest.raises(BsfParseError, match="base directory"):
        parse_behavior(text, argcache)


def test_parse_reference_to_missing_file_raises(argcache, tmp_path):
    text = 'behavior Caller():\n\ncall(sub=1)\n\nsub Shared from "shared.bsf"\n'
    with pytest.raises(BsfParseError, match="doesn't exist"):
        parse_behavior(text, argcache, base_dir=tmp_path)


def test_parse_reference_name_mismatch_raises(argcache, tmp_path):
    (tmp_path / "shared.bsf").write_text("behavior ActuallyNamedThis():\n\nexit()\n")
    text = 'behavior Caller():\n\ncall(sub=1)\n\nsub Shared from "shared.bsf"\n'
    with pytest.raises(BsfParseError, match="stale"):
        parse_behavior(text, argcache, base_dir=tmp_path)


def test_circular_sub_reference_raises(argcache, tmp_path):
    (tmp_path / "a.bsf").write_text('behavior A():\n\ncall(sub=1)\n\nsub B from "b.bsf"\n')
    (tmp_path / "b.bsf").write_text('behavior B():\n\ncall(sub=1)\n\nsub A from "a.bsf"\n')
    with pytest.raises(BsfParseError, match="circular"):
        parse_behavior((tmp_path / "a.bsf").read_text(), argcache, base_dir=tmp_path)
