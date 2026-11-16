# Usage reference

Every flag, every environment variable, the exit-code contract and the two streams that carry
machine-readable output. The README shows the three shapes; this page is the rest.

`pageplate --help` prints the flag list. Its EXIT CODES block predates the wide-`--clip` change, so the
table below is the authoritative one ([#33](https://github.com/leshchenko1979/pageplate/issues/33)).

## Synopsis

```
pageplate <html> <out.png> <width> <height> [scale] [--document] [--fragment] [--full]
pageplate --clip <html> <outdir> <width> <height> <scale> <selector> [--document] [--fragment] [--per-element]
```

## Arguments

| argument | meaning |
|---|---|
| `<html>` | path to the document, not a URL. A missing file panics, exit `101` |
| `<out.png>` | output path for the single-image modes |
| `<outdir>` | output directory for `--clip`, created if absent |
| `<width> <height>` | viewport in CSS pixels |
| `<scale>` | device pixels per CSS pixel; `1` is 1:1. Optional in the single-image modes, required for `--clip` |
| `<selector>` | CSS selector; one PNG per match, numbered from `01` in document order as `%02d`, so past 99 frames a numeric sort is required, not `ls` order ([#40](https://github.com/leshchenko1979/pageplate/issues/40)) |

## Modes

| mode | output | peak memory |
|---|---|---|
| default | one PNG of the viewport | one canvas of the viewport |
| `--full` | one PNG as tall as the whole document, painted in row bands and streamed into the encoder, so page height has no ceiling | one band |
| `--clip` | one PNG per selector match, `clip_01.png`, `clip_02.png`, … An output directory is **single-writer**: two runs aimed at the same one interleave their frames and both exit `0` ([#37](https://github.com/leshchenko1979/pageplate/issues/37)) | one band, as wide as the widest match |
| `--clip --per-element` | one PNG per match, each on its own element-sized canvas with the viewport scrolled to it | one element-sized canvas; flat in page height |

A document holding a `position:fixed` element is painted as **one canvas** under `--full`, because a banded
paint would repeat that element down the image. That is the one case where `--full`'s memory follows page
height instead of the band budget.

Either `--clip` strategy refuses a canvas wider than 65,408 px or taller than 65,532 px, with exit `5` and
nothing written.

## Flags

| flag | effect |
|---|---|
| `--document` | treat the file as a full document; the default is auto-detected |
| `--fragment` | treat the file as a fragment and wrap it |
| `--full` | one PNG as tall as the document, painted in bands |
| `--clip` | one PNG per selector match. Must come first |
| `--per-element` | with `--clip`: paint each match on its own canvas |
| `--scroll` | accepted alias for `--per-element`, kept so older invocations keep working; `--help` does not list it |
| `-h`, `--help` | print the flag list, exit `0` |
| `-V`, `--version` | print the version, exit `0` |

## Environment variables

| variable | default | effect |
|---|---|---|
| `BLITZ_BAND_MIB` | `4` | rows per band, derived from a byte budget so peak memory stays flat at any scale. `0` disables banding and paints one canvas. The budget is a target, not a cap: a frame taller than one band gets a band of its own, so the peak is the larger of one band and the tallest frame. On a document that fits a single band, changing it changes nothing |
| `BLITZ_DEADLINE_SECS` | `120` | how long to wait for sub-resources. The frame is still written, complete, and the run exits `4` |
| `BLITZ_MAX_IMAGE_MP` | `128` | refuse to decode an image above this many megapixels (value × 1,000,000 px). The refusal is a failed sub-resource: exit `4`, resource named on stderr. One 128 MP decode costs about 488 MiB of RGBA |
| `BLITZ_DRAIN` | `quiet` | shutdown drain mode. `naive`, `naive2` and `yield` reproduce upstream's loops for diagnosis; `naive` can drop an image that was fetched but not yet ingested when the paint loop exits. `tools/drain_probe.py` reproduces the measured table in [GATES.md](../GATES.md) |
| `BLITZ_RUNTIME` | `current` | tokio flavour. `multi` reproduces upstream's multi-thread runtime; the shipped CLI uses the current-thread runtime |
| `BLITZ_TRACE` | unset | set to anything to enable trace logging. Output is capped, because an unbounded drain loop once wrote 1.12 GB |

`--help` names `BLITZ_BAND_MIB` twice, beside the two banded modes; the other five appear only here.

## Exit codes

Wrong arguments are exit `2`: `--per-element` outside `--clip`, `--full` together with `--clip`, and
`--document` together with `--fragment`. So is any **long** flag this binary does not implement — a typo used to pass
through the positional filter and render a fragment of a full document, so `--bogus` is refused rather than ignored. A
bare `--` is not an end-of-options marker; it is refused the same way.

**Single-dash forms are not refused.** `-x`, `-q`, `-v` and any other `-abc` are accepted and ignored, and the render
proceeds at exit `0`; only `-h` and `-V` mean anything
([#39](https://github.com/leshchenko1979/pageplate/issues/39)). A typo'd short flag is therefore silent, so use the long
forms.

| code | meaning |
|---|---|
| `0` | a PNG was written, or `--help` / `--version` was printed |
| `2` | bad arguments: unknown flag, too few arguments, `--clip` not first, `--per-element` outside `--clip`, `--full` with `--clip`, `--document` with `--fragment` |
| `3` | the selector matched nothing, or every match had no layout box (`br`, `wbr`, an empty inline `span`). A match with a **zero-size** box is a different case: `script`, `style`, `template` and `display:none` return `0` with a 1×1 PNG ([#38](https://github.com/leshchenko1979/pageplate/issues/38)) |
| `4` | output written but known to be incomplete: a sub-resource failed to fetch (`404`, DNS, connection refused), was refused as too large, a fetch hit `BLITZ_DEADLINE_SECS`, or a frame past 65,408 px was cut |
| `5` | one canvas would exceed the renderer limit (65,408 wide, 65,532 tall) and nothing was written; with banding on, that means one band or one element is too large, banding was disabled with `BLITZ_BAND_MIB=0`, or a `position:fixed` element forces one canvas past the limit |
| `101` | panic: unparsable number, invalid selector, unreadable input, unwritable output path |

CI tests these against a real binary, not against this table (`tools/test_exit_codes.py`).

A killed process returns none of them: the shell reports `128+signal` (`137` on `SIGKILL`, `143` on
`SIGTERM`), and the output path may hold a truncated PNG
([#26](https://github.com/leshchenko1979/pageplate/issues/26)).

**A non-zero exit does not mean nothing was written.** What the output path holds afterwards:

| code | output path after the run |
|---|---|
| `2` | untouched — arguments are validated before anything is opened |
| `3` | with `--clip`: a selector that matched nothing leaves the previous frames **untouched**; the all-matches-boxless path **sweeps them first**, so the directory is empty |
| `4` | a complete PNG (or the `--clip` set), plus a named reason on stderr |
| `5` | with `--clip`: **empty** — the sweep runs before the paint, so a refusal leaves nothing |
| `101` | possibly a **truncated PNG at the final path**: the file is opened before the panic, so a good file can be replaced by a stub |
| `128+signal` | possibly a truncated PNG, as above |

A caller that publishes on any of these has published a wrong image. Write to a fresh directory per run and decode
before publishing ([#26](https://github.com/leshchenko1979/pageplate/issues/26),
[#35](https://github.com/leshchenko1979/pageplate/issues/35)).

## Streams

Two surfaces are machine-readable, and they sit on different streams.

`TIMING` goes to **stderr**, one line per render:

```
TIMING parse_ms=12 layout_ms=48 paint_ms=310 total_ms=371 scale=2 mode=document
```

`content_h=<document px> canvas_h=<canvas px>` is appended when the document was taller than the canvas,
so a log reader sees the truncation without parsing the warning text.

Under `--clip`, one line per frame goes to **stdout**, under `--per-element` ending in `(per-element)`:

```
clip_01 css x=0.00 y=21.44 w=400.00 h=38.00  physical 400x38 at 0,21
```

`css` coordinates are document coordinates at scroll 0, so they are the same whether the run was banded or
not. Stdout stays empty in every other mode, and the output path is never echoed. The order of these lines relative to
each other is not part of the contract: match them by prefix, never by position.

There is no `--json` and no manifest
([#28](https://github.com/leshchenko1979/pageplate/issues/28)).

## Determinism

On one host, hold the document, the font stack, the renderer version, the band budget and the host fixed,
and the bytes repeat. Vary any of them and the digest moves, so a changed pixel in CI is one of them — with
one exception: the band budget changes nothing on a document that fits a single band, where every setting
renders the same bytes.

## Behaviour worth knowing

- **A missing font is substituted, and named.** fontconfig resolves a substitute rather than failing, so a
  family the host lacks renders in whatever was picked, at exit `0`. Each such family is named once on
  stderr. Generic keywords and `@font-face` families go unreported: there the substitution is the request
  being honoured.
- **A failed sub-resource exits `4`.** A `404`, a DNS failure, a refused connection, or an image past
  `BLITZ_MAX_IMAGE_MP` leaves a partial PNG and names the resource on stderr.
- **A document taller than the canvas is cut, and says so.** The PNG keeps the requested `<width> x
  <height>` at exit `0`, because every golden depends on that default; `TIMING` carries `content_h` and
  `canvas_h`, and stderr names how many rows were lost. `--full` paints the whole document instead.
- **A frame wider than the viewport is written at its own width.** The default `--clip` band is as wide as
  the widest match, so a 1600 px element in an 800 px viewport comes out 1600 px wide, at a peak of one
  band. Past 65,408 px no paint happens, so that residual case is a cut, at exit `4`.
- **Sub-resources are fetched with no allowlist**, so an untrusted document reaches the network. There is no
  offline mode ([#29](https://github.com/leshchenko1979/pageplate/issues/29));
  [SECURITY.md](../SECURITY.md) measures what rendering does to the machine.
- **Verify the output before using it.** On a non-zero exit, decode the PNG and compare before publishing:
  a killed render can leave a truncated file ([#26](https://github.com/leshchenko1979/pageplate/issues/26)),
  and a `--clip` run that fails after its sweep has already removed the previous run's frames
  ([#35](https://github.com/leshchenko1979/pageplate/issues/35)).
