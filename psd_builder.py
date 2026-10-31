"""
PSD Builder - turns a Substance Painter export into the Chain + Pendant PSD.

Painter (with the "MXB" preset) exports, per texture set X:
    <mesh>_X.1001.psd   one PSD holding layers  X, X_ao, X_2D, X_mask, X_curv
    X_n.tga  X_s.tga  X_r.tga

The builder makes this layout (top to bottom), just like Base.psd:
    Pendant / Chain
        Rough   -> X_r            (hidden)
        Spec    -> X_s            (hidden)
        Norm    -> Inverted for Bikes (green flipped), X_n   (hidden)
        Base    -> X_mask (hidden helper), Shading (X_ao, X_2D, X_curv), 2 colour fills, X

Photoshop does the work: the app writes a small script and hands it to Photoshop.
"""
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

# ------------------------------------------------------------ the recipe
# Blend mode + opacity for each Painter map. Change these to change every new PSD.
SHADING = {
    "ao":   {"blend": "MULTIPLY",  "opacity": 70},
    "2D":   {"blend": "SOFTLIGHT", "opacity": 60},
    "curv": {"blend": "OVERLAY",   "opacity": {"Chain": 20, "Pendant": 39}},
}
# Colour fills added in each part's Base group (bottom one first). White = no change on Multiply.
FILLS = {
    "Chain":   [("Clasp color", (255, 255, 255)), ("Chain color", (255, 255, 255))],
    "Pendant": [("Plate color", (255, 255, 255)), ("Pendant color", (255, 255, 255))],
}
FLIPPED_NORMAL_NAME = "Inverted for Bikes"
SLOTS = ("Chain", "Pendant")          # top-level groups (Pendant ends up on top)
NONE = "— none —"

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
function blendOf(n) {
    return { MULTIPLY: BlendMode.MULTIPLY, SOFTLIGHT: BlendMode.SOFTLIGHT, OVERLAY: BlendMode.OVERLAY,
             SCREEN: BlendMode.SCREEN, NORMAL: BlendMode.NORMAL }[n] || BlendMode.NORMAL;
}
function findLayer(doc, name, suffix) {
    var L = doc.artLayers, i;
    for (i = 0; i < L.length; i++) if (L[i].name == name) return L[i];
    for (i = 0; i < L.length; i++) if (L[i].name.toLowerCase() == name.toLowerCase()) return L[i];
    if (suffix) for (i = 0; i < L.length; i++) {
        var n = L[i].name.toLowerCase();
        if (n.length > suffix.length && n.substr(n.length - suffix.length) == suffix.toLowerCase()) return L[i];
    }
    return null;
}
// copy a layer from the Painter PSD into a group of the new doc (lands on top of that group)
function copyLayer(src, doc, set, name, suffix) {
    app.activeDocument = src;
    var L = findLayer(src, name, suffix);
    if (!L) { warn("Layer '" + name + "' not found in " + src.name); return null; }
    var d = L.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
    app.activeDocument = doc;
    d.move(set, ElementPlacement.INSIDE);
    d.name = name;
    d.blendMode = BlendMode.NORMAL; d.opacity = 100; d.visible = true;
    return d;
}
function makeFill(name, rgb) {
    var d = new ActionDescriptor(), ref = new ActionReference();
    ref.putClass(stringIDToTypeID("contentLayer"));
    d.putReference(charIDToTypeID("null"), ref);
    var d2 = new ActionDescriptor(), d3 = new ActionDescriptor(), c = new ActionDescriptor();
    c.putDouble(charIDToTypeID("Rd  "), rgb[0]); c.putDouble(charIDToTypeID("Grn "), rgb[1]);
    c.putDouble(charIDToTypeID("Bl  "), rgb[2]);
    d3.putObject(charIDToTypeID("Clr "), charIDToTypeID("RGBC"), c);
    d2.putObject(charIDToTypeID("Type"), stringIDToTypeID("solidColorLayer"), d3);
    d.putObject(charIDToTypeID("Usng"), stringIDToTypeID("contentLayer"), d2);
    executeAction(charIDToTypeID("Mk  "), d, DialogModes.NO);
    var L = app.activeDocument.activeLayer;
    L.name = name; L.blendMode = BlendMode.MULTIPLY;
    return L;
}
// place a file as an embedded smart object inside a group
function placeInto(doc, set, path, name) {
    var f = new File(path);
    if (!f.exists) { warn("Missing file: " + path); return null; }
    app.activeDocument = doc;
    var d = new ActionDescriptor();
    d.putPath(charIDToTypeID("null"), f);
    d.putEnumerated(charIDToTypeID("FTcs"), charIDToTypeID("QCSt"), charIDToTypeID("Qcsa"));
    executeAction(charIDToTypeID("Plc "), d, DialogModes.NO);
    var L = doc.activeLayer;
    L.move(set, ElementPlacement.INSIDE);
    L.name = name;
    return L;
}
function toSmartObject() { executeAction(stringIDToTypeID("newPlacedLayer"), undefined, DialogModes.NO); }

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
        var base = part.layerSets.getByName("Base");
        if (base.artLayers.length) return base.artLayers[base.artLayers.length - 1].name;
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
            try { subs.push([maps[j], part.layerSets.getByName(maps[j][0])]); } catch (e) {}
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

