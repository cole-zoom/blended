"""Minimal PNG read/write. No third-party imaging library.

`blended` declares three dependencies and none of them decode images, so these tools carry
their own codec rather than pulling in Pillow for what amounts to unfiltering scanlines and
deflating them again. It handles what these tools actually produce: 8-bit, non-interlaced,
grey/RGB/RGBA/palette in, RGB or RGBA out.

If this ever needs to grow — 16-bit, interlacing, colour profiles — that is the signal to add
Pillow instead of extending this.
"""

from __future__ import annotations

import struct
import zlib


def decode(path):
    b = open(path, "rb").read()
    assert b[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, pal, trns = 8, b"", None, None
    while pos < len(b):
        ln = struct.unpack(">I", b[pos:pos+4])[0]
        typ = b[pos+4:pos+8]
        data = b[pos+8:pos+8+ln]
        if typ == b"IHDR":
            w, h, depth, color, comp, filt, inter = struct.unpack(">IIBBBBB", data)
            assert depth == 8, "only 8-bit supported, got %d" % depth
            assert inter == 0, "interlaced PNG unsupported"
        elif typ == b"PLTE": pal = data
        elif typ == b"tRNS": trns = data
        elif typ == b"IDAT": idat += data
        elif typ == b"IEND": break
        pos += 12 + ln
    nch = {0:1, 2:3, 3:1, 4:2, 6:4}[color]
    raw = zlib.decompress(idat)
    stride = w * nch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = raw[p]; p += 1
        line = bytearray(raw[p:p+stride]); p += stride
        if f == 1:
            for i in range(nch, stride): line[i] = (line[i] + line[i-nch]) & 255
        elif f == 2:
            for i in range(stride): line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i-nch] if i >= nch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i-nch] if i >= nch else 0
                c = prev[i-nch] if i >= nch else 0
                bb = prev[i]
                pa, pb, pc = abs(bb-c), abs(a-c), abs(a+bb-2*c)
                pr = a if (pa <= pb and pa <= pc) else (bb if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out[y*stride:(y+1)*stride] = line
        prev = line
    return w, h, nch, color, bytes(out), pal, trns

def alpha_at(w, nch, color, data, pal, trns, x, y):
    i = (y * w + x) * nch
    if color == 6: return data[i+3]
    if color == 4: return data[i+1]
    if color == 3 and trns:
        idx = data[i]
        return trns[idx] if idx < len(trns) else 255
    return 255


def encode(path, width, height, rows, alpha=False, level=6):
    """Write an 8-bit PNG from pre-filtered scanlines.

    `rows` must already carry a leading filter byte per scanline — every caller here writes
    filter 0 (none), because the images are smooth gradients where filtering buys little and
    the loop to apply it costs more than the bytes it saves.

    `level` 1 is worth using for intermediates that are about to be re-encoded to video; the
    compression time exceeds what the disk cares about.
    """
    def chunk(tag, payload):
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    png = (b"\x89PNG\r\n\x1a\x0a"[:8]
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2,
                                        0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(rows), level))
           + chunk(b"IEND", b""))
    import pathlib
    pathlib.Path(path).write_bytes(png)
    return width, height


def rgb_rows(pixels, width, height):
    """Wrap a flat RGB buffer in per-scanline filter bytes."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        rows += pixels[y * width * 3:(y + 1) * width * 3]
    return rows
