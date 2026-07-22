"""MockWorld.load_blueprint: spawning a real blueprint (wire type `'B'`) into the mock world
(docs/mock_world_spec.md's "BSF envelope/sidecar" follow-on -- a blueprint bundles several
buildings/components/behaviors, not a single behavior, so it needs its own load path rather than
`attach_behavior`).

Fixture: `magnifier_lattice.dcs`, a real deployed 8-building Blight Magnifier lattice (copied from
the `desynced-behaviors` repo's `library/magnifier_lattice.dcs` -- see that repo's
`blight_magnifier_mining.md`). Each building carries an Integrated Behavior Controller
(`c_integrated_behavior`) running "MagnifierSignal", parameterized by a `Resource` register set in
the blueprint's own `regs`. Decoded shape confirmed against `data/library.lua`'s
`UnpackCompactedItemToLibraryTable`/`iblueprintcomponents`: a behavior-hosting component's 3rd
array field is a 1-based index into the blueprint's own `dependencies` array (the same convention
`call`'s `sub` field uses), and `regs` keys are either a plain int (a frame register, 1..4) or a
`"compIdx|regIdx"` string (component `compIdx`'s own register `regIdx`).
"""

from pathlib import Path

import pytest

from blz.desynced_toolkit import MockWorld

DATA_DIR = Path(__file__).parent / "data"


def _load_lattice(w, **kwargs):
    dcs = (DATA_DIR / "magnifier_lattice.dcs").read_text().strip()
    return w.load_blueprint(dcs, **kwargs)


def test_load_blueprint_spawns_every_building_at_its_offset(engine):
    w = MockWorld(engine)
    entities = _load_lattice(w, x=100, y=200)

    assert len(entities) == 8
    assert {e.id for e in entities} == {"f_building3x2a", "f_building2x1c"}
    # first building's blueprint-local offset is (0, 0) -- lands exactly on the given origin
    assert entities[0].location.x == 100 and entities[0].location.y == 200
    # every other building is offset from the origin, none collide, all inside the lattice's span
    locations = {(e.location.x, e.location.y) for e in entities}
    assert len(locations) == 8
    for lx, ly in locations:
        assert 100 <= lx <= 110 and 200 <= ly <= 210


def test_load_blueprint_attaches_and_installs_component_programs(engine):
    w = MockWorld(engine)
    entities = _load_lattice(w)

    # every building has its own Integrated Behavior Controller running its own Interpreter --
    # each program install is independent, not one shared instance
    assert len(w.interpreters) == len(entities)
    hosting_ids = {interp.comp.id for interp in w.interpreters}
    assert hosting_ids == {"c_integrated_behavior"}
    owner_ids = {interp.comp.owner.eid for interp in w.interpreters}
    assert owner_ids == {e.eid for e in entities}


def test_load_blueprint_applies_regs_as_real_register_writes(engine):
    w = MockWorld(engine)
    entities = _load_lattice(w)
    e = entities[0]

    # plain-int reg key 4 == FRAMEREG_SIGNAL on the entity itself
    signal = e.GetRegister(e, 4)
    assert signal.id == "metalore" and signal.num == -1

    # "8|1" == the 8th component (Integrated Behavior Controller) 's own register 1 -- also its
    # "Resource" call parameter, since parameters ARE component registers
    comps = list(e.components.values())
    ib = comps[7]
    assert ib.id == "c_integrated_behavior"
    resource_param = ib.GetRegister(ib, 1)
    assert resource_param.id == "metalore"


def test_load_blueprint_installed_programs_run_without_erroring(engine):
    w = MockWorld(engine)
    _load_lattice(w)
    # MagnifierSignal starts with `unlock` + a `wait(20)` -- just confirm every installed program
    # activates and runs real ticks cleanly (no missing-primitive errors) rather than asserting its
    # full broadcast logic, which belongs to a MagnifierSignal-specific test.
    w.step(5)


def test_load_blueprint_rejects_non_blueprint_dcs(engine):
    w = MockWorld(engine)
    behavior_dcs = (DATA_DIR / "observer.dcs").read_text().strip()
    with pytest.raises(ValueError, match="not a blueprint"):
        w.load_blueprint(behavior_dcs)
