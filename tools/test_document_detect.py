#!/usr/bin/env python3
"""Regression test for automatic document-root detection.

A file that declares its own document root — `<!doctype>`, `<html>`, `<head>` or
`<body>` as its first tag — must be rendered as a document, verbatim. Anything
else is a fragment and gets wrapped in the reset the parity harness has always
used.

Wrapping a real document is not merely untidy. The wrapper's
`html{background:#ffffff}` reset wins the canvas over the document's own
`body{background:…}` — the common idiom — so the page renders white with
identical geometry and nothing on stderr to say so. Measured on the r2 release
binary: 70.4 % of pixels differ for a `body`-coloured document, 89.9 % for a
file that opens at `<body>`.

    python3 tools/test_document_detect.py --bin target/release/render

Exit 0 = the contract holds. Exit 1 = it does not (the message names the leg).
Legs (a) and (b) FAIL against the r2 binary, which is the point: a test that
cannot observe the failure it was written for is decoration.
"""

import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure  # noqa: E402

W, H, SCALE = 200, 120, 1

# Byte-identical to tools/run_parity.py:WRAPPER and to the branch in
# src/main.rs, so "the wrapped render" means the same thing on both sides.
WRAPPER = (
    "<html><head><style>html,body{{margin:0;padding:0;background:#ffffff;}}"
    "</style></head><body>{}</body></html>"
)

# A full document that paints its canvas on `body` — the idiom the wrapper
# steals. `html` is left unpainted on purpose: that is what lets the reset win.
FULL_DOC = (
    "<!doctype html>\n<html><head><style>\n"
    "html, body { margin: 0; padding: 0 }\n"
    "body { background: #eeeeff }\n"
    "</style></head>\n"
    '<body><div style="width:120px;height:60px;background:#cc0000"></div></body>'
    "</html>\n"
)

# The same page with the root tags omitted: still a document as far as a browser
# is concerned, and it still loses its canvas to the wrapper.
BODY_DOC = (
    '<body style="margin:0;background:#eeeeff">'
    '<div style="width:120px;height:60px;background:#cc0000"></div></body>'
)

# A fragment: nothing here declares a root, so it must be wrapped as before.
FRAGMENT = (
    "<style>body{margin:0;background:#eeeeff}</style>"
    '<div style="width:120px;height:60px;background:#cc0000"></div>'
)


def render(binary, src, out, extra=()):
    cmd = [binary, src, out, str(W), str(H), str(SCALE)] + list(extra)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    return proc.returncode, proc.stdout, proc.stderr


def pixels(path):
    w, h, _channels, data = measure.read_png(path)
    return w, h, bytes(data)


