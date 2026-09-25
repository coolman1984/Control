"""Preflight checks for a project: environment, config, sources, storage and last trusted dataset."""
from __future__ import annotations

import argparse
import os
import platform
import sqlite3
import sys
import tempfile

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.runs import current_run_id
from .sources.inventory import build_inventory


def _check(name,status,detail,level="ok"):
    return {"name":name,"status":status,"detail":detail,"level":level}


def run_doctor(cfg):
    checks=[]
    is_windows=os.name=="nt"
    checks.append(_check("platform",is_windows,
                         platform.platform() if is_windows else "not Windows; the Excel engine is unavailable (the direct engine and every other stage still work)",
                         "ok" if is_windows else "warn"))
    try:
        from .extract.common import PYWIN32_AVAILABLE
    except Exception:
        PYWIN32_AVAILABLE=False
    checks.append(_check("pywin32",bool(PYWIN32_AVAILABLE),
                         "available" if PYWIN32_AVAILABLE else "missing; install pywin32 on the Windows extraction machine",
                         "ok" if PYWIN32_AVAILABLE else ("error" if is_windows else "warn")))
    from .extract.direct import available as direct_available
    from .extract.pipeline import resolve_engine
    engine=resolve_engine(cfg.extract_options())
    checks.append(_check("direct_engine",direct_available(),
                         "python-calamine available: workbooks can be read without Excel" if direct_available()
                         else "python-calamine missing; pip install python-calamine to read workbooks without Excel",
                         "ok" if direct_available() else ("error" if engine=="direct" else "warn")))
    checks.append(_check("engine",True,f"extraction engine in use: {engine} (configured: {cfg.extract['engine']})"))
    try:
        sources,warnings=build_inventory(cfg)
        checks.append(_check("sources",True,f"{len(sources)} source(s) found"+(f"; {len(warnings)} warning(s)" if warnings else "")))
    except Xl2aiError as e:
        checks.append(_check("sources",False,e.message,"error"))
    try:
        from .rules import load_packs
        packs=load_packs(cfg.pack_paths())
        checks.append(_check("rule_packs",True,f"{len(packs)} pack(s) valid"))
    except Xl2aiError as e:
        checks.append(_check("rule_packs",False,e.message,"error"))
    try:
        os.makedirs(cfg.data_dir,exist_ok=True)
        fd,p=tempfile.mkstemp(prefix=".xl2ai-write-",dir=cfg.data_dir)
        os.close(fd); os.remove(p)
        checks.append(_check("data_dir",True,f"writable: {cfg.data_dir}"))
    except OSError as e:
        checks.append(_check("data_dir",False,f"not writable: {e}","error"))
    rid=current_run_id(cfg)
    if rid:
        run_dir=os.path.join(cfg.runs_dir,rid)
        cat=os.path.join(run_dir,"catalog.db")
        pack=os.path.join(run_dir,"ai","context_pack.json")
        checks.append(_check("current_run",True,rid))
        checks.append(_check("catalog",os.path.isfile(cat),"present" if os.path.isfile(cat) else "missing","ok" if os.path.isfile(cat) else "warn"))
        checks.append(_check("context_pack",os.path.isfile(pack),"present" if os.path.isfile(pack) else "missing","ok" if os.path.isfile(pack) else "warn"))
        if os.path.isfile(cat):
            try:
                con=sqlite3.connect(f"file:{os.path.abspath(cat)}?mode=ro",uri=True)
                n=con.execute("SELECT COUNT(*) FROM _tables").fetchone()[0]; con.close()
                checks.append(_check("catalog_tables",True,f"{n} table(s)"))
            except sqlite3.DatabaseError as e:
                checks.append(_check("catalog_tables",False,f"catalog unreadable: {e}","error"))
    else:
        checks.append(_check("current_run",False,"none yet; run refresh","warn"))
    return checks


def main(argv=None):
    ap=argparse.ArgumentParser(prog="xl2ai doctor",description="Check whether this project is ready to refresh/query.")
    ap.add_argument("--config")
    args=ap.parse_args(argv)
    try:
        cfg=load_config(args.config)
        checks=run_doctor(cfg)
    except Xl2aiError as e:
        print(str(e),file=sys.stderr); return 1
    for c in checks:
        icon={"ok":"OK","warn":"WARN","error":"ERROR"}[c["level"]]
        print(f"{icon:5} {c['name']:<14} {c['detail']}")
    return 1 if any(c["level"]=="error" for c in checks) else 0


if __name__=="__main__":
    sys.exit(main())
