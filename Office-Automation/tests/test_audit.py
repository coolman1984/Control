"""Artifact audit tests."""
import os
import sqlite3
import tempfile
import unittest

from xl2ai.audit import audit_run
from xl2ai.catalog import DDL
from xl2ai.core.config import load_config
from xl2ai.core.runs import Run


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=self.tmp.name
        cfgp=os.path.join(root,"xl2ai.toml")
        with open(cfgp,"w",encoding="utf-8") as f:f.write('[project]\nname="audit"\n')
        self.cfg=load_config(cfgp)
        self.run=Run.create(self.cfg)
        db=self.run.path("extract","s.db")
        os.makedirs(os.path.dirname(db),exist_ok=True)
        c=sqlite3.connect(db)
        c.executescript("""
        CREATE TABLE _extraction_log (
          sheet_index INTEGER,sheet_name TEXT,table_name TEXT,visibility TEXT,status TEXT,
          data_rows INTEGER,columns INTEGER,header_row INTEGER
        );
        CREATE TABLE data (_xl_row INTEGER,id INTEGER,value TEXT);
        """)
        c.execute("INSERT INTO _extraction_log VALUES (1,'S','data','visible','extracted',2,2,1)")
        c.executemany("INSERT INTO data VALUES (?,?,?)",[(2,1,"a"),(3,2,"b")])
        c.commit(); c.close()

        cat=self.run.path("catalog.db")
        c=sqlite3.connect(cat); c.executescript(DDL)
        c.execute("INSERT INTO _sources VALUES (?,?,?,?,?,?,?,?,?)",
                  ("s","x","h",1,"t","full","extract/s.db",0,None))
        c.execute("INSERT INTO _tables VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("t","s","S","data","extract/s.db",2,2,1,"visible","fp",None,None))
        c.commit(); c.close()
        self.cat=cat

    def tearDown(self):
        self.tmp.cleanup()

    def test_fast_and_deep_audit_pass(self):
        self.assertTrue(audit_run(self.cfg,self.run.id,False,self.cat)["ok"])
        self.assertTrue(audit_run(self.cfg,self.run.id,True,self.cat)["ok"])

    def test_deep_audit_catches_actual_row_tamper(self):
        db=self.run.path("extract","s.db")
        c=sqlite3.connect(db); c.execute("DELETE FROM data WHERE id=2"); c.commit(); c.close()
        fast=audit_run(self.cfg,self.run.id,False,self.cat)
        deep=audit_run(self.cfg,self.run.id,True,self.cat)
        self.assertTrue(fast["ok"])
        self.assertFalse(deep["ok"])
        self.assertTrue(any(x["code"]=="AUDIT_ROW_COUNT" for x in deep["issues"]))


if __name__=="__main__":
    unittest.main(verbosity=2)
