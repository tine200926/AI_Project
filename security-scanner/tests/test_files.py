import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

from secscan import cli
from secscan.checks import files
from secscan.context import ScanContext, default_hash_files, load_hashes
from secscan.model import Severity
from secscan.report import to_html

from tests.helpers import make_samples


class FileScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="secscan-test-")
        cls.paths = make_samples(cls.dir)
        ctx = ScanContext(hashes=load_hashes(default_hash_files()), check_signatures=False)
        cls.findings = {os.path.basename(f.target): f for f in files.run(ctx, [cls.dir], None)}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def sev(self, name):
        f = self.findings.get(name)
        return f.severity if f else None

    def test_eicar_is_critical(self):
        f = self.findings["eicar.com"]
        self.assertEqual(f.severity, Severity.CRITICAL)
        self.assertEqual(f.details["sha256"], "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f")

    def test_disguised_executables(self):
        self.assertGreaterEqual(self.sev("invoice.pdf.exe"), Severity.MEDIUM)
        self.assertGreaterEqual(self.sev("photo.jpg"), Severity.MEDIUM)
        self.assertGreaterEqual(self.sev("report\u202efdp.exe"), Severity.MEDIUM)

    def test_scripts(self):
        self.assertGreaterEqual(self.sev("cradle.ps1"), Severity.MEDIUM)
        self.assertGreaterEqual(self.sev("ransom.ps1"), Severity.HIGH)
        self.assertGreaterEqual(self.sev("README_DECRYPT.txt"), Severity.MEDIUM)

    def test_macro_document(self):
        self.assertIn("orders.docm", self.findings)

    def test_benign_files_not_reported(self):
        self.assertNotIn("shopping-list.txt", self.findings)
        self.assertNotIn("hello.py", self.findings)

    def test_html_report_escapes(self):
        page = to_html(list(self.findings.values()), {
            "hostname": "<host>", "platform": "x", "finished": "now", "duration_sec": 1, "checks": ["files"],
            "scan_roots": [self.dir], "notes": [], "admin": False, "known_hashes": 3})
        self.assertIn("&lt;host&gt;", page)
        self.assertNotIn("<host>", page)
        self.assertIn("EICAR", page)

    def test_cli_end_to_end(self):
        out = os.path.join(self.dir, "out", "r.json")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--path", self.dir, "--checks", "files", "--json", out, "-q", "--no-color",
                             "--allowlist", os.path.join(self.dir, "none.txt")])
        self.assertEqual(code, 3)
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertGreaterEqual(data["summary"]["critical"], 1)
        self.assertEqual(data["findings"][0]["severity"], "critical")

    def test_allowlist(self):
        allow = os.path.join(self.dir, "allow.txt")
        with open(allow, "w", encoding="utf-8") as fh:
            fh.write("# test\n" + os.path.join(self.dir, "*") + "\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--path", self.dir, "--checks", "files", "-q", "--no-color", "--allowlist", allow])
        self.assertEqual(code, 0)
        self.assertIn("白名單", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