JSX_BUILD = r"""
function buildPart(doc, p, src) {
    log("Building " + p.slot + " from " + p.set);
    app.activeDocument = doc;
    var grp = doc.layerSets.add(); grp.name = p.slot;

    // ---- Base
    var base = grp.layerSets.add(); base.name = "Base";
    var art = copyLayer(src, doc, base, p.set, null);
    if (!art) {   // fall back to the bottom layer of the Painter PSD
        app.activeDocument = src;
        var bottom = src.artLayers[src.artLayers.length - 1];
        art = copyLayer(src, doc, base, bottom.name, null);
        if (art) art.name = p.set;
    }
    app.activeDocument = doc;
    if (art) doc.activeLayer = art; else doc.activeLayer = base;
    for (var i = 0; i < p.fills.length; i++) {
        var fl = makeFill(p.fills[i][0], p.fills[i][1]);
        if (!art) fl.move(base, ElementPlacement.INSIDE);
    }
    var sh = base.layerSets.add(); sh.name = "Shading";
    var order = ["curv", "2D", "ao"];           // bottom to top
    for (i = 0; i < order.length; i++) {
        var k = order[i], st = p.shading[k];
        var L = copyLayer(src, doc, sh, p.set + "_" + k, "_" + k);
        if (L) { L.blendMode = blendOf(st.blend); L.opacity = st.opacity; }
    }
    var mk = copyLayer(src, doc, base, p.set + "_mask", "_mask");
    if (mk) mk.visible = false;

    // ---- Norm (original + green flipped for the game)
    var norm = grp.layerSets.add(); norm.name = "Norm";
    var n = p.n ? placeInto(doc, norm, p.n, p.set + "_n") : null;
    if (n) {
        var f = n.duplicate();
        f.rasterize(RasterizeType.ENTIRELAYER);
        doc.activeLayer = f;
        doc.activeChannels = [doc.channels[1]];
        f.invert();
        doc.activeChannels = doc.componentChannels;
        toSmartObject();
        f = doc.activeLayer;
        f.name = CFG.flipped_name;
    } else warn(p.set + "_n.tga not found - Norm group is empty");

    // ---- Spec / Rough
    var spec = grp.layerSets.add(); spec.name = "Spec";
    if (p.s) placeInto(doc, spec, p.s, p.set + "_s"); else warn(p.set + "_s.tga not found");
    var rough = grp.layerSets.add(); rough.name = "Rough";
    if (p.r) placeInto(doc, rough, p.r, p.set + "_r"); else warn(p.set + "_r.tga not found");

    norm.visible = false; spec.visible = false; rough.visible = false;
}

(function () {
    var oldUnits = app.preferences.rulerUnits, oldDlg = app.displayDialogs;
    var opened = [];
    try {
        app.preferences.rulerUnits = Units.PIXELS;
        app.displayDialogs = DialogModes.NO;
        log("Opening " + CFG.parts[0].psd);
        var first = open(new File(CFG.parts[0].psd)); opened.push(first);
        var W = first.width.as("px"), H = first.height.as("px"), RES = first.resolution;
        var doc = app.documents.add(UnitValue(W, "px"), UnitValue(H, "px"), RES, CFG.doc_name,
                                    NewDocumentMode.RGB, DocumentFill.TRANSPARENT, 1, BitsPerChannelType.EIGHT);
        var blank = doc.artLayers[0];
        for (var i = 0; i < CFG.parts.length; i++) {
            var p = CFG.parts[i];
            var src = i == 0 ? first : open(new File(p.psd));
            if (i) opened.push(src);
            app.activeDocument = src;
            if (src.width.as("px") != W || src.height.as("px") != H)
                warn(p.set + " is " + src.width.as("px") + "x" + src.height.as("px") + ", not " + W + "x" + H);
            buildPart(doc, p, src);
        }
        app.activeDocument = doc;
        try { blank.remove(); } catch (e) {}
        for (i = 0; i < opened.length; i++) opened[i].close(SaveOptions.DONOTSAVECHANGES);
        opened = [];
        app.activeDocument = doc;

        var o = new PhotoshopSaveOptions();
        o.layers = true; o.embedColorProfile = true; o.alphaChannels = true;
        log("Saving " + CFG.out_psd);
        doc.saveAs(new File(CFG.out_psd), o, false, Extension.LOWERCASE);

        var made = [];
        if (CFG.export_dir) made = exportGame(doc, CFG.export_dir, CFG.tga_src_dir);
        if (!CFG.keep_open) doc.close(SaveOptions.DONOTSAVECHANGES);
        var m = ""; for (i = 0; i < made.length; i++) m += (i ? "," : "") + q(made[i]);
        finish(true, "Built " + CFG.out_psd, ',"exported":[' + m + ']');
    } catch (e) {
        for (var j = 0; j < opened.length; j++) try { opened[j].close(SaveOptions.DONOTSAVECHANGES); } catch (e2) {}
        finish(false, e.message + " (script line " + e.line + ")");
    } finally {
        app.preferences.rulerUnits = oldUnits; app.displayDialogs = oldDlg;
    }
})();
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


def build_cfg(folder, chain_set, pendant_set, doc_name="Base", export_game=False,
                 keep_open=True, out_dir=None):
    """Returns the script settings. chain_set / pendant_set are set dicts from find_sets (or None)."""
    out_dir = out_dir or folder
    doc_name = (doc_name or "Base").strip()
    if doc_name.lower().endswith(".psd"):
        doc_name = doc_name[:-4]
    parts = []
    for slot, s in (("Chain", chain_set), ("Pendant", pendant_set)):   # Chain first -> Pendant on top
        if not s:
            continue
        if not s.get("psd"):
            raise ValueError(f"No Painter PSD found for '{s['name']}'")
        parts.append({
            "slot": slot, "set": s["name"], "psd": _js_path(s["psd"]),
            "n": _js_path(s.get("n")), "s": _js_path(s.get("s")), "r": _js_path(s.get("r")),
            "fills": [[n, list(c)] for n, c in FILLS[slot]],
            "shading": {k: {"blend": v["blend"],
                            "opacity": v["opacity"][slot] if isinstance(v["opacity"], dict) else v["opacity"]}
                        for k, v in SHADING.items()},
        })
    if not parts:
        raise ValueError("Pick a texture set for the Chain or the Pendant")
    cfg = {
        "parts": parts, "doc_name": doc_name,
        "out_psd": _js_path(os.path.join(out_dir, doc_name + ".psd")),
        "export_dir": _js_path(os.path.join(out_dir, "Game textures")) if export_game else None,
        "tga_src_dir": _js_path(folder), "keep_open": bool(keep_open),
        "flipped_name": FLIPPED_NORMAL_NAME,
    }
    return cfg


def export_script_cfg(psd_path):
    folder = os.path.dirname(psd_path)
    return {"psd": _js_path(psd_path), "export_dir": _js_path(os.path.join(folder, "Game textures")),
            "tga_src_dir": _js_path(folder)}


def make_jsx(cfg, kind="build"):
    return _header(cfg) + JSX_LIB + (JSX_BUILD if kind == "build" else JSX_EXPORT)


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
