"""
Team badge cache for football_scores.py.

football-data.org serves team crests as 200x200 PNGs, and Presto's pngdec can
only scale images *up* - so a badge has to be shrunk before it can be drawn at
the ~36px this dashboard wants.

This module downloads a badge once, decodes it in Python, box-filters it down
to the target size, flattens it onto the panel colour, and re-encodes it as a
small PNG held in memory. pngdec then draws it from RAM at native size, which
is fast.

Nothing is written to the filesystem, deliberately. Presto's display driver
runs a render loop on core1, and a flash write has to lock that core out while
XIP is halted - a path the driver itself marks "not tight but endless
potentially". On hardware it deadlocks the board: the screen freezes and USB
stops responding. Pimoroni's own launcher sidesteps this by keeping its
scratch files on a PSRAM ramfs rather than flash.

Crest URLs arrive with the match data and are served from a plain CDN that
needs no token and counts against no quota, so re-fetching them after a reboot
costs nothing.
"""

import gc
import io

import requests

# PNG colour types.
_GREY = 0
_RGB = 2
_PALETTE = 3
_GREY_ALPHA = 4
_RGBA = 6

_CHANNELS = {_GREY: 1, _RGB: 3, _PALETTE: 1, _GREY_ALPHA: 2, _RGBA: 4}

_CRC_TABLE = []


def _crc_table():
    if not _CRC_TABLE:
        for n in range(256):
            c = n
            for _ in range(8):
                c = (0xEDB88320 ^ (c >> 1)) if (c & 1) else (c >> 1)
            _CRC_TABLE.append(c)
    return _CRC_TABLE


def _crc32(data, crc=0):
    table = _crc_table()
    crc ^= 0xFFFFFFFF
    for byte in data:
        crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def _adler32(data):
    a, b = 1, 0
    for byte in data:
        a = (a + byte) % 65521
        b = (b + a) % 65521
    return (b << 16) | a


def _inflate_stream(data):
    """A file-like reader over zlib-compressed data, streaming if we can."""
    try:
        import deflate
        return deflate.DeflateIO(io.BytesIO(data), deflate.ZLIB)
    except (ImportError, AttributeError):
        import zlib
        return io.BytesIO(zlib.decompress(data))


def _read_exact(stream, count):
    """Read exactly `count` bytes, or fewer only at genuine end of stream.

    A decompressing stream is free to hand back short reads, so asking once is
    not enough.
    """
    chunks = bytearray()
    while len(chunks) < count:
        part = stream.read(count - len(chunks))
        if not part:
            break
        chunks += part
    return chunks


