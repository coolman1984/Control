"""Build the synthetic Excel fixtures once per test process and share them between test modules."""
import atexit
import os
import tempfile

import make_fixtures

_state = {"dir": None, "paths": None, "large": False}


def get(large=False):
    if _state["paths"] is None or (large and not _state["large"]):
        if _state["dir"] is None:
            tmp = tempfile.TemporaryDirectory()
            atexit.register(tmp.cleanup)
            _state["dir"] = tmp
        _state["paths"] = make_fixtures.build_all(os.path.join(_state["dir"].name, "fx"), large=large)
        _state["large"] = large
    return _state["paths"]
