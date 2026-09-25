"""Separate table regions, grouped headers and formula lineage: pure detectors (no Excel, no files), then lineage
resolution over a synthetic catalog. The whole path through a real workbook is in test_direct_engine.py."""
import os
import sqlite3
import tempfile
import unittest

from xl2ai.extract.regions import find_regions, header_groups, multiple_tables, parse_address
from xl2ai.lineage import build_lineage, formula_refs

N = None


class TestFindRegions(unittest.TestCase):
    def test_single_table_is_one_region(self):
        g = [["id", "name"], [1.0, "a"], [2.0, "b"]]
        regions = find_regions(g)
        self.assertEqual(len(regions), 1)
        self.assertFalse(multiple_tables(regions))
        self.assertEqual(regions[0]["header_row"], 1)

    def test_stacked_tables_after_a_gap_with_a_new_header(self):
        g = [["id", "qty"], [1.0, 2.0], [2.0, 3.0], [N, N], [N, N], ["region", "total"], ["E", 5.0]]
        regions = find_regions(g, first_row=10, first_col=3)
        self.assertTrue(multiple_tables(regions))
        self.assertEqual([(r["first_row"], r["last_row"]) for r in regions], [(10, 12), (15, 16)])
        self.assertEqual(regions[1]["first_col"], 3)
        self.assertEqual(regions[1]["header_row"], 15)

    def test_blank_separated_groups_without_a_new_header_stay_one_table(self):
        g = [["id", "qty"], [1.0, 2.0], [N, N], [N, N], [3.0, 4.0], [5.0, 6.0]]
        self.assertEqual(len(find_regions(g)), 1)

    def test_single_blank_row_never_splits(self):
        g = [["id", "qty"], [1.0, 2.0], [N, N], ["code", "desc"], ["x", "y"]]
        self.assertEqual(len(find_regions(g)), 1)

    def test_side_by_side_tables_split_on_an_empty_column(self):
        g = [["id", "qty", N, "code", "desc"], [1.0, 2.0, N, "x", "y"], [2.0, 3.0, N, "z", "w"],
             [N, N, N, "q", "r"]]
        regions = find_regions(g)
        self.assertEqual([(r["first_col"], r["last_col"]) for r in regions], [(1, 2), (4, 5)])

    def test_spacer_column_inside_one_table_does_not_split(self):
        g = [["id", "qty", N, "code"], [1.0, 2.0, N, "x"], [2.0, 3.0, N, "z"]]
        self.assertEqual(len(find_regions(g)), 1)

    def test_title_is_a_note_not_a_table(self):
        g = [["Monthly report", N], [N, N], [N, N], ["id", "qty"], [1.0, 2.0]]
        regions = find_regions(g)
        self.assertEqual([r["kind"] for r in regions], ["note", "table"])
        self.assertFalse(multiple_tables(regions))

    def test_empty_grid(self):
        self.assertEqual(find_regions([]), [])


class TestHeaderGroups(unittest.TestCase):
    def test_label_run_groups(self):
        g = [[N, "Q1", N, "Q2", N], ["item", "Plan", "Actual", "Plan", "Actual"], ["a", 1.0, 2.0, 3.0, 4.0]]
        groups, method = header_groups(g, 1)
        self.assertEqual(groups, {2: ["Q1"], 3: ["Q1"], 4: ["Q2"], 5: ["Q2"]})
        self.assertEqual(method, "label_run")

    def test_merged_area_bounds_a_lone_label(self):
        g = [[N, "Q1", N, N], ["a", "b", "c", "d"], [1.0, 2.0, 3.0, 4.0]]
        groups, method = header_groups(g, 1, merged=[("B1:C1", "Q1")])
        self.assertEqual(groups, {2: ["Q1"], 3: ["Q1"]})
        self.assertEqual(method, "merged_area")

    def test_lone_unmerged_title_is_not_a_group(self):
        self.assertEqual(header_groups([["Sales report", N, N], ["a", "b", "c"]], 1), ({}, None))

    def test_two_levels(self):
        g = [[N, "2024", N, N, N], [N, "Q1", N, "Q2", N], ["item", "P", "A", "P", "A"]]
        groups, _ = header_groups(g, 2, merged=[("B1:E1", "2024")])
        self.assertEqual(groups[2], ["2024", "Q1"])
        self.assertEqual(groups[5], ["2024", "Q2"])

    def test_offsets_are_excel_columns(self):
        g = [[N, "Q1", N], ["item", "Plan", "Actual"]]
        groups, _ = header_groups(g, 1, first_row=5, first_col=4, merged=[("E5:F5", "Q1")])
        self.assertEqual(sorted(groups), [5, 6])

    def test_no_header(self):
        self.assertEqual(header_groups([["a"]], None), ({}, None))

    def test_parse_address(self):
        self.assertEqual(parse_address("$B$2:D3"), (2, 2, 3, 4))
        self.assertEqual(parse_address("AA10"), (10, 27, 10, 27))
        self.assertIsNone(parse_address("Sheet1!A1"))


