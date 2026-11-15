# bench/results.json — the receipt behind the numbers on the front page

`results.json` is the **raw output** of one CI run of `tools/bench.py`. It is not
edited by hand: every figure the README advertises is read from it by
`tools/check_claims.py`, and the guard fails if the prose and this file disagree.

## Provenance

| | |
|---|---|
| Workflow run | [43744837000](https://github.com/leshchenko1979/html-render/actions/runs/43744837000) (`parity`) |
| Artefact | `bench-results`, step *Benchmark against Chromium* |
| Measured | 2026-11-15T18:08:34Z |
| Host | `runnervmy75tu`, Ubuntu 24.04, glibc 2.35, 4 vCPU, 15,988 MiB |
| Probe | `html-render 0.3.0-beta.10`, `sha256 de69ad53…` |
| Browser | Google Chrome 157.0.8103.78 |

## Reproduce it

```sh
git clone https://github.com/leshchenko1979/html-render
cd html-render
cargo build --release          # or download a release tarball
python3 -m pip install -r tools/requirements.txt
python3 tools/bench.py --bin ./target/release/render --repeats 3
```

Chromium must be on `PATH` (or pass `--chrome`) for the comparison half. On a
host where it cannot launch, the probe records `chromium: unavailable` with the
measured reason and still benches the renderer — it never silently skips, and it
never reports success without saying which engines it actually measured.

## What the deck is

`tools/bench_deck.py` generates it from seed `20261115`: 8 slides of 1280×720
CSS px, rendered at scale 2, so the document is 1280×5760 CSS px → a
**2560×11520** PNG. Same seed writes the same bytes anywhere, which is why
`deck.html_md5` here equals the md5 on a developer's machine — the fixture is
arithmetic, not a checked-in blob.

That property is what makes a cross-host comparison meaningful: every reader
measures the identical document.

## How the two engines are measured

Identically, deliberately:

- one process each, one tall PNG out;
- wall clock = median of 3 runs, spawn to exit;
- peak memory = the **whole process tree**, sampled every 25 ms
  (`tools/measure_rss.py`), because a browser is a browser plus renderers plus
  helpers, and a per-process maximum understates what the host must hold;
- Chromium is driven with `tools/run_parity.py`'s own flags at the same device
  scale, so both images are the same pixel grid.

## Read the numbers honestly

- The **speed** ratio is modest and the deck is small. It is one document, not a
  workload class; a heavier page changes both sides.
- The **memory** ratio is the striking one, and it is large because the
  instrument changed: earlier figures were largest-single-process maxima, which
  flatter a multi-process browser. Tree RSS is what a host has to hold.
- **Fidelity** is a share of pixels off by more than 16/255 against Chromium's
  own screenshot of the same document. It is not a statement about any other
  page, and the divergent pixels are concentrated in a few known layout defects
  (`tools/parity_baseline.json`).
