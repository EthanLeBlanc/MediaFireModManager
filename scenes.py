"""
Animated sidebar scenes for the holiday themes (Christmas + Thanksgiving).

Each theme in SCENES describes its picture: sky, moon or sun, ground, trees, one
centrepiece (Christmas tree, snowman, barn, turkey...), what falls from the sky
(snow, snowflakes, peppermints, leaves) and anything that flies past (Santa's
sleigh, a V of geese). Falling things dodge the mouse and can be clicked away.
"""
import math
import random
import tkinter as tk


def blend(c1, c2, t):
    t = max(0.0, min(1.0, t))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


LIGHTS = ("#ff4d4d", "#ffd23f", "#4dd2ff", "#7dff7a", "#ff8ad8", "#ffffff")

SCENES = {
    # ----------------------------------------------------------------- Christmas
    "christmas_eve": dict(night=True, haze="#24305a", moon="#f3f1e4", glow="#c9d4ff", ground="#dfe8f3",
                          ground2="#c4d2e4", trees="pine", item="xmas_tree", fall="snow", flyer="sleigh"),
    "candy_cane": dict(night=True, haze="#4a1622", moon="#ffeaf0", glow="#ff9aa8", ground="#f3e9ec",
                       ground2="#e2cdd3", trees=None, item="candy_canes", fall="peppermint"),
    "frosty": dict(night=True, haze="#1e4a6a", moon="#eaf6ff", glow="#9fd8ff", ground="#e6f2fb",
                   ground2="#c8deee", trees="pine", item="snowman", fall="flakes"),
    "north_pole": dict(night=True, haze="#0f3326", moon=None, aurora=True, ground="#e4efe9", ground2="#c6dacd",
                       trees="pine_many", item="presents", fall="snow"),
    "gingerbread": dict(night=True, haze="#3a2214", moon="#fff1d6", glow="#ffcf8a", ground="#f4ece2",
                        ground2="#e0d2c0", trees="pine", item="ginger_house", fall="snow"),
    # -------------------------------------------------------------- Thanksgiving
    "harvest": dict(night=False, haze="#c0601c", sun="#ffd27a", glow="#ff9a3c", ground="#3a2410",
                    ground2="#4c3016", hills="#5a3418", trees="autumn", item="hay_pumpkins", fall="leaves",
                    leaves=("#e8851a", "#f2b33d", "#c9561c", "#8f3a14")),
    "turkey_trot": dict(night=False, haze="#963f1e", sun="#ffc078", glow="#ff8a4c", ground="#2e1e10",
                        ground2="#3e2814", hills="#4a2a16", trees="autumn", item="turkey", fall="leaves",
                        leaves=("#c8553d", "#e0a050", "#8f4a20", "#f2c14e")),
    "cranberry": dict(night=True, haze="#4a1426", moon="#f6e3e8", glow="#d96a8a", ground="#1a0a10",
                      ground2="#24101a", trees="bare", item="barn", fall="leaves",
                      leaves=("#b3123a", "#d7264f", "#7a1430", "#e05a3a")),
    "maple": dict(night=False, haze="#c0522a", sun="#ffd08a", glow="#ff8a4c", ground="#2a2412",
                  ground2="#3a3218", hills="#4a3a1c", trees="autumn", item=None, fall="leaves", flyer="geese",
                  leaves=("#e2562a", "#c9361c", "#f29a2e", "#a82814")),
    "pumpkin_spice": dict(night=False, haze="#a8582a", sun="#ffcf8a", glow="#ff9a5c", ground="#3a2414",
                          ground2="#4a2e18", hills="#5a361c", trees="autumn", item="pumpkins", fall="leaves",
                          leaves=("#e07b39", "#c8672a", "#f2b56b", "#8f4a20")),
}


