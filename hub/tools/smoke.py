"""Live check on the real PC. Run from the signed-in desktop: .venv\\Scripts\\python tools\\smoke.py"""
import sys
import tempfile

from control import areas, registry, runner

areas.load_all()
bad = {k: v for k, v in registry.AREAS.items() if not v["ok"]}
print("areas:", {k: v["count"] if v["ok"] else "OFF" for k, v in registry.AREAS.items()})
checks = [("win.windows", {}), ("win.desktop-check", {}), ("sys.health", {}),
          ("data.start", {"workspace": tempfile.mkdtemp()}), ("gmes.batch_list", {})]
failed = []
for name, args in checks:
    payload, text = runner.execute(name, args)
    ok = payload.get("ok", True) or payload.get("code", "").startswith("DATA_")
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {text.splitlines()[0] if text else ''}")
    if not ok:
        failed.append(name)
print("\nNow a risky action: a yes/no box must appear. Click NO.")
payload, _ = runner.execute("win.process-kill", {"which": "control-smoke-does-not-exist.exe"})
print("PASS" if payload.get("code") == "APPROVAL_DENIED" else f"FAIL  risky gate: {payload}")
sys.exit(1 if failed or bad or payload.get("code") != "APPROVAL_DENIED" else 0)
