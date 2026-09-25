import argparse
import json
import sys
import time

p = argparse.ArgumentParser()
p.add_argument("command")
p.add_argument("screens", nargs="+")
p.add_argument("--division"); p.add_argument("--from", dest="date_from"); p.add_argument("--to", dest="date_to")
p.add_argument("--date"); p.add_argument("--export"); p.add_argument("--verify"); p.add_argument("--output-dir")
p.add_argument("--set", action="append", default=[]); p.add_argument("--manifest"); p.add_argument("--dry-run", action="store_true")
a = p.parse_args()
if "BUSY" in a.screens:
    sys.exit(3)
if "SLOW" in a.screens:
    time.sleep(30)
if "BAD" in a.screens:
    print("screen BAD not found"); sys.exit(1)
print(f"{a.command} {' '.join(a.screens)} division={a.division} set={a.set} مرحبا")
if a.manifest:
    json.dump({"from": a.date_from, "to": a.date_to, "division": a.division,
               "results": [{"screen": s, "rows": 42} for s in a.screens]}, open(a.manifest, "w"))
