# Changelog

Newest first. Every entry names its commit range, so it can be re-derived with
`git log --oneline <prev>..<tag>` rather than trusted. Pre-1.0: flags and output
names may change between minor versions, and this file is where that shows up.

## Unreleased

Since `v0.3.0-beta.4-r2` (`git log --oneline v0.3.0-beta.4-r2..HEAD`).

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

**Changed**

- `parity` runs on a `paths` allowlist with per-ref `concurrency`, so a docs
  commit no longer buys a Rust build and a superseded push stops paying for one.
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
Relicensed **Apache-2.0** (was unlicensed), carrying Blitz's patent grant.

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
