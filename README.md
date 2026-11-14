# html-render

![html-render: HTML + CSS to PNG with no browser](assets/hero.png)

[![parity](https://github.com/leshchenko1979/html-render/actions/workflows/parity.yml/badge.svg)](https://github.com/leshchenko1979/html-render/actions/workflows/parity.yml)
[![release](https://img.shields.io/github/v/release/leshchenko1979/html-render)](https://github.com/leshchenko1979/html-render/releases)
[![licence](https://img.shields.io/github/license/leshchenko1979/html-render)](https://github.com/leshchenko1979/html-render/blob/main/LICENSE)

**HTML + CSS → PNG, with no browser.**

**Linux x86_64, glibc ≥ 2.35.** No macOS, no Windows. A container image carries
the shared libraries and the font for you — [skip the host
entirely](#or-skip-the-host-entirely).

```bash
# the image above is this command's output, byte for byte: 3200×1800
html-render assets/hero.html hero.png 1600 900 2 --document
```

A small Rust CLI that renders real documents — stylesheets, flexbox, grid, images,
web fonts — using [Blitz](https://github.com/DioxusLabs/blitz)'s Stylo + Taffy +
Vello pipeline. No Chromium, no JavaScript engine, one process.

## Contents

- [Status](#status) · [Install](#install) · [Use cases](#use-cases) · [Usage](#usage)
- [Fidelity](#fidelity) · [What it doesn't](#what-it-doesnt) · [Security](#security)
- [Contributing](#contributing) · [Licence](#licence)

## Status

`0.3.0-beta.6`, one maintainer. No API promise: flags and output
names may change between minor versions. Linux only. Two known layout defects,
eleven corpus boxes: consecutive `<br>` runs in a `font-size:0` inline-block
context (4 boxes) and table layout, up to 18 px short (7 boxes,
[issue #9](https://github.com/leshchenko1979/html-render/issues/9)). See
[the corpus gate](GATES.md#corpus).

It exists to answer a concrete question: can a 25 MB binary replace a 500 MB
browser for server-side rendering? On a real 12-slide, 4 MB deck, yes
(what changed between releases: [CHANGELOG.md](CHANGELOG.md)):

| 12-slide deck, 2560×1440 | wall | peak RSS, largest process |
|---|---|---|
| headless Chromium (Playwright) | 13.36 s | 858 MiB |
| **html-render `--scroll`** | **5.37 s** | **93.4 MiB** |
| html-render `--clip` | 5.72 s | 275.2 MiB |

`--scroll` and `--clip` are the *clip* mode's two painting strategies; the
default mode is a single image (`html-render page.html out.png 1280 720`), which
is not in this table.

**2.34× faster and 3.12× lighter on the plain `--clip` recipe; 2.49× and 9.19×
with `--scroll`**, which paints one element-sized canvas per slide instead of one
full-height canvas that is cropped afterwards.

One asymmetry is baked into those ratios and it favours us. The Chromium side
sleeps **2.94 s** by construction — `tools/gates.py` waits 1.5 s for the page to
settle plus 120 ms per element screenshot — while html-render is synchronous and
waits for nothing. That is 22 % of Chromium's wall clock. Subtract it and the
`--scroll` speed ratio is **1.94×**, not 2.49×; the memory ratio is untouched
(sleeping allocates nothing). Both numbers are true: 2.49× is what a pipeline
pays end to end, 1.94× is what the two engines cost once the harness stops
padding the browser's side.

Those RSS figures are the largest single process, which is the wrong unit for one
of the two engines: Chromium is a tree of renderers and helpers. Measured on the
deck in this repo (`assets/demo-deck.html`, 2560×1440, scale 2), its tree peaks at
**1703.5–1826.2 MiB** while its largest process reads **297.1–299.2 MiB** — the
single-process reading undercounts it **5.7–6.1×**. `html-render` is one process,
so its two readings agree (81.2 vs 81.0 MiB). The memory ratios above therefore
*understate* the difference; the wall ratios do not depend on the method.
`python3 tools/measure_rss.py --rss-mode tree -- <command>` reproduces either.

Both encoders are lossless: `--scroll` and `--clip` differ on 3,551 of 44,236,800
pixels (0.0080 %, max channel delta 2/255) — the same renderer twice, not a
comparison. Against Chromium the same deck differs on **4.13 %** of pixels by more
than 16 levels (per slide 0.88 – 11.67 %), which is antialiasing and image
resampling rather than layout; [GATES.md](GATES.md#fidelity) breaks that down
region by region. Output is byte-for-byte deterministic,
which a browser is not: ten renders of the same input produce one checksum, so
output can be diffed in CI. That holds for a document whose resources are local
or `data:` URIs. A remote image is fetched at render time and painted as
**absent** if it misses `BLITZ_DEADLINE_SECS` (default 120 s), so a slow origin
changes the picture without changing the input.

### Remote assets fail silently, and the failure looks like success

Every one of these writes a PNG, prints nothing beyond `TIMING`, and exits `0`.
The redirect row is fixed in `v0.3.0-beta.6` and pinned by
`tools/test_redirect.py`; the rest still stand:

| Case | What you get |
|---|---|
| `404`, or a `200` with a body that is not an image | blank box, no diagnostic |
| a 197 KB image that decodes to 522 MB of pixels | no cap on decoded size: RSS follows the image, not the document |
| decode OOM under `ulimit -v` | the panic is swallowed and a blank PNG is written with `rc=0` |
| **fixed in beta.6:** `302` / `301` / `307` redirect | both hops were fetched and the image was then **dropped**, because Blitz keyed the decoded result on the *final* URL while the node was registered under the *requested* one. Every CDN and signed-URL origin hit it. `--bin` a beta.5 build and leg `b-redirect` still fails: `painted=False wanted=True` |

So the pipeline this README recommends — commit the PNG, diff the PNG, fail the
build — is the only thing that catches a dropped asset. **Verify the PNG, not the
exit code:** check the magic bytes and the IHDR dimensions against
`width × scale`, and diff against a golden render, which determinism makes exact.

The deck is not part of this repo (4.16 MB, embedded JPEGs, four `@font-face`
faces), so the headline cost table above is **history, not a gate**: nobody
outside this project can re-run it, because the input is not here. What
`python3 tools/gates.py --deck <path>` reproduces is the *method* — fidelity,
determinism and dimensions on **your** document — and a different deck gives
different numbers by construction. `tools/gates.py` has no cost gate at all
(`grep '^def ' tools/gates.py` lists `gate_a`, `gate_b`, `gate_c`), so it never
measures Chromium wall time or RSS.

[`assets/demo-deck.html`](assets/demo-deck.html) is a four-slide, 4 KB layout
fixture in the repo, so the two recipes are runnable from a fresh clone without
that deck:

```bash
# four slides out, one PNG per section.slide
html-render --clip assets/demo-deck.html slides/ 1280 720 2 'section.slide' --document

# determinism: ten renders, one checksum
for i in $(seq 10); do html-render assets/demo-deck.html /tmp/r$i.png 1280 720 1 --document; done
sha256sum /tmp/r*.png | awk '{print $1}' | sort -u | wc -l   # → 1
```

## Install

No package manager, no toolchain. A single 25 MB binary — but **not** a static
one: it links glibc and the font stack at runtime. From a
[release](https://github.com/leshchenko1979/html-render/releases):

```bash
V=v0.3.0-beta.6
N=html-render_0.3.0-beta.6_linux_amd64
BASE="https://github.com/leshchenko1979/html-render/releases/download/$V"

curl -fsSL -O "$BASE/$N.tar.gz"
curl -fsSL -O "$BASE/checksums.txt"
sha256sum -c checksums.txt
tar xzf "$N.tar.gz"
install -m755 html-render /usr/local/bin/html-render
```

Every release verifies its own asset: the workflow downloads the published
tarball back over HTTPS, checks `checksums.txt`, byte-compares it with the build,
renders a PNG and asserts its dimensions. A release serving the wrong file fails
its own run.

**What that does not prove.** The check runs in the same job, with
`contents: write`, immediately after the build that produced the bytes, and
`checksums.txt` is fetched from the same release over the same TLS. It catches a
corrupted download, not a compromised one: there is no signature, no provenance
attestation and no SBOM — `cosign`, `sigstore`, `attest`, `sbom`, `cargo audit`
and `cargo deny` appear nowhere in this tree. For that guarantee, pin the sha256
from a channel you trust, or build from source at a commit you have reviewed.

### What it needs on the host

`ldd` on the shipped binary lists **15** entries — **13** shared libraries plus
the loader and the vDSO — and a `scratch` or distroless image satisfies none of
them — the binary will not start:

| | |
|---|---|
| glibc | **2.35+** (`libc.so.6`, `libm.so.6`, `libgcc_s.so.1`) |
| TLS | `libssl.so.3`, `libcrypto.so.3` (Debian 12+ / Ubuntu 22.04+) |
| fonts | `libfontconfig.so.1`, `libfreetype.so.6`, `libexpat.so.1` |
| TLS trust store | `/etc/ssl/certs/ca-certificates.crt` — opened at startup; without it every `https://` image is silently dropped |
| images | `libpng16.so.16`, `libbz2.so.1.0`, `libbrotlicommon.so.1`, `libbrotlidec.so.1` |
| misc | `libz.so.1`, `ld-linux-x86-64.so.2`, `linux-vdso.so.1` |

On Debian/Ubuntu the non-base half is one line:

```bash
apt-get install -y libssl3 libfontconfig1 libfreetype6 libpng16-16 libbz2-1.0 libbrotli1 ca-certificates
```

`libexpat.so.1`, `libz.so.1` and `libgcc_s.so.1` are absent from that line on
purpose: the six packages above depend on them, so apt pulls them in. `ldd` still
lists all thirteen, which is why the table and the command do not match line for
line — the table is the closure, the command is the shortest way to it.

### Fonts are a runtime dependency, and a silent one

The binary ships **no** font. `assets/hero.html` and `assets/og.html` name
`DejaVu Sans` explicitly, `assets/demo-deck.html` asks for plain `sans-serif`
(which fontconfig resolves to DejaVu on most hosts), and the deck this was
measured on names DejaVu too. If the host has it, nothing needs installing.
Check before you trust a PNG:

```bash
fc-match "DejaVu Sans"    # → DejaVuSans.ttf, or a substitute you did not ask for
```

The failure mode is the reason this is a section rather than a footnote:
fontconfig always resolves *something*, so a host without DejaVu renders every
line in a fallback face, re-wraps the text to different line breaks, and produces
a perfectly plausible PNG of the wrong thing. No warning, no non-zero exit. On a
minimal container:

```bash
apt-get install -y fonts-dejavu-core
```

### Or skip the host entirely

Everything above — the shared libraries, the glibc floor, the font — is
discharged once by an image:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
    --cap-drop ALL --security-opt no-new-privileges \
    -v "$PWD:/work" \
    ghcr.io/leshchenko1979/html-render:latest \
    /work/card.html /work/card.png 1280 720 2
```

`--user` is required, not optional. The image runs as uid 10001 so it cannot
touch anything outside the file it was told to write, which also means it cannot
write into a bind mount owned by you: without `--user` the render exits 101 with
`create png: Permission denied`. Passing your own uid:gid makes the PNG belong to
you, which is what mounting a directory implies.

`latest` is a **moving** tag: it follows the default branch and every release, so
the same name can mean a different binary next week. Pin a digest or a `v*` tag
for anything reproducible:

```bash
docker pull ghcr.io/leshchenko1979/html-render@sha256:<digest>
```

Rendering HTML you do not trust needs the network taken away, because remote
sub-resources are fetched with no allowlist (see [Security](#security)):

```bash
docker run --rm --user "$(id -u):$(id -g)" \
    --cap-drop ALL --security-opt no-new-privileges \
    --network none --read-only --tmpfs /tmp \
    -v "$PWD:/work" \
    ghcr.io/leshchenko1979/html-render:latest \
    /work/untrusted.html /work/out.png 1280 720 2
```

The image is built from the same commit as the release tarball, on the same tag
push, and its own CI proves it renders: `ldd` resolves the full closure with
none `not found`, `fc-match "DejaVu Sans"` returns DejaVu, and a shipped asset
renders inside it at the right dimensions with glyphs painted. It asserts the
closure **of the image it just built**, not a number copied from a release log —
why that distinction matters is in
[CONTRIBUTING.md](CONTRIBUTING.md#building).

## Use cases

Four things this is used for, each one a file you can name:

- **Slide images from HTML/CSS.** A deck authored as `<section class="slide">`
  becomes one PNG per slide at any scale, with no browser in the loop —
  `--clip` writes `clip_01.png`, `clip_02.png`, … in document order.
- **Open Graph and social cards.** [`assets/og.png`](assets/og.png) is
  2560×1280 — a 1280×640 card at scale 2 — rendered from
  [`assets/og.html`](assets/og.html) by
  `html-render assets/og.html og.png 1280 640 2 --document`.
- **CI visual diffs.** Deterministic output makes a changed pixel a changed
  document: commit the PNG, diff the PNG, fail the build.
- **Report and chart images.** One-pagers and dashboards where the layout is CSS
  and the alternative is a screenshot service you have to keep warm.

## Usage

Two modes. Both take the **viewport in CSS pixels**; `scale` multiplies the
output resolution (scale 2 → a 1280×720 viewport produces 2560×1440).

```bash
# one image
html-render page.html out.png 1280 720 [scale] [--document] [--fragment] [--full]

# one PNG per element matching a selector
html-render --clip deck.html png/ 1280 720 2 'section.slide' [--document] [--fragment] [--scroll]
```

**Single-image mode paints exactly `<width> × <height>`, so a taller document is
cut.** A 2200 px page rendered at `800 600` produces an 800×600 PNG of its top
600 px — exit `0`, nothing on stderr, the rest of the page simply gone. This is
the trap behind the "one-pagers and dashboards" recommendation above: those are
precisely the documents taller than a viewport. Pass `--full` to make the canvas
the document's own scroll height instead (`800×2200` for the same page), or use
clip mode with a selector that matches the whole body, which derives its canvas
from the element. The default stays the viewport because every golden image in
this repo and in CI was rendered that way; `tools/test_full_page.py` pins both
halves — that the default cuts, and that `--full` does not.

`--help` prints those two lines plus every flag and exit code and exits `0`;
`--version` prints the version and exits `0`. Both used to exit `2` with
`unknown flag`, which made `docker run image --help` and every `cmd --help | head`
pipeline read as a broken binary. The output directory is created if it does not
exist, in both modes — a missing `out/` used to panic `101` in single-image mode
while clip mode created it, so the same argument shape gave two answers.

**A file that declares its own document root is rendered as one, automatically.**
Detection reads the first tag after any BOM, whitespace and comments, and matches
it against `<!doctype>`, `<html>`, `<head>`, `<body>`; anything else — a `<div>`,
a `<style>`, bare text — is a fragment and is wrapped in a synthetic
`<html><body>` with a white-background reset.

The two flags override the guess: `--document` forces the document reading,
`--fragment` forces the wrapped one, and together they exit `2` rather than
silently preferring one. The resolved mode is on the `TIMING` line.

This matters more than it looks. Wrapping a real document hands the canvas to the
wrapper's `html{background:#ffffff}` reset, so a page that colours itself via
`body{background:…}` — the common idiom — renders **white**, with identical
geometry and nothing on stderr. Measured against the previous release, a
`body{background:…}` document differs from its `--document` render in **70.4 %**
of pixels, and a file that opens at `<body …>` in **89.9 %** — which is why the
rule matches all four opening tags, not just `<!doctype>`. Passing `--document`
was already the documented advice; the guess means forgetting it is no longer
silent.

`--scroll` (clip mode) paints one element-sized canvas per match instead of one
full-height canvas that is cropped afterwards — the same picture for a third of
the peak memory. Not byte-identical: on the 4-slide demo deck the two strategies
differ on 0 / 75 / 58 / 125 pixels of 3.69 M per slide, never by more than one
channel level, because a scrolled canvas rounds its origin differently. It is a
clip-mode reading and nothing else reads it, so passing it without `--clip`
exits `2` rather than being silently ignored.

```bash
# a 12-slide deck, 2560×1440 per slide, ~93 MiB peak
html-render --clip deck.html slides/ 1280 720 2 'section.slide' --document --scroll
```

Clip mode writes `clip_NN.png` into the output directory (one per match, in
document order) and prints each match's CSS box and physical size:

```
clip_01 css x=0.00 y=0.00 w=1280.00 h=720.00  physical 2560x1440 (scroll)
```

A matched element with no layout box at all (`br`, `wbr`, `script`, `style`, …)
is skipped, not fatal, and the skip is reported on stderr:

```
skipped 3 of 306 matched element(s): no layout box
```

That makes `'*'` usable on a real document. The skip is narrow on purpose:
`hr` **is** emitted, because it is a block box with a default border (measured
`w=400 h=2`), and so is anything else that has geometry — a skip that dropped
whatever it could not handle would pass a casual test and lose real boxes.
`clip_NN` numbering stays contiguous over the emitted boxes. A selector whose
matches are *all* boxless exits `3`, the same code as a selector that matched
nothing.

### Exit codes

Five codes. `tools/test_exit_codes.py` asserts every one of them, and runs in CI
against the binary built from the same commit:

| Code | Means |
|---|---|
| `0` | the PNG, or the whole clip directory, was written; or `--help` / `--version` printed and exited |
| `2` | wrong argument count, an unknown flag, `--clip` not first, or `--scroll` outside `--clip` |
| `3` | the selector matched nothing, or every match had no layout box |
| `4` | an asset fetch hit its deadline: the output was written, but may be missing images |
| `101` | a panic: an unparsable number, an invalid selector, an input file that cannot be read |

`101` is a panic, not a diagnosis: the message names a line in `src/main.rs`
rather than the argument you got wrong, so read the argument before the file.

**`v0.3.0-beta.6` is the first release that satisfies this table.** Earlier ones
do not. A release older than this page is not a scandal — the page moves and
artefacts don't — which is why the published-artefact step in CI is a **hard
gate**: when `Cargo.toml` and the release tag name the same version, a failing
leg fails the build, and a tag that predates `main` only warns, so red still
means something. What each published binary actually did is measured rather than
summarised, in [GATES.md](GATES.md#published-artefacts).

**The failure with no exit code is the one to watch.** Unsupported CSS is
ignored, not rejected: an unimplemented property or value falls back to its
initial value, the layout settles around it, and a plausible PNG comes out. No
warning, no non-zero exit. A clean run proves the document rendered, not that it
rendered as designed — check the properties you depend on against a browser
once, before trusting a deck to this. (A missing *image* is no longer in this
class: the asset deadline exits `4`.)

### Environment variables

Four, all optional, read once at startup from the process environment. They
exist for diagnosis and for reproducing a measurement; none of them changes what
a successful render looks like.

| Variable | Default | Effect |
|---|---|---|
| `BLITZ_DEADLINE_SECS` | `120` | How long a sub-resource fetch may take before the image is painted **absent**. The render still finishes and writes a PNG, but exits `4` instead of `0`, so a slow origin is visible in the exit code rather than only in the picture. |
| `BLITZ_DRAIN` | `quiet` | Selects the sub-resource drain loop, so the documented pattern and the one that works can be compared on the same binary: `naive`, `naive2`, `yield`, `quiet`. `naive` loses images on both runtime flavours; `quiet` is what the shipped build uses. `tools/drain_probe.py` sweeps them. |
| `BLITZ_RUNTIME` | current-thread | `multi` selects the tokio multi-thread runtime, reproducing upstream's `#[tokio::main]` examples. Anything else, including unset, uses the current-thread runtime the CLI ships with. |
| `BLITZ_TRACE` | unset | Set to anything and every drain pass prints its resolve count and emptiness to stderr. Diagnostic only; it never changes the PNG. |

They are deliberately not listed in `--help`: that text is tested against the
argument parser by `tools/test_exit_codes.py`, and an environment variable is not
an argument.

## Fidelity

Measured against Chromium 151 on the same document, same viewport, same scale:
**4.13 %** of pixels differ by more than 16 levels (per slide 0.88 – 11.67 %),
and that residual is antialiasing and image resampling rather than layout — wrap
points match, boxes sit at Chromium's coordinates, and every photo region sits at
a **0 px** integer shift. Against *itself* the renderer is exact, which is the
property a CI gate actually needs.

Both instruments and their measurements are in [GATES.md](GATES.md#fidelity):
the per-region breakdown, the determinism table, and the CSS audit that answers
*which declarations did it drop* by deleting one declaration at a time and
diffing the PNG instead of trusting a list of "supported properties".

The remaining hole: **no fixture exercises non-Latin text shaping or
right-to-left scripts**, and text-dependent rows are confounded by font
availability, so they are a lower bound and not a verdict. If your document is
CJK, RTL, or table-heavy, render one page and look at it.

## What it does

Cascade and selectors, flexbox, grid, inline formatting and line boxes,
`@font-face` (including data URIs), images over `http(s)://`, `file://` and
`data:`, backgrounds, gradients, borders, transforms, absolute and relative
positioning, CSS variables, `box-sizing`, `min`/`max` sizing, auto margins,
`aspect-ratio`, `overflow: clip`.

## What it doesn't

- **JavaScript.** No script engine is compiled in. That is the point: no
  script-execution surface, and no 200 MB of V8.
- **PDF.** PNG only. For paged output see [Alternatives](#alternatives).
- **A canvas taller than the viewport, unless you ask.** Single-image mode cuts
  at `<height>`; `--full` raises the canvas to the document, but nothing here
  paginates a long page into several images. Use clip mode with a selector for
  that.
- **`@page` and print layout.** No page boxes, no `@page` margins, no page
  counters. A document authored for print renders as one continuous page at the
  viewport you asked for. This is not the same gap as "exotic selectors": if you
  need pagination, no selector will get you there.
- **The long tail of CSS.** It is Blitz's CSS support, not Chromium's, so expect
  gaps in newer properties, colour spaces and selector forms. Unsupported CSS is
  **ignored, not rejected** — a gap shows up as a property that had no effect,
  never as an error. See [Exit codes](#exit-codes).

## Alternatives

Use one of these instead when the constraint is theirs, not ours:

- **`wkhtmltoimage`** — when the deliverable is **PDF** as well as images and you
  already ship a Qt stack. This renders PNG only. Conversely, pick this over
  `wkhtmltoimage` when a 25 MB binary with no Qt is the requirement.
- **WeasyPrint** — when the input is HTML *for print*: real `@page`, page
  counters, running headers, page breaks. It is a paged-media engine; this is a
  viewport rasteriser.
- **`resvg`** — when the input is already SVG. Blitz rasterises HTML and CSS, so
  SVG here means an `<img>` wrapper and a quality loss you do not need.
- **Chrome print-to-PDF, or Playwright** — when the page needs JavaScript, or
  when you must match a browser pixel for pixel. That is the 500 MB and the
  13.36 s this exists to avoid, and it is the right call when fidelity to Chrome
  *is* the requirement rather than an implementation detail.

## Security

No JavaScript engine is compiled in, so a document cannot execute code. **That is
not the same thing as a sandbox:** `http(s)://` sub-resources are fetched at
render time with no allowlist, and `file://` is readable, so rendering untrusted
HTML gives that HTML the network reach of the machine you render on. Details,
the measured egress proof, and how to report: [SECURITY.md](SECURITY.md).

## Corpus

`fixtures/` holds 32 documents that each paint elements in exact probe colours, so
geometry can be read out of a PNG without the instrument knowing anything about
the markup. `tools/run_parity.py` renders every fixture through Blitz and through
Chromium and reports per-box deltas:

```
VERDICT  text-free (54 rows): 42 exact, 0 within 1px, 11 divergent, 0 missing, 1 absence
```

The 11 divergent boxes are two open defects: four from consecutive `<br>` runs in
a `font-size:0` inline-block context, where the deltas grow 16 px per extra break,
and seven from table layout — every box in `table-collapse.html` diverges, the
table landing 18 px shorter than Chromium's and two cells at half height
([issue #9](https://github.com/leshchenko1979/html-render/issues/9)).
`tools/parity_baseline.json` pins all eleven, so a *new* divergence
still fails the build.

**`absence` is a pass, not a gap.** One fixture (`margin-auto-stretch`) asserts
that *neither* engine paints a probe, because auto cross margins cancel the
stretch: there is no box to compare, and the absence itself is the assertion.

**This is a gate, not a report.** [GATES.md](GATES.md#corpus) carries the rest:
the six text-dependent boxes that are reported separately, every leg
`run_parity.py` exits `1` on, and `tools/test_parity_gate.py`, which proves each
one fires without needing a browser.

## Contributing

Local Rust compilation is forbidden on the machine this was written on, so **CI
is the only build gate** — the five workflows, what each one asserts, the pinned
runner and the two requirements that are not optional are all in
[CONTRIBUTING.md](CONTRIBUTING.md).

Two rules that bite before you open a pull request:

- **`assets/hero.png` and `assets/og.png` are command output, not images to
  hand-edit.** `tools/check_claims.py` re-renders both and asserts the committed
  bytes, and the README states their real dimensions.
- **After touching any prose number, run `python3 tools/check_claims.py`.** It
  recomputes every derived figure from the inputs at the top of that file and
  fails when the page disagrees. `--selftest` proves the spelled-count guard
  fires.

## Licence

Apache-2.0 — see [LICENSE](LICENSE).
Same licence as Blitz itself, and it carries an explicit patent grant.
