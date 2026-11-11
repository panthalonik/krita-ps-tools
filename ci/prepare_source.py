#!/usr/bin/env python3
"""Turns a plain Krita source checkout into the "Krita PS" source tree:

  1. copies everything under <repo>/krita/ (except patches/) over the Krita
     source - new plugins such as plugins/filters/psfilters;
  2. registers new plugin folders in their parent CMakeLists.txt;
  3. applies the engine patches <repo>/krita/patches/*.patch (sorted), with
     `git apply` - a patch that does not apply stops the build.

Usage: prepare_source.py --krita <krita source dir> [--repo <this repo>]
Safe to run twice (already applied patches / registered folders are skipped)."""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def overlay(src_root, dst_root):
    added = []
    for dirpath, dirnames, filenames in os.walk(src_root):
        rel = os.path.relpath(dirpath, src_root)
        if rel == "patches" or rel.startswith("patches" + os.sep):
            continue
        dst = os.path.join(dst_root, rel) if rel != "." else dst_root
        if not os.path.isdir(dst):
            os.makedirs(dst)
            added.append(rel)
        for f in filenames:
            if f == ".keep":
                continue
            shutil.copy2(os.path.join(dirpath, f), os.path.join(dst, f))
    return added


def register(dst_root, rel_dirs):
    """add_subdirectory() for every new top folder that has a CMakeLists.txt"""
    tops = sorted(set(d for d in rel_dirs
                      if os.path.isfile(os.path.join(dst_root, d, "CMakeLists.txt"))
                      and not any(d != o and d.startswith(o + os.sep) for o in rel_dirs)))
    for rel in tops:
        parent, name = os.path.split(rel)
        cml = os.path.join(dst_root, parent, "CMakeLists.txt")
        if not os.path.isfile(cml):
            sys.exit("ERROR: no CMakeLists.txt to register %s in" % rel)
        text = open(cml, encoding="utf-8").read()
        line = "add_subdirectory( %s )" % name
        if line not in text:
            with open(cml, "a", encoding="utf-8") as f:
                f.write("\n%s\n" % line)
            print("registered", rel)


def patch(dst_root, patch_dir):
    if not os.path.isdir(patch_dir):
        return
    for name in sorted(os.listdir(patch_dir)):
        if not name.endswith(".patch"):
            continue
        p = os.path.join(patch_dir, name)
        rev = subprocess.run(["git", "apply", "--check", "--reverse", p], cwd=dst_root,
                             capture_output=True)
        if rev.returncode == 0:
            print("already applied:", name)
            continue
        print("applying:", name)
        subprocess.run(["git", "apply", "--whitespace=nowarn", p], cwd=dst_root, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--krita", required=True)
    ap.add_argument("--repo", default=os.path.dirname(HERE))
    a = ap.parse_args()
    src = os.path.join(a.repo, "krita")
    added = overlay(src, a.krita)
    register(a.krita, added)
    patch(a.krita, os.path.join(src, "patches"))
    print("source prepared")


if __name__ == "__main__":
    main()
