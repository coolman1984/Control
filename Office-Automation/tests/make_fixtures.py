"""Build edge-case workbooks with real Excel (COM) so the extractor can be tested against them.

Run directly (python tests/make_fixtures.py OUT_DIR) or import build_all(out_dir).
"""
import os
import sys

import pythoncom
import win32com.client as win32

XL_SHEET_HIDDEN, XL_SHEET_VERYHIDDEN = 0, 2


def _excel():
    app = win32.DispatchEx("Excel.Application")
    app.Visible = False
    app.DisplayAlerts = False
    return app


def _quit(app):
    """Quit Excel and wait until its process is really gone (tests count EXCEL.EXE processes)."""
    import time
    import win32api
    import win32process
    try:
        pid = win32process.GetWindowThreadProcessId(app.Hwnd)[1]
    except Exception:
        pid = None
    try:
        app.Quit()
    except Exception:
        pass
    if pid:
        for _ in range(150):
            try:
                h = win32api.OpenProcess(0x1000, False, pid)
            except Exception:
                return
            try:
                if win32process.GetExitCodeProcess(h) != 259:
                    return
            finally:
                win32api.CloseHandle(h)
            time.sleep(0.2)


def _fresh_sheet(wb, name, first=False):
    if first:
        ws = wb.Sheets(1)
    else:
        ws = wb.Sheets.Add(After=wb.Sheets(wb.Sheets.Count))
    ws.Name = name
    return ws


def _put(ws, top, left, rows):
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v is not None:
                ws.Cells(top + r, left + c).Value2 = v


def build_edge_cases(path):
    app = _excel()
    try:
        wb = app.Workbooks.Add()
        while wb.Sheets.Count > 1:
            wb.Sheets(wb.Sheets.Count).Delete()

        # ---- Types: every value kind in one table ------------------------------------------------
        ws = _fresh_sheet(wb, "Types", first=True)
        heads = ["Int", "Real", "Text", "ZipCode", "Date", "DateTime", "Time", "Elapsed", "Bool", "Mixed", "Err",
                 "Empty", "Unicode", "Long", "Big", "EmptyStr"]
        _put(ws, 1, 1, [heads])
        ws.Range("D2:D6").NumberFormat = "@"
        ws.Range("E2:E6").NumberFormat = "yyyy-mm-dd"
        ws.Range("F2:F6").NumberFormat = "yyyy-mm-dd hh:mm:ss"
        ws.Range("G2:G6").NumberFormat = "hh:mm:ss"
        ws.Range("H2:H6").NumberFormat = "[h]:mm:ss"
        cols = {
            1: [1, 2, 3, -4, 1000000],
            2: [1.5, 2.25, 0.30000000000000004, -0.5, 1e-300],
            3: ["a", "b c", "  padded  ", "multi\nline", 'quote"s and \'apostrophes\''],
            4: ["00123", "00456", "0", "007", "1E5"],
            5: [1, 59, 60, 44927, 36526],                 # 1900-01-01, 1900-02-28, phantom 1900-02-29, 2023-01-01, 2000-01-01
            6: [44927.5, 44927.25, 36526.0, 44928.999988, 60000.0],
            7: [0.5, 0.75, 0.999988426, 0.25, 0.0],
            8: [1.5, 0.5, 25.0, 2.0, 100.0],
            9: [True, False, True, True, False],
            10: [1, "a", True, 2.5, None],
            12: [None] * 5,
            13: ["日本語", "😀 emoji", "مرحبا", "café", "한국어"],
            14: ["x" * 32000, "short", None, "y" * 5000, "z"],
            15: [12345678901234567, 1e15, 9007199254740993, 1e300, -1e-5],
        }
        for c, vals in cols.items():
            for r, v in enumerate(vals):
                if v is not None:
                    ws.Cells(2 + r, c).Value2 = v
        for r, f in enumerate(["=1/0", "=NA()", "=SQRT(-1)", "=NoSuchFunction()", "=42"]):
            ws.Cells(2 + r, 11).Formula = f
        for r in range(5):
            ws.Cells(2 + r, 16).Formula = '=IF(1=1,"","x")' if r < 2 else "keep"

        # ---- Dup Headers ------------------------------------------------------------------------------
        ws = _fresh_sheet(wb, "Dup Headers")
        _put(ws, 1, 1, [["A b", "A_b", "a B", None, "ok", "ok", " spaced "], [1, 2, 3, 4, 5, 6, 7], [8, 9, 10, 11, 12, 13, 14]])

        # ---- Preamble: title rows, blank row inside the data, totals row -----------------------------
        ws = _fresh_sheet(wb, "Preamble")
        _put(ws, 1, 1, [["Quarterly report"], [], ["Generated", "2026-01-01"],
                        ["Name", "Qty", "Amt"], ["a", 1, 10.5], ["b", 2, 20.5], [], ["c", 3, 30.5],
                        ["d", 4, 40.5], ["Total", 10, 102.0]])
        ws.Range("B3").NumberFormat = "yyyy-mm-dd"        # Excel parsed "2026-01-01" into a date serial: keep it a date

        # ---- NoHeader: numbers only -------------------------------------------------------------------
        ws = _fresh_sheet(wb, "NoHeader")
        _put(ws, 1, 1, [[1, 2, 3], [4, 5, 6], [7, 8, 9.5], [10, 11, 12]])

        # ---- Merged: header-region merge + merge in the data --------------------------------------------
        ws = _fresh_sheet(wb, "Merged")
        _put(ws, 1, 1, [["Big title"], ["Region", "Sales", "Cost"], ["North", 1, 2], ["North", 3, 4], ["South", 5, 6]])
        ws.Range("A1:C1").Merge()
        ws.Range("A3:A4").Merge()

        # ---- Empty / hidden / very hidden / chart ---------------------------------------------------------
        _fresh_sheet(wb, "Empty")
        ws = _fresh_sheet(wb, "Hidden")
        _put(ws, 1, 1, [["k", "v"], ["x", 1], ["y", 2]])
        ws.Visible = XL_SHEET_HIDDEN
        ws = _fresh_sheet(wb, "VeryHidden")
        _put(ws, 1, 1, [["k", "v"], ["x", 1]])
        ws.Visible = XL_SHEET_VERYHIDDEN

        # ---- Phantom: tiny data + formatting a million rows down (UsedRange lies) ----------------------------
        ws = _fresh_sheet(wb, "Phantom")
        _put(ws, 1, 1, [["k", "v"], ["x", 1], ["y", 2]])
        ws.Range("D1000000").Interior.Color = 255

        # ---- awkward names ----------------------------------------------------------------------------------
        for name in ("Bob's Sheet", "sqlite_test", "a b", "a_b", "2024 data", "a.b-c"):
            ws = _fresh_sheet(wb, name)
            _put(ws, 1, 1, [["k", "v"], ["x", 1]])

        # ---- Filtered: rows hidden by AutoFilter must still be extracted -------------------------------------------
        ws = _fresh_sheet(wb, "Filtered")
        _put(ws, 1, 1, [["grp", "n"]] + [["a" if i % 2 else "b", i] for i in range(1, 11)])
        ws.Range("A1:B11").AutoFilter(1, "a")

        ws = _fresh_sheet(wb, "TableFilter")
        _put(ws, 1, 1, [["grp", "n"]] + [["a" if i % 2 else "b", i] for i in range(1, 7)])
        lo = ws.ListObjects.Add(1, ws.Range("A1:B7"), pythoncom.Missing, 1)   # xlSrcRange, xlYes
        lo.Range.AutoFilter(1, "a")

        # ---- Formulas -------------------------------------------------------------------------------------------
        ws = _fresh_sheet(wb, "Formulas")
        _put(ws, 1, 1, [["x", "y", "sum"]] + [[i, i * 2, None] for i in range(1, 6)])
        for r in range(2, 7):
            ws.Cells(r, 3).Formula = f"=A{r}+B{r}"

        # ---- chart sheet ----------------------------------------------------------------------------------------------
        wb.Charts.Add(After=wb.Sheets(wb.Sheets.Count)).Name = "ChartOnly"

        wb.SaveAs(path, 51)                                   # xlOpenXMLWorkbook
        wb.Close(False)
    finally:
        _quit(app)


