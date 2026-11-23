#!/usr/bin/env python3
"""Markdown summary of package changes between two conda `versions.yml` exports.

Usage: diff_versions.py OLD.yml NEW.yml [--label-old X] [--label-new Y]
"""
import argparse
import re

LINE = re.compile(r"^  - ([^=\s]+)=([^=\s]+)=(\S+)$")


def load(path):
    pkgs = {}
    with open(path) as f:
        for line in f:
            m = LINE.match(line.rstrip("\n"))
            if m:
                pkgs[m.group(1)] = (m.group(2), m.group(3))
    return pkgs


def major(v):
    return v.split(".")[0]


def is_calver(v):
    m = major(v)
    return m.isdigit() and int(m) >= 2000


def main():
    p = argparse.ArgumentParser()
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--label-old", default="old")
    p.add_argument("--label-new", default="new")
    a = p.parse_args()
    old, new = load(a.old), load(a.new)

    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if old[k][0] != new[k][0])
    # date-versioned packages (e.g. 2025.x -> 2026.x) are not counted as major bumps
    majors = [k for k in changed if major(old[k][0]) != major(new[k][0])
              and not (is_calver(old[k][0]) and is_calver(new[k][0]))]

    print(f"### {a.label_old} -> {a.label_new}\n")
    print(f"{len(old)} -> {len(new)} packages: {len(added)} added, {len(removed)} removed, "
          f"{len(changed)} version changes ({len(majors)} major)\n")
    if majors:
        print("**Major version bumps:** " + ", ".join(
            f"`{k}` {old[k][0]}→{new[k][0]}" for k in majors) + "\n")
    if added:
        print("**Added:** " + ", ".join(f"`{k}` {new[k][0]}" for k in added) + "\n")
    if removed:
        print("**Removed:** " + ", ".join(f"`{k}` {old[k][0]}" for k in removed) + "\n")
    minor = [k for k in changed if k not in majors]
    if minor:
        print("<details><summary>Other version changes</summary>\n")
        for k in minor:
            print(f"- `{k}` {old[k][0]} → {new[k][0]}")
        print("\n</details>\n")


if __name__ == "__main__":
    main()
