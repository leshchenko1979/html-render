#!/usr/bin/env python3
"""Contract test for tools/css_audit.py.

The tool answers "which declarations did you drop?" by removing one declaration
at a time and diffing the PNG. Its whole value is that the answer is measured,
so the test has to prove it can tell the two outcomes apart:

  (a) POSITIVE control — a declaration the renderer does not honour is
      reported inert. Two of them: `foobar-baz`, which no engine knows, and
      `container-type`, a real property this renderer has no support for.
  (b) NEGATIVE control — a declaration that IS load-bearing is never reported
      inert. Without this leg a tool that called everything inert would pass
      (a) and be worse than useless.
  (c) an at-rule is skipped AND counted, and nothing from inside it is
      audited: removing a declaration from `@media print` measures a state the
      file may never render in.
  (d) a `style=""` attribute is audited and attributed to its element, not to
      the string "style=".
  (e) `--json` is valid, agrees with the table's counts, and is stable across
      two runs (the renderer is deterministic, so the report must be too).
  (f) `--fail-on-inert` exits 1 when something is inert and 0 when nothing is:
      the flag has to be able to both fire and not fire.
  (g) a real shipped asset runs end to end.

    python3 tools/test_css_audit.py --bin target/release/render

Exit 0 = the contract holds. Exit 1 = it does not, and the message names the leg.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
AUDIT = os.path.join(HERE, "css_audit.py")

# Two declarations the renderer cannot honour, three it must.
FIXTURE = """<!doctype html>
<html><head><style>
  @media print { .box { color: #123456 } }
  .box { background: #ff0000; width: 120px; height: 80px; }
  .box { foobar-baz: 12px; }
  .box { container-type: inline-size; }
  .tile { padding-left: 9px; }
</style></head>
<body>
  <div class="box">A</div>
  <div class="tile" style="border-left: 6px solid #0000ff">B</div>
</body></html>
"""

# Same file with the two unhonoured declarations deleted: nothing may be inert.
CLEAN = """<!doctype html>
<html><head><style>
  .box { background: #ff0000; width: 120px; height: 80px; }
  .tile { padding-left: 9px; }
</style></head>
<body>
  <div class="box">A</div>
  <div class="tile" style="border-left: 6px solid #0000ff">B</div>
</body></html>
"""

INERT_WANT = {"foobar-baz", "container-type"}
LOAD_BEARING_WANT = {"background", "width", "height", "padding-left", "border-left"}


def run(binary, html, extra=()):
    p = subprocess.run(
        [sys.executable, AUDIT, "--bin", binary, html, "--width", "400",
         "--height", "200", "--scale", "1"] + list(extra),
        capture_output=True,
        text=True,
        timeout=900,
    )
    return p.returncode, p.stdout, p.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the renderer binary")
    args = ap.parse_args()
    binary = os.path.abspath(args.bin)
    fails = []
    ran = 0

    def leg(tag, ok, detail=""):
        nonlocal ran
        ran += 1
        if not ok:
            fails.append("%s: %s" % (tag, detail))

    with tempfile.TemporaryDirectory() as tmp:
        fixture = os.path.join(tmp, "fixture.html")
        clean = os.path.join(tmp, "clean.html")
        open(fixture, "w").write(FIXTURE)
        open(clean, "w").write(CLEAN)

        rc, out, err = run(binary, fixture)
        if rc != 0:
            print("legs run: 0")
            print("FAIL")
            print("  [setup] audit of the fixture exited %d\n%s" % (rc, err[-800:]))
            return 1

        rcj, outj, errj = run(binary, fixture, ("--json",))
        if rcj != 0:
            print("legs run: 0")
            print("FAIL")
            print("  [setup] --json exited %d\n%s" % (rcj, errj[-800:]))
            return 1
        report = json.loads(outj)
        rows = report["rows"]
        inert = {r["property"] for r in rows if r["verdict"] == "no pixel effect"}
        live = {r["property"] for r in rows if r["verdict"] != "no pixel effect"}

        # (a) positive control: exactly the two unhonoured declarations.
        leg("[a] inert set", inert == INERT_WANT,
            "reported %s, want %s" % (sorted(inert), sorted(INERT_WANT)))

        # (b) negative control: nothing load-bearing may be called inert.
        missed = LOAD_BEARING_WANT & inert
        leg("[b] load-bearing not inert", not missed,
            "wrongly inert: %s" % sorted(missed))
        missing = LOAD_BEARING_WANT - live
        leg("[b2] load-bearing measured", not missing,
            "absent from the report: %s" % sorted(missing))

        # (c) the at-rule is skipped, counted, and contributes no declarations.
        leg("[c] at-rule skipped", report["skipped_at_rules"] == ["@media print"],
            "skipped=%r" % (report["skipped_at_rules"],))
        leg("[c2] at-rule body not audited", "color" not in {r["property"] for r in rows},
            "a declaration from inside @media print was audited")

        # (d) the style attribute is attributed to its element.
        attr = [r for r in rows if r["where"] == "attr"]
        leg("[d] style= attribute audited", len(attr) == 1,
            "%d attribute declarations found" % len(attr))
        if attr:
            leg("[d2] attribute selector names the element",
                "tile" in attr[0]["selector"] and not attr[0]["selector"].startswith("style"),
                "selector=%r" % attr[0]["selector"])
            leg("[d3] attribute declaration is load-bearing",
                attr[0]["property"] in live, "border-left was called inert")

        # (e) the JSON agrees with the table, and two runs agree with each other.
        leg("[e] JSON counts agree",
            report["declarations"] == len(rows) and report["inert"] == len(inert),
            "declarations=%s rows=%s inert=%s" % (report["declarations"], len(rows), report["inert"]))
        rc2, out2, _err2 = run(binary, fixture, ("--json",))
        leg("[e2] JSON is stable across runs", rc2 == 0 and out2 == outj,
            "two runs of the same file disagree")

        # (f) the flag both fires and does not fire.
        rcf, _o, _e = run(binary, fixture, ("--fail-on-inert",))
        leg("[f] --fail-on-inert fires", rcf == 1, "rc=%d (want 1)" % rcf)
        rcc, _o, _e = run(binary, clean, ("--fail-on-inert",))
        leg("[f2] --fail-on-inert stays quiet on a clean file", rcc == 0,
            "rc=%d (want 0)" % rcc)

        # (g) a real asset, end to end.
        deck = os.path.join(REPO, "assets", "demo-deck.html")
        if os.path.exists(deck):
            rcd, outd, errd = run(binary, deck, ("--limit", "3"))
            leg("[g] shipped asset audits", rcd == 0 and "declarations" in outd,
                "rc=%d\n%s" % (rcd, errd[-400:]))

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
