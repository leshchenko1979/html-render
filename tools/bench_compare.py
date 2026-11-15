#!/usr/bin/env python3
"""Compare two PNGs of the same document, and read dimensions without decoding.

`fidelity()` answers the question the README's fidelity claim asks: how much of
the picture differs from the browser's own screenshot, and by how much. Four
figures, computed from the two files and nothing else, over **every** pixel:

  over16_pct       share of pixels with any channel off by more than 16/255
  mean_diff        mean per-channel absolute difference, 0-255
  ink_ref_pct      share of the reference's pixels that are not near-white
  ink_engine_pct   the same for the engine: a dropped stylesheet collapses it

Two decoders, one definition. Pillow does the work in C when it is installed
(`tools/requirements.txt` declares it for this harness); without it the stdlib
decoder in `measure.py` produces the same pixels and the same numbers, just
slowly. The decoder used is written into the result, because a figure whose
instrument is unstated cannot be re-derived.

The metric is deliberately **not** sampled. A sampled percentage is a different
statistic from a full one, and two hosts disagreeing about a sampling step would
disagree about the answer to the same question.

Both images are composited over white first, so an engine writing transparent
regions is not credited with a difference the compositor would have removed.
"""

import struct

import measure  # stdlib PNG decoder in this directory

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError:  # pragma: no cover - the fallback has its own leg
    Image = None

WHITE = (255, 255, 255)


def ihdr(path):
    """(width, height) straight out of the IHDR chunk, without decoding pixels."""
    with open(path, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError("%s: not a PNG with a leading IHDR" % path)
    return struct.unpack(">II", head[16:24])


def _pillow_rgb(path):
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, WHITE + (255,))
        im = Image.alpha_composite(bg, im)
    return im.convert("RGB")


def _stdlib_rgb(path):
    """(width, height, channels, buffer) from measure.read_png, un-composited."""
    w, h, ch, buf = measure.read_png(path)
    if ch not in (3, 4):
        raise ValueError("%s: %d channels, want RGB or RGBA" % (path, ch))
    return w, h, ch, buf


def _fidelity_pillow(ref, eng):
    a = _pillow_rgb(ref)
    b = _pillow_rgb(eng)
    if a.size != b.size:
        raise ValueError("cannot compare %s against %s" % (a.size, b.size))

    diff = ImageChops.difference(a, b)
    # Per-channel threshold, then the union of the three masks: a pixel counts
    # once however many of its channels are over the line.
    masks = [band.point(lambda v: 255 if v > 16 else 0) for band in diff.split()]
    union = masks[0]
    for m in masks[1:]:
        union = ImageChops.lighter(union, m)
    over = union.histogram()[255]
    mean = sum(ImageStat.Stat(diff).mean) / 3.0

    def ink(im):
        bands = [band.point(lambda v: 255 if v < 250 else 0) for band in im.split()]
        u = bands[0]
        for m in bands[1:]:
            u = ImageChops.lighter(u, m)
        return u.histogram()[255]

    px = a.size[0] * a.size[1]
    return {
        "width": a.size[0],
        "height": a.size[1],
        "decoder": "pillow",
        "pixels": px,
        "over16_pct": round(100.0 * over / px, 4),
        "mean_diff": round(mean, 4),
        "ink_ref_pct": round(100.0 * ink(a) / px, 4),
        "ink_engine_pct": round(100.0 * ink(b) / px, 4),
    }


def _fidelity_stdlib(ref, eng):
    aw, ah, ach, abuf = _stdlib_rgb(ref)
    bw, bh, bch, bbuf = _stdlib_rgb(eng)
    if (aw, ah) != (bw, bh):
        raise ValueError("cannot compare %dx%d against %dx%d" % (aw, ah, bw, bh))

    stride_a = aw * ach
    stride_b = bw * bch
    alpha_a = ach == 4
    alpha_b = bch == 4
    over = 0
    total = 0
    ink_a = 0
    ink_b = 0
    px = aw * ah

    for y in range(ah):
        ra = abuf[y * stride_a:(y + 1) * stride_a]
        rb = bbuf[y * stride_b:(y + 1) * stride_b]
        ar, ag, ab = ra[0::ach], ra[1::ach], ra[2::ach]
        br, bg, bb = rb[0::bch], rb[1::bch], rb[2::bch]
        if alpha_a:
            aa = ra[3::ach]
        if alpha_b:
            ba = rb[3::bch]
        for i in range(aw):
            r1, g1, b1 = ar[i], ag[i], ab[i]
            r2, g2, b2 = br[i], bg[i], bb[i]
            if alpha_a and aa[i] != 255:
                a1 = aa[i]
                r1 = (r1 * a1 + 255 * (255 - a1)) // 255
                g1 = (g1 * a1 + 255 * (255 - a1)) // 255
                b1 = (b1 * a1 + 255 * (255 - a1)) // 255
            if alpha_b and ba[i] != 255:
                a2 = ba[i]
                r2 = (r2 * a2 + 255 * (255 - a2)) // 255
                g2 = (g2 * a2 + 255 * (255 - a2)) // 255
                b2 = (b2 * a2 + 255 * (255 - a2)) // 255
            dr, dg, db = r1 - r2, g1 - g2, b1 - b2
            if dr < 0:
                dr = -dr
            if dg < 0:
                dg = -dg
            if db < 0:
                db = -db
            total += dr + dg + db
            if dr > 16 or dg > 16 or db > 16:
                over += 1
            if r1 < 250 or g1 < 250 or b1 < 250:
                ink_a += 1
            if r2 < 250 or g2 < 250 or b2 < 250:
                ink_b += 1

    return {
        "width": aw,
        "height": ah,
        "decoder": "stdlib",
        "pixels": px,
        "over16_pct": round(100.0 * over / px, 4),
        "mean_diff": round(total / (3.0 * px), 4),
        "ink_ref_pct": round(100.0 * ink_a / px, 4),
        "ink_engine_pct": round(100.0 * ink_b / px, 4),
    }


def fidelity(ref_path, eng_path, decoder="auto"):
    """Compare `eng_path` against `ref_path`. `decoder` is auto|pillow|stdlib."""
    if decoder == "auto":
        decoder = "pillow" if Image is not None else "stdlib"
    if decoder == "pillow":
        if Image is None:
            raise ValueError("pillow requested but not installed")
        return _fidelity_pillow(ref_path, eng_path)
    if decoder == "stdlib":
        return _fidelity_stdlib(ref_path, eng_path)
    raise ValueError("unknown decoder %r (want auto, pillow or stdlib)" % decoder)
