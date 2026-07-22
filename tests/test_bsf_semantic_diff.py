"""bsf/semantic_diff.py: a wire-position-independent diff between two real .dcs saves.

The real regression case: a user re-saved `magnifier_signal.dcs` in the in-game editor after
making three deliberate edits (added `unlock()`, retargeted a `set_number`'s `Result` straight
to `@signal` instead of through a scratch variable, removed an explicit loop-back `jump` in
favor of relying on POP's own auto-restart). The re-save *also* reshuffled two unrelated things
the user never touched: which branch of a `check_number` got written as an explicit target vs.
omitted, and the relative wire-array position of one `set_reg` node. A raw decompiled-text diff
shows all five as "changes"; a correct semantic diff must report only the three real ones."""

from pathlib import Path

from blz.desynced_toolkit.bsf.argcache import ArgCache
from blz.desynced_toolkit.bsf.semantic_diff import semantic_diff_behaviors, semantic_diff_dcs

DATA_DIR = Path(__file__).parent / "data"

# The exact re-save from the real session: unlock() added, the two check_number branches on the
# 200-cap check reshuffled to omission (re-save artifact only), the NeedsRegen-marking node
# relocated in wire order (re-save artifact only), the signal broadcast collapsed from two
# instructions to one direct `Result=@signal` write, and the final loop-back `jump` replaced by
# relying on POP's auto-restart.
RESAVED_DCS = (
    "DSC1Ju1XvlEm1IjcPH3cHgWq0PdcoS2ILHlK3L2CCm02Z2up3IbWlB1iVBgM3AhAk82klWYY4IfY2z1ySMv412LVza"
    "2mFPPC2FDyLm1A1jqa0wRTbP3dpQSJ1rycen4XVSd429wwhL1QoZGB1aNUip1vl8XG41NxBS2AfhGN1uFlDq1Zoux3"
    "1WEfqw0zmkV00hia1O16I87o2qJl8F3fEphR1wNQw11WHaKC32Be2F1cMMBA00nlr22dfYC93Sj9Za0Db7h61n3BZh"
    "04C0aE0IXRcc3isOtT2hu7jX30wnBP3Pbr0c4MmRNB2f8wdq193KbE2cpbdW2FL0Hn4MJ6zB40croP4UfrZh2UIxjj"
    "0OCC14345oUP0VNHHb2yxrN31b5pCc2aUTNw46I4jI1OSt3T21Dhwj2yoECI0bsjlO1iRNwo2DKD3Q3Yowgj0rfMSF"
    "3Ed4yT4c7aIt2GjeM30kStUM03qrVQ3msJLq3LrKqr2k4ySg0QO2a62sxXsC0OfWzH4GpfnE1O7dBl2MJK2Z04SHL24"
    "ESapu1mg0is41GnRC2Y5zQ11L9GUZ3Ot7LT2v9Wu21osnd10GrVsL2EEwKe0USmhf3nHqCq0TXIUt1gugNd2Jpbc72a"
    "vg6p2aVIfd4PV2K13VbSOs4ClfCo3c8Uzm38ercr355STC27eFzp1YA9JK3jqqeK4RGjr82dDFM838ufvy2quxWa47"
    "lBSP0ZcOfT1k5kk34TcMag3RCfDw1HMTUV4AyWFV1AaHo926OG5v0g0IY231oYS34OT29h0mzWCi0lndhV3nUbcj4Q"
    "wiKd1xvIEW2nOZkj3UQavF4TYecY1pTfTB08eMl4372xBO0T390o28tIre36ZCTg2nZyHb4EGvAA09cx6W3evyoB2N"
    "matN2oSl9410YPtz2DRu9T2Lb8iF1wp1sP1zg8pz3E7Lrz1wC5mf26gJPu3UIRJX26ODpc1znku71XYmpT0gA9Vl3bc"
    "icr2ecVHq1GqADV0QRZYx2kzSET4HeqlC2Rh4WH1oASNQ1ru2PP0Q9WxK1AZ0yU4bmDy63TtDQN0Ky8Zy2Oe4Cc02Wg"
    "er2Lijax0Stq3u2T2BPm3Cz3Kn33TxFj11GoCL0ZmW2H4DFi482zeALE00hV2m0yQfhj2QED5A26qnNH1er0YD0ilf2"
    "u2kf2Rb1Ef9Sf1z7UXT0hxPHz3WXQYA4SuYxH2qqQxA0wOKyk0646fh4FBdDh3r3POK2Op2zm49Ptc04HbeIt3I5mdf"
    "1Nw76I0kYTGM0dOAnK"
)


def test_no_diff_against_self(engine):
    raw = (DATA_DIR / "beacon.dcs").read_text().strip()
    assert semantic_diff_dcs(engine, raw, raw) == ""


def test_magnifier_resave_reports_only_real_edits(engine):
    # magnifier_signal_original.dcs is the pre-resave export; the live library/ copy is the
    # re-save itself (identical to RESAVED_DCS), so it can't serve as the "original" side.
    original = (DATA_DIR / "magnifier_signal_original.dcs").read_text().strip()
    report = semantic_diff_dcs(engine, original, RESAVED_DCS)

    # The three real edits must be present.
    assert "unlock()" in report
    assert "Result=@signal" in report and "Result=$Sig" in report
    assert "jump(Label=v_arrow_right)" in report  # reported as removed

    # The two re-save-only artifacts (a branch-encoding reshuffle and a node's relative wire
    # position moving) must NOT show up as reported diffs at all.
    assert "NeedsRegen" not in report
    assert "check_number(Value=$Amt, Compare=200" not in report


def test_added_removed_and_changed_nodes_on_hand_built_ir(engine):
    """Synthetic case isolating each diff kind: a node present only in `new` (added), one
    present only in `old` (removed), and one whose args changed but stayed in the same
    structural position (changed) -- independent of the real-fixture case above, which mixes
    several kinds together."""
    from blz.desynced_toolkit.bsf.ir import BsfBehavior, BsfNode
    from blz.desynced_toolkit.bsf.values import Num, Var

    argcache = ArgCache(engine)

    def make(value_for_b):
        a = BsfNode(id="A", op="set_reg", args={"Value": Num(1), "Target": Var("x")})
        b = BsfNode(id="B", op="set_reg", args={"Value": Num(value_for_b), "Target": Var("y")})
        b.branches["next"] = "POP"
        return BsfBehavior(name="Test", params=[], nodes={"A": a, "B": b}, order=["A", "B"])

    old = make(2)
    new = make(3)  # same shape, one arg value changed
    report = semantic_diff_behaviors(old, new, argcache)
    assert "Value=2" in report and "Value=3" in report

    # Now add a genuinely new node reachable only in `new`.
    new2 = make(2)
    c = BsfNode(id="C", op="set_reg", args={"Value": Num(9), "Target": Var("z")})
    c.branches["next"] = "POP"
    new2.nodes["B"].branches["next"] = "C"
    new2.nodes["C"] = c
    new2.order = ["A", "B", "C"]
    report2 = semantic_diff_behaviors(old, new2, argcache)
    assert "+ [C]" in report2
    assert "Value=9" in report2
