"""用假的 winreg 模組測試 Windows 登錄檔檢查（在任何系統上都能跑）。"""
import sys
import types
import unittest
from unittest import mock

from secscan.checks import persistence
from secscan.context import ScanContext
from secscan.model import Severity

HKCU, HKLM = "HKCU", "HKLM"
REGISTRY = {
    (HKCU, r"Software\Microsoft\Windows\CurrentVersion\Run"): {
        "OneDrive": r'"C:\Users\bob\AppData\Local\Microsoft\OneDrive\OneDrive.exe" /background',
        "Updater": r"powershell.exe -nop -w hidden -enc SQBFAFgAKABOAGUAdwAtAE8AYgBqAGUAYwB0ACkA",
    },
    (HKLM, r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon"): {
        "Shell": "explorer.exe",
        "Userinit": r"C:\Windows\system32\userinit.exe, C:\Users\Public\svc.exe",
    },
    (HKLM, r"Software\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\sethc.exe"): {
        "Debugger": r"C:\Windows\System32\cmd.exe",
    },
}


def fake_winreg():
    m = types.ModuleType("winreg")
    m.HKEY_CURRENT_USER, m.HKEY_LOCAL_MACHINE = HKCU, HKLM
    m.KEY_READ, m.KEY_WOW64_32KEY, m.KEY_WOW64_64KEY = 1, 2, 4

    class Key:
        def __init__(self, hive, path):
            self.hive, self.path = hive, path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(hive, path, reserved=0, access=0):
        if access & m.KEY_WOW64_32KEY:
            raise OSError("no 32-bit view")
        prefix = path + "\\"
        if (hive, path) not in REGISTRY and not any(h == hive and p.startswith(prefix) for h, p in REGISTRY):
            raise OSError("missing")
        return Key(hive, path)

    def EnumValue(key, i):
        items = list(REGISTRY.get((key.hive, key.path), {}).items())
        if i >= len(items):
            raise OSError("done")
        return items[i][0], items[i][1], 1

    def EnumKey(key, i):
        prefix = key.path + "\\"
        subs = sorted({p[len(prefix):].split("\\")[0] for h, p in REGISTRY if h == key.hive and p.startswith(prefix)})
        if i >= len(subs):
            raise OSError("done")
        return subs[i]

    m.OpenKey, m.EnumValue, m.EnumKey = OpenKey, EnumValue, EnumKey
    return m


class RegistryTests(unittest.TestCase):
    def test_registry_entries(self):
        with mock.patch.dict(sys.modules, {"winreg": fake_winreg()}):
            entries = persistence.windows_registry_entries(ScanContext(check_signatures=False))
            with mock.patch.object(persistence, "collect_entries", return_value=entries):
                found = {f.title: f for f in persistence.run(ScanContext(check_signatures=False))}
        self.assertEqual(found["登錄檔開機執行：OneDrive"].severity, Severity.INFO)
        self.assertGreaterEqual(found["登錄檔開機執行：Updater"].severity, Severity.HIGH)
        self.assertGreaterEqual(found["Winlogon：Userinit"].severity, Severity.HIGH)
        self.assertGreaterEqual(found["IFEO 偵錯器：Debugger"].severity, Severity.MEDIUM)
        self.assertNotIn("Winlogon：Shell", found)


if __name__ == "__main__":
    unittest.main()
