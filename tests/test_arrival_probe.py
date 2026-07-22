"""Golden differential for the arrival model (docs/mock_world_spec.md's RequestStateMove-arrival item):
run the real ArrivalProbe behavior (`tests/data/arrival_probe.bsf`, compiled copy alongside) in
the mock world and diff its print stream against the behavior's real in-game run
(`arrival_probe_ingame.log`, captured 2026-07-20).

RESULT (this is no longer provisional): the in-game readouts CONFIRM world.lua's arrival model --
arrived iff `Map.GetDistance(unit, target) <= range`, floored at 1 for an entity target. The
coordinate-target cases (100/102/105) reproduce the in-game log tile-for-tile; the entity-target
cases (200/202/205) reproduce its arrival GATE exactly (distance readouts 1/2/5, the range-0 floor
to 1), diverging only in the stop *tile*, because the in-game Target was a 3x3 building (engine
closest-tile / placeable-location approach) while the mock uses a point target -- the pre-existing
pathfinding divergence flagged in world.lua's model note, not an arrival-model difference.

In-game geometry (from the log header): home tile {37,-10}; coordinate target 10 east = {47,-10};
entity target = a 3x3 Command Center (f_landingpod), raw origin {45,-15}, footprint x[45,47]
y[-15,-13], center {46,-14} (what get_location returns). get_distance on the entity measures to
the CLOSEST footprint tile -- {45,-13}, the SW corner (home is west & south of the pod; +X=East,
+Y=South) -- which is what yields the 1/2/5
readouts (measuring to the center {46,-14} would read 2 at the first stop; a 2x2 would too, so the
readouts confirm the 3x3). The mock is seeded to match with a point target at that closest tile
{45,-13}: for a point entity closest-tile == center, so it reproduces the entity-case gate."""

import re
from pathlib import Path

from blz.desynced_toolkit import MockWorld

DATA_DIR = Path(__file__).parent / "data"

HOME = (37, -10)
COORD_MARKERS = (100, 102, 105)
ENTITY_MARKERS = (200, 202, 205)
POD_CLOSEST_TILE = (45, -13)  # 3x3 pod's SW-corner footprint tile, closest to the SW approach
# (get_distance measures here); center is {46,-14}. A point target here reproduces the entity gate.


def _parse_ingame_log(text):
    """The in-game DEBUGPRINT stream -> {marker: (distance_readout, stop_tile)}, in order.

    Each case is three consecutive Register prints: the marker num, the get_distance num, then
    the stop Coord. (The middle record renders as an Entity because get_distance's output register
    still carries the unit ref; only its Num -- the distance -- is the measurement.)"""
    num_re = re.compile(r"Num=(-?\d+)")
    coord_re = re.compile(r"Coord=(-?\d+),(-?\d+)")
    values = []
    for line in text.splitlines():
        if line.lstrip().startswith("#") or "DEBUGPRINT" not in line:
            continue
        m = coord_re.search(line)
        if m:
            values.append(("coord", (int(m.group(1)), int(m.group(2)))))
        else:
            m = num_re.search(line)
            values.append(("num", int(m.group(1))))
    cases = {}
    i = 0
    while i < len(values):
        v = values[i]
        if v[0] == "num" and v[1] in COORD_MARKERS + ENTITY_MARKERS and i + 2 < len(values):
            cases[v[1]] = (values[i + 1][1], values[i + 2][1])
            i += 3
        else:
            i += 1
    return values, cases


def _run_mock(engine):
    """Run the probe in the mock with the in-game geometry -> {marker: (distance, stop_tile)}."""
    dcs = (DATA_DIR / "arrival_probe.dcs").read_text().strip()
    w = MockWorld(engine)
    e = w.spawn("f_bot_1s_adw", "player", *HOME)  # Engineer at the in-game home tile
    target = w.spawn("f_bot_1m_c", "player", *POD_CLOSEST_TILE)  # point stand-in for the 3x3 pod
    interp = w.attach_behavior(
        e, dcs, params={1: engine.lua.globals().NewValue(0, None, None, target)}
    )
    for _ in range(600):
        w.step(1)
        if interp._finished:
            break
    assert interp._finished

    values = []
    for p in w.prints:
        v = p.value
        if v.coord is not None:
            values.append(("coord", (int(v.coord.x), int(v.coord.y))))
        else:
            values.append(("num", int(v.num)))
    cases = {}
    i = 0
    while i < len(values):
        v = values[i]
        if v[0] == "num" and v[1] in COORD_MARKERS + ENTITY_MARKERS and i + 2 < len(values):
            cases[v[1]] = (values[i + 1][1], values[i + 2][1])
            i += 3
        else:
            i += 1
    assert ("num", 888) not in values, "a case hit the path-blocked marker in the mock"
    assert ("num", 999) in values, "the probe did not reach the done marker in the mock"
    return cases


def test_coordinate_cases_reproduce_ingame_log_tile_for_tile(engine):
    """Coordinate target (ranges 0/2/5): the mock matches the in-game log exactly -- same
    get_distance readout AND same stop tile."""
    _, golden = _parse_ingame_log((DATA_DIR / "arrival_probe_ingame.log").read_text())
    mock = _run_mock(engine)
    for marker in COORD_MARKERS:
        assert mock[marker] == golden[marker], (
            f"case {marker}: mock {mock[marker]} != in-game {golden[marker]}"
        )
    # Pin the actual measured numbers so a regression is legible without the log file.
    assert {m: golden[m] for m in COORD_MARKERS} == {
        100: (0, (47, -10)),  # range 0: stops ON the target tile
        102: (2, (45, -10)),  # range 2: first tile with get_distance <= 2
        105: (5, (42, -10)),  # range 5: first tile with get_distance <= 5
    }


def test_entity_case_arrival_gate_matches_ingame(engine):
    """Entity target (ranges 0/2/5): the mock reproduces the in-game arrival GATE -- the
    get_distance readout at which it stops, including range 0 floored to 1. The stop TILE differs
    (mock point target vs in-game 3x3 building approach) and is asserted only against the mock's
    own point-target prediction, not the in-game footprint."""
    _, golden = _parse_ingame_log((DATA_DIR / "arrival_probe_ingame.log").read_text())
    mock = _run_mock(engine)

    # In-game gate: range 0 -> readout 1 (entity tile unenterable), range 2 -> 2, range 5 -> 5.
    assert {m: golden[m][0] for m in ENTITY_MARKERS} == {200: 1, 202: 2, 205: 5}
    for marker in ENTITY_MARKERS:
        assert mock[marker][0] == golden[marker][0], (
            f"case {marker}: mock gate readout {mock[marker][0]} != in-game {golden[marker][0]}"
        )

    # The mock's point-target stops (approach along y = pod ref row) -- documents the divergence.
    assert {m: mock[m] for m in ENTITY_MARKERS} == {
        200: (1, (44, -13)),
        202: (2, (43, -13)),
        205: (5, (40, -13)),
    }
