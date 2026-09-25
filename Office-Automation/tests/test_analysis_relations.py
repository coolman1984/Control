"""Analysis and relation inference tests use small local SQLite files; no Excel is required."""
import os
import sqlite3
import tempfile
import unittest

from xl2ai.analyze import analyze_catalog
from xl2ai.catalog import build_catalog
from xl2ai.core.config import load_config
from xl2ai.core.runs import Run
from xl2ai.relations import infer_relations


def make_extract(path, sheets):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE _extraction_log (sheet_index INTEGER, sheet_name TEXT, table_name TEXT, visibility TEXT,
      status TEXT, data_rows INTEGER, columns INTEGER, header_row INTEGER);
    CREATE TABLE _columns (table_name TEXT, position INTEGER, sql_name TEXT, original_header TEXT, xl_col INTEGER,
      xl_col_letter TEXT, sql_type TEXT, kind TEXT, non_null INTEGER, error_cells INTEGER);
    """)
    for idx, (table, cols, rows) in enumerate(sheets, 1):
        con.execute("INSERT INTO _extraction_log VALUES (?,?,?,?,?,?,?,?)",
                    (idx, table, table, "visible", "extracted", len(rows), len(cols), 1))
        ddl = ", ".join(f'"{name}" {typ}' for name, typ, kind in cols)
        con.execute(f'CREATE TABLE "{table}" (_xl_row INTEGER, {ddl})')
        for pos, (name, typ, kind) in enumerate(cols, 1):
            nonnull = sum(r[pos-1] is not None for r in rows)
            con.execute("INSERT INTO _columns VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (table,pos,name,name,pos,chr(64+pos),typ,kind,nonnull,0))
        marks = ",".join("?" * (len(cols)+1))
        con.executemany(f'INSERT INTO "{table}" VALUES ({marks})',
                        [(i+2,*row) for i,row in enumerate(rows)])
    con.commit(); con.close()


class TestAnalyzeRelations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        cfg_path = os.path.join(root, "xl2ai.toml")
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write('[project]\nname="analysis"\n[analysis]\nrelation_sample=100\n')
        self.cfg = load_config(cfg_path)
        self.run = Run.create(self.cfg)
        sid="source-a"
        self.run.m["inputs"]=[{"source_id":sid,"path":"x.xlsx","sha256":"b"*64,"size":1,
                               "mtime":"2026-01-01T00:00:00","hash_mode":"full"}]
        db=self.run.path("extract",sid+".db"); os.makedirs(os.path.dirname(db),exist_ok=True)
        make_extract(db,[
            ("customers",[("customer_id","INTEGER","integer"),("name","TEXT","text")],
             [(1,"A"),(2,"B"),(3,"C"),(4,"D"),(5,"E")]),
            ("orders",[("order_id","INTEGER","integer"),("customer_id","INTEGER","integer"),("note","TEXT","text")],
             [(10,1,"ok"),(11,2,"N/A"),(12,2,"ok"),(13,4,"ok"),(14,5,"ok")]),
        ])
        with self.run.stage("extract") as st:
            st.artifact(db)
            st.detail("sources",[{"source_id":sid,"exit_code":0,"db":self.run.rel(db),
                                  "sheets":{"extracted":2,"skipped":0,"error":0},
                                  "verify_checks":6,"verify_mismatches":0}])
        self.catalog=build_catalog(self.cfg,self.run.id,self.run.m)

    def tearDown(self):
        self.tmp.cleanup()

    def test_profiles_quality_keys_and_row_hash(self):
        analyze_catalog(self.cfg,self.run.id,self.catalog)
        con=sqlite3.connect(self.catalog)
        profiles=con.execute("SELECT COUNT(*) FROM _profile_columns").fetchone()[0]
        keys=con.execute("SELECT COUNT(*) FROM _keys WHERE method='single_column_uniqueness'").fetchone()[0]
        null_tokens=con.execute("SELECT COUNT(*) FROM _dq_findings WHERE code='DQ_NULL_TOKEN'").fetchone()[0]
        hashes=con.execute("SELECT COUNT(*) FROM _table_profiles WHERE row_fingerprint IS NOT NULL").fetchone()[0]
        row_hashes=con.execute("SELECT COUNT(*) FROM _row_hashes").fetchone()[0]
        con.close()
        self.assertEqual(profiles,5)
        self.assertGreaterEqual(keys,2)
        self.assertEqual(null_tokens,1)
        self.assertEqual(hashes,2)
        self.assertGreater(row_hashes,0)

    def test_infers_customer_relationship_but_not_small_text_domain(self):
        analyze_catalog(self.cfg,self.run.id,self.catalog)
        infer_relations(self.cfg,self.run.id,self.catalog)
        con=sqlite3.connect(self.catalog)
        rows=con.execute("""SELECT fc.name,tc.name,r.containment
                            FROM _relationships r
                            JOIN _columns fc ON fc.column_id=r.from_column
                            JOIN _columns tc ON tc.column_id=r.to_column""").fetchall()
        con.close()
        self.assertTrue(any(a=="customer_id" and b=="customer_id" and c>=0.9 for a,b,c in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
