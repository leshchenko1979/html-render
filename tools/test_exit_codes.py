#!/usr/bin/env python3
"""Pins the exit-code contract documented in README §Exit codes.

A CLI that exits `0` on a typo is worse than one that crashes: `--documnet`
rendered a fragment of a full document and wrote a plausible PNG of the wrong
thing, with nothing on stderr. Every code below is documented, so every code is
tested here — one scripted pass, against the binary CI just built.

    python3 tools/test_exit_codes.py --bin target/release/render

Exit 0 = the contract holds. Exit 1 = it does not (the message says which leg).
Run it against an older binary and two legs fail (unknown flag, all-boxless),
which is the point: a test that cannot observe the defect it was written for is
decoration.
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


def run(binary, argv):
    p = subprocess.run([binary] + argv, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the renderer binary")
    args = ap.parse_args()
    binary = os.path.abspath(args.bin)
    fails = []

    def check(name, argv, want_rc, want_err=None):
        rc, out, err = run(binary, argv)
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
        rc, out, _err = run(binary, ["--help"])
        for needle in ("USAGE:", "--clip", "EXIT CODES:", "101"):
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
