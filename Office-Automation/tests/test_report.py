"""Human report smoke test."""
import os
import sqlite3
import tempfile
import unittest

from xl2ai.catalog import DDL
from xl2ai.core.config import load_config
from xl2ai.core.runs import Run
from xl2ai.report import build_report, render_report


class TestReport(unittest.TestCase):
    def test_report_summarizes_metadata_without_rows(self):
        with tempfile.TemporaryDirectory() as root:
            cfgp=os.path.join(root,"xl2ai.toml")
            with open(cfgp,"w",encoding="utf-8") as f:f.write('[project]\nname="report"\n')
            cfg=load_config(cfgp); run=Run.create(cfg)
            cat=run.path("catalog.db"); c=sqlite3.connect(cat); c.executescript(DDL)
            c.execute("INSERT INTO _sources VALUES (?,?,?,?,?,?,?,?,?)",("s","x","h",1,"t","full","x.db",0,None))
            c.execute("INSERT INTO _tables VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",("t","s","S","data","x.db",7,1,1,"visible","fp",None,None))
            c.execute("INSERT INTO _dq_findings VALUES (?,?,?,?,?,?,?,?)",("d","DQ_X","warn","t",None,1,"[]","check me"))
            c.execute("INSERT INTO _kpi_results VALUES (?,?,?,?,?,?,?,?)",("sales","p","1","42","EGP","{}","p","{}"))
            c.commit(); c.close()
            r=build_report(cfg,run.id)
            text=render_report(r)
            self.assertEqual(r["rows"],7)
            self.assertIn("warn=1",text)
            self.assertIn("p/sales = 42 EGP",text)


if __name__=="__main__":
    unittest.main(verbosity=2)
