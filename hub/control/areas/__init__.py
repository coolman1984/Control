"""Loads every area. One area failing to load never stops the others."""
import importlib

from .. import config, registry

AREA_MODULES = ["core", "win", "data", "sys", "gmes"]


def load_all(cfg=None):
    if registry.AREAS:
        return
    cfg = cfg or config.load()
    for mod in AREA_MODULES:
        try:
            importlib.import_module(f"control.areas.{mod}").register_area(cfg)
        except Exception as e:                       # noqa: BLE001 - reported, not raised
            hint = getattr(e, "hint", "") or "run setup.ps1, then control doctor"
            registry.set_area(mod, False, reason=f"{type(e).__name__}: {getattr(e, 'message', e)}", hint=hint)
