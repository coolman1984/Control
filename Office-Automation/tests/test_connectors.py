"""`xl2ai connect --install`: registering the MCP server with Claude Code, Codex CLI and Claude Desktop. Every host
file lives in a temporary home and `claude` is a fake program that records its arguments -- nothing real is touched."""
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

from xl2ai import connectors


class TestConnectors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.env = mock.patch.dict(os.environ, {"HOME": root, "USERPROFILE": root, "APPDATA": os.path.join(root, "ad"),
                                                "XDG_CONFIG_HOME": os.path.join(root, "cfg"),
                                                "CODEX_HOME": os.path.join(root, "codex")})
        self.env.start()
        self.cmd = ["/opt/py/python", "-m", "xl2ai", "serve"]

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_codex_appends_then_replaces_only_its_own_section(self):
        path = connectors.codex_config_path()
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write('model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "x"\n')
        r = connectors.install_codex(self.cmd)
        self.assertTrue(r["changed"])
        text = open(path).read()
        self.assertIn('model = "gpt-5"', text)
        self.assertIn("[mcp_servers.other]", text)
        self.assertIn('[mcp_servers.xl2ai]\ncommand = "/opt/py/python"\nargs = ["-m", "xl2ai", "serve"]', text)
        self.assertTrue(os.path.isfile(path + ".bak"))
        self.assertFalse(connectors.install_codex(self.cmd)["changed"])          # idempotent
        connectors.install_codex(self.cmd + ["--workspace", "D:/Sales"])           # an update replaces, never duplicates
        text = open(path).read()
        self.assertEqual(text.count("[mcp_servers.xl2ai]"), 1)
        self.assertIn('"--workspace", "D:/Sales"', text)
        self.assertIn("[mcp_servers.other]", text)

    def test_codex_section_in_the_middle_keeps_what_follows(self):
        path = connectors.codex_config_path()
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write('[mcp_servers.xl2ai]\ncommand = "old"\nargs = []\n\n[profiles.fast]\nmodel = "m"\n')
        connectors.install_codex(self.cmd)
        text = open(path).read()
        self.assertNotIn('"old"', text)
        self.assertIn('[profiles.fast]\nmodel = "m"', text)

    def test_claude_desktop_merges_json(self):
        path = connectors.claude_desktop_config_path()
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            json.dump({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}, f)
        self.assertTrue(connectors.install_claude_desktop(self.cmd)["changed"])
        data = json.load(open(path))
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["mcpServers"]["other"], {"command": "x"})
        self.assertEqual(data["mcpServers"]["xl2ai"], {"command": "/opt/py/python", "args": ["-m", "xl2ai", "serve"]})
        self.assertFalse(connectors.install_claude_desktop(self.cmd)["changed"])

    def test_dry_run_writes_nothing(self):
        r = connectors.install_claude_desktop(self.cmd, dry_run=True)
        self.assertTrue(r["changed"])
        self.assertFalse(os.path.exists(connectors.claude_desktop_config_path()))
        connectors.install_codex(self.cmd, dry_run=True)
        self.assertFalse(os.path.exists(connectors.codex_config_path()))

    @unittest.skipIf(os.name == "nt", "fake program is a shell script")
    def test_claude_code_uses_its_own_cli(self):
        bindir = os.path.join(self.tmp.name, "bin")
        os.makedirs(bindir)
        log = os.path.join(self.tmp.name, "calls.txt")
        fake = os.path.join(bindir, "claude")
        with open(fake, "w") as f:
            f.write(f'#!/bin/sh\necho "$@" >> "{log}"\n')
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        with mock.patch.dict(os.environ, {"PATH": bindir}):
            r = connectors.install_claude_code(self.cmd)
        self.assertTrue(r["changed"])
        calls = open(log).read().splitlines()
        self.assertEqual(calls, ["mcp remove --scope user xl2ai",
                                 "mcp add --scope user xl2ai -- /opt/py/python -m xl2ai serve"])

    def test_missing_claude_code_explains_the_manual_step(self):
        with mock.patch.dict(os.environ, {"PATH": ""}):
            r = connectors.install_claude_code(self.cmd)
        self.assertTrue(r.get("missing"))
        self.assertIn("claude mcp add --scope user xl2ai --", r["note"])

    def test_server_command_uses_this_python_or_the_exe(self):
        self.assertEqual(connectors.server_command()[1:4], ["-m", "xl2ai", "serve"])
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(connectors.server_command("x")[1:3], ["serve", "--workspace"])


class TestDefaultWorkspaceIsTheOpenFolder(unittest.TestCase):
    def test_folder_with_workbooks_is_the_default(self):
        from xl2ai.mcp_server import Server
        with tempfile.TemporaryDirectory() as d:
            old = os.getcwd()
            try:
                os.chdir(d)
                self.assertIsNone(Server()._target({}))
                open(os.path.join(d, "sales.xlsx"), "wb").close()
                self.assertEqual(os.path.realpath(Server()._target({})), os.path.realpath(d))
                self.assertEqual(Server()._target({"workspace": "E:/x"}), "E:/x")     # explicit always wins
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
