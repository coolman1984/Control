"""Context-pack and query-tool tests. No Excel is required."""
import os
import sqlite3
import tempfile
import unittest

from xl2ai.catalog import DDL
from xl2ai.contextpack import build_context_pack
from xl2ai.core.config import load_config
from xl2ai.core.errors import Xl2aiError
from xl2ai.core.runs import Run
from xl2ai.query import compare, describe, meta, sample, schema, sql, trace


class TestContextAndQuery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=self.tmp.name
        cfgp=os.path.join(root,"xl2ai.toml")
        with open(cfgp,"w",encoding="utf-8") as f:
            f.write('[project]\nname="ai"\n[ai]\ncontext_tokens=700\nquery_rows=2\nquery_bytes=2048\nquery_timeout=2\n')
        self.cfg=load_config(cfgp)
        self.run=Run.create(self.cfg)
        db=self.run.path("extract","s.db")
        os.makedirs(os.path.dirname(db),exist_ok=True)
        c=sqlite3.connect(db)
        c.execute("CREATE TABLE data (_xl_row INTEGER,id INTEGER,value TEXT)")
        c.executemany("INSERT INTO data VALUES (?,?,?)",[(2,1,"a"),(3,2,"b"),(4,3,"c")])
        c.commit()
        c.close()
        cat=self.run.path("catalog.db")
        c=sqlite3.connect(cat)
        c.executescript(DDL)
        c.execute("INSERT INTO _sources VALUES (?,?,?,?,?,?,?,?,?)",("s","x","h",3,"2026-01-01","full","extract/s.db",0,None))
        c.execute("INSERT INTO _tables VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",("t1","s","Sheet1","data","extract/s.db",3,2,1,"visible","fp",None,None))
        c.executemany("INSERT INTO _columns VALUES (?,?,?,?,?,?,?,?,?,?,?)",[
            ("t1.c1","t1",1,"id","ID",1,"A","INTEGER","integer",3,0),
            ("t1.c2","t1",2,"value","Value",2,"B","TEXT","text",3,0)])
        c.executemany("INSERT INTO _profile_columns VALUES (?,?,?,?,?,?,?,?,?)",[
            ("t1.c1",3,0,3,"1","3",2.0,'[[1,1],[2,1]]','[1,2]'),
            ("t1.c2",3,0,3,"a","c",None,'[["a",1],["b",1]]','["a","b"]')])
        c.execute("INSERT INTO _dictionary VALUES (?,?,?,?,?,?,?,?,?)",
                  ("value","شرح عربي مفصل للمصطلح",'["قيمة"]',"","t1.c2","confirmed","pack","p","1"))
        c.execute("INSERT INTO _relationships VALUES (?,?,?,?,?,?,?,?,?)",
                  ("r1","t1.c2","t1.c1","reference",None,"confirmed","pack",1.0,'{"pack":"p"}'))
        c.execute("INSERT INTO _dq_findings VALUES (?,?,?,?,?,?,?,?)",
                  ("d1","DQ_TEST","warn","t1","t1.c2",1,'["x"]',"demo warning"))
        c.execute("INSERT INTO _changes VALUES (?,?,?,?,?,?,?)",("c1","volume","info","s/Sheet1","2","3","{}"))
        c.commit()
        c.close()
        self.cat=cat

    def tearDown(self):
        self.tmp.cleanup()

    def test_context_pack_is_deterministic_and_budgeted(self):
        _,_,p1=build_context_pack(self.cfg,self.run.id,self.cat)
        with open(self.run.path("ai","context_pack.json"),encoding="utf-8") as f:
            b1=f.read()
        _,_,p2=build_context_pack(self.cfg,self.run.id,self.cat)
        with open(self.run.path("ai","context_pack.json"),encoding="utf-8") as f:
            b2=f.read()
        self.assertEqual(b1,b2)
        self.assertLessEqual(p2["est_tokens"],self.cfg.ai["context_tokens"])
        self.assertEqual(p2["definitions"][0]["term"],"value")
        self.assertIn("hash_mode",p2["sources"][0])
        self.assertIn("format",p2["sources"][0])
        self.assertNotIn("kind",p2["sources"][0])
        self.assertEqual(p2["relationships"][0]["status"],"confirmed")
        self.assertIsNone(p2["relationships"][0]["containment"])

    def test_schema_and_describe_obey_query_caps(self):
        c=sqlite3.connect(self.cat)
        c.execute("INSERT INTO _tables VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("t2","s","Sheet2","data2","extract/s.db",1,1,1,"visible","fp2",None,None))
        c.execute("INSERT INTO _columns VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  ("t2.c1","t2",1,"id","ID",1,"A","INTEGER","integer",1,0))
        c.commit()
        c.close()

        self.cfg.ai["query_rows"]=1
        sch=schema(self.cfg,run_id=self.run.id)
        self.assertEqual(sch["row_count"],1)
        self.assertTrue(sch["truncated"])
        desc=describe(self.cfg,"t1",run_id=self.run.id)
        self.assertEqual(desc["row_count"],1)
        self.assertTrue(desc["truncated"])

    def test_sample_is_capped_and_trace_has_evidence(self):
        out=sample(self.cfg,"t1",limit=10,run_id=self.run.id)
        self.assertEqual(out["row_count"],2)
        self.assertTrue(out["truncated"])
        tr=trace(self.cfg,"t1",3,run_id=self.run.id)
        self.assertEqual(tr["rows"][0][1],2)
        self.assertEqual(tr["evidence"][0]["xl_row"],3)

    def test_meta_exposes_catalog_sections(self):
        out=meta(self.cfg,"quality",run_id=self.run.id)
        self.assertEqual(out["row_count"],1)
        self.assertEqual(out["rows"][0][0],"DQ_TEST")
        rel=meta(self.cfg,"relationships",run_id=self.run.id)
        self.assertEqual(rel["rows"][0][0],"confirmed")
        defs=meta(self.cfg,"definitions",run_id=self.run.id)
        self.assertEqual(defs["rows"][0][2],["قيمة"])

    def test_compare_reads_capped_deterministic_changes(self):
        out=compare(self.cfg,"volume",run_id=self.run.id)
        self.assertEqual(out["row_count"],1)
        self.assertEqual(out["rows"][0][0],"volume")
        self.assertEqual(out["rows"][0][3],2)
        self.assertEqual(out["rows"][0][4],3)

    def test_sql_is_read_only(self):
        ok=sql(self.cfg,"s","SELECT COUNT(*) AS n FROM data",run_id=self.run.id)
        self.assertEqual(ok["rows"][0][0],3)
        with self.assertRaises(Xl2aiError):
            sql(self.cfg,"s","DELETE FROM data",run_id=self.run.id)
        with self.assertRaises(Xl2aiError):
            sql(self.cfg,"s","PRAGMA table_info(data)",run_id=self.run.id)
        with self.assertRaises(Xl2aiError):
            sql(self.cfg,"s","SELECT load_extension('x')",run_id=self.run.id)


if __name__=="__main__":
    unittest.main(verbosity=2)