def _paeth(a, b, c):
    p = a + b - c
    pa = p - a if p > a else a - p
    pb = p - b if p > b else b - p
    pc = p - c if p > c else c - p
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(line, prev, bpp, ftype):
    """Reverse a PNG scanline filter, in place."""
    if ftype == 0:
        return
    if ftype == 1:
        for i in range(bpp, len(line)):
            line[i] = (line[i] + line[i - bpp]) & 0xFF
    elif ftype == 2:
        for i in range(len(line)):
            line[i] = (line[i] + prev[i]) & 0xFF
    elif ftype == 3:
        for i in range(len(line)):
            left = line[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
    elif ftype == 4:
        for i in range(len(line)):
            left = line[i - bpp] if i >= bpp else 0
            upleft = prev[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + _paeth(left, prev[i], upleft)) & 0xFF
    else:
        raise ValueError("bad PNG filter {}".format(ftype))


def _read_chunks(data):
    """Yield (type, payload) for each PNG chunk."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    end = len(data)
    while pos + 8 <= end:
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = bytes(data[pos + 4:pos + 8])
        payload = data[pos + 8:pos + 8 + length]
        yield kind, payload
        pos += 12 + length          # length + type + data + crc


def decode_scaled(data, box, background):
    """Decode a PNG and box-filter it down to fit `box`, over `background`.

    Returns (width, height, RGB bytes), or raises ValueError.
    """
    width = height = 0
    depth = colour_type = interlace = 0
    palette = None
    trns = None
    idat = bytearray()

    for kind, payload in _read_chunks(data):
        if kind == b"IHDR":
            width = int.from_bytes(payload[0:4], "big")
            height = int.from_bytes(payload[4:8], "big")
            depth = payload[8]
            colour_type = payload[9]
            interlace = payload[12]
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"tRNS":
            trns = payload
        elif kind == b"IDAT":
            idat += payload
        elif kind == b"IEND":
            break

    # Badges come through as 8-bit almost always, but 16-bit RGBA does turn up.
    # Sub-byte depths (1/2/4) are rare enough to not be worth unpacking; the
    # caller falls back to drawing initials when we refuse one.
    if depth not in (8, 16):
        raise ValueError("unsupported bit depth {}".format(depth))
    if interlace:
        raise ValueError("interlaced PNGs not supported")
    if colour_type not in _CHANNELS:
        raise ValueError("unsupported colour type {}".format(colour_type))
    if colour_type == _PALETTE and palette is None:
        raise ValueError("palette PNG without a PLTE chunk")
    if not width or not height:
        raise ValueError("bad PNG size")

    channels = _CHANNELS[colour_type]
    # Bytes per channel. For 16-bit we read only the high byte of each sample,
    # which is the usual way to reduce 16-bit to 8-bit.
    bpc = depth // 8
    sample = channels * bpc
    stride = width * sample

    # Preserve the aspect ratio - badges are not all square.
    scale = min(box / width, box / height)
    out_w = max(1, min(box, int(width * scale + 0.5)))
    out_h = max(1, min(box, int(height * scale + 0.5)))

    # Box filter accumulators, in premultiplied form, plus how many source
    # pixels landed in each cell (the integer mapping does not divide evenly).
    cells = out_w * out_h
    acc_r = [0] * cells
    acc_g = [0] * cells
    acc_b = [0] * cells
    acc_a = [0] * cells
    acc_n = [0] * cells

    stream = _inflate_stream(bytes(idat))
    prev = bytearray(stride)
    line = bytearray(stride)

    for y in range(height):
        ftype = _read_exact(stream, 1)
        if not ftype:
            raise ValueError("PNG data ended early")
        chunk = _read_exact(stream, stride)
        if len(chunk) != stride:
            raise ValueError("PNG scanline short")
        line[:] = chunk
        _unfilter(line, prev, sample, ftype[0])

        row = (y * out_h) // height
        base = row * out_w

        for x in range(width):
            i = x * sample
            if colour_type == _RGBA:
                r, g, b, a = line[i], line[i + bpc], line[i + 2 * bpc], line[i + 3 * bpc]
            elif colour_type == _RGB:
                r, g, b, a = line[i], line[i + bpc], line[i + 2 * bpc], 255
            elif colour_type == _PALETTE:
                idx = line[i]
                p = idx * 3
                if p + 2 >= len(palette):
                    raise ValueError("palette index out of range")
                r, g, b = palette[p], palette[p + 1], palette[p + 2]
                a = trns[idx] if trns is not None and idx < len(trns) else 255
            elif colour_type == _GREY:
                r = g = b = line[i]
                a = 255
            else:                                   # grey + alpha
                r = g = b = line[i]
                a = line[i + bpc]

            cell = base + (x * out_w) // width
            acc_r[cell] += r * a
            acc_g[cell] += g * a
            acc_b[cell] += b * a
            acc_a[cell] += a
            acc_n[cell] += 1

        prev, line = line, prev

    bg_r, bg_g, bg_b = background
    out = bytearray(cells * 3)
    for cell in range(cells):
        alpha = acc_a[cell]
        o = cell * 3
        if alpha == 0:
            out[o] = bg_r
            out[o + 1] = bg_g
            out[o + 2] = bg_b
            continue
        # Un-premultiply to get the cell's colour, then blend it over the
        # background by its average coverage.
        r = acc_r[cell] // alpha
        g = acc_g[cell] // alpha
        b = acc_b[cell] // alpha
        cover = alpha / float(acc_n[cell] * 255)
        out[o] = int(r * cover + bg_r * (1.0 - cover) + 0.5)
        out[o + 1] = int(g * cover + bg_g * (1.0 - cover) + 0.5)
        out[o + 2] = int(b * cover + bg_b * (1.0 - cover) + 0.5)

    return out_w, out_h, bytes(out)


def _chunk(kind, payload):
    return (len(payload).to_bytes(4, "big") + kind + payload
            + _crc32(kind + payload).to_bytes(4, "big"))


def encode_png(width, height, rgb):
    """Write an 8-bit RGB PNG using uncompressed deflate blocks."""
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)                                # filter: none
        raw += rgb[y * stride:(y + 1) * stride]

    body = bytearray(b"\x78\x01")                    # zlib header, no compression
    pos = 0
    total = len(raw)
    while True:
        block = raw[pos:pos + 65535]
        pos += len(block)
        final = 1 if pos >= total else 0
        body.append(final)
        body += len(block).to_bytes(2, "little")
        body += (len(block) ^ 0xFFFF).to_bytes(2, "little")
        body += block
        if final:
            break
    body += _adler32(raw).to_bytes(4, "big")

    ihdr = (width.to_bytes(4, "big") + height.to_bytes(4, "big")
            + bytes((8, 2, 0, 0, 0)))                # 8-bit, truecolour
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", bytes(body)) + _chunk(b"IEND", b""))


def _quiet(*_args):
    pass


class BadgeCache:
    """Downloads, shrinks and keeps team badges in memory.

    Badges are held as ready-to-draw PNG bytes rather than files - see the
    module docstring for why the filesystem is off limits while the display is
    running. `limit` bounds how many are kept, since a season brings a lot of
    opponents through; the least recently requested are dropped first.
    """

    def __init__(self, size, background, log=None, limit=24):
        self.size = size
        self.background = background
        self.log = log or _quiet
        self.limit = limit
        self.ready = {}          # team id -> (width, height, PNG bytes)
        self.order = []          # team ids, least recently used first
        self.failed = set()
        self.pending = []

    def buffer(self, team_id):
        """The badge's PNG bytes, ready for pngdec's open_RAM, or None."""
        entry = self.ready.get(team_id)
        if entry is None:
            return None
        # Touch it so it survives trimming.
        if team_id in self.order:
            self.order.remove(team_id)
            self.order.append(team_id)
        return entry[2]

    def size_of(self, team_id):
        """The badge's pixel size, for centring it in its box."""
        entry = self.ready.get(team_id)
        return (entry[0], entry[1]) if entry else (self.size, self.size)

    def forget(self, team_id):
        """Drop a badge that would not draw, so we stop trying."""
        self.ready.pop(team_id, None)
        if team_id in self.order:
            self.order.remove(team_id)
        self.failed.add(team_id)

    def _trim(self):
        while len(self.order) > self.limit:
            oldest = self.order.pop(0)
            self.ready.pop(oldest, None)

    def request(self, wanted):
        """Queue any badges we do not have yet, as (team_id, crest_url)."""
        queued = [t for t, _ in self.pending]
        for team_id, url in wanted:
            if (team_id in self.ready or team_id in self.failed
                    or team_id in queued or not team_id or not url):
                continue
            self.pending.append((team_id, url))
            queued.append(team_id)

    def process_one(self):
        """Fetch and convert a single queued badge. Returns True if it worked.

        Deliberately one at a time so the dashboard keeps redrawing while a
        season's worth of opponents trickles in.
        """
        if not self.pending:
            return False
        team_id, url = self.pending.pop(0)

        response = None
        try:
            response = requests.get(url)
            if response.status_code != 200:
                raise ValueError("HTTP {}".format(response.status_code))
            data = response.content
        except (OSError, ValueError) as e:
            self.log("Badge download failed for", team_id, "-", e)
            self.failed.add(team_id)
            return False
        finally:
            if response is not None:
                response.close()

        try:
            width, height, rgb = decode_scaled(data, self.size, self.background)
            del data
            # bytearray, not bytes: pngdec takes a writable buffer, and it is
            # read just-in-time at decode, so this has to stay referenced.
            blob = bytearray(encode_png(width, height, rgb))
            del rgb
        except (ValueError, MemoryError) as e:
            self.log("Badge conversion failed for", team_id, "-", e)
            self.failed.add(team_id)
            gc.collect()
            return False
        finally:
            # Decoding a 200x200 PNG builds several thousand-element lists and
            # a full-size source buffer. Hand that back now rather than
            # leaving it for whenever the main loop next collects.
            gc.collect()

        self.ready[team_id] = (width, height, blob)
        if team_id in self.order:
            self.order.remove(team_id)
        self.order.append(team_id)
        self._trim()
        self.log("Badge ready:", team_id, "{}x{}".format(width, height))
        return True
