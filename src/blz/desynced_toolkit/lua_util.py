"""Generic lupa Lua table <-> Python conversion helpers, shared by any module that needs to
inspect a decoded/compiled behavior table from Python without going through a full BsfValue
translation (e.g. for structural equality comparisons in tests, or ad hoc inspection)."""

import lupa.lua54 as lupa


def to_py(t, seen=None):
    """Recursively convert a genuine lupa Lua table into plain Python dict/list/scalar values.
    A table with exactly the keys 1..N (in some order) becomes a list; anything else becomes a
    dict. Non-table values pass through unchanged. `seen` guards against cyclic tables (real
    behavior tables aren't expected to cycle, but this is cheap insurance)."""
    if lupa.lua_type(t) != "table":
        return t
    seen = seen if seen is not None else set()
    if id(t) in seen:
        return "<cycle>"
    seen = seen | {id(t)}
    keys = list(t.keys())
    if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
        return [to_py(v, seen) for v in (t[k] for k in sorted(keys))]
    return {k: to_py(t[k], seen) for k in keys}
