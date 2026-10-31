"""
Tiny layered-PSD writer (8-bit RGB) - just enough for the PSD Builder:
pixel layers with blend mode / opacity / visibility / lock, and nested groups.
Only needs Pillow. Layer pixels are stored ZIP-compressed, which Photoshop reads.

    doc = Doc(2048, 2048)
    g = doc.group("Chain")
    g.layer("chain", pil_image, blend="normal")
    doc.save("Base.psd")
"""
import struct
import zlib

BLEND = {   # name -> PSD blend key
    "normal": b"norm", "pass": b"pass", "multiply": b"mul ", "screen": b"scrn", "overlay": b"over",
    "softlight": b"sLit", "hardlight": b"hLit", "darken": b"dark", "lighten": b"lite",
    "colordodge": b"div ", "colorburn": b"idiv", "linearburn": b"lbrn", "lineardodge": b"lddg",
    "difference": b"diff", "color": b"colr", "luminosity": b"lum ",
}
BLEND_NAMES = ["Normal", "Multiply", "Overlay", "Soft Light", "Hard Light", "Screen", "Darken",
               "Lighten", "Color Burn", "Linear Burn", "Color Dodge", "Linear Dodge"]


def blend_key(name):
    return BLEND.get((name or "normal").lower().replace(" ", "").replace("_", ""), b"norm")


class Layer:
    def __init__(self, name, image, blend="normal", opacity=100, visible=True, locked=False):
        self.name, self.image, self.blend = name, image, blend
        self.opacity, self.visible, self.locked = opacity, visible, locked


class Group:
    def __init__(self, name, visible=True, open_=True):
        self.name, self.visible, self.open = name, visible, open_
        self.children = []          # top to bottom, like the Layers panel

    def group(self, name, **kw):
        g = Group(name, **kw)
        self.children.append(g)
        return g

    def layer(self, name, image, **kw):
        L = Layer(name, image, **kw)
        self.children.append(L)
        return L


class Doc(Group):
    def __init__(self, width, height):
        super().__init__("")
        self.width, self.height = width, height

    def save(self, path):
        records, data = [], []
        self._flatten(self.children, records, data)
        with open(path, "wb") as f:
            # header
            f.write(b"8BPS" + struct.pack(">H6xHIIHH", 1, 3, self.height, self.width, 8, 3))
            f.write(struct.pack(">I", 0))                       # colour mode data
            res = _resolution_resource(72)
            f.write(struct.pack(">I", len(res)) + res)          # image resources
            layer_info = struct.pack(">h", len(records)) + b"".join(records) + b"".join(data)
            if len(layer_info) % 4:
                layer_info += b"\0" * (4 - len(layer_info) % 4)
            body = struct.pack(">I", len(layer_info)) + layer_info + struct.pack(">I", 0)  # + global mask
            f.write(struct.pack(">I", len(body)) + body)
            f.write(self._composite())

    # layers are stored bottom to top; a group is: end marker, children, then the group itself
    def _flatten(self, children, records, data):
        for item in reversed(children):
            if isinstance(item, Group):
                records.append(_record("</Layer group>", None, 0, 0, 0, 0, b"norm", 255, True, False,
                                       section=3))
                data.append(_empty_channels())
                self._flatten(item.children, records, data)
                records.append(_record(item.name, None, 0, 0, 0, 0, b"pass", 255, item.visible, False,
                                       section=1 if item.open else 2))
                data.append(_empty_channels())
            else:
                img = item.image
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                w, h = img.size
                chans = img.split()             # R G B A
                blobs = [_zip(chans[3]), _zip(chans[0]), _zip(chans[1]), _zip(chans[2])]
                records.append(_record(item.name, [len(b) for b in blobs], 0, 0, h, w,
                                       blend_key(item.blend), round(item.opacity * 2.55), item.visible,
                                       item.locked))
                data.append(b"".join(blobs))

    def _composite(self):
        """Merged image: plain white (Photoshop rebuilds it from the layers). RLE rows of one value."""
        w, h = self.width, self.height
        row = b""
        left = w
        while left:
            n = min(128, left)
            row += struct.pack(">bB", 1 - n, 255)
            left -= n
        counts = struct.pack(">H", len(row)) * (h * 3)
        return struct.pack(">H", 1) + counts + row * (h * 3)


def _zip(channel):
    return struct.pack(">H", 2) + zlib.compress(channel.tobytes(), 6)


def _empty_channels():
    return struct.pack(">H", 0) * 4


def _pascal(name):
    b = name.encode("latin-1", "replace")[:255]
    s = bytes([len(b)]) + b
    return s + b"\0" * ((4 - len(s) % 4) % 4)


def _block(key, payload):
    if len(payload) % 4:
        payload += b"\0" * (4 - len(payload) % 4)
    return b"8BIM" + key + struct.pack(">I", len(payload)) + payload


def _record(name, chan_lens, top, left, bottom, right, blend, opacity, visible, locked, section=None):
    ids = (-1, 0, 1, 2)
    lens = chan_lens or [2, 2, 2, 2]
    r = struct.pack(">iiiiH", top, left, bottom, right, 4)
    for cid, ln in zip(ids, lens):
        r += struct.pack(">hI", cid, ln)
    flags = 0x08 | (0x01 if locked else 0) | (0 if visible else 0x02)
    if section is not None:
        flags |= 0x10                    # pixel data irrelevant
    r += b"8BIM" + blend + struct.pack(">BBBB", opacity, 0, flags, 0)
    extra = struct.pack(">I", 0)         # no layer mask
    extra += struct.pack(">I", 0)        # no blending ranges
    extra += _pascal(name)
    uni = name.encode("utf-16-be")
    extra += _block(b"luni", struct.pack(">I", len(name)) + uni)
    if section is not None:
        extra += _block(b"lsct", struct.pack(">I", section) + (b"8BIM" + b"pass" if section in (1, 2) else b""))
    if locked:
        extra += _block(b"lspf", struct.pack(">I", 0x80000000))
    return r + struct.pack(">I", len(extra)) + extra


def _resolution_resource(dpi):
    fixed = struct.pack(">I", int(dpi * 65536))
    data = fixed + struct.pack(">HH", 1, 1) + fixed + struct.pack(">HH", 1, 1)
    return b"8BIM" + struct.pack(">H", 1005) + b"\0\0" + struct.pack(">I", len(data)) + data
