"""Prints the CHANGELOG.md section for one version (used by the GitHub build for the release notes)."""
import os
import re
import sys

version = sys.argv[1].lstrip("vV")
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHANGELOG.md")
try:
    text = open(path, encoding="utf-8").read()
except FileNotFoundError:
    text = ""
m = re.search(rf"^##\s*\[?v?{re.escape(version)}\]?[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.S | re.M)
print(m.group(1).strip() if m and m.group(1).strip() else "Bug fixes and improvements.")
