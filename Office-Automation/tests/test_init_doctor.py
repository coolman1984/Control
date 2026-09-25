"""Project-init and doctor smoke tests."""
import os
import tempfile
import unittest

from xl2ai.core.config import load_config
from xl2ai.doctor import run_doctor
from xl2ai.init_project import create_project


class TestInitDoctor(unittest.TestCase):
    def test_init_creates_loadable_project_and_pack(self):
        with tempfile.TemporaryDirectory() as d:
            src=os.path.join(d,"source.xlsx")
            with open(src,"wb") as f:f.write(b"PK fake")
            out=os.path.join(d,"xl2ai.toml")
            cfgp,pack=create_project(out,"Demo Project",[src],with_pack=True)
            cfg=load_config(cfgp)
            self.assertEqual(cfg.project,"Demo Project")
            self.assertEqual(len(cfg.sources),1)
            self.assertEqual(cfg.sources[0].alias,"source")
            self.assertTrue(os.path.isfile(pack))
            checks=run_doctor(cfg)
            self.assertTrue(any(c["name"]=="sources" and c["status"] for c in checks))
            self.assertTrue(any(c["name"]=="rule_packs" and c["status"] for c in checks))


if __name__=="__main__":
    unittest.main(verbosity=2)
