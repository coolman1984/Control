"""Helper for test_core_runs.py: hold the refresh lock and sit inside a running stage until killed.

Usage: python slow_run.py <xl2ai.toml path> [seconds]
Deliberately bypasses xl2ai.refresh.STAGES: this test is about the run/lock machinery surviving a hard kill,
not about any particular stage's logic.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from xl2ai.core.config import load_config  # noqa: E402
from xl2ai.core.runs import Lock, Run  # noqa: E402

if __name__ == "__main__":
    cfg = load_config(sys.argv[1])
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    with Lock(cfg):
        run = Run.create(cfg)
        with run.stage("slow"):
            print(f"RUN_ID={run.id}", flush=True)  # emitted only after the stage is durably recorded as running
            time.sleep(seconds)
