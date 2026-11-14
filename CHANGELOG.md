# Changelog

Newest first. Every entry names its commit range, so it can be re-derived with
`git log --oneline <prev>..<tag>` rather than trusted. Pre-1.0: flags and output
names may change between minor versions, and this file is where that shows up.

## Unreleased

**Fixed**

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

## v0.3.0-beta.4-r2 — 2026-11-05

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

## v0.3.0-beta.4-r1 — 2026-11-04

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
