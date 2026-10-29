"""
MediaFire Mod Manager  (v2 - dark UI)
Drag-and-drop uploads to MediaFire, update files in place (same link),
version history, auto-zip mod folders, and watched folders.
"""
import hashlib
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog

import customtkinter as ctk

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAVE_DND = True
except Exception:
    HAVE_DND = False

try:
    import keyring
except Exception:
    keyring = None

from mediafire_api import MediaFireClient, MFError, share_link
import updater
import app_version
VERSION = app_version.VERSION
UPDATE_LINK = getattr(app_version, "UPDATE_LINK", "")
UPDATE_REPO = getattr(app_version, "UPDATE_REPO", "")

APP_NAME = "MediaFire Mod Manager"
KEYRING_SERVICE = "MediaFireModManager"
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/.config"),
                        "MediaFireModManager")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
WATCH_INTERVAL = 4          # seconds between checks
WATCH_SETTLE = 2            # identical checks in a row before a change counts

# ---------------------------------------------------------------- theme
FAMILY = "Segoe UI"
C = {
    "bg": "#0e1014", "side": "#12151a", "card": "#171b21", "card2": "#1d2229",
    "hover": "#232933", "border": "#272d36", "border_hi": "#3a4352",
    "text": "#e8eaee", "muted": "#8d96a5", "dim": "#5d6573",
    "accent": "#4f8cff", "accent_h": "#3b77ec", "accent_soft": "#16233b", "select": "#1d3560",
    "ok": "#3fcf8e", "ok_bg": "#11301f", "warn": "#f4b03e", "warn_bg": "#352910",
    "bad": "#f26b6b", "bad_bg": "#381818", "info": "#82a9ff", "info_bg": "#16243e",
}
C.update({"on_accent": "#ffffff", "knob": "#ffffff", "sel_text": "#ffffff"})

# Pick a theme with the switch in the sidebar (applies after a restart).
THEMES = {
    "midnight": dict(C),
    "halloween": {
        "bg": "#0c0910", "side": "#110c17", "card": "#18111f", "card2": "#211829",
        "hover": "#2b1f36", "border": "#2c2138", "border_hi": "#4d3462",
        "text": "#f4ebdd", "muted": "#a796b6", "dim": "#6e5e80",
        "accent": "#ff7a1a", "accent_h": "#e86509", "accent_soft": "#371c0b", "select": "#3f2214",
        "ok": "#8ee26b", "ok_bg": "#17300f", "warn": "#ffb627", "warn_bg": "#3a2a0a",
        "bad": "#ff4d6d", "bad_bg": "#3b1220", "info": "#b48cff", "info_bg": "#271a42",
        "on_accent": "#1c0d03", "knob": "#f4ebdd", "sel_text": "#fff3e6",
    },
}
THEME_LABELS = {"midnight": "Midnight", "halloween": "Halloween"}
THEME = "midnight"
TITLE_FAMILY = FAMILY


def apply_theme(name):
    global THEME
    THEME = name if name in THEMES else "midnight"
    C.update(THEMES[THEME])


