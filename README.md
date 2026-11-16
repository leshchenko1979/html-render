# pageplate

Turns HTML and CSS into a PNG without a browser in the loop. One Rust binary, one process. `0.4.0`, one maintainer.
Linux x86_64, glibc ≥ 2.35.

![The 3200×1800 hero render: a slide titled "HTML + CSS → PNG, with no browser", beside bar charts of peak memory and install size against Chromium](assets/hero.png)

```bash
printf '<h1>hello</h1>' > t.html   # the argument is a path, not a URL; a missing file panics (101)
pageplate t.html hello.png 1280 720 2                      # exit 0, hello.png written
pageplate assets/hero.html hero.png 1600 900 2 --document  # the PNG above; needs this repo cloned
```

[![parity](https://github.com/leshchenko1979/pageplate/actions/workflows/parity.yml/badge.svg)](https://github.com/leshchenko1979/pageplate/actions/workflows/parity.yml)
[![release](https://img.shields.io/github/v/release/leshchenko1979/pageplate)](https://github.com/leshchenko1979/pageplate/releases)
[![licence](https://img.shields.io/github/license/leshchenko1979/pageplate)](https://github.com/leshchenko1979/pageplate/blob/main/LICENSE)

## Why it exists

Getting a page into an image usually means using a browser: a memory footprint of 2GB+, a cold start ahead of the
first pixel, and a runtime to install before any of it. Our CLI tool reads a file and writes a PNG. On an 8-slide deck
its peak is **at least 31× less memory** than headless Chromium ([the measurements](#performance)), and deck and probe
both ship in this repo, so `python3 tools/bench.py` re-derives every row. Decks, social cards, report one-pagers, CI
visual diffs: HTML plus CSS in, an image out.

## Install

Linux x86_64, glibc ≥ 2.35: Ubuntu 22.04+, Debian 12+, or a current Fedora. Anything else wants the container below.

```bash
V=v0.4.0
N=pageplate_0.4.0_linux_amd64
BASE="https://github.com/leshchenko1979/pageplate/releases/download/$V"
curl -fsSL -O "$BASE/$N.tar.gz" -O "$BASE/checksums.txt"
sha256sum -c checksums.txt && tar xzf "$N.tar.gz"
install -m755 pageplate /usr/local/bin/ && pageplate --version
```

Each release asset is attested by the release workflow's own OIDC identity, with GitHub as issuer, so provenance is
checkable without a key. The checksum catches a damaged download; the attestation names the repository, commit and
workflow behind the build. Both need the `gh` CLI.

```bash
N=pageplate_0.4.0_linux_amd64
gh attestation verify "$N.tar.gz" --repo leshchenko1979/pageplate \
    --signer-workflow leshchenko1979/pageplate/.github/workflows/release.yml \
    --source-ref refs/tags/v0.4.0
gh attestation verify checksums.txt --repo leshchenko1979/pageplate \
    --signer-workflow leshchenko1979/pageplate/.github/workflows/release.yml \
    --source-ref refs/tags/v0.4.0
```

`--repo` alone accepts an attestation from *any* workflow in the repository; the two extra flags pin the check to this
workflow and tag. The release job attests both assets, publishes them, downloads them back and verifies them with the
same pair, so a release whose downloaded asset does not verify turns the job red; a red run after publication means a
live release needs re-cutting.

The binary names seven objects — the loader plus `libc`, `libssl`, `libcrypto`, `libfontconfig`, `libm`, `libgcc_s` —
and pulls freetype, expat, libpng, brotli and zlib through fontconfig. The closure varies by distro: 12 objects on
bookworm and 13 on jammy, the extra one `libuuid.so.1`, which jammy's fontconfig links and bookworm's does not. A slim
Debian or Ubuntu host needs one extra package line:
`apt-get install -y libssl3 libfontconfig1 libfreetype6 libpng16-16 libbrotli1 ca-certificates fonts-dejavu-core`

A host with no font package still renders, with a substituted face, but the warning says substitution cannot be
detected there: with nothing visible to compare against, it cannot name the family it wanted. When *some* fonts are
visible and the requested one is not, it names each missing family. The image carries those libraries and a font, but
no network policy: a document's sub-resources are fetched at render time, so the container does not isolate egress
([#29](https://github.com/leshchenko1979/pageplate/issues/29)).

```bash
docker run --rm --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges \
    --memory 1g \
    -v "$PWD:/work" ghcr.io/leshchenko1979/pageplate:v0.4.0 \
    /work/card.html /work/card.png 1280 720 2
```

The image's uid is 10001, so omitting `--user` leaves it unable to write your mounted directory, and it panics (
`101`). Two tags exist, `v0.4.0` and `latest`; a moving tag lets two builds of one commit disagree, so pin the
version, or the digest when the image must be frozen. The tarball's attestation does not extend to the image, so
verify that one by digest ([#31](https://github.com/leshchenko1979/pageplate/issues/31)).

The image is built on a tag push, a `workflow_dispatch`, or a commit touching `src/`, `Cargo.toml`, `Cargo.lock`,
`Dockerfile`, `assets/` or `container.yml`; it has no scheduled rebuild, so pin the digest when base-image patches
matter ([#31](https://github.com/leshchenko1979/pageplate/issues/31)). The tarball links your host's `libssl.so.3`,
so your distro patches it: better TLS patch latency. Building from source:
[CONTRIBUTING.md](CONTRIBUTING.md#building).

## Usage

```bash
pageplate page.html out.png 1280 720 2                            # your document → one PNG
pageplate --clip assets/demo-deck.html slides/ 1280 720 2 'section.slide'    # one PNG per match
pageplate --clip assets/demo-deck.html slides/ 1280 720 2 'section.slide' --per-element   # element-sized canvases
```

`page.html` is your own file; the `--clip` recipes use the four-slide deck this repo ships and run from a fresh clone.
The viewport is in CSS pixels and `scale` multiplies the output; a single-image run may omit it. A `1280 640 2` run
writes 2560×1280, the shape of `assets/og.png`. The canvas is the viewport, so a document taller than it is cut at
exit `0`. Add `--full` when the height is unknown: a 640-CSS-px viewport on a 770-px card loses the bottom 130 px and
still reports success. `--clip` writes `slides/clip_01.png`, `clip_02.png` and so on, numbered from `01` in document
order with `%02d`; past 99 frames the names need a numeric sort, not `ls` order
([#40](https://github.com/leshchenko1979/pageplate/issues/40)). An output directory is single-writer: two `--clip`
runs at the same one interleave frames and both exit `0`
([#37](https://github.com/leshchenko1979/pageplate/issues/37)).

**Full reference: [docs/USAGE.md](docs/USAGE.md)** covers every flag, all six environment variables, the exit-code
contract, the three modes and their memory shapes, and the two machine-readable streams. On stderr: `TIMING …` every
run, plus a `warning: …` when the document was cut, a font was substituted, a sub-resource failed, stale frames were
swept, or a matched element was skipped; `error: exiting N` when the run fails. On stdout, one
`clip_NN css x= y= w= h=  physical WxH at x,y` line per `--clip` frame. Sub-resources are fetched at render time with
no allowlist, so an untrusted document reaches the network
([SECURITY.md](SECURITY.md#what-rendering-does-to-the-machine)).

## Performance

Figures below are gated by `tools/check_claims.py` against `bench/results.json`; `tools/bench.py` wrote that receipt
on a GitHub runner and the repo commits it unedited. The six-run spread is the exception, since one receipt carries one
run: it comes from the run-history table in GATES.md. The deck comes from a seed, so one command reproduces it
anywhere:

```bash
git clone https://github.com/leshchenko1979/pageplate && cd pageplate
python3 tools/bench.py --bin <path-to-render> --chrome <path-to-chrome> --out /tmp/mine.json
```

A `deck.html` digest of `0593ca9f047b4110432898a9cba23263` means the same document as the receipt; any other digest
means the generator moved and the rows below describe a different document. Eight slides at 1280×5760 CSS px, scale 2,
three renders per engine; peak RSS sums every process the engine spawned:

| measurement | Chromium | pageplate | ratio |
|---|---|---|---|
| peak RSS, `--full` | 2380.2 MiB | 37.8 MiB | **62.97×** |
| peak RSS, `--clip` | 2380.2 MiB | 75.4 MiB | **31.57×** |
| peak RSS, `--per-element` | 2380.2 MiB | 47.5 MiB | **50.11×** |

At least 31× less in the least favourable mode, 50× in the advertised one: `--per-element`, the figure the hero quotes.
Six runs of the probe on one runner image (43744837000, 43746767310, 43752965645, 43754284965, 43762042304,
43886554380) kept pageplate's `--full` peak inside 37.6 MiB to 37.9 MiB, a 0.8 % spread, while Chromium ranged over
2336 MiB to 2453 MiB. Four of the six measured the previous deck (same canvas, different document;
[GATES.md](GATES.md#cost) has the table). The published figure is the per-run median of sampled peaks; the widest
single samples reached 38.1 and 2427.8 MiB, and earlier runs peaked higher on their own decks, maxima that belong to
the table beside the run that produced them. Peak memory follows canvas area, not page length, and there is no daemon
and no queue: N parallel renders multiply the peak by N. One 128 MP decode costs about 488 MiB of RGBA, and the worst
case in `tools/memory_baseline.json` is `clip_oversized_frame` at **809.5 MiB**, 21× the 37.8 MiB headline. Budget a
container from that.

The receipt carries wall clock for both engines, and the drift check
gates the wall *ratio* at a deliberately coarse 45 %: a 1.85× slowdown of this renderer trips it, a 1.80× one does
not. Absolute seconds stay ungated, because on one host, one deck and one binary Chromium read 3.186 s on one run
and 3.774 s on the next; a ratio that mostly reports the other program's load is not a property of this one.
Method and host: [GATES.md](GATES.md#cost).

## Fidelity against Chromium

At the same viewport and scale, pixel for pixel against Chromium, the probe's deck differs on **4.7102 % of
pixels by more than 16 levels** across 29,491,200 pixels. The mean channel difference is 8.291; ink coverage is
17.073 % (Blitz) against 16.6941 % (Chromium). This is the entire deck at full resolution rather than a sample, and
`tools/bench_compare.py` recomputes it from the two PNG sets. Pixel divergence is what the deck measures: mostly
antialiasing and glyph rasterisation, with how much of it is layout left unquantified. The deck contains a
`border-collapse` table, and table layout is one of the known divergences below. Geometry is the number that
reproduces: every run measures it over 36 fixtures at one viewport against Chromium, and
**the corpus agrees on 42 of its 54 text-free rows**; 11 diverge and 1 asserts a shared absence.
`tools/parity_baseline.json` pins all 11 box by box, so a regression reddens the build while the current defects
stay visible. The reference engine is the runner image's Chrome, not a pinned build, 157.0.8103.78 when measured,
so a runner refresh moves the baseline with it ([#34](https://github.com/leshchenko1979/pageplate/issues/34)).
The divergence is not spread: one table accounts for **7 rows** (#9), one `<br>` run for **4** (#15).

## Scope

Beta: flags and output names can move between minor versions; nothing here is a stable API. Linux x86_64 with
glibc ≥ 2.35. No JavaScript: the document renders as the HTML that arrived. PNG only, no PDF or print model. Blitz's
CSS is not Chromium's: an unknown property is dropped, not rejected, so a reader sees a declaration with no effect.

## Known defects

Defects and gaps are tracked as issues rather than restated here:
**[open limitations](https://github.com/leshchenko1979/pageplate/issues?q=is%3Aissue+is%3Aopen+label%3Alimitation)**.
That list is authoritative and current; this page describes what the tool does, not what is wrong with it.

## Where the detail lives

| file | holds |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | every flag, environment variable, exit code and output stream |
| [GATES.md](GATES.md) | determinism, device scale, element clip, cost, corpus, fidelity, CSS audit |
| [CONTRIBUTING.md](CONTRIBUTING.md) | build requirements, the CI workflows, the rules a change must pass |
| [SECURITY.md](SECURITY.md) | what rendering does to the machine, measured egress, reporting |
| [CHANGELOG.md](CHANGELOG.md) | what moved between releases |
| [bench/README.md](bench/README.md) | the probe: what it measures, how to re-run it, what the bands mean |
| [docs/why-html-first.md](docs/why-html-first.md) | what HTML-and-CSS-first rendering is good for, with sources |

## Contributing

A minimal HTML repro in a bug report is the most useful thing to send. Pull requests get read and reviewed at the pace
of one person on a beta; [CONTRIBUTING.md](CONTRIBUTING.md#rules-that-bite) lists the rules a change must pass, and
[CONTRIBUTING.md#building](CONTRIBUTING.md#building) has the build and the CI workflows.

## Licence

Apache-2.0, with an explicit patent grant. Full text in [LICENSE](LICENSE). If you redistribute a binary, ship that
file with it.
