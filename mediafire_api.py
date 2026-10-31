"""
Small MediaFire Core API client (API 1.5, session token v2).

Uses MediaFire's public SDK application id, so you only need your
MediaFire email + password (no developer key).
"""
import hashlib
import os
import threading
import time
from urllib.parse import urlencode, quote

import requests

API_BASE = "https://www.mediafire.com"
API_VER = "1.5"
APP_ID = "42511"          # MediaFire's public SDK app id

ERR_INVALID_TOKEN = 105
ERR_INVALID_SIGNATURE = 127
ERR_MISSING_SIGNATURE = 215


class MFError(Exception):
    def __init__(self, code, message):
        super().__init__(f"MediaFire error {code}: {message}")
        self.code = code
        self.message = message


def login_signature(email, password, app_id=APP_ID, api_key=""):
    return hashlib.sha1((email + password + app_id + api_key).encode("utf-8")).hexdigest()


def call_signature(secret_key, time_str, uri):
    return hashlib.md5(f"{int(secret_key) % 256}{time_str}{uri}".encode("utf-8")).hexdigest()


def next_secret_key(secret_key):
    return (int(secret_key) * 16807) % 2147483647


def share_link(quickkey, filename):
    return f"https://www.mediafire.com/file/{quickkey}/{quote(filename)}/file"


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class _ProgressFile:
    """File wrapper that reports bytes read; has __len__ so requests sends Content-Length."""

    def __init__(self, path, callback=None):
        self._f = open(path, "rb")
        self._size = os.path.getsize(path)
        self._done = 0
        self._cb = callback

    def __len__(self):
        return self._size

    def read(self, n=-1):
        data = self._f.read(n if n and n > 0 else 256 * 1024)
        self._done += len(data)
        if self._cb:
            self._cb(self._done, self._size)
        return data

    def close(self):
        self._f.close()


