"""Tests for excel_to_sqlite.py.

Unit tests need nothing. Integration tests drive real Excel through COM (they build their own fixtures with
tests/make_fixtures.py, ~30 s) and are skipped when Excel is not available.

    python -m unittest discover -s tests -v
    set XL2SQL_PERF=1   -> also builds and times a 200k-row workbook
"""
import contextlib
import datetime as dt
import io
import os
import sqlite3
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from types import SimpleNamespace  # noqa: E402

from xl2ai.extract import coltypes as _coltypes, dates as _dates, layout as _layout  # noqa: E402
from xl2ai.extract import names as _names, pipeline as _pipeline, sheet as _sheet  # noqa: E402

m = SimpleNamespace(                    # flat view of the extractor API used by these tests
    classify_format=_dates.classify_format, serial_to_iso=_dates.serial_to_iso,
    build_columns=_names.build_columns, clean_header=_names.clean_header, sanitize_table=_names.sanitize_table,
    col_letter=_names.col_letter, ColStat=_coltypes.ColStat, ColPlan=_coltypes.ColPlan,
    convert_column=_coltypes.convert_column, clean_surrogates=_coltypes.clean_surrogates,
    find_header=_layout.find_header, iter_data=_sheet.iter_data, main=_pipeline.main)


class TestDates(unittest.TestCase):
    def test_classify(self):
        cases = {"yyyy-mm-dd": "date", "dd/mm/yyyy": "date", "d-mmm-yy": "date", "mmmm": "date",
                 "yyyy-mm-dd hh:mm:ss": "datetime", "m/d/yy h:mm": "datetime", "hh:mm:ss": "time",
                 "[h]:mm:ss": "time", "h:mm AM/PM": "time", "[$-409]dddd, mmmm dd, yyyy": "date",
                 "General": None, "0.00": None, "#,##0": None, "0.00E+00": None, '"Day "0': None,
                 '_-* #,##0.00_-;-* #,##0.00_-;_-* "-"??_-;_-@_-': None, "@": None, "": None, None: None,
                 "[Red]0.0": None, '0 "days"': None}
        for fmt, want in cases.items():
            self.assertEqual(m.classify_format(fmt), want, fmt)

    def test_serial_1900_system(self):
        f = m.serial_to_iso
        self.assertEqual(f(1.0, "date", False), "1900-01-01")
        self.assertEqual(f(59.0, "date", False), "1900-02-28")
        self.assertEqual(f(60.0, "date", False), "1900-02-29")        # Excel's phantom leap day
        self.assertEqual(f(61.0, "date", False), "1900-03-01")
        self.assertEqual(f(36526.0, "date", False), "2000-01-01")
        self.assertEqual(f(44927.0, "date", False), "2023-01-01")
        self.assertEqual(f(44927.5, "datetime", False), "2023-01-01 12:00:00")
        self.assertEqual(f(36526.0, "datetime", False), "2000-01-01 00:00:00")   # always a full timestamp

    def test_serial_1904_system(self):
        self.assertEqual(m.serial_to_iso(0.0, "date", True), "1904-01-01")
        self.assertEqual(m.serial_to_iso(1461.0, "date", True), "1908-01-01")      # 1904 is a leap year

    def test_time_and_elapsed(self):
        self.assertEqual(m.serial_to_iso(0.5, "time", False), "12:00:00")
        self.assertEqual(m.serial_to_iso(0.999988426, "time", False), "23:59:59")
        self.assertEqual(m.serial_to_iso(1.5, "time", False), "36:00:00")

    def test_out_of_range_is_kept_as_number(self):
        self.assertEqual(m.serial_to_iso(-3.0, "date", False), "-3")
        self.assertEqual(m.serial_to_iso(9e9, "date", False), "9000000000")


