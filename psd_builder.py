"""
PSD Builder - turns a Substance Painter export into the Chain + Pendant PSD.

Painter (with the "MXB" preset) exports, per texture set X:
    <mesh>_X.1001.psd   one PSD holding layers  X, X_ao, X_2D, X_mask, X_curv
    X_n.tga  X_s.tga  X_r.tga

The app writes this PSD itself (no Photoshop needed), top to bottom:
    Hide me                      (locked "thank you" overlay)
    Pendant / Chain
        rough  -> X_r
        spec   -> X_s
        Norm   -> Bikes (green flipped), X_n
        Base   -> Shading (X_ao, X_2D, X_curv, X_mask - per the shading recipe), X
Colour fills are left for you to add.

Exporting game textures from a finished PSD (after your masks and colours) still uses Photoshop,
because only Photoshop can blend your edits exactly.
"""
import copy
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from PIL import Image, ImageChops

import psd_writer

# ------------------------------------------------------------ the recipe
# Shading layers, top to bottom, per part. Editable in the app (saved in the config).
MAPS = ("ao", "2D", "curv", "mask")
DEFAULT_SHADING = {
    "Chain": [
        {"map": "ao", "blend": "Multiply", "opacity": 50, "on": True},
        {"map": "2D", "blend": "Overlay", "opacity": 60, "on": True},
        {"map": "curv", "blend": "Overlay", "opacity": 25, "on": False},
        {"map": "mask", "blend": "Multiply", "opacity": 100, "on": False},
    ],
    "Pendant": [
        {"map": "ao", "blend": "Multiply", "opacity": 70, "on": True},
        {"map": "2D", "blend": "Overlay", "opacity": 60, "on": True},
        {"map": "curv", "blend": "Overlay", "opacity": 25, "on": True},
        {"map": "mask", "blend": "Multiply", "opacity": 100, "on": True},
    ],
}
FLIPPED_NORMAL_NAME = "Bikes"
HIDE_ME_NAME = "Hide me"
SLOTS = ("Chain", "Pendant")          # Pendant group ends up on top
NONE = "— none —"


def shading_recipe(saved=None):
    """Defaults merged with what the user saved; always every map, in MAPS order."""
    out = copy.deepcopy(DEFAULT_SHADING)
    for slot in SLOTS:
        got = {r.get("map"): r for r in (saved or {}).get(slot, []) if isinstance(r, dict)}
        for row in out[slot]:
            r = got.get(row["map"])
            if r:
                if r.get("blend") in psd_writer.BLEND_NAMES:
                    row["blend"] = r["blend"]
                try:
                    row["opacity"] = max(0, min(100, int(r.get("opacity", row["opacity"]))))
                except (TypeError, ValueError):
                    pass
                row["on"] = bool(r.get("on", row["on"]))
    return out

UDIM_RE = re.compile(r"\.\d{4}$")     # the ".1001" Painter adds


# --------------------------------------------------------- finding sets
def find_sets(folder):
    """Look in a Painter export folder and return the texture sets found, sorted by name.

    Each set: {"name", "psd", "n", "s", "r", "missing": [..]}
    The set name comes from the tga names (X_n.tga); the PSD is matched by "<anything>_X.1001.psd".
    """
    if not folder or not os.path.isdir(folder):
        return []
    files = os.listdir(folder)
    tgas = {f.lower(): f for f in files if f.lower().endswith(".tga")}
    psds = [f for f in files if f.lower().endswith(".psd") and UDIM_RE.search(os.path.splitext(f)[0])]

    names = []
    for low, real in tgas.items():
        m = re.match(r"(.+)_([nsr])\.tga$", real, re.I)
        if m and m.group(1) not in names:
            names.append(m.group(1))

    sets, used_psds = {}, set()
    for name in names:
        psd = None
        for f in psds:
            stem = UDIM_RE.sub("", os.path.splitext(f)[0])
            if stem == name or stem.endswith("_" + name):
                psd = f
                break
        if psd:
            used_psds.add(psd)
        sets[name] = {"name": name, "psd": psd}

    # PSDs with no matching tgas still count (name = text after the mesh name)
    for f in psds:
        if f in used_psds:
            continue
        stem = UDIM_RE.sub("", os.path.splitext(f)[0])
        name = stem.split("_", 1)[1] if "_" in stem else stem
        sets.setdefault(name, {"name": name, "psd": f})

    out = []
    for name, s in sets.items():
        item = {"name": name, "psd": os.path.join(folder, s["psd"]) if s["psd"] else None}
        missing = [] if s["psd"] else ["PSD"]
        for k in ("n", "s", "r"):
            real = tgas.get(f"{name}_{k}.tga".lower())
            item[k] = os.path.join(folder, real) if real else None
            if not real:
                missing.append(f"_{k}")
        item["missing"] = missing
        out.append(item)
    out.sort(key=lambda s: s["name"].lower())
    return out


