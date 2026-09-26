"""Render draw_screen() to SVG so the 480x480 layout can be eyeballed."""
import os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
import datetime
import sys
import types

SRC = os.path.join(REPO, "football_scores.py")

ops = []          # drawing operations, in order
cur = {"pen": (0, 0, 0), "size": 24}

# Rough Roboto Medium advance widths, as a fraction of the font size.
NARROW = set("iljtIf.,:;'!|()[] ")
WIDE = set("mMwWO@")


def text_width(s, size):
    total = 0.0
    for ch in s:
        if ch in NARROW:
            total += 0.30
        elif ch in WIDE:
            total += 0.82
        elif ch.isupper() or ch.isdigit():
            total += 0.60
        else:
            total += 0.53
    return total * size


class Poly:
    def __init__(self):
        self.shape = None

    def rectangle(self, x, y, w, h, corners=(0, 0, 0, 0), stroke=0):
        self.shape = ("rect", x, y, w, h, corners[0] if corners else 0)

    def circle(self, x, y, r, stroke=0):
        self.shape = ("circle", x, y, r)


class Vec:
    def __init__(self, *a, **k): pass
    def set_antialiasing(self, *a): pass
    def set_transform(self, *a): pass
    def set_font(self, name, size): cur["size"] = size
    def set_font_size(self, size): cur["size"] = size
    def set_font_letter_spacing(self, *a): pass
    def set_font_word_spacing(self, *a): pass
    def set_font_line_height(self, *a): pass
    def set_font_align(self, *a): pass

    def measure_text(self, t, x=0, y=0, angle=None):
        return (0, 0, text_width(t, cur["size"]), cur["size"])

    def text(self, t, x, y, angle=None, max_width=0, max_height=0):
        ops.append(("text", t, x, y, cur["size"], cur["pen"]))

    def draw(self, poly):
        ops.append(("shape", poly.shape, cur["pen"]))


class Display:
    def get_bounds(self): return (480, 480)
    def create_pen(self, r, g, b): return (r, g, b)
    def set_pen(self, pen): cur["pen"] = pen
    def clear(self): ops.append(("clear", cur["pen"]))
    def rectangle(self, x, y, w, h): ops.append(("shape", ("rect", x, y, w, h, 0), cur["pen"]))
    def circle(self, x, y, r): ops.append(("shape", ("circle", x, y, r), cur["pen"]))
    def __getattr__(self, n): return lambda *a, **k: None


class Presto:
    def __init__(self, *a, **k): self.display = Display()
    def update(self): pass
    def connect(self): return True
    def __getattr__(self, n): return lambda *a, **k: None


def stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m


BADGE_DIR = os.path.join(HERE, "badges")
_have = {int(n.split(".")[0]) for n in __import__("os").listdir(BADGE_DIR)}

# The real badge module, loaded under another name so the stub below (which
# stands in for the on-device cache) does not shadow it.
import importlib.util as _ilu

sys.modules["requests"] = types.ModuleType("requests")
_real_spec = _ilu.spec_from_file_location(
    "_real_badges", os.path.join(REPO, "football_badges.py"))
real_badges = _ilu.module_from_spec(_real_spec)
_real_spec.loader.exec_module(real_badges)


class PNG:
    """Records a badge draw, and carries the real image through to the SVG."""
    def __init__(self, *a, **k): self.tag = None

    def open_RAM(self, buf):
        self.tag = bytes(buf[:24]).decode()   # our stub puts "id:<n>" in front

    def decode(self, x, y, **k):
        ops.append(("badge", self.tag, x, y))


class BadgeCache:
    """Serves the real downloaded badges, shrunk by the real decoder."""
    def __init__(self, size, background, log=None, limit=24):
        self.size = size
        self.background = background
        self.dims = {}
        self._palettes = {}
        self.pending = []

    def buffer(self, team_id):
        if team_id not in _have:
            return None
        return bytearray(("id:%d" % team_id).ljust(24).encode())

    def size_of(self, team_id):
        if team_id not in self.dims:
            real = real_badges
            data = open("%s/%d.png" % (BADGE_DIR, team_id), "rb").read()
            w, h, _rgb, _pal = real.decode_scaled(data, self.size, self.background)
            self.dims[team_id] = (w, h)
        return self.dims[team_id]

    def colours(self, team_id):
        if team_id not in _have:
            return []
        if team_id not in self._palettes:
            data = open("%s/%d.png" % (BADGE_DIR, team_id), "rb").read()
            _w, _h, _rgb, pal = real_badges.decode_scaled(data, self.size, self.background)
            self._palettes[team_id] = pal
        return self._palettes[team_id]
    def forget(self, team_id): pass
    def request(self, ids): pass
    def process_one(self): return False


sys.path.insert(0, REPO)
stub("pngdec", PNG=PNG)
stub("football_badges", BadgeCache=BadgeCache)
stub("presto", Presto=Presto)
stub("picovector", ANTIALIAS_BEST=0, PicoVector=Vec, Polygon=Poly, Transform=object)
stub("ntptime", settime=lambda: None)
stub("requests", get=lambda *a, **k: None)
stub("secrets", FOOTBALL_DATA_TOKEN="tok", FAVOURITE_TEAMS=[65], UTC_OFFSET=1,
     USE_24_HOUR=True, WIFI_SSID="", WIFI_PASSWORD="")