def build_1904(path):
    app = _excel()
    try:
        wb = app.Workbooks.Add()
        wb.Date1904 = True
        ws = wb.Sheets(1)
        ws.Name = "D1904"
        _put(ws, 1, 1, [["when", "n"], [0, 1], [1, 2], [1461, 3], [43000.5, 4]])
        ws.Range("A2:A5").NumberFormat = "yyyy-mm-dd hh:mm"
        wb.SaveAs(path, 51)
        wb.Close(False)
    finally:
        _quit(app)


def build_protected(path):
    app = _excel()
    try:
        wb = app.Workbooks.Add()
        _put(wb.Sheets(1), 1, 1, [["a", "b"], [1, 2]])
        wb.SaveAs(path, 51, "secret")
        wb.Close(False)
    finally:
        _quit(app)


def build_large(path, rows=200_000, cols=12):
    app = _excel()
    try:
        wb = app.Workbooks.Add()
        ws = wb.Sheets(1)
        ws.Name = "Large"
        ws.Range(ws.Cells(1, 1), ws.Cells(1, cols)).Value2 = tuple(f"c{i}" for i in range(cols))
        chunk = 20_000
        for r0 in range(0, rows, chunk):
            n = min(chunk, rows - r0)
            data = tuple(tuple((r0 + i + 1) * (c + 1) * 0.5 if c % 3 else f"txt{r0 + i}_{c}" for c in range(cols))
                         for i in range(n))
            ws.Range(ws.Cells(2 + r0, 1), ws.Cells(1 + r0 + n, cols)).Value2 = data
        wb.SaveAs(path, 51)
        wb.Close(False)
    finally:
        _quit(app)


def build_all(out_dir, large=False):
    os.makedirs(out_dir, exist_ok=True)
    paths = {"edge": os.path.join(out_dir, "edge_cases.xlsx"), "d1904": os.path.join(out_dir, "edge_1904.xlsx"),
             "protected": os.path.join(out_dir, "protected.xlsx"), "corrupt": os.path.join(out_dir, "corrupt.xlsx")}
    for p in paths.values():
        if os.path.exists(p):
            os.remove(p)
    build_edge_cases(paths["edge"])
    build_1904(paths["d1904"])
    build_protected(paths["protected"])
    with open(paths["corrupt"], "wb") as f:
        f.write(b"this is not an excel file at all")
    if large:
        paths["large"] = os.path.join(out_dir, "large.xlsx")
        build_large(paths["large"])
    return paths


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "fixtures")
    print(build_all(out, large="--large" in sys.argv))