def guess_slots(sets):
    """Best guess for which set is the Chain and which is the Pendant."""
    names = [s["name"] for s in sets if s["psd"]]
    pend_words = ("pendant", "plate", "pend", "piece", "medal", "charm")
    pendant = next((n for n in names if any(w in n.lower() for w in pend_words)), None)
    chain = next((n for n in names if n != pendant), None)
    if pendant is None and len(names) > 1:
        pendant = next((n for n in names if n != chain), None)
    return {"Chain": chain or NONE, "Pendant": pendant or NONE}


# --------------------------------------------------------- Photoshop
def _no_window():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def running_photoshop():
    """Path of the Photoshop that is open right now (Windows), or None."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process Photoshop -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path"],
            capture_output=True, text=True, timeout=8, creationflags=_no_window()).stdout
        paths = [p.strip() for p in out.splitlines() if p.strip().lower().endswith(".exe")]
        return paths[0] if paths else None
    except Exception:
        return None


def installed_photoshops():
    """All Photoshop.exe found under Program Files, newest first."""
    found = []
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"):
        for p in glob.glob(os.path.join(base, "Adobe", "Adobe Photoshop*", "Photoshop.exe")):
            if p not in found:
                found.append(p)

    def ver(p):
        nums = re.findall(r"\d+", os.path.basename(os.path.dirname(p)))
        return [int(n) for n in nums] or [0]
    return sorted(found, key=ver, reverse=True)


def pick_photoshop(preferred=None):
    """Use the open Photoshop if there is one, else the saved choice, else the newest installed."""
    for p in (running_photoshop(), preferred):
        if p and os.path.isfile(p):
            return p
    allp = installed_photoshops()
    return allp[0] if allp else None


# --------------------------------------------------------- script
def _js_path(p):
    return p.replace("\\", "/") if p else None


JSX_LIB = r"""
var warnings = [];
function q(s) {
    s = String(s);
    return '"' + s.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r/g, '').replace(/\n/g, '\\n') + '"';
}
function log(s) {
    try { var f = new File(CFG.log); f.encoding = "UTF-8"; f.open("a"); f.writeln(s); f.close(); } catch (e) {}
}
function warn(s) { warnings.push(s); log("! " + s); }
function finish(ok, msg, extra) {
    var w = "";
    for (var i = 0; i < warnings.length; i++) { w += (i ? "," : "") + q(warnings[i]); }
    var f = new File(CFG.result); f.encoding = "UTF-8"; f.open("w");
    f.write('{"ok":' + (ok ? 'true' : 'false') + ',"message":' + q(msg) + ',"warnings":[' + w + ']' + (extra || '') + '}');
    f.close();
}
// find a group inside a group by name, ignoring upper/lower case
function subSet(parent, name) {
    for (var i = 0; i < parent.layerSets.length; i++)
        if (parent.layerSets[i].name.replace(/\s+$/, "").toLowerCase() == name.toLowerCase()) return parent.layerSets[i];
    return null;
}
// ---------------------------------------------------------- export
function exportVisible(doc, file) {
    var d = doc.duplicate("tw_export", true);
    d.flatten();
    for (var i = d.channels.length - 1; i >= 0; i--)
        if (d.channels[i].kind != ChannelType.COMPONENT) d.channels[i].remove();
    if (d.bitsPerChannel != BitsPerChannelType.EIGHT) d.bitsPerChannel = BitsPerChannelType.EIGHT;
    var o = new TargaSaveOptions();
    o.resolution = TargaBitsPerPixels.TWENTYFOUR; o.alphaChannels = false; o.rleCompression = false;
    d.saveAs(file, o, true, Extension.LOWERCASE);
    d.close(SaveOptions.DONOTSAVECHANGES);
    app.activeDocument = doc;
}
function setNameOf(part) {
    try {
        var base = subSet(part, "Base");
        if (base && base.artLayers.length) return base.artLayers[base.artLayers.length - 1].name;
    } catch (e) {}
    return part.name;
}
// export every Chain/Pendant group as game tgas: X.tga (Base) and X_n.tga (Norm).
// X_s / X_r are copied from the original Painter tgas (keeps their alpha) or exported if missing.
function exportGame(doc, outDir, srcDir) {
    var out = new Folder(outDir);
    if (!out.exists) out.create();
    var top = [], i, j, made = [];
    for (i = 0; i < doc.layers.length; i++) top.push([doc.layers[i], doc.layers[i].visible]);
    var maps = [["Base", ""], ["Norm", "_n"], ["Spec", "_s"], ["Rough", "_r"]];
    for (i = 0; i < doc.layerSets.length; i++) {
        var part = doc.layerSets[i];
        var subs = [];
        for (j = 0; j < maps.length; j++) {
            var sub = subSet(part, maps[j][0]);
            if (sub) subs.push([maps[j], sub]);
        }
        if (!subs.length) continue;
        var X = setNameOf(part);
        log("Exporting " + part.name + " (" + X + ")");
        for (j = 0; j < top.length; j++) top[j][0].visible = (top[j][0] == part);
        var subVis = [];
        for (j = 0; j < subs.length; j++) subVis.push(subs[j][1].visible);
        for (j = 0; j < subs.length; j++) {
            var suffix = subs[j][0][1], target = new File(outDir + "/" + X + suffix + ".tga");
            if ((suffix == "_s" || suffix == "_r") && srcDir) {
                var orig = new File(srcDir + "/" + X + suffix + ".tga");
                if (orig.exists && orig.fsName != target.fsName) {
                    if (target.exists) target.remove();
                    orig.copy(target); made.push(target.name); continue;
                }
                if (orig.exists) { made.push(target.name); continue; }
                warn("No original " + X + suffix + ".tga - exported it from the PSD without alpha");
            }
            for (var k = 0; k < subs.length; k++) subs[k][1].visible = (k == j);
            exportVisible(doc, target);
            made.push(target.name);
        }
        for (j = 0; j < subs.length; j++) subs[j][1].visible = subVis[j];
    }
    for (j = 0; j < top.length; j++) top[j][0].visible = top[j][1];
    return made;
}
"""

JSX_EXPORT = r"""
(function () {
    var oldUnits = app.preferences.rulerUnits, oldDlg = app.displayDialogs;
    try {
        app.preferences.rulerUnits = Units.PIXELS;
        app.displayDialogs = DialogModes.NO;
        var f = new File(CFG.psd), doc = null;
        for (var i = 0; i < app.documents.length; i++) {
            try { if (app.documents[i].fullName.fsName == f.fsName) doc = app.documents[i]; }
            catch (e) {}          // unsaved documents have no file yet
        }
        var wasOpen = !!doc;
        if (!doc) { log("Opening " + CFG.psd); doc = open(f); }
        app.activeDocument = doc;
        var made = exportGame(doc, CFG.export_dir, CFG.tga_src_dir);
        if (!wasOpen) doc.close(SaveOptions.DONOTSAVECHANGES);
        var m = ""; for (i = 0; i < made.length; i++) m += (i ? "," : "") + q(made[i]);
        if (!made.length) finish(false, "No Chain/Pendant groups with Base/Norm/Spec/Rough found in that PSD");
        else finish(true, "Exported " + made.length + " files", ',"exported":[' + m + ']');
    } catch (e) {
        finish(false, e.message + " (script line " + e.line + ")");
    } finally {
        app.preferences.rulerUnits = oldUnits; app.displayDialogs = oldDlg;
    }
})();
"""


def _header(cfg):
    return "#target photoshop\n// Made by Terps WorkFlow - PSD Builder\nvar CFG = " + json.dumps(cfg) + ";\n"


def export_script_cfg(psd_path):
    folder = os.path.dirname(psd_path)
    return {"psd": _js_path(psd_path), "export_dir": _js_path(os.path.join(folder, "Game textures")),
            "tga_src_dir": _js_path(folder)}


def make_jsx(cfg, kind="export"):
    return _header(cfg) + JSX_LIB + JSX_EXPORT


# --------------------------------------------------------- running it
class Job:
    """Runs a script in Photoshop and reports back. on_progress(text) and on_done(result) are called
    from a background thread - wrap them for the UI thread yourself."""

    def __init__(self, cfg, kind, preferred_photoshop, on_progress, on_done, timeout=20 * 60):
        self.cfg, self.kind, self.preferred = dict(cfg), kind, preferred_photoshop
        self.photoshop = None
        self.on_progress, self.on_done, self.timeout = on_progress, on_done, timeout
        self.work = os.path.join(tempfile.gettempdir(), "TerpsWorkFlow", "psd_" + uuid.uuid4().hex[:8])
        os.makedirs(self.work, exist_ok=True)
        self.cfg["log"] = _js_path(os.path.join(self.work, "log.txt"))
        self.cfg["result"] = _js_path(os.path.join(self.work, "result.json"))
        self.jsx = os.path.join(self.work, "terps_psd_%s.jsx" % kind)
        with open(self.jsx, "w", encoding="utf-8") as f:
            f.write(make_jsx(self.cfg, kind))

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        result_path = self.cfg["result"]
        log_path = self.cfg["log"]
        try:
            self.on_progress("Finding Photoshop…")
            self.photoshop = pick_photoshop(self.preferred)
            if not self.photoshop or not os.path.isfile(self.photoshop):
                raise RuntimeError("Photoshop wasn't found. Pick Photoshop.exe in the PSD Builder settings.")
            self.on_progress("Sending it to " + os.path.basename(os.path.dirname(self.photoshop)) + "…")
            subprocess.Popen([self.photoshop, self.jsx], creationflags=_no_window())
            start, seen = time.time(), 0
            while not os.path.exists(result_path):
                if time.time() - start > self.timeout:
                    raise RuntimeError("Photoshop didn't finish in time. Check Photoshop for a message, or run "
                                       "the script yourself: File > Scripts > Browse… > " + self.jsx)
                try:
                    with open(log_path, encoding="utf-8") as f:
                        lines = f.read().splitlines()
                    if len(lines) > seen:
                        seen = len(lines)
                        self.on_progress(lines[-1].lstrip("! "))
                except OSError:
                    if time.time() - start > 25 and seen == 0:
                        self.on_progress("Waiting for Photoshop… If it asks to run the script, click Run. "
                                         "Nothing happening? In Photoshop: File > Scripts > Browse… > " + self.jsx)
                time.sleep(0.4)
            time.sleep(0.2)
            with open(result_path, encoding="utf-8") as f:
                res = json.load(f)
        except Exception as e:
            res = {"ok": False, "message": str(e), "warnings": []}
        if res.get("ok"):          # keep the script around after a problem so it can be run by hand
            shutil.rmtree(self.work, ignore_errors=True)
        self.on_done(res)


# --------------------------------------------------------- building (no Photoshop)
def _painter_layers(psd_path):
    """{layer name: PIL image} for every layer in a Painter export PSD."""
    im = Image.open(psd_path)
    out = {}
    for i, info in enumerate(im.layers):
        im.seek(i + 1)
        out[info[0]] = im.copy()
    size = im.size
    im.close()
    return out, size


def _find(layers, name, suffix=None):
    if name in layers:
        return layers[name]
    low = {k.lower(): v for k, v in layers.items()}
    if name.lower() in low:
        return low[name.lower()]
    if suffix:
        for k, v in layers.items():
            if k.lower().endswith(suffix.lower()) and len(k) > len(suffix):
                return v
    return None


def _flip_green(img):
    r, g, b = img.convert("RGB").split()
    return Image.merge("RGB", (r, g.point(lambda v: 255 - v), b))


def _blend(base, top, mode, opacity):
    """Approximate Photoshop blend of an opaque layer over base (both RGB)."""
    mode = (mode or "normal").lower().replace(" ", "")
    fn = {"multiply": ImageChops.multiply, "overlay": ImageChops.overlay, "softlight": ImageChops.soft_light,
          "hardlight": ImageChops.hard_light, "screen": ImageChops.screen, "darken": ImageChops.darker,
          "lighten": ImageChops.lighter}.get(mode)
    mixed = fn(base, top) if fn else top
    return Image.blend(base, mixed, max(0, min(100, opacity)) / 100.0)


def build_psd(folder, chain_set, pendant_set, out_psd, shading=None, export_dir=None,
              hide_me=None, progress=lambda t: None):
    """Write the PSD. Returns {"ok", "message", "warnings", "exported"}."""
    warnings, exported = [], []
    recipe = shading_recipe(shading)
    parts = [(slot, s) for slot, s in (("Pendant", pendant_set), ("Chain", chain_set)) if s]  # top first
    if not parts:
        raise ValueError("Pick a texture set for the Chain or the Pendant")
    for slot, s in parts:
        if not s.get("psd"):
            raise ValueError(f"No Painter PSD found for '{s['name']}'")

    doc = None
    game = {}
    for slot, s in parts:
        X = s["name"]
        progress(f"Reading {X} ({slot})…")
        layers, size = _painter_layers(s["psd"])
        if doc is None:
            doc = psd_writer.Doc(*size)
            if hide_me and os.path.isfile(hide_me):
                img = Image.open(hide_me).convert("RGBA")
                if img.size != size:
                    img = img.resize(size)
                doc.layer(HIDE_ME_NAME, img, locked=True)
        elif size != (doc.width, doc.height):
            warnings.append(f"{X} is {size[0]}x{size[1]}, not {doc.width}x{doc.height}")

        g = doc.group(slot)
        rough, spec, norm, base = g.group("rough"), g.group("spec"), g.group("Norm"), g.group("Base")

        def tga(key, label):
            p = s.get(key)
            if not p:
                warnings.append(f"{X}_{key}.tga not found - {label} group is empty")
                return None
            return Image.open(p)

        img = tga("r", "rough")
        if img:
            rough.layer(f"{X}_r", img.convert("RGB"))
        img = tga("s", "spec")
        if img:
            spec.layer(f"{X}_s", img.convert("RGB"))
        img = tga("n", "Norm")
        flipped = None
        if img:
            flipped = _flip_green(img)
            norm.layer(FLIPPED_NORMAL_NAME, flipped)
            norm.layer(f"{X}_n", img.convert("RGB"))

        shade = base.group("Shading")
        stack = []
        for row in recipe[slot]:
            if not row["on"]:
                continue
            L = _find(layers, f"{X}_{row['map']}", "_" + row["map"])
            if L is None:
                warnings.append(f"Layer {X}_{row['map']} not in {os.path.basename(s['psd'])}")
                continue
            shade.layer(f"{X}_{row['map']}", L, blend=row["blend"], opacity=row["opacity"])
            stack.append((L, row))
        art = _find(layers, X)
        if art is None:     # fall back to the bottom layer of the Painter PSD
            art = list(layers.values())[-1] if layers else None
            if art is not None:
                warnings.append(f"No layer called '{X}' - used the bottom layer as the art")
        if art is not None:
            base.layer(X, art)
        game[X] = (art, stack, flipped, s)

    progress("Saving " + os.path.basename(out_psd) + "…")
    tmp = out_psd + ".part"
    doc.save(tmp)
    os.replace(tmp, out_psd)

    if export_dir:
        os.makedirs(export_dir, exist_ok=True)
        for X, (art, stack, flipped, s) in game.items():
            progress(f"Writing game textures for {X}…")
            if art is not None:
                out = art.convert("RGB")
                for L, row in reversed(stack):        # bottom layer first
                    out = _blend(out, L.convert("RGB"), row["blend"], row["opacity"])
                out.save(os.path.join(export_dir, X + ".tga"))
                exported.append(X + ".tga")
            if flipped is not None:
                flipped.save(os.path.join(export_dir, X + "_n.tga"))
                exported.append(X + "_n.tga")
            for key in ("s", "r"):
                if s.get(key):
                    shutil.copy2(s[key], os.path.join(export_dir, f"{X}_{key}.tga"))
                    exported.append(f"{X}_{key}.tga")
    return {"ok": True, "message": "Built " + out_psd, "warnings": warnings, "exported": exported}


class BuildJob:
    """Runs build_psd on a background thread. Callbacks run on that thread - hand them to the UI yourself."""

    def __init__(self, on_progress, on_done, **kw):
        self.kw, self.on_progress, self.on_done = kw, on_progress, on_done

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        try:
            res = build_psd(progress=self.on_progress, **self.kw)
        except Exception as e:
            msg = str(e)
            if isinstance(e, PermissionError):
                msg = "Couldn't save - is that PSD open in Photoshop? Close it and try again."
            res = {"ok": False, "message": msg, "warnings": []}
        self.on_done(res)