src = open(SRC).read().split("# --- Startup ---")[0]
fs = types.ModuleType("fs")
exec(compile(src, SRC, "exec"), fs.__dict__)

# --- scene setup -------------------------------------------------------------
NOW = int(datetime.datetime(2026, 9, 21, 12, 0, tzinfo=datetime.timezone.utc).timestamp())
fs.now_unix = lambda: NOW
fs.save_state = lambda: None
fs.tz_offset = 3600
fs.state["limit"], fs.state["used"] = 100, 13


# football-data.org team ids, matching the crests fetch_badges.py downloads.
TEAMS = {"Man City": 65, "Arsenal": 57, "Chelsea": 61, "Liverpool": 64,
         "Man United": 66, "Newcastle": 67, "Tottenham": 73,
         "Dortmund": 4, "Bayern": 5, "Real Madrid": 86, "Inter": 108,
         "PSG": 524}
FAV = TEAMS["Man City"]


def fx(fid, days, hour, home, away, league, status="TIMED", gh=None, ga=None, elapsed=None):
    ts = NOW + days * 86400
    ts = ts - (ts % 86400) + hour * 3600 - 3600   # local hour -> utc
    c = fs.civil_from_unix(ts + 3600)
    return {"id": fid, "ts": ts,
            "date": "%04d-%02d-%02dT%02d:%02d:00+01:00" % (c[0], c[1], c[2], c[3], c[4]),
            "status": status, "elapsed": elapsed, "league": league,
            "home": home, "home_id": TEAMS[home],
            "away": away, "away_id": TEAMS[away],
            "gh": gh, "ga": ga, "fav": FAV, "finished_at": None}


UPCOMING = [
    fx(2, 6, 15, "Man City", "Man United", "Premier League"),
    fx(3, 9, 20, "Dortmund", "Man City", "UEFA Champions League"),
    fx(4, 13, 12, "Man City", "Chelsea", "Premier League"),
    fx(5, 17, 17, "Newcastle", "Man City", "Premier League"),
    fx(6, 20, 19, "Man City", "Tottenham", "Premier League"),
]


def scene(name, setup):
    ops.clear()
    fs.schedule.clear()
    fs.live_fixtures.clear()
    fs.state["results"] = {}
    fs.status_message = ""
    setup()
    fs.draw_screen()
    return name, list(ops)


def upcoming_only():
    fs.schedule[FAV] = list(UPCOMING)


def live():
    fs.schedule[FAV] = list(UPCOMING)
    m = fx(1, 0, 12, "Man City", "Liverpool", "Premier League",
           status="IN_PLAY", gh=2, ga=1, elapsed=67)
    m["ts"] = NOW - 4200
    fs.live_fixtures[1] = m


def finished():
    fs.schedule[FAV] = list(UPCOMING)
    m = fx(1, 0, 12, "Tottenham", "Man City", "Premier League",
           status="FINISHED", gh=1, ga=3, elapsed=90)
    m["ts"] = NOW - 9000
    m["finished_at"] = NOW - 1800
    fs.state["results"] = {"1": m}


def half_time():
    fs.schedule[FAV] = list(UPCOMING)
    m = fx(1, 0, 12, "Man City", "PSG", "UEFA Champions League",
           status="PAUSED", gh=0, ga=0, elapsed=45)
    m["ts"] = NOW - 3000
    fs.live_fixtures[1] = m


def badges_loading():
    """First run: badges have not downloaded yet, so initials stand in."""
    rows = []
    for i, src in enumerate(UPCOMING[:5]):
        row = dict(src)
        # Point the opponent at an id with no cached badge.
        if row["home_id"] == FAV:
            row["away_id"] = 900000 + i
        else:
            row["home_id"] = 900000 + i
        rows.append(row)
    fs.schedule[FAV] = rows


def detail_scene(name, setup):
    """Render a detail view rather than the dashboard."""
    ops.clear()
    fs.schedule.clear()
    fs.live_fixtures.clear()
    fs.state["results"] = {}
    fs.status_message = ""
    fs.detail_scroll = 0
    fs.detail_h2h.clear()
    fs.view = "detail"
    fs.detail_fixture = setup()
    fs.draw_detail()
    fs.view = "dashboard"
    return name, list(ops)


H2H = [
    {"when": "8 Feb 26", "home": "LIV", "away": "MCI", "home_id": TEAMS["Liverpool"],
     "gh": 1, "ga": 2, "winner": "AWAY"},
    {"when": "9 Nov 25", "home": "MCI", "away": "LIV", "home_id": TEAMS["Man City"],
     "gh": 3, "ga": 0, "winner": "HOME"},
    {"when": "23 Feb 25", "home": "MCI", "away": "LIV", "home_id": TEAMS["Man City"],
     "gh": 0, "ga": 2, "winner": "AWAY"},
    {"when": "1 Dec 24", "home": "LIV", "away": "MCI", "home_id": TEAMS["Liverpool"],
     "gh": 2, "ga": 0, "winner": "HOME"},
    {"when": "10 Mar 24", "home": "LIV", "away": "MCI", "home_id": TEAMS["Liverpool"],
     "gh": 1, "ga": 1, "winner": "DRAW"},
    {"when": "25 Nov 23", "home": "MCI", "away": "LIV", "home_id": TEAMS["Man City"],
     "gh": 1, "ga": 1, "winner": "DRAW"},
]


