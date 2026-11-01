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


# ------------------------------------------------------------------ reading
def read_layers(path):
    """Read the pixel layers of an 8-bit RGB PSD (like Painter's exports).
    Returns ({layer name: RGBA image the size of the document}, (width, height)).
    Pillow's own PSD reader mixes up the colour channels of layers, so this reads them directly."""
    from PIL import Image
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"8BPS" or struct.unpack(">H", data[4:6])[0] != 1:
        raise ValueError(f"{path} isn't a PSD this app can read")
    chans, H, W, depth, mode = struct.unpack(">HIIHH", data[12:26])
    if depth != 8 or mode != 3:
        raise ValueError(f"{path} must be 8-bit RGB (it is {depth}-bit, mode {mode})")
    pos = 26
    pos += 4 + struct.unpack(">I", data[pos:pos + 4])[0]          # colour mode data
    pos += 4 + struct.unpack(">I", data[pos:pos + 4])[0]          # image resources
    pos += 4                                                      # layer & mask section length
    li_len = struct.unpack(">I", data[pos:pos + 4])[0]
    pos += 4
    if not li_len:
        return {}, (W, H)
    count = abs(struct.unpack(">h", data[pos:pos + 2])[0])
    pos += 2
    records = []
    for _ in range(count):
        top, left, bottom, right, n = struct.unpack(">iiiiH", data[pos:pos + 18])
        pos += 18
        ch = []
        for _ in range(n):
            cid, ln = struct.unpack(">hI", data[pos:pos + 6])
            ch.append((cid, ln))
            pos += 6
        pos += 12                                                 # 8BIM, blend, opacity, clip, flags, filler
        extra_len = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        e, end = pos, pos + extra_len
        e += 4 + struct.unpack(">I", data[e:e + 4])[0]            # layer mask
        e += 4 + struct.unpack(">I", data[e:e + 4])[0]            # blending ranges
        nlen = data[e]
        name = data[e + 1:e + 1 + nlen].decode("latin-1")
        e += (1 + nlen + 3) // 4 * 4
        while e + 12 <= end:                                      # unicode name if present
            sig, key, ln = data[e:e + 4], data[e + 4:e + 8], struct.unpack(">I", data[e + 8:e + 12])[0]
            if sig not in (b"8BIM", b"8B64"):
                break
            if key == b"luni":
                cnt = struct.unpack(">I", data[e + 12:e + 16])[0]
                name = data[e + 16:e + 16 + cnt * 2].decode("utf-16-be")
            e += 12 + ln
        pos = end
        records.append((name, top, left, bottom, right, ch))

    layers = {}
    for name, top, left, bottom, right, ch in records:
        w, h = right - left, bottom - top
        bands = {}
        for cid, ln in ch:
            blob = data[pos:pos + ln]
            pos += ln
            if w <= 0 or h <= 0 or cid < -1 or cid > 2:
                continue
            comp = struct.unpack(">H", blob[:2])[0]
            body = blob[2:]
            if comp == 0:
                band = Image.frombytes("L", (w, h), body[:w * h])
            elif comp == 1:
                band = Image.frombytes("L", (w, h), body[h * 2:], "packbits", "L")
            elif comp in (2, 3):
                raw = zlib.decompress(body)
                if comp == 3:                                     # undo the per-row prediction
                    rows = bytearray(raw)
                    for y in range(h):
                        o = y * w
                        for x in range(1, w):
                            rows[o + x] = (rows[o + x] + rows[o + x - 1]) & 255
                    raw = bytes(rows)
                band = Image.frombytes("L", (w, h), raw[:w * h])
            else:
                continue
            bands[cid] = band
        if w <= 0 or h <= 0 or not all(c in bands for c in (0, 1, 2)):
            continue
        alpha = bands.get(-1, Image.new("L", (w, h), 255))
        img = Image.merge("RGBA", (bands[0], bands[1], bands[2], alpha))
        if (left, top, w, h) != (0, 0, W, H):
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            canvas.paste(img, (left, top))
            img = canvas
        layers[name] = img
    return layers, (W, H)
