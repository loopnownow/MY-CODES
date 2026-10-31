"""P3: provenance init failures must warn, not fail silently."""
from __future__ import annotations

import importlib
import warnings


def test_modules_import_warns_when_provenance_collect_fails(monkeypatch):
    import modules
    import modules.provenance as prov

    def boom(*_a, **_k):
        raise OSError("simulated provenance failure")

    monkeypatch.setattr(prov, "collect", boom)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.reload(modules)
    assert any("provenance initialization failed" in str(w.message) for w in caught)
