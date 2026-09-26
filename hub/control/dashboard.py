"""`control dashboard`: piece 8 of the master roadmap, first slice — a local, read-only view of
what the flow engine is doing. `127.0.0.1` only, a token in the URL (random unless the owner sets
one), stdlib `http.server` only — no new dependency, no third-party web framework, matching the
rest of `control/`. Read-only on purpose for this first version: approving a risky action, or
resuming/cancelling a run, from a web page is a bigger security decision (who else can reach
`127.0.0.1` on this PC? a browser extension? another user account?) than this slice takes on.
"""
import json
import secrets as secrets_mod
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .journal import default as default_journal
from .store import default as default_store


def _run_row(r):
    return {"id": r["id"], "flow": r["flow_name"], "status": r["status"], "cursor": r["cursor"],
            "created_at": r["created_at"], "updated_at": r["updated_at"], "error": r.get("error"),
            "dry_run": bool(r.get("dry_run"))}


def api_runs(store, qs):
    limit = int((qs.get("limit") or ["50"])[0])
    flow_name = (qs.get("flow") or [None])[0]
    status = (qs.get("status") or [None])[0]
    return [_run_row(r) for r in store.list_runs(flow_name=flow_name, status=status, limit=limit)]


def api_run(store, run_id):
    r = store.get_run(run_id)
    if r is None:
        return None
    row = _run_row(r)
    row["steps"] = r["context"].get("steps", {})
    row["step_log"] = store.steps_for(run_id)
    row["wait_for"] = r.get("wait_for")
    return row


def api_journal(journal, qs):
    n = int((qs.get("limit") or ["50"])[0])
    return journal.last(n)


def api_triggers(store, flows_dir):
    from .flow import model
    rows = []
    if flows_dir.exists():
        for path in sorted(flows_dir.glob("*.toml")):
            try:
                f = model.parse_file(path)
            except model.FlowError as e:
                rows.append({"flow": path.stem, "error": str(e)})
                continue
            approved = store.is_approved(f.name, f.hash)
            for idx, trig in enumerate(f.triggers):
                state = store.get_trigger_state(f.name, idx)
                rows.append({"flow": f.name, "trigger": idx, "type": trig["type"],
                            "approved": approved, "last_fired_at": state["last_fired_at"],
                            "last_run_id": state["last_run_id"]})
    return rows


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Control dashboard</title>
<style>
body{font-family:system-ui,sans-serif;margin:2rem;background:#0b0d12;color:#e6e8ee}
h2{margin-top:2rem}
table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #2a2f3a;padding:.4rem .6rem;text-align:left;font-size:.9rem}
th{color:#9aa4b2;font-weight:600}
.ok{color:#5fd68a}.bad{color:#f16565}.mid{color:#e0c34a}
code{background:#161a22;padding:.1rem .3rem;border-radius:4px}
</style></head>
<body>
<h1>Control</h1>
<h2>Runs</h2><table id="runs"></table>
<h2>Triggers</h2><table id="triggers"></table>
<h2>Journal</h2><table id="journal"></table>
<script>
const token = new URLSearchParams(location.search).get("token") || "";
const q = (p) => fetch(p + (p.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token)).then(r => r.json());
const cls = (s) => s === "done" || s === "ok" ? "ok" : (s === "failed" ? "bad" : "mid");
function rows(id, cols, data) {
  const t = document.getElementById(id);
  t.innerHTML = "<tr>" + cols.map(c => `<th>${c}</th>`).join("") + "</tr>" +
    data.map(r => "<tr>" + cols.map(c => `<td>${r[c] ?? ""}</td>`).join("") + "</tr>").join("");
}
q("/api/runs").then(d => rows("runs", ["id", "flow", "status", "cursor", "updated_at", "error"], d));
q("/api/triggers").then(d => rows("triggers", ["flow", "trigger", "type", "approved", "last_fired_at"], d));
q("/api/journal").then(d => rows("journal", ["id", "action", "tier", "ok", "time"], d));
</script>
</body></html>
"""


def make_handler(token, store, journal, flows_dir):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _allowed(self, qs):
            return not token or (qs.get("token") or [None])[0] == token

        def _send(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status=200):
            self._send(status, "application/json; charset=utf-8",
                       json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if not self._allowed(qs):
                return self._json({"error": "bad or missing token"}, 403)
            if parsed.path == "/":
                return self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            if parsed.path == "/api/runs":
                return self._json(api_runs(store, qs))
            if parsed.path.startswith("/api/runs/"):
                row = api_run(store, parsed.path.rsplit("/", 1)[-1])
                return self._json(row) if row is not None else self._json({"error": "not found"}, 404)
            if parsed.path == "/api/journal":
                return self._json(api_journal(journal, qs))
            if parsed.path == "/api/triggers":
                return self._json(api_triggers(store, flows_dir))
            return self._json({"error": "not found"}, 404)

        def log_message(self, fmt, *args):
            pass                       # keep stdout clean; run_forever prints its own status line

    return Handler


def run_forever(port=8765, token=None, bind="127.0.0.1", out=None, serve_one=False):
    out = out or sys.stdout
    from .areas.flow import _flows_dir
    store, journal = default_store(), default_journal()
    token = token if token is not None else secrets_mod.token_urlsafe(16)
    server = ThreadingHTTPServer((bind, port), make_handler(token, store, journal, _flows_dir()))
    url = f"http://{bind}:{server.server_port}/" + (f"?token={token}" if token else "")
    print(f"control dashboard: {url}", file=out)
    try:
        if serve_one:
            server.handle_request()
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        print("control dashboard: stopped", file=out)
    finally:
        server.server_close()
    return 0
