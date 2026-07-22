"""Validates the real `HexAt`/`HexIndexOf` sub-behaviors (embedded in `HexIndexOf_test_1.dcs`)
against the reference math in `hex_expansion_math.md`, running them through the *real*
`data/instructions.lua` via `Interpreter` -- not a hand-rolled Python reimplementation. This is
the strongest validation these routines have: HexAt against closed-form reference math, and
HexIndexOf as a full round-trip through HexAt's own (independently-validated) output, both
executed by genuine game Lua end to end.
"""

import math
from pathlib import Path

import pytest

from blz.desynced_toolkit import Interpreter

DATA_DIR = Path(__file__).parent / "data"

D_HALF = 5
ORIGIN = (0, 0)
SCALE = 10000
K = 8660


def ref_hexat(R, T, d_half=D_HALF, origin=ORIGIN):
    d = d_half * 2
    if R == 0:
        return origin
    k = math.floor(T / R)
    t = T - k * R
    dirs = {
        0: (t - R, R),
        1: (t, R - t),
        2: (R, -t),
        3: (R - t, -R),
        4: (-t, t - R),
        5: (-R, t),
    }
    q, r = dirs[k]
    x = origin[0] + d * q + d_half * r
    ynum = origin[1] * SCALE + d * r * K
    y = math.floor((2 * ynum + SCALE) // (2 * SCALE))
    return (x, y)


def _rt_cases(max_r=8):
    for r in range(0, max_r + 1):
        for t in range(0, max(6 * r, 1)):
            if r == 0 and t > 0:
                continue
            yield r, t


@pytest.fixture(scope="module")
def hexat_hexindexof(engine):
    s = (DATA_DIR / "HexIndexOf_test_1.dcs").read_text().strip()
    _, prog = engine.decode_dcs(s)
    deps = prog["dependencies"]
    return deps[1], deps[2]  # HexAt, HexIndexOf


@pytest.mark.parametrize("R,T", list(_rt_cases()))
def test_hexat_matches_reference(engine, hexat_hexindexof, R, T):
    hexat, _ = hexat_hexindexof
    interp = Interpreter(engine, hexat, params={1: R, 2: T, 4: ORIGIN, 5: D_HALF})
    interp.run()
    result = interp.read_param(3)
    assert (result.coord.x, result.coord.y) == ref_hexat(R, T)


@pytest.mark.parametrize("R,T", list(_rt_cases()))
def test_hexindexof_roundtrips_hexat(engine, hexat_hexindexof, R, T):
    hexat, hexindexof = hexat_hexindexof
    interp1 = Interpreter(engine, hexat, params={1: R, 2: T, 4: ORIGIN, 5: D_HALF})
    interp1.run()
    coord = interp1.read_param(3)
    xy = (coord.coord.x, coord.coord.y)

    interp2 = Interpreter(engine, hexindexof, params={1: xy, 2: ORIGIN, 3: D_HALF})
    interp2.run()
    r2 = interp2.read_param(4).num
    t2 = interp2.read_param(5).num
    assert (r2, t2) == (R, T)
