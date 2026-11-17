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
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading

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
