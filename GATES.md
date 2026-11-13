# Gates

The three gates that decide whether `html-render` can stand in for a browser
for static slide rendering. All numbers below were measured on one 4 vCPU Linux
host (glibc 2.39) with Blitz `0.3.0-beta.4` and Chromium 151, and all of them
are reproducible with `tools/gates.py` in this repo.

The document under test is not part of this repo: a 12-slide, 4.16 MB deck
(embedded JPEGs, four `@font-face` faces, 78 CSS rules). Pass your own with
`--deck`.

```
python3 tools/gates.py --binary ./render --deck deck.html --chromium $(which google-chrome)
```

## A · determinism

Ten renders of the same input at `--scale 2` must be byte-identical.

| | |
|---|---|
| result | **PASS** |
| md5 of all ten | `1dffe1ae…` (one distinct hash) |
| median wall | 2.51 s |
| peak RSS | 86.7 MiB (single process: tree sum = process max) |

Determinism is the gate that makes the rest useful: without it, output cannot be
diffed in CI, and a pixel comparison is noise.

## B · device scale

`--scale 2` must produce exactly twice the physical output of `--scale 1`, and
agree with Chromium at both scales.

| scale | output | mean channel delta | pixels off by >16 |
|---|---|---|---|
| 1 | 1280×720 | 5.31 | 3.15 % |
| 2 | 2560×1440 | 5.53 | 2.66 % |

**PASS**: the 2× invariant holds exactly and fidelity does not degrade with
scale.

## C · element clip

Every `section.slide` clipped to its own PNG must match the dimensions Chromium
produces for the same element.

| | |
|---|---|
| result | **PASS** |
| slides | 12/12 at 2560×1440 |
| whole deck | 4.13 % of pixels off by >16 |
| per slide | 0.88–11.67 % |

Two structural checks, because a gate that passes on a wrong mechanism is worse
than no gate:

- `--clip` output is **byte-identical to cropping the `--document` render**
  (0 differing pixels, 12/12), so the clip branch cannot hide a defect the
  full-document measurement missed.
- clip mode is deterministic too: 3 runs, one md5.

## What the residual is

The 4.13 % is antialiasing and image resampling, not layout:

- best integer shift on every photo region is **0 px**, so nothing is displaced;
- 8× downsampling collapses the difference (mean 20.4 → 5.73), which is the
  signature of edge noise rather than misplaced geometry;
- photos are pixel-exact in the region mask at scale 1 (0.00 % off by >16);
  at scale 2 they read 0.3-3.9 % off, which is the 2x upscale filter rather
  than a displacement, since the shift stays 0 px.

So `html-render` will never match Chromium at 99 % of pixels, and that is not
the useful target: determinism means it can be diffed against **itself**
byte-exactly, which is what a CI gate needs.

## Cost

Same host, same job. **All three figures below are per-process maxima**, taken
with `/usr/bin/time -f %M`, which reports the largest single process in the tree
rather than what the host has to hold. The deck they were measured on — 12
slides, 4 MB — is not in this repo, so these rows are history, not a gate:

| engine | wall | peak RSS (largest process) |
|---|---|---|
| Chromium 151, Playwright, 12 element screenshots at dsf=2 | 13.36 s | 858.4 MiB |
| `--scroll` | **5.37 s** | **93.4 MiB** |
| `--clip` (one full-height canvas, cropped) | 5.72 s | 275.2 MiB |

That method is not comparable between the two engines, and the size of the error
is measured: on `assets/demo-deck.html` at 2560×1440, scale 2, Chromium's process
tree peaks at **1703.5–1826.2 MiB** across four runs while its largest single
process reads **297.1–299.2 MiB** — a **5.7–6.1×** undercount. `html-render` is one
process, so the two methods agree for it (tree 81.2 MiB, max 81.0 MiB). The
memory ratios above are ratios of the wrong quantity and understate the tree
difference; the wall times are unaffected. Re-measuring the external deck is one
command once it is at hand:

    python3 tools/measure_rss.py --rss-mode tree -- <engine command>

`--scroll` paints one element-sized canvas per region instead of one
full-height canvas, which is where the 3× memory difference comes from — that
comparison is `html-render` against itself, so both sides are single-process and
the per-process method is the right one there. It is
lossless: `--scroll` and `--clip` differ on 3,551 of 44,236,800 pixels
(0.0080 %), max channel delta 2/255, which is antialiasing at fractional
offsets.

## Traps

Each of these cost real time, and none of them raises an error.

1. **The drain loop can exit before the image is ingested.** The loop in
   `examples/screenshot.rs` breaks on `net.is_empty()`, which reports the
   *provider's* waker count — but the loaded asset arrives on the *document's*
   message channel, and `resolve()` is the only thing that drains it. So the loop
   can break with the image fetched and still queued: measured **0/8** images on
   both runtime flavours. One trailing `resolve(0.0)` after the loop gives
   **8/8** (that is why `examples/paint_bench.rs` survives — it has one and
   `screenshot.rs` does not). `yield_now()` is *not* the fix: it rescues the
   current-thread case only and reads 0/8 on multi-thread. `is_empty()` itself is
   not racy: `fetch()` clones the waker before `spawn()`. Upstream #1419.
2. **`--clip` mixes two coordinate systems.** `get_client_bounding_rect()` and
   painter node coordinates are **CSS** px; the canvas and the PNG are
   **physical** px. Feed CSS px to the painter and the offsets are right but the
   output is half resolution; feed physical px and everything is culled to a
   blank slide. Neither case errors.
3. **Never raise the viewport to document height.** A real deck resolves `vh`
   units and `height: 100%` against the viewport, so a taller viewport relayouts
   the page and the comparison becomes meaningless. Paint one tall canvas and
   crop, or use `--scroll`.
4. **Know the document height.** A 12-slide deck with 24 px gaps is 8904 CSS px,
   not 8640. Guessing short makes the last slide read as 37.6 % wrong when the
   real figure is 1.41 %: the crop, not the renderer.
5. **`--help` exits 2.** Usage goes to stderr, so `set -euo pipefail` fails on a
   successful invocation. Use `|| true` in scripts.

## Withdrawn claims

**"Blitz makes N line boxes where Chromium makes N−1."** Filed as
[DioxusLabs/blitz#1391](https://github.com/DioxusLabs/blitz/pull/1391) and
withdrawn. With the fix reverted, unpatched Blitz renders `x<br>`, `x<br><br>`
and `x<br><br><br>` as 1, 2 and 3 lines: already correct. The real signature in
the `br-run-blocks` fixture is deltas of 6/22/38/54 px, growing by 16 per extra
`<br>`; a wrong line *count* gives a constant offset and cannot grow. The cause
is a line-box *height* computation in a `font-size: 0` + inline-block context,
still unidentified and unfiled.

**"`Provider::is_empty()` races the first poll."** Also wrong: see trap 1.
