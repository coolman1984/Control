"""`control agent`: the foreground process that makes flows run by themselves (piece 3).

Not a Windows service on purpose: a service runs in Session 0 and cannot see the owner's desktop,
so it could never drive `win.*`/`web.*` or show the approval box (see the master roadmap, piece 3
architecture). Start this instead from a Task Scheduler "at log on" task, or just run it in a
terminal the owner leaves open. All the real logic is `agent.tick` (control/areas/agent.py) — this
is just the loop and the Ctrl+C handling around it.
"""
import sys
import time

from . import areas, config, runner


def run_forever(interval_s=None, *, out=None, sleep=time.sleep, once=False):
    out = out or sys.stdout
    areas.load_all()
    interval_s = interval_s or config.load()["agent_poll_s"]
    print(f"control agent: checking every flow's triggers every {interval_s}s (Ctrl+C to stop)", file=out)
    try:
        while True:
            payload, text = runner.execute("agent.tick", {})
            if payload.get("fired") or payload.get("errors"):
                print(text, file=out)
            if once:
                return 0
            sleep(interval_s)
    except KeyboardInterrupt:
        print("control agent: stopped", file=out)
        return 0
