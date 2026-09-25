"""Terminal toolkit tests: alignment, degradation and the live region's cursor arithmetic.

Layout bugs are invisible in a screenshot and obvious in production, so everything here asserts exact column
counts rather than 'looks right'. No Excel, no terminal required.
"""
import unittest

from xl2ai.ui import badge, bar, panel, rule, style as st, table, tree
from xl2ai.ui.caps import Caps
from xl2ai.ui.console import Console
from xl2ai.ui.glyphs import Glyphs
from xl2ai.ui.live import Live
from xl2ai.ui.measure import pad, truncate, width, wrap
from xl2ai.ui.spinner import Spinner, duration, size_bytes


class TestMeasure(unittest.TestCase):
    def test_width_ignores_colour_escapes(self):
        self.assertEqual(width("\x1b[31mred\x1b[0m"), 3)

    def test_width_counts_wide_and_combining_characters(self):
        self.assertEqual(width("ab"), 2)
        self.assertEqual(width("你好"), 4)           # CJK: two columns each
        self.assertEqual(width("é"), 1)                # combining accent adds nothing

    def test_arabic_is_single_width(self):
        self.assertEqual(width("مرحبا"), 5)

    def test_truncate_never_exceeds_the_limit(self):
        for limit in range(0, 12):
            self.assertLessEqual(width(truncate("abcdefghij", limit)), limit)

    def test_truncate_marks_removal_only_when_needed(self):
        self.assertEqual(truncate("abc", 5), "abc")
        self.assertTrue(truncate("abcdefgh", 5).endswith("…"))

    def test_pad_hits_the_target_exactly(self):
        for align in ("left", "right", "center"):
            self.assertEqual(width(pad("ab", 7, align)), 7)
            self.assertEqual(width(pad("far too long to fit", 7, align)), 7)

    def test_wrap_never_loses_a_long_unbroken_token(self):
        lines = wrap("short " + "x" * 50, 12)
        self.assertTrue(all(width(line) <= 12 for line in lines))
        self.assertEqual("".join(lines).count("x"), 50)


class TestLayout(unittest.TestCase):
    def setUp(self):
        self.console = Console.capture(80)

    def test_rules_and_headings_fill_the_width_exactly(self):
        for candidate in (rule.rule(self.console),
                          rule.heading(self.console, "extract"),
                          rule.heading(self.console, "extract", "3 sources")):
            self.assertEqual(width(candidate), 80)

    def test_headings_stay_exact_at_any_terminal_width(self):
        for size in (40, 61, 100, 200):
            console = Console.capture(size)
            self.assertEqual(width(rule.heading(console, "stage", "note")), size)

    def test_panel_sides_line_up(self):
        lines = panel.panel(self.console, ["one", "two"], title="title")
        self.assertTrue(all(width(line) == 80 for line in lines))

    def test_callout_wraps_long_text_inside_its_box(self):
        lines = panel.callout(self.console, "word " * 60, kind="error", title="boom")
        self.assertTrue(all(width(line) == 80 for line in lines))
        self.assertGreater(len(lines), 3)

    def test_table_columns_align(self):
        t = table.Table([table.Column("a", priority=1), table.Column("b", align="right", priority=2)])
        t.add("x", "1").add("much longer value", "23456")
        lines = t.render(self.console)
        self.assertEqual(len({width(line) for line in lines}), 1)

    def test_table_drops_low_priority_columns_when_cramped(self):
        console = Console.capture(22)                        # 2 indent + 10 + 2 gap + 14 needs 28: too narrow
        t = table.Table([table.Column("keep", priority=1, min_width=10),
                         table.Column("drop-me-first", priority=9, min_width=14)])
        t.add("value", "secondary")
        rendered = "\n".join(t.render(console))
        self.assertIn("keep", rendered)
        self.assertNotIn("drop-me-first", rendered)
        self.assertTrue(all(width(line) <= 22 for line in rendered.splitlines()))

    def test_table_keeps_every_column_when_they_all_fit(self):
        console = Console.capture(28)
        t = table.Table([table.Column("keep", priority=1, min_width=10),
                         table.Column("drop-me-first", priority=9, min_width=14)])
        t.add("value", "secondary")
        self.assertIn("drop-me-first", "\n".join(t.render(console)))

    def test_tree_nests_with_connectors(self):
        root = tree.Node("parent")
        root.add("child one", "note")
        root.add("child two")
        lines = tree.render(self.console, [root])
        self.assertEqual(len(lines), 3)
        self.assertIn("child one", lines[1])


class TestDegradation(unittest.TestCase):
    def test_plain_caps_emit_no_escape_sequences(self):
        console = Console.capture(60)
        painted = console.paint("danger", "error", bold=True)
        self.assertEqual(painted, "danger")
        self.assertNotIn("\x1b", painted)

    def test_colour_is_applied_when_supported(self):
        console = Console(stream=None, caps=Caps(True, 24, True, 80, 24, True))
        self.assertIn("\x1b[", console.paint("ok", "ok"))

    def test_ascii_glyphs_are_single_width(self):
        for unicode_ok in (True, False):
            glyphs = Glyphs(unicode_ok)
            for name in ("ok", "fail", "warn", "running", "v", "h", "tl", "diamond"):
                self.assertEqual(width(glyphs(name)), 1, f"{name} at unicode={unicode_ok}")

    def test_badges_and_bars_render_without_colour(self):
        console = Console.capture(60)
        self.assertIn("passed", badge.badge(console, "passed"))
        self.assertEqual(width(bar.bar(console, 1, 4, size=20)), 20)
        self.assertEqual(width(bar.segmented(console, [(1, "ok"), (1, "error")], size=20)), 20)
        self.assertEqual(bar.percent(1, 4), " 25%")
        self.assertEqual(bar.percent(0, 0), "  0%")


class TestLive(unittest.TestCase):
    def test_non_interactive_live_region_writes_no_escapes(self):
        console = Console.capture(40)
        with Live(console) as live:
            live.update(["working"], force=True)
            live.emit(["done"])
        self.assertNotIn("\x1b", console.captured)
        self.assertIn("done", console.captured)

    def test_interactive_region_truncates_to_width_so_redraw_stays_exact(self):
        import io
        console = Console(stream=io.StringIO(), caps=Caps(True, 0, True, 20, 24, True))
        with Live(console) as live:
            live.update(["x" * 100], force=True)
            self.assertEqual(live._drawn, 1)
            live.update(["short"], force=True)
        body = [line for line in console.stream.getvalue().split("\n") if "x" in line]
        self.assertTrue(all(width(line) <= 20 for line in body))


class TestFormatting(unittest.TestCase):
    def test_duration_scales_by_magnitude(self):
        self.assertEqual(duration(0.4), "0.40s")
        self.assertEqual(duration(75), "1m15s")
        self.assertEqual(duration(4000), "1h06m")
        self.assertEqual(duration(None), "-")

    def test_byte_sizes_are_readable(self):
        self.assertEqual(size_bytes(512), "512 B")
        self.assertIn("MB", size_bytes(5_000_000))

    def test_spinner_advances_with_the_clock(self):
        spinner = Spinner("abcd", interval=0.1)
        self.assertEqual(spinner.frame(spinner.started), "a")
        self.assertEqual(spinner.frame(spinner.started + 0.25), "c")


if __name__ == "__main__":
    unittest.main(verbosity=2)
