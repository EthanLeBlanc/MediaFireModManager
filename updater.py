"""
Self-update for MediaFire Mod Manager.

How it works
  * Normally updates come from GitHub Releases: pushing a tag like v2.3.0 makes
    GitHub Actions build the exe and attach it to a release (see
    .github/workflows/release.yml). The app asks the GitHub API for the latest
    release on start-up and offers to update if it's newer.
  * Fallback: a version.json + exe published to MediaFire (the app's
    "Publish new version" button), used only when no GitHub repo is set.
  * Updating downloads the new exe, checks its SHA-256, then a tiny batch script
    waits for the app to close, swaps the exe and starts the new one.
"""
import base64
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


class UpdateError(Exception):
    pass


def is_frozen():
    """True when running as the built .exe (not as a .py script)."""
    return bool(getattr(sys, "frozen", False))


def parse_version(v):
    nums = re.findall(r"\d+", str(v))
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(remote, local):
    a, b = list(parse_version(remote)), list(parse_version(local))
    n = max(len(a), len(b))
    return a + [0] * (n - len(a)) > b + [0] * (n - len(b))


# ------------------------------------------------------------ links
def find_download_url(page_html):
    """Pull the real file URL out of a MediaFire download page."""
    m = re.search(r'data-scrambled-url="([^"]+)"', page_html)
    if m:
        try:
            url = base64.b64decode(m.group(1)).decode("utf-8")
            if url.startswith("http"):
                return url
        except Exception:
            pass
    m = re.search(r'href="(https?://download\d*\.mediafire\.com/[^"]+)"', page_html)
    if m:
        return html.unescape(m.group(1))
    m = re.search(r'id="downloadButton"[^>]*href="(https?://[^"]+)"', page_html) or \
        re.search(r'href="(https?://[^"]+)"[^>]*id="downloadButton"', page_html)
    if m:
        return html.unescape(m.group(1))
    m = re.search(r'https?://download[^\s"\'<>]*\.mediafire\.com/[^\s"\'<>]+', page_html) or \
        re.search(r"khtmlurl\s*=\s*['\"](https?://download[^'\"]+)['\"]", page_html)
    if m:
        return html.unescape(m.group(1) if m.lastindex else m.group(0))
    return None


def _open(url, http, stream=False):
    """GET a MediaFire share link (or a direct link) and return a response for the file itself."""
    r = http.get(url, headers={"User-Agent": UA}, timeout=30, stream=stream, allow_redirects=True)
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "")
    if "text/html" not in ctype:
        return r                       # already the file
    page = r.text
    direct = find_download_url(page)
    if not direct:
        raise UpdateError("Couldn't find the download button on the MediaFire page. "
                          "The file may be private, or MediaFire changed their site.")
    r2 = http.get(direct, headers={"User-Agent": UA, "Referer": url}, timeout=60, stream=stream)
    r2.raise_for_status()
    if "text/html" in r2.headers.get("Content-Type", ""):
        raise UpdateError("MediaFire returned a web page instead of the file.")
    return r2


# ------------------------------------------------------------ check
EXE_NAME = "MediaFireModManager.exe"
GITHUB_API = "https://api.github.com"


def is_github_repo(source):
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", (source or "").strip()))


def fetch_github_info(repo, http=None):
    """Latest GitHub release -> the same info dict that version.json gives."""
    http = http or requests.Session()
    r = http.get(f"{GITHUB_API}/repos/{repo}/releases/latest", timeout=20,
                 headers={"Accept": "application/vnd.github+json", "User-Agent": UA,
                          "X-GitHub-Api-Version": "2022-11-28"})
    if r.status_code == 404:
        raise UpdateError("No releases on GitHub yet.")
    if r.status_code in (403, 429):
        raise UpdateError("GitHub is limiting update checks right now. Try again later.")
    r.raise_for_status()
    rel = r.json()
    assets = {a.get("name"): a for a in rel.get("assets", [])}
    exe = assets.get(EXE_NAME) or next((a for n, a in assets.items() if str(n).lower().endswith(".exe")), None)
    if not exe:
        raise UpdateError("The newest GitHub release has no exe yet (it may still be building).")
    sha = None
    digest = exe.get("digest") or ""
    if digest.startswith("sha256:"):
        sha = digest.split(":", 1)[1]
    elif EXE_NAME + ".sha256" in assets:
        try:
            t = http.get(assets[EXE_NAME + ".sha256"]["browser_download_url"],
                         headers={"User-Agent": UA}, timeout=20)
            t.raise_for_status()
            sha = t.text.split()[0].strip()
        except Exception:
            sha = None
    return {"version": str(rel.get("tag_name", "")).lstrip("vV"),
            "exe_link": exe["browser_download_url"], "sha256": sha, "size": exe.get("size"),
            "notes": (rel.get("body") or "").strip(), "released": (rel.get("published_at") or "")[:10],
            "page": rel.get("html_url")}


