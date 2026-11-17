#!/usr/bin/env python3
"""Contract test: a sub-resource that never arrived must be reported (#11).

A fetch that fails used to be invisible. `blitz_net::Provider::fetch` swallows
the error through `tracing` (compiled out) and never calls the handler, so a
`404` left the node pending and the run exited `0` with a valid PNG of an empty
box -- indistinguishable from a render where the image loaded. `src/main.rs`
calls the provider's `fetch_with_callback` instead, which hands back the
`Result`, and reports every failure before exiting `4`.

The same wrapper caps the decoded size of an image. A 60-byte PNG header can
demand a multi-gigabyte allocation, and on a host without the headroom that
aborts with `134` -- indistinguishable from a segfault, naming no file.

Legs:

  a-control    200 PNG              -> paints, exit 0   (the pipeline works)
  b-404        missing              -> blank, exit 4, names the url and the status
  c-huge       PNG header 30000x30000 -> refused, exit 4, names the dimensions
  d-cap-off    the same, cap off    -> cap silent, body still not decodable: exit 4
  e-cap-low    real 1200x1200, cap 1 MP -> refused at 1.44 MP
  d2-cap-off-decodes  e's image, cap off -> paints, exit 0 (the cap WAS the refusal)
  f-large-ok   real 9000x9000, default cap -> paints, exit 0 (no false refusal)
  g-formats    header-only PNG/JPEG/GIF/WebP at 4000x4000, cap 1 MP
                                    -> each one names 4000x4000

Leg `c` is deliberately a header-only file: the cap refuses it before any
decoder runs, which is the property under test. That is also why `d` cannot be
its mirror image: with the cap off the body still has no pixels, so `d` asserts
the cap message is ABSENT while the decode failure is named, and `d2` supplies
the positive half on a body that really decodes. Leg `g` relies on the same
thing to gate the four header parsers without needing an encoder for each
format -- a parser that reads the wrong field states the wrong dimensions and
fails.

Usage:
    python3 tools/test_assets.py --bin /path/to/pageplate
    python3 tools/test_assets.py --bin /path/to/beta.8   # b, c, e and g fail
"""

import argparse
import os
import struct
import subprocess
import sys
import tempfile
import threading
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure import read_png  # noqa: E402

RED = (200, 30, 30)


def chunk(kind, payload):
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def solid_png(width=120, height=120, rgb=RED):
    """A real, decodable non-interlaced 8-bit RGB PNG of one flat colour."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def big_png(width, height, rgb=RED):
    """A decodable PNG of the given size, compressed row by row.

    Building this with `solid_png` would hold `height * width * 3` bytes at
    once; streaming the compressor keeps the peak at one row.
    """
    comp = zlib.compressobj(1)
    row = b"\x00" + bytes(rgb) * width
    parts = [comp.compress(row) for _ in range(height)]
    parts.append(comp.flush())
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", b"".join(parts))
        + chunk(b"IEND", b"")
    )


def header_png(w, h):
    """PNG magic plus an IHDR that claims `w`x`h`, and nothing decodable."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00", 9))
        + chunk(b"IEND", b"")
    )


def header_gif(w, h):
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00\x00\x00" + b";"


def header_webp(w, h):
    """VP8X: canvas size is stored as 24-bit values, minus one."""
    payload = (
        b"VP8X"
        + struct.pack("<I", 10)
        + b"\x00\x00\x00\x00"
        + (w - 1).to_bytes(3, "little")
        + (h - 1).to_bytes(3, "little")
    )
    riff = b"WEBP" + payload
    return b"RIFF" + struct.pack("<I", len(riff)) + riff