def _blend(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


PILL = {"ok": ("ok_bg", "ok"), "warn": ("warn_bg", "warn"), "bad": ("bad_bg", "bad"),
        "info": ("info_bg", "info"), "neutral": ("card2", "muted")}
_FONTS = {}


def F(size=13, weight="normal"):
    key = (size, weight)
    if key not in _FONTS:
        _FONTS[key] = ctk.CTkFont(family=FAMILY, size=size, weight=weight)
    return _FONTS[key]


# ------------------------------------------------------------- storage
def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def human_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def nice_date(s):
    """'2026-10-28 15:40:12' -> 'Today 3:40 PM' / 'Oct 27, 2026'."""
    try:
        d = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return s or ""
    now = datetime.now()
    t = d.strftime("%I:%M %p").lstrip("0")
    if d.date() == now.date():
        return "Today " + t
    if (now.date() - d.date()).days == 1:
        return "Yesterday " + t
    return d.strftime("%b %d, %Y").replace(" 0", " ")


def path_signature(path):
    """Cheap fingerprint of a file or folder: names + sizes + modified times."""
    if not os.path.exists(path):
        return None
    if os.path.isfile(path):
        st = os.stat(path)
        return f"{st.st_size}:{st.st_mtime_ns}"
    parts = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            parts.append(f"{os.path.relpath(fp, path)}|{st.st_size}|{st.st_mtime_ns}")
    return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()


def zip_folder(folder, dest_dir):
    """Zip a folder so the folder itself is the top level inside the zip."""
    name = os.path.basename(os.path.normpath(folder))
    out = os.path.join(dest_dir, name + ".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(folder):
            dirs.sort()
            rel_root = os.path.relpath(root, os.path.dirname(os.path.normpath(folder)))
            if not files and not dirs:
                z.writestr(rel_root.replace(os.sep, "/") + "/", "")
            for fn in sorted(files):
                z.write(os.path.join(root, fn), os.path.join(rel_root, fn))
    return out


def _icon(kind, s=18):
    """Small pixel icons drawn without image files."""
    img = tk.PhotoImage(width=s, height=s)
    put = lambda col, x0, y0, x1, y1: img.put(col, to=(x0, y0, x1, y1))
    if kind == "folder":
        put("#d99a2b", 1, 3, 8, 6)
        put("#e9ac3e", 1, 5, 17, 15)
        put("#f4bf58", 1, 7, 17, 15)
    elif kind == "zip":
        put("#4b5872", 3, 1, 15, 17)
        put("#6d7c99", 4, 2, 14, 16)
        for y in range(2, 11, 2):
            put("#2a3243", 8, y, 10, y + 1)
        put("#2a3243", 7, 11, 11, 14)
    else:
        put("#4b5872", 3, 1, 15, 17)
        put("#8794ab", 4, 2, 14, 16)
        for y in (6, 9, 12):
            put("#4b5872", 6, y, 12, y + 1)
    return img


def make_root():
    ctk.set_appearance_mode("dark")
    if HAVE_DND:
        class Root(ctk.CTk, TkinterDnD.DnDWrapper):
            def __init__(self):
                super().__init__()
                self.TkdndVersion = TkinterDnD._require(self)
        return Root()
    return ctk.CTk()


def _inside(widget, parent):
    while widget is not None:
        if widget == parent:
            return True
        widget = getattr(widget, "master", None)
    return False


# ============================================================ halloween decor
def _scale(widget):
    try:
        return ctk.ScalingTracker.get_widget_scaling(widget)
    except Exception:
        return 1.0


class PumpkinLogo(tk.Canvas):
    """Jack-o'-lantern with a flickering candle inside."""
    GLOW = ("#ffe16b", "#ffcf3f", "#ffb627", "#ff9b1f", "#ffd55a")

    def __init__(self, master, size=40, bg=None):
        self.k = _scale(master)
        px = int(size * self.k)
        super().__init__(master, width=px, height=px, bg=bg or C["side"], highlightthickness=0, bd=0)
        k = px / 40
        P = lambda *xy: [v * k for v in xy]
        self.create_oval(*P(3, 9, 23, 37), fill="#d9600a", outline="")
        self.create_oval(*P(17, 9, 37, 37), fill="#d9600a", outline="")
        self.create_oval(*P(9, 8, 31, 38), fill="#ff7a1a", outline="")
        self.create_oval(*P(14, 9, 26, 37), fill="#ff8c33", outline="")
        self.create_polygon(*P(18, 9, 19, 3, 23, 1, 24, 3, 21, 4, 21, 9), fill="#5d8a2e", outline="")
        self.face = [
            self.create_polygon(*P(10, 18, 16, 18, 13, 13), fill=self.GLOW[0], outline=""),
            self.create_polygon(*P(24, 18, 30, 18, 27, 13), fill=self.GLOW[0], outline=""),
            self.create_polygon(*P(18.5, 23, 21.5, 23, 20, 20), fill=self.GLOW[0], outline=""),
            self.create_polygon(*P(9, 26, 12, 28, 14, 26, 16, 29, 18, 27, 20, 30, 22, 27, 24, 29, 26, 26,
                                   28, 28, 31, 26, 29, 31, 25, 33.5, 20, 34.5, 15, 33.5, 11, 31),
                                fill=self.GLOW[0], outline=""),
        ]
        self._flicker()

    def _flicker(self):
        import random
        col = random.choice(self.GLOW)
        for f in self.face:
            self.itemconfigure(f, fill=col)
        self.after(random.randint(90, 260), self._flicker)


class NightScene(tk.Canvas):
    """Sidebar night sky: moon, twinkling stars, a graveyard, bats that flee your
    cursor (click one to make it vanish) and a spider that drops down to say hi."""
    TICK = 45

    def __init__(self, master):
        super().__init__(master, bg=C["side"], highlightthickness=0, bd=0, height=10)
        import random
        self.rnd = random.Random()
        self.k = _scale(master)
        self.w = self.h = 0
        self.mouse = None
        self.bats, self.poofs, self.stars = [], [], []
        self.spider_y = 0.0
        self.t = 0
        self.bind("<Configure>", self._layout)
        self.bind("<Motion>", lambda e: setattr(self, "mouse", (e.x, e.y)))
        self.bind("<Leave>", lambda e: setattr(self, "mouse", None))
        self.bind("<Button-1>", self._click)
        self.configure(cursor="hand2")
        self.after(self.TICK, self._tick)

    # ---------------------------------------------------------- static art
    def _layout(self, e):
        if (e.width, e.height) == (self.w, self.h):
            return
        self.w, self.h = e.width, e.height
        self.delete("all")
        w, h, k = self.w, self.h, self.k
        if h < 60 * k:
            return
        ground = h - 34 * k
        bands = 40
        for i in range(bands):                       # sky fades into a purple haze
            t = (i / bands) ** 1.8
            y0, y1 = ground * i / bands, ground * (i + 1) / bands + 1
            self.create_rectangle(0, y0, w, y1, fill=_blend(C["side"], "#3a1c50", t), outline="")
        self.stars = []
        for _ in range(int(w * ground / (900 * k * k)) + 6):
            x, y = self.rnd.uniform(4, w - 4), self.rnd.uniform(4, ground * 0.7)
            r = self.rnd.choice((0.8, 1.0, 1.3)) * k
            self.stars.append(self.create_oval(x - r, y - r, x + r, y + r, fill="#8f80a8", outline=""))
        mx, my, mr = w * 0.7, min(ground * 0.36, 80 * k), 17 * k
        sky = _blend(C["side"], "#3a1c50", (my / ground) ** 1.8)
        for i in range(12, 0, -1):                   # soft moon glow
            rr = mr + i * 1.7 * k
            self.create_oval(mx - rr, my - rr, mx + rr, my + rr, outline="",
                             fill=_blend(sky, "#c9b27a", ((13 - i) / 13) ** 2.2 * 0.45))
        self.create_oval(mx - mr, my - mr, mx + mr, my + mr, fill="#f4e6b4", outline="")
        for cx, cy, cr in ((-6, -5, 4), (5, 3, 5), (-3, 8, 2.5), (8, -8, 2)):
            self.create_oval(mx + (cx - cr) * k, my + (cy - cr) * k, mx + (cx + cr) * k, my + (cy + cr) * k,
                             fill="#e3d29a", outline="")
        # bare tree
        tx = w * 0.16
        self.create_line(tx, ground + 4 * k, tx + 2 * k, ground - 58 * k, fill="#07040b", width=5 * k)
        for (x0, y0, x1, y1, wd) in ((2, -40, -18, -62, 3), (2, -48, 22, -70, 3), (-10, -53, -20, -60, 2),
                                     (14, -61, 26, -64, 2), (2, -30, 18, -42, 2.5), (2, -58, 6, -76, 2)):
            self.create_line(tx + x0 * k, ground + y0 * k, tx + x1 * k, ground + y1 * k,
                             fill="#07040b", width=wd * k, capstyle="round")
        # hill + tombstones + pumpkin
        pts = []
        for i in range(25):
            x = w * i / 24
            pts += [x, ground - (6 + 5 * __import__("math").sin(i / 3.2)) * k]
        self.create_polygon(0, h, *pts, w, h, fill="#07040b", outline="")
        for gx, gh in ((0.42, 22), (0.6, 17), (0.84, 24)):
            x = w * gx
            top = ground - gh * k
            self.create_rectangle(x - 8 * k, top + 6 * k, x + 8 * k, ground + 4 * k, fill="#2a2036", outline="")
            self.create_oval(x - 8 * k, top, x + 8 * k, top + 13 * k, fill="#2a2036", outline="")
            self.create_line(x, top + 5 * k, x, top + 15 * k, fill="#3d3150", width=2 * k)
            self.create_line(x - 4 * k, top + 8 * k, x + 4 * k, top + 8 * k, fill="#3d3150", width=2 * k)
        px, py = w * 0.72, ground + 6 * k
        self.glow = self.create_oval(px - 16 * k, py - 10 * k, px + 16 * k, py + 12 * k,
                                     fill=_blend("#07040b", "#ff7a1a", 0.18), outline="")
        self.create_oval(px - 8 * k, py - 6 * k, px + 8 * k, py + 6 * k, fill="#ff7a1a", outline="")
        self.create_line(px, py - 6 * k, px + 1 * k, py - 9 * k, fill="#5d8a2e", width=2 * k)
        self.p_face = self.create_polygon(px - 4 * k, py - 1 * k, px - 1 * k, py - 1 * k, px - 2.5 * k, py - 3.5 * k,
                                          fill="#ffd23f", outline="")
        self.p_face2 = self.create_polygon(px + 1 * k, py - 1 * k, px + 4 * k, py - 1 * k, px + 2.5 * k, py - 3.5 * k,
                                           fill="#ffd23f", outline="")
        self.ground = ground
        self.bats = [self._new_bat(x=self.rnd.uniform(0, w)) for _ in range(4)]
        self.spider_y = 8 * k
        self.thread = self.create_line(w * 0.32, 0, w * 0.32, self.spider_y, fill="#5a4b6b", width=1)
        self.spider = []

    # ---------------------------------------------------------------- bats
    def _new_bat(self, x=None):
        k = self.k
        return {"x": -20 * k if x is None else x, "y": self.rnd.uniform(14 * k, self.ground * 0.62),
                "vx": self.rnd.uniform(0.5, 1.3) * k, "vy": 0.0, "ph": self.rnd.uniform(0, 6.28),
                "s": self.rnd.uniform(0.8, 1.15) * k, "id": None}

    UP = (-10, -4, -6, -1, -3, -3, 0, 0, 3, -3, 6, -1, 10, -4, 7, 2, 3, 1, 0, 3, -3, 1, -7, 2)
    DOWN = (-10, 5, -6, 3, -3, 1, 0, 0, 3, 1, 6, 3, 10, 5, 6, 5, 3, 3, 0, 3, -3, 3, -6, 5)

    def _bat_points(self, b):
        shape = self.UP if int(self.t / 3 + b["ph"] * 3) % 2 else self.DOWN
        return [b["x"] + v * b["s"] if i % 2 == 0 else b["y"] + v * b["s"] for i, v in enumerate(shape)]

    def _click(self, e):
        for b in self.bats:
            if b["id"] and abs(b["x"] - e.x) < 16 * self.k and abs(b["y"] - e.y) < 12 * self.k:
                self.delete(b["id"])
                for i in range(7):
                    ang = i * 6.283 / 7
                    self.poofs.append({"x": b["x"], "y": b["y"], "dx": __import__("math").cos(ang) * 2 * self.k,
                                       "dy": __import__("math").sin(ang) * 2 * self.k, "life": 12, "id": None})
                b.update(self._new_bat(), respawn=self.t + self.rnd.randint(40, 120))
                return

    # ---------------------------------------------------------------- loop
    def _tick(self):
        self.after(self.TICK, self._tick)
        if not self.winfo_ismapped() or not self.w or self.h < 60 * self.k:
            return
        import math
        self.t += 1
        k = self.k
        if self.stars and self.t % 4 == 0:
            self.itemconfigure(self.rnd.choice(self.stars), fill=self.rnd.choice(("#8f80a8", "#c9bde0", "#5e5174", "#f4ebdd")))
        if self.t % 5 == 0 and hasattr(self, "p_face"):
            col = self.rnd.choice(PumpkinLogo.GLOW)
            self.itemconfigure(self.p_face, fill=col)
            self.itemconfigure(self.p_face2, fill=col)
        for b in self.bats:
            if b.get("respawn", 0) > self.t:
                continue
            if self.mouse:
                dx, dy = b["x"] - self.mouse[0], b["y"] - self.mouse[1]
                d = math.hypot(dx, dy)
                if d < 46 * k:                         # flee the cursor
                    f = (46 * k - d) / (46 * k) * 1.6 * k
                    b["vx"] += dx / (d + 1) * f
                    b["vy"] += dy / (d + 1) * f
            b["vx"] = max(-4 * k, min(4 * k, b["vx"] * 0.96 + 0.04 * 0.9 * k))
            b["vy"] *= 0.9
            b["x"] += b["vx"]
            b["y"] += b["vy"] + math.sin(self.t / 6 + b["ph"]) * 0.6 * k
            b["y"] = max(8 * k, min(self.ground - 26 * k, b["y"]))
            if b["x"] > self.w + 24 * k or b["x"] < -40 * k:
                b.update(self._new_bat())
                if b["id"]:
                    self.delete(b["id"])
                    b["id"] = None
            pts = self._bat_points(b)
            if b["id"] is None:
                b["id"] = self.create_polygon(*pts, fill="#050308", outline="")
            else:
                self.coords(b["id"], *pts)
        for p in list(self.poofs):
            p["life"] -= 1
            p["x"] += p["dx"]
            p["y"] += p["dy"]
            r = (3 - p["life"] / 6) * k
            col = _blend("#c9bde0", C["side"], 1 - p["life"] / 12)
            if p["id"] is None:
                p["id"] = self.create_oval(0, 0, 0, 0, fill=col, outline="")
            self.coords(p["id"], p["x"] - r, p["y"] - r, p["x"] + r, p["y"] + r)
            self.itemconfigure(p["id"], fill=col)
            if p["life"] <= 0:
                self.delete(p["id"])
                self.poofs.remove(p)
        # spider follows your cursor down its thread
        target = min(self.mouse[1], self.ground * 0.55) if self.mouse else 8 * k
        self.spider_y += (target - self.spider_y) * 0.08
        sx, sy = self.w * 0.32, self.spider_y
        self.coords(self.thread, sx, 0, sx, sy)
        for i in self.spider:
            self.delete(i)
        wig = math.sin(self.t / 3) * 1.5 * k
        sp = []
        for side in (-1, 1):
            for j, (lx, ly) in enumerate(((7, -4), (8, -1), (8, 2), (7, 5))):
                bend = wig if j % 2 else -wig
                sp.append(self.create_line(sx, sy + 2 * k, sx + side * 4 * k, sy + (ly - 2) * k + bend,
                                           sx + side * lx * k, sy + (ly + 2) * k, fill="#1b1424", width=1.4 * k,
                                           smooth=True))
        sp.append(self.create_oval(sx - 4 * k, sy - 1 * k, sx + 4 * k, sy + 7 * k, fill="#1b1424", outline=""))
        sp.append(self.create_oval(sx - 3 * k, sy - 4 * k, sx + 3 * k, sy + 1 * k, fill="#241a30", outline=""))
        sp.append(self.create_oval(sx - 2 * k, sy - 2.5 * k, sx - 0.6 * k, sy - 1.1 * k, fill="#ff4d6d", outline=""))
        sp.append(self.create_oval(sx + 0.6 * k, sy - 2.5 * k, sx + 2 * k, sy - 1.1 * k, fill="#ff4d6d", outline=""))
        self.spider = sp


# ============================================================ widgets
class NavItem(ctk.CTkFrame):
    def __init__(self, master, icon, text, command):
        super().__init__(master, fg_color="transparent", corner_radius=10, height=42)
        self.command = command
        self.selected = False
        self.grid_columnconfigure(2, weight=1)
        self.bar = ctk.CTkFrame(self, width=3, height=18, corner_radius=2, fg_color="transparent")
        self.bar.grid(row=0, column=0, padx=(6, 0))
        self.ic = ctk.CTkLabel(self, text=icon, width=22, font=F(15), text_color=C["muted"])
        self.ic.grid(row=0, column=1, padx=(8, 8), pady=9)
        self.lb = ctk.CTkLabel(self, text=text, anchor="w", font=F(13), text_color=C["muted"])
        self.lb.grid(row=0, column=2, sticky="w")
        self.badge = ctk.CTkLabel(self, text="", font=F(11, "bold"), corner_radius=9, height=20,
                                  width=26, fg_color=C["accent"], text_color=C["on_accent"])
        for w in (self, self.ic, self.lb, self.badge):
            w.bind("<Button-1>", lambda e: self.command())
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)
            w.configure(cursor="hand2")

    def _enter(self, _e=None):
        if not self.selected:
            self.configure(fg_color=C["card"])
            self.lb.configure(text_color=C["text"])

    def _leave(self, _e=None):
        x, y = self.winfo_pointerxy()
        if _inside(self.winfo_containing(x, y), self):
            return
        if not self.selected:
            self.configure(fg_color="transparent")
            self.lb.configure(text_color=C["muted"])

    def set_selected(self, sel):
        self.selected = sel
        self.configure(fg_color=C["card2"] if sel else "transparent")
        self.bar.configure(fg_color=C["accent"] if sel else "transparent")
        self.lb.configure(text_color=C["text"] if sel else C["muted"],
                          font=F(13, "bold") if sel else F(13))
        self.ic.configure(text_color=C["accent"] if sel else C["muted"])

    def set_badge(self, n, kind="accent"):
        if n:
            self.badge.configure(text=str(n), fg_color=C[kind],
                                 text_color="#111111" if kind == "warn" else C["on_accent"])
            self.badge.grid(row=0, column=3, padx=(0, 10))
        else:
            self.badge.grid_remove()


def Pill(master, text, kind="neutral"):
    bg, fg = PILL[kind]
    return ctk.CTkLabel(master, text=f"  {text}  ", font=F(11, "bold"), corner_radius=10,
                        height=24, fg_color=C[bg], text_color=C[fg])


def set_pill(p, text, kind):
    bg, fg = PILL[kind]
    p.configure(text=f"  {text}  ", fg_color=C[bg], text_color=C[fg])


def Btn(master, text, command, kind="primary", width=None, **kw):
    styles = {
        "primary": dict(fg_color=C["accent"], hover_color=C["accent_h"], text_color=C["on_accent"]),
        "secondary": dict(fg_color=C["card2"], hover_color=C["hover"], text_color=C["text"],
                          border_width=1, border_color=C["border"]),
        "ghost": dict(fg_color="transparent", hover_color=C["hover"], text_color=C["muted"]),
        "danger": dict(fg_color="transparent", hover_color=C["bad_bg"], text_color=C["bad"],
                       border_width=1, border_color="#4a2424"),
    }
    opts = dict(corner_radius=9, height=34, font=F(12, "bold" if kind == "primary" else "normal"))
    opts.update(styles[kind])
    opts.update(kw)
    if width:
        opts["width"] = width
    return ctk.CTkButton(master, text=text, command=command, **opts)


# ================================================================ app
class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_NAME)
        root.geometry("1180x760")
        root.minsize(980, 640)
        root.configure(fg_color=C["bg"])

        self.cfg = _load_json(CONFIG_PATH, {})
        apply_theme(self.cfg.get("theme", "halloween"))
        global TITLE_FAMILY
        try:
            import tkinter.font as tkfont
            if THEME == "halloween" and "Chiller" in tkfont.families(root):
                TITLE_FAMILY = "Chiller"
        except Exception:
            pass
        self.cfg.setdefault("watches", [])
        self.history = _load_json(HISTORY_PATH, [])

        self.api = MediaFireClient()
        self.ui_q = queue.Queue()
        self.jobs = queue.Queue()
        self.job_cards = {}
        self.job_state = {}
        self.folder_stack = [(None, "My Files")]
        self.folder_items = ([], [])
        self.current_files = {}
        self.watch_state = {}
        self.watch_cards = {}
        self.page = None
        self._hover_row = None
        self._toast = None
        self._toast_after = None
        self._drag_after = None
        self.icons = {k: _icon(k) for k in ("folder", "zip", "file")}

        self.zip_var = tk.BooleanVar(value=self.cfg.get("zip_folders", True))
        self.replace_var = tk.BooleanVar(value=self.cfg.get("replace_same_name", True))

        self._style_tables()
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self.main = ctk.CTkFrame(root, fg_color=C["bg"], corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(0, weight=1)
        self.pages = {}
        self._build_files_page()
        self._build_uploads_page()
        self._build_history_page()
        self._build_watch_page()
        self._build_updates_page()
        self._build_login()
        self._make_menus()

        self._refresh_history()
        self._refresh_watches()
        self._refresh_uploads_empty()
        self.show_page("files")
        if HAVE_DND:
            self._register_drop(self.main)

        threading.Thread(target=self._upload_worker, daemon=True).start()
        threading.Thread(target=self._watch_worker, daemon=True).start()
        root.after(100, self._pump)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(200, self._auto_login)
        self.update_info = None
        if self.cfg.get("auto_update_check", True):
            root.after(1500, lambda: self._check_updates(silent=True))

    # ---------------------------------------------------------- styling
    def _style_tables(self):
        try:
            scale = ctk.ScalingTracker.get_window_scaling(self.root)
        except Exception:
            scale = 1.0
        st = ttk.Style()
        st.theme_use("clam")
        st.configure("Dark.Treeview", background=C["card"], fieldbackground=C["card"],
                     foreground=C["text"], rowheight=int(38 * scale), borderwidth=0,
                     relief="flat", font=(FAMILY, 10), indent=int(14 * scale))
        st.configure("Dark.Treeview.Heading", background=C["card"], foreground=C["dim"],
                     borderwidth=0, relief="flat", font=(FAMILY, 9, "bold"), padding=(10, 10))
        st.map("Dark.Treeview.Heading", background=[("active", C["card"])],
               foreground=[("active", C["muted"])])
        st.map("Dark.Treeview", background=[("selected", C["select"])],
               foreground=[("selected", C["sel_text"])])
        st.layout("Dark.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    def _table(self, parent, columns, widths, tree_col=None):
        """Dark Treeview inside a rounded card. tree_col=(heading, width) adds an icon column."""
        card = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=14,
                            border_width=1, border_color=C["border"])
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)
        show = ("tree", "headings") if tree_col else ("headings",)
        tree = ttk.Treeview(card, columns=columns, show=show, style="Dark.Treeview",
                            selectmode="extended")
        if tree_col:
            tree.heading("#0", text=tree_col[0].upper(), anchor="w")
            tree.column("#0", width=tree_col[1], stretch=True, anchor="w")
        for c, w in zip(columns, widths):
            tree.heading(c, text=c.upper(), anchor="w")
            tree.column(c, width=w, anchor="w", stretch=(c == columns[-1] and not tree_col) or w >= 200)
        sb = ctk.CTkScrollbar(card, command=tree.yview, fg_color=C["card"],
                              button_color=C["card2"], button_hover_color=C["hover"])
        tree.configure(yscrollcommand=sb.set)
        tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sb.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=10)
        tree.tag_configure("odd", background=_blend(C["card"], C["card2"], 0.35))
        tree.tag_configure("hover", background=C["hover"])
        tree.bind("<Motion>", lambda e, t=tree: self._row_hover(t, t.identify_row(e.y)))
        tree.bind("<Leave>", lambda e, t=tree: self._row_hover(t, None))
        return card, tree

    def _row_hover(self, tree, row):
        old = self._hover_row
        if old and old[0] is tree and old[1] == row:
            return
        if old and old[0].exists(old[1]):
            tags = [t for t in old[0].item(old[1], "tags") if t != "hover"]
            old[0].item(old[1], tags=tags)
        self._hover_row = (tree, row) if row else None
        if row:
            tree.item(row, tags=list(tree.item(row, "tags")) + ["hover"])

    def _header(self, page, title, subtitle):
        h = ctk.CTkFrame(page, fg_color="transparent")
        h.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        h.grid_columnconfigure(0, weight=1)
        tf = ctk.CTkFont(family=TITLE_FAMILY, size=34) if TITLE_FAMILY != FAMILY else F(24, "bold")
        ctk.CTkLabel(h, text=title, font=tf, text_color=C["accent"] if THEME == "halloween" else C["text"]).grid(
            row=0, column=0, sticky="w")
        var = tk.StringVar(value=subtitle)
        ctk.CTkLabel(h, textvariable=var, font=F(12), text_color=C["muted"]).grid(
            row=1, column=0, sticky="w")
        actions = ctk.CTkFrame(h, fg_color="transparent")
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        return var, actions

    def _new_page(self, name):
        p = ctk.CTkFrame(self.main, fg_color=C["bg"], corner_radius=0)
        p.grid_columnconfigure(0, weight=1)
        inner = ctk.CTkFrame(p, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=28, pady=(24, 20))
        inner.grid_columnconfigure(0, weight=1)
        self.pages[name] = p
        return inner

    def _empty_state(self, parent, icon, title, sub):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(f, text=icon, font=F(30), text_color=C["dim"]).pack()
        f.title = ctk.CTkLabel(f, text=title, font=F(15, "bold"), text_color=C["text"])
        f.title.pack(pady=(6, 2))
        f.sub = ctk.CTkLabel(f, text=sub, font=F(12), text_color=C["muted"])
        f.sub.pack()
        return f

    # ----------------------------------------------------------- sidebar
    def _build_sidebar(self):
        sb = ctk.CTkFrame(self.root, fg_color=C["side"], corner_radius=0, width=232)
        sb.grid(row=0, column=0, sticky="ns")
        sb.grid_propagate(False)
        sb.grid_columnconfigure(0, weight=1)
        sb.grid_rowconfigure(8, weight=1)

        brand = ctk.CTkFrame(sb, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 26))
        if THEME == "halloween":
            PumpkinLogo(brand).grid(row=0, column=0, rowspan=2)
        else:
            ctk.CTkLabel(brand, text="⇪", width=38, height=38, corner_radius=11, fg_color=C["accent"],
                         text_color=C["on_accent"], font=F(19, "bold")).grid(row=0, column=0, rowspan=2)
        ctk.CTkLabel(brand, text="Mod Manager", font=F(15, "bold"), text_color=C["text"]).grid(
            row=0, column=1, sticky="w", padx=(10, 0))
        ctk.CTkLabel(brand, text=f"for MediaFire  ·  v{VERSION}", font=F(11), text_color=C["muted"]).grid(
            row=1, column=1, sticky="w", padx=(10, 0))

        ctk.CTkLabel(sb, text="WORKSPACE", font=F(10, "bold"), text_color=C["dim"]).grid(
            row=1, column=0, sticky="w", padx=24, pady=(0, 6))
        self.nav = {}
        for i, (key, icon, text) in enumerate((("files", "▤", "Files"), ("uploads", "⇪", "Uploads"),
                                               ("history", "↺", "History"), ("watched", "◉", "Watched"))):
            n = NavItem(sb, icon, text, lambda k=key: self.show_page(k))
            n.grid(row=2 + i, column=0, sticky="ew", padx=12, pady=2)
            self.nav[key] = n

        acct = ctk.CTkFrame(sb, fg_color=C["card"], corner_radius=12, border_width=1,
                            border_color=C["border"])
        ctk.CTkLabel(sb, text="APP", font=F(10, "bold"), text_color=C["dim"]).grid(
            row=6, column=0, sticky="w", padx=24, pady=(18, 6))
        n = NavItem(sb, "⟳", "Updates", lambda: self.show_page("updates"))
        n.grid(row=7, column=0, sticky="ew", padx=12, pady=2)
        self.nav["updates"] = n

        if THEME == "halloween":
            NightScene(sb).grid(row=8, column=0, sticky="nsew", pady=(14, 0))
        tsw = ctk.CTkFrame(sb, fg_color="transparent")
        tsw.grid(row=9, column=0, sticky="ew", padx=12, pady=(12, 0))
        ctk.CTkLabel(tsw, text="THEME", font=F(10, "bold"), text_color=C["dim"]).pack(anchor="w", padx=12, pady=(0, 4))
        self.theme_seg = ctk.CTkSegmentedButton(
            tsw, values=list(THEME_LABELS.values()), command=self._choose_theme, height=30, corner_radius=9,
            font=F(12), fg_color=C["card"], selected_color=C["accent"], selected_hover_color=C["accent_h"],
            unselected_color=C["card"], unselected_hover_color=C["hover"], text_color=C["text"],
            text_color_disabled=C["dim"])
        self.theme_seg.set(THEME_LABELS[THEME])
        self.theme_seg.pack(fill="x")
        acct.grid(row=10, column=0, sticky="ew", padx=12, pady=14)
        acct.grid_columnconfigure(1, weight=1)
        self.avatar = ctk.CTkLabel(acct, text="?", width=34, height=34, corner_radius=17,
                                   fg_color=C["accent_soft"], text_color=C["accent"], font=F(14, "bold"))
        self.avatar.grid(row=0, column=0, rowspan=2, padx=(10, 8), pady=10)
        self.acct_name = ctk.CTkLabel(acct, text="Not signed in", font=F(12, "bold"),
                                      text_color=C["text"], anchor="w")
        self.acct_name.grid(row=0, column=1, columnspan=2, sticky="sw", pady=(10, 0), padx=(0, 10))
        self.acct_status = ctk.CTkLabel(acct, text="○  Offline", font=F(11), text_color=C["muted"],
                                        anchor="w")
        self.acct_status.grid(row=1, column=1, sticky="w", pady=(0, 8))
        self.logout_btn = ctk.CTkButton(acct, text="Sign out", width=58, height=26, corner_radius=7,
                                        fg_color="transparent", hover_color=C["hover"],
                                        text_color=C["muted"], font=F(11), command=self._logout)
        self.logout_btn.grid(row=1, column=2, padx=(0, 8), pady=(0, 8))

    def _choose_theme(self, label):
        name = next(k for k, v in THEME_LABELS.items() if v == label)
        if name == THEME:
            return
        busy = any(st in ("queued", "active") for st in self.job_state.values())
        msg = "The app restarts to switch themes." + (" Unfinished uploads will stop." if busy else "")
        if not self.confirm(f"Switch to {label}?", msg, "Restart now", danger=busy):
            self.theme_seg.set(THEME_LABELS[THEME])
            return
        self.cfg["theme"] = name
        _save_json(CONFIG_PATH, self.cfg)
        self._restart()

    def _restart(self):
        import subprocess
        if updater.is_frozen():
            args = [sys.executable]
        else:
            args = [sys.executable, os.path.abspath(sys.argv[0])]
        updater.launch_fresh(args, cwd=os.path.dirname(os.path.abspath(args[-1])))
        self._quit_for_update()

    def show_page(self, name):
        if self.page == name:
            return
        for k, p in self.pages.items():
            if k == name:
                p.grid(row=0, column=0, sticky="nsew")
            else:
                p.grid_remove()
        for k, n in self.nav.items():
            n.set_selected(k == name)
        self.page = name
        if self._toast is not None:
            self.root.after(30, self._reposition_toast)

    # -------------------------------------------------------- files page
    def _build_files_page(self):
        pg = self._new_page("files")
        pg.grid_rowconfigure(4, weight=1)
        self.files_sub, act = self._header(pg, "Files", "Your MediaFire storage")
        self.search = ctk.CTkEntry(act, placeholder_text="Search this folder", width=220, height=34,
                                   corner_radius=9, fg_color=C["card"], border_color=C["border"],
                                   font=F(12))
        self.search.pack(side="left", padx=(0, 8))
        self.search.bind("<KeyRelease>", lambda e: self._render_files())
        Btn(act, "+  New folder", self._new_folder, "secondary").pack(side="left", padx=(0, 8))
        Btn(act, "⟳", self._load_folder, "secondary", width=36).pack(side="left")

        # drop zone
        self.dz = ctk.CTkFrame(pg, fg_color=C["card"], corner_radius=16, border_width=2,
                               border_color=C["border"], height=108)
        self.dz.grid(row=1, column=0, sticky="ew")
        self.dz.grid_propagate(False)
        self.dz.grid_columnconfigure(1, weight=1)
        self.dz.grid_rowconfigure((0, 3), weight=1)
        self.dz_icon = ctk.CTkLabel(self.dz, text="⇪", width=52, height=52, corner_radius=26,
                                    fg_color=C["accent_soft"], text_color=C["accent"], font=F(22, "bold"))
        self.dz_icon.grid(row=1, column=0, rowspan=2, padx=(22, 16))
        self.dz_title = ctk.CTkLabel(self.dz, text="Drop files or mod folders here", font=F(15, "bold"),
                                     text_color=C["text"], anchor="w")
        self.dz_title.grid(row=1, column=1, sticky="sw")
        self.dz_sub = ctk.CTkLabel(self.dz, text="", font=F(12), text_color=C["muted"], anchor="w")
        self.dz_sub.grid(row=2, column=1, sticky="nw")
        bf = ctk.CTkFrame(self.dz, fg_color="transparent")
        bf.grid(row=1, column=2, rowspan=2, padx=20)
        Btn(bf, "Browse files", self._pick_files, "primary").pack(side="left", padx=(0, 8))
        Btn(bf, "Browse folder", self._pick_folder, "secondary").pack(side="left")
        for w in (self.dz, self.dz_icon, self.dz_title, self.dz_sub):
            w.bind("<Enter>", lambda e: self._dz_hover(True))
            w.bind("<Leave>", lambda e: self._dz_hover(False))

        # options
        opts = ctk.CTkFrame(pg, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", pady=(12, 14))
        sw = dict(onvalue=True, offvalue=False, progress_color=C["accent"], button_color=C["knob"],
                  button_hover_color="#dfe6f5", fg_color=C["card2"], font=F(12), text_color=C["text"],
                  command=self._save_opts)
        ctk.CTkSwitch(opts, text="Zip folders before upload", variable=self.zip_var, **sw).pack(side="left")
        ctk.CTkSwitch(opts, text="Keep the same link (replace by name)", variable=self.replace_var,
                      **sw).pack(side="left", padx=(22, 0))
        self.note_entry = ctk.CTkEntry(opts, placeholder_text="Version / note  —  e.g. v1.2 new paints",
                                       width=290, height=34, corner_radius=9, fg_color=C["card"],
                                       border_color=C["border"], font=F(12))
        self.note_entry.pack(side="right")

        # breadcrumb + loading bar
        crumb_row = ctk.CTkFrame(pg, fg_color="transparent")
        crumb_row.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        crumb_row.grid_columnconfigure(0, weight=1)
        self.crumbs = ctk.CTkFrame(crumb_row, fg_color="transparent")
        self.crumbs.grid(row=0, column=0, sticky="w")
        self.count_lbl = ctk.CTkLabel(crumb_row, text="", font=F(11), text_color=C["dim"])
        self.count_lbl.grid(row=0, column=1, sticky="e")
        self.loading = ctk.CTkProgressBar(crumb_row, mode="indeterminate", height=3, corner_radius=2,
                                          progress_color=C["accent"], fg_color=C["bg"])
        self.loading.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.loading.grid_remove()

        card, self.files_tree = self._table(pg, ("Size", "Version", "Modified"), (100, 170, 170),
                                            tree_col=("Name", 420))
        card.grid(row=4, column=0, sticky="nsew")
        self.files_empty = self._empty_state(card, "☁", "This folder is empty",
                                             "Drop files or mod folders above to upload them here")
        t = self.files_tree
        t.bind("<Double-1>", self._open_item)
        t.bind("<Return>", self._open_item)
        t.bind("<Button-3>", self._files_menu)
        t.bind("<<TreeviewSelect>>", lambda e: self._update_actionbar())

        # selection action bar
        self.actionbar = ctk.CTkFrame(pg, fg_color=C["card2"], corner_radius=12, border_width=1,
                                      border_color=C["border_hi"])
        self.actionbar.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        self.sel_lbl = ctk.CTkLabel(self.actionbar, text="", font=F(12, "bold"), text_color=C["text"])
        self.sel_lbl.pack(side="left", padx=16, pady=10)
        Btn(self.actionbar, "Delete", self._delete_selected, "danger", height=32, width=76).pack(side="right", padx=(6, 10))
        Btn(self.actionbar, "Watch local copy…", self._watch_for_selected, "secondary",
            height=32, width=130).pack(side="right", padx=3)
        Btn(self.actionbar, "Update from file…", self._update_selected, "secondary",
            height=32, width=130).pack(side="right", padx=3)
        Btn(self.actionbar, "Open", self._open_link, "secondary", height=32, width=64).pack(side="right", padx=3)
        Btn(self.actionbar, "Copy link", self._copy_link, "primary", height=32, width=96).pack(side="right", padx=3)
        self.actionbar.grid_remove()
        self._update_dz_sub()

    def _dz_hover(self, on):
        if getattr(self, "_dragging", False):
            return
        if not on:
            x, y = self.root.winfo_pointerxy()
            if _inside(self.root.winfo_containing(x, y), self.dz):
                return
        self.dz.configure(border_color=C["border_hi"] if on else C["border"])

    def _set_drag(self, on):
        self._dragging = on
        if on:
            self.dz.configure(border_color=C["accent"], fg_color=C["accent_soft"])
            self.dz_icon.configure(fg_color=C["accent"], text_color=C["on_accent"])
            self.dz_title.configure(text="Release to upload")
        else:
            self.dz.configure(border_color=C["border"], fg_color=C["card"])
            self.dz_icon.configure(fg_color=C["accent_soft"], text_color=C["accent"])
            self.dz_title.configure(text="Drop files or mod folders here")

    def _update_dz_sub(self):
        extra = "  ·  folders are zipped first" if self.zip_var.get() else ""
        self.dz_sub.configure(text=f"Uploads to  {self._cur_path(' › ')}{extra}")

    # ------------------------------------------------------ uploads page
    def _build_uploads_page(self):
        pg = self._new_page("uploads")
        pg.grid_rowconfigure(1, weight=1)
        self.up_sub, act = self._header(pg, "Uploads", "Nothing uploading right now")
        Btn(act, "Clear finished", self._clear_finished, "secondary").pack(side="left")
        self.up_list = ctk.CTkScrollableFrame(pg, fg_color="transparent", corner_radius=0,
                                              scrollbar_button_color=C["card2"],
                                              scrollbar_button_hover_color=C["hover"])
        self.up_list.grid(row=1, column=0, sticky="nsew")
        self.up_list.grid_columnconfigure(0, weight=1)
        self.up_empty = self._empty_state(self.up_list, "⇪", "No uploads yet",
                                          "Drop something on the Files page to get started")

    def _job_card(self, job):
        card = ctk.CTkFrame(self.up_list, fg_color=C["card"], corner_radius=14, border_width=1,
                            border_color=C["border"])
        card.grid(row=len(self.job_cards) + 1, column=0, sticky="ew", pady=(0, 10), padx=(0, 6))
        card.grid_columnconfigure(1, weight=1)
        isdir = os.path.isdir(job["path"])
        ctk.CTkLabel(card, text="", image=self.icons["folder" if isdir else "zip"], width=40, height=40,
                     corner_radius=10, fg_color=C["card2"]).grid(row=0, column=0, rowspan=2,
                                                                  padx=(16, 12), pady=(14, 4))
        name = os.path.basename(job["path"]) + (".zip" if isdir and job["zip"] else "")
        ctk.CTkLabel(card, text=job.get("remote_name") or name, font=F(14, "bold"), text_color=C["text"],
                     anchor="w").grid(row=0, column=1, sticky="sw", pady=(14, 0))
        sub = f"→  {job['folder_name'].replace(' / ', ' › ')}"
        if job["note"]:
            sub += f"   ·   {job['note']}"
        ctk.CTkLabel(card, text=sub, font=F(12), text_color=C["muted"], anchor="w").grid(
            row=1, column=1, sticky="nw")
        top_right = ctk.CTkFrame(card, fg_color="transparent")
        top_right.grid(row=0, column=2, rowspan=2, padx=16, sticky="e")
        pill = Pill(top_right, "Queued", "neutral")
        pill.pack(side="right")
        bar = ctk.CTkProgressBar(card, height=6, corner_radius=3, progress_color=C["accent"],
                                 fg_color=C["card2"])
        bar.set(0)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(16, 10), pady=(10, 16))
        pct = ctk.CTkLabel(card, text="", font=F(12, "bold"), text_color=C["muted"], width=44, anchor="e")
        pct.grid(row=2, column=2, sticky="e", padx=16, pady=(4, 10))
        link_btn = Btn(top_right, "Copy link", None, "secondary", height=28, width=96, font=F(11))
        self.job_cards[job["id"]] = dict(card=card, pill=pill, bar=bar, pct=pct, link=link_btn)
        self.job_state[job["id"]] = "queued"

    def _update_job(self, jid, kw):
        c = self.job_cards.get(jid)
        if not c:
            return
        state = kw.get("state")
        if state:
            self.job_state[jid] = state
        if "progress" in kw and kw["progress"] is not None:
            c["bar"].set(kw["progress"])
            c["pct"].configure(text=f"{int(kw['progress'] * 100)}%")
        if kw.get("status"):
            kind = {"done": "ok", "failed": "bad", "unchanged": "neutral"}.get(state or self.job_state[jid], "info")
            set_pill(c["pill"], kw["status"], kind)
        if state == "done":
            c["bar"].configure(progress_color=C["ok"])
            c["bar"].set(1)
            c["pct"].configure(text="✓", text_color=C["ok"])
            if kw.get("link"):
                c["link"].configure(command=lambda l=kw["link"]: self._copy_text(l, "Link copied"))
                c["link"].pack(side="right", padx=(0, 10))
        elif state == "failed":
            c["bar"].configure(progress_color=C["bad"])
            c["pct"].configure(text="✕", text_color=C["bad"])
            c["card"].configure(border_color="#4a2424")
        elif state == "unchanged":
            c["bar"].configure(progress_color=C["dim"])
            c["bar"].set(1)
            c["pct"].configure(text="=")
        if state in ("done", "failed", "unchanged", "active"):
            self._refresh_upload_counts()

    def _refresh_upload_counts(self):
        active = sum(1 for s in self.job_state.values() if s in ("queued", "active"))
        done = sum(1 for s in self.job_state.values() if s in ("done", "unchanged"))
        failed = sum(1 for s in self.job_state.values() if s == "failed")
        self.nav["uploads"].set_badge(active, "accent")
        parts = []
        if active:
            parts.append(f"{active} in progress")
        if done:
            parts.append(f"{done} finished")
        if failed:
            parts.append(f"{failed} failed")
        self.up_sub.set("  ·  ".join(parts) if parts else "Nothing uploading right now")

    def _refresh_uploads_empty(self):
        if self.job_cards:
            self.up_empty.grid_remove()
        else:
            self.up_empty.grid(row=0, column=0, pady=120)

    def _clear_finished(self):
        for jid in [j for j, s in self.job_state.items() if s in ("done", "failed", "unchanged")]:
            self.job_cards.pop(jid)["card"].destroy()
            self.job_state.pop(jid)
        for i, c in enumerate(self.job_cards.values()):
            c["card"].grid(row=i + 1)
        self._refresh_upload_counts()
        self._refresh_uploads_empty()

    # ------------------------------------------------------ history page
    def _build_history_page(self):
        pg = self._new_page("history")
        pg.grid_rowconfigure(1, weight=1)
        self.hist_sub, act = self._header(pg, "History", "Every upload, with its version note and link")
        self.hist_search = ctk.CTkEntry(act, placeholder_text="Search history", width=220, height=34,
                                        corner_radius=9, fg_color=C["card"], border_color=C["border"],
                                        font=F(12))
        self.hist_search.pack(side="left", padx=(0, 8))
        self.hist_search.bind("<KeyRelease>", lambda e: self._refresh_history())
        Btn(act, "Open log file", lambda: self._open_path(HISTORY_PATH), "secondary").pack(side="left")
        card, self.hist_tree = self._table(pg, ("When", "Version", "Action", "Size", "Folder"),
                                           (140, 150, 100, 80, 170), tree_col=("File", 220))
        card.grid(row=1, column=0, sticky="nsew")
        self.hist_tree.bind("<Double-1>", lambda e: self._copy_hist_link())
        self.hist_tree.bind("<Button-3>", self._hist_menu)
        ctk.CTkLabel(pg, text="Double-click a row to copy its link", font=F(11), text_color=C["dim"]).grid(
            row=2, column=0, sticky="w", pady=(8, 0))

    # -------------------------------------------------------- watch page
    def _build_watch_page(self):
        pg = self._new_page("watched")
        pg.grid_rowconfigure(1, weight=1)
        self.watch_sub, act = self._header(pg, "Watched", "Get told when a mod changes on your PC")
        Btn(act, "+  Watch file", lambda: self._add_watch(folder=False), "secondary").pack(side="left", padx=(0, 8))
        Btn(act, "+  Watch folder", lambda: self._add_watch(folder=True), "secondary").pack(side="left", padx=(0, 8))
        self.upload_changed_btn = Btn(act, "Upload changed", self._upload_changed, "primary")
        self.upload_changed_btn.pack(side="left")
        self.watch_list = ctk.CTkScrollableFrame(pg, fg_color="transparent", corner_radius=0,
                                                 scrollbar_button_color=C["card2"],
                                                 scrollbar_button_hover_color=C["hover"])
        self.watch_list.grid(row=1, column=0, sticky="nsew")
        self.watch_list.grid_columnconfigure(0, weight=1)
        self.watch_empty = self._empty_state(
            self.watch_list, "◉", "Nothing watched yet",
            "Watch a mod folder and this app will spot when you change it")
        ctk.CTkLabel(pg, text="New watches upload to the MediaFire folder that's open on the Files page",
                     font=F(11), text_color=C["dim"]).grid(row=2, column=0, sticky="w", pady=(8, 0))

    def _watch_card(self, w, row):
        card = ctk.CTkFrame(self.watch_list, fg_color=C["card"], corner_radius=14, border_width=1,
                            border_color=C["border"])
        card.grid(row=row, column=0, sticky="ew", pady=(0, 10), padx=(0, 6))
        card.grid_columnconfigure(1, weight=1)
        isdir = os.path.isdir(w["path"]) or not os.path.splitext(w["path"])[1]
        ctk.CTkLabel(card, text="", image=self.icons["folder" if isdir else "file"], width=40, height=40,
                     corner_radius=10, fg_color=C["card2"]).grid(row=0, column=0, rowspan=2,
                                                                  padx=(16, 12), pady=16)
        ctk.CTkLabel(card, text=os.path.basename(w["path"]), font=F(14, "bold"), text_color=C["text"],
                     anchor="w").grid(row=0, column=1, sticky="sw", pady=(14, 0))
        dest = w["folder_name"].replace(" / ", " › ") + (f" › {w['remote_name']}" if w.get("remote_name") else "")
        ctk.CTkLabel(card, text=f"{w['path']}   →   {dest}", font=F(11), text_color=C["muted"],
                     anchor="w").grid(row=1, column=1, sticky="nw", pady=(0, 14))
        pill = Pill(card, "Up to date", "ok")
        pill.grid(row=0, column=2, rowspan=2, padx=(10, 14))
        auto = tk.BooleanVar(value=bool(w.get("auto")))
        ctk.CTkSwitch(card, text="Auto", variable=auto, onvalue=True, offvalue=False, width=60,
                      progress_color=C["accent"], fg_color=C["card2"], font=F(12), text_color=C["muted"],
                      command=lambda wid=w["id"], v=auto: self._set_auto(wid, v.get())).grid(
            row=0, column=3, rowspan=2, padx=(0, 10))
        up = Btn(card, "Upload now", lambda wid=w["id"]: self._upload_watch(self._watch(wid)), "secondary",
                 height=32)
        up.grid(row=0, column=4, rowspan=2, padx=(0, 6))
        rm = ctk.CTkButton(card, text="✕", width=32, height=32, corner_radius=8, fg_color="transparent",
                           hover_color=C["bad_bg"], text_color=C["dim"], font=F(13),
                           command=lambda wid=w["id"]: self._remove_watch(wid))
        rm.grid(row=0, column=5, rowspan=2, padx=(0, 14))
        self.watch_cards[w["id"]] = dict(card=card, pill=pill, up=up)

    # --------------------------------------------------------------- login
    def _build_login(self):
        self.login = ctk.CTkFrame(self.root, fg_color=C["bg"], corner_radius=0)
        card = ctk.CTkFrame(self.login, fg_color=C["card"], corner_radius=18, border_width=1,
                            border_color=C["border"])
        card.place(relx=0.5, rely=0.5, anchor="center")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=36, pady=32)
        if THEME == "halloween":
            PumpkinLogo(inner, size=56, bg=C["card"]).pack()
        else:
            ctk.CTkLabel(inner, text="⇪", width=52, height=52, corner_radius=14, fg_color=C["accent"],
                         text_color=C["on_accent"], font=F(24, "bold")).pack()
        ctk.CTkLabel(inner, text="Sign in to MediaFire", font=F(20, "bold"), text_color=C["text"]).pack(
            pady=(16, 2))
        ctk.CTkLabel(inner, text="Use the email and password for your MediaFire account",
                     font=F(12), text_color=C["muted"]).pack(pady=(0, 20))
        ent = dict(width=320, height=42, corner_radius=10, fg_color=C["bg"], border_color=C["border"],
                   font=F(13))
        self.l_email = ctk.CTkEntry(inner, placeholder_text="Email", **ent)
        self.l_email.pack(pady=(0, 10))
        self.l_pw = ctk.CTkEntry(inner, placeholder_text="Password", show="•", **ent)
        self.l_pw.pack()
        self.l_remember = tk.BooleanVar(value=bool(keyring))
        cb = ctk.CTkCheckBox(inner, text="Remember me on this PC", variable=self.l_remember, font=F(12),
                             text_color=C["muted"], fg_color=C["accent"], hover_color=C["accent_h"],
                             border_color=C["border_hi"], checkbox_width=18, checkbox_height=18,
                             corner_radius=5)
        cb.pack(anchor="w", pady=(14, 0))
        if not keyring:
            cb.configure(state="disabled")
        self.l_err = ctk.CTkLabel(inner, text="", font=F(12), text_color=C["bad"], wraplength=320,
                                  justify="left")
        self.l_err.pack(anchor="w", pady=(8, 0))
        self.l_btn = Btn(inner, "Sign in", self._login_submit, "primary", width=320, height=42,
                         font=F(13, "bold"))
        self.l_btn.pack(pady=(6, 0))
        ctk.CTkLabel(inner, text="Sign in with Google or Facebook? Set a password in your\n"
                                 "MediaFire account settings first.",
                     font=F(11), text_color=C["dim"], justify="center").pack(pady=(16, 0))
        for w in (self.l_email, self.l_pw):
            w.bind("<Return>", lambda e: self._login_submit())

    def show_login(self, error=""):
        self.l_err.configure(text=error)
        self.l_btn.configure(state="normal", text="Sign in")
        if self.cfg.get("email") and not self.l_email.get():
            self.l_email.insert(0, self.cfg["email"])
        self.login.place(x=0, y=0, relwidth=1, relheight=1)
        self.login.lift()
        (self.l_pw if self.l_email.get() else self.l_email).focus_set()

    def _login_submit(self):
        email, pw = self.l_email.get().strip(), self.l_pw.get()
        if not email or not pw:
            self.l_err.configure(text="Enter your email and password.")
            return
        self.l_btn.configure(state="disabled", text="Signing in…")
        self._do_login(email, pw, self.l_remember.get())

    # ======================================================= login logic
    def _save_opts(self):
        self.cfg["zip_folders"] = self.zip_var.get()
        self.cfg["replace_same_name"] = self.replace_var.get()
        _save_json(CONFIG_PATH, self.cfg)
        self._update_dz_sub()

    def _auto_login(self):
        email = self.cfg.get("email")
        pw = None
        if email and keyring and self.cfg.get("remember"):
            try:
                pw = keyring.get_password(KEYRING_SERVICE, email)
            except Exception:
                pw = None
        if email and pw:
            self._do_login(email, pw, remember=True)
        else:
            self.show_login()

    def _logout(self):
        if not self.api.logged_in:
            self.show_login()
            return
        if not self.confirm("Log out?", "You'll need your password to sign in again.", "Log out"):
            return
        self.api.logout()
        if keyring and self.cfg.get("email"):
            try:
                keyring.delete_password(KEYRING_SERVICE, self.cfg["email"])
            except Exception:
                pass
        self.cfg["remember"] = False
        _save_json(CONFIG_PATH, self.cfg)
        self.acct_name.configure(text="Not signed in")
        self.acct_status.configure(text="○  Offline", text_color=C["muted"])
        self.avatar.configure(text="?")
        self.files_tree.delete(*self.files_tree.get_children())
        self.l_pw.delete(0, "end")
        self.show_login()

    def _do_login(self, email, pw, remember):
        def work():
            self.api.login(email, pw)

        def done(_):
            self.cfg["email"] = email
            self.cfg["remember"] = bool(remember and keyring)
            if remember and keyring:
                try:
                    keyring.set_password(KEYRING_SERVICE, email, pw)
                except Exception:
                    pass
            _save_json(CONFIG_PATH, self.cfg)
            self.login.place_forget()
            self.l_pw.delete(0, "end")
            short = email if len(email) <= 22 else email[:20] + "…"
            self.acct_name.configure(text=short)
            self.acct_status.configure(text="●  Connected", text_color=C["ok"])
            self.avatar.configure(text=email[:1].upper())
            last = self.cfg.get("last_folder_stack")
            if last:
                self.folder_stack = [tuple(x) for x in last]
            self._load_folder()

        def fail(e):
            msg = e.message if isinstance(e, MFError) else str(e)
            if isinstance(e, MFError) and e.code == 107:
                msg = "That email or password didn't work."
            elif not isinstance(e, MFError):
                msg = "Couldn't reach MediaFire. Check your internet connection."
            self.show_login(msg)

        self._bg(work, done, fail)

    # ========================================================== threading
    def _bg(self, fn, on_ok=None, on_err=None):
        def run():
            try:
                res = fn()
                self.ui_q.put(("call", on_ok, res))
            except Exception as e:
                self.ui_q.put(("call", on_err or self._show_error, e))
        threading.Thread(target=run, daemon=True).start()

    def _show_error(self, e):
        self.toast(e.message if isinstance(e, MFError) else str(e), "bad")

    def _pump(self):
        try:
            while True:
                kind, *rest = self.ui_q.get_nowait()
                if kind == "call":
                    fn, arg = rest
                    if fn:
                        fn(arg)
                elif kind == "job":
                    self._update_job(*rest)
                elif kind == "watch":
                    self._on_watch_event(*rest)
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    # ============================================================== toast
    def toast(self, text, kind="ok"):
        icons = {"ok": "✓", "bad": "✕", "warn": "!", "info": "i"}
        if self._toast is None:
            t = ctk.CTkFrame(self.root, fg_color=C["card2"], corner_radius=12, border_width=1,
                             border_color=C["border_hi"])
            t.ic = ctk.CTkLabel(t, text="", width=24, height=24, corner_radius=12, font=F(12, "bold"))
            t.ic.pack(side="left", padx=(12, 10), pady=12)
            t.lb = ctk.CTkLabel(t, text="", font=F(12), text_color=C["text"], wraplength=360, justify="left")
            t.lb.pack(side="left", padx=(0, 16))
            self._toast = t
        t = self._toast
        bg, fg = PILL[kind if kind in PILL else "info"]
        t.ic.configure(text=icons.get(kind, "i"), fg_color=C[bg], text_color=C[fg])
        t.lb.configure(text=text)
        if self._toast_after:
            self.root.after_cancel(self._toast_after)

        def slide(y, end, step, then=None):
            t.place(relx=1, rely=1, x=-24, y=y, anchor="se")
            t.lift()
            if (step < 0 and y > end) or (step > 0 and y < end):
                self.root.after(14, lambda: slide(y + step, end, step, then))
            elif then:
                then()

        end = self._toast_end()
        slide(40, end, -8)
        self._toast_after = self.root.after(2800, lambda: slide(self._toast_end(), 80, 10, t.place_forget))

    def _toast_end(self):
        if self.page == "files" and self.actionbar.winfo_ismapped():
            return -24 - max(self.actionbar.winfo_height(), 50) - 12
        return -24

    def _reposition_toast(self):
        if self._toast is not None and self._toast.winfo_ismapped():
            self._toast.place(relx=1, rely=1, x=-24, y=self._toast_end(), anchor="se")

    def _copy_text(self, text, msg):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.toast(msg, "ok")

    def confirm(self, title, message, ok="OK", danger=False):
        d = ctk.CTkToplevel(self.root)
        d.title(title)
        d.configure(fg_color=C["card"])
        d.resizable(False, False)
        d.transient(self.root)
        res = {"v": False}
        f = ctk.CTkFrame(d, fg_color="transparent")
        f.pack(padx=26, pady=22)
        ctk.CTkLabel(f, text=title, font=F(16, "bold"), text_color=C["text"]).pack(anchor="w")
        ctk.CTkLabel(f, text=message, font=F(12), text_color=C["muted"], wraplength=380,
                     justify="left").pack(anchor="w", pady=(6, 18))
        bf = ctk.CTkFrame(f, fg_color="transparent")
        bf.pack(fill="x")

        def close(v):
            res["v"] = v
            d.grab_release()
            d.destroy()

        ok_btn = Btn(bf, ok, lambda: close(True), "primary")
        if danger:
            ok_btn.configure(fg_color=C["bad"], hover_color="#d95656")
        ok_btn.pack(side="right")
        Btn(bf, "Cancel", lambda: close(False), "secondary").pack(side="right", padx=8)
        d.bind("<Escape>", lambda e: close(False))
        d.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - d.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - d.winfo_height()) // 3
        d.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        d.after(50, d.grab_set)
        self.root.wait_window(d)
        return res["v"]

    # ============================================================= menus
    def _make_menus(self):
        mk = lambda: tk.Menu(self.root, tearoff=0, bg=C["card2"], fg=C["text"], activebackground=C["accent"],
                             activeforeground=C["on_accent"], bd=0, relief="flat", font=(FAMILY, 10))
        self.menu = mk()
        self.menu.add_command(label="  Copy link", command=self._copy_link)
        self.menu.add_command(label="  Open in browser", command=self._open_link)
        self.menu.add_command(label="  Update from file…", command=self._update_selected)
        self.menu.add_command(label="  Watch a local copy…", command=self._watch_for_selected)
        self.menu.add_separator()
        self.menu.add_command(label="  Delete", command=self._delete_selected, foreground=C["bad"])
        self.hmenu = mk()
        self.hmenu.add_command(label="  Copy link", command=self._copy_hist_link)
        self.hmenu.add_command(label="  Open in browser", command=self._open_hist_link)

    # ========================================================= browsing
    def _cur_key(self):
        return self.folder_stack[-1][0]

    def _cur_path(self, sep=" / "):
        return sep.join(n for _, n in self.folder_stack)

    def _render_crumbs(self):
        for w in self.crumbs.winfo_children():
            w.destroy()
        for i, (_, name) in enumerate(self.folder_stack):
            last = i == len(self.folder_stack) - 1
            if i:
                ctk.CTkLabel(self.crumbs, text="›", font=F(14), text_color=C["dim"]).pack(side="left", padx=2)
            b = ctk.CTkButton(self.crumbs, text=("☁  " if i == 0 else "") + name, height=28, width=10,
                              corner_radius=7, fg_color=C["card2"] if last else "transparent",
                              hover_color=C["hover"], text_color=C["text"] if last else C["muted"],
                              font=F(12, "bold" if last else "normal"),
                              command=lambda n=i: self._jump(n))
            b.pack(side="left")

    def _jump(self, idx):
        if idx < len(self.folder_stack) - 1:
            self.folder_stack = self.folder_stack[:idx + 1]
            self._load_folder()

    def _load_folder(self):
        if not self.api.logged_in:
            return
        key = self._cur_key()
        self._render_crumbs()
        self._update_dz_sub()
        self.loading.grid()
        self.loading.start()
        self.cfg["last_folder_stack"] = self.folder_stack
        _save_json(CONFIG_PATH, self.cfg)

        def stop():
            self.loading.stop()
            self.loading.grid_remove()

        def done(res):
            stop()
            self.folder_items = res
            self._render_files()

        def fail(e):
            stop()
            if isinstance(e, MFError) and e.code == 112 and len(self.folder_stack) > 1:
                self.folder_stack = [(None, "My Files")]
                self._load_folder()
            else:
                self._show_error(e)

        self._bg(lambda: self.api.list_folder(key), done, fail)

    def _render_files(self):
        folders, files = self.folder_items
        q = self.search.get().strip().lower()
        t = self.files_tree
        t.delete(*t.get_children())
        self._hover_row = None
        self.current_files = {}
        versions = self._latest_versions()
        i = 0
        for fo in sorted(folders, key=lambda x: x.get("name", "").lower()):
            if q and q not in fo.get("name", "").lower():
                continue
            t.insert("", "end", iid="D:" + fo["folderkey"], text="  " + fo.get("name", ""),
                     image=self.icons["folder"], values=("—", "", nice_date(fo.get("created", ""))),
                     tags=("odd",) if i % 2 else ())
            i += 1
        for fi in sorted(files, key=lambda x: x.get("filename", "").lower()):
            qk = fi["quickkey"]
            self.current_files[qk] = fi
            if q and q not in fi.get("filename", "").lower():
                continue
            name = fi.get("filename", "")
            t.insert("", "end", iid="F:" + qk, text="  " + name,
                     image=self.icons["zip" if name.lower().endswith((".zip", ".rar", ".7z")) else "file"],
                     values=(human_size(fi.get("size")), versions.get(qk, ""),
                             nice_date(fi.get("created", ""))),
                     tags=("odd",) if i % 2 else ())
            i += 1
        nf, nfi = len(folders), len(files)
        self.count_lbl.configure(text=f"{nf} folder{'s' * (nf != 1)}  ·  {nfi} file{'s' * (nfi != 1)}")
        if i:
            self.files_empty.place_forget()
        else:
            if q:
                self.files_empty.title.configure(text="No matches")
                self.files_empty.sub.configure(text=f"Nothing in this folder matches “{q}”")
            else:
                self.files_empty.title.configure(text="This folder is empty")
                self.files_empty.sub.configure(text="Drop files or mod folders above to upload them here")
            self.files_empty.place(relx=0.5, rely=0.45, anchor="center")
        self._update_actionbar()

    def _update_actionbar(self):
        sel = self._selected_files()
        if not sel:
            self.actionbar.grid_remove()
        else:
            name = self.current_files[sel[0]].get("filename", "") if len(sel) == 1 else f"{len(sel)} files"
            self.sel_lbl.configure(text=f"{name}  selected")
            self.actionbar.grid()
        self.root.after(30, self._reposition_toast)

    def _open_item(self, _e=None):
        sel = self.files_tree.focus()
        if sel.startswith("D:"):
            name = self.files_tree.item(sel, "text").strip()
            self.folder_stack.append((sel[2:], name))
            self.search.delete(0, "end")
            self._load_folder()
        elif sel.startswith("F:"):
            self._copy_link()

    def _new_folder(self):
        if not self.api.logged_in:
            return
        dlg = ctk.CTkInputDialog(text="Folder name", title="New folder", fg_color=C["card"],
                                 button_fg_color=C["accent"], button_hover_color=C["accent_h"],
                                 entry_fg_color=C["bg"], entry_border_color=C["border"])
        name = dlg.get_input()
        if name and name.strip():
            self._bg(lambda: self.api.create_folder(name.strip(), self._cur_key()),
                     lambda _: (self._load_folder(), self.toast(f"Created “{name.strip()}”")))

    def _files_menu(self, e):
        row = self.files_tree.identify_row(e.y)
        if row and row.startswith("F:"):
            if row not in self.files_tree.selection():
                self.files_tree.selection_set(row)
            self.files_tree.focus(row)
            self.menu.tk_popup(e.x_root, e.y_root)

    def _selected_files(self):
        return [i[2:] for i in self.files_tree.selection() if i.startswith("F:")]

    def _copy_link(self):
        qks = self._selected_files()
        if not qks:
            return
        links = [share_link(q, self.current_files[q].get("filename", "")) for q in qks]
        self._copy_text("\n".join(links), "Link copied" if len(links) == 1 else f"{len(links)} links copied")

    def _open_link(self):
        for q in self._selected_files()[:5]:
            webbrowser.open(share_link(q, self.current_files[q].get("filename", "")))

    def _delete_selected(self):
        qks = self._selected_files()
        if not qks:
            return
        names = ", ".join(self.current_files[q].get("filename", "") for q in qks)
        if not self.confirm("Move to trash?", f"{names}\n\nAnyone with the link won't be able to "
                            "download it anymore.", "Delete", danger=True):
            return
        self._bg(lambda: [self.api.delete_file(q) for q in qks],
                 lambda _: (self._load_folder(), self.toast("Moved to MediaFire trash")))

    def _update_selected(self):
        qks = self._selected_files()
        if len(qks) != 1:
            self.toast("Select one file to update", "warn")
            return
        fi = self.current_files[qks[0]]
        path = filedialog.askopenfilename(title=f"New version of {fi.get('filename')}")
        if path:
            self._enqueue(path, force_quickkey=qks[0], remote_name=fi.get("filename"))

    def _watch_for_selected(self):
        qks = self._selected_files()
        if len(qks) != 1:
            self.toast("Select one file", "warn")
            return
        fi = self.current_files[qks[0]]
        if fi.get("filename", "").lower().endswith(".zip"):
            path = filedialog.askdirectory(title=f"Local folder that becomes {fi.get('filename')}")
        else:
            path = filedialog.askopenfilename(title=f"Local file for {fi.get('filename')}")
        if path:
            self._create_watch(path, remote_name=fi.get("filename"))

    # ============================================================ drag/drop
    def _register_drop(self, widget):
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropEnter>>", self._drop_enter)
            widget.dnd_bind("<<DropPosition>>", self._drop_enter)
            widget.dnd_bind("<<DropLeave>>", self._drop_leave)
            widget.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass
        for child in widget.winfo_children():
            if child is not getattr(self, "login", None):
                self._register_drop(child)

    def _drop_enter(self, event):
        if self._drag_after:
            self.root.after_cancel(self._drag_after)
            self._drag_after = None
        if not getattr(self, "_dragging", False):
            self.show_page("files")
            self._set_drag(True)
        return event.action

    def _drop_leave(self, event):
        self._drag_after = self.root.after(120, lambda: self._set_drag(False))
        return event.action

    def _on_drop(self, event):
        self._set_drag(False)
        for p in self.root.tk.splitlist(event.data):
            self._enqueue(p)
        return event.action

    def _pick_files(self):
        for p in filedialog.askopenfilenames(title="Upload files"):
            self._enqueue(p)

    def _pick_folder(self):
        p = filedialog.askdirectory(title="Upload folder")
        if p:
            self._enqueue(p)

    # ============================================================== uploads
    def _enqueue(self, path, folder_key="__current__", folder_name=None, remote_name=None,
                 force_quickkey=None, watch_id=None, note=None, sig=None, zip_=None):
        if not self.api.logged_in:
            self.toast("Sign in first", "warn")
            return
        path = os.path.normpath(path)
        if folder_key == "__current__":
            folder_key, folder_name = self._cur_key(), self._cur_path()
        job = {
            "id": uuid.uuid4().hex, "path": path, "folder_key": folder_key,
            "folder_name": folder_name or "My Files", "remote_name": remote_name,
            "zip": self.zip_var.get() if zip_ is None else zip_, "replace": self.replace_var.get(),
            "force_quickkey": force_quickkey, "watch_id": watch_id, "sig": sig,
            "note": self.note_entry.get().strip() if note is None else note,
        }
        self._job_card(job)
        self._refresh_uploads_empty()
        self._refresh_upload_counts()
        self.jobs.put(job)
        if not watch_id:
            self.toast(f"Uploading {os.path.basename(path)}", "info")

    def _upload_worker(self):
        while True:
            job = self.jobs.get()
            jid = job["id"]
            post = lambda **kw: self.ui_q.put(("job", jid, kw))
            tmpdir = None
            try:
                post(state="active", status="Starting")
                files = []
                if os.path.isdir(job["path"]):
                    if job["zip"]:
                        post(status="Zipping")
                        tmpdir = tempfile.mkdtemp(prefix="mfmm_")
                        z = zip_folder(job["path"], tmpdir)
                        files.append((z, job["folder_key"], job["folder_name"],
                                      job["remote_name"] or os.path.basename(z)))
                    else:
                        files.extend(self._mirror_folder(job, post))
                elif os.path.isfile(job["path"]):
                    files.append((job["path"], job["folder_key"], job["folder_name"],
                                  job["remote_name"] or os.path.basename(job["path"])))
                else:
                    raise FileNotFoundError(f"Not found: {job['path']}")

                actions, link = [], None
                for n, (lf, fkey, fname, rname) in enumerate(files, 1):
                    prefix = f"{n}/{len(files)} · " if len(files) > 1 else ""
                    qk = job["force_quickkey"]
                    if not qk and job["replace"]:
                        post(status=prefix + "Checking")
                        existing = self.api.find_file(fkey, rname)
                        qk = existing["quickkey"] if existing else None
                    res = self.api.upload(
                        lf, folder_key=fkey, remote_name=rname, replace_quickkey=qk,
                        progress=lambda d, t, n=n: post(progress=((n - 1) + d / max(t, 1)) / len(files)),
                        status=lambda s, p=prefix: post(status=p + s))
                    action = "unchanged" if res.get("unchanged") else ("updated" if qk else "new")
                    same_link = (not qk) or res["quickkey"] == qk
                    self._log_history(job, fname, rname, res, action, same_link)
                    actions.append((action, same_link))
                    link = share_link(res["quickkey"], rname)
                if all(a == "unchanged" for a, _ in actions):
                    post(state="unchanged", status="Already up to date")
                else:
                    kinds = {a for a, _ in actions}
                    label = "Updated · same link" if kinds == {"updated"} else (
                        "Uploaded" if kinds == {"new"} else "Done")
                    if not all(s for _, s in actions):
                        label = "Done · link changed"
                    post(state="done", status=label, link=link if len(files) == 1 else None)
                    name = os.path.basename(job["path"])
                    self.ui_q.put(("call", lambda _, n=name: self.toast(f"{n} uploaded", "ok"), None))
                if job["watch_id"]:
                    self.ui_q.put(("watch", "uploaded", job["watch_id"], job.get("sig")))
                if self._cur_key() in {f[1] for f in files}:
                    self.ui_q.put(("call", lambda _: self._load_folder(), None))
            except Exception as e:
                msg = e.message if isinstance(e, MFError) else str(e)
                post(state="failed", status="Failed")
                self.ui_q.put(("call", lambda _, m=msg: self.toast(m, "bad"), None))
                if job["watch_id"]:
                    self.ui_q.put(("watch", "failed", job["watch_id"], None))
            finally:
                if tmpdir:
                    shutil.rmtree(tmpdir, ignore_errors=True)

    def _mirror_folder(self, job, post):
        """Recreate a local folder tree on MediaFire; returns the files to upload."""
        post(status="Creating folders")
        base = os.path.normpath(job["path"])
        top = os.path.basename(base)
        keys = {base: (self.api.create_folder(top, job["folder_key"]), f"{job['folder_name']} / {top}")}
        out = []
        for root, dirs, files in os.walk(base):
            dirs.sort()
            pkey, pname = keys[root]
            for d in dirs:
                keys[os.path.join(root, d)] = (self.api.create_folder(d, pkey), f"{pname} / {d}")
            for fn in sorted(files):
                out.append((os.path.join(root, fn), pkey, pname, fn))
        return out

    # ============================================================== history
    def _log_history(self, job, folder_name, remote_name, res, action, same_link):
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": job["path"], "file": remote_name, "folder": folder_name,
            "folder_key": job["folder_key"], "quickkey": res["quickkey"],
            "link": share_link(res["quickkey"], remote_name), "version": job["note"],
            "action": action, "same_link": same_link,
            "size": res.get("size"), "sha256": res.get("sha256"),
        }
        self.ui_q.put(("call", self._add_history, entry))

    def _add_history(self, entry):
        self.history.append(entry)
        _save_json(HISTORY_PATH, self.history)
        self._refresh_history()

    def _refresh_history(self):
        t = self.hist_tree
        t.delete(*t.get_children())
        self._hover_row = None
        q = self.hist_search.get().strip().lower()
        labels = {"new": "New", "updated": "Updated", "unchanged": "Unchanged"}
        i = 0
        for idx in range(len(self.history) - 1, -1, -1):
            h = self.history[idx]
            if q and q not in " ".join(str(h.get(k, "")) for k in ("file", "version", "folder")).lower():
                continue
            name = h.get("file", "")
            t.insert("", "end", iid=str(idx), text="  " + name,
                     image=self.icons["zip" if name.lower().endswith(".zip") else "file"],
                     values=(nice_date(h.get("time", "")), h.get("version", "") or "—",
                             labels.get(h.get("action"), h.get("action", "")), human_size(h.get("size")),
                             (h.get("folder") or "").replace(" / ", " › ")),
                     tags=("odd",) if i % 2 else ())
            i += 1
        n = len(self.history)
        self.hist_sub.set(f"{n} upload{'s' * (n != 1)} logged  ·  version notes and links"
                          if n else "Every upload, with its version note and link")

    def _latest_versions(self):
        out = {}
        for h in self.history:
            if h.get("version"):
                out[h["quickkey"]] = h["version"]
        return out

    def _hist_entry(self):
        sel = self.hist_tree.focus()
        return self.history[int(sel)] if sel else None

    def _copy_hist_link(self):
        h = self._hist_entry()
        if h:
            self._copy_text(h["link"], "Link copied")

    def _open_hist_link(self):
        h = self._hist_entry()
        if h:
            webbrowser.open(h["link"])

    def _hist_menu(self, e):
        row = self.hist_tree.identify_row(e.y)
        if row:
            self.hist_tree.selection_set(row)
            self.hist_tree.focus(row)
            self.hmenu.tk_popup(e.x_root, e.y_root)

    # ============================================================== watches
    def _add_watch(self, folder):
        if not self.api.logged_in:
            self.toast("Sign in first", "warn")
            return
        path = filedialog.askdirectory(title="Folder to watch") if folder \
            else filedialog.askopenfilename(title="File to watch")
        if path:
            self._create_watch(path)

    def _create_watch(self, path, remote_name=None):
        path = os.path.normpath(path)
        w = {"id": uuid.uuid4().hex, "path": path, "folder_key": self._cur_key(),
             "folder_name": self._cur_path(), "remote_name": remote_name, "auto": False,
             "zip": self.zip_var.get(), "uploaded_sig": path_signature(path)}
        self.cfg["watches"].append(w)
        _save_json(CONFIG_PATH, self.cfg)
        self._refresh_watches()
        self.show_page("watched")
        if self.confirm("Watching " + os.path.basename(path),
                        "You'll be told when it changes. Upload the current version now too?",
                        "Upload now"):
            self._upload_watch(w)

    def _watch(self, wid):
        return next((w for w in self.cfg["watches"] if w["id"] == wid), None)

    def _refresh_watches(self):
        for c in self.watch_cards.values():
            c["card"].destroy()
        self.watch_cards = {}
        for i, w in enumerate(self.cfg["watches"]):
            self._watch_card(w, i + 1)
            st = self.watch_state.get(w["id"], {})
            self._set_watch_label(w["id"], st.get("label") or (
                "Missing" if not os.path.exists(w["path"]) else "Up to date"))
        if self.cfg["watches"]:
            self.watch_empty.grid_remove()
        else:
            self.watch_empty.grid(row=0, column=0, pady=120)
        self._update_watch_counts()

    def _set_watch_label(self, wid, label):
        self.watch_state.setdefault(wid, {})["label"] = label
        c = self.watch_cards.get(wid)
        if c:
            kind = ("warn" if label.startswith("Changed") and "failed" not in label else
                    "bad" if ("failed" in label or label == "Missing") else
                    "info" if label.startswith(("Uploading", "Changing")) else "ok")
            set_pill(c["pill"], label, kind)
            c["card"].configure(border_color="#4a3a17" if kind == "warn" else C["border"])
            c["up"].configure(**(dict(fg_color=C["accent"], hover_color=C["accent_h"], text_color=C["on_accent"],
                                      border_width=0) if kind == "warn" else
                                 dict(fg_color=C["card2"], hover_color=C["hover"], text_color=C["text"],
                                      border_width=1)))
        self._update_watch_counts()

    def _update_watch_counts(self):
        n = sum(1 for s in self.watch_state.values() if s.get("label", "").startswith("Changed"))
        self.nav["watched"].set_badge(n, "warn")
        self.upload_changed_btn.configure(text=f"Upload changed ({n})" if n else "Upload changed",
                                          state="normal" if n else "disabled")
        total = len(self.cfg["watches"])
        self.watch_sub.set(f"{total} watched  ·  {n} changed" if total else
                           "Get told when a mod changes on your PC")
        self.root.title(f"({n} changed) {APP_NAME}" if n else APP_NAME)

    def _watch_worker(self):
        while True:
            time.sleep(WATCH_INTERVAL)
            for w in list(self.cfg["watches"]):
                try:
                    sig = path_signature(w["path"])
                except Exception:
                    continue
                self.ui_q.put(("watch", "sig", w["id"], sig))

    def _on_watch_event(self, kind, wid, sig):
        w = self._watch(wid)
        if not w:
            return
        st = self.watch_state.setdefault(wid, {})
        if kind == "sig":
            if st.get("busy"):
                return
            if sig is None:
                if st.get("label") != "Missing":
                    self._set_watch_label(wid, "Missing")
                return
            if sig == w.get("uploaded_sig"):
                st["stable"] = 0
                if not str(st.get("label", "")).startswith("Up to date"):
                    self._set_watch_label(wid, "Up to date")
                return
            if sig == st.get("last_sig"):
                st["stable"] = st.get("stable", 0) + 1
            else:
                st["stable"] = 0
                if not st.get("label", "").startswith("Changed"):
                    self._set_watch_label(wid, "Changing…")
            st["last_sig"] = sig
            if st["stable"] >= WATCH_SETTLE and not st.get("label", "").startswith("Changed"):
                if w.get("auto"):
                    self._upload_watch(w)
                else:
                    self._set_watch_label(wid, "Changed")
                    self.toast(f"{os.path.basename(w['path'])} changed — ready to upload", "warn")
                    self.root.bell()
        elif kind == "uploaded":
            st["busy"] = False
            if sig is not None:
                w["uploaded_sig"] = sig
                _save_json(CONFIG_PATH, self.cfg)
            self._set_watch_label(wid, "Up to date · " + datetime.now().strftime("%I:%M %p").lstrip("0"))
        elif kind == "failed":
            st["busy"] = False
            self._set_watch_label(wid, "Changed · upload failed")

    def _upload_watch(self, w):
        if not w or not os.path.exists(w["path"]):
            return
        st = self.watch_state.setdefault(w["id"], {})
        st["busy"] = True
        self._set_watch_label(w["id"], "Uploading…")
        self._enqueue(w["path"], folder_key=w["folder_key"], folder_name=w["folder_name"],
                      remote_name=w.get("remote_name"), watch_id=w["id"],
                      sig=path_signature(w["path"]), zip_=w.get("zip", True))

    def _upload_changed(self):
        for w in self.cfg["watches"]:
            if self.watch_state.get(w["id"], {}).get("label", "").startswith("Changed"):
                self._upload_watch(w)

    def _set_auto(self, wid, value):
        w = self._watch(wid)
        if w:
            w["auto"] = value
            _save_json(CONFIG_PATH, self.cfg)
            self.toast(f"Auto-upload {'on' if value else 'off'} for {os.path.basename(w['path'])}", "info")

    def _remove_watch(self, wid):
        w = self._watch(wid)
        if w and self.confirm("Stop watching?", f"{os.path.basename(w['path'])} will no longer be checked "
                              "for changes. Nothing is deleted from MediaFire.", "Stop watching"):
            self.cfg["watches"] = [x for x in self.cfg["watches"] if x["id"] != wid]
            self.watch_state.pop(wid, None)
            _save_json(CONFIG_PATH, self.cfg)
            self._refresh_watches()

    # ============================================================== updates
    def _card(self, parent, row, pady=(0, 12)):
        c = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=14, border_width=1,
                         border_color=C["border"])
        c.grid(row=row, column=0, sticky="ew", pady=pady)
        c.grid_columnconfigure(1, weight=1)
        return c

    def _update_link(self):
        """Where updates come from: the GitHub repo if set, otherwise the MediaFire version.json link."""
        return (UPDATE_REPO or self.cfg.get("update_link") or UPDATE_LINK or "").strip()

    def _build_updates_page(self):
        pg = self._new_page("updates")
        self.upd_sub, act = self._header(pg, "Updates", f"You're on version {VERSION}")
        Btn(act, "Check now", lambda: self._check_updates(silent=False), "secondary").pack(side="left")

        st = self._card(pg, 1)
        self.upd_icon = ctk.CTkLabel(st, text="✓", width=46, height=46, corner_radius=23,
                                     fg_color=C["ok_bg"], text_color=C["ok"], font=F(18, "bold"))
        self.upd_icon.grid(row=0, column=0, rowspan=2, padx=(20, 14), pady=20)
        self.upd_title = ctk.CTkLabel(st, text="Not checked yet", font=F(16, "bold"),
                                      text_color=C["text"], anchor="w")
        self.upd_title.grid(row=0, column=1, sticky="sw")
        self.upd_detail = ctk.CTkLabel(st, text="", font=F(12), text_color=C["muted"], anchor="w",
                                       justify="left")
        self.upd_detail.grid(row=1, column=1, sticky="nw")
        self.upd_btn = Btn(st, "Update now", lambda: self.update_info and self._update_dialog(self.update_info),
                           "primary")
        self.auto_upd_var = tk.BooleanVar(value=self.cfg.get("auto_update_check", True))
        ctk.CTkSwitch(st, text="Check when the app opens", variable=self.auto_upd_var, onvalue=True,
                      offvalue=False, progress_color=C["accent"], fg_color=C["card2"], font=F(12),
                      text_color=C["muted"], command=self._save_upd_opts).grid(
            row=0, column=3, rowspan=2, padx=20)

        if UPDATE_REPO:
            self._build_github_cards(pg)
            self._set_upd_status("idle")
            return

        lk = self._card(pg, 2)
        ctk.CTkLabel(lk, text="Update link", font=F(14, "bold"), text_color=C["text"], anchor="w").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=20, pady=(16, 0))
        ctk.CTkLabel(lk, text="Where this app looks for new versions (the MediaFire link to version.json). "
                              "It fills in by itself after you publish.",
                     font=F(12), text_color=C["muted"], anchor="w").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=20)
        self.upd_link = ctk.CTkEntry(lk, height=36, corner_radius=9, fg_color=C["bg"], border_color=C["border"],
                                     font=F(12), placeholder_text="https://www.mediafire.com/file/…/version.json/file")
        self.upd_link.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(20, 8), pady=(10, 18))
        if self._update_link():
            self.upd_link.insert(0, self._update_link())
        Btn(lk, "Save", self._save_upd_link, "secondary", width=80).grid(row=2, column=2, padx=(0, 20),
                                                                         pady=(10, 18))

        pb = self._card(pg, 3)
        ctk.CTkLabel(pb, text="Publish a new version", font=F(14, "bold"), text_color=C["text"],
                     anchor="w").grid(row=0, column=0, sticky="w", padx=20, pady=(16, 0))
        steps = ("1.  Run build.bat and give it a higher version number",
                 "2.  Click Publish and pick the new exe from the dist folder",
                 "3.  Every copy of the app gets asked to update the next time it opens")
        for i, line in enumerate(steps):
            ctk.CTkLabel(pb, text=line, font=F(12), text_color=C["muted"], anchor="w",
                         fg_color=C["card"]).grid(row=1 + i, column=0, sticky="w", padx=20,
                                                  pady=(6 if i == 0 else 0, 18 if i == len(steps) - 1 else 0))
        Btn(pb, "Publish new version…", self._publish, "primary").grid(row=0, column=1, rowspan=4,
                                                                       sticky="e", padx=20)
        self._set_upd_status("idle")

    def _build_github_cards(self, pg):
        url = f"https://github.com/{UPDATE_REPO}"
        src = self._card(pg, 2)
        ctk.CTkLabel(src, text="GH", width=46, height=46, corner_radius=12, fg_color=C["card2"],
                     text_color=C["text"], font=F(14, "bold")).grid(row=0, column=0, rowspan=2, padx=(20, 14), pady=18)
        ctk.CTkLabel(src, text="Updates come from GitHub", font=F(14, "bold"), text_color=C["text"],
                     anchor="w").grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(src, text=f"github.com/{UPDATE_REPO}", font=F(12), text_color=C["muted"],
                     anchor="w").grid(row=1, column=1, sticky="nw")
        Btn(src, "View releases", lambda: webbrowser.open(url + "/releases"), "secondary").grid(
            row=0, column=2, rowspan=2, padx=(8, 8))
        Btn(src, "Code", lambda: webbrowser.open(url), "ghost", width=70).grid(
            row=0, column=3, rowspan=2, padx=(0, 20))

        how = self._card(pg, 3)
        ctk.CTkLabel(how, text="How a new version gets here", font=F(14, "bold"), text_color=C["text"],
                     anchor="w").grid(row=0, column=0, sticky="w", padx=20, pady=(16, 0))
        steps = ("1.  Changes are pushed to GitHub with a higher version number",
                 "2.  GitHub builds the exe by itself and posts it as a release",
                 "3.  Every copy of the app is asked to update the next time it opens")
        for i, line in enumerate(steps):          # one label per line (multi-line labels can fail to paint on Windows)
            ctk.CTkLabel(how, text=line, font=F(12), text_color=C["muted"], anchor="w",
                         fg_color=C["card"]).grid(row=1 + i, column=0, sticky="w", padx=20,
                                                  pady=(6 if i == 0 else 0, 18 if i == len(steps) - 1 else 0))

    def _save_upd_opts(self):
        self.cfg["auto_update_check"] = self.auto_upd_var.get()
        _save_json(CONFIG_PATH, self.cfg)

    def _save_upd_link(self):
        self.cfg["update_link"] = self.upd_link.get().strip()
        _save_json(CONFIG_PATH, self.cfg)
        self.toast("Update link saved", "ok")
        self._check_updates(silent=False)

    def _set_upd_status(self, kind, info=None, error=None):
        last = self.cfg.get("last_update_check")
        when = f"Last checked {nice_date(last)}" if last else "Not checked yet"
        self.upd_btn.grid_remove()
        self.nav["updates"].set_badge(0)
        if kind == "available":
            self.upd_icon.configure(text="⇪", fg_color=C["accent_soft"], text_color=C["accent"])
            self.upd_title.configure(text=f"Version {info['version']} is available")
            self.upd_detail.configure(text=f"You have {VERSION}  ·  {when}")
            self.upd_btn.grid(row=0, column=2, rowspan=2)
            self.nav["updates"].set_badge(1, "accent")
            self.upd_sub.set(f"You're on version {VERSION}  ·  update available")
        elif kind == "uptodate":
            self.upd_icon.configure(text="✓", fg_color=C["ok_bg"], text_color=C["ok"])
            self.upd_title.configure(text="You're up to date")
            self.upd_detail.configure(text=f"Version {VERSION} is the latest  ·  {when}")
            self.upd_sub.set(f"You're on version {VERSION}")
        elif kind == "checking":
            self.upd_icon.configure(text="…", fg_color=C["info_bg"], text_color=C["info"])
            self.upd_title.configure(text="Checking for updates…")
            self.upd_detail.configure(text=when)
        elif kind == "error":
            self.upd_icon.configure(text="!", fg_color=C["bad_bg"], text_color=C["bad"])
            self.upd_title.configure(text="Couldn't check for updates")
            self.upd_detail.configure(text=f"{error}\n{when}")
        else:
            no_link = not self._update_link()
            self.upd_icon.configure(text="i" if no_link else "✓", fg_color=C["card2"], text_color=C["muted"])
            self.upd_title.configure(text="No update link yet" if no_link else "Not checked yet")
            self.upd_detail.configure(text="Publish a version below, or paste the link to version.json"
                                      if no_link else when)

    def _check_updates(self, silent=True):
        link = self._update_link()
        if not link:
            self._set_upd_status("idle")
            if not silent:
                self.toast("No update link yet - publish a version first", "warn")
            return
        self._set_upd_status("checking")

        def done(info):
            self.cfg["last_update_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _save_json(CONFIG_PATH, self.cfg)
            if updater.is_newer(info["version"], VERSION):
                self.update_info = info
                self._set_upd_status("available", info)
                if not (silent and self.cfg.get("skip_version") == info["version"]):
                    self._update_dialog(info)
            else:
                self.update_info = None
                self._set_upd_status("uptodate")
                if not silent:
                    self.toast(f"You're on the latest version ({VERSION})", "ok")

        def fail(e):
            self._set_upd_status("error", error=str(e) if isinstance(e, updater.UpdateError)
                                 else "Couldn't reach MediaFire.")
            if not silent:
                self.toast("Couldn't check for updates", "bad")

        self._bg(lambda: updater.fetch_info(link), done, fail)

    def _dialog(self, title):
        d = ctk.CTkToplevel(self.root)
        d.title(title)
        d.configure(fg_color=C["card"])
        d.resizable(False, False)
        d.transient(self.root)
        f = ctk.CTkFrame(d, fg_color="transparent")
        f.pack(padx=28, pady=24, fill="both", expand=True)
        return d, f

    def _place_dialog(self, d):
        d.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - d.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - d.winfo_height()) // 3
        d.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        d.after(60, lambda: (d.lift(), d.focus_force()))

    def _update_dialog(self, info):
        if getattr(self, "_upd_dialog", None) is not None and self._upd_dialog.winfo_exists():
            self._upd_dialog.lift()
            return
        d, f = self._dialog("Update available")
        self._upd_dialog = d
        top = ctk.CTkFrame(f, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text="⇪", width=48, height=48, corner_radius=14, fg_color=C["accent"],
                     text_color=C["on_accent"], font=F(22, "bold")).pack(side="left", padx=(0, 14))
        tf = ctk.CTkFrame(top, fg_color="transparent")
        tf.pack(side="left", fill="x")
        ctk.CTkLabel(tf, text=f"Version {info['version']} is available", font=F(18, "bold"),
                     text_color=C["text"], anchor="w").pack(anchor="w")
        rel = ""
        if info.get("released"):
            day = nice_date(info["released"] + " 00:00:00")
            rel = "  ·  released " + (day.split(" ")[0].lower() if day.startswith(("Today", "Yesterday")) else day)
        ctk.CTkLabel(tf, text=f"You have {VERSION}{rel}", font=F(12), text_color=C["muted"],
                     anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text="WHAT'S NEW", font=F(10, "bold"), text_color=C["dim"]).pack(anchor="w", pady=(18, 4))
        notes = ctk.CTkTextbox(f, width=440, height=120, corner_radius=10, fg_color=C["bg"],
                               border_width=1, border_color=C["border"], font=F(12), text_color=C["text"],
                               wrap="word")
        notes.insert("1.0", (info.get("notes") or "No release notes.").strip())
        notes.configure(state="disabled")
        notes.pack(fill="x")
        prog_row = ctk.CTkFrame(f, fg_color="transparent")
        bar = ctk.CTkProgressBar(prog_row, height=6, corner_radius=3, progress_color=C["accent"],
                                 fg_color=C["card2"])
        bar.set(0)
        bar.pack(fill="x")
        msg = ctk.CTkLabel(prog_row, text="", font=F(12), text_color=C["muted"], anchor="w",
                           wraplength=440, justify="left")
        msg.pack(anchor="w", pady=(6, 0))
        btns = ctk.CTkFrame(f, fg_color="transparent")
        btns.pack(fill="x", pady=(18, 0))

        def close():
            self._upd_dialog = None
            d.destroy()

        def skip():
            self.cfg["skip_version"] = info["version"]
            _save_json(CONFIG_PATH, self.cfg)
            close()

        def go():
            if not updater.is_frozen():
                prog_row.pack(fill="x", pady=(16, 0), before=btns)
                bar.pack_forget()
                msg.configure(text="You're running the Python script, so it can't replace itself. "
                                   "Run build.bat to get the new version, or download the exe in your browser.",
                              text_color=C["warn"])
                upd.configure(text="Open download page",
                              command=lambda: webbrowser.open(info.get("page") or info["exe_link"]))
                return
            busy = any(s in ("queued", "active") for s in self.job_state.values())
            if busy and not self.confirm("Uploads still running",
                                         "Updating restarts the app and stops unfinished uploads. Continue?",
                                         "Update anyway", danger=True):
                return
            for b in (upd, later, skip_b):
                b.configure(state="disabled")
            prog_row.pack(fill="x", pady=(16, 0), before=btns)
            msg.configure(text="Downloading…", text_color=C["muted"])

            def prog(done_, total):
                frac = done_ / total if total else 0
                self.ui_q.put(("call", lambda _: (bar.set(frac), msg.configure(
                    text=f"Downloading…  {human_size(done_)}" + (f" of {human_size(total)}" if total else ""))),
                    None))

            def ok(path):
                msg.configure(text="Installing - the app will restart in a moment…", text_color=C["ok"])
                bar.configure(progress_color=C["ok"])
                bar.set(1)
                try:
                    updater.install_and_restart(path)
                except Exception as e:
                    return fail(e)
                self.root.after(600, self._quit_for_update)

            def fail(e):
                msg.configure(text=f"Update failed: {e}", text_color=C["bad"])
                bar.configure(progress_color=C["bad"])
                for b in (upd, later, skip_b):
                    b.configure(state="normal")

            self._bg(lambda: updater.download_exe(info, progress=prog), ok, fail)

        upd = Btn(btns, "Update now", go, "primary")
        upd.pack(side="right")
        later = Btn(btns, "Later", close, "secondary")
        later.pack(side="right", padx=8)
        skip_b = Btn(btns, "Skip this version", skip, "ghost")
        skip_b.pack(side="left")
        d.protocol("WM_DELETE_WINDOW", close)
        d.bind("<Escape>", lambda e: close())
        self._place_dialog(d)

    def _quit_for_update(self):
        try:
            self.root.destroy()
        finally:
            os._exit(0)

    # ------------------------------------------------------------ publishing
    def _publish(self):
        if not self.api.logged_in:
            self.toast("Sign in first", "warn")
            return
        start = os.path.dirname(sys.executable) if updater.is_frozen() else \
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")
        exe = filedialog.askopenfilename(title="Pick the new MediaFireModManager.exe",
                                         initialdir=start if os.path.isdir(start) else None,
                                         filetypes=[("Programs", "*.exe")])
        if not exe:
            return
        d, f = self._dialog("Publish a new version")
        ctk.CTkLabel(f, text="Publish a new version", font=F(18, "bold"), text_color=C["text"]).pack(anchor="w")
        status = ctk.CTkLabel(f, text="Reading the version from the exe…", font=F(12), text_color=C["muted"],
                              anchor="w", justify="left", wraplength=440)
        status.pack(anchor="w", pady=(4, 14))
        ctk.CTkLabel(f, text="WHAT'S NEW  (shown to people when they update)", font=F(10, "bold"),
                     text_color=C["dim"]).pack(anchor="w", pady=(0, 4))
        notes = ctk.CTkTextbox(f, width=440, height=110, corner_radius=10, fg_color=C["bg"], border_width=1,
                               border_color=C["border"], font=F(12), text_color=C["text"], wrap="word")
        notes.pack(fill="x")
        bar = ctk.CTkProgressBar(f, height=6, corner_radius=3, progress_color=C["accent"], fg_color=C["card2"])
        bar.set(0)
        btns = ctk.CTkFrame(f, fg_color="transparent")
        btns.pack(fill="x", pady=(18, 0))
        pub = Btn(btns, "Publish", None, "primary", state="disabled")
        pub.pack(side="right")
        cancel = Btn(btns, "Cancel", d.destroy, "secondary")
        cancel.pack(side="right", padx=8)
        self._place_dialog(d)
        state = {}

        def inspect():
            ver = updater.read_exe_version(exe)
            if not ver:
                raise updater.UpdateError("That exe didn't report a version. Rebuild it with the new build.bat.")
            published = None
            if self._update_link():
                try:
                    published = updater.fetch_info(self._update_link())["version"]
                except Exception:
                    published = None
            return ver, published

        def inspected(res):
            ver, published = res
            state["ver"] = ver
            if published and not updater.is_newer(ver, published):
                status.configure(text=f"This exe is version {ver}, but {published} is already published. "
                                      "Run build.bat again with a higher version number.", text_color=C["bad"])
                return
            pre = f"  (currently published: {published})" if published else "  (first release)"
            status.configure(text=f"Version {ver}{pre}", text_color=C["text"])
            pub.configure(state="normal", command=do_publish)
            notes.focus_set()

        def inspect_fail(e):
            status.configure(text=f"Couldn't read the version: {e}", text_color=C["bad"])

        def do_publish():
            text = notes.get("1.0", "end").strip()
            pub.configure(state="disabled", text="Publishing…")
            cancel.configure(state="disabled")
            notes.configure(state="disabled")
            bar.pack(fill="x", pady=(14, 0), before=btns)

            def say(t, frac=None):
                self.ui_q.put(("call", lambda _: (status.configure(text=t, text_color=C["muted"]),
                                                   frac is not None and bar.set(frac)), None))

            def work():
                api = self.api
                say("Finding the Mod Manager Updates folder…", 0.02)
                fk = api.find_folder(None, "Mod Manager Updates") or api.create_folder("Mod Manager Updates", None)
                ex = api.find_file(fk, "MediaFireModManager.exe")
                res = api.upload(exe, folder_key=fk, remote_name="MediaFireModManager.exe",
                                 replace_quickkey=ex["quickkey"] if ex else None,
                                 progress=lambda a, b: say(f"Uploading the exe…  {a * 100 // max(b, 1)}%",
                                                           0.05 + 0.8 * a / max(b, 1)))
                exe_link = share_link(res["quickkey"], "MediaFireModManager.exe")
                info = updater.make_info(state["ver"], exe_link, exe, text, datetime.now().strftime("%Y-%m-%d"))
                say("Uploading version.json…", 0.9)
                tmp = tempfile.mkdtemp(prefix="mfmm_pub_")
                jp = os.path.join(tmp, "version.json")
                with open(jp, "w", encoding="utf-8") as fh:
                    json.dump(info, fh, indent=2)
                ej = api.find_file(fk, "version.json")
                res2 = api.upload(jp, folder_key=fk, remote_name="version.json",
                                  replace_quickkey=ej["quickkey"] if ej else None)
                shutil.rmtree(tmp, ignore_errors=True)
                for qk in (res["quickkey"], res2["quickkey"]):
                    try:
                        api.set_public(qk)
                    except Exception:
                        pass
                return share_link(res2["quickkey"], "version.json"), info

            def done(res):
                link, info = res
                self.cfg["update_link"] = link
                _save_json(CONFIG_PATH, self.cfg)
                if hasattr(self, "upd_link"):
                    self.upd_link.delete(0, "end")
                    self.upd_link.insert(0, link)
                bar.configure(progress_color=C["ok"])
                bar.set(1)
                status.configure(text=f"Version {info['version']} is published. Every copy of the app will be "
                                      "asked to update the next time it opens.", text_color=C["ok"])
                self.toast(f"Published version {info['version']}", "ok")
                for w in btns.winfo_children():
                    w.destroy()
                Btn(btns, "Done", d.destroy, "secondary").pack(side="right")
                if updater.is_frozen() and updater.is_newer(info["version"], VERSION):
                    def restart_now():
                        new = os.path.join(tempfile.gettempdir(), "MediaFireModManager_update",
                                           "MediaFireModManager_new.exe")
                        os.makedirs(os.path.dirname(new), exist_ok=True)
                        shutil.copy2(exe, new)
                        updater.install_and_restart(new)
                        self.root.after(400, self._quit_for_update)
                    Btn(btns, f"Update this app to {info['version']}", restart_now, "primary").pack(
                        side="right", padx=8)
                if updater.is_newer(info["version"], VERSION):
                    self.update_info = info
                    self._set_upd_status("available", info)
                else:
                    self._set_upd_status("uptodate")

            def fail(e):
                msg = e.message if isinstance(e, MFError) else str(e)
                status.configure(text=f"Publishing failed: {msg}", text_color=C["bad"])
                bar.configure(progress_color=C["bad"])
                pub.configure(state="normal", text="Try again")
                cancel.configure(state="normal")
                notes.configure(state="normal")

            self._bg(work, done, fail)

        self._bg(inspect, inspected, inspect_fail)

    # ================================================================ misc
    @staticmethod
    def _open_path(p):
        if os.path.exists(p):
            if hasattr(os, "startfile"):
                os.startfile(p)
            else:
                webbrowser.open("file://" + p)

    def _on_close(self):
        busy = any(s in ("queued", "active") for s in self.job_state.values())
        if busy and not self.confirm("Uploads still running", "Quit anyway? Unfinished uploads will stop.",
                                     "Quit", danger=True):
            return
        self.root.destroy()


def main():
    if "--write-version" in sys.argv:          # used by "Publish new version" to read an exe's version
        i = sys.argv.index("--write-version")
        with open(sys.argv[i + 1], "w", encoding="utf-8") as f:
            f.write(VERSION)
        return
    root = make_root()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
