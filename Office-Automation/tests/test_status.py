"""Status tests: fast metadata mode and deep SHA-256 verification."""
import os
import tempfile
import unittest

from xl2ai.core.config import load_config
from xl2ai.core.fsutil import format_mtime, sha256_file
from xl2ai.core.runs import Run
from xl2ai.status import compute_status


class TestStatus(unittest.TestCase):
    def test_deep_mode_catches_same_size_same_mtime_content_change(self):
        with tempfile.TemporaryDirectory() as root:
            source=os.path.join(root,"book.xlsx")
            with open(source,"wb") as f:
                f.write(b"AAAA")
            cfgp=os.path.join(root,"xl2ai.toml")
            with open(cfgp,"w",encoding="utf-8") as f:
                f.write(f'[project]\nname="status"\n[[sources]]\npath="{source.replace(os.sep,"/")}"\n')
            cfg=load_config(cfgp)
            digest,mode=sha256_file(source)
            st=os.stat(source)
            run=Run.create(cfg)
            run.m["inputs"]=[{"source_id":"s","path":source,"size":st.st_size,
                              "mtime":format_mtime(st.st_mtime),
                              "sha256":digest,"hash_mode":mode}]
            with run.stage("sources"):
                pass
            run.finish()

            out,code=compute_status(cfg,deep=False)
            self.assertEqual(code,0)
            self.assertTrue(out["sources_fresh"])

            original_mtime=st.st_mtime
            with open(source,"wb") as f:
                f.write(b"BBBB")
            os.utime(source,(original_mtime,original_mtime))

            out,code=compute_status(cfg,deep=False)
            self.assertEqual(code,0)
            out,code=compute_status(cfg,deep=True)
            self.assertEqual(code,2)
            self.assertEqual(out["changes"][0]["reason"],"content_changed_same_metadata")


if __name__=="__main__":
    unittest.main(verbosity=2)
