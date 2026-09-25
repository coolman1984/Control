"""Cross-artifact integrity gate for a run.

The fast mode is suitable for every refresh. Deep mode additionally counts every business row and runs SQLite's
full integrity_check, which is slower but useful before release/archive or when storage corruption is suspected.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

from .core.config import load_config
from .core.errors import Xl2aiError
from .core.runs import current_run_id
from .core.sqliteutil import ro_connection


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _inside(root, rel):
    root=os.path.abspath(root)
    path=os.path.abspath(os.path.join(root,str(rel).replace("/",os.sep)))
    try:
        ok=os.path.commonpath([root,path])==root
    except ValueError:
        ok=False
    return path if ok else None


def _db_check(con, deep):
    pragma="integrity_check" if deep else "quick_check"
    rows=[r[0] for r in con.execute(f"PRAGMA {pragma}").fetchall()]
    return rows == ["ok"], rows


def audit_run(cfg, run_id=None, deep=False, catalog_path=None):
    rid=run_id or current_run_id(cfg)
    if not rid:
        raise Xl2aiError("E_STAGE_INPUT","no current run")
    run_dir=os.path.join(cfg.runs_dir,rid)
    cat_path=catalog_path or os.path.join(run_dir,"catalog.db")
    issues=[]
    checks=0
    if not os.path.isfile(cat_path):
        return {"ok":False,"run_id":rid,"deep":deep,"checks":checks,
                "issues":[{"code":"AUDIT_CATALOG_MISSING","detail":cat_path}]}

    try:
        with ro_connection(cat_path) as cat:
            ok, detail=_db_check(cat,deep)
            checks += 1
            if not ok:
                issues.append({"code":"AUDIT_CATALOG_SQLITE","detail":detail})

            sources=cat.execute("SELECT source_id,db_rel FROM _sources ORDER BY source_id").fetchall()
            tables=cat.execute("""SELECT table_id,table_name,db_rel,row_count,column_count
                                  FROM _tables ORDER BY table_id""").fetchall()

            db_cache={}
            try:
                for source_id,db_rel in sources:
                    path=_inside(run_dir,db_rel)
                    if path is None:
                        issues.append({"code":"AUDIT_PATH_ESCAPE","subject":source_id,"detail":db_rel})
                        continue
                    if not os.path.isfile(path):
                        issues.append({"code":"AUDIT_SOURCE_DB_MISSING","subject":source_id,"detail":db_rel})
                        continue
                    con=sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro",uri=True)
                    db_cache[db_rel]=con
                    ok, detail=_db_check(con,deep)
                    checks += 1
                    if not ok:
                        issues.append({"code":"AUDIT_SOURCE_SQLITE","subject":source_id,"detail":detail})

                for table_id,table_name,db_rel,row_count,column_count in tables:
                    con=db_cache.get(db_rel)
                    if con is None:
                        continue
                    exists=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table_name,)).fetchone()
                    checks += 1
                    if not exists:
                        issues.append({"code":"AUDIT_TABLE_MISSING","subject":table_id,"detail":table_name})
                        continue
                    actual_cols=[r[1] for r in con.execute(f"PRAGMA table_info({q(table_name)})").fetchall()
                                 if r[1] != "_xl_row"]
                    checks += 1
                    if len(actual_cols) != int(column_count):
                        issues.append({"code":"AUDIT_COLUMN_COUNT","subject":table_id,
                                       "expected":int(column_count),"actual":len(actual_cols)})
                    log=con.execute("""SELECT data_rows,columns,status FROM _extraction_log
                                       WHERE table_name=? LIMIT 1""",(table_name,)).fetchone()
                    checks += 1
                    if not log:
                        issues.append({"code":"AUDIT_EXTRACTION_LOG","subject":table_id,"detail":"missing"})
                    else:
                        if log[2] != "extracted":
                            issues.append({"code":"AUDIT_EXTRACTION_STATUS","subject":table_id,"detail":log[2]})
                        if int(log[0] or 0) != int(row_count):
                            issues.append({"code":"AUDIT_ROW_METADATA","subject":table_id,
                                           "expected":int(row_count),"actual":int(log[0] or 0)})
                        if int(log[1] or 0) != int(column_count):
                            issues.append({"code":"AUDIT_COLUMN_METADATA","subject":table_id,
                                           "expected":int(column_count),"actual":int(log[1] or 0)})
                    if deep:
                        actual_rows=con.execute(f"SELECT COUNT(*) FROM {q(table_name)}").fetchone()[0]
                        checks += 1
                        if int(actual_rows) != int(row_count):
                            issues.append({"code":"AUDIT_ROW_COUNT","subject":table_id,
                                           "expected":int(row_count),"actual":int(actual_rows)})
            finally:
                for con in db_cache.values():
                    con.close()
    except sqlite3.DatabaseError as e:
        issues.append({"code":"AUDIT_SQLITE_ERROR","detail":str(e)})

    return {"ok":not issues,"run_id":rid,"deep":bool(deep),"checks":checks,"issues":issues}


def main(argv=None):
    ap=argparse.ArgumentParser(prog="xl2ai audit",description="Verify run artifacts and catalog/source consistency.")
    ap.add_argument("--config")
    ap.add_argument("--run")
    ap.add_argument("--deep",action="store_true",help="full SQLite integrity checks plus actual row counts")
    args=ap.parse_args(argv)
    try:
        cfg=load_config(args.config)
        result=audit_run(cfg,args.run,args.deep)
    except Xl2aiError as e:
        print(str(e),file=sys.stderr)
        return 1
    print(f"run: {result['run_id']} | checks: {result['checks']} | deep: {result['deep']} | "
          f"status: {'OK' if result['ok'] else 'FAILED'}")
    for issue in result["issues"]:
        print(f"  {issue['code']}: {issue.get('subject','')} {issue.get('detail','')}".rstrip())
    return 0 if result["ok"] else 1


if __name__=="__main__":
    sys.exit(main())
