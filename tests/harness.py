"""Load the pure-logic half of football_scores.py with hardware stubbed out."""
import os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
import sys
import types

SRC = os.path.join(REPO, "football_scores.py")


def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _Vec:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return lambda *a, **k: None
    def measure_text(self, t, *a, **k): return (0, 0, len(t) * 9, 20)


class _Poly:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return lambda *a, **k: None


class _Display:
    def get_bounds(self): return (480, 480)
    def create_pen(self, *a): return 0
    def __getattr__(self, n): return lambda *a, **k: None


class _Presto:
    def __init__(self, *a, **k): self.display = _Display()
    def __getattr__(self, n): return lambda *a, **k: None


class _PNG:
    def __init__(self, *a, **k): pass
    def open_RAM(self, buf): self.buf = buf
    def decode(self, *a, **k): pass


class _BadgeCache:
    """Pretends every badge is cached, so layout tests exercise the badge path."""
    def __init__(self, size, background, log=None, limit=24):
        self.size = size
        self.requested = []
        self.pending = []

    def buffer(self, team_id): return bytearray(b"\x89PNG fake")
    def size_of(self, team_id): return (self.size, self.size)
    def forget(self, team_id): pass
    def request(self, wanted): self.requested.extend(wanted)
    def process_one(self): return False


_stub("pngdec", PNG=_PNG)
_stub("football_badges", BadgeCache=_BadgeCache)
_stub("presto", Presto=_Presto)
_stub("picovector", ANTIALIAS_BEST=0, PicoVector=_Vec, Polygon=_Poly, Transform=_Vec)
_stub("ntptime", settime=lambda: None)
_stub("requests", get=lambda *a, **k: None)
_stub("secrets", FOOTBALL_DATA_TOKEN="tok", FAVOURITE_TEAMS=[65], UTC_OFFSET=1,
      USE_24_HOUR=True, WIFI_SSID="", WIFI_PASSWORD="")

src = open(SRC).read()
src = src.split("# --- Startup ---")[0]

mod = types.ModuleType("fs")
mod.__dict__["__name__"] = "fs"
exec(compile(src, SRC, "exec"), mod.__dict__)
sys.modules["fs"] = mod