class TestNaming(unittest.TestCase):
    def test_headers(self):
        names, gen, dup = m.build_columns(["A b", "A_b", "a B", None, "ok", "ok", " spaced ", 2026.0, "_xl_row", ""])
        self.assertEqual(names, ["A_b", "A_b_2", "a_B_3", "col_4", "ok", "ok_2", "spaced", "2026", "xl_row", "col_10"])
        self.assertEqual((gen, dup), (2, 3))

    def test_error_header(self):
        self.assertEqual(m.clean_header(-2146826246), "N_A")                   # a #N/A header cell

    def test_tables(self):
        used = set()
        got = [m.sanitize_table(n, used) for n in ("Bob's Sheet", "a b", "a_b", "2024", "sqlite_x", "  ", "日本語", "PV ", "pv")]
        self.assertEqual(got, ["Bob_s_Sheet", "a_b", "a_b_2", "t_2024", "t_sqlite_x", "sheet", "日本語", "PV", "pv_2"])

    def test_col_letter(self):
        self.assertEqual([m.col_letter(n) for n in (1, 26, 27, 52, 703, 16384)], ["A", "Z", "AA", "AZ", "AAA", "XFD"])


class TestTyping(unittest.TestCase):
    def stat(self, col):
        s = m.ColStat()
        s.update(col)
        return s

    def test_kinds(self):
        E = -2146826246
        self.assertEqual(self.stat((1.0, 2.0, None)).kind(), "int")
        self.assertEqual(self.stat((1.0, 2.5)).kind(), "real")
        self.assertEqual(self.stat((2.0 ** 60,)).kind(), "real")             # beyond exact-integer range
        self.assertEqual(self.stat(("a", None)).kind(), "text")
        self.assertEqual(self.stat((True, False)).kind(), "bool")
        self.assertEqual(self.stat((1.0, "a")).kind(), "mixed")
        self.assertEqual(self.stat((1.0, True)).kind(), "mixed")
        self.assertEqual(self.stat((None, None)).kind(), "text")
        s = self.stat((1.0, E, None))
        self.assertEqual((s.kind(), s.nerr), ("int", 1))
        self.assertEqual(self.stat((E, E)).kind(), "text")                  # errors only: nothing typed

    def test_stats_accumulate_across_blocks(self):
        s = m.ColStat()
        s.update((1.0, 2.0))
        s.update((3.5,))
        self.assertEqual(s.kind(), "real")
        s.update(("x",))
        self.assertEqual(s.kind(), "mixed")

    def plan(self, col, strict=False):
        s = self.stat(col)
        return m.ColPlan("c", 1, "c", s, strict)

    def test_convert(self):
        E = -2146826246
        col = (1.0, E, None, 3.0)
        vals, errs = m.convert_column(col, self.plan(col), [10, 11, 12, 13], False)
        self.assertEqual(vals, [1, None, None, 3])
        self.assertTrue(all(type(v) is int for v in vals if v is not None))
        self.assertEqual(errs, [(1, "#N/A")])
        col = (True, None, False)
        self.assertEqual(m.convert_column(col, self.plan(col), [1, 2, 3], False)[0], [1, None, 0])
        col = (1.5, "a", True, None)
        self.assertEqual(m.convert_column(col, self.plan(col), [1, 2, 3, 4], False)[0], [1.5, "a", "TRUE", None])
        self.assertEqual(self.plan(col).sql_type, "BLOB")
        self.assertEqual(self.plan(col, strict=True).sql_type, "ANY")

    def test_surrogates_are_repaired(self):
        self.assertEqual(m.clean_surrogates(["ok", "bad\ud800x", 5]), ["ok", "bad�x", 5])


class TestLayout(unittest.TestCase):
    def test_header_detection(self):
        blk = [(None, "T07", "T07", None), ("Name", "Qty", "Amt", "Qty"), ("a", 1.0, 2.0, 3.0)]
        self.assertEqual(m.find_header(blk, 4), 1)
        self.assertIsNone(m.find_header([(1.0, 2.0), (3.0, 4.0)], 2))
        self.assertIsNone(m.find_header([("a",), ], 1))                       # header with no data below it

    def test_iter_data_skips_blank_rows_and_keeps_positions(self):
        blk = [("h1", "h2"), (1.0, 2.0), (None, None), (3.0, None)]
        counters = {"blank": 0}
        got = list(m.iter_data([(5, blk)], 6, counters))
        self.assertEqual(got, [([6, 8], [(1.0, 3.0), (2.0, None)])])
        self.assertEqual(counters["blank"], 1)