def compare(a, b):
    """(differing_pixel_count, total_pixels). Bytes are compared, not files:
    PNG metadata must not be able to mask or manufacture a difference."""
    wa, ha, pa = pixels(a)
    wb, hb, pb = pixels(b)
    if (wa, ha) != (wb, hb):
        return -1, wa * ha
    if pa == pb:
        return 0, wa * ha
    step = len(pa) // (wa * ha)
    diff = sum(
        1 for i in range(wa * ha) if pa[i * step : (i + 1) * step] != pb[i * step : (i + 1) * step]
    )
    return diff, wa * ha


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the renderer binary")
    args = ap.parse_args()
    binary = os.path.abspath(args.bin)
    fails = []
    ran = 0

    def render_ok(tag, src, out, extra=()):
        rc, _out, err = render(binary, src, out, extra)
        if rc != 0:
            fails.append("%s: rc=%d (want 0)\n%s" % (tag, rc, err[-500:]))
            return False
        return True

    def expect_same(tag, a, b):
        nonlocal ran
        ran += 1
        diff, total = compare(a, b)
        if diff < 0:
            fails.append("%s: sizes differ" % tag)
        elif diff:
            fails.append("%s: %d of %d pixels differ (%.2f %%)"
                         % (tag, diff, total, 100.0 * diff / total))

    with tempfile.TemporaryDirectory() as tmp:
        src = {}
        for name, text in (("full", FULL_DOC), ("body", BODY_DOC), ("frag", FRAGMENT)):
            src[name] = os.path.join(tmp, name + ".html")
            with open(src[name], "w") as fh:
                fh.write(text)
        for name in ("full", "frag"):
            with open(src[name]) as fh:
                wrapped = WRAPPER.format(fh.read())
            src[name + "_wrapped"] = os.path.join(tmp, name + "_wrapped.html")
            with open(src[name + "_wrapped"], "w") as fh:
                fh.write(wrapped)

        def out(name, flags):
            return os.path.join(tmp, "%s_%s.png" % (name, flags or "auto"))

        # --- the four renderings every leg needs -------------------------
        need = [
            ("full", "auto", (), src["full"]),
            ("full", "document", ("--document",), src["full"]),
            ("full", "fragment", ("--fragment",), src["full"]),
            ("full_wrapped", "document", ("--document",), src["full_wrapped"]),
            ("body", "auto", (), src["body"]),
            ("body", "document", ("--document",), src["body"]),
            ("frag", "auto", (), src["frag"]),
            ("frag", "document", ("--document",), src["frag"]),
            ("frag_wrapped", "document", ("--document",), src["frag_wrapped"]),
        ]
        built = set()
        for name, flags, extra, path in need:
            if render_ok("[%s %s]" % (name, flags), path, out(name, flags), extra):
                built.add((name, flags))

        def have(*keys):
            return all(k in built for k in keys)

        # (a) a full document is detected and rendered verbatim: this is the
        #     70.4 % failure the whole change exists for.
        if have(("full", "auto"), ("full", "document")):
            expect_same("[a] full doc: auto vs --document",
                        out("full", "auto"), out("full", "document"))

        # (b) the same for a file that opens at <body>: 89.9 % on the r2 binary.
        if have(("body", "auto"), ("body", "document")):
            expect_same("[b] <body>-leading: auto vs --document",
                        out("body", "auto"), out("body", "document"))

        # (c) positive control: a fragment is STILL wrapped. Its auto render
        #     must equal the wrapper rendered explicitly, or the detection has
        #     started eating fragments.
        if have(("frag", "auto"), ("frag_wrapped", "document")):
            expect_same("[c] fragment: auto vs explicit wrapper",
                        out("frag", "auto"), out("frag_wrapped", "document"))

        # (d) backwards compatibility: --fragment on a full document reproduces
        #     the OLD output exactly, i.e. the wrapper applied to it.
        if have(("full", "fragment"), ("full_wrapped", "document")):
            expect_same("[d] full doc: --fragment vs explicit wrapper",
                        out("full", "fragment"), out("full_wrapped", "document"))

        # (e) --document still forces on a fragment: it must NOT equal the
        #     auto (wrapped) render, otherwise the flag is a no-op.
        if have(("frag", "document"), ("frag", "auto")):
            ran += 1
            diff, total = compare(out("frag", "document"), out("frag", "auto"))
            if diff == 0:
                fails.append("[e] fragment: --document == auto, so the flag does nothing")

        # (f) the two flags name opposite readings; together they must refuse
        #     rather than silently prefer one. Checked on the MESSAGE, because
        #     "unknown flag" also exits 2 and would pass for the wrong reason.
        rc, _out, err = render(binary, src["frag"], out("frag", "both"),
                               ("--document", "--fragment"))
        ran += 1
        if rc != 2:
            fails.append("[f] --document --fragment: rc=%d (want 2)" % rc)
        elif "mutually exclusive" not in err:
            fails.append("[f] --document --fragment: refused without naming the "
                         "conflict; stderr=%r" % err[-300:])

    print("legs run: %d" % ran)
    if fails:
        print("FAIL")
        for f in fails:
            print("  " + f)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
