# html-render

HTML and CSS in, PNG out, on a machine with no browser anywhere in the loop.
Rust, one process, one binary.

`0.3.0-beta.10`, one maintainer. Linux x86_64, glibc ≥ 2.35; no macOS, no Windows.

![A 3200×1800 rendering of a slide headed "HTML + CSS → PNG, with no browser", with bar charts of wall time and peak memory against Chromium](assets/hero.png)

One command produced the PNG above:

```bash
html-render assets/hero.html hero.png 1600 900 2 --document
```

[![parity](https://github.com/leshchenko1979/html-render/actions/workflows/parity.yml/badge.svg)](https://github.com/leshchenko1979/html-render/actions/workflows/parity.yml)
[![release](https://img.shields.io/github/v/release/leshchenko1979/html-render)](https://github.com/leshchenko1979/html-render/releases)
[![licence](https://img.shields.io/github/license/leshchenko1979/html-render)](https://github.com/leshchenko1979/html-render/blob/main/LICENSE)

## Why it exists

Rendering a page to an image in CI normally means shipping a browser:
hundreds of megabytes of image,
a JavaScript engine the job never uses,
a cold start before the first pixel exists,
and a language runtime to install first.

This reads a file and writes a PNG.
On an 8-slide deck it peaks at **at least 31× less memory** than headless Chromium
([the measurements](#performance)).
The deck and the probe that measured it both live in this repo, so unlike most
benchmarks you can re-run it yourself: `python3 tools/bench.py`.

Decks, social cards, report one-pagers, CI visual diffs — if the input is HTML plus CSS and the output is an image, that is the entire job.

## Install

Linux x86_64, glibc ≥ 2.35 — Ubuntu 22.04+, Debian 12+, or a current Fedora.
Anything else wants the container below.

```bash
V=v0.3.0-beta.10
N=html-render_0.3.0-beta.10_linux_amd64
BASE="https://github.com/leshchenko1979/html-render/releases/download/$V"
curl -fsSL -O "$BASE/$N.tar.gz" -O "$BASE/checksums.txt"
sha256sum -c checksums.txt && tar xzf "$N.tar.gz"
install -m755 html-render /usr/local/bin/ && html-render --version
```

Every release asset from `v0.3.0-beta.9` onward carries a **build provenance
attestation**, signed by the release workflow's own OIDC identity — there is no
key to fetch or trust, and GitHub is the issuer. The checksum above proves the
download arrived intact; the attestation proves which repository, commit and
workflow produced it:

```bash
gh attestation verify "$N.tar.gz" --repo leshchenko1979/html-render
gh attestation verify checksums.txt --repo leshchenko1979/html-render
```

That needs the `gh` CLI, and it is the check worth running if the tarball goes
into a build pipeline. The release job runs both itself against the **downloaded**
file, so a release published without one fails its own verification.

That is a ~25 MB binary, dynamically linked against 13 shared libraries — glibc,
OpenSSL 3, fontconfig, freetype, expat, libpng, bzip2, brotli, zlib. On a slim
Debian or Ubuntu image the rest is one command:

```bash
apt-get install -y libssl3 libfontconfig1 libfreetype6 libpng16-16 libbz2-1.0 libbrotli1 ca-certificates
```

The container image carries the libraries and a font:

```bash
docker run --rm --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges \
    -v "$PWD:/work" ghcr.io/leshchenko1979/html-render:latest \
    /work/card.html /work/card.png 1280 720 2
```

`--user` is required, not decorative: the image runs as uid 10001 and exits `101` without it.
Tags are `latest` and `v0.3.0-beta.10`; pin a digest for reproducibility.
Building from source: [CONTRIBUTING.md](CONTRIBUTING.md#building).

## Usage

Both modes take the viewport in CSS pixels; the scale factor multiplies output resolution and is optional for a single image.

```bash
html-render page.html out.png 1280 720 2                            # one PNG
html-render --clip deck.html slides/ 1280 720 2 'section.slide'    # one per match
html-render --clip deck.html slides/ 1280 720 2 'section.slide' --per-element  # lowest memory
# → slides/clip_00.png, clip_01.png, … in document order
```

`--clip` cuts every match out of the document, painted in row bands, so peak memory is one
band rather than the whole page;
`--per-element` paints an element-sized canvas per match instead and holds even less.
`--full` lets a single image run to the document's height rather than the viewport,
painted in row bands and streamed to the encoder, so a tall page is not a memory limit
and has no height ceiling (a `position:fixed` element forces one canvas instead, and says so).
Whether the input is a document or a fragment is auto-detected from the first tag and can be overridden; passing both overrides at once is an error.
Output is deterministic — same input, same bytes — so a changed pixel in CI means a changed document.
A 1280×640 card at scale 2, the shape of `assets/og.png` at 2560×1280, is the same call with different numbers.

Exit codes are a contract, tested in CI against a real binary:

| code | meaning |
|---|---|
| `0` | PNG written, or `--help` / `--version` printed |
| `2` | bad arguments: unknown flag, `--clip` not first, `--per-element` outside `--clip`, `--full` with `--clip`, `--document` with `--fragment` |
| `3` | the selector matched nothing |
| `4` | output written but known to be incomplete: a sub-resource failed to fetch (`404`, DNS, connection refused) or was refused as too large, a fetch hit `BLITZ_DEADLINE_SECS`, or a frame was cut to the canvas width |
| `5` | a single canvas would exceed the renderer limit (65408 wide, 65532 tall): nothing written. `--clip` and `--full` band the page, so this now means one *band* or one element is too large, or banding was disabled with `BLITZ_BAND_MIB=0` |
| `101` | panic: unparsable number, invalid selector, unreadable input |

`html-render --help` is the only maintained list of flags.
Six optional environment variables exist for diagnostics —
`BLITZ_BAND_MIB` (`4`), `BLITZ_DEADLINE_SECS` (`120`), `BLITZ_DRAIN` (`quiet`),
`BLITZ_MAX_IMAGE_MP` (`128`), `BLITZ_RUNTIME` (current-thread), `BLITZ_TRACE` (unset) —
and stay out of that text because it is tested against the argument parser.

## Performance

Every figure below is read from `bench/results.json`, a receipt produced by
`tools/bench.py` on a GitHub runner and committed unedited.
The deck is generated from a seed, so the same command renders the same
document on your machine:

```bash
git clone https://github.com/leshchenko1979/html-render && cd html-render
python3 tools/bench.py --bin <path-to-render> --chrome <path-to-chrome> --out /tmp/mine.json
```

If `deck.html` hashes to `b77aac2de5931588dfa01eb5af418bc5`, you are measuring
the deck in the receipt. Anything else means the generator moved and the
figures below describe a document that no longer exists.

Eight slides, 1280×5760 CSS px at scale 2, three repeats per engine, peak RSS as
the **sum of every process the engine spawned**:

| measurement | Chromium | html-render | ratio |
|---|---|---|---|
| peak RSS, `--full` | 2350.8 MiB | 37.6 MiB | **62.52×** |
| peak RSS, `--clip` | 2350.8 MiB | 75.5 MiB | **31.14×** |
| peak RSS, `--per-element` | 2350.8 MiB | 47.4 MiB | **49.59×** |

**Memory is the claim.** At least 31× less in the least favourable mode, and
49× in the advertised one. It is steady enough to carry a claim: across five
runs of this probe on one GitHub runner image (43744837000, 43746767310,
43752965645, 43754284965, 43762042304) html-render's `--full` peak held between
37.6 MiB and 37.9 MiB, 0.8 %, while Chromium's moved between 2336 MiB and
2453 MiB.
Earlier figures of 9.19× compared html-render's whole footprint against
Chromium's *fattest single process*; the probe sums the entire process tree on
both sides, which is what a container limit actually counts.

**Speed is measured and gated, but it is not a claim.** The receipt carries wall
clock for both engines and the drift check fails if it moves past 35 %, so a
regression still gets caught. It is not advertised as a ratio, because the
browser's own wall clock moved 21.7 % between those same four runs while ours
moved 15.2 %: the ratio mostly reports how fast the browser happened to be
that day, and a number that mostly measures the other program is not a
property of this one. Run the probe yourself — it prints both sides and lets
you judge.

Host, method and the run behind these figures: [GATES.md](GATES.md#cost).

## Fidelity against Chromium

Diffed pixel for pixel against Chromium at the same viewport and scale, the
probe's deck comes out **4.7083 % of pixels differing by more than 16 levels**
over all 29,491,200 pixels — antialiasing and glyph rasterisation, not layout:
mean channel difference 7.115, and both engines ink 19.1–19.4 % of the canvas.
That is the whole deck at full resolution, not a sample, and
`tools/bench_compare.py` will recompute it from the two PNG sets you just made.

**The reproducible fidelity number is the corpus**, not the deck. Geometry is
measured over 36 fixtures, every run, against Chromium at the same viewport:
**42 of the 54 text-free rows land exactly** where Chromium puts them, 11
diverge and 1 asserts a shared absence. The 11 are pinned per box in
`tools/parity_baseline.json`, so a further regression fails the build while the
current defects stay visible.
The divergence is concentrated, not spread: **7 rows are one table** (#9) and
**4 are one `<br>` run** (#15). Nothing else in the corpus diverges by more than
1px.

## Limits

- **Linux x86_64 only.** glibc ≥ 2.35; there is no macOS or Windows build.
- **No JavaScript.** A page that needs script renders as the static HTML it shipped with.
- **No PDF, no print layout.** PNG only: no `@page`, no page boxes, no page counters.
- **Blitz's CSS, not Chromium's.** An unsupported property is ignored, not rejected, so a gap shows up as a declaration that did nothing.
- **Dynamically linked.** 13 shared libraries, so the host needs them or you use the container.
- **Beta.** No API promise: flags and output names may move between minor versions.
- **Untrusted input is not sandboxed.** Sub-resources are fetched at render time with no allowlist
  and `file://` is readable ([SECURITY.md](SECURITY.md#what-rendering-does-to-the-machine)).
- **Peak memory is per process.** Counted across the whole process tree the advantage narrows to 5.7–6.1×.
- **Three known defects produce wrong output with exit 0**, measured on `0.3.0-beta.8`:
  table layout
  off by up to 18px (#9), `<br>` runs shifting content 16px per break (#15), and a `200`
  whose body is not an image — that one fails to decode inside Blitz, after the bytes
  were delivered, so nothing upstream of the decoder can see it. [Read them](https://github.com/leshchenko1979/html-render/issues?q=is%3Aissue+is%3Aopen)
  before putting a render in a pipeline. #9 and #15 are pinned in the corpus gate;
  **the non-image `200` has no test at all**.
- **A document taller than the canvas is cut, and now says so** (#10). The PNG is still
  `<width> x <height>` at exit `0`, because every golden depends on that default, but the
  `TIMING` line carries `content_h=<document px> canvas_h=<canvas px>` and stderr names how
  many rows were lost. Pass `--full` to paint the whole document.
- **A font the host does not have is reported** (#12). fontconfig always resolves
  something, so a document naming a family that is not installed is rendered in
  whatever the substitution picked — at exit `0`, with a layout that still looks
  like a layout. Every such family is now named on stderr, once. Generic keywords
  (`serif`, `sans-serif`, `monospace`) are not reported, because substituting one
  is the request being honoured; `@font-face` families are not reported, because
  the document registers those itself. The exit code stays `0`: the output is
  still a render of the document, and the caller decides.
- **A sub-resource that fails to fetch, or is refused for size, exits `4`** (#11).
  A `404`, a DNS failure, a connection refused, or an image whose decoded size exceeds
  `BLITZ_MAX_IMAGE_MP` writes the partial PNG and names the resource on stderr. Before
  this, all four produced a valid PNG of an empty box with exit `0`.
- **A frame wider than the viewport is no longer cut** (#17). The default `--clip`
  strategy paints the document into one shared canvas, and that canvas is now as wide
  as the widest match: a 1600 px element in an 800 px viewport is written 1600 px wide
  rather than 800. Peak memory stays one band, at that width, so the cost is
  proportional to the widest element and not to the page. Past the renderer's 65,408 px
  limit nothing can be painted, so that residual case is still a cut and still exits `4`.

## Where the detail lives

| file | holds |
|---|---|
| [GATES.md](GATES.md) | determinism, device scale, element clip, cost, corpus, fidelity, CSS audit |
| [CONTRIBUTING.md](CONTRIBUTING.md) | build requirements, the CI workflows, the rules a change must pass |
| [SECURITY.md](SECURITY.md) | what rendering does to the machine, measured egress, reporting |
| [CHANGELOG.md](CHANGELOG.md) | what moved between releases |

## Contributing

Bug reports with a minimal HTML repro are the most useful thing you can send.
Pull requests are read and reviewed, at the pace of one person on a beta.
Read [CONTRIBUTING.md](CONTRIBUTING.md#rules-that-bite) before opening one.

## Licence

Apache-2.0, with an explicit patent grant. Full text in [LICENSE](LICENSE).