class MediaFireClient:
    def __init__(self):
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "MediaFireModManager/1.0"
        self.lock = threading.RLock()   # signed calls must be strictly sequential
        self.session_token = None
        self.secret_key = None
        self.time = None
        self._email = None
        self._password = None

    # ---------------------------------------------------------------- auth
    @property
    def logged_in(self):
        return self.session_token is not None

    def login(self, email, password):
        params = {
            "email": email,
            "password": password,
            "application_id": APP_ID,
            "signature": login_signature(email, password),
            "token_version": "2",
            "response_format": "json",
        }
        with self.lock:
            r = self.http.post(f"{API_BASE}/api/{API_VER}/user/get_session_token.php",
                               data=params, timeout=30)
            resp = self._parse(r)
            self.session_token = resp["session_token"]
            self.secret_key = int(resp["secret_key"])
            self.time = resp["time"]
            self._email, self._password = email, password
        return True

    def logout(self):
        with self.lock:
            self.session_token = self.secret_key = self.time = None
            self._email = self._password = None

    def _relogin(self):
        if not self._email:
            raise MFError(ERR_INVALID_TOKEN, "Session expired - please log in again.")
        self.login(self._email, self._password)

    # ------------------------------------------------------------- plumbing
    @staticmethod
    def _parse(r):
        try:
            data = r.json()
        except ValueError:
            raise MFError(-1, f"HTTP {r.status_code}: unexpected reply from MediaFire")
        resp = data.get("response", data)
        if resp.get("result") == "Error":
            raise MFError(int(resp.get("error", -1)), resp.get("message", "Unknown error"))
        return resp

    def _signed_url(self, action, params):
        p = dict(params)
        p["session_token"] = self.session_token
        p["response_format"] = "json"
        uri = f"/api/{API_VER}/{action}.php?" + urlencode(p)
        sig = call_signature(self.secret_key, self.time, uri)
        return API_BASE + uri + "&signature=" + sig

    def _after_call(self, r):
        """Rotate the secret key if the server asks, then parse."""
        try:
            raw = r.json().get("response", {})
            if raw.get("new_key") == "yes":
                self.secret_key = next_secret_key(self.secret_key)
        except ValueError:
            pass
        return self._parse(r)

    def call(self, action, params=None, _retry=True):
        if not self.logged_in:
            raise MFError(ERR_INVALID_TOKEN, "Not logged in")
        with self.lock:
            url = self._signed_url(action, {k: v for k, v in (params or {}).items() if v is not None})
            r = self.http.get(url, timeout=60)
            try:
                return self._after_call(r)
            except MFError as e:
                if _retry and e.code in (ERR_INVALID_TOKEN, ERR_INVALID_SIGNATURE, ERR_MISSING_SIGNATURE):
                    self._relogin()
                    return self.call(action, params, _retry=False)
                raise

    # -------------------------------------------------------------- folders
    def list_folder(self, folder_key=None):
        """Returns (folders, files) lists of dicts for a folder (None = root)."""
        out = {}
        for ctype in ("folders", "files"):
            items, chunk = [], 1
            while True:
                resp = self.call("folder/get_content", {
                    "folder_key": folder_key, "content_type": ctype,
                    "chunk": chunk, "chunk_size": 1000})
                fc = resp.get("folder_content", {})
                items.extend(fc.get(ctype, []) or [])
                if fc.get("more_chunks") != "yes":
                    break
                chunk += 1
            out[ctype] = items
        return out["folders"], out["files"]

    def create_folder(self, name, parent_key=None):
        resp = self.call("folder/create", {"foldername": name, "parent_key": parent_key,
                                           "action_on_duplicate": "skip"})
        return resp.get("folder_key") or resp.get("folderkey")

    def delete_file(self, quickkey):
        return self.call("file/delete", {"quick_key": quickkey})

    def find_folder(self, parent_key, name):
        folders, _ = self.list_folder(parent_key)
        for f in folders:
            if f.get("name", "").lower() == name.lower():
                return f.get("folderkey")
        return None

    def set_public(self, quickkey):
        return self.call("file/update", {"quick_key": quickkey, "privacy": "public"})

    def get_storage(self):
        """(used_bytes, limit_bytes) for the signed-in account."""
        info = self.call("user/get_info").get("user_info", {}) or {}
        used = info.get("used_storage_size")
        limit = info.get("storage_limit") or info.get("base_storage")
        if used in (None, "") or limit in (None, "", "0", 0):
            raise MFError(-1, "MediaFire didn't report storage usage")
        return int(used), int(limit)

    def find_file(self, folder_key, filename):
        _, files = self.list_folder(folder_key)
        for f in files:
            if f.get("filename", "").lower() == filename.lower():
                return f
        return None

    # --------------------------------------------------------------- upload
    def upload(self, path, folder_key=None, remote_name=None, replace_quickkey=None,
               progress=None, status=None):
        """
        Upload a file. If replace_quickkey is given, the existing file is updated in
        place (same quickkey => same download link). Returns dict with quickkey, filename.
        """
        name = remote_name or os.path.basename(path)
        size = os.path.getsize(path)
        if status:
            status("Hashing")
        digest = sha256_file(path)
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Filename": name.encode("utf-8"),
            "X-Filesize": str(size),
            "X-Filehash": digest,
        }

        def _post(action, params):
            body = _ProgressFile(path, progress)
            try:
                with self.lock:
                    url = self._signed_url(action, {k: v for k, v in params.items() if v is not None})
                    r = self.http.post(url, data=body, headers=headers, timeout=(30, 3600))
                    return self._after_call(r)
            finally:
                body.close()

        if status:
            status("Uploading")
        resp = None
        if replace_quickkey:
            try:
                resp = _post("upload/update", {"quick_key": replace_quickkey})
            except MFError as e:
                if e.code in (ERR_INVALID_TOKEN, ERR_INVALID_SIGNATURE):
                    self._relogin()
                    resp = _post("upload/update", {"quick_key": replace_quickkey})
                elif e.code == 238:          # file has not changed
                    return {"quickkey": replace_quickkey, "filename": name, "unchanged": True,
                            "sha256": digest, "size": size}
                else:
                    resp = None              # fall back to simple+replace below
        if resp is None:
            params = {"folder_key": folder_key,
                      "action_on_duplicate": "replace" if replace_quickkey else "keep"}
            try:
                resp = _post("upload/simple", params)
            except MFError as e:
                if e.code in (ERR_INVALID_TOKEN, ERR_INVALID_SIGNATURE):
                    self._relogin()
                    resp = _post("upload/simple", params)
                else:
                    raise

        du = resp.get("doupload", {})
        if str(du.get("result", "0")) != "0":
            raise MFError(int(du.get("result", -1)), "Upload was rejected by MediaFire")
        key = du.get("key")
        if not key:
            raise MFError(-1, "MediaFire did not return an upload key")
        if status:
            status("Processing")
        result = self._poll(key)
        result.update({"sha256": digest, "size": size})
        result.setdefault("filename", name)
        return result

    def _poll(self, upload_key, timeout=600):
        # poll_upload is unauthenticated: never sign it (that would desync the secret key)
        url = f"{API_BASE}/api/{API_VER}/upload/poll_upload.php"
        end = time.time() + timeout
        while time.time() < end:
            r = self.http.get(url, params={"key": upload_key, "response_format": "json"}, timeout=30)
            du = self._parse(r).get("doupload", {})
            if str(du.get("fileerror", "") or "").strip():
                raise MFError(int(du.get("fileerror")), "MediaFire could not process the file "
                              "(error %s)" % du.get("fileerror"))
            if str(du.get("status")) == "99":
                if not du.get("quickkey"):
                    raise MFError(-1, du.get("description", "Upload finished without a file key"))
                return {"quickkey": du["quickkey"], "filename": du.get("filename")}
            time.sleep(2)
        raise MFError(-1, "Timed out waiting for MediaFire to finish processing the upload")
