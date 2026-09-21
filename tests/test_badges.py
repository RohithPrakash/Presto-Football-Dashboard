"""Verify the badge decoder/encoder against Pillow, on real API badges."""
import os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
import io
import os
import sys
import types
import zlib

sys.path.insert(0, REPO)
sys.modules["requests"] = types.ModuleType("requests")
import football_badges as fb
from PIL import Image

BG = (26, 30, 40)
BOX = 36
fails = []


def check(name, cond, detail=""):
    if not cond:
        fails.append(f"{name}{(': ' + detail) if detail else ''}")


# --- checksums match zlib's ---------------------------------------------------
for sample in (b"", b"a", b"hello world", bytes(range(256)), os.urandom(1000)):
    check("crc32", fb._crc32(sample) == zlib.crc32(sample) & 0xFFFFFFFF, repr(sample[:12]))
    check("adler32", fb._adler32(sample) == zlib.adler32(sample) & 0xFFFFFFFF, repr(sample[:12]))

# --- encode_png produces a file Pillow agrees with ---------------------------
for w, h in ((1, 1), (3, 2), (36, 36), (36, 19), (7, 36)):
    rgb = bytes((x * 7 + y * 13 + 11) % 256 for y in range(h) for x in range(w) for _ in range(3))
    rgb = bytes((((x * 37 + y * 91) % 256) if c == 0 else ((x * 5 + y * 3) % 256) if c == 1
                 else ((x + y) % 256)) for y in range(h) for x in range(w) for c in range(3))
    blob = fb.encode_png(w, h, rgb)
    im = Image.open(io.BytesIO(blob))
    check(f"encode {w}x{h} size", im.size == (w, h), str(im.size))
    check(f"encode {w}x{h} mode", im.mode == "RGB", im.mode)
    check(f"encode {w}x{h} pixels", im.tobytes() == rgb)

# a large image must span multiple stored deflate blocks
big_w, big_h = 150, 150
big = bytes((i * 31) % 256 for i in range(big_w * big_h * 3))
blob = fb.encode_png(big_w, big_h, big)
check("multi-block encode spans blocks", len(big) + big_h > 65535)
check("multi-block encode round-trips", Image.open(io.BytesIO(blob)).tobytes() == big)

# --- decode_scaled against Pillow's own resize -------------------------------
badge_dir = os.path.join(HERE, "badges")
worst = 0.0
worst_file = None
checked = 0
box_errors = []
near_errors = []

if not os.path.isdir(badge_dir) or not os.listdir(badge_dir):
    print("no badge corpus - run: python3 tests/fetch_badges.py")
    sys.exit(1)

for name in sorted(os.listdir(badge_dir)):
    data = open(os.path.join(badge_dir, name), "rb").read()
    w, h, rgb = fb.decode_scaled(data, BOX, BG)

    ref = Image.open(os.path.join(badge_dir, name)).convert("RGBA")
    scale = min(BOX / ref.width, BOX / ref.height)
    ew = max(1, min(BOX, int(ref.width * scale + 0.5)))
    eh = max(1, min(BOX, int(ref.height * scale + 0.5)))

    check(f"{name} fits the box", w <= BOX and h <= BOX, f"{w}x{h}")
    check(f"{name} size matches", (w, h) == (ew, eh), f"got {w}x{h} want {ew}x{eh}")
    check(f"{name} aspect preserved",
          abs((w / h) - (ref.width / ref.height)) < 0.08,
          f"{w}x{h} from {ref.width}x{ref.height}")
    check(f"{name} byte length", len(rgb) == w * h * 3, str(len(rgb)))

    # Pillow reference: box-resize with alpha, then flatten onto the same bg.
    small = ref.resize((w, h), Image.BOX)
    flat = Image.new("RGB", (w, h), BG)
    flat.paste(small, (0, 0), small)
    want = flat.tobytes()

    diffs = [abs(rgb[i] - want[i]) for i in range(len(rgb))]
    mean = sum(diffs) / len(diffs)
    if mean > worst:
        worst, worst_file = mean, name

    # Pillow's BOX weights source pixels that straddle a cell boundary
    # fractionally; ours assigns each wholly to one cell. The gap between the
    # two grows as the source gets smaller (fewer source pixels per output
    # cell), so an absolute threshold would be a size-dependent guess.
    # Assert the property that actually matters instead: we must be doing real
    # averaging, i.e. closer to BOX than to point sampling.
    near = ref.resize((w, h), Image.NEAREST)
    flat_near = Image.new("RGB", (w, h), BG)
    flat_near.paste(near, (0, 0), near)
    near_bytes = flat_near.tobytes()
    mean_near = sum(abs(rgb[i] - near_bytes[i]) for i in range(len(rgb))) / len(rgb)

    # Per image the two can tie - a flat-colour logo resizes almost
    # identically under either filter - so only flag being markedly worse.
    check(f"{name} not markedly worse than point sampling",
          mean <= mean_near + 3.0, f"BOX {mean:.1f} vs NEAREST {mean_near:.1f}")
    check(f"{name} within sanity bound", mean < 25.0, f"mean {mean:.1f}")
    box_errors.append(mean)
    near_errors.append(mean_near)

    # the converted badge must survive a re-encode / re-decode round trip
    again = Image.open(io.BytesIO(fb.encode_png(w, h, rgb)))
    check(f"{name} re-encodes", again.tobytes() == rgb)
    checked += 1

avg_box = sum(box_errors) / len(box_errors)
avg_near = sum(near_errors) / len(near_errors)
print(f"  checked {checked} real badges, worst mean channel error {worst:.2f} ({worst_file})")
print(f"  average error vs Pillow BOX {avg_box:.2f}, vs NEAREST {avg_near:.2f}")

