"""Writes app_version.py.

  python set_version.py                 -> asks for the version (used by build.bat)
  python set_version.py 2.3.0           -> sets it without asking (used by the GitHub build)
  python set_version.py 2.3.0 --repo owner/name
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "app_version.py")


def main(argv):
    ns = {}
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            exec(f.read(), ns)
    except FileNotFoundError:
        pass
    current = ns.get("VERSION", "1.0.0")
    repo = ns.get("UPDATE_REPO", "")
    link = ns.get("UPDATE_LINK", "")
    if "--repo" in argv:
        i = argv.index("--repo")
        repo = argv[i + 1]
        del argv[i:i + 2]

    if not link:   # MediaFire fallback link saved by the app after a "Publish new version"
        cfg_path = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/.config"),
                                "MediaFireModManager", "config.json")
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                link = json.load(f).get("update_link") or ""
        except Exception:
            pass

    if argv:
        ans = argv[0].lstrip("vV")
        if not re.fullmatch(r"\d+(\.\d+){1,3}", ans):
            sys.exit(f"Bad version number: {argv[0]}")
    else:
        parts = [int(x) for x in re.findall(r"\d+", current)] or [1, 0, 0]
        parts[-1] += 1
        suggested = ".".join(str(p) for p in parts)
        print()
        print(f"  Current version: {current}")
        while True:
            ans = input(f"  Version for this build [press Enter for {suggested}]: ").strip() or suggested
            if re.fullmatch(r"\d+(\.\d+){1,3}", ans):
                break
            print("  Use numbers like 2.1.3")

    with open(PATH, "w", encoding="utf-8") as f:
        f.write("# Written by set_version.py (build.bat and the GitHub build) - you normally don't edit this by hand.\n")
        f.write(f'VERSION = "{ans}"\n')
        f.write("# GitHub repo the app checks for new releases\n")
        f.write(f"UPDATE_REPO = {repo!r}\n")
        f.write("# Fallback: MediaFire link to version.json (only used if UPDATE_REPO is empty)\n")
        f.write(f"UPDATE_LINK = {link!r}\n")
    print(f"  Version {ans}" + (f"  (updates from github.com/{repo})" if repo else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
