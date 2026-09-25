"""The agent connection end to end: an Excel folder -> `xl2ai open` workspace -> MCP server (in-process JSON-RPC and
a real stdio subprocess), the way an agent host drives it. No Excel needed (direct engine)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

try:
    import openpyxl
    import python_calamine  # noqa: F401
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False


def _folder(root):
    """An Excel folder the way users have them: files at the top, more in a sub-folder, a lock file, a stray."""
    folder = os.path.join(root, "Sales Files")
    os.makedirs(os.path.join(folder, "archive"))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["order_id", "customer_id", "city", "amount"])
    for i in range(1, 31):
        ws.append([i, 100 + i % 4, ["Cairo", "Giza"][i % 2], 10 * i])
    wb.save(os.path.join(folder, "orders.xlsx"))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Clients"
    ws.append(["customer_id", "segment"])
    for k in range(100, 104):
        ws.append([k, ["Retail", "Corporate"][k % 2]])
    wb.save(os.path.join(folder, "archive", "clients.xlsx"))
    with open(os.path.join(folder, "~$orders.xlsx"), "wb") as f:       # Excel's lock file: never a source
        f.write(b"lock")
    with open(os.path.join(folder, "notes.txt"), "w") as f:
        f.write("not a workbook")
    return folder


@unittest.skipUnless(HAVE_DEPS, "python-calamine and openpyxl are needed")
class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.old_home = os.environ.get("XL2AI_HOME")
        os.environ["XL2AI_HOME"] = self.home
        self.folder = _folder(self.tmp.name)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("XL2AI_HOME", None)
        else:
            os.environ["XL2AI_HOME"] = self.old_home
        self.tmp.cleanup()

    def test_open_beside_files_and_resolve(self):
        from xl2ai.core.config import load_config
        from xl2ai.sources.inventory import build_inventory
        from xl2ai.workspace import open_workspace, resolve_workspace
        info = open_workspace(self.folder, engine="direct")
        self.assertEqual(info["location"], "beside_files")
        self.assertEqual(info["workspace_dir"], os.path.join(self.folder, ".xl2ai"))
        self.assertTrue(os.path.isfile(os.path.join(info["workspace_dir"], "AGENTS.md")))
        self.assertEqual(resolve_workspace(self.folder), info["config"])
        self.assertFalse(open_workspace(self.folder)["created"])            # idempotent
        cfg = load_config(self.folder)                                       # a folder works as --config
        names = sorted(os.path.basename(s["path"]) for s in build_inventory(cfg)[0])
        self.assertEqual(names, ["clients.xlsx", "notes.txt", "orders.xlsx"])   # sub-folder and text in, lock file out

    def test_home_workspace_is_found_from_the_excel_folder(self):
        from xl2ai.workspace import open_workspace, resolve_workspace
        info = open_workspace(self.folder, home=True, engine="direct")
        self.assertEqual(info["location"], "home")
        self.assertTrue(info["workspace_dir"].startswith(self.home))
        self.assertFalse(os.path.exists(os.path.join(self.folder, ".xl2ai")))   # nothing written beside the files
        self.assertEqual(resolve_workspace(self.folder), info["config"])

    def test_unknown_folder_is_a_clear_error(self):
        from xl2ai.core.config import load_config
        from xl2ai.core.errors import Xl2aiError
        with self.assertRaises(Xl2aiError) as cm:
            load_config(self.folder)
        self.assertIn("xl2ai open", cm.exception.hint)


@unittest.skipUnless(HAVE_DEPS, "python-calamine and openpyxl are needed")
class TestMcpServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from xl2ai.core.log import silence
        from xl2ai.mcp_server import Server
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = _folder(cls.tmp.name)
        cls.server = Server(cls.folder)
        cls.n = 0
        cls.prev = silence(True)

    @classmethod
    def tearDownClass(cls):
        from xl2ai.core.log import silence
        silence(cls.prev)
        cls.tmp.cleanup()

    def rpc(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            type(self).n += 1
            msg["id"] = self.n
        return self.server.handle(msg)

    def tool(self, name, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        text = res["content"][0]["text"]
        return res["isError"], res["structuredContent"], text

    def prepared(self):
        from xl2ai.workspace import open_workspace
        cfg = open_workspace(self.folder, engine="direct")["config"]
        with open(cfg, encoding="utf-8") as f:
            self.assertIn('engine = "direct"', f.read())
        for _ in range(20):
            err, data, _ = self.tool("prepare", wait_seconds=30)
            if data["status"] != "running":
                return data
        self.fail("prepare never finished")

    def test_1_handshake_and_listing(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "test", "version": "0"}})["result"]
        self.assertEqual(init["protocolVersion"], "2025-03-26")          # the client's version is honoured
        self.assertIn("Call `start` first", init["instructions"])
        self.assertIsNone(self.rpc("notifications/initialized", notify=True))
        tools = {t["name"]: t for t in self.rpc("tools/list")["result"]["tools"]}
        self.assertEqual(set(tools), {"start", "prepare", "find", "table", "query", "facts", "region", "trace",
                                      "search", "read", "save_records"})
        writers = {n for n, t in tools.items() if not t["annotations"]["readOnlyHint"]}
        self.assertEqual(writers, {"prepare", "save_records"})           # everything else only reads
        self.assertEqual(self.rpc("ping")["result"], {})
        self.assertEqual(self.rpc("nope")["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "nope"})["error"]["code"], -32602)

    def test_2_before_preparation(self):
        err, data, _ = self.tool("start")
        self.assertFalse(err)
        self.assertEqual(data["status"], "not_prepared")
        self.assertIn("prepare", data["next"])
        err, data, _ = self.tool("find", text="x")
        self.assertTrue(err)
        self.assertIn("prepare", data["hint"])

    def test_3_prepare_then_work(self):
        from xl2ai.core.config import load_config
        from xl2ai.workspace import resolve_workspace
        # the agent's first call is on an Excel folder nobody opened yet: prepare opens it itself
        data = self.prepared()
        self.assertEqual(data["status"], "done")
        cfg = load_config(resolve_workspace(self.folder))
        cfg.extract["engine"] = "direct"
        err, data, text = self.tool("start")
        self.assertFalse(err)
        self.assertIn(data["status"], ("ready", "needs_review"))
        self.assertIn("# Agent brief", text)
        self.assertIn("How the tables connect", text)
        err, data, _ = self.tool("prepare")
        self.assertTrue(data.get("already_up_to_date"))

        err, data, _ = self.tool("query", sql="SELECT c.segment, SUM(o.amount) FROM Orders o JOIN Clients c "
                                              "ON c.customer_id = o.customer_id GROUP BY 1 ORDER BY 1")
        self.assertFalse(err)
        self.assertEqual([r[0] for r in data["rows"]], ["Corporate", "Retail"])
        err, data, _ = self.tool("query", sql="DROP TABLE Orders")
        self.assertTrue(err)

        err, data, _ = self.tool("find", text="segment")
        self.assertTrue(any(r[0] == "column" for r in data["rows"]))
        err, data, _ = self.tool("table", table="Orders", sample_rows=2)
        self.assertEqual(data["table"]["sheet"], "Orders")
        self.assertEqual(len(data["sample"]["rows"]), 2)
        self.assertTrue(any("Key numbers" in n for n in data["notes"]))
        err, data, _ = self.tool("facts", topic="relationships")
        self.assertFalse(err)
        self.assertTrue(data["rows"])
        err, data, _ = self.tool("trace", table="Orders", excel_row=2)
        self.assertEqual(data["evidence"][0]["xl_row"], 2)
        self.assertIn("orders.xlsx", data["note"])
        err, data, _ = self.tool("facts", topic="nonsense")
        self.assertTrue(err)

        res = self.rpc("resources/read", {"uri": "xl2ai://brief"})["result"]
        self.assertIn("# Agent brief", res["contents"][0]["text"])
        latest = os.path.join(self.folder, ".xl2ai", "data", "latest", "agent_brief.md")
        self.assertTrue(os.path.isfile(latest))                          # the file-based path works too

    def test_4_stdio_subprocess(self):
        """A real host: spawn `xl2ai serve`, talk newline-delimited JSON-RPC, and nothing but JSON on stdout."""
        env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        p = subprocess.Popen([sys.executable, "-m", "xl2ai", "serve", "--workspace", self.folder],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             env=env)
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "start", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}]
        out, _ = p.communicate("\n".join(json.dumps(m) for m in msgs) + "\nnot json\n", timeout=120)
        lines = [json.loads(line) for line in out.splitlines() if line.strip()]
        self.assertEqual([m.get("id") for m in lines], [1, 2, 3, None])
        self.assertEqual(lines[3]["error"]["code"], -32700)
        self.assertEqual(p.returncode, 0)

    def test_5_connect_prints_client_config(self):
        from xl2ai.mcp_server import client_config
        conf = client_config(self.folder)["mcpServers"]["xl2ai"]
        self.assertEqual(conf["args"][:3], ["-m", "xl2ai", "serve"])
        self.assertIn(self.folder, conf["args"])


if __name__ == "__main__":
    unittest.main()
