"""Cross-stage smoke test from trusted extraction artifact to AI-facing outputs. No Excel required."""
import os
import sqlite3
import tempfile
import unittest

from xl2ai.analyze import analyze_catalog
from xl2ai.catalog import build_catalog
from xl2ai.changes import detect_changes
from xl2ai.contextpack import build_context_pack
from xl2ai.core.config import load_config
from xl2ai.core.runs import Run
from xl2ai.query import aggregate, compare, schema
from xl2ai.relations import infer_relations
from xl2ai.report import build_report
from xl2ai.rules import run_packs


def make_extract(path, order_rows):
    con=sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE _extraction_log (
      sheet_index INTEGER, sheet_name TEXT, table_name TEXT, visibility TEXT, status TEXT,
      data_rows INTEGER, columns INTEGER, header_row INTEGER, formula_cells INTEGER DEFAULT 0,
      pivot_tables INTEGER DEFAULT 0, merged_in_data INTEGER DEFAULT 0
    );
    CREATE TABLE _columns (
      table_name TEXT, position INTEGER, sql_name TEXT, original_header TEXT, xl_col INTEGER,
      xl_col_letter TEXT, sql_type TEXT, kind TEXT, non_null INTEGER, error_cells INTEGER
    );
    CREATE TABLE customers (_xl_row INTEGER, customer_id INTEGER, name TEXT);
    CREATE TABLE orders (_xl_row INTEGER, order_id INTEGER, customer_id INTEGER, amount REAL);
    """)
    customers=[(2,1,"A"),(3,2,"B"),(4,3,"C"),(5,4,"D"),(6,5,"E")]
    con.executemany("INSERT INTO customers VALUES (?,?,?)",customers)
    con.executemany("INSERT INTO orders VALUES (?,?,?,?)",[(i+2,*r) for i,r in enumerate(order_rows)])
    con.executemany("INSERT INTO _extraction_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",[
        (1,"Customers","customers","visible","extracted",len(customers),2,1,0,0,0),
        (2,"Orders","orders","visible","extracted",len(order_rows),3,1,0,0,0),
    ])
    for table,cols,rows in [
        ("customers",[("customer_id","INTEGER","integer"),("name","TEXT","text")],customers),
        ("orders",[("order_id","INTEGER","integer"),("customer_id","INTEGER","integer"),("amount","REAL","real")],
         [(i+2,*r) for i,r in enumerate(order_rows)]),
    ]:
        for pos,(name,typ,kind) in enumerate(cols,1):
            nonnull=len(rows)
            con.execute("INSERT INTO _columns VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (table,pos,name,name,pos,chr(64+pos),typ,kind,nonnull,0))
    con.commit()
    con.close()


class TestPlatformE2E(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        cfgp=os.path.join(self.tmp.name,"xl2ai.toml")
        with open(cfgp,"w",encoding="utf-8") as f:
            f.write('[project]\nname="e2e"\n[ai]\ncontext_tokens=2500\nquery_rows=20\nquery_bytes=4096\n')
        self.cfg=load_config(cfgp)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, order_rows, sha):
        run=Run.create(self.cfg)
        sid="book"
        run.m["inputs"]=[{"source_id":sid,"path":"book.xlsx","sha256":sha,"size":100,
                          "mtime":"2026-09-21T12:00:00","hash_mode":"full"}]
        db=run.path("extract",sid+".db")
        os.makedirs(os.path.dirname(db),exist_ok=True)
        make_extract(db,order_rows)
        with run.stage("extract") as st:
            st.artifact(db)
            st.detail("sources",[{"source_id":sid,"exit_code":0,"db":run.rel(db),
                                  "sheets":{"extracted":2,"skipped":0,"error":0},
                                  "verify_checks":5,"verify_mismatches":0}])
        cat=build_catalog(self.cfg,run.id,run.m)
        analyze_catalog(self.cfg,run.id,cat)
        infer_relations(self.cfg,run.id,cat)
        run_packs(self.cfg,run.id,cat)
        return run,cat

    def test_full_non_excel_pipeline(self):
        r1,c1=self._run([(10,1,100.0),(11,2,200.0),(12,3,300.0),(13,4,400.0),(14,5,500.0)],"a"*64)
        detect_changes(self.cfg,r1.id,previous_run_id=None,catalog_path=c1)
        build_context_pack(self.cfg,r1.id,c1)

        r2,c2=self._run([(10,1,100.0),(11,2,250.0),(12,3,300.0),(13,4,400.0),(14,5,500.0),(15,1,50.0)],"b"*64)
        detect_changes(self.cfg,r2.id,previous_run_id=r1.id,catalog_path=c2)
        jp,mp,payload=build_context_pack(self.cfg,r2.id,c2)

        self.assertTrue(os.path.isfile(jp))
        self.assertTrue(os.path.isfile(mp))
        self.assertLessEqual(payload["est_tokens"],self.cfg.ai["context_tokens"])

        sch=schema(self.cfg,run_id=r2.id)
        self.assertEqual(sch["row_count"],2)

        orders=[row for row in sch["rows"] if row[3]=="orders"][0][0]
        agg=aggregate(self.cfg,orders,"amount","sum",run_id=r2.id)
        self.assertEqual(agg["rows"][0][0],1600.0)

        diffs=compare(self.cfg,"row",run_id=r2.id)
        self.assertGreaterEqual(diffs["row_count"],1)

        report=build_report(self.cfg,r2.id)
        self.assertEqual(report["sources"],1)
        self.assertEqual(report["tables"],2)
        self.assertEqual(report["rows"],11)

        con=sqlite3.connect(c2)
        try:
            rels=con.execute("SELECT COUNT(*) FROM _relationships WHERE status='inferred'").fetchone()[0]
            changes=con.execute("SELECT COUNT(*) FROM _changes").fetchone()[0]
        finally:
            con.close()
        self.assertGreaterEqual(rels,1)
        self.assertGreaterEqual(changes,1)


if __name__=="__main__":
    unittest.main(verbosity=2)