def fetch_info(update_link, http=None):
    """Latest version info from a GitHub repo ("owner/name") or a MediaFire version.json link."""
    if is_github_repo(update_link):
        return fetch_github_info(update_link.strip(), http)
    http = http or requests.Session()
    r = _open(update_link, http)
    try:
        info = json.loads(r.content.decode("utf-8-sig"))
    except ValueError:
        raise UpdateError("The update file isn't valid JSON.")
    for key in ("version", "exe_link"):
        if not info.get(key):
            raise UpdateError(f"The update file is missing '{key}'.")
    return info


def download_exe(info, dest_dir=None, progress=None, http=None):
    """Download the new exe, verify size + SHA-256. Returns its path."""
    http = http or requests.Session()
    dest_dir = dest_dir or os.path.join(tempfile.gettempdir(), "MediaFireModManager_update")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "MediaFireModManager_new.exe")
    r = _open(info["exe_link"], http, stream=True)
    total = int(r.headers.get("Content-Length") or info.get("size") or 0)
    h, done = hashlib.sha256(), 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(256 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if info.get("size") and done != int(info["size"]):
        raise UpdateError(f"Download was incomplete ({done} of {info['size']} bytes).")
    if info.get("sha256") and h.hexdigest().lower() != info["sha256"].lower():
        raise UpdateError("The downloaded file didn't match the published checksum, so it wasn't installed.")
    with open(dest, "rb") as f:
        if f.read(2) != b"MZ":
            raise UpdateError("The download isn't a Windows program.")
    return dest


# ------------------------------------------------------------ install
SWAP_SCRIPT = r"""@echo off
setlocal
set "NEW={new}"
set "TARGET={target}"
set /a tries=0
:retry
rem The old exe stays locked until the app has fully closed - keep trying for up to a minute.
rem (ping is used as a 1-second wait because "timeout" fails without a visible console)
ping -n 2 127.0.0.1 >nul
move /Y "%NEW%" "%TARGET%" >nul 2>&1 && goto launch
set /a tries+=1
if %tries% lss 60 goto retry
:launch
start "" "%TARGET%"
(goto) 2>nul & del "%~f0"
"""


def install_and_restart(new_exe, target=None):
    """Start the swap script, then the caller must quit the app straight away."""
    if os.name != "nt":
        raise UpdateError("Updating is only supported on Windows.")
    target = target or sys.executable
    script = os.path.join(tempfile.gettempdir(), f"mfmm_update_{os.getpid()}.cmd")
    with open(script, "w", encoding="mbcs" if os.name == "nt" else "utf-8") as f:
        f.write(SWAP_SCRIPT.format(new=new_exe, target=target))
    flags = 0x08000000 | 0x00000200          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd", "/c", script], creationflags=flags, close_fds=True,
                     cwd=os.path.dirname(target))


# ------------------------------------------------------------ publish helpers
def read_exe_version(exe_path, timeout=90):
    """Ask a built exe which version it is (it writes it to a file and exits)."""
    out = os.path.join(tempfile.gettempdir(), f"mfmm_ver_{os.getpid()}.txt")
    if os.path.exists(out):
        os.remove(out)
    flags = 0x08000000 if os.name == "nt" else 0
    subprocess.run([exe_path, "--write-version", out], timeout=timeout, creationflags=flags)
    try:
        with open(out, "r", encoding="utf-8") as f:
            return f.read().strip()
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def make_info(version, exe_link, exe_path, notes, released):
    h = hashlib.sha256()
    with open(exe_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {"version": version, "exe_link": exe_link, "sha256": h.hexdigest(),
            "size": os.path.getsize(exe_path), "notes": notes, "released": released}