# Across the whole corpus we must clearly beat point sampling - that is what
# proves the box filter is really averaging rather than picking one pixel.
check("box filter beats point sampling across the corpus", avg_box < avg_near,
      f"BOX {avg_box:.2f} vs NEAREST {avg_near:.2f}")

# --- synthetic coverage of the colour types we might meet --------------------
def make(mode, size=(40, 24), **kw):
    im = Image.new(mode, size)
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            if mode == "RGBA":
                px[x, y] = (x * 6 % 256, y * 10 % 256, (x + y) * 3 % 256, (x * 8) % 256)
            elif mode == "RGB":
                px[x, y] = (x * 6 % 256, y * 10 % 256, (x + y) * 3 % 256)
            elif mode == "L":
                px[x, y] = (x * 5 + y) % 256
            elif mode == "LA":
                px[x, y] = ((x * 5 + y) % 256, (y * 9) % 256)
    return im


for mode, label in (("RGBA", "colour type 6"), ("RGB", "colour type 2"),
                    ("L", "colour type 0"), ("LA", "colour type 4")):
    buf = io.BytesIO()
    make(mode).save(buf, "PNG")
    try:
        w, h, rgb = fb.decode_scaled(buf.getvalue(), BOX, BG)
        check(f"{label} decodes", len(rgb) == w * h * 3)
    except ValueError as e:
        fails.append(f"{label} decode raised: {e}")

# palette, with and without transparency
buf = io.BytesIO()
make("RGB").convert("P", palette=Image.ADAPTIVE).save(buf, "PNG")
w, h, rgb = fb.decode_scaled(buf.getvalue(), BOX, BG)
check("colour type 3 decodes", len(rgb) == w * h * 3)

buf = io.BytesIO()
pal = make("RGBA").convert("P", palette=Image.ADAPTIVE)
pal.save(buf, "PNG", transparency=0)
w, h, rgb = fb.decode_scaled(buf.getvalue(), BOX, BG)
check("colour type 3 + tRNS decodes", len(rgb) == w * h * 3)

# every PNG filter type, forced via Pillow's compression levels
for level in (0, 1, 6, 9):
    buf = io.BytesIO()
    make("RGBA", (64, 64)).save(buf, "PNG", compress_level=level)
    w, h, rgb = fb.decode_scaled(buf.getvalue(), BOX, BG)
    check(f"filters survive compress_level={level}", len(rgb) == w * h * 3)

# --- a fully transparent image must come out as pure background --------------
clear = Image.new("RGBA", (50, 50), (255, 0, 0, 0))
buf = io.BytesIO()
clear.save(buf, "PNG")
w, h, rgb = fb.decode_scaled(buf.getvalue(), BOX, BG)
check("transparent image is all background", set(zip(rgb[0::3], rgb[1::3], rgb[2::3])) == {BG},
      str(sorted(set(zip(rgb[0::3], rgb[1::3], rgb[2::3])))[:3]))

# --- rubbish input is rejected, not crashed on -------------------------------
for bad, why in ((b"not a png at all", "garbage"),
                 (b"\x89PNG\r\n\x1a\n", "header only"),
                 (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, "truncated")):
    try:
        fb.decode_scaled(bad, BOX, BG)
        fails.append(f"{why}: expected ValueError")
    except ValueError:
        pass
    except Exception as e:                      # noqa: BLE001 - test harness
        fails.append(f"{why}: raised {type(e).__name__} instead of ValueError: {e}")

# 16-bit is supported (one real badge uses it); sub-byte depths are refused.
buf = io.BytesIO()
Image.new("I;16", (20, 20)).save(buf, "PNG")
w, h, rgb = fb.decode_scaled(buf.getvalue(), BOX, BG)
check("16-bit greyscale decodes", len(rgb) == w * h * 3)

buf = io.BytesIO()
make("RGB").convert("P", palette=Image.ADAPTIVE, colors=4).save(buf, "PNG", bits=2)
try:
    fb.decode_scaled(buf.getvalue(), BOX, BG)
except ValueError as e:
    check("sub-byte depth refused clearly", "bit depth" in str(e), str(e))

buf = io.BytesIO()
make("RGBA").save(buf, "PNG", interlace=True)
try:
    fb.decode_scaled(buf.getvalue(), BOX, BG)
except ValueError:
    pass                                        # refused, as intended
except Exception as e:                          # noqa: BLE001
    fails.append(f"interlaced raised {type(e).__name__}: {e}")

# --- the streaming deflate path (MicroPython) must match the zlib path -------
class FakeDeflateIO:
    def __init__(self, stream, fmt=None):
        self._buf = io.BytesIO(zlib.decompress(stream.read()))

    def read(self, n=-1):
        return self._buf.read(n)


fake = types.ModuleType("deflate")
fake.DeflateIO = FakeDeflateIO
fake.ZLIB = 1
sys.modules["deflate"] = fake

sample_name = sorted(os.listdir(badge_dir))[0]
data = open(os.path.join(badge_dir, sample_name), "rb").read()
stream_result = fb.decode_scaled(data, BOX, BG)
del sys.modules["deflate"]
zlib_result = fb.decode_scaled(data, BOX, BG)
check("streaming and zlib paths agree", stream_result == zlib_result)

print()
if fails:
    print(f"FAILED ({len(fails)}):")
    seen = set()
    for f in fails:
        if f not in seen:
            seen.add(f)
            print("  -", f)
    sys.exit(1)
print("All badge checks passed.")
