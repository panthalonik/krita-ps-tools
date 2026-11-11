#!/usr/bin/env python3
"""Imports a plugin zip with Krita's own plugin importer (the code behind
Tools > Scripts > Import Python Plugin from File) into a temporary folder.
Usage: check_plugin_zip.py <plugin_importer.py from Krita> <plugin.zip>"""
import builtins
import importlib.util
import os
import sys
import tempfile

builtins.i18n = lambda text, *args: text          # Krita provides it
spec = importlib.util.spec_from_file_location("plugin_importer", sys.argv[1])
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)
target = tempfile.mkdtemp()
plugins = importer.PluginImporter(sys.argv[2], target, lambda *a: True).import_all()
names = [p["name"] for p in plugins]
installed = sorted(os.listdir(os.path.join(target, "pykrita")))
print("imported:", names, "->", installed)
if names != ["ps_tools"] or "ps_tools" not in installed:
    sys.exit("ERROR: the plugin zip is not accepted by Krita's importer")