class TestFormulaRefs(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(formula_refs("='Raw Data'!D2*1.14"), [(None, "Raw Data")])
        self.assertEqual(formula_refs("=SUM(Jan!A:A)+[Sales.xlsx]Feb!B1"), [(None, "Jan"), ("Sales.xlsx", "Feb")])
        self.assertEqual(formula_refs(r"='C:\d\[B k.xlsx]My Sheet'!R1C1"), [("B k.xlsx", "My Sheet")])
        self.assertEqual(formula_refs("='It''s'!A1"), [(None, "It's")])
        self.assertEqual(formula_refs("=[1]Prices!A1"), [("1", "Prices")])

    def test_no_refs(self):
        self.assertEqual(formula_refs("=A1+B2*R[1]C"), [])
        self.assertEqual(formula_refs('=IF(A1="x!y",1,0)'), [])
        self.assertEqual(formula_refs(None), [])

    def test_duplicates_removed(self):
        self.assertEqual(formula_refs("=Data!A1+Data!B1"), [(None, "Data")])


def _catalog(path):
    from xl2ai.catalog import DDL
    con = sqlite3.connect(path)
    con.executescript(DDL)
    con.execute("INSERT INTO _sources (source_id, path, db_rel) VALUES ('s1', 'C:/x/sales.xlsx', 'x')")
    con.execute("INSERT INTO _sources (source_id, path, db_rel) VALUES ('s2', 'C:/x/Budget 2024.xlsx', 'x')")
    for tid, sid, sheet in [("s1/raw", "s1", "Raw Data"), ("s1/rep", "s1", "Report"), ("s2/b", "s2", "Plan")]:
        con.execute("INSERT INTO _tables (table_id, source_id, sheet_name, table_name, db_rel, row_count, column_count,"
                    " schema_fingerprint) VALUES (?,?,?,?,'x',1,1,'f')",
                    (tid, sid, sheet, sheet.replace(" ", "_")))
    for cid, tid, name, pos in [("s1/rep/a", "s1/rep", "Actual", 1), ("s1/rep/b", "s1/rep", "Budget", 2),
                                ("s1/rep/c", "s1/rep", "Other", 3), ("s1/rep/d", "s1/rep", "Self", 4)]:
        con.execute("INSERT INTO _columns (column_id, table_id, position, name) VALUES (?,?,?,?)",
                    (cid, tid, pos, name))
    return con


class TestBuildLineage(unittest.TestCase):
    def test_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            con = _catalog(os.path.join(d, "c.db"))
            con.executemany("INSERT INTO _formulas VALUES (?,?,?,?)", [
                ("s1/rep/a", "s1/rep", 1, "=SUMIF('Raw Data'!C:C,A2,'Raw Data'!D:D)"),
                ("s1/rep/b", "s1/rep", 1, "='C:\\x\\[Budget 2024.xlsx]Plan'!B2"),
                ("s1/rep/c", "s1/rep", 1, "=[Elsewhere.xlsx]Jan!A1"),
                ("s1/rep/d", "s1/rep", 1, "=Report!A1*2"),
            ])
            self.assertEqual(build_lineage(con), 3)
            rows = {r[0]: r[1:] for r in con.execute(
                "SELECT column_id, target_table_id, status, method FROM _lineage")}
            self.assertEqual(rows["s1/rep/a"], ("s1/raw", "resolved", "formula_sample"))
            self.assertEqual(rows["s1/rep/b"], ("s2/b", "resolved", "formula_sample"))
            self.assertEqual(rows["s1/rep/c"][:2], (None, "external"))
            self.assertNotIn("s1/rep/d", rows)          # own-sheet arithmetic is not lineage
            con.close()

    def test_full_formula_refs_win_over_sample(self):
        with tempfile.TemporaryDirectory() as d:
            con = _catalog(os.path.join(d, "c.db"))
            con.execute("INSERT INTO _formulas VALUES ('s1/rep/a','s1/rep',1,'=[1]Plan!A1')")
            con.execute("INSERT INTO _formula_refs VALUES ('s1/rep/a','s1/rep','Budget 2024.xlsx','Plan',40,'=[1]Plan!A1')")
            build_lineage(con)
            rows = con.execute("SELECT target_table_id, cells, method FROM _lineage").fetchall()
            self.assertEqual(rows, [("s2/b", 40, "all_formulas")])
            con.close()


if __name__ == "__main__":
    unittest.main()


class TestExcelEngineFormulaRefs(unittest.TestCase):
    """record_formula_refs against a fake Excel: one COM read per block, distinct R1C1 formulas parsed once."""

    def test_counts_references_per_sheet(self):
        from xl2ai.extract.sheet import record_formula_refs

        class Rng:
            def __init__(self, vals):
                self.FormulaR1C1 = vals

        class Ws:
            def Cells(self, r, c):
                return (r, c)

            def Range(self, a, b):
                n = b[0] - a[0] + 1
                col = [("='Raw Data'!R[0]C[2]*2",)] * (n - 1) + [(5.0,)]      # last row typed over: a value
                return Rng(tuple(col))

        class Sess:
            calls = 0

            def call(self, fn):
                Sess.calls += 1
                return fn()

        class Plan:
            xl_col, name = 3, "amount"

        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE _formula_refs (table_name, sql_name, ref_workbook, ref_sheet, cells, sample)")
        record_formula_refs(Sess(), Ws(), con, "Report", Plan(), 2, 11)
        self.assertEqual(con.execute("SELECT ref_workbook, ref_sheet, cells FROM _formula_refs").fetchall(),
                         [(None, "Raw Data", 9)])
        self.assertEqual(Sess.calls, 1)