class SeasonScene(tk.Canvas):
    TICK = 45

    def __init__(self, master, theme, side, scale=1.0):
        super().__init__(master, bg=side, highlightthickness=0, bd=0, height=10)
        self.cfg = SCENES[theme]
        self.side = side
        self.k = scale
        self.rnd = random.Random()
        self.w = self.h = 0
        self.t = 0
        self.mouse = None
        self.parts, self.poofs, self.stars, self.lights, self.windows = [], [], [], [], []
        self.flyer, self.next_flyer = None, 60
        self.turkey = None
        self.aurora = []
        self.bind("<Configure>", self._layout)
        self.bind("<Motion>", lambda e: setattr(self, "mouse", (e.x, e.y)))
        self.bind("<Leave>", lambda e: setattr(self, "mouse", None))
        self.bind("<Button-1>", self._click)
        self.configure(cursor="hand2")
        self.after(self.TICK, self._tick)

    # ================================================================ drawing
    def _layout(self, e):
        if (e.width, e.height) == (self.w, self.h):
            return
        self.w, self.h = e.width, e.height
        self.delete("all")
        self.parts, self.poofs, self.stars, self.lights, self.windows, self.aurora = [], [], [], [], [], []
        self.flyer = None
        k, w, h, cf = self.k, self.w, self.h, self.cfg
        if h < 60 * k:
            return
        ground = self.ground = h - 30 * k
        bands = 40
        for i in range(bands):
            t = (i / bands) ** (1.8 if cf["night"] else 1.2)
            self.create_rectangle(0, ground * i / bands, w, ground * (i + 1) / bands + 1,
                                  fill=blend(self.side, cf["haze"], t), outline="")
        if cf.get("aurora"):
            for i, col in enumerate(("#3dffb0", "#38e0d0", "#8a7dff")):
                self.aurora.append({"id": self.create_line(0, 0, 1, 1, fill=blend(self.side, col, 0.35),
                                                           width=(9 - i * 2) * k, smooth=True, capstyle="round"),
                                    "y": ground * (0.22 + i * 0.1), "ph": i * 1.7, "col": col})
        if cf["night"]:
            for _ in range(int(w * ground / (900 * k * k)) + 6):
                x, y = self.rnd.uniform(4, w - 4), self.rnd.uniform(4, ground * 0.7)
                r = self.rnd.choice((0.8, 1.0, 1.3)) * k
                self.stars.append(self.create_oval(x - r, y - r, x + r, y + r, fill="#a9b6d6", outline=""))
        # moon (night) or a low sun (sunset)
        sky_at = lambda y: blend(self.side, cf["haze"], (y / ground) ** (1.8 if cf["night"] else 1.2))
        if cf.get("moon"):
            mx, my, mr = w * 0.7, min(ground * 0.36, 80 * k), 17 * k
            self.moon = (mx, my)
            for i in range(12, 0, -1):
                rr = mr + i * 1.7 * k
                self.create_oval(mx - rr, my - rr, mx + rr, my + rr, outline="",
                                 fill=blend(sky_at(my), cf["glow"], ((13 - i) / 13) ** 2.2 * 0.45))
            self.create_oval(mx - mr, my - mr, mx + mr, my + mr, fill=cf["moon"], outline="")
            for cx, cy, cr in ((-6, -5, 4), (5, 3, 5), (-3, 8, 2.5)):
                self.create_oval(mx + (cx - cr) * k, my + (cy - cr) * k, mx + (cx + cr) * k, my + (cy + cr) * k,
                                 fill=blend(cf["moon"], "#000000", 0.08), outline="")
        elif cf.get("sun"):
            sx, sy, sr = w * 0.68, ground - 22 * k, 20 * k
            self.moon = (sx, sy)
            for i in range(14, 0, -1):
                rr = sr + i * 2.2 * k
                self.create_oval(sx - rr, sy - rr, sx + rr, sy + rr, outline="",
                                 fill=blend(sky_at(sy), cf["glow"], ((15 - i) / 15) ** 2 * 0.6))
            self.create_oval(sx - sr, sy - sr, sx + sr, sy + sr, fill=cf["sun"], outline="")
        else:
            self.moon = (w * 0.7, ground * 0.3)
        # far hills (autumn)
        if cf.get("hills"):
            pts = []
            for i in range(25):
                x = w * i / 24
                pts += [x, ground - (18 + 8 * math.sin(i / 2.3 + 1)) * k]
            self.create_polygon(0, h, *pts, w, h, fill=cf["hills"], outline="")
        # trees behind the ground line
        self._trees()
        # ground
        pts = []
        for i in range(25):
            x = w * i / 24
            pts += [x, ground - (6 + 5 * math.sin(i / 3.2)) * k]
        self.create_polygon(0, h, *pts, w, h, fill=cf["ground2"], outline="")
        pts2 = [v + (3 * k if j % 2 else 0) for j, v in enumerate(pts)]
        self.create_polygon(0, h, *pts2, w, h, fill=cf["ground"], outline="")
        # the centrepiece
        item = cf.get("item")
        if item:
            getattr(self, "_item_" + item)(w * 0.64, ground + 2 * k)
        # falling things
        n = {"snow": 46, "flakes": 14, "peppermint": 9, "leaves": 15}[cf["fall"]]
        self.parts = [self._new_part(top=False) for _ in range(n)]

    def _trees(self):
        k, w, g, kind = self.k, self.w, self.ground, self.cfg.get("trees")
        if kind in ("pine", "pine_many"):
            spots = ((0.1, 46), (0.27, 32), (0.9, 40)) if kind == "pine" else \
                ((0.06, 44), (0.2, 30), (0.34, 38), (0.86, 46), (0.97, 30))
            for fx, th in spots:
                self._pine(w * fx, g + 2 * k, th * k)
        elif kind == "autumn":
            for fx, th in ((0.1, 50), (0.28, 34), (0.92, 44)):
                self._autumn_tree(w * fx, g + 2 * k, th * k)
        elif kind == "bare":
            tx = w * 0.16
            col = blend(self.cfg["ground"], "#000000", 0.3)
            self.create_line(tx, g + 4 * k, tx + 2 * k, g - 58 * k, fill=col, width=5 * k)
            for (x0, y0, x1, y1, wd) in ((2, -40, -18, -62, 3), (2, -48, 22, -70, 3), (-10, -53, -20, -60, 2),
                                         (14, -61, 26, -64, 2), (2, -30, 18, -42, 2.5), (2, -58, 6, -76, 2)):
                self.create_line(tx + x0 * k, g + y0 * k, tx + x1 * k, g + y1 * k, fill=col, width=wd * k,
                                 capstyle="round")

    def _pine(self, x, base, h):
        k = self.k
        dark, snow = "#0d2a1b", "#eef5fb"
        self.create_rectangle(x - 2 * k, base - h * 0.18, x + 2 * k, base, fill="#3a2414", outline="")
        for i in range(3):
            top = base - h + i * h * 0.24
            bot = top + h * 0.42
            half = h * (0.2 + i * 0.09)
            self.create_polygon(x, top, x + half, bot, x - half, bot, fill=dark, outline="")
            self.create_polygon(x, top, x + half * 0.42, top + h * 0.17, x - half * 0.42, top + h * 0.17,
                                fill=snow, outline="")

    def _autumn_tree(self, x, base, h):
        k = self.k
        trunk = blend(self.cfg["ground"], "#000000", 0.25)
        self.create_line(x, base, x, base - h * 0.55, fill=trunk, width=4 * k)
        cols = [blend(c, "#2a1208", 0.35) for c in self.cfg["leaves"]]
        for dx, dy, r, c in ((-0.18, -0.62, 0.26, 0), (0.18, -0.64, 0.26, 1), (0, -0.82, 0.3, 2), (0, -0.6, 0.24, 3)):
            cx, cy, rr = x + dx * h, base + dy * h, r * h
            self.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill=cols[c % len(cols)], outline="")

    # ---------------------------------------------------------- centrepieces
    def _item_xmas_tree(self, x, base):
        k = self.k
        h = 62 * k
        self.create_oval(x - 30 * k, base - 8 * k, x + 30 * k, base + 6 * k, fill=blend(self.cfg["ground"], "#ffd23f", 0.12), outline="")
        self.create_rectangle(x - 3 * k, base - 10 * k, x + 3 * k, base, fill="#5a3418", outline="")
        for i in range(4):
            top = base - h + i * h * 0.2
            half = (9 + i * 6) * k
            self.create_polygon(x, top, x + half, top + h * 0.34, x - half, top + h * 0.34, fill="#14532d", outline="")
        for i in range(16):                                    # lights
            row = self.rnd.uniform(0.2, 0.92)
            y = base - h + row * h
            span = (6 + row * 22) * k
            lx = x + self.rnd.uniform(-span, span) * 0.85
            r = 1.7 * k
            self.lights.append(self.create_oval(lx - r, y - r, lx + r, y + r, fill=self.rnd.choice(LIGHTS), outline=""))
        sy = base - h - 2 * k                                  # star
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rr = (6 if i % 2 == 0 else 2.6) * k
            pts += [x + rr * math.cos(a), sy + rr * math.sin(a)]
        self.star = self.create_polygon(*pts, fill="#ffd23f", outline="")
        for gx, gw, gh, col, rib in ((-20, 11, 8, "#e63946", "#ffd23f"), (14, 12, 9, "#2a9df4", "#ffffff"),
                                     (-6, 9, 6, "#5ccf7a", "#e63946")):
            x0, y0 = x + gx * k, base - gh * k
            self.create_rectangle(x0, y0, x0 + gw * k, base, fill=col, outline="")
            self.create_rectangle(x0 + gw * k / 2 - k, y0, x0 + gw * k / 2 + k, base, fill=rib, outline="")

    def _item_presents(self, x, base):
        k = self.k
        for gx, gw, gh, col, rib in ((-26, 14, 11, "#e63946", "#ffd23f"), (-10, 12, 15, "#e8c35a", "#14532d"),
                                     (4, 15, 10, "#2a9df4", "#ffffff"), (-18, 10, 7, "#5ccf7a", "#e63946")):
            x0, y0 = x + gx * k, base - gh * k
            self.create_rectangle(x0, y0, x0 + gw * k, base, fill=col, outline="")
            self.create_rectangle(x0 + gw * k / 2 - k, y0, x0 + gw * k / 2 + k, base, fill=rib, outline="")
            self.create_rectangle(x0, y0 + gh * k / 2 - k, x0 + gw * k, y0 + gh * k / 2 + k, fill=rib, outline="")

    def _item_candy_canes(self, x, base):
        k = self.k
        for dx, hh, flip in ((-22, 40, 1), (0, 52, -1), (22, 34, 1)):
            cx = x + dx * k
            top = base - hh * k
            for col, dash in (("#ffffff", None), ("#e63946", (int(4 * k) or 1, int(4 * k) or 1))):
                kw = dict(fill=col, width=5 * k, capstyle="butt")
                if dash:
                    kw["dash"] = dash
                self.create_line(cx, base + 2 * k, cx, top + 6 * k, **kw)
                ax0 = cx if flip > 0 else cx - 12 * k
                self.create_arc(ax0, top, ax0 + 12 * k, top + 12 * k, start=0 if flip > 0 else 0, extent=180,
                                style="arc", outline=col, width=5 * k, **({"dash": dash} if dash else {}))

    def _item_snowman(self, x, base):
        k = self.k
        white, shade = "#f7fbff", "#d6e4f0"
        for r, off in ((15, 13), (11, 36), (8, 53)):
            cy = base - off * k
            self.create_oval(x - r * k, cy - r * k, x + r * k, cy + r * k, fill=white, outline=shade, width=1)
        hy = base - 53 * k - 8 * k                              # hat
        self.create_rectangle(x - 10 * k, hy, x + 10 * k, hy + 2.5 * k, fill="#111418", outline="")
        self.create_rectangle(x - 6 * k, hy - 12 * k, x + 6 * k, hy, fill="#111418", outline="")
        self.create_rectangle(x - 6 * k, hy - 4 * k, x + 6 * k, hy - 1.5 * k, fill="#e63946", outline="")
        ey = base - 55 * k
        self.eyes = [self.create_oval(x + dx * k - 1.4 * k, ey - 1.4 * k, x + dx * k + 1.4 * k, ey + 1.4 * k,
                                      fill="#111418", outline="") for dx in (-3, 3)]
        self.create_polygon(x, base - 52 * k, x + 10 * k, base - 50.5 * k, x, base - 49 * k, fill="#ff8a1f", outline="")
        self.create_line(x - 5 * k, base - 46 * k, x + 5 * k, base - 46 * k, fill="#e63946", width=3 * k)   # scarf
        self.create_line(x + 3 * k, base - 46 * k, x + 6 * k, base - 38 * k, fill="#e63946", width=3 * k)
        for off in (40, 33):
            b = base - off * k
            self.create_oval(x - 1.4 * k, b - 1.4 * k, x + 1.4 * k, b + 1.4 * k, fill="#111418", outline="")
        for side in (-1, 1):                                     # stick arms
            self.create_line(x + side * 10 * k, base - 37 * k, x + side * 24 * k, base - 46 * k, fill="#5a3418", width=2 * k)
            self.create_line(x + side * 20 * k, base - 43.5 * k, x + side * 22 * k, base - 50 * k, fill="#5a3418", width=1.5 * k)

    def _item_ginger_house(self, x, base):
        k = self.k
        body, icing = "#b8743a", "#fff8f0"
        self.create_oval(x - 34 * k, base - 10 * k, x + 34 * k, base + 6 * k, fill=blend(self.cfg["ground"], "#ffd27a", 0.2), outline="")
        self.create_rectangle(x - 22 * k, base - 30 * k, x + 22 * k, base, fill=body, outline="")
        self.create_polygon(x - 28 * k, base - 28 * k, x, base - 52 * k, x + 28 * k, base - 28 * k, fill="#8a4f24", outline="")
        self.create_line(x - 28 * k, base - 28 * k, x, base - 52 * k, x + 28 * k, base - 28 * k, fill=icing, width=4 * k,
                         joinstyle="round", capstyle="round")
        for i in range(7):                                       # icing drips
            dx = -24 + i * 8
            self.create_oval(x + dx * k - 2 * k, base - 29 * k, x + dx * k + 2 * k, base - 24 * k, fill=icing, outline="")
        for wx in (-14, 9):
            w = self.create_rectangle(x + wx * k, base - 22 * k, x + (wx + 6) * k, base - 15 * k, fill="#ffd27a", outline=icing, width=1)
            self.windows.append(w)
        self.create_rectangle(x - 4 * k, base - 13 * k, x + 4 * k, base, fill="#5a2e12", outline=icing, width=1)
        for cx, col in ((-17, "#e63946"), (-7, "#5ccf7a"), (3, "#ffd23f"), (13, "#2a9df4")):   # gumdrops
            self.create_oval(x + cx * k, base - 4 * k, x + (cx + 4) * k, base, fill=col, outline="")

    def _item_hay_pumpkins(self, x, base):
        k = self.k
        self.create_rectangle(x - 26 * k, base - 16 * k, x + 4 * k, base, fill="#d9a441", outline="")
        for i in range(5):
            yy = base - 14 * k + i * 3.2 * k
            self.create_line(x - 25 * k, yy, x + 3 * k, yy + 1 * k, fill="#b98624", width=1)
        self._pumpkin(x + 14 * k, base, 9 * k)
        self._pumpkin(x - 12 * k, base - 16 * k, 6.5 * k)
        self._pumpkin(x + 28 * k, base, 6 * k)

    def _item_pumpkins(self, x, base):
        k = self.k
        for dx, r in ((-30, 7), (-14, 10), (6, 8), (22, 11), (38, 6)):
            self._pumpkin(x + dx * k, base + 1 * k, r * k)
        self.create_line(x - 40 * k, base - 2 * k, x + 46 * k, base - 3 * k, fill="#3e6b2a", width=1.5 * k, smooth=True)

    def _pumpkin(self, cx, base, r):
        k = self.k
        cy = base - r * 0.8
        for dx, col in ((-0.45, "#c9561c"), (0.45, "#c9561c"), (0, "#e8851a")):
            self.create_oval(cx + dx * r - r * 0.62, cy - r * 0.8, cx + dx * r + r * 0.62, cy + r * 0.8, fill=col, outline="")
        self.create_line(cx, cy - r * 0.8, cx + r * 0.15, cy - r * 1.15, fill="#4a6b2a", width=max(1.5 * k, r * 0.18))

    def _item_barn(self, x, base):
        k = self.k
        red, trim = "#8e1b2a", "#f3e6e8"
        self.create_rectangle(x - 22 * k, base - 26 * k, x + 22 * k, base, fill=red, outline="")
        self.create_polygon(x - 26 * k, base - 25 * k, x - 14 * k, base - 40 * k, x + 14 * k, base - 40 * k,
                            x + 26 * k, base - 25 * k, fill="#5e1220", outline="")
        self.create_line(x - 26 * k, base - 25 * k, x - 14 * k, base - 40 * k, x + 14 * k, base - 40 * k,
                         x + 26 * k, base - 25 * k, fill=trim, width=1.5 * k)
        self.create_rectangle(x - 9 * k, base - 18 * k, x + 9 * k, base, fill="#6e1422", outline=trim, width=1)
        self.create_line(x - 9 * k, base - 18 * k, x + 9 * k, base, fill=trim, width=1)
        self.create_line(x + 9 * k, base - 18 * k, x - 9 * k, base, fill=trim, width=1)
        w = self.create_rectangle(x - 4 * k, base - 33 * k, x + 4 * k, base - 27 * k, fill="#ffd27a", outline=trim, width=1)
        self.windows.append(w)
        self.create_oval(x - 40 * k, base - 12 * k, x - 28 * k, base, fill="#c9a441", outline="")   # hay

    def _item_turkey(self, x, base):
        k = self.k
        for i in range(6):                                          # fence
            fx = self.w * 0.05 + i * 14 * k
            self.create_line(fx, base + 2 * k, fx, base - 12 * k, fill="#5a3a20", width=2 * k)
        self.create_line(self.w * 0.05 - 3 * k, base - 9 * k, self.w * 0.05 + 73 * k, base - 9 * k, fill="#5a3a20", width=1.5 * k)
        self.create_line(self.w * 0.05 - 3 * k, base - 4 * k, self.w * 0.05 + 73 * k, base - 4 * k, fill="#5a3a20", width=1.5 * k)
        self.turkey = {"x": x, "dir": -1, "ids": [], "base": base}

    def _draw_turkey(self):
        tk_ = self.turkey
        k = self.k
        for i in tk_["ids"]:
            self.delete(i)
        x, base, d = tk_["x"], tk_["base"], tk_["dir"]
        bob = abs(math.sin(self.t / 3)) * 1.5 * k
        ids = []
        for i, col in enumerate(("#8f4a20", "#e0a050", "#c8553d", "#f2c14e", "#6b3a1a")):   # tail fan
            r = (17 - i * 2.4) * k
            ids.append(self.create_arc(x - d * 4 * k - r, base - 16 * k - r - bob, x - d * 4 * k + r, base - 16 * k + r - bob,
                                       start=20, extent=140, style="pieslice", fill=col, outline=""))
        ids.append(self.create_oval(x - 9 * k, base - 20 * k - bob, x + 9 * k, base - 5 * k - bob, fill="#6b3a1a", outline=""))
        hx = x + d * 9 * k
        ids.append(self.create_line(x + d * 5 * k, base - 14 * k - bob, hx, base - 24 * k - bob, fill="#6b3a1a", width=3 * k))
        ids.append(self.create_oval(hx - 3 * k, base - 28 * k - bob, hx + 3 * k, base - 22 * k - bob, fill="#9a5a3a", outline=""))
        ids.append(self.create_polygon(hx + d * 2.5 * k, base - 26 * k - bob, hx + d * 6 * k, base - 25 * k - bob,
                                       hx + d * 2.5 * k, base - 24 * k - bob, fill="#f2c14e", outline=""))
        ids.append(self.create_oval(hx + d * 0.5 * k - 1.2 * k, base - 24 * k - bob, hx + d * 0.5 * k + 1.2 * k,
                                    base - 19 * k - bob, fill="#d7263d", outline=""))
        step = math.sin(self.t / 2.5) * 2 * k
        for lx, s in ((-3, step), (3, -step)):
            ids.append(self.create_line(x + lx * k, base - 6 * k - bob, x + lx * k + s, base, fill="#e0a050", width=1.5 * k))
        tk_["ids"] = ids

    # ========================================================== falling things
    def _new_part(self, top=True):
        k, kind = self.k, self.cfg["fall"]
        p = {"x": self.rnd.uniform(0, self.w), "y": -10 * k if top else self.rnd.uniform(0, self.ground),
             "ph": self.rnd.uniform(0, 6.28), "rot": self.rnd.uniform(0, 6.28), "ids": []}
        if kind == "snow":
            p.update(r=self.rnd.uniform(0.9, 2.1) * k, vy=self.rnd.uniform(0.35, 1.0) * k,
                     col=self.rnd.choice(("#ffffff", "#eef5ff", "#dbe8ff")))
        elif kind == "flakes":
            p.update(r=self.rnd.uniform(3.5, 6.5) * k, vy=self.rnd.uniform(0.3, 0.7) * k, vr=self.rnd.uniform(-0.04, 0.04))
        elif kind == "peppermint":
            p.update(r=self.rnd.uniform(4, 6) * k, vy=self.rnd.uniform(0.45, 0.9) * k, vr=self.rnd.uniform(-0.08, 0.08))
        else:
            p.update(r=self.rnd.uniform(4, 6.5) * k, vy=self.rnd.uniform(0.45, 0.95) * k, vr=self.rnd.uniform(-0.06, 0.06),
                     col=self.rnd.choice(self.cfg["leaves"]))
        return p

    def _draw_part(self, p):
        kind, x, y, r = self.cfg["fall"], p["x"], p["y"], p["r"]
        if kind == "snow":
            if not p["ids"]:
                p["ids"] = [self.create_oval(0, 0, 0, 0, fill=p["col"], outline="")]
            self.coords(p["ids"][0], x - r, y - r, x + r, y + r)
        elif kind == "flakes":
            if not p["ids"]:
                p["ids"] = [self.create_line(0, 0, 0, 0, fill="#f2f9ff", width=max(1, self.k * 1.1)) for _ in range(3)]
            for i, item in enumerate(p["ids"]):
                a = p["rot"] + i * math.pi / 3
                dx, dy = math.cos(a) * r, math.sin(a) * r
                self.coords(item, x - dx, y - dy, x + dx, y + dy)
        elif kind == "peppermint":
            if not p["ids"]:
                p["ids"] = [self.create_oval(0, 0, 0, 0, fill="#ffffff", outline="")] + \
                           [self.create_arc(0, 0, 0, 0, fill="#e63946", outline="", style="pieslice", extent=40)
                            for _ in range(4)]
            self.coords(p["ids"][0], x - r, y - r, x + r, y + r)
            for i, item in enumerate(p["ids"][1:]):
                self.coords(item, x - r, y - r, x + r, y + r)
                self.itemconfigure(item, start=math.degrees(p["rot"]) + i * 90)
        else:   # leaf
            shape = ((0, -1), (0.45, -0.45), (0.5, 0.2), (0, 1), (-0.5, 0.2), (-0.45, -0.45))
            ca, sa = math.cos(p["rot"]), math.sin(p["rot"])
            pts = []
            for px, py in shape:
                pts += [x + (px * ca - py * sa) * r, y + (px * sa + py * ca) * r]
            if not p["ids"]:
                p["ids"] = [self.create_polygon(*pts, fill=p["col"], outline=blend(p["col"], "#000000", 0.25))]
            else:
                self.coords(p["ids"][0], *pts)

    def _click(self, e):
        k = self.k
        if self.turkey and abs(self.turkey["x"] - e.x) < 16 * k and abs(self.turkey["base"] - 14 * k - e.y) < 16 * k:
            self._poof(self.turkey["x"], self.turkey["base"] - 16 * k, ("#8f4a20", "#e0a050", "#c8553d"), 10)
            self.turkey["dir"] *= -1
            return
        for p in self.parts:
            if abs(p["x"] - e.x) < max(10 * k, p["r"] * 2) and abs(p["y"] - e.y) < max(10 * k, p["r"] * 2):
                col = p.get("col", "#ffffff" if self.cfg["fall"] != "peppermint" else "#e63946")
                self._poof(p["x"], p["y"], (col,), 7)
                for i in p["ids"]:
                    self.delete(i)
                p.clear()
                p.update(self._new_part())
                return

    def _poof(self, x, y, cols, n):
        for i in range(n):
            ang = i * 6.283 / n
            self.poofs.append({"x": x, "y": y, "dx": math.cos(ang) * 2 * self.k, "dy": math.sin(ang) * 2 * self.k,
                               "life": 12, "id": None, "col": self.rnd.choice(cols)})

    # ================================================================ flyers
    def _start_flyer(self):
        k = self.k
        kind = self.cfg.get("flyer")
        if kind == "sleigh":
            self.flyer = {"kind": kind, "x": self.w + 50 * k, "y": self.moon[1] + 4 * k, "vx": -1.5 * k, "ids": []}
        elif kind == "geese":
            self.flyer = {"kind": kind, "x": -60 * k, "y": self.rnd.uniform(18, self.ground * 0.4 / k) * k,
                          "vx": 1.2 * k, "ids": []}

    def _draw_flyer(self):
        f, k = self.flyer, self.k
        for i in f["ids"]:
            self.delete(i)
        f["ids"] = []
        x, y = f["x"], f["y"] + math.sin(self.t / 8) * 2 * k
        sil = "#05070d"
        if f["kind"] == "sleigh":
            for j, rx in enumerate((-30, -46)):                      # reindeer
                bx, by = x + rx * k, y - 3 * k + (math.sin(self.t / 3 + j) * 1.2 * k)
                f["ids"].append(self.create_oval(bx - 6 * k, by - 2.5 * k, bx + 6 * k, by + 2.5 * k, fill=sil, outline=""))
                f["ids"].append(self.create_line(bx - 5 * k, by - 1 * k, bx - 8 * k, by - 6 * k, fill=sil, width=1.5 * k))
                f["ids"].append(self.create_line(bx - 8 * k, by - 6 * k, bx - 10 * k, by - 9 * k, fill=sil, width=1))
                f["ids"].append(self.create_line(bx - 8 * k, by - 6 * k, bx - 6 * k, by - 10 * k, fill=sil, width=1))
                leg = math.sin(self.t / 2 + j) * 2 * k
                f["ids"].append(self.create_line(bx - 3 * k, by + 2 * k, bx - 4 * k - leg, by + 6 * k, fill=sil, width=1))
                f["ids"].append(self.create_line(bx + 4 * k, by + 2 * k, bx + 5 * k + leg, by + 6 * k, fill=sil, width=1))
            f["ids"].append(self.create_line(x - 24 * k, y - 3 * k, x - 8 * k, y - 1 * k, fill=sil, width=1))
            f["ids"].append(self.create_polygon(x - 9 * k, y - 4 * k, x + 8 * k, y - 4 * k, x + 10 * k, y - 10 * k,
                                                x + 12 * k, y - 10 * k, x + 10 * k, y + 2 * k, x - 7 * k, y + 2 * k,
                                                fill=sil, outline=""))
            f["ids"].append(self.create_line(x - 11 * k, y + 4 * k, x + 12 * k, y + 4 * k, fill=sil, width=1.5 * k))
            f["ids"].append(self.create_oval(x + 1 * k, y - 9 * k, x + 7 * k, y - 4 * k, fill=sil, outline=""))   # santa
        else:   # a V of geese
            flap = 2.2 * k if int(self.t / 4) % 2 else -1.5 * k
            for j, (dx, dy) in enumerate(((0, 0), (-9, -5), (-9, 5), (-18, -10), (-18, 10))):
                gx, gy = x + dx * k, y + dy * k
                f["ids"].append(self.create_line(gx - 5 * k, gy - flap, gx, gy, gx + 5 * k, gy - flap,
                                                 fill="#1a1208", width=1.5 * k, joinstyle="round"))

    # ================================================================== loop
    def _tick(self):
        if not self.winfo_exists():
            return
        self.after(self.TICK, self._tick)
        if not self.winfo_ismapped() or not self.w or self.h < 60 * self.k:
            return
        self.t += 1
        k = self.k
        if self.stars and self.t % 4 == 0:
            self.itemconfigure(self.rnd.choice(self.stars),
                               fill=self.rnd.choice(("#a9b6d6", "#e6ecff", "#6c7aa0", "#ffffff")))
        if self.lights and self.t % 3 == 0:
            for item in self.rnd.sample(self.lights, 4):
                self.itemconfigure(item, fill=self.rnd.choice(LIGHTS))
            if self.t % 9 == 0 and hasattr(self, "star"):
                self.itemconfigure(self.star, fill=self.rnd.choice(("#ffd23f", "#fff2a8", "#ffe066")))
        if self.windows and self.t % 5 == 0:
            for wdw in self.windows:
                self.itemconfigure(wdw, fill=self.rnd.choice(("#ffd27a", "#ffc65c", "#ffe0a0")))
        if hasattr(self, "eyes") and self.t % 80 in (0, 1):     # snowman blinks
            for e in self.eyes:
                self.itemconfigure(e, state="hidden" if self.t % 80 == 0 else "normal")
        for a in self.aurora:
            pts = []
            for i in range(9):
                x = self.w * i / 8
                pts += [x, a["y"] + math.sin(i / 1.4 + self.t / 22 + a["ph"]) * 9 * k]
            self.coords(a["id"], *pts)
            self.itemconfigure(a["id"], fill=blend(self.side, a["col"], 0.25 + 0.2 * (1 + math.sin(self.t / 30 + a["ph"])) / 2))
        # falling things
        for p in self.parts:
            p["y"] += p["vy"]
            p["x"] += math.sin(self.t / 14 + p["ph"]) * 0.45 * k
            p["rot"] += p.get("vr", 0)
            if self.mouse:
                dx, dy = p["x"] - self.mouse[0], p["y"] - self.mouse[1]
                d = math.hypot(dx, dy)
                if d < 30 * k:
                    p["x"] += dx / (d + 1) * 2.2 * k
                    p["y"] += dy / (d + 1) * 1.2 * k
            if p["y"] > self.ground + 6 * k or p["x"] < -20 * k or p["x"] > self.w + 20 * k:
                for i in p["ids"]:
                    self.delete(i)
                p.clear()
                p.update(self._new_part())
            self._draw_part(p)
        # sleigh / geese
        if self.cfg.get("flyer"):
            if self.flyer is None:
                self.next_flyer -= 1
                if self.next_flyer <= 0:
                    self._start_flyer()
            else:
                self.flyer["x"] += self.flyer["vx"]
                self._draw_flyer()
                if self.flyer["x"] < -80 * k or self.flyer["x"] > self.w + 80 * k:
                    for i in self.flyer["ids"]:
                        self.delete(i)
                    self.flyer = None
                    self.next_flyer = self.rnd.randint(250, 520)
        # turkey struts back and forth
        if self.turkey:
            self.turkey["x"] += self.turkey["dir"] * 0.55 * k
            if self.turkey["x"] < self.w * 0.42:
                self.turkey["dir"] = 1
            elif self.turkey["x"] > self.w * 0.9:
                self.turkey["dir"] = -1
            self._draw_turkey()
        # click puffs
        for p in list(self.poofs):
            p["life"] -= 1
            p["x"] += p["dx"]
            p["y"] += p["dy"]
            r = (3 - p["life"] / 6) * k
            col = blend(p["col"], self.side, 1 - p["life"] / 12)
            if p["id"] is None:
                p["id"] = self.create_oval(0, 0, 0, 0, fill=col, outline="")
            self.coords(p["id"], p["x"] - r, p["y"] - r, p["x"] + r, p["y"] + r)
            self.itemconfigure(p["id"], fill=col)
            if p["life"] <= 0:
                self.delete(p["id"])
                self.poofs.remove(p)
