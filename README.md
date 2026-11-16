# html-render

Turns HTML and CSS into a PNG without a browser in the loop. One Rust binary, one process.

`0.3.0-beta.10`, one maintainer. Linux x86_64, glibc ≥ 2.35.

![The 3200×1800 hero render: a slide titled "HTML + CSS → PNG, with no browser", beside bar charts of peak memory and install size against Chromium](assets/hero.png)

```bash
printf '<h1>hello</h1>' > t.html   # the argument is a path, not a URL; a missing file panics (101)
html-render t.html hello.png 1280 720 2                      # exit 0, hello.png written
html-render assets/hero.html hero.png 1600 900 2 --document  # the PNG above; needs this repo cloned
```

[![parity](https://github.com/leshchenko1979/html-render/actions/workflows/parity.yml/badge.svg)](https://github.com/leshchenko1979/html-render/actions/workflows/parity.yml)
[![release](https://img.shields.io/github/v/release/leshchenko1979/html-render)](https://github.com/leshchenko1979/html-render/releases)
[![licence](https://img.shields.io/github/license/leshchenko1979/html-render)](https://github.com/leshchenko1979/html-render/blob/main/LICENSE)

## Why it exists

Getting a page into an image inside CI usually means shipping a browser: several hundred megabytes
of image, a JavaScript engine the job never calls, a cold start ahead of the first pixel, and a
runtime to install before any of it.

This one reads a file and writes a PNG. On an 8-slide deck its peak is **at least 31× less memory**
than headless Chromium ([the measurements](#performance)), and deck and probe both ship in this
repo, so `python3 tools/bench.py` re-derives every row.

Decks, social cards, report one-pagers, CI visual diffs: HTML plus CSS in, an image out.

## Install

Linux x86_64 with glibc ≥ 2.35: Ubuntu 22.04+, Debian 12+, or a current Fedora. Anything else wants the container below.

```bash
V=v0.3.0-beta.10
N=html-render_0.3.0-beta.10_linux_amd64
BASE="https://github.com/leshchenko1979/html-render/releases/download/$V"
curl -fsSL -O "$BASE/$N.tar.gz" -O "$BASE/checksums.txt"
sha256sum -c checksums.txt && tar xzf "$N.tar.gz"
install -m755 html-render /usr/local/bin/ && html-render --version
```

Provenance is checkable without a key: each release asset is attested by the release workflow's own
OIDC identity, with GitHub as issuer. The checksum catches a damaged download; the attestation
names the repository, commit and workflow behind the build. Both need the `gh` CLI.

```bash
gh attestation verify "$N.tar.gz" --repo leshchenko1979/html-render
gh attestation verify checksums.txt --repo leshchenko1979/html-render
```

The release job attests both assets, publishes them, then downloads the published files back and runs these same two
commands on them; a release whose downloaded asset does not verify turns the job red. That check runs after publication,
so a red run there means a live release needs re-cutting, not that nothing was published.

The binary names six libraries directly — `libc`, `libssl`, `libcrypto`, `libfontconfig`, `libm`, `libgcc_s` — and
pulls freetype, expat, libpng, bzip2, brotli and zlib through fontconfig, so `ldd` lists 12 of them on bookworm and 13
on jammy. A slim Debian or Ubuntu host needs one extra package line:
`apt-get install -y libssl3 libfontconfig1 libfreetype6 libpng16-16 libbz2-1.0 libbrotli1 ca-certificates`

The image carries those libraries and a font, but no network policy: a document's sub-resources are fetched at render time, so the container does not isolate egress ([#29](https://github.com/leshchenko1979/html-render/issues/29)).

```bash
docker run --rm --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges \
    -v "$PWD:/work" ghcr.io/leshchenko1979/html-render:v0.3.0-beta.10 \
    /work/card.html /work/card.png 1280 720 2
```

`--user` is not decorative: the image's own uid is 10001, and omitting the flag exits `101`. Two tags
exist, `v0.3.0-beta.10` and `latest`. A moving tag lets two builds of one commit disagree, so pin the
version, or the digest when the image itself must be frozen. The tarball's attestation does not extend to the image,
so verify that one by digest ([#31](https://github.com/leshchenko1979/html-render/issues/31)).
Building from source: [CONTRIBUTING.md](CONTRIBUTING.md#building).

The image is built on a tag push, a `workflow_dispatch`, or a commit touching `src/`, `Cargo.toml`, `Cargo.lock`,
`Dockerfile` or `assets/`. There is no schedule, and Dependabot watches the action pins and the crates, not
`debian:bookworm-slim`, so its `libssl3` is as old as the last build. The tarball is the other way round: it links
your host's `libssl.so.3`, so your distro patches it. For TLS patch latency the tarball is the better artifact.

## Usage

The viewport is given in CSS pixels. Scale multiplies output resolution, and a single-image run may leave it out.
Sub-resources are fetched at render time with no allowlist, so an untrusted document reaches the network by design.

```bash
html-render page.html out.png 1280 720 2                            # one PNG
html-render --clip deck.html slides/ 1280 720 2 'section.slide'    # one PNG per match
html-render --clip deck.html slides/ 1280 720 2 'section.slide' --per-element   # element-sized canvases
```

`--clip` writes `slides/clip_01.png`, `clip_02.png` and so on, numbered from `01` in document order. The three
shapes differ in memory: `--full` holds least, 37.6 MiB, then `--per-element` at 47.4 MiB; `--clip` holds most,
75.5 MiB, because it keeps one canvas as wide as the widest match. `--full` paints one image down to the
document's full height, in row bands, streamed to the encoder, so page height is not a memory limit and has no
ceiling; a `position:fixed` element forces one canvas instead, and says so.

On one host the output is reproducible: hold the document, the font stack, the renderer version, the band budget
(`BLITZ_BAND_MIB`) and the host fixed and the bytes repeat. Vary any of these and the digest moves, so a changed
pixel in CI is one of them — with one exception: the band budget changes nothing on a document that fits a
single band, where every setting renders the same bytes. For a 1280×640 card at scale 2 — the shape of
`assets/og.png`, 2560×1280 — change the numbers and nothing else.

The exit code is part of the contract, and CI tests it against a real binary:

| code | meaning |
|---|---|
| `0` | a PNG was written, or `--help` / `--version` was printed |
| `2` | bad arguments: unknown flag, too few arguments, `--clip` not first, `--per-element` outside `--clip`, `--full` with `--clip`, `--document` with `--fragment` |
| `3` | the selector matched nothing, or every match had no layout box |
| `4` | output written but known to be incomplete: a sub-resource failed to fetch (`404`, DNS, connection refused), was refused as too large, a fetch hit `BLITZ_DEADLINE_SECS`, or a frame past 65,408 px was cut |
| `5` | one canvas would exceed the renderer limit (65,408 wide, 65,532 tall) and nothing was written; with banding on, that means one band or one element is too large, banding was disabled with `BLITZ_BAND_MIB=0`, or a `position:fixed` element forces one canvas that exceeds it |
| `101` | panic: unparsable number, invalid selector, unreadable input, unwritable output path |

A killed process returns none of these: the shell reports `128+signal` (137 on `SIGKILL`, 143 on `SIGTERM`), and the output may hold a truncated PNG ([#26](https://github.com/leshchenko1979/html-render/issues/26)).

`html-render --help` is the maintained list of flags, though its EXIT CODES block predates the wide-`--clip`
change, so the table above is authoritative ([#33](https://github.com/leshchenko1979/html-render/issues/33)).
Six environment variables exist. `BLITZ_MAX_IMAGE_MP` (`128`) refuses an oversized decode; `BLITZ_DEADLINE_SECS`
(`120`) cuts the wait for assets — the frame is still written, complete, at exit `4`; `BLITZ_BAND_MIB` (`4`)
sets the band size — the peak follows it, and on a banded document so can the bytes. `BLITZ_DRAIN`,
`BLITZ_RUNTIME` and `BLITZ_TRACE` steer shutdown, runtime flavour and logging; `BLITZ_DRAIN=naive` drops images
that were fetched but not yet ingested when the paint loop exits (the measured 0/8 against 8/8 table is in
[GATES.md](GATES.md), and `tools/drain_probe.py` reproduces it). `--help` names `BLITZ_BAND_MIB` twice, beside
the banded modes.

No `--json`, no manifest ([#28](https://github.com/leshchenko1979/html-render/issues/28)). Two surfaces are machine-readable, and they sit on different streams: `TIMING` (parse, layout, paint, total, scale, mode, plus `content_h=`/`canvas_h=` when a document was cut) on stderr, and one line per frame on stdout under `--clip` — under `--per-element` that line ends `(per-element)`. Stdout stays empty otherwise, and the output path is never echoed:

```
clip_01 css x=0.00 y=21.44 w=400.00 h=38.00  physical 400x38 at 0,21
```

## Performance

Nothing below is typed by hand: `bench/results.json` is a receipt that `tools/bench.py` wrote on a GitHub
runner, and the repo commits it unedited. The deck comes from a seed, so one command reproduces this document anywhere:

```bash
git clone https://github.com/leshchenko1979/html-render && cd html-render
python3 tools/bench.py --bin <path-to-render> --chrome <path-to-chrome> --out /tmp/mine.json
```

A `deck.html` digest of `b77aac2de5931588dfa01eb5af418bc5` means the same document as the receipt. Any other
digest means the generator moved, and the rows below describe a different document.

Eight slides at 1280×5760 CSS px, scale 2, three renders per engine; peak RSS sums every process the engine spawned:

| measurement | Chromium | html-render | ratio |
|---|---|---|---|
| peak RSS, `--full` | 2350.8 MiB | 37.6 MiB | **62.52×** |
| peak RSS, `--clip` | 2350.8 MiB | 75.5 MiB | **31.14×** |
| peak RSS, `--per-element` | 2350.8 MiB | 47.4 MiB | **49.59×** |

**Memory carries the claim.** At least 31× less in the least favourable mode, and 49× in the advertised one —
`--per-element`, the figure the hero quotes. The band repeats: five runs of the probe on one runner image
(43744837000, 43746767310, 43752965645, 43754284965, 43762042304) kept html-render's `--full` peak inside
37.6 MiB to 37.9 MiB, a 0.8 % spread, while Chromium ranged over 2336 MiB to 2453 MiB. Four of the five
measured the previous deck (same canvas, different document — [GATES.md](GATES.md#cost) has the table); the
published figure is the per-run median of sampled peaks, and the widest single samples reached 38.0 and
2474.1 MiB.

**Wall clock is measured, gated, and not advertised.** The receipt carries it for both engines, and the drift
check gates the wall *ratio* at a deliberately coarse 45 %: a 1.85× slowdown of this renderer trips it, a 1.80×
one does not. Absolute seconds stay ungated, because on one host, one deck and one binary Chromium read
1.604 s on one run and 3.120 s on the next. A ratio that mostly reports the other program's load is not a
property of this one.

Method, host and the run itself: [GATES.md](GATES.md#cost).

## Fidelity against Chromium

At the same viewport and scale, pixel for pixel against Chromium, the probe's deck differs on **4.7083 % of
pixels by more than 16 levels** across 29,491,200 pixels. The mean channel difference is 8.2889; ink coverage
is 17.073 % (Blitz) against 16.6941 % (Chromium). This is the entire deck at full resolution rather than a
sample, and `tools/bench_compare.py` recomputes it from the two PNG sets.

Pixel divergence is what the deck measures. On this deck it is mostly antialiasing and glyph rasterisation;
how much of it is layout is not quantified here. The deck contains a `border-collapse` table, and table layout
is one of the known divergences below.

**The reproducible number is the corpus.** Every run measures geometry over 36 fixtures at one viewport,
against Chromium: **the corpus agrees on 42 of its 54 text-free rows**; 11 diverge and 1 asserts a shared
absence. `tools/parity_baseline.json` pins all 11 box by box, so a regression reddens the build while the
current defects stay visible. The reference engine is the runner image's Chrome, not a pinned build —
157.0.8103.78 when measured — so a runner refresh moves the baseline with it ([#34](https://github.com/leshchenko1979/html-render/issues/34)).
The divergence is not spread: one table accounts for **7 rows** (#9), one
`<br>` run for **4** (#15).

## Limits

- **Linux x86_64 only.** glibc ≥ 2.35 is the floor. No macOS or Windows build exists.
- **No JavaScript.** Script does not run; the document renders as the HTML that arrived.
- **PNG only.** No PDF and no print model: `@page`, page boxes and page counters are all absent.
- **Blitz's CSS, not Chromium's.** An unknown property is dropped, not rejected, so what a reader sees is a
  declaration with no effect.
- **Linked against the host's libraries.** Six libraries are named directly and the rest arrive through fontconfig, so
  `ldd` on your own host is the only accurate list. The container carries its own copy.
- **Beta.** Flags and output names can move between minor versions; nothing here is a stable API.
- **Nothing sandboxes the input.** Sub-resources are fetched with no allowlist, and `file://` can be read
  ([SECURITY.md](SECURITY.md#what-rendering-does-to-the-machine)). No offline mode: a document controls its own outbound HTTP ([#29](https://github.com/leshchenko1979/html-render/issues/29)).
- **Peak memory follows canvas area, not page length.** The figures above count the whole process tree, but on one
  deck, one render: there is no daemon and no queue, and N parallel renders multiply the peak by N. One 128 MP
  decode costs about 488 MiB of RGBA, and `clip_oversized_frame` in
  `tools/memory_baseline.json` peaks at **809.5 MiB**, 21× the 37.6 MiB headline. Budget a container from that.
- **A tagged release is not gated on the parity suite.** `release.yml` runs a smoke render only; the corpus gate
  runs on branches ([#30](https://github.com/leshchenko1979/html-render/issues/30)). Check a tag against
  [GATES.md](GATES.md#published-artefacts) before you build on it.
- **Three defects exit `0` with wrong pixels**, all reproduced on `0.3.0-beta.10`: table layout, off by up to 18px
  and painting the table taller than Chromium does, 104 px against 86 (#9); `<br>` runs shifting content 16px per
  break (#15); and a `200` whose body is not an image, which fails to decode inside Blitz after the bytes were
  delivered, so nothing upstream of the decoder sees it (#27). The corpus gate pins #9 and #15;
  **the non-image `200` has no test at all**.
- **Taller than the canvas means a truncated PNG.** The output keeps the requested `<width> x <height>` at exit `0`,
  because every golden depends on that default, but `TIMING` carries `content_h=<document px> canvas_h=<canvas px>`
  and stderr names how many rows were lost (#10). `--full` paints the whole document instead.
- **A missing font is substituted, and named on stderr.** fontconfig resolves a substitute rather than failing,
  so a family the host lacks renders in whatever was picked, at exit `0`, with a layout that still looks like a
  layout. Each such family is named once. Generic keywords and `@font-face` families go unreported: there the
  substitution is the request being honoured. Exit stays `0`, and the caller decides.
- **A failed sub-resource exits `4`** (#11): a `404`, a DNS failure, a refused connection, or an image past
  `BLITZ_MAX_IMAGE_MP` leaves a partial PNG and the resource named on stderr.
- **A frame wider than the viewport is written at its own width.** The default `--clip` band is as wide as the
  widest match, so a 1600 px element in an 800 px viewport comes out 1600 px wide, at a peak of one band. Beyond
  the renderer's 65,408 px limit no paint happens, so that residual case is still a cut, at exit `4`.
- **A killed render leaves a truncated PNG at the output path**: no temp file, no rename
  ([#26](https://github.com/leshchenko1979/html-render/issues/26)). At exit `4` the PNG is complete and valid, so
  an "is it an image?" check passes — decode and compare, and delete on a non-zero exit. Under `--clip` the sweep
  that clears a previous run's frames runs before painting, so a run that then paints nothing — exit `3` with
  all-boxless matches, exit `5` with one canvas over the limit — leaves the directory empty: neither this run's
  frames nor the last good ones. Exit `4` does write this run's frames, but the sweep has already deleted the
  previous run's, so the same verify-by-decoding rule is the only defence. No SBOM is generated and no dependency
  advisory scan runs in CI ([#25](https://github.com/leshchenko1979/html-render/issues/25)).

## Where the detail lives

| file | holds |
|---|---|
| [GATES.md](GATES.md) | determinism, device scale, element clip, cost, corpus, fidelity, CSS audit |
| [CONTRIBUTING.md](CONTRIBUTING.md) | build requirements, the CI workflows, the rules a change must pass |
| [SECURITY.md](SECURITY.md) | what rendering does to the machine, measured egress, reporting |
| [CHANGELOG.md](CHANGELOG.md) | what moved between releases |
| [bench/README.md](bench/README.md) | the probe: what it measures, how to re-run it, what the bands mean |
| [docs/why-html-first.md](docs/why-html-first.md) | what HTML-and-CSS-first rendering is good for, with sources |

## Contributing

A minimal HTML repro in a bug report is the most useful thing to send. Pull requests get read and reviewed, at
the pace of one person on a beta; [CONTRIBUTING.md](CONTRIBUTING.md#rules-that-bite) lists the rules a change has
to pass, and [CONTRIBUTING.md#building](CONTRIBUTING.md#building) has the build and the CI workflows.

## Licence

Apache-2.0, with an explicit patent grant. Full text in [LICENSE](LICENSE). The release tarball ships no licence
text of its own ([#32](https://github.com/leshchenko1979/html-render/issues/32)), so redistribute the LICENSE with it.
