#!/usr/bin/env python3
"""Contract test: a redirected sub-resource must still paint.

`blitz_net::Provider` reports the FINAL url after an HTTP redirect, while
`Document::pending_images` registers nodes under the REQUESTED url. When the two
disagree the decoded image is dropped and the box stays blank -- with exit code
0 and nothing on stderr. `src/main.rs` wraps the provider to restore the
requested url; this test is the proof, and the direct-URL leg is the control
that proves the harness can paint a picture at all.

Three legs:

  a  direct 200  -> paints           (control: the pipeline works)
  b  302 -> 200  -> paints           (the fix; fails on any earlier binary)
  c  404         -> blank, exit 0    (documented, not a regression)

Usage:
    python3 tools/test_redirect.py --bin /path/to/html-render
    python3 tools/test_redirect.py --bin /path/to/old-binary   # a and c pass, b fails
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

RED = (255, 0, 0)


def solid_png(width=8, height=8, rgb=RED):
    """A minimal non-interlaced 8-bit RGB PNG of one flat colour."""

    def chunk(kind, payload):
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


PNG = solid_png()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/real.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)
        elif self.path == "/redir.png":
            self.send_response(302)
            self.send_header("Location", "/real.png")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args):
        pass


def painted(path):
    """True when the PNG contains any pixel that is not white.

    Honours the decoded channel count: a blank render comes back as a
    single-channel PNG, so stepping by three overruns the buffer.
    """
    _w, _h, channels, px = read_png(path)
    step = max(1, channels)
    for i in range(0, len(px), step):
        if any(px[i + c] < 250 for c in range(min(3, step))):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the html-render binary")
    ap.add_argument("--keep", action="store_true", help="keep the temp dir")
    args = ap.parse_args()

    if not os.path.exists(args.bin):
        print(f"FAIL: no such binary: {args.bin}")
        return 1

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    tmp = tempfile.mkdtemp(prefix="redirect-")
    legs = []

    def leg(name, src, want_painted):
        html = os.path.join(tmp, f"{name}.html")
        out = os.path.join(tmp, f"{name}.png")
        with open(html, "w", encoding="utf-8") as fh:
            fh.write(
                '<!doctype html><html><body style="margin:0">'
                f'<img src="{src}" style="display:block;width:100px;height:100px">'
                "</body></html>"
            )
        proc = subprocess.run(
            [args.bin, html, out, "100", "100", "1", "--document"],
            capture_output=True,
            text=True,
        )
        if not os.path.exists(out):
            legs.append((name, False, f"no output file (rc={proc.returncode})"))
            return
        got = painted(out)
        ok = got == want_painted
        legs.append(
            (
                name,
                ok,
                f"painted={got} wanted={want_painted} rc={proc.returncode}",
            )
        )

    leg("a-direct", f"http://127.0.0.1:{port}/real.png", True)
    leg("b-redirect", f"http://127.0.0.1:{port}/redir.png", True)
    leg("c-404", f"http://127.0.0.1:{port}/missing.png", False)

    server.shutdown()

    failed = 0
    for name, ok, detail in legs:
        print(f"{'ok  ' if ok else 'FAIL'} {name:12s} {detail}")
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