# ------------------------------------------------------------------------------------------------- integration
def started_pids(text):
    """PIDs of the Excel instances the tool itself started in this run (from its own log)."""
    return [int(p) for p in re.findall(r"Excel started \(pid (\d+)\)", text)]


def assert_no_leak(test, text, expect_started=True):
    """Every Excel the tool started must be gone. Immune to other Excel activity on the machine."""
    from xl2ai.extract.com import pid_alive
    pids = started_pids(text)
    if expect_started:
        test.assertTrue(pids, "the tool did not report starting Excel")
    for pid in pids:
        test.assertFalse(pid_alive(pid), f"Excel pid {pid} started by the tool is still running")


def have_excel():
    try:
        import win32com.client
        win32com.client.gencache  # noqa: B018
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Excel.Application"))
        return True
    except Exception:
        return False


def run_tool(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = m.main([str(a) for a in args])
    return code, buf.getvalue()


@unittest.skipUnless(have_excel(), "Excel is not installed")
class TestIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import fixture_cache
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = cls.tmp.name
        cls.paths = fixture_cache.get(large=bool(os.environ.get("XL2SQL_PERF")))     # built once per test process
        cls.out = os.path.join(cls.dir, "out")
        cls.code, cls.text = run_tool(cls.paths["edge"], "-o", cls.out)
        cls.db = sqlite3.connect(os.path.join(cls.out, "edge_cases.db"))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def rows(self, sql, *a):
        return self.db.execute(sql, a).fetchall()

    def col(self, table, name):
        return [r[0] for r in self.rows(f'SELECT "{name}" FROM "{table}" ORDER BY _xl_row')]

    def log(self, sheet):
        cur = self.db.execute("SELECT * FROM _extraction_log WHERE sheet_name=?", (sheet,))
        names = [d[0] for d in cur.description]
        return dict(zip(names, cur.fetchone()))

    # ---- run level ------------------------------------------------------------------------------------
    def test_run_is_clean(self):
        self.assertEqual(self.code, 0, self.text)
        meta = dict(self.rows("SELECT key, value FROM _meta"))
        self.assertEqual(meta["verify_mismatches"], "0")
        self.assertGreater(int(meta["verify_checks"]), 40)
        self.assertEqual(self.rows("SELECT count(*) FROM _verification WHERE ok = 0"), [(0,)])
        assert_no_leak(self, self.text)
        self.assertEqual(self.rows("SELECT count(*) FROM _verification WHERE check_name='cells_total' AND ok=1"),
                         [(self.rows("SELECT count(*) FROM _extraction_log WHERE status='extracted'")[0][0],)])
        self.assertFalse(os.path.exists(os.path.join(self.out, "edge_cases.db.partial")))

    def test_sheet_statuses(self):
        st = {r[0]: (r[1], r[2]) for r in self.rows("SELECT sheet_name, status, message FROM _extraction_log")}
        self.assertEqual(st["Empty"], ("skipped", "empty sheet"))
        self.assertEqual(st["ChartOnly"][0], "skipped")
        self.assertEqual(self.log("Hidden")["visibility"], "hidden")
        self.assertEqual(self.log("VeryHidden")["visibility"], "very hidden")
        self.assertEqual(self.log("Hidden")["data_rows"], 2)
        self.assertEqual(self.log("VeryHidden")["data_rows"], 1)

    # ---- Types sheet: one assertion per value kind ------------------------------------------------------
    def test_integers_and_reals(self):
        self.assertEqual(self.col("Types", "Int"), [1, 2, 3, -4, 1000000])
        self.assertEqual({r[0] for r in self.rows('SELECT typeof("Int") FROM Types')}, {"integer"})
        self.assertEqual(self.col("Types", "Real")[2], 0.30000000000000004)      # bit-exact double
        self.assertEqual(self.col("Types", "Real")[4], 1e-300)

    def test_text_is_untouched(self):
        t = self.col("Types", "Text")
        self.assertEqual(t, ["a", "b c", "  padded  ", "multi\nline", 'quote"s and \'apostrophes\''])
        self.assertEqual(self.col("Types", "ZipCode"), ["00123", "00456", "0", "007", "1E5"])
        self.assertEqual(self.col("Types", "Unicode"), ["日本語", "😀 emoji", "مرحبا", "café", "한국어"])
        long = self.col("Types", "Long")
        self.assertEqual((len(long[0]), long[0][:3], len(long[3])), (32000, "xxx", 5000))
        self.assertIsNone(long[2])

    def test_dates_times(self):
        self.assertEqual(self.col("Types", "Date"), ["1900-01-01", "1900-02-28", "1900-02-29", "2023-01-01", "2000-01-01"])
        self.assertEqual(self.col("Types", "DateTime"),
                         ["2023-01-01 12:00:00", "2023-01-01 06:00:00", "2000-01-01 00:00:00",
                          "2023-01-02 23:59:59",
                          (dt.datetime(1899, 12, 30) + dt.timedelta(days=60000)).strftime("%Y-%m-%d 00:00:00")])
        self.assertEqual(self.col("Types", "Time"), ["12:00:00", "18:00:00", "23:59:59", "06:00:00", "00:00:00"])
        self.assertEqual(self.col("Types", "Elapsed"), ["36:00:00", "12:00:00", "600:00:00", "48:00:00", "2400:00:00"])

    def test_1904_workbook(self):
        code, text = run_tool(self.paths["d1904"], "-o", self.out)
        self.assertEqual(code, 0, text)
        con = sqlite3.connect(os.path.join(self.out, "edge_1904.db"))
        got = [r[0] for r in con.execute('SELECT "when" FROM D1904 ORDER BY _xl_row')]
        con.close()
        self.assertEqual(got[:3], ["1904-01-01 00:00:00", "1904-01-02 00:00:00", "1908-01-01 00:00:00"])   # serials 0, 1, 1461
        self.assertEqual(got[3], (dt.datetime(1904, 1, 1) + dt.timedelta(days=43000.5)).strftime("%Y-%m-%d %H:%M:%S"))

    def test_bool_mixed_empty(self):
        self.assertEqual(self.col("Types", "Bool"), [1, 0, 1, 1, 0])
        self.assertEqual(self.col("Types", "Mixed"), [1.0, "a", "TRUE", 2.5, None])
        self.assertEqual(self.col("Types", "Empty"), [None] * 5)
        self.assertEqual(self.col("Types", "EmptyStr"), ["", "", "keep", "keep", "keep"])   # '' is not NULL

    def test_big_numbers_are_floats_not_silently_truncated_ints(self):
        big = self.col("Types", "Big")
        self.assertEqual(big[0], float(12345678901234567))
        self.assertEqual(big[3], 1e300)
        self.assertEqual(self.rows("SELECT sql_type FROM _columns WHERE table_name='Types' AND sql_name='Big'"), [("REAL",)])

    def test_error_cells(self):
        self.assertEqual(self.col("Types", "Err"), [None, None, None, None, 42])
        errs = self.rows("SELECT xl_row, xl_col, error FROM _cell_errors WHERE table_name='Types' ORDER BY xl_row")
        self.assertEqual(errs, [(2, 11, "#DIV/0!"), (3, 11, "#N/A"), (4, 11, "#NUM!"), (5, 11, "#NAME?")])

    # ---- layout -----------------------------------------------------------------------------------------------
    def test_duplicate_and_blank_headers(self):
        names = [r[0] for r in self.rows("SELECT sql_name FROM _columns WHERE table_name='Dup_Headers' ORDER BY position")]
        self.assertEqual(names, ["A_b", "A_b_2", "a_B_3", "col_4", "ok", "ok_2", "spaced"])
        orig = [r[0] for r in self.rows("SELECT original_header FROM _columns WHERE table_name='Dup_Headers' ORDER BY position")]
        self.assertEqual(orig[6], " spaced ")                                    # original text is kept

    def test_preamble_blank_rows_and_totals(self):
        lg = self.log("Preamble")
        self.assertEqual((lg["header_row"], lg["data_rows"], lg["blank_rows_skipped"]), (4, 5, 1))
        self.assertEqual(self.col("Preamble", "Name"), ["a", "b", "c", "d", "Total"])
        self.assertEqual([r[0] for r in self.rows("SELECT _xl_row FROM Preamble ORDER BY _xl_row")], [5, 6, 8, 9, 10])
        pre = self.rows("SELECT xl_row, xl_col, value FROM _sheet_preamble WHERE table_name='Preamble' ORDER BY 1, 2")
        self.assertEqual(pre, [(1, 1, "Quarterly report"), (3, 1, "Generated"), (3, 2, "2026-01-01")])

    def test_headerless_sheet(self):
        lg = self.log("NoHeader")
        self.assertIsNone(lg["header_row"])
        self.assertEqual(lg["data_rows"], 4)
        self.assertEqual(self.rows("SELECT col_1, col_3 FROM NoHeader ORDER BY _xl_row")[2], (7, 9.5))
        self.assertEqual(self.rows("SELECT sql_type FROM _columns WHERE table_name='NoHeader' ORDER BY position"),
                         [("INTEGER",), ("INTEGER",), ("REAL",)])

    def test_merged_cells(self):
        lg = self.log("Merged")
        self.assertEqual((lg["merged_areas"], lg["merged_in_data"], lg["header_row"]), (2, 1, 2))
        self.assertEqual(self.rows("SELECT area, value FROM _merged_areas WHERE sheet_name='Merged' ORDER BY rowid"),
                         [("A1:C1", "Big title"), ("A3:A4", "North")])
        self.assertEqual(self.log("Types")["merged_in_data"], 0)
        self.assertEqual(self.col("Merged", "Region"), ["North", None, "South"])   # not filled: value lives in the anchor

    def test_phantom_used_range(self):
        lg = self.log("Phantom")
        self.assertEqual((lg["last_row"], lg["last_col"], lg["data_rows"]), (3, 2, 2))

    def test_filtered_rows_are_all_extracted(self):
        lg = self.log("Filtered")
        self.assertEqual((lg["data_rows"], lg["filter_active"]), (10, 1))
        self.assertEqual(sum(self.col("Filtered", "n")), 55)
        lg = self.log("TableFilter")                       # a filter on an Excel table hides the LAST row too
        self.assertEqual((lg["data_rows"], lg["filter_active"], lg["last_row"]), (6, 1, 7))

    def test_verification_catches_a_truncated_extent(self):
        """With the filter fix disabled the extent is too small; the whole-sheet COUNTA check must notice."""
        real = _sheet.show_filtered_rows                 # patch where it is used, not where it is defined
        _sheet.show_filtered_rows = lambda ws: 0
        try:
            code, text = run_tool(self.paths["edge"], "-o", os.path.join(self.dir, "trunc"), "--sheet", "Filtered")
        finally:
            _sheet.show_filtered_rows = real
        self.assertEqual(code, 3, text)
        con = sqlite3.connect(os.path.join(self.dir, "trunc", "edge_cases.db"))
        self.assertEqual(con.execute("SELECT count(*) FROM Filtered").fetchone()[0], 9)           # one row lost...
        self.assertEqual(con.execute("SELECT ok FROM _verification WHERE check_name='cells_total'").fetchall(), [(0,)])
        con.close()                                                                                # ...and reported

    def test_formulas_are_values(self):
        self.assertEqual(self.log("Formulas")["formula_cells"], 5)
        self.assertEqual(self.col("Formulas", "sum"), [3, 6, 9, 12, 15])

    def test_awkward_sheet_names(self):
        tables = {r[0] for r in self.rows("SELECT table_name FROM _extraction_log WHERE status='extracted'")}
        for want in ("Bob_s_Sheet", "t_sqlite_test", "a_b", "a_b_2", "t_2024_data", "a_b_c"):
            self.assertIn(want, tables)
        self.assertEqual(self.col("Bob_s_Sheet", "v"), [1])

    def test_sqlite_row_counts_match_log(self):
        for table, n in self.rows("SELECT table_name, data_rows FROM _extraction_log WHERE status='extracted'"):
            self.assertEqual(self.rows(f'SELECT count(*) FROM "{table}"')[0][0], n, table)

    # ---- failure handling -----------------------------------------------------------------------------------------
    def test_password_protected_file_fails_cleanly(self):
        code, text = run_tool(self.paths["protected"], "-o", self.out)
        self.assertEqual(code, 1)
        self.assertIn("password", text.lower())
        self.assertFalse(os.path.exists(os.path.join(self.out, "protected.db")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "protected.db.partial")))
        assert_no_leak(self, text)                     # the blocked Excel was killed by the watchdog

    def test_corrupt_file_fails_cleanly(self):
        code, text = run_tool(self.paths["corrupt"], "-o", self.out)
        self.assertEqual(code, 1)
        self.assertIn("could not open", text.lower())
        self.assertFalse(os.path.exists(os.path.join(self.out, "corrupt.db")))
        assert_no_leak(self, text)

    def test_failed_run_keeps_previous_database(self):
        target = os.path.join(self.out, "keep.db")
        with open(target, "wb") as f:
            f.write(b"previous good database")
        code, _ = run_tool(self.paths["corrupt"], "-o", target)
        self.assertEqual(code, 1)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"previous good database")

    def test_excel_crash_is_recovered(self):
        os.environ["XL2SQL_TEST_KILL_BEFORE"] = "Preamble"
        try:
            code, text = run_tool(self.paths["edge"], "-o", os.path.join(self.dir, "crash"))
        finally:
            del os.environ["XL2SQL_TEST_KILL_BEFORE"]
        self.assertEqual(code, 0, text)
        self.assertIn("Restarting Excel", text)
        self.assertEqual(len(started_pids(text)), 2)   # original + one restart
        assert_no_leak(self, text)                     # including the instance that was killed on purpose
        con = sqlite3.connect(os.path.join(self.dir, "crash", "edge_cases.db"))
        self.assertEqual(con.execute("SELECT count(*) FROM Preamble").fetchone()[0], 5)
        self.assertEqual(con.execute("SELECT value FROM _meta WHERE key='excel_restarts'").fetchone()[0], "1")
        self.assertEqual(con.execute("SELECT count(*) FROM _extraction_log WHERE status='error'").fetchone()[0], 0)
        con.close()

    def test_strict_mode(self):
        code, text = run_tool(self.paths["edge"], "-o", os.path.join(self.dir, "strict"), "--strict")
        self.assertEqual(code, 0, text)
        con = sqlite3.connect(os.path.join(self.dir, "strict", "edge_cases.db"))
        self.assertIn("STRICT", con.execute("SELECT sql FROM sqlite_master WHERE name='Types'").fetchone()[0])
        self.assertEqual([r[0] for r in con.execute('SELECT "Mixed" FROM Types ORDER BY _xl_row')], [1.0, "a", "TRUE", 2.5, None])
        con.close()

    @unittest.skipUnless(os.environ.get("XL2SQL_PERF"), "set XL2SQL_PERF=1 for the 200k-row timing test")
    def test_large_workbook_speed(self):
        import time
        t = time.perf_counter()
        code, text = run_tool(self.paths["large"], "-o", self.out)
        secs = time.perf_counter() - t
        self.assertEqual(code, 0, text)
        con = sqlite3.connect(os.path.join(self.out, "large.db"))
        self.assertEqual(con.execute("SELECT count(*) FROM Large").fetchone()[0], 200_000)
        con.close()
        print(f"\n200k x 12 workbook: {secs:.1f}s total")
        self.assertLess(secs, 120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
