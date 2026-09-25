"""Rule-pack and change-detection tests. No Excel is required."""
import os
import sqlite3
import tempfile
import unittest

from xl2ai.catalog import DDL
from xl2ai.changes import detect_changes
from xl2ai.core.config import load_config
from xl2ai.core.errors import Xl2aiError
from xl2ai.core.runs import Run
from xl2ai.rules import load_pack, load_packs, run_packs


class TestRules(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); root=self.tmp.name
        pack=os.path.join(root,"pack.toml")
        with open(pack,"w",encoding="utf-8") as f:
            f.write("""[pack]
name="demo"
version="1.0"
[[rule]]
id="no_negative"
sql="SELECT COUNT(*) FROM {{table:source-a/orders}} WHERE amount < 0"
expect="zero"
severity="error"
[[kpi]]
id="total_amount"
sql="SELECT SUM(amount) FROM {{table:source-a/orders}}"
unit="EGP"
[[key]]
table="source-a/orders"
columns=["amount"]
[[relation]]
from_table="source-a/orders"
from_column="customer_id"
to_table="source-a/customers"
to_column="customer_id"
""")
        cfgp=os.path.join(root,"xl2ai.toml")
        with open(cfgp,"w",encoding="utf-8") as f:
            f.write(f'[project]\nname="rules"\n[rules]\npacks=["{pack.replace(os.sep,"/")}"]\n')
        self.cfg=load_config(cfgp)
        self.run=Run.create(self.cfg)
        db=self.run.path("extract","source-a.db"); os.makedirs(os.path.dirname(db),exist_ok=True)
        c=sqlite3.connect(db)
        c.execute("CREATE TABLE orders (_xl_row INTEGER, amount REAL, customer_id INTEGER)")
        c.execute("CREATE TABLE customers (_xl_row INTEGER, customer_id INTEGER)")
        c.executemany("INSERT INTO orders VALUES (?,?,?)",[(2,10.0,1),(3,20.0,2)])
        c.executemany("INSERT INTO customers VALUES (?,?)",[(2,1),(3,2)])
        c.commit(); c.close()
        cat=self.run.path("catalog.db"); c=sqlite3.connect(cat); c.executescript(DDL)
        c.execute("INSERT INTO _sources VALUES (?,?,?,?,?,?,?,?,?)",
                  ("source-a","x","h",1,"t","full","extract/source-a.db",0,None))
        c.execute("INSERT INTO _tables VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("t1","source-a","Orders","orders","extract/source-a.db",2,2,1,"visible","fp1",None,None))
        c.execute("INSERT INTO _tables VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("t2","source-a","Customers","customers","extract/source-a.db",2,1,1,"visible","fp2",None,None))
        c.executemany("INSERT INTO _columns VALUES (?,?,?,?,?,?,?,?,?,?,?)",[
            ("t1.c1","t1",1,"amount","Amount",1,"A","REAL","real",2,0),
            ("t1.c2","t1",2,"customer_id","Customer ID",2,"B","INTEGER","integer",2,0),
            ("t2.c1","t2",1,"customer_id","Customer ID",1,"A","INTEGER","integer",2,0),
        ])
        c.commit(); c.close()
        self.cat=cat

    def tearDown(self): self.tmp.cleanup()

    def test_pack_validation_rejects_duplicate_ids_and_bad_severity(self):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,"bad.toml")
            with open(p,"w",encoding="utf-8") as f:
                f.write("""[pack]
name="bad"
version="1"
[[rule]]
id="x"
sql="SELECT 1"
severity="warn"
[[rule]]
id="x"
sql="SELECT 1"
severity="warn"
""")
            with self.assertRaises(Xl2aiError):
                load_pack(p)
            with open(p,"w",encoding="utf-8") as f:
                f.write("""[pack]
name="bad"
version="1"
[[rule]]
id="x"
sql="SELECT 1"
severity="fatal"
""")
            with self.assertRaises(Xl2aiError):
                load_pack(p)

    def test_duplicate_pack_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            paths=[]
            for n in ("a","b"):
                p=os.path.join(d,n+".toml")
                with open(p,"w",encoding="utf-8") as f:
                    f.write('[pack]\nname="same"\nversion="1"\n')
                paths.append(p)
            with self.assertRaises(Xl2aiError):
                load_packs(paths)

    def test_rule_and_kpi(self):
        run_packs(self.cfg,self.run.id,self.cat)
        c=sqlite3.connect(self.cat)
        rule=c.execute("SELECT status FROM _rule_results WHERE rule_id='no_negative'").fetchone()[0]
        kpi=c.execute("SELECT value,unit FROM _kpi_results WHERE kpi_id='total_amount'").fetchone()
        key_status=c.execute("SELECT status FROM _keys WHERE table_id='t1' AND method='pack'").fetchone()[0]
        rel_status=c.execute("SELECT status FROM _relationships WHERE from_column='t1.c2' AND to_column='t2.c1'").fetchone()[0]
        c.close()
        self.assertEqual(rule,"pass")
        self.assertEqual(kpi,("30.0","EGP"))
        self.assertEqual(key_status,"confirmed")
        self.assertEqual(rel_status,"confirmed")


class TestChanges(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); root=self.tmp.name
        cfgp=os.path.join(root,"xl2ai.toml")
        with open(cfgp,"w",encoding="utf-8") as f:f.write('[project]\nname="changes"\n')
        self.cfg=load_config(cfgp)
        self.r1=Run.create(self.cfg); self.r2=Run.create(self.cfg)
        self._cat(self.r1,5,"schema-a","rows-a","10")
        self._cat(self.r2,8,"schema-b","rows-b","15")

    def tearDown(self): self.tmp.cleanup()

    def _cat(self,run,rows,schema,rowfp,kpi,top='[["A",3],["B",2]]',distinct=2):
        p=run.path("catalog.db"); c=sqlite3.connect(p); c.executescript(DDL)
        c.execute("INSERT INTO _sources VALUES (?,?,?,?,?,?,?,?,?)",("s","x","hash"+str(rows),1,"t","full","x.db",0,None))
        c.execute("INSERT INTO _tables VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",("t","s","Sheet1","data","x.db",rows,1,1,"visible",schema,None,None))
        c.execute("INSERT INTO _columns VALUES (?,?,?,?,?,?,?,?,?,?,?)",("t.c1","t",1,"category","Category",1,"A","TEXT","text",rows,0))
        c.execute("INSERT INTO _profile_columns VALUES (?,?,?,?,?,?,?,?,?)",
                  ("t.c1",rows,0,distinct,"A","B",None,top,'["A","B"]'))
        c.execute("INSERT INTO _table_profiles VALUES (?,?,?,?)",("t",rows,rowfp,"multiset_sha256"))
        hashes=[("same",min(rows,4))]
        if rows <= 5:
            hashes.append(("old-only",1))
        else:
            hashes.append(("new-only",rows-min(rows,4)))
        c.executemany("INSERT INTO _row_hashes VALUES (?,?,?)",(("t",h,n) for h,n in hashes))
        c.execute("INSERT INTO _kpi_results VALUES (?,?,?,?,?,?,?,?)",("sales","p","1",kpi,"EGP","{}","p","{}"))
        c.commit(); c.close()

    def test_detects_schema_volume_value_and_kpi_changes(self):
        detect_changes(self.cfg,self.r2.id,previous_run_id=self.r1.id)
        c=sqlite3.connect(self.r2.path("catalog.db"))
        kinds={r[0] for r in c.execute("SELECT kind FROM _changes")}
        c.close()
        self.assertTrue({"source","schema","volume","value","row","kpi"}.issubset(kinds))

    def test_distinguishes_category_membership_from_frequency_shift(self):
        r3=Run.create(self.cfg)
        r4=Run.create(self.cfg)
        self._cat(r3,5,"same","same","10",top='[["A",3],["B",2]]',distinct=2)
        self._cat(r4,5,"same","same","10",top='[["A",2],["B",3]]',distinct=2)
        detect_changes(self.cfg,r4.id,previous_run_id=r3.id)
        c=sqlite3.connect(r4.path("catalog.db"))
        kinds={r[0] for r in c.execute("SELECT kind FROM _changes")}
        c.close()
        self.assertIn("distribution",kinds)
        self.assertNotIn("category",kinds)

        r5=Run.create(self.cfg)
        self._cat(r5,5,"same","same","10",top='[["A",2],["C",3]]',distinct=2)
        detect_changes(self.cfg,r5.id,previous_run_id=r4.id)
        c=sqlite3.connect(r5.path("catalog.db"))
        kinds={r[0] for r in c.execute("SELECT kind FROM _changes")}
        c.close()
        self.assertIn("category",kinds)


if __name__=="__main__":
    unittest.main(verbosity=2)
