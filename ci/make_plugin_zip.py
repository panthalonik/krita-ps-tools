#!/usr/bin/env python3
"""Packs plugin/ into a zip that Krita's "Import Python Plugin from File"
accepts: ps_tools.desktop and ps_tools.action at the top, the ps_tools/
module with an explicit directory entry (Krita's importer looks for the
entry "ps_tools/" and fails with "No plugins found in archive" without it).

Usage: make_plugin_zip.py <output.zip>"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "plugin")


def main(out):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("ps_tools.desktop", "ps_tools.action"):
            z.write(os.path.join(PLUGIN, name), name)
        root = os.path.join(PLUGIN, "ps_tools")
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            rel = os.path.relpath(dirpath, PLUGIN).replace(os.sep, "/")
            z.writestr(zipfile.ZipInfo(rel + "/"), b"")
            for f in sorted(filenames):
                if f.endswith(".pyc"):
                    continue
                z.write(os.path.join(dirpath, f), rel + "/" + f)
    names = zipfile.ZipFile(out).namelist()
    for need in ("ps_tools.desktop", "ps_tools/", "ps_tools/__init__.py"):
        if need not in names:
            sys.exit("ERROR: %s missing from %s" % (need, out))
    print("%s: %d entries" % (out, len(names)))


if __name__ == "__main__":
    main(sys.argv[1])