def header_jpeg(w, h):
    """SOI, an APP0 segment to prove the walker skips segments, then SOF0."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", h, w) + b"\x01\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


HEADER_ONLY = {
    "png": header_png,
    "jpeg": header_jpeg,
    "gif": header_gif,
    "webp": header_webp,
}


class Handler(BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):
        body = self.routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def painted(path):
    """True when the PNG contains any pixel that is not white."""
    _w, _h, channels, px = read_png(path)
    step = max(1, channels)
    for i in range(0, len(px), step):
        if any(px[i + c] < 250 for c in range(min(3, step))):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the pageplate binary")
    ap.add_argument("--keep", action="store_true", help="keep the temp dir")
    args = ap.parse_args()

    if not os.path.exists(args.bin):
        print(f"FAIL: no such binary: {args.bin}")
        return 1

    big = big_png(9000, 9000)
    medium = solid_png(1200, 1200)
    small = solid_png(120, 120)
    huge_header = header_png(30000, 30000)
    formats = {name: fn(4000, 4000) for name, fn in HEADER_ONLY.items()}

    Handler.routes = {
        "/ok.png": small,
        "/huge.png": huge_header,
        "/medium.png": medium,
        "/big.png": big,
    }
    for name, body in formats.items():
        Handler.routes[f"/fmt.{name}"] = body

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    tmp = tempfile.mkdtemp(prefix="assets-")
    legs = []

    def leg(name, src, want_painted, want_rc=0, want_stderr=(), no_stderr=(), env=None):
        html = os.path.join(tmp, f"{name}.html")
        out = os.path.join(tmp, f"{name}.png")
        with open(html, "w", encoding="utf-8") as fh:
            fh.write(
                '<!doctype html><html><body style="margin:0">'
                f'<img src="{src}" style="display:block;width:100px;height:100px">'
                "</body></html>"
            )
        run_env = dict(os.environ)
        run_env.update(env or {})
        proc = subprocess.run(
            [args.bin, html, out, "100", "100", "1", "--document"],
            capture_output=True,
            text=True,
            env=run_env,
        )
        if not os.path.exists(out):
            legs.append((name, False, f"no output file (rc={proc.returncode})"))
            return
        got = painted(out)
        problems = []
        if got != want_painted:
            problems.append(f"painted={got} wanted={want_painted}")
        if proc.returncode != want_rc:
            problems.append(f"rc={proc.returncode} wanted={want_rc}")
        for needle in want_stderr:
            if needle not in proc.stderr:
                problems.append(f"stderr lacks {needle!r}")
        for needle in no_stderr:
            if needle in proc.stderr:
                problems.append(f"stderr carries {needle!r}")
        legs.append((name, not problems, "; ".join(problems) or f"rc={proc.returncode}"))

    url = f"http://127.0.0.1:{port}"
    leg("a-control", f"{url}/ok.png", True)
    leg("b-404", f"{url}/missing.png", False, want_rc=4,
        want_stderr=("/missing.png", "404"))
    leg("c-huge", f"{url}/huge.png", False, want_rc=4,
        want_stderr=("30000x30000", "128 MP"))
    # `d` is the same header-only body as `c` with the cap off, so the property
    # is "the cap was the refusal, not something else" -- the cap message must be
    # gone. The body still cannot decode (it is a header and no pixels), and #27
    # made that loud: exit 4 naming the resource, where this used to be a blank
    # box at exit 0. Asserting `want_rc=0` here would pin the silent failure back
    # in place, so the leg asserts the two things that are true at once.
    leg("d-cap-off", f"{url}/huge.png", False, want_rc=4,
        want_stderr=("not a decodable image",),
        no_stderr=("128 MP",), env={"BLITZ_MAX_IMAGE_MP": "0"})
    leg("e-cap-low", f"{url}/medium.png", False, want_rc=4,
        want_stderr=("1200x1200", "1.4 MP"), env={"BLITZ_MAX_IMAGE_MP": "1"})
    # The positive half of the same property, which `d` cannot show because its
    # body never decodes: this is `e`'s exact image and the exact cap that
    # refused it, with the cap off. Painted, exit 0 -- so the refusal in `e` is
    # the cap and nothing else.
    leg("d2-cap-off-decodes", f"{url}/medium.png", True, want_rc=0,
        env={"BLITZ_MAX_IMAGE_MP": "0"})
    leg("f-large-ok", f"{url}/big.png", True)
    for name in HEADER_ONLY:
        leg(f"g-{name}", f"{url}/fmt.{name}", False, want_rc=4,
            want_stderr=("4000x4000",), env={"BLITZ_MAX_IMAGE_MP": "1"})

    server.shutdown()

    failed = 0
    for name, ok, detail in legs:
        print(f"{'ok  ' if ok else 'FAIL'} {name:14s} {detail}")
        failed += 0 if ok else 1

    print()
    if failed:
        print(f"{failed} of {len(legs)} legs FAIL")
        if not args.keep:
            subprocess.run(["rm", "-rf", tmp], check=False)
        return 1
    print(f"all {len(legs)} legs hold")
    if args.keep:
        print(f"kept: {tmp}")
    else:
        subprocess.run(["rm", "-rf", tmp], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
