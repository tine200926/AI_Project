import os
import unittest

from secscan.checks.persistence import cron_command
from secscan.checks.protection import _exclusion_score, analyze_hosts, decode_product_state
from secscan.model import Severity, severity_from_score
from secscan.scoring import Locations, check_masquerade, extract_executable, referenced_paths, score_command


class ScoringTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("WINDIR", r"C:\Windows")

    def test_extract_executable(self):
        self.assertEqual(extract_executable('"C:\\Program Files\\App\\app.exe" --x'), "C:\\Program Files\\App\\app.exe")
        self.assertEqual(extract_executable("C:\\Program Files\\App\\app.exe --x"), "C:\\Program Files\\App\\app.exe")
        self.assertEqual(extract_executable("rundll32.exe C:\\x\\a.dll,Run"), "rundll32.exe")
        self.assertEqual(extract_executable("/usr/bin/env python3"), "/usr/bin/env")
        self.assertIsNone(extract_executable("   "))

    def test_referenced_paths(self):
        paths = referenced_paths("rundll32.exe C:\\Users\\bob\\AppData\\Local\\Temp\\x.dll,Start")
        self.assertIn("C:\\Users\\bob\\AppData\\Local\\Temp\\x.dll", paths)

    def test_masquerade(self):
        self.assertGreaterEqual(check_masquerade("svchost.exe", r"C:\Users\bob\AppData\svchost.exe").score, 6)
        self.assertEqual(check_masquerade("svchost.exe", r"C:\Windows\System32\svchost.exe").score, 0)
        self.assertEqual(check_masquerade("explorer.exe", r"C:\Windows\explorer.exe").score, 0)
        self.assertGreaterEqual(check_masquerade("svch0st.exe", r"C:\x\svch0st.exe").score, 5)
        self.assertGreaterEqual(check_masquerade("scvhost.exe", r"C:\x\scvhost.exe").score, 0)
        self.assertGreaterEqual(check_masquerade("lsasss.exe", r"C:\x\lsasss.exe").score, 5)
        self.assertEqual(check_masquerade("iexplore.exe", r"C:\Program Files\Internet Explorer\iexplore.exe").score, 0)
        self.assertEqual(check_masquerade("chrome.exe", r"C:\Program Files\chrome.exe").score, 0)

    def test_score_command_location(self):
        loc = Locations()
        s = score_command("/tmp/.x/payload", loc)
        self.assertGreaterEqual(s.score, 2)
        s = score_command("powershell -w hidden -enc " + "A" * 40, loc)
        self.assertGreaterEqual(s.score, 6)
        self.assertEqual(score_command("/usr/bin/true", loc).score, 0)

    def test_relative_names_ignore_cwd(self):
        import tempfile
        loc = Locations()
        old = os.getcwd()
        os.chdir(tempfile.gettempdir())
        try:
            self.assertEqual(loc.classify("python3"), (0, ""))
            self.assertEqual(score_command("python3 -m http.server", loc).score, 0)
        finally:
            os.chdir(old)

    def test_severity(self):
        self.assertEqual(severity_from_score(0), Severity.INFO)
        self.assertEqual(severity_from_score(2), Severity.LOW)
        self.assertEqual(severity_from_score(4), Severity.MEDIUM)
        self.assertEqual(severity_from_score(6), Severity.HIGH)
        self.assertEqual(severity_from_score(12), Severity.CRITICAL)
        self.assertEqual(Severity.parse("Medium"), Severity.MEDIUM)
        self.assertEqual(Severity.parse("高"), Severity.HIGH)

    def test_product_state(self):
        self.assertEqual(decode_product_state(397568), (True, True))    # 0x061100
        self.assertEqual(decode_product_state(393472), (False, True))   # 0x060100
        self.assertEqual(decode_product_state(397584), (True, False))   # 0x061110
        self.assertEqual(decode_product_state(266240), (True, True))    # 0x041000

    def test_hosts(self):
        text = "\n".join([
            "127.0.0.1 localhost",
            "::1 localhost",
            "0.0.0.0 ads.example.com",
            "127.0.0.1 update.microsoft.com",
            "203.0.113.9 www.paypal.com",
        ])
        result = {score: lines for score, _, lines in analyze_hosts(text)}
        self.assertIn(7, result)
        self.assertIn(5, result)
        self.assertEqual(result[0], ["0.0.0.0 ads.example.com"])
        self.assertEqual(analyze_hosts("127.0.0.1 localhost\n# comment\n"), [])

    def test_exclusions(self):
        self.assertEqual(_exclusion_score("path", "C:\\")[0], 7)
        self.assertEqual(_exclusion_score("path", "C:\\Users\\bob\\AppData\\Local\\Temp")[0], 6)
        self.assertEqual(_exclusion_score("path", "D:\\Projects\\build")[0], 4)
        self.assertEqual(_exclusion_score("extension", ".exe")[0], 7)
        self.assertEqual(_exclusion_score("process", "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe")[0], 7)

    def test_cron(self):
        self.assertEqual(cron_command("*/5 * * * * root /tmp/x.sh", True), "/tmp/x.sh")
        self.assertEqual(cron_command("*/5 * * * * curl x | sh", False), "curl x | sh")
        self.assertEqual(cron_command("@reboot /home/a/run.sh --now", False), "/home/a/run.sh --now")
        self.assertEqual(cron_command("@reboot root /opt/x", True), "/opt/x")
        self.assertIsNone(cron_command("SHELL=/bin/sh", True))
        self.assertIsNone(cron_command("# comment", False))


if __name__ == "__main__":
    unittest.main()
