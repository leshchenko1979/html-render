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
| `<selector>` | CSS selector; one PNG per match, `clip_001.png`, `clip_002.png`, … in document order, three digits wide so a lexical sort is also numeric ([#40](https://github.com/leshchenko1979/pageplate/issues/40)) |

## Modes

| mode | output | peak memory |
|---|---|---|
| default | one PNG of the viewport | one canvas of the viewport |
| `--full` | one PNG as tall as the whole document, painted in row bands and streamed into the encoder, so page height has no ceiling | one band |
| `--clip` | one PNG per selector match, `clip_001.png`, `clip_002.png`, … An output directory is **single-writer**: two runs aimed at the same one interleave their frames and both exit `0` ([#37](https://github.com/leshchenko1979/pageplate/issues/37)) | one band, as wide as the widest match |
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
| `--no-network` | refuse every sub-resource that is not `file://` or `data:`; each refusal is named on stderr and the run exits `4` |
| `--allow-host=<glob>` | fetch a sub-resource only when its host matches; repeatable, `*` is any run of characters. `file://` and `data:` always pass. A bare `--allow-host` or an empty value exits `2` |
| `--json` | write one JSON object per PNG to stdout; every human diagnostic stays on stderr. Schema below |
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

**Single-dash forms are refused too.** `-x`, `-q`, `-v` and any other `-abc` exit `2`; only `-h` and `-V` mean
anything. `-q` in a trailing position used to be collected as a surplus positional and silently dropped, so a typo'd
short flag rendered the wrong thing at exit `0`
([#39](https://github.com/leshchenko1979/pageplate/issues/39)).

| code | meaning |
|---|---|
| `0` | a PNG was written, or `--help` / `--version` was printed |
| `2` | bad arguments: unknown flag (including any single-dash flag other than `-h`/`-V`), too few or too many positionals, `--clip` not first, `--per-element` outside `--clip`, `--full` with `--clip`, `--document` with `--fragment`, or an output path that resolves to the input file |
| `3` | the selector matched nothing, or every match had no layout box (`br`, `wbr`) or a zero-area box (`script`, `style`, `template`, `display:none`). Matches that are skipped are counted on stderr, so a short dump is distinguishable from a complete one |
| `4` | output written but known to be incomplete: a sub-resource failed to fetch (`404`, DNS, connection refused), was refused as too large, was refused by `--no-network` or `--allow-host`, arrived with a body that is not a decodable image, a fetch hit `BLITZ_DEADLINE_SECS`, or a frame past 65,408 px was cut |
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
| `3` | with `--clip`: the previous frames are left **untouched**, whether nothing matched or every match was unpaintable (no layout box, or a zero-area box) — the stale-frame sweep runs only after a successful paint |
| `4` | a complete PNG (or the `--clip` set), plus a named reason on stderr |
| `5` | with `--clip`: the previous frames are left **untouched** — the canvas is refused before anything is painted or swept |
| `101` | the final path holds either the **previous file** or a complete new one: the render is written to a hidden `.<name>.partial-<pid>` sibling and renamed into place, so a panic leaves the old file untouched and a `.partial-` file behind |
| `128+signal` | as above. Before this, a killed render left a **truncated PNG under the real name** — measured on `0.4.0`, a `SIGKILL` 0.45 s into a 2.5 s render left 69,869 bytes of a 589,369-byte image with no `IEND` ([#26](https://github.com/leshchenko1979/pageplate/issues/26)) |

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
clip_001 css x=0.00 y=21.44 w=400.00 h=38.00  physical 400x38 at 0,21
```

`css` coordinates are document coordinates at scroll 0, so they are the same whether the run was banded or
not. Stdout stays empty in every other mode, and the output path is never echoed. The order of these lines relative to
each other is not part of the contract: match them by prefix, never by position.

### `--json`: one object per PNG, on stdout

`--json` writes one JSON object per PNG **actually written**, and nothing else, to stdout. Every human line —
the `TIMING` line, the warnings, the per-frame geometry above — stays on stderr, so `--json` composes with a
redirect that also wants the diagnostics, and a reader of stdout never has to skip a line it cannot parse.

```json
{"out":"slides/clip_001.png","width":400,"height":38,"content_h":5760,"canvas_h":720,"rows_lost":0,
 "mode":"clip","parse_mode":"document","scale":2,"substituted_families":["Arial"],
 "failed_resources":[{"url":"http://127.0.0.1:8080/t.txt","reason":"delivered but the body is not a decodable image"}],
 "timings_ms":{"parse":18,"layout":599,"paint":72,"total":689},"exit":0}
```

| field | meaning |
|---|---|
| `out` | the PNG this object describes |
| `width`, `height` | that PNG's pixel size |
| `content_h` | the document's own height, in physical pixels, the same for every frame of the run |
| `canvas_h` | the canvas height the run was given, before any document cut |
| `rows_lost` | `content_h - canvas_h` when the canvas cut the document, else `0`. Clip frames are cropped on purpose, so it stays `0` there |
| `mode` | `single`, `clip` or `per-element` |
| `parse_mode` | `document` or `fragment`: how the input was parsed |
| `scale` | the device scale factor |
| `substituted_families` | font families the renderer replaced, or `null` when no fonts were visible at all — `[]` means none were substituted, `null` means the host cannot tell |
| `failed_resources` | `[{url, reason}]` for every sub-resource that will not be in the picture |
| `timings_ms` | `parse`, `layout`, `paint`, `total` |
| `exit` | the code the process exits with, on every object of the run |

**The schema is stable**: a field will not be renamed or removed without a major version, and a leg in
`tools/test_exit_codes.py` fails if the keys above and the keys the binary emits stop matching in either
direction. New fields may be added. A run that writes no PNG emits no object — a `2`, `3` or `5` is the
process exit code alone, since there is no artefact to describe.

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
- **Sub-resources are fetched with no allowlist unless you set one.** An untrusted document reaches the
  network by default; `--no-network` refuses everything that is not `file://` or `data:`, and
  `--allow-host=<glob>` narrows it to a set of hosts. Both name each refusal and exit `4`;
  [SECURITY.md](../SECURITY.md) measures what rendering does to the machine.
- **Verify the output before using it.** On a non-zero exit, decode the PNG and compare before publishing.
  Each file is written to a hidden sibling and renamed, so a killed render leaves the previous file
  intact rather than a truncated one ([#26](https://github.com/leshchenko1979/pageplate/issues/26)), and a
  `--clip` run that paints nothing no longer removes the previous frames
  ([#35](https://github.com/leshchenko1979/pageplate/issues/35)) — but a crash mid-dump can still leave
  this run's frames beside an older tail, and the exit code is how you know to look.
