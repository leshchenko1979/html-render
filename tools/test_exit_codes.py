#!/usr/bin/env python3
"""Pins the exit-code contract documented in docs/USAGE.md §Exit codes.

A CLI that exits `0` on a typo is worse than one that crashes: `--documnet`
rendered a fragment of a full document and wrote a plausible PNG of the wrong
thing, with nothing on stderr. Every code below is documented, so every code is
tested here — one scripted pass, against the binary CI just built.

    python3 tools/test_exit_codes.py --bin target/release/pageplate

Exit 0 = the contract holds. Exit 1 = it does not (the message says which leg).
Run it against the published r2 binary and most legs fail — the point being that
a test which cannot observe the defect it was written for is decoration.

The summary counts both numbers at runtime: failed ASSERTIONS and the LEGS that
ran. They differ, because one leg can report several failures (the `--help` leg
checks an exit code and a handful of stdout needles). Neither total is written
by hand here, because a hand-written total is the same class of lie as a
hand-written measurement: it stays put when a leg is added or deleted.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time

# One box, one boxless pair, one empty document. Small enough that the whole
# pass is a few hundred milliseconds and can afford to run on every push.
DOC = """<!doctype html><html><body style="margin:0">
<div id="a" style="width:40px;height:20px;background:#c00"></div>
<p>text<br>more<wbr>tail</p>
</body></html>
"""

EMPTY = """<!doctype html><html><body><p>no divs here</p></body></html>
"""

# Taller than any canvas the renderer can paint (65532 px). `--full` now paints
# such a page in bands, so this document is what proves BOTH halves of the
# contract: with banding on it renders whole, and with `BLITZ_BAND_MIB=0` the
# single canvas is refused BEFORE painting, so a caller never finds a partial
# dump.
TALL = """<!doctype html><html><body style="margin:0">
<div style="height:70000px"></div></body></html>
"""

def png_height(path):
    """IHDR height, read from the header rather than from a decoder.

    Deliberately not a full decode: the leg asserts the canvas the renderer
    declared, and a decoder would also mask a truncated stream.
    """
    with open(path, "rb") as fh:
        head = fh.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(head[20:24], "big")


def run(binary, argv, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    p = subprocess.run([binary] + argv, capture_output=True, text=True, timeout=120,
                       env=full_env)
    return p.returncode, p.stdout, p.stderr


def black_hole():
    """A local TCP server that accepts and then says nothing.

    The asset-deadline leg needs a fetch that BLOCKS. A non-routable address
    looks like it does, but it is a bet on network failure MODE: while the
    kernel is still retrying, connect hangs and the deadline trips; the moment
    an ICMP unreachable is cached, connect fails in milliseconds, the image
    errors out instead of blocking, the deadline never arrives, and the leg
    reports a product defect that is really a routing coincidence. Measured
    once against the published beta.6 -- layout_ms=13, rc=0, no warning -- and
    21/21 passed on the same binary a minute later.

    This server accepts the connection and never writes a byte, so the client
    blocks for exactly as long as its deadline allows. No routing, no DNS, no
    external host: the leg is a property of the renderer, not of the network.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    held = []

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            held.append(conn)  # keep it open, never respond

    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