def detail_upcoming():
    f = fx(600, 19, 21, "Liverpool", "Man City", "Premier League")
    f["matchday"] = 8
    fs.detail_h2h[600] = H2H
    return f


def detail_played():
    f = fx(601, 0, 18, "Man City", "Newcastle", "Premier League",
           status="FINISHED", gh=5, ga=3)
    f["gh_ht"], f["ga_ht"] = 3, 2
    f["matchday"] = 5
    f["referee"] = "Robert Jones"
    fs.detail_h2h[601] = H2H[:4]
    return f


def detail_goalless():
    f = fx(602, 0, 18, "Man City", "Chelsea", "Premier League",
           status="FINISHED", gh=0, ga=0)
    f["gh_ht"], f["ga_ht"] = 0, 0
    f["matchday"] = 6
    f["referee"] = "Michael Oliver"
    fs.detail_h2h[602] = []
    return f


def detail_live():
    f = fx(603, 0, 18, "Man City", "Dortmund", "UEFA Champions League",
           status="IN_PLAY", gh=2, ga=1, elapsed=67)
    f["gh_ht"], f["ga_ht"] = 1, 1
    f["matchday"] = 2
    f["referee"] = "Clement Turpin"
    fs.detail_h2h[603] = H2H[:3]
    return f


scenes = [scene("Upcoming only", upcoming_only),
          scene("Live match", live),
          scene("Half time", half_time),
          scene("Finished (held 24h)", finished),
          scene("Badges still loading", badges_loading),
          detail_scene("Detail: upcoming fixture", detail_upcoming),
          detail_scene("Detail: finished match", detail_played),
          detail_scene("Detail: goalless", detail_goalless),
          detail_scene("Detail: live match", detail_live)]


# --- SVG ---------------------------------------------------------------------
def rgb(pen): return "rgb(%d,%d,%d)" % pen


_badge_uris = {}


def badge_data_uri(team_id):
    """Run the real decoder/encoder, so the preview shows what the device draws."""
    if team_id not in _badge_uris:
        import base64
        real = real_badges
        data = open("%s/%d.png" % (BADGE_DIR, team_id), "rb").read()
        w, h, px, _pal = real.decode_scaled(data, 36, fs.PANEL_RGB)
        blob = real.encode_png(w, h, px)
        _badge_uris[team_id] = ("data:image/png;base64,"
                                + base64.b64encode(blob).decode())
    return _badge_uris[team_id]


def to_svg(op_list):
    header = ('<svg width="480" height="480" viewBox="0 0 480 480" '
              'xmlns="http://www.w3.org/2000/svg" '
              'font-family="Roboto, Arial, sans-serif">')
    out = [header]
    for op in op_list:
        if op[0] == "clear":
            out.append('<rect x="0" y="0" width="480" height="480" fill="%s"/>' % rgb(op[1]))
        elif op[0] == "shape":
            shape, pen = op[1], op[2]
            if shape is None:
                continue
            if shape[0] == "rect":
                _, x, y, w, h, r = shape
                out.append('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="%s"/>'
                           % (x, y, w, h, r, rgb(pen)))
            else:
                _, x, y, r = shape
                out.append('<circle cx="%g" cy="%g" r="%g" fill="%s"/>' % (x, y, r, rgb(pen)))
        elif op[0] == "badge":
            _, tag, x, y = op
            team_id = int(tag.strip().split(":")[1])
            out.append('<image x="%g" y="%g" href="%s"/>' % (x, y, badge_data_uri(team_id)))
        else:
            _, t, x, y, size, pen = op
            t = t.replace("&", "&amp;").replace("<", "&lt;")
            out.append('<text x="%g" y="%g" font-size="%g" font-weight="500" fill="%s">%s</text>'
                       % (x, y, size, rgb(pen), t))
    out.append("</svg>")
    return "".join(out)


cards = "".join(
    "<figure><figcaption>%s</figcaption>%s</figure>" % (name, to_svg(o))
    for name, o in scenes
)
html = """<!doctype html><meta charset="utf-8"><title>Presto football layout</title>
<style>
 body{background:#1b1d22;color:#cfd4dd;font:14px system-ui;margin:24px}
 .row{display:flex;gap:24px;flex-wrap:wrap}
 figure{margin:0}
 figcaption{margin-bottom:8px;font-size:13px;color:#8b94a4}
 svg{border-radius:14px;box-shadow:0 6px 24px #0008}
</style>
<div class="row">%s</div>""" % cards

open("layout.html", "w").write(html)
print("wrote layout.html with", len(scenes), "scenes")
