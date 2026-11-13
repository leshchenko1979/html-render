#!/usr/bin/env python3
"""Pins the exit-code contract documented in README §Exit codes.

A CLI that exits `0` on a typo is worse than one that crashes: `--documnet`
rendered a fragment of a full document and wrote a plausible PNG of the wrong
thing, with nothing on stderr. Every code below is documented, so every code is
tested here — one scripted pass, against the binary CI just built.

    python3 tools/test_exit_codes.py --bin target/release/render

Exit 0 = the contract holds. Exit 1 = it does not (the message says which leg).
Run it against the published r2 binary and it prints "21 leg(s) failed" — the
point being that a test which cannot observe the defect it was written for is
decoration.

The summary counts failed *assertions*, not legs: r2 prints "21 leg(s) failed"
across 14 distinct legs, because the `--help` leg alone contributes six of them
(exit code plus five stdout checks).
"""

import argparse
import os
import subprocess
import sys
import tempfile

# One box, one boxless pair, one empty document. Small enough that the whole
# pass is a few hundred milliseconds and can afford to run on every push.
DOC = """<!doctype html><html><body style="margin:0">
<div id="a" style="width:40px;height:20px;background:#c00"></div>
<p>text<br>more<wbr>tail</p>
</body></html>
"""

EMPTY = """<!doctype html><html><body><p>no divs here</p></body></html>
"""


def run(binary, argv, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    p = subprocess.run([binary] + argv, capture_output=True, text=True, timeout=120,
                       env=full_env)
    return p.returncode, p.stdout, p.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the renderer binary")
    args = ap.parse_args()
    binary = os.path.abspath(args.bin)
    fails = []

    def check(name, argv, want_rc, want_err=None, env=None):
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
        for path, body in ((doc, DOC), (empty, EMPTY)):
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
        for needle in ("USAGE:", "--clip", "EXIT CODES:", "101", "4"):
            if needle not in out:
                fails.append("--help stdout lacks %r" % needle)
                print("FAIL --help stdout lacks %r" % needle)
        if not out.startswith("html-render "):
            fails.append("--help does not start with the program name and version")
            print("FAIL --help does not start with the program name and version")

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
              want_err="usage: html-render")

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

        # 2 — `--scroll` is a clip-mode reading and nothing else reads it. It
        # used to be accepted in single-image mode and silently ignored, so the
        # flag looked honoured while the output was the plain full-document
        # render — the same "did something else, said nothing" class as a typo.
        check("2 --scroll outside --clip", [doc, os.path.join(tmp, "sc.png"), "120", "60", "1", "--scroll"], 2,
              want_err="only applies to --clip")

        # 0 — stale clip frames are swept. Numbering restarts at 1 each run, so
        # a shorter re-render used to leave the previous run's tail in place and
        # a caller could not tell a 1-frame dump from a 5-frame one. The sweep
        # is exact, so an unrelated file and a lookalike name both survive.
        stale_dir = os.path.join(tmp, "stale")
        os.makedirs(stale_dir)
        for i in range(1, 6):
            with open(os.path.join(stale_dir, "clip_%02d.png" % i), "wb") as fh:
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
        if frames != ["clip_01.png"]:
            fails.append("stale sweep left %r, want ['clip_01.png']" % (frames,))
            print("FAIL stale sweep left %r" % (frames,))
        for survivor in (keep, lookalike):
            if not os.path.isfile(survivor):
                fails.append("stale sweep deleted %s, which is not a numbered frame" % survivor)
                print("FAIL stale sweep deleted %s" % os.path.basename(survivor))

        # 4 — an asset that never arrives. The drain loop gives up at the
        # deadline and still paints, which used to exit 0 with a plausible PNG
        # missing its images: the one failure mode with no exit code at all.
        # 10.255.255.1 is non-routable, so the fetch cannot complete; a 1 s
        # deadline keeps the leg fast.
        slow = os.path.join(tmp, "slow.html")
        with open(slow, "w") as fh:
            fh.write('<!doctype html><html><body style="margin:0">'
                     '<img src="http://10.255.255.1/never.png" width="40" height="20">'
                     "</body></html>\n")
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
        print("\n%d leg(s) failed:" % len(fails))
        for f in fails:
            print("  - " + f)
        return 1
    print("\nall legs hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