def png_1x1():
    """A valid 1x1 RGBA PNG, built here rather than shipped as a fixture.

    The legs below need a body the decoder ACCEPTS, so a failure cannot be the
    fixture's fault, and they must not depend on a file another test can move.
    """
    import struct
    import zlib

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    raw = b"\x00" + b"\xff\x00\x00\xff"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def counting_server():
    """A local HTTP server that answers two bodies and logs every request.

    `--no-network` is only proven by ZERO requests reaching a server that WOULD
    have answered: a refusal that still connects has refused nothing, and the
    exit code alone cannot tell the two apart. The counter is the instrument, so
    the legs assert on it.

    `*.png` gets a real PNG and everything else `text/plain`, which is what the
    non-image-200 leg (#27) needs: same server, same 200, different body.
    """
    import http.server

    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path.endswith(".png"):
                body, ctype = png_1x1(), "image/png"
            else:
                body, ctype = b"hello world", "text/plain"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the renderer binary")
    args = ap.parse_args()
    binary = os.path.abspath(args.bin)
    fails = []
    legs = 0

    def check(name, argv, want_rc, want_err=None, env=None):
        nonlocal legs
        legs += 1
        rc, out, err = run(binary, argv, env=env)
        why = []
        if rc != want_rc:
            why.append("rc=%d want %d" % (rc, want_rc))
        if want_err and want_err not in err:
            why.append("stderr lacks %r" % want_err)
        if why:
            fails.append("%s: %s | stderr=%r" % (name, "; ".join(why), err[:200]))
            print("FAIL %-28s %s" % (name, "; ".join(why)))
        else:
            print("ok   %-28s rc=%d" % (name, rc))

    with tempfile.TemporaryDirectory() as tmp:
        doc = os.path.join(tmp, "doc.html")
        empty = os.path.join(tmp, "empty.html")
        tall = os.path.join(tmp, "tall.html")
        for path, body in ((doc, DOC), (empty, EMPTY), (tall, TALL)):
            with open(path, "w") as fh:
                fh.write(body)

        # 0 — the happy path, in both modes.
        out_png = os.path.join(tmp, "one.png")
        check("0 success: single image", [doc, out_png, "120", "60"], 0)
        if not os.path.isfile(out_png) or os.path.getsize(out_png) == 0:
            fails.append("0 success: single image wrote no PNG")
            print("FAIL 0 success: single image wrote no PNG")
        check("0 success: clip mode",
              ["--clip", doc, os.path.join(tmp, "clips"), "120", "60", "2", "div"], 0)

        # 0 — `--help` and `--version` are the two flags a reader types before
        # reading a README, and both used to exit 2 with "unknown flag". A usage
        # request is not an error: `docker run image --help` and every
        # `cmd --help | head` pipeline read a nonzero here as a broken binary.
        check("0 --help", ["--help"], 0)
        check("0 -h", ["-h"], 0)
        check("0 --version", ["--version"], 0)
        check("0 -V", ["-V"], 0)
        # 0 — the same two flags in TRAILING position. They used to be honoured
        # only at argv[1], so `render in.html out.png 794 900 --help` fell
        # through to the arity check and exited 2: the flag looked broken from
        # the one place a reader is most likely to type it.
        check("0 --help trailing", [doc, os.path.join(tmp, "t.png"), "120", "60", "--help"], 0)
        check("0 -V trailing", [doc, os.path.join(tmp, "t2.png"), "120", "60", "-V"], 0)
        rc, out, _err = run(binary, ["--help"])
        for needle in ("USAGE:", "--clip", "--per-element", "EXIT CODES:", "101", "4", "5"):
            if needle not in out:
                fails.append("--help stdout lacks %r" % needle)
                print("FAIL --help stdout lacks %r" % needle)
        if not out.startswith("pageplate "):
            fails.append("--help does not start with the program name and version")
            print("FAIL --help does not start with the program name and version")

        # 0 — every flag the parser accepts is documented, and the doc invents
        # none. AGENTS.md used to forbid a flags table anywhere but `--help`,
        # on the grounds that a third copy drifts unguarded; that is exactly
        # what happened to `--scroll`, which the parser accepts, `--help` omits
        # and no document named. The table in docs/USAGE.md is now the copy
        # with a leg behind it.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "src", "main.rs"), encoding="utf-8") as fh:
            source = fh.read()
        block = re.search(r"if !matches!\(\s*a\.as_str\(\),(.*?)\)\s*\{",
                          source, re.S)
        if not block:
            fails.append("cannot find the accepted-flag list in src/main.rs")
            print("FAIL flags: accepted-flag list not found")
        else:
            parser_flags = set(re.findall(r'"(--[a-z-]+)"', block.group(1)))
            with open(os.path.join(root, "docs", "USAGE.md"), encoding="utf-8") as fh:
                usage = fh.read()
            section = usage.split("## Flags", 1)[-1].split("## Environment", 1)[0]
            doc_flags = set(re.findall(r"(--[a-z][a-z-]+)", section))
            help_flags = set(re.findall(r"--[a-z][a-z-]+", out))
            for name, missing in (("parser", parser_flags - doc_flags),
                                  ("--help", help_flags - doc_flags),
                                  ("USAGE.md", doc_flags - parser_flags
                                   - {"--help", "--version"})):
                for flag in sorted(missing):
                    fails.append("%s names %s, which docs/USAGE.md does not "
                                 "document in its Flags table" % (name, flag)
                                 if name != "USAGE.md" else
                                 "docs/USAGE.md documents %s, which the parser "
                                 "does not accept" % flag)
                    print("FAIL flags: %s vs docs/USAGE.md: %s" % (name, flag))
            if not (parser_flags - doc_flags or help_flags - doc_flags
                    or doc_flags - parser_flags - {"--help", "--version"}):
                print("ok   flags: %d accepted, %d documented, none hidden"
                      % (len(parser_flags), len(doc_flags)))

        # 0 — a missing output directory is created, in BOTH modes. Clip mode
        # always did; the single-image path panicked 101 instead, so the same
        # argument shape gave two different answers.
        nested = os.path.join(tmp, "deep", "nested", "one.png")
        check("0 single image creates outdir", [doc, nested, "120", "60"], 0)
        if not os.path.isfile(nested):
            fails.append("single-image mode reported success without writing %s" % nested)
            print("FAIL single image creates outdir wrote nothing")

        # 2 — arity. `scale` is optional, so four positionals is the floor.
        check("2 usage: too few args", [doc, os.path.join(tmp, "x.png")], 2,
              want_err="usage: pageplate")

        # 2 — an unknown flag. This leg is the regression: it used to exit 0 and
        # render anyway, so a typo silently produced the wrong picture.
        check("2 usage: unknown flag", [doc, os.path.join(tmp, "y.png"), "120", "60", "1", "--bogus"], 2,
              want_err="unknown flag")

        # 2 — `--clip` is only a mode when it leads, because it shifts the
        # positionals by one. Anywhere else it would be read as a stray flag.
        check("2 usage: --clip not first", [doc, "--clip", os.path.join(tmp, "z"), "120", "60", "2", "div"], 2,
              want_err="must be the first argument")

        # 3 — selector matched nothing.
        check("3 no match", ["--clip", empty, os.path.join(tmp, "nm"), "120", "60", "1", "div"], 3,
              want_err="no match for selector")

        # 3 — matched, but every match is boxless. Same code as no match by
        # design: both mean "nothing to write".
        check("3 all matches boxless", ["--clip", doc, os.path.join(tmp, "bl"), "120", "60", "1", "br, wbr"], 3,
              want_err="no layout box")

        # 3 — matched, but every match has a ZERO-AREA box. This is the leg
        # that was missing: a box that exists but paints nothing (`script`,
        # `style`, `template`, `display:none`) reported 0x0, the `.max(1)`
        # clamped it to a 1x1 canvas, and the run exited 0 having written a
        # 68-byte PNG. Same "nothing to write" as no match, so the same code —
        # the defect was the success code, not the classification (#38).
        void = os.path.join(tmp, "void.html")
        with open(void, "w") as fh:
            fh.write("<!doctype html><html><head><style>p{color:red}</style></head>"
                     "<body style=\"margin:0\"><script>var x=1</script>"
                     '<div style="display:none;width:40px;height:20px"></div>'
                     "<p>visible</p></body></html>\n")
        check("3 all matches zero-area", ["--clip", void, os.path.join(tmp, "za"), "120", "60", "1", "script"], 3,
              want_err="non-zero area")
        check("3 display:none zero-area",
              ["--clip", void, os.path.join(tmp, "za2"), "120", "60", "1", "div"], 3,
              want_err="non-zero area")
        # A selector mixing a paintless match with a real one must still dump the
        # real one: the skip is per-element, and turning one 0x0 match into a
        # fatal error would destroy the other 300 boxes a `*` selector finds.
        check("0 zero-area skipped, visible kept",
              ["--clip", void, os.path.join(tmp, "za3"), "120", "60", "1", "script, p"], 0,
              want_err="zero-area box")

        # 2 — a single-dash argument. `-q` in a trailing position used to be
        # collected as a surplus positional and dropped without a word, so a
        # typo'd short flag read as "it ran fine"; `-x` in the html slot panicked
        # on a file literally named `-x` instead of naming the flag. `-h` and
        # `-V` are handled before this point and exit 0, so they are not caught
        # by it — proven by the two `0 -h` / `0 -V` legs above (#39).
        check("2 short flag leading", ["-x", os.path.join(tmp, "sf.png"), "120", "60"], 2,
              want_err="short flags other than -h and -V")
        check("2 short flag trailing", [doc, os.path.join(tmp, "sf2.png"), "120", "60", "1", "-q"], 2,
              want_err="short flags other than -h and -V")

        # 2 — a surplus positional. `pageplate in.html out.png 1280 720 2 7`
        # rendered at exit 0 and dropped the `7`, which a caller cannot tell
        # from a value that was honoured (#39).
        check("2 surplus positional", [doc, os.path.join(tmp, "sp.png"), "120", "60", "1", "7"], 2,
              want_err="unexpected extra argument")

        # 2 — the output path IS the input path. The document is read into
        # memory before the write, so `pageplate t.html t.html` used to SUCCEED:
        # exit 0, a plausible PNG, and the source gone (#36). Both sides are
        # resolved rather than compared lexically, so `./t.html`, a symlink and
        # a `..` segment all have to be refused too — and the input has to still
        # be there afterwards, which is the assertion that matters: the exit code
        # is the fix, the surviving file is the point.
        guard = os.path.join(tmp, "guard.html")
        with open(guard, "w") as fh:
            fh.write(DOC)
        before = open(guard, "rb").read()
        check("2 out==in", [guard, guard, "120", "60"], 2, want_err="over the input")
        check("2 out==in relative", [guard, os.path.join(tmp, ".", "guard.html"), "120", "60"], 2,
              want_err="over the input")
        dotdot = os.path.join(tmp, "sub", "..", "guard.html")
        os.makedirs(os.path.join(tmp, "sub"), exist_ok=True)
        check("2 out==in dotdot", [guard, dotdot, "120", "60"], 2, want_err="over the input")
        link = os.path.join(tmp, "guard_link.png")
        os.symlink(guard, link)
        check("2 out==in symlink", [guard, link, "120", "60"], 2, want_err="over the input")
        if open(guard, "rb").read() != before:
            fails.append("out==in: the input was modified despite the refusal")
            print("FAIL out==in: the input was modified despite the refusal")

        # 0 — a NEIGHBOUR of the input is not the input. The refusal above must
        # not become "refuses anything in the same directory", so this leg pins
        # the boundary the other three cannot see.
        check("0 out beside input", [guard, os.path.join(tmp, "beside.png"), "120", "60"], 0)

        # 2 — `--per-element` is a clip-mode reading and nothing else reads it.
        # It used to be accepted in single-image mode and silently ignored, so
        # the flag looked honoured while the output was the plain full-document
        # render — the same "did something else, said nothing" class as a typo.
        # `--scroll` is the retired spelling, kept as a hidden alias: an alias
        # that silently stops being honoured is that same defect wearing a
        # compatibility story, so both spellings are checked.
        check("2 --per-element outside --clip", [doc, os.path.join(tmp, "sc.png"), "120", "60", "1", "--per-element"], 2,
              want_err="only applies to --clip")
        check("2 --scroll alias outside --clip", [doc, os.path.join(tmp, "sc2.png"), "120", "60", "1", "--scroll"], 2,
              want_err="only applies to --clip")

        # 2 — `--full` is a single-image reading: it makes the canvas the
        # document's own height. Clip mode already derives its canvas from the
        # matched elements, so the flag would name nothing there. Accepting it
        # and ignoring it is the same defect as `--per-element` above, mirrored.
        check("2 --full with --clip", ["--clip", doc, os.path.join(tmp, "fu"), "120", "60", "1", "div", "--full"], 2,
              want_err="only applies to single-image")

        # 5 — the canvas the renderer cannot paint. Both ceilings are measured
        # on a released binary: 65408 wide and 65532 tall render, one pixel past
        # each panics inside vello with an out-of-range index and leaves a
        # partial dump behind. Refusing up front turns that into one documented
        # code. The assertion is not the number, it is that NOTHING is written:
        # a caller reading rc=5 must not find half a file on disk.
        wide_png = os.path.join(tmp, "wide.png")
        check("5 canvas too wide", [doc, wide_png, "70000", "60", "1"], 5,
              want_err="exceeds the renderer limit")
        if os.path.exists(wide_png):
            fails.append("5 canvas too wide wrote a file before refusing")
            print("FAIL 5 canvas too wide wrote a file before refusing")
        # With banding off, `--full` is back to one canvas of the document's own
        # height, so the ceiling bites and nothing is written.
        tall_off = os.path.join(tmp, "tall-off.png")
        check("5 canvas too tall (--full, banding off)",
              [tall, tall_off, "120", "60", "1", "--full"], 5,
              want_err="set BLITZ_BAND_MIB above 0", env={"BLITZ_BAND_MIB": "0"})
        if os.path.exists(tall_off):
            fails.append("5 canvas too tall (--full, banding off) wrote a file before refusing")
            print("FAIL 5 canvas too tall (--full, banding off) wrote a file before refusing")

        # With banding on (the default) the same page renders whole: the ceiling
        # was a property of the single-canvas strategy, not of the renderer.
        tall_on = os.path.join(tmp, "tall-on.png")
        check("0 canvas too tall (--full) banded", [tall, tall_on, "120", "60", "1", "--full"], 0)
        got = png_height(tall_on) if os.path.exists(tall_on) else None
        legs += 1
        if got != 70000:
            fails.append("0 canvas too tall (--full) banded: PNG height %r, want 70000" % (got,))
            print("FAIL 0 canvas too tall (--full) banded: PNG height %r, want 70000" % (got,))
        else:
            print("ok   0 canvas too tall (--full) banded   height=%d" % got)

        # 0 — stale clip frames are swept. Numbering restarts at 1 each run, so
        # a shorter re-render used to leave the previous run's tail in place and
        # a caller could not tell a 1-frame dump from a 5-frame one. The sweep
        # is exact, so an unrelated file and a lookalike name both survive.
        stale_dir = os.path.join(tmp, "stale")
        os.makedirs(stale_dir)
        for i in range(1, 6):
            with open(os.path.join(stale_dir, "clip_%03d.png" % i), "wb") as fh:
                fh.write(b"old")
        keep = os.path.join(stale_dir, "notes.txt")
        with open(keep, "w") as fh:
            fh.write("keep me")
        lookalike = os.path.join(stale_dir, "clip_x.png")
        with open(lookalike, "wb") as fh:
            fh.write(b"not a numbered frame")
        check("0 stale clip frames swept",
              ["--clip", doc, stale_dir, "120", "60", "1", "div"], 0,
              want_err="stale clip frame")
        frames = sorted(n for n in os.listdir(stale_dir)
                        if n.startswith("clip_") and n.endswith(".png") and n[5:-4].isdigit())
        if frames != ["clip_001.png"]:
            fails.append("stale sweep left %r, want ['clip_001.png']" % (frames,))
            print("FAIL stale sweep left %r" % (frames,))
        for survivor in (keep, lookalike):
            if not os.path.isfile(survivor):
                fails.append("stale sweep deleted %s, which is not a numbered frame" % survivor)
                print("FAIL stale sweep deleted %s" % os.path.basename(survivor))

        # 0 — frame numbering past 99. `clip_%02d` gave `clip_01` … `clip_99`
        # then `clip_100`, which sorts BETWEEN `clip_10` and `clip_11`: a caller
        # that globs the dump and sorts by name interleaves the hundredth frame
        # into the teens. Three digits keep lexical order equal to numeric order
        # for any dump the renderer can produce (#40).
        many = os.path.join(tmp, "many.html")
        with open(many, "w") as fh:
            fh.write('<!doctype html><html><body style="margin:0">')
            for i in range(105):
                fh.write('<div style="width:8px;height:4px;background:#0a0"></div>')
            fh.write("</body></html>\n")
        many_dir = os.path.join(tmp, "many")
        check("0 105 frames numbered", ["--clip", many, many_dir, "120", "60", "1", "div"], 0)
        names = sorted(n for n in os.listdir(many_dir) if n.endswith(".png"))
        if len(names) != 105:
            fails.append("105 frames: wrote %d, want 105" % len(names))
            print("FAIL 105 frames: wrote %d, want 105" % len(names))
        if names[:2] != ["clip_001.png", "clip_002.png"] or names[-1:] != ["clip_105.png"]:
            fails.append("105 frames: names run %r … %r, want clip_001.png … clip_105.png"
                         % (names[:2], names[-1:]))
            print("FAIL 105 frames: %r … %r" % (names[:2], names[-1:]))
        if names != sorted(names, key=lambda n: int(n[5:-4])):
            fails.append("105 frames: lexical sort does not match numeric sort")
            print("FAIL 105 frames: lexical sort does not match numeric sort")

        # 0 — a completed render leaves no `.partial-` sibling (#26). The temp
        # name is the mechanism; a run that forgot to rename would leave the
        # whole image behind under a name no glob finds, which is a silent disk
        # leak dressed as a success.
        clean_dir = os.path.join(tmp, "atomic")
        os.makedirs(clean_dir, exist_ok=True)
        clean_out = os.path.join(clean_dir, "clean.png")
        check("0 no partial left behind", [doc, clean_out, "120", "60"], 0)
        leftovers = [n for n in os.listdir(clean_dir) if ".partial-" in n]
        if leftovers:
            fails.append("no partial left behind: %r survived a successful render" % leftovers)
            print("FAIL no partial left behind: %r" % (leftovers,))

        # 101 — a render killed mid-write must NOT leave a truncated PNG at the
        # final path (#26). This is the leg that has to be timed rather than
        # argued: the encoder streams, so the window is the paint plus the
        # encode, and a fixture that finishes in 20 ms has no window at all.
        # A 70,000-px banded page takes ~2.5 s at 1600 px wide on the runner.
        #
        # Against a binary that opens the FINAL path (0.4.0) this leg fails with
        # a truncated file carrying a valid PNG signature — measured, 69,869
        # bytes of 589,369 with no IEND. That is the point of the leg.
        kill_dir = os.path.join(tmp, "kill")
        os.makedirs(kill_dir, exist_ok=True)
        kill_out = os.path.join(kill_dir, "killed.png")
        proc = subprocess.Popen(
            [binary, tall, kill_out, "1600", "600", "1", "--full"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.45)
        proc.kill()
        proc.wait()
        if os.path.exists(kill_out):
            size = os.path.getsize(kill_out)
            with open(kill_out, "rb") as fh:
                head = fh.read(8)
            fails.append("killed mid-write left %d bytes at the final path "
                         "(signature %r); a killed render must leave no output "
                         "there" % (size, head))
            print("FAIL killed mid-write left %d bytes at the final path" % size)
        # The previous file must survive, not just be absent: a caller re-rendering
        # over a good image must still have that image after a kill.
        keep_out = os.path.join(kill_dir, "keep.png")
        subprocess.run([binary, doc, keep_out, "120", "60"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        before_keep = open(keep_out, "rb").read() if os.path.exists(keep_out) else b""
        proc = subprocess.Popen(
            [binary, tall, keep_out, "1600", "600", "1", "--full"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.45)
        proc.kill()
        proc.wait()
        if not os.path.exists(keep_out):
            fails.append("killed mid-write deleted the previous file at the output path")
            print("FAIL killed mid-write deleted the previous file")
        elif open(keep_out, "rb").read() != before_keep:
            fails.append("killed mid-write replaced the previous file with %d bytes"
                         % os.path.getsize(keep_out))
            print("FAIL killed mid-write replaced the previous file")

        # 3 and 5 — a --clip run that paints NOTHING must not delete the last
        # good dump (#35). The sweep used to run before the paint, so every
        # refusal below had already emptied the directory while the exit code
        # named no file.
        for label, selector, want_rc in (("all boxless", "br, wbr", 3),
                                         ("no match", "span.nope", 3)):
            dump = os.path.join(tmp, "dump_" + label.replace(" ", "_"))
            os.makedirs(dump, exist_ok=True)
            seeded = []
            for i in range(1, 4):
                f = os.path.join(dump, "clip_%03d.png" % i)
                with open(f, "wb") as fh:
                    fh.write(b"previous run")
                seeded.append(f)
            check("3 %s keeps old dump" % label,
                  ["--clip", doc, dump, "120", "60", "1", selector], want_rc)
            missing = [f for f in seeded if not os.path.exists(f)]
            if missing:
                fails.append("%s: exit %d destroyed %d of %d previous frame(s)"
                             % (label, want_rc, len(missing), len(seeded)))
                print("FAIL %s destroyed %d previous frame(s)" % (label, len(missing)))

        # 0 — and the sweep still happens when the run DOES paint: a shorter
        # re-render must not leave the previous run's tail, which was the whole
        # reason the sweep existed.
        dump = os.path.join(tmp, "dump_short")
        os.makedirs(dump, exist_ok=True)
        for i in range(1, 6):
            with open(os.path.join(dump, "clip_%03d.png" % i), "wb") as fh:
                fh.write(b"previous run")
        # `DOC` holds ONE `div`, so this paints one frame over five seeded ones:
        # the kept count is the frame count, and the swept count is the rest.
        check("0 shorter run sweeps the tail", ["--clip", doc, dump, "120", "60", "1", "div"], 0,
              want_err="removed 4 stale clip frame(s)")
        left = sorted(n for n in os.listdir(dump) if n.endswith(".png"))
        if left != ["clip_001.png"]:
            fails.append("shorter run left %r, want clip_001.png only" % (left,))
            print("FAIL shorter run left %r" % (left,))

        # 4 — an asset that never arrives. The drain loop gives up at the
        # deadline and still paints, which used to exit 0 with a plausible PNG
        # missing its images: the one failure mode with no exit code at all.
        # The server below accepts and never answers, so the fetch blocks for
        # the full deadline by construction rather than by routing luck.
        port = black_hole()
        slow = os.path.join(tmp, "slow.html")
        with open(slow, "w") as fh:
            fh.write('<!doctype html><html><body style="margin:0">'
                     '<img src="http://127.0.0.1:%d/never.png" width="40" height="20">'
                     "</body></html>\n" % port)
        try:
            check("4 asset deadline", [slow, os.path.join(tmp, "slow.png"), "120", "60"], 4,
                  want_err="deadline", env={"BLITZ_DEADLINE_SECS": "1"})
        except subprocess.TimeoutExpired:
            fails.append("4 asset deadline: the render hung instead of giving up")
            print("FAIL 4 asset deadline hung")

        # 4 — #29. A document decides what the renderer fetches unless the
        # caller says otherwise. The counter is the assertion: a refusal that
        # still connects is not a refusal, and the exit code cannot tell them
        # apart on its own.
        srv, hits = counting_server()
        port = srv.server_address[1]
        remote = os.path.join(tmp, "remote.html")
        with open(remote, "w") as fh:
            fh.write('<!doctype html><html><body style="margin:0">'
                     '<img src="http://127.0.0.1:%d/t.png" width="20" height="10">'
                     "</body></html>\n" % port)

        check("4 --no-network refuses, names it",
              ["--no-network", remote, os.path.join(tmp, "off.png"), "120", "60", "1"], 4,
              want_err="refused by --no-network")
        if hits:
            fails.append("--no-network still sent %d request(s): %r" % (len(hits), hits))
            print("FAIL --no-network reached the server %r" % (hits,))

        # The same document unmodified must fetch. Without this control the
        # zero above could be a document that never had an image in it.
        hits.clear()
        check("0 --allow-host passes the matching host",
              ["--allow-host=127.0.0.1", remote, os.path.join(tmp, "on.png"),
               "120", "60", "1"], 0)
        if not hits:
            fails.append("--allow-host=127.0.0.1 sent no request at all")
            print("FAIL --allow-host sent nothing")

        hits.clear()
        check("4 --allow-host refuses another host",
              ["--allow-host=example.invalid", remote, os.path.join(tmp, "no.png"),
               "120", "60", "1"], 4,
              want_err="not matched by any --allow-host")
        if hits:
            fails.append("--allow-host=example.invalid still sent %r" % (hits,))

        # `file:` is not network traffic, so `--no-network` must leave it alone:
        # a document that embeds its own images still renders offline.
        with open(os.path.join(tmp, "local.png"), "wb") as fh:
            fh.write(png_1x1())
        local_doc = os.path.join(tmp, "local.html")
        with open(local_doc, "w") as fh:
            fh.write('<!doctype html><html><body style="margin:0">'
                     '<img src="local.png" width="20" height="10"></body></html>\n')
        check("0 --no-network keeps file:// images",
              ["--no-network", local_doc, os.path.join(tmp, "local_out.png"),
               "120", "60", "1"], 0)

        # A value-taking flag passed without one must not read as "allow all".
        check("2 --allow-host without a value",
              [remote, os.path.join(tmp, "v.png"), "120", "60", "1", "--allow-host"], 2,
              want_err="needs a value")
        check("2 --allow-host with an empty value",
              [remote, os.path.join(tmp, "v.png"), "120", "60", "1", "--allow-host="], 2,
              want_err="non-empty value")

        # 4 — #27. A 200 whose body is not an image used to paint a blank box at
        # exit 0 with nothing on stderr. Same server, a `.txt` path: the failure
        # happens inside the decoder, after the bytes arrived, so the exit code
        # is the only thing a caller could have seen.
        bad = os.path.join(tmp, "badimg.html")
        with open(bad, "w") as fh:
            fh.write('<!doctype html><html><body style="margin:0">'
                     '<img src="http://127.0.0.1:%d/t.txt" width="20" height="10">'
                     "</body></html>\n" % port)
        check("4 non-image 200 names the resource",
              [bad, os.path.join(tmp, "bad.png"), "120", "60", "1"], 4,
              want_err="not a decodable image")

        # Control: the same shape with a body the decoder accepts still exits 0,
        # so the leg above is about the body and not about remote images.
        good = os.path.join(tmp, "goodimg.html")
        with open(good, "w") as fh:
            fh.write('<!doctype html><html><body style="margin:0">'
                     '<img src="http://127.0.0.1:%d/ok.png" width="20" height="10">'
                     "</body></html>\n" % port)
        check("0 image 200 still exits 0",
              [good, os.path.join(tmp, "good.png"), "120", "60", "1"], 0)

        # ---- #28: --json. The schema is a contract, so the legs below compare
        # the keys the binary emits against the keys docs/USAGE.md lists, in both
        # directions: a field that vanishes from the code, or one the doc invents,
        # fails here. Everything else asserts a FACT, because a schema that parses
        # and carries the wrong number is the defect class this file exists for.
        def json_objects(argv, env=None):
            rc, out, err = run(binary, argv, env=env)
            objs = []
            for line in out.splitlines():
                if not line.strip():
                    continue
                try:
                    objs.append(json.loads(line))
                except ValueError:
                    fails.append("--json wrote a non-JSON line on stdout: %r" % line[:120])
                    print("FAIL --json non-JSON stdout line %r" % line[:80])
            return rc, objs, err

        one_json = os.path.join(tmp, "one_json.png")
        rc, objs, err = json_objects(["--json", doc, one_json, "120", "60"])
        if rc != 0:
            fails.append("--json rc=%d want 0" % rc)
            print("FAIL --json rc=%d" % rc)
        if len(objs) != 1:
            fails.append("--json wrote %d object(s), want 1" % len(objs))
            print("FAIL --json wrote %d objects, want 1" % len(objs))
        else:
            obj = objs[0]
            want_keys = {
                "out", "width", "height", "content_h", "canvas_h", "rows_lost",
                "mode", "parse_mode", "scale", "substituted_families",
                "failed_resources", "timings_ms", "exit",
            }
            if set(obj) != want_keys:
                fails.append("--json keys differ: missing %r extra %r"
                             % (sorted(want_keys - set(obj)), sorted(set(obj) - want_keys)))
                print("FAIL --json keys: missing %r extra %r"
                      % (sorted(want_keys - set(obj)), sorted(set(obj) - want_keys)))
            # The DOC is the contract: every field named in its schema table must
            # be one the binary emits, and vice versa.
            with open(os.path.join(root, "docs", "USAGE.md"), encoding="utf-8") as fh:
                usage_doc = fh.read()
            schema = usage_doc.split("### `--json`", 1)[-1]
            schema = schema.split("**The schema is stable**", 1)[0]
            # The first CELL, not the first backticked token: `width` and
            # `height` share a row, and a regex anchored at the line start would
            # silently drop the second name of every pair.
            doc_keys = set()
            for row in schema.splitlines():
                if row.startswith("|"):
                    doc_keys |= set(re.findall(r"`([a-z_]+)`", row.split("|")[1]))
            if doc_keys != want_keys:
                fails.append("docs/USAGE.md schema and the emitted keys differ: "
                             "doc-only %r code-only %r"
                             % (sorted(doc_keys - want_keys), sorted(want_keys - doc_keys)))
                print("FAIL --json schema drift: doc-only %r code-only %r"
                      % (sorted(doc_keys - want_keys), sorted(want_keys - doc_keys)))
            if obj.get("width") != 120 or obj.get("height") != 60:
                fails.append("--json geometry %rx%r, want 120x60"
                             % (obj.get("width"), obj.get("height")))
                print("FAIL --json geometry %r" % ((obj.get("width"), obj.get("height")),))
            if obj.get("out") != one_json:
                fails.append("--json out=%r, want %r" % (obj.get("out"), one_json))
                print("FAIL --json out=%r" % (obj.get("out"),))
            if obj.get("exit") != 0 or obj.get("mode") != "single":
                fails.append("--json exit/mode %r/%r, want 0/single"
                             % (obj.get("exit"), obj.get("mode")))
                print("FAIL --json exit/mode %r %r" % (obj.get("exit"), obj.get("mode")))
            timings = obj.get("timings_ms") or {}
            if set(timings) != {"parse", "layout", "paint", "total"}:
                fails.append("--json timings_ms keys %r" % sorted(timings))
                print("FAIL --json timings_ms keys %r" % sorted(timings))
            if not (isinstance(obj.get("substituted_families"), list)
                    or obj.get("substituted_families") is None):
                fails.append("--json substituted_families is neither a list nor null")
                print("FAIL --json substituted_families type")
            if not err.strip():
                fails.append("--json suppressed stderr entirely; diagnostics belong there")
                print("FAIL --json wrote nothing to stderr")
            else:
                print("ok   --json single object")

        # A CUT document: the object must carry the same fact the TIMING line and
        # the warning carry, or the field is decoration.
        cut_json = os.path.join(tmp, "cut_json.png")
        rc, objs, _ = json_objects(["--json", tall, cut_json, "120", "60"])
        if len(objs) != 1:
            fails.append("--json cut: %d object(s), want 1" % len(objs))
            print("FAIL --json cut: %d objects" % len(objs))
        else:
            obj = objs[0]
            ch, cv, lost = obj.get("content_h"), obj.get("canvas_h"), obj.get("rows_lost")
            if not (isinstance(ch, int) and isinstance(cv, int) and isinstance(lost, int)):
                fails.append("--json cut: content_h/canvas_h/rows_lost not integers: %r"
                             % ((ch, cv, lost),))
                print("FAIL --json cut field types")
            elif ch <= cv:
                fails.append("--json cut: content_h=%r not above canvas_h=%r" % (ch, cv))
                print("FAIL --json cut: %r <= %r" % (ch, cv))
            elif lost != ch - cv:
                fails.append("--json cut: rows_lost=%r, want content_h-canvas_h=%d"
                             % (lost, ch - cv))
                print("FAIL --json cut: rows_lost=%r want %d" % (lost, ch - cv))
            else:
                print("ok   --json cut document names the rows lost")

        # --clip: one object per frame, and stdout carries NOTHING but objects,
        # which is the property that makes the stream parseable at all.
        clips = os.path.join(tmp, "clips_json")
        rc, objs, err = json_objects(["--json", "--clip", doc, clips, "120", "60", "1", "div"])
        if rc != 0 or len(objs) != 1:
            fails.append("--json --clip rc=%d objects=%d, want 0/1" % (rc, len(objs)))
            print("FAIL --json --clip rc=%d objects=%d" % (rc, len(objs)))
        else:
            obj = objs[0]
            if obj.get("mode") != "clip":
                fails.append("--json --clip mode=%r, want clip" % obj.get("mode"))
                print("FAIL --json --clip mode=%r" % obj.get("mode"))
            if not str(obj.get("out", "")).endswith("clip_001.png"):
                fails.append("--json --clip out=%r, want .../clip_001.png" % obj.get("out"))
                print("FAIL --json --clip out=%r" % obj.get("out"))
            # The human frame line must have moved to stderr, not disappeared.
            if "clip_001 css" not in err:
                fails.append("--json --clip dropped the geometry line instead of moving it")
                print("FAIL --json --clip geometry line missing from stderr")
            else:
                print("ok   --json --clip frame line moved to stderr")

        # exit 4 with the resource named, in the object rather than only on stderr.
        rc, objs, _ = json_objects(["--json", bad, os.path.join(tmp, "bad_json.png"),
                                    "120", "60", "1"])
        if rc != 4:
            fails.append("--json non-image rc=%d want 4" % rc)
            print("FAIL --json non-image rc=%d" % rc)
        elif len(objs) != 1:
            fails.append("--json non-image wrote %d object(s), want 1" % len(objs))
            print("FAIL --json non-image objects=%d" % len(objs))
        else:
            obj = objs[0]
            fr = obj.get("failed_resources") or []
            if obj.get("exit") != 4:
                fails.append("--json non-image exit=%r, want 4" % obj.get("exit"))
                print("FAIL --json non-image exit=%r" % obj.get("exit"))
            elif not fr or not fr[0].get("url") or not fr[0].get("reason"):
                fails.append("--json non-image failed_resources=%r, want url+reason" % (fr,))
                print("FAIL --json non-image failed_resources=%r" % (fr,))
            elif "not a decodable image" not in fr[0]["reason"]:
                fails.append("--json non-image reason=%r" % fr[0]["reason"])
                print("FAIL --json non-image reason=%r" % fr[0]["reason"])
            else:
                print("ok   --json carries exit 4 and the failed resource")

        # A run that writes no PNG emits no object: there is no artefact to
        # describe, and inventing one would make `out` a lie.
        rc, objs, _ = json_objects(["--json", empty, os.path.join(tmp, "none.png"),
                                    "120", "60", "--clip", "div"])
        if rc != 3 or objs:
            fails.append("--json no-match rc=%d objects=%d, want 3/0" % (rc, len(objs)))
            print("FAIL --json no-match rc=%d objects=%d" % (rc, len(objs)))
        else:
            print("ok   --json emits nothing when no PNG is written")

        srv.shutdown()

        # 101 — panics. Three distinct causes, one documented code.
        check("101 unparsable number", [doc, os.path.join(tmp, "s.png"), "120", "60", "abc"], 101)
        check("101 invalid selector", ["--clip", doc, os.path.join(tmp, "iv"), "120", "60", "1", "div[["], 101)
        check("101 missing input", [os.path.join(tmp, "nope.html"), os.path.join(tmp, "m.png"), "120", "60"], 101)

    if fails:
        # Both numbers are counted, never written by hand. They differ because a
        # single leg can report more than one failed assertion (the --help leg
        # checks one exit code and several stdout needles), and a hardcoded
        # total would stay put when a leg is added or deleted.
        print("\n%d failed assertion(s) across %d leg(s):" % (len(fails), legs))
        for f in fails:
            print("  - " + f)
        return 1
    print("\nall %d legs hold" % legs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
