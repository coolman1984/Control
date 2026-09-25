"""Unit tests for xl2ai.core.runs: manifest, StageRec, Lock, retention, orphan recovery. No Excel needed.

The hard-kill test is the phase-1 gate from ARCHITECTURE.md: a run killed mid-way must never promote and must
never leave the lock unrecoverable.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from xl2ai.core import runs as runsmod
from xl2ai.core.config import load_config
from xl2ai.core.errors import Xl2aiError
from xl2ai.core.fsutil import read_json


def make_cfg(tmp, extra=""):
    p = os.path.join(tmp, "xl2ai.toml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(extra) or "[project]\nname = \"t\"\n")
    return load_config(p)


class TestStageRec(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(self.tmp.name)
        self.run = runsmod.Run.create(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def test_passed_stage(self):
        with self.run.stage("a") as st:
            st.detail("rows", 5)
        self.assertEqual(self.run.stage_status("a"), "passed")
        m = read_json(self.run.path("manifest.json"))
        self.assertEqual(m["stages"][0]["details"]["rows"], 5)
        self.assertIsNotNone(m["stages"][0]["seconds"])

    def test_failed_stage_records_xl2ai_error(self):
        # StageRec.__exit__ swallows ordinary exceptions (records them, lets the orchestrator move on to
        # run.finish() without its own try/except); only KeyboardInterrupt/SystemExit propagate. See runs.py.
        with self.run.stage("a"):
            raise Xl2aiError("E_TEST", "boom", "try again")
        self.assertEqual(self.run.stage_status("a"), "failed")
        err = self.run.m["stages"][0]["error"]
        self.assertEqual(err, {"code": "E_TEST", "message": "boom", "hint": "try again"})

    def test_failed_stage_records_unexpected_exception(self):
        with self.run.stage("a"):
            raise ValueError("nope")
        err = self.run.m["stages"][0]["error"]
        self.assertEqual(err["code"], "E_STAGE")
        self.assertIn("nope", err["message"])

    def test_keyboard_interrupt_is_not_swallowed(self):
        """Ctrl+C must stop the process, not be recorded as an ordinary stage failure and continue."""
        with self.assertRaises(KeyboardInterrupt):
            with self.run.stage("a"):
                raise KeyboardInterrupt()
        self.assertEqual(self.run.stage_status("a"), "interrupted")

    def test_explicit_fail_without_raising(self):
        with self.run.stage("a") as st:
            st.fail("E_X", "bad")
        self.assertEqual(self.run.stage_status("a"), "failed")

    def test_partial_warns_and_sets_status(self):
        with self.run.stage("a") as st:
            st.partial("some rows skipped")
        self.assertEqual(self.run.stage_status("a"), "partial")
        self.assertEqual(self.run.m["stages"][0]["warnings"], ["some rows skipped"])

    def test_artifact_path_is_relative_to_run_dir(self):
        f = self.run.path("out", "x.db")
        os.makedirs(os.path.dirname(f))
        open(f, "w").close()
        with self.run.stage("a") as st:
            st.artifact(f)
        self.assertEqual(self.run.m["stages"][0]["artifacts"], ["out/x.db"])


class TestRunFinishAndPromotion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_run_never_promotes(self):
        run = runsmod.Run.create(self.cfg)
        self.assertFalse(run.finish())
        self.assertEqual(run.m["status"], "failed")
        self.assertIsNone(runsmod.current_run_id(self.cfg))

    def test_all_passed_promotes(self):
        run = runsmod.Run.create(self.cfg)
        with run.stage("a"):
            pass
        self.assertTrue(run.finish())
        self.assertEqual(runsmod.current_run_id(self.cfg), run.id)
        self.assertTrue(run.m["promoted"])
        self.assertEqual(run.m["status"], "passed")

    def test_a_failed_stage_never_promotes(self):
        run = runsmod.Run.create(self.cfg)
        with run.stage("a"):
            raise Xl2aiError("E_X", "bad")
        self.assertFalse(run.finish())
        self.assertIsNone(runsmod.current_run_id(self.cfg))
        self.assertEqual(run.m["status"], "failed")

    def test_partial_not_promoted_by_default(self):
        run = runsmod.Run.create(self.cfg)
        with run.stage("a") as st:
            st.partial("meh")
        self.assertFalse(run.finish())
        self.assertEqual(run.m["status"], "partial")

    def test_partial_promoted_when_allowed(self):
        cfg = make_cfg(self.tmp.name, "[refresh]\nallow_partial = true\n")
        run = runsmod.Run.create(cfg)
        with run.stage("a") as st:
            st.partial("meh")
        self.assertTrue(run.finish())
        self.assertEqual(runsmod.current_run_id(cfg), run.id)

    def test_second_good_run_replaces_pointer_and_records_previous(self):
        run1 = runsmod.Run.create(self.cfg)
        with run1.stage("a"):
            pass
        run1.finish()
        run2 = runsmod.Run.create(self.cfg)
        with run2.stage("a"):
            pass
        run2.finish()
        self.assertEqual(runsmod.current_run_id(self.cfg), run2.id)
        cur = read_json(self.cfg.current_file)
        self.assertEqual(cur["previous"], run1.id)

    def test_failed_run_after_a_good_one_keeps_the_good_pointer(self):
        run1 = runsmod.Run.create(self.cfg)
        with run1.stage("a"):
            pass
        run1.finish()
        run2 = runsmod.Run.create(self.cfg)
        with run2.stage("a"):
            raise Xl2aiError("E_X", "bad")
        run2.finish()
        self.assertEqual(runsmod.current_run_id(self.cfg), run1.id, "a failed run must not replace a good dataset")


class TestRetention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_runs_are_pruned_but_current_and_recent_kept(self):
        cfg = make_cfg(self.tmp.name, "[refresh]\nkeep_runs = 2\n")
        ids = []
        for _ in range(5):
            run = runsmod.Run.create(cfg)
            with run.stage("a"):
                pass
            run.finish()
            ids.append(run.id)
            time.sleep(0.05)                      # run_id has 1-second resolution before the random suffix
        remaining = set(runsmod.list_runs(cfg))
        self.assertEqual(remaining, set(ids[-2:]))
        self.assertEqual(runsmod.current_run_id(cfg), ids[-1])

    def test_retention_never_deletes_current_even_if_old(self):
        cfg = make_cfg(self.tmp.name, "[refresh]\nkeep_runs = 1\n")
        run1 = runsmod.Run.create(cfg)
        with run1.stage("a"):
            pass
        run1.finish()
        run2 = runsmod.Run.create(cfg)              # a later run that fails must not evict the promoted one
        with run2.stage("a"):
            raise Xl2aiError("E_X", "bad")
        run2.finish()
        self.assertIn(run1.id, runsmod.list_runs(cfg))
        self.assertEqual(runsmod.current_run_id(cfg), run1.id)


class TestLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_acquire_and_release(self):
        lock = runsmod.Lock(self.cfg)
        with lock:
            self.assertTrue(os.path.exists(self.cfg.lock_file))
        self.assertFalse(os.path.exists(self.cfg.lock_file))

    def test_second_acquire_is_rejected_while_held(self):
        with runsmod.Lock(self.cfg):
            with self.assertRaises(Xl2aiError) as ctx:
                runsmod.Lock(self.cfg).acquire()
            self.assertEqual(ctx.exception.code, "E_LOCKED")

    def test_lock_with_a_dead_pid_is_taken_over(self):
        os.makedirs(os.path.dirname(self.cfg.lock_file), exist_ok=True)
        with open(self.cfg.lock_file, "w", encoding="utf-8") as f:
            json.dump({"pid": 999_999_999, "started": "x", "epoch": time.time()}, f)   # a pid that cannot exist
        with runsmod.Lock(self.cfg):
            self.assertTrue(os.path.exists(self.cfg.lock_file))

    def test_old_lock_is_not_taken_over_while_owner_is_alive(self):
        # Age may indicate a suspiciously long run, but it is never permission to overlap two live refreshes.
        cfg = make_cfg(self.tmp.name, "[refresh]\nlock_stale_hours = 1\n")
        os.makedirs(os.path.dirname(cfg.lock_file), exist_ok=True)
        with open(cfg.lock_file, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started": "x", "epoch": time.time() - 7200}, f)
        with self.assertRaises(Xl2aiError) as ctx:
            runsmod.Lock(cfg).acquire()
        self.assertEqual(ctx.exception.code, "E_LOCKED")
        self.assertIn("owner pid is still alive", ctx.exception.message)

    def test_lock_stale_hours_zero_disables_the_age_check(self):
        """0 disables the old-lock advisory threshold; live-owner liveness remains authoritative."""
        cfg = make_cfg(self.tmp.name, "[refresh]\nlock_stale_hours = 0\n")
        os.makedirs(os.path.dirname(cfg.lock_file), exist_ok=True)
        with open(cfg.lock_file, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started": "x", "epoch": time.time() - 365 * 86400}, f)
        with self.assertRaises(Xl2aiError):
            runsmod.Lock(cfg).acquire()


class TestHardKillGate(unittest.TestCase):
    """Phase-1 proof gate: a run killed mid-stage must never become `current`, and must be recoverable."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_killed_run_never_promotes_and_is_reaped(self):
        helper = os.path.join(ROOT, "tests", "helpers", "slow_run.py")
        before = set(runsmod.list_runs(self.cfg))
        proc = subprocess.Popen([sys.executable, "-u", helper, self.cfg.path, "60"], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        run_id = None
        manifest_path = None
        try:
            for _ in range(300):
                created = [r for r in runsmod.list_runs(self.cfg) if r not in before]
                if created:
                    candidate = created[0]
                    p = os.path.join(self.cfg.runs_dir, candidate, "manifest.json")
                    try:
                        m = read_json(p)
                    except (OSError, ValueError):
                        m = {}
                    if m.get("stages") and m["stages"][0].get("status") == "running":
                        run_id, manifest_path = candidate, p
                        break
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            self.assertTrue(run_id, "helper never reached a durably recorded running stage")
            self.assertTrue(os.path.exists(self.cfg.lock_file), "the lock should still be held at kill time")
        finally:
            proc.kill()                                     # hard stop: child cleanup code must not run
            proc.wait(timeout=10)

        self.assertIsNone(runsmod.current_run_id(self.cfg), "a killed run must never become current")
        m = read_json(manifest_path)
        self.assertEqual(m["status"], "running", "the manifest is left as-is; recovery is a separate, explicit step")

        runsmod.reap_orphans(self.cfg)                       # what the next `refresh` does before starting

        m = read_json(manifest_path)
        self.assertEqual(m["status"], "aborted")
        self.assertEqual(m["stages"][0]["status"], "interrupted")
        self.assertIsNone(runsmod.current_run_id(self.cfg))

        with runsmod.Lock(self.cfg):                         # the stale lock (dead pid) must be takeable now
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
