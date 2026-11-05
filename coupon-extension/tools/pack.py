"""Zip the extension (to share it or upload it to the Chrome Web Store).

    python3 tools/pack.py        ->  dist/coupon-extension-<version>.zip
"""
import json
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCLUDE = ['manifest.json', 'background.js', 'content.js', 'hook.js',
           'popup.html', 'popup.js', 'options.html', 'options.js',
           'styles.css', 'lib', 'icons']


def main():
    with open(os.path.join(ROOT, 'manifest.json'), encoding='utf-8') as f:
        version = json.load(f)['version']
    os.makedirs(os.path.join(ROOT, 'dist'), exist_ok=True)
    out = os.path.join(ROOT, 'dist', f'coupon-extension-{version}.zip')
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for item in INCLUDE:
            src = os.path.join(ROOT, item)
            if os.path.isdir(src):
                for base, _, files in os.walk(src):
                    for name in sorted(files):
                        full = os.path.join(base, name)
                        z.write(full, os.path.relpath(full, ROOT).replace(os.sep, '/'))
            else:
                z.write(src, item)
    print(out)


if __name__ == '__main__':
    main()
