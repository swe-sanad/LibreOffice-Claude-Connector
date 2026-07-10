# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Assemble the installable ``.oxt`` (a ZIP) from ``ext/`` + ``src/``.

Layout produced inside the archive:

    connector.py                     (the registered UNO component)
    Addons.xcu, ProtocolHandler.xcu  (UI + dispatch config)
    description.xml, META-INF/...    (extension metadata)
    description/desc_en.txt, icons/  (assets)
    LICENSE
    pythonpath/claudeconn/*.py       (helper modules, importable by connector)

Run:  python scripts/build_oxt.py   ->  dist/claude-connector-<version>.oxt
"""

import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SRC = os.path.join(ROOT, "src")
EXT = os.path.join(ROOT, "ext")
DIST = os.path.join(ROOT, "dist")

# Registered UNO components — live at the .oxt root, listed in the manifest.
ROOT_COMPONENTS = ["connector", "sidebar_panel"]

# Helper modules bundled as the `claudeconn` package (imported by connector.py).
PACKAGE_MODULES = [
    "claude_client", "calc_actions", "writer_actions",
    "uno_bridge", "config", "keystore", "uno_ui",
]


def _version():
    with open(os.path.join(EXT, "description.xml"), "r", encoding="utf-8") as fh:
        match = re.search(r'<version\s+value="([^"]+)"', fh.read())
    return match.group(1) if match else "0.0.0"


def _ensure_icons():
    icon = os.path.join(EXT, "icons", "icon.png")
    if not os.path.exists(icon):
        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
        import make_icons
        make_icons.main()


def build():
    _ensure_icons()
    os.makedirs(DIST, exist_ok=True)
    version = _version()
    out_path = os.path.join(DIST, "claude-connector-%s.oxt" % version)

    # arcname -> source file on disk
    files = {}
    for base, _dirs, names in os.walk(EXT):
        for name in names:
            full = os.path.join(base, name)
            arc = os.path.relpath(full, EXT).replace(os.sep, "/")
            files[arc] = full
    for component in ROOT_COMPONENTS:
        files["%s.py" % component] = os.path.join(SRC, component + ".py")
    for module in PACKAGE_MODULES:
        files["pythonpath/claudeconn/%s.py" % module] = os.path.join(SRC, module + ".py")
    license_path = os.path.join(ROOT, "LICENSE")
    if os.path.exists(license_path):
        files["LICENSE"] = license_path

    # sanity: every listed source must exist
    missing = [arc for arc, full in files.items() if not os.path.exists(full)]
    if missing:
        raise SystemExit("build error: missing sources for %s" % missing)

    if os.path.exists(out_path):
        os.remove(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for arc in sorted(files):
            archive.write(files[arc], arc)
        archive.writestr("pythonpath/claudeconn/__init__.py",
                         '"""Claude Connector helper modules (bundled in the .oxt)."""\n')

    print("built:", out_path)
    print("       %d entries, %d bytes" %
          (len(files) + 1, os.path.getsize(out_path)))
    return out_path


if __name__ == "__main__":
    build()
