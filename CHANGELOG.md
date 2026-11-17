# Changelog

Newest first. Every entry names its commit range, so it can be re-derived with
`git log --oneline <prev>..<tag>` rather than trusted. Pre-1.0: flags and output
names may change between minor versions, and this file is where that shows up.

## v0.5.0 — unreleased

Branch `fix/open-issues`; the commit range is filled in when this is cut. The
version moved because the CLI contract did: frame names and three exit codes
change, which is breaking under this file's own pre-1.0 rule.

### Fixed — behaviour that exited `0` while doing the wrong thing

- **`pageplate t.html t.html` no longer destroys `t.html`.** The document is read
  into memory before the write, so the render SUCCEEDED: exit `0`, a plausible
  PNG, and the source gone. It now exits `2` and names the collision. Both sides
  are resolved through the filesystem, so `./t.html`, a `..` segment and a
  symlink to the input are refused too ([#36]).
- **A selector whose every match has a zero-area box exits `3`, not `0`.**
  `script`, `style`, `template` and `display:none` report a `0x0` rect rather
  than no rect, and a `1x1` clamp turned that into a 68-byte PNG at exit `0`.
  A selector mixing paintless and real matches still dumps the real ones, and
  both skip causes are counted separately on stderr, so a short dump is
  distinguishable from a complete one. **A `*` dump therefore emits fewer frames
  than it did**: the `1x1` placeholders for `head`, `script` and `style` are gone,
  and the surviving frames keep contiguous `clip_NNN` numbering ([#38]).
- **A single-dash flag is refused.** `-q` in a trailing position was collected
  as a surplus positional and dropped without a word, and `-x` in the html slot
  panicked on a file named `-x`. Both now exit `2` naming the flag; `-h` and
  `-V` keep working ([#39]).
- **A surplus positional is refused** instead of silently ignored ([#39]).

### Fixed — a killed render left a truncated PNG under the real name

- **Every output is now written to a hidden `.<name>.partial-<pid>` sibling and
  renamed into place**, after `sync_all`. The encoder used to stream straight
  into the final path, so any interruption left a file that exists, carries a
  PNG signature, and is not the document — measured on `0.4.0`, a `SIGKILL`
  0.45 s into a 2.5 s render left **69,869 bytes of a 589,369-byte image** with
  no `IEND`, 11.9 % of a real PNG, which a pipeline then diffs against a golden
  and reports as a visual regression that never happened. A killed run now
  leaves the previous file intact plus one `.partial-` file ([#26]).

### Fixed — a `--clip` run that painted nothing deleted the last good dump

- **The stale-frame sweep runs after the paint, not before it**, and removes only
  the tail this run did not write. The sweep existed so a shorter re-render
  leaves no stale frames, and that still holds; what changes is that a run which
  then refuses — every match boxless, a band over the canvas limit — no longer
  empties the directory while naming no file in its exit code. Exit `3` and `5`
  now leave the previous frames untouched ([#35]).

### Added — a machine-readable result

- **`--json` writes one JSON object per PNG to stdout**, with every human
  diagnostic left on stderr, so a pipeline reads a field instead of regexing
  English and a redirect still collects the warnings. Until now the only
  machine-readable surfaces were the `TIMING` line on stderr and the per-frame
  geometry on stdout, and the failure modes a caller must act on — a cut
  document, a substituted font, a refused resource — were all exit `0` with a
  sentence on stderr. The schema is documented in `docs/USAGE.md` and is stable;
  a test leg compares the documented keys against the emitted ones in both
  directions, so a field cannot disappear quietly ([#28]).
- **`substituted_families` is `null`, not `[]`, when no fonts are visible.** The
  check cannot distinguish "nothing was substituted" from "substitution cannot
  be detected on this host", and an empty list would claim the first.

### Added — a caller can turn the network off

- **`--no-network` refuses every sub-resource that is not `file://` or `data:`**,
  and `--allow-host=<glob>` narrows the fetch to a set of hosts (repeatable, `*`
  is any run of characters). Each refusal is named on stderr and the run exits
  `4`, through the same path a `404` already took. Until now a document decided
  what the renderer fetched and the only bound was the asset deadline, so a slide
  service rendering user HTML was an SSRF probe of its own VPC and a one-line DoS
  on its worker pool. Neither flag is the default: a render that silently dropped
  images would be the worse failure, and `SECURITY.md` says so ([#29]).
- **A `200` whose body is not an image exits `4`, naming the resource.** The
  decode failure happens inside Blitz after the bytes were delivered, so the
  fetch layer never saw an error and the run painted an empty box at exit `0`
  with nothing on stderr — a silent content loss wearing a success code. The
  renderer now checks, after the drain, whether an `<img>` whose URL this run
  actually requested still holds no image, which is the only observable the
  failure leaves. An image that was never fetched (`display:none`, inside a
  `<template>`) is not reported, and a resource already named by the fetch layer
  is named once ([#27]).

### Packaging and supply chain

- **The container image is attested and its digest is published.** `container.yml`
  gained `id-token`/`attestations` permissions, an `actions/attest-build-provenance`
  step over the pushed digest with `push-to-registry: true`, and a step that writes
  the digest to the run summary and fails if it is empty. Until now the image was
  the one artefact with no provenance at all — the tarball has been attested since
  v0.4.0 ([#31]).
- **The image is rebuilt weekly** (`0 5 * * 1`), so a base-image CVE does not wait
  for the next release ([#31]).
- **The tarball has shipped `LICENSE` since v0.4.0**; the packaging step now
  asserts it in the same job (`tar tzf … | grep -qx LICENSE`), so a future change
  cannot drop it silently. Apache-2.0 §4(a) requires the licence to travel with
  the artefact recipients actually get ([#32]).
- **A `v*` tag runs parity.** Path filters do not apply to tag pushes, so a release
  tag now runs the full gate against the exact tree being published ([#30]).
- **The reference engine is pinned.** `tools/chrome_reference.txt` records the
  Chrome version the parity numbers were measured against, and `run_parity.py`
  refuses a host whose Chrome differs unless `--allow-chrome-drift` is passed. The
  fidelity figures were only reproducible on one browser build and nothing said
  which ([#34]).
- **RustSec advisory check and an SPDX SBOM** (`supply-chain.yml`): the audit runs
  on a `Cargo.toml`/`Cargo.lock` change, weekly, and on dispatch; the SBOM is
  attached to the run and asserts the renderer's own dependency names are present,
  so an empty or truncated document fails instead of uploading ([#25]).

### Changed

- **Clip frames are named `clip_001.png`, not `clip_01.png`.** Two digits put
  `clip_100` between `clip_10` and `clip_11` in a lexical sort, so a caller that
  globs and sorts a dump interleaved the hundredth frame into the teens. Three
  digits keep lexical order equal to numeric order for any dump the renderer can
  produce. A reader that matches `clip_*` is unaffected; one that hardcodes
  `clip_01` must be updated ([#40]).

### Fixed — documentation

- `--help` exit `4` said "a frame was cut to the canvas width", which names the
  symptom and not the cause. The canvas is `widest.min(65,408)`, so the only
  width a frame is cut to is the **65,408 px** renderer limit ([#33]).
- `docs/USAGE.md` documented the zero-size case accurately as "returns `0` with a
  1×1 PNG" — that was the defect, written down rather than fixed. The row now
  describes what the code does (exit `3`), and the "output path after the run"
  table says what the zero-area path leaves behind ([#38]).

[#25]: https://github.com/leshchenko1979/pageplate/issues/25
[#26]: https://github.com/leshchenko1979/pageplate/issues/26
[#27]: https://github.com/leshchenko1979/pageplate/issues/27
[#28]: https://github.com/leshchenko1979/pageplate/issues/28
[#29]: https://github.com/leshchenko1979/pageplate/issues/29
[#30]: https://github.com/leshchenko1979/pageplate/issues/30
[#31]: https://github.com/leshchenko1979/pageplate/issues/31
[#32]: https://github.com/leshchenko1979/pageplate/issues/32
[#33]: https://github.com/leshchenko1979/pageplate/issues/33
[#34]: https://github.com/leshchenko1979/pageplate/issues/34
[#35]: https://github.com/leshchenko1979/pageplate/issues/35
[#36]: https://github.com/leshchenko1979/pageplate/issues/36
[#38]: https://github.com/leshchenko1979/pageplate/issues/38
[#39]: https://github.com/leshchenko1979/pageplate/issues/39
[#40]: https://github.com/leshchenko1979/pageplate/issues/40

## v0.4.0 — 2026-11-16

Raw list: `git log --oneline v0.3.0-beta.10..v0.4.0` (35 commits). A rename and a
version scheme; no renderer change, so the pixels this release paints are the ones
`v0.3.0-beta.10` painted.

### Breaking

- **Renamed `html-render` to `pageplate`; the binary is `pageplate`.** The tarball,
  the container image and the crate take the new name. `render` is **not** kept as
  an alias: a script that calls it must be updated. Published tags and their assets
  are untouched, and the old URLs redirect.
- **No more `-beta.N` or `-rN` in a version string.** `0.3.0-beta.4-r2` sorted
  *above* `0.3.0-beta.10` under SemVer §11, because an alphanumeric identifier
  outranks a numeric one, so any tool resolving "latest" picked an eleven-day-old
  release. A published number is now final; a re-cut takes a new number.

### Added

- `release.yml` refuses a tag that disagrees with `Cargo.toml` before the build,
  and reads the built binary's `--version` back before publishing.
- `docs/USAGE.md`: every flag, every environment variable, the exit contract and
  both machine-readable streams, with a test that fails if the flag list and
  `--help` disagree. The README now points at it instead of carrying it.
- A `limitation` label on the tracker; the README's known-defect list became a
  filter link, so a defect is tracked once and closed once.

### Changed

- `--help` and `--version` print the name from `CARGO_PKG_NAME`, so the crate name
  and the printed name cannot drift apart.
- The advertised images state the new name and `v0.4.0`, re-rendered from their
  source HTML with the `v0.3.0-beta.10` binary (`sha256 de69ad53…`), which paints
  the same pixels as this release.
- **`bench/results.json` re-measured on the renamed deck** (CI run `43886554380`).
  The deck's `html_md5` moved to `0593ca9f…` because the generator stamps the
  product name into the document it builds, so the previous receipt described a
  document that no longer exists; the drift gate refused to compare the two, which
  is what it is for. Chromium's tree peak on the new deck is 2380.2 MiB and
  pageplate's `--full` is 37.8 MiB, so the ratios moved to 62.97× / 31.57× / 50.11×.

## v0.3.0-beta.10 — 2026-11-15

Raw list: `git log --oneline v0.3.0-beta.9..v0.3.0-beta.10`. This cut carries one
behaviour change and one guard: the default clip canvas now follows the widest
match instead of the viewport, and the figures baked into the advertised images
are now read against the constants they are derived from.

### Changed

- **#17: the default `--clip` canvas is as wide as the widest match.** The shared
  canvas used to be the viewport's width, so an element wider than the viewport was
  cropped at the viewport's edge: a 1600 px element in an 800 px viewport produced an
  **800×100** PNG at `rc=0`, half its content missing, with the numbers that would
  have revealed it (`w=1600.00` beside `physical 800x100`) on the line a caller does
  not read. `v0.3.0-beta.9` made that cut loud (exit `4`); this removes it where the
  width is paintable. Widening is a paint-surface change, not a layout one —
  `paint_scene` builds its root clip from the surface size it is given while the
  document's viewport, and therefore every `vh` unit, is untouched — and the row
  budget is derived from the widened width, so peak memory stays one band at the
  widest frame's width rather than one band at the viewport's. Past the renderer's
  65,408 px limit nothing can be painted, so that residual case is still a cut and
  still exits `4`. `tools/test_clip_wide.py` pins the width, the two strategies
  agreeing on it, and — as the control that stops a widened canvas from passing while
  blank — that the frame's right half carries the ink of a child sitting entirely past
  the viewport edge. On the released `0.3.0-beta.9` binary seven of its legs fail.

### Fixed

- **The hero and og images advertised `v0.3.0-beta.6` for four releases, with
  every gate green.** `tools/check_claims.py` re-rendered each committed PNG and
  compared its bytes to the HTML that produced it, which proves the image is the
  source's output and nothing else: it never read the figures *inside* the HTML.
  Both assets state the same measurements the README derives — the `2.49x` tile,
  the `5.37 s vs 13.36 s` caption, the `93.4 vs 858 MiB` caption, two bar widths
  that encode those ratios as pixel widths, and a version kicker — so each was a
  second copy of a fact that nothing compared. The kicker was written correctly in
  `814ac50` (Cargo.toml read `0.3.0-beta.6` then) and never moved again while
  `beta.7`, `beta.8`, `beta.9` and this cut passed every gate. The guard now reads
  all thirteen figures out of both HTMLs, and `skills/html-render/SKILL.md` § Releases requires a
  release to move the README, both assets and the regenerated PNGs in one commit.
  Falsified before trusting it: a hero ratio of `2.48` and a bar width of `40.1 %`
  each fail by name, and the real drift failed the same way before the kickers were
  fixed. `og.html` carries the same tiles minus the chart.

## v0.3.0-beta.9 — 2026-11-15

Raw list: `git log --oneline v0.3.0-beta.8..v0.3.0-beta.9`. This cut closes the
silent-wrong-output class the previous one started on: four issues that each
produced a valid PNG of the wrong thing at exit `0` now either say what went
wrong or refuse. It also makes the published tarball verifiable — provenance
attestations are signed by this workflow's own OIDC identity, so the artefact can
be traced to a commit without trusting a checksum file that ships beside it.

### Corrected

- **The table divergence was stated backwards, in three places.** `v0.3.0-beta.5`
  below says the `table-collapse` table is "18px shorter than Chromium's" and one
  cell is "12px tall where Chromium paints 28". Both are inverted: the parity run
  prints the `chromium` column first and `blitz` second (`tools/run_parity.py:157`),
  so Blitz paints the table **18px taller** (104 against 86) and that cell **28px
  tall where Chromium paints 12**. The same inversion was in `GATES.md` and in
  #9's table. Corrected in both living documents; this released entry is left as
  written, because a changelog records what was believed at the time.
- **The advertised fidelity figure is not reproducible from this repo.** The
  `4.13 %` pixel difference and the performance rows come from a 12-slide deck
  that is not here, which `GATES.md` already said ("history, not a gate") and
  `README.md` did not. The README now says so, and names the corpus as the
  reproducible number.
- **`README.md` said "seven known defects".** There are five that produce wrong
  output with exit 0, and three of those are now measured on `0.3.0-beta.8`:
  #16 and #17 no longer reproduce as filed (see below).

### Fixed

- **#11: a sub-resource that fails now exits `4`.** `blitz_net::Provider::fetch`
  reported a failed fetch through `tracing` (compiled out) and never called the
  handler, so a `404` left the node pending and the run exited `0` with a valid
  PNG of an empty box. Measured on the released binary: **795 bytes of all-white
  output**, nothing on stderr. The provider is now driven through
  `fetch_with_callback`, the public path that hands back the `Result`, and every
  failure is named before exit `4`. The same wrapper caps one decoded image at
  **128 MP** (`BLITZ_MAX_IMAGE_MP`): a **60-byte** PNG header claiming
  `30000x30000` asks for 3.6 GB before a pixel is decoded and aborts with
  `rc=134`, which names no file. The size is read from the header, so the
  refusal costs no allocation, and a real `9000x9000` PNG still paints in
  **2.36 s**. `tools/test_assets.py` pins all of it in 10 legs.
- **#12: a font the host does not have is reported.** fontconfig always resolves
  something, so `font-family:'Absolutely Nonexistent Font XYZ 9999'` painted
  **8238 non-white pixels** of DejaVu Sans at exit `0` with nothing on stderr.
  Every family that no installed font provides is now named once on stderr. The
  exit code stays `0` — the output is still a render of the document, and the
  caller decides. Generic keywords and `@font-face` families are excluded, and a
  host where no fonts are visible says so rather than reporting an empty list as
  a clean document. `tools/test_fonts.py` pins it in 4 legs.
- **#16 no longer reproduces.** A 70,000 px document under `--full` exits `0` and
  writes a correct `800x70000` PNG, measured on the released binary; the `u16`
  ceiling is gone from the banded path and still refuses with exit `5` under
  `BLITZ_BAND_MIB=0`, which `tools/test_exit_codes.py` pins in both directions.
- **#17 is no longer silent.** An element wider than the viewport now exits `4`
  with `1 frame(s) cut to the canvas width` instead of exiting `0`. The truncation
  itself remains (`--per-element` renders it at its full 1600x100, measured on the
  same document), but a pipeline can now see it. #17's body still says `--scroll`,
  a flag renamed to `--per-element` before this release.
- **#10: a document taller than the canvas says how much it lost.** `--clip` and
  `--document` both paint a fixed canvas, so a page past that height was cut with
  nothing on stderr and exit `0` — the PNG was valid, complete-looking, and short.
  Both modes now measure the laid-out content height, print
  `content_h=`/`canvas_h=` on the `TIMING` line, and warn
  `document is {h}px tall but the canvas is {phys_h}px: the bottom {h-phys_h}px
  were cut (pass --full to paint the whole document)`. The exit code stays `0`:
  the render succeeded at the size it was asked for, and `--full` is the fix.
  `tools/test_full_page.py` pins the warning (`f-signal`) and its absence on a
  document that fits (`g-nosignal`), so a renderer that always warns fails too.
- **#14: the corpus had no non-ASCII byte in it.** 32 fixtures, every one Latin-1,
  so shaping, bidi and combining marks were unmeasured while the page quoted a
  fidelity number. Four fixtures added (`arabic-joining`, `hebrew-rtl`,
  `combining-marks`, `direction-rtl-latin`), corpus now **36**, and
  `tools/test_scripts.py` gates five legs. Two things the fixtures taught, both
  recorded in `GATES.md`: CSS `direction` does **not** drive RTL ordering — the
  bidi algorithm does, and flipping the property leaves geometry byte-identical,
  so only `direction-rtl-latin` isolates it — and CJK cannot be measured here at
  all, because the runner has no CJK font and `日`/`本`/`語` render as
  byte-identical tofu. A test that passes on tofu is a test that proves nothing.
- **#13: the release is now attestable, not just checksummed.** `release.yml`
  gained `id-token: write` and `attestations: write`, and a step using
  `actions/attest-build-provenance` (`4d101475…`, `v4.2.2`) runs before Publish,
  signing `dist/*.tar.gz` and `dist/checksums.txt`. The publish step now verifies
  both with `gh attestation verify` against the downloaded files, and the README
  says which guarantee is which: the checksum proves the download is intact, the
  attestation proves this repository, commit and workflow built it. Signing the
  assets is not a substitute for a signature on the *binary* — that gap is #13's
  other half and is not closed here.

## v0.3.0-beta.8 — 2026-11-15

Raw list: `git log --oneline v0.3.0-beta.7..v0.3.0-beta.8`. This cut exists
because `beta.7` is the artefact that fails the contract this tree now
documents: it predates banded painting, so on it a document past the canvas
ceiling still exits `5` under `--full` and never emits the `BLITZ_BAND_MIB`
remedy the exit-code contract asserts. Nothing about `beta.7` is wrong for what
it shipped; the page simply moved on, and the published tarball and the page
have to describe the same object.

**Changed**

- `--clip` paints the document in row bands instead of one document-height
  canvas, cutting each frame out of the band that contains it and scrolling to
  the next band when it runs past. Peak memory is one band (`BLITZ_BAND_MIB`,
  default 4) rather than the page, and the total page height stops mattering:
  a 72,000 px document that used to exit `5` now renders all its frames. The
  per-canvas ceiling still applies to one band and to a single element, so a
  frame taller than 65,532 px is still refused.
  The pixels are NOT byte-identical, and the first version of this entry claimed
  they were. Scrolling the viewport shifts the subpixel antialiasing phase, so a
  handful of glyph pixels land one level out of 255 differently: measured
  **32 of 40** frames on a 48,000-row deck, max channel delta **1**, two to ten
  pixels per frame, inside the text. What is guaranteed instead is what the gate
  actually reads. `tools/test_bands.py` asserts (a) on a 60,000-row deck the
  measured probe geometry is identical and no channel moves by more than one
  level; and (b) a 72,000-row document renders where it used to be refused. The
  54-row corpus verdict is safe for a structural reason rather than a measured
  one: `run_parity.py` renders fixtures with no `--clip`, and `--clip` is the
  only path that bands, so banding cannot enter that verdict at all. Gate C does
  render the deck with `--clip`, so it is the leg that exercises the banded path
  against Chromium, and it passed on this change. The test's first leg is a
  negative control — the single-PNG path renders identically at 1 MiB and 0 —
  which catches banding leaking into a mode that must not have it.
- `--full` streams too: the page is painted in row bands and each band's rows
  go straight into the PNG encoder, so `width x document height x 4` is never
  materialised and the height ceiling no longer applies to it. A 70,000 px
  document that exited `5` now renders whole. Two costs, both named rather than
  hidden. The encoder streams, so `IHDR` height must be known up front — it is,
  from `scroll_height`. And a `position:fixed` element cannot be banded at all:
  blitz repaints it at every band's top (measured: a red fixed header appears in
  all six frames of a six-band deck), so the renderer falls back to one canvas,
  warns on stderr, and `tools/test_full_page.py` asserts the fallback image is
  byte-identical to the `BLITZ_BAND_MIB=0` render. The same test asserts a
  2200 px banded `--full` is byte-identical to the single canvas it replaced,
  on a document tall enough (two bands) to reach the mechanism.
- `GATES.md` claimed `--clip` output is byte-identical to cropping the
  `--document` render, "0 differing pixels, 12/12". Banding falsified that and
  nothing went red, because the claim was prose that no test read. Measured on a
  12-slide deck spanning 11 bands: **1 of 12** frames identical, 11 differing by
  max channel delta **1**, the differing-pixel count growing with band index
  (0 → 28 px) — the same antialiasing phase shift, not a layout difference. The
  sentence now states the tolerance it actually has, and `tools/test_bands.py`
  leg 4 asserts it frame by frame against the `--full` render, plus a mechanism
  check that at least one frame was painted at a non-zero scroll offset: without
  it, a fixture whose glyphs are too small to shift passes the leg while never
  reaching the path the leg exists to guard (it did, at 16 px text).
- The band budget defaults to **4 MiB**. The budget only matters when frames
  are short relative to it: `plan_bands` gives a frame taller than the budget a
  band of its own, so on a deck of tall slides 1, 4 and 16 MiB produce one
  identical plan. An earlier version of this entry ranked the budgets on exactly
  such a deck (1 MiB / 471 bands, 4 / 118, 16 / 30) — that column was arithmetic
  never observed, and it is withdrawn. On a deck that can tell them apart
  (400 frames x 100 CSS px, 1280 phys wide, medians of nine interleaved runs):
  1 MiB / 200 bands / 31.1 MiB / 2.01 s, **4 MiB / 50 / 37.3 / 1.77**,
  16 MiB / 13 / 61.0 / 1.78, 64 MiB / 4 bands, off / one 515 MiB canvas. A
  smaller budget buys 6 MiB of peak and pays 0.24 s for 150 extra repaints, so
  4 MiB is the knee, not 1. `tools/test_bands.py` asserts the default's plan
  from a `BLITZ_TRACE=1` line (50 bands, 1,310 rows/band on a 100-slide deck),
  because peak RSS can say "something was small" but never which budget ran.

## v0.3.0-beta.7 — 2026-11-14

Raw list: `git log --oneline v0.3.0-beta.6..v0.3.0-beta.7`. This cut exists
because `beta.6` is the artefact that fails the contract this tree now
documents: on it a document past the canvas ceiling panics at `rc=101` after
writing part of the output, and an element wider than the viewport is cut
silently at `rc=0`. Both are refusals with a named exit code here, so the
published tarball and the page describe the same object again.

**Fixed**

- The renderer truncates a canvas dimension through a `u16`, so a document past
  65,532 px tall — or a canvas past 65,408 px wide — panicked with an
  out-of-range index after writing part of the dump: an 80-slide deck left 34
  frames behind at `rc=101`, and a 72,000 px document produced a 6,464-row
  buffer. Both ceilings are measured one pixel either side on a released
  binary. `refuse_oversized_canvas` now exits `5` before painting, with nothing
  written.
- Default clip mode cut an element wider than the viewport to the canvas width
  and reported success: a 1600 px element in an 800 px viewport became an
  800×100 PNG at `rc=0` with half the element gone and nothing on stderr. It
  warns per frame and the run exits `4`, the code that already means "written,
  but known to be incomplete".
- `Vec::with_capacity((w * h * 4) as usize)` multiplied in `u32` before the
  cast, so past ~4.29 G pixel-bytes the capacity wrapped to a small number and
  the buffer silently reallocated instead. Sized in `usize` now.
- A wrapped fragment held the HTML twice: `format!` copied `source` into
  `doc_html`, and `source` then stayed alive to the end of `main`.
- The crop path allocated a frame-sized scratch buffer per slide — 14.7 MiB at
  2560×1440 scale 2, on a deck that can hold 200 of them. One buffer, reused.

- The corpus verdict line said `text-free (54 boxes)` while one of those 54
  entries is an absence assertion with no box at all — `run_parity.py`'s own
  comment two lines above says "there is no box to compare and the absence
  itself is the assertion". 53 boxes, 54 rows. The baseline key is now
  `text_free_rows`, and the line says `rows`.
- That count check was guarded by `baseline.get("text_free_boxes") is not None`,
  so renaming the key would have skipped it silently — a check that passes
  because it never ran. A baseline missing the key is now a hard failure, with
  a leg in `test_parity_gate.py` that proves it.
- `tools/test_parity_gate.py` printed a hand-written `legs run: 12 / PASS`. It
  counts its legs now. A hardcoded leg count is the same class of lie as a
  hardcoded measurement: it stays 12 when a leg is added, and 12 when one is
  deleted.
- The asset-deadline leg bet on a non-routable address (`10.255.255.1`) to make
  a fetch block. That is a bet on network failure MODE, not on the renderer:
  while the kernel retries, connect hangs and the deadline trips; once an ICMP
  unreachable is cached, connect fails in milliseconds, the deadline never
  arrives, and the leg reports a product defect that is really a routing
  coincidence. It now blocks on a localhost socket that accepts and never
  writes, so the fetch blocks by construction.
- `tools/test_exit_codes.py` claimed in its own docstring that r2 fails "21
  leg(s) ... across 14 distinct legs". Neither number was measured: the run
  prints 22 failed assertions across 21 legs. Both totals are counted at
  runtime now, and the docstring states what the script prints instead of
  what a previous edit remembered.

**Changed**

- `--scroll` is now `--per-element`. The old name described the mechanism —
  every strategy here scrolls — and named neither the choice nor its inverse;
  `--per-element` says what it paints, one element-sized canvas per match,
  against the default's single document-height canvas. `--scroll` stays
  accepted as a hidden alias so no caller breaks, and stays out of `--help`: a
  documented alias is a second name to keep true.
- `--help` said `--clip` paints "one canvas per matching element", which is
  what `--scroll` does. The default paints one document-height canvas and cuts
  every frame out of it. `--full` moved from `FLAGS` to `MODES` for the same
  reason: it is a mode, not a modifier.
- README rewritten from scratch against a researched structure rather than
  edited in place: 522 lines to 164, one screen per section. Every figure the
  CI guard checks is still present, so `check_claims.py` reads 37 claims PASS
  and `--selftest` 5/5 — but they now sit where a cold visitor meets them
  instead of inside the methodology that derived them.
- The seven open defects left the README as prose and became issues #9-#15,
  each filed with the measurement that exposes it. The README keeps a single
  pointer to the tracker and no per-defect prose: the repro, the deltas and
  the dates live on the issues.
- `GATES.md` was referenced by five workflows and the guard but linked from
  nowhere in the README — the measurement document was orphaned while the page
  carried its contents. It is linked now, under "Where the detail lives".
- `## Fidelity` answers the visitor's question in two paragraphs instead of
  carrying the instrument: the `--scroll`-vs-`--clip` self-consistency check,
  the 54-row verdict line and the 4+7 defect split moved to `GATES.md#corpus`.
  The four guard claims that pinned those strings to `README.md` were retargeted
  to `GATES.md` in the same commit, so every number is still checked against
  the arithmetic — just in the file that owns the measurement.

**Documented**

- The README's account of a failed sub-resource was wrong: it claimed the
  decode OOM was swallowed into a blank PNG at exit 0. Measured on beta.6 it
  aborts with `memory allocation of 324000000 bytes failed`, exit 134, and
  writes no file. Issue #11 carries the correction and the README no longer
  repeats the claim.

## v0.3.0-beta.6 — 2026-11-14

Raw list: `git log --oneline v0.3.0-beta.5..v0.3.0-beta.6`. This cut exists so
the published tarball finally passes the contract the page documents: `beta.5`
failed 8 of the 21 legs, and CI can now prove that against the release itself
rather than against its own build.

**Added**

- `--full` paints the whole document instead of the viewport. Single-image
  mode sized the canvas from the width/height arguments and cut anything
  taller — 2200 px at `800 600` produced an 800x600 PNG, lost the bottom 1600
  px, and exited `0` with nothing on stderr. `--scroll` is clip-only, so it
  never helped here. The escape hatch existed (`--clip body` returns 800x2200)
  but had to be guessed. Height now comes from
  `root_element().scroll_height()`; `--full` with `--clip` is a usage error
  (`2`) because they are two different modes.
- `tools/test_full_page.py`: leg (a) pins the truncation trap itself so a
  change to the default is visible, (b) and (c) prove `--full` raises the
  canvas to the document and never below it. Against the pre-fix binary (a)
  passes and (b)/(c) fail.
- CI runs the exit-code contract against the **published tarball**, not only
  against the binary the job just built. Every previous step tested the
  latter, which is how the page and `beta.5` drifted apart with both
  considered verified. The step downloads the release, verifies
  `checksums.txt`, and runs the suite; it is soft until a release is cut from
  `main`, then it becomes a hard gate.

**Fixed**

- `--scroll` without `--clip` is a usage error (`2`). It was accepted in
  single-image mode and ignored, so the flag looked honoured while the output
  was the plain full-document render — the same "did something else, said
  nothing" class as a mistyped flag.
- Clip mode sweeps `clip_NN.png` frames left by a previous, longer run before it
  paints. Numbering restarts at 1 each run, so a re-render matching fewer
  elements left the old tail in place and a caller could not tell a 3-frame dump
  from a 9-frame one. The sweep matches `clip_` + digits + `.png` exactly and
  reports the count.
- An asset that never arrives now exits `4` instead of `0`. The drain loop used
  to print `warning: asset fetch deadline reached, painting anyway`, write a
  plausible PNG missing its images, and report success — the one failure mode
  with no exit code at all. The output is still written (diagnosis beats
  nothing) but the run no longer claims success.
- `--help` and `--version` are honoured anywhere in argv, not only at `argv[1]`.
  `html-render in.html out.png 794 900 --help` fell through to the arity check
  and exited `2`, so the flag looked broken from the position a reader is most
  likely to type it.

`tools/test_exit_codes.py` grew from 15 legs to 21 and pins all five: against
the pre-fix binary every new leg fails, and against `v0.3.0-beta.4-r2` the
suite prints 21 failed assertions across 14 legs.

## v0.3.0-beta.5 — 2026-11-13

Everything landed since `v0.3.0-beta.4-r2`. The raw list is
`git log --oneline v0.3.0-beta.4-r2..v0.3.0-beta.5`. This release exists to
close the gap the fourth review round put first: `v0.3.0-beta.4-r2` predates
every CLI fix below, so its tarball disagreed with the README on `--help`,
`--version`, unknown flags and output-directory creation. A release cut from
`main` is what makes the page and the artefact describe the same program.

**Fixed**

- `--clip` no longer aborts on an element with no layout box. `'*'` matched
  `<br>`/`<wbr>`/`<script>`/`<style>` in any real document and the run died on
  the first one; those are now skipped, counted on stderr, and a selector whose
  matches are *all* boxless exits `3`. `<hr>` is not boxless and still clips.
- An unknown flag is an error (`2`) instead of being silently dropped.
  `--documnet` rendered a fragment of a full document and wrote a plausible PNG
  of the wrong thing with nothing on stderr.
- `release.yml` is idempotent: re-dispatching a published tag replaces its assets
  instead of failing on `gh release create`, and the build resolves the
  dispatched tag rather than only asserting it exists.

**Added**

- `tools/css_audit.py`: reports which declarations change no pixel, by removing
  one at a time and diffing the PNG. No supported-property list — that rots with
  Blitz, and a stale list calls a dropped declaration fine. Says "no pixel
  effect", never "unsupported", because a pixel diff cannot tell a dropped
  declaration from one another rule already sets.
- `Cargo.lock`, resolved by `lockfile.yml` (326 packages) and committed back.
  Five crates were ranges, so two builds of one commit could resolve different
  graphs while `GATES.md` quoted numbers as if they could not.
- A container image (`container.yml`, GHCR): 13 libraries, the glibc floor and
  `DejaVu Sans` discharged once. The binary is not self-contained, and a host
  without DejaVu re-wraps every line with no warning.
- `dependabot.yml` gains the `cargo` ecosystem, which it could not have before
  the lockfile existed.
- `tools/test_parity_gate.py`: 12 legs over the corpus gate's pure function, 8 of
  them negative controls, so the gate is provable on a machine with no browser.
- `tools/test_undefined_names.py`: walks every scope with `symtable` and reports a
  name that is read but bound nowhere. `py_compile` accepts those, which is how
  `run_parity.py` reached CI with a `NameError` on its own return statement.
- Fixture 32 (`table-collapse`), the corpus's first `<table>`. It found a real
  defect: all seven of its boxes diverge, the table is 18px shorter than
  Chromium's, and one cell is 12px tall where Chromium paints 28 (#9).
- `--help`/`--version` print USAGE, ARGS, FLAGS and EXIT CODES and exit `0`
  instead of `2`.

**Changed**

- Builds use `cargo build --release --locked`, so a `Cargo.toml` that resolves
  to a different graph fails instead of silently re-resolving.
- `parity` runs on a `paths` allowlist with per-ref `concurrency`, so a docs
  commit no longer buys a Rust build and a superseded push stops paying for one.
- Every `uses:` is pinned to a commit SHA with the version in a trailing comment.
  `dtolnay/rust-toolchain` is the one exception: its ref name *is* the toolchain
  spec, so a bare SHA makes it fail on `'toolchain' is a required input`. It is
  pinned to a `master` commit with `toolchain: stable` passed explicitly.
- The corpus gate actually gates. `run_parity.py` used to print a verdict and
  return it without ever setting an exit code, so four divergences passed every
  push; it now exits `1` on a new or grown divergence, on a baseline that went
  stale, and on a missing box.
- `drain-probe` is dispatch-only. It answers one question and was paying a
  build per push to answer it.

**Documented**

- Exit codes, including the failure that has none: unsupported CSS is ignored,
  so a gap renders as a property that had no effect, never as an error.
- `SECURITY.md`: `http(s)` sub-resources are fetched at render time with no
  allowlist and `file://` is readable, so untrusted HTML inherits the network
  reach of the host. No JS engine is not the same thing as a sandbox.
- `@page` and print layout are their own documented gap, not lumped into
  "exotic selectors".
- Determinism is scoped to documents with local or `data:` resources; a remote
  image that misses `BLITZ_DEADLINE_SECS` paints as absent.
- `assets/demo-deck.html`: four slides, pure layout, so the clip and determinism
  recipes run from a fresh clone.
- Workflow trigger table corrected to match the workflows.

## v0.3.0-beta.4-r2 — 2026-11-05 [YANKED]

> Yanked 2026-11-16: this tag and `-r1` encode a rebuild into the version string, and
> under SemVer §11 `0.3.0-beta.4-r2` outranks `0.3.0-beta.10` — so a resolver reads the
> oldest release as the newest. The tag and its asset are left in place (published
> numbers are immutable); this marker is the notice. Nothing here should be pinned.

`git log --oneline v0.3.0-beta.4-r1..v0.3.0-beta.4-r2` — 10 commits.

**Renamed.** `blitz-render` → `html-render`, binary `render` → `html-render`.
The r1 asset and install snippet name the old repo, which is why `-r2` exists.
**Apache-2.0**, which the repository has carried in every commit since it was
created: the rename did not change the licence, and neither the old nor the new
name was ever published without one.

**Added**

- `--scroll` clip mode: one element-sized canvas per match instead of one
  full-height canvas cropped afterwards — same pixels, ~3× less peak memory.
- Drain-probe workflow and `BLITZ_DRAIN` modes, which established that the
  shipped "four empty polls + yield + 2 ms" loop is the shape that makes
  sub-resource images arrive.
- Trace flag on `run_mode()` to measure resolve passes rather than infer them.

**Changed**

- README rewritten as a product page rather than a spike log.
- `GATES.md` cost table corrected after measuring both tokio runtime flavours.
- `__pycache__` untracked.

## v0.3.0-beta.4-r1 — 2026-11-04 [YANKED]

> Yanked 2026-11-16: tagged `0.3.0-beta.4-r1` while `Cargo.toml` still said `0.1.0`,
> so `--version` and the tag disagreed on a shipped release. Superseded by `-r2` and
> then by the `0.4.0` scheme. Tag and asset left in place; nothing here should be pinned.

`git log --oneline v0.3.0-beta.4-r1` — 18 commits from the first spike.

**Added**

- The renderer itself: Blitz (Stylo + Taffy + Vello) to PNG, no browser, no JS.
- Device scale, per-element clipping (`--clip`) and full-document rendering.
- `tools/gates.py`: the three switch gates as one runnable script, plus
  scale-2 determinism and a photo/text appearance split.
- A parity corpus of probe-colour fixtures measured against Chromium, and the
  `ogre` column so all three engines shared one machine.

**Fixed**

- Asset fetches are drained without racing the first poll; the renderer runs
  inside a tokio runtime.
- Clip rects scale to physical pixels; CSS offset and physical size are
  reported per match.
- `blitz_dom::Point` is a plain struct, not euclid's `Point2D`.

**CI**

- Runner pinned to `ubuntu-22.04` with a `GLIBC_*` guard at 2.35 — `latest`
  produced a binary that would not start on a glibc 2.39 host.
- `libfontconfig1-dev` installed before building Stylo.
- Release workflow publishes the asset **and then verifies it**: download it
  back, check `checksums.txt`, render a PNG, assert its dimensions.
