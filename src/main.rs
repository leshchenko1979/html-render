//! Render HTML to PNG at a fixed viewport, with optional device-scale and
//! per-element clipping, so Blitz can be measured against Chromium the way the
//! deck pipeline actually uses it.
//!
//! Usage (the crate builds `target/release/pageplate`; the release renames it to
//! `pageplate`, which is the name both strings below use):
//!   pageplate <html> <out.png> <width> <height> [scale] [--document] [--fragment]
//!   pageplate --clip <html> <outdir> <width> <height> <scale> <selector> [--document] [--fragment] [--per-element]
//!
//! A file that declares its own document root (`<!doctype>`, `<html>`, `<head>`
//! or `<body>` as its first tag) is rendered as a complete document, verbatim.
//! Anything else is treated as a fragment and wrapped in the same reset the
//! parity harness has always used, so both engines see identical markup.
//!
//! `--document` forces the first reading and `--fragment` the second; they are
//! mutually exclusive. The auto-detection exists because wrapping a real
//! document is not merely untidy: the wrapper's `html{background:#ffffff}`
//! reset takes the canvas away from the document's own `body{background:…}`,
//! so the page renders white — a plausible PNG of the wrong colour, with
//! nothing on stderr to say so.
//!
//! Clip mode mirrors Playwright's `locator(...).screenshot()`: every match of
//! `selector` is painted into its own PNG at the element's border box, in
//! physical pixels (CSS box x scale).

use std::env;
use std::fs::File;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use anyrender::{render_to_buffer, ImageRenderer as _, PaintScene as _};
use anyrender_vello_cpu::VelloCpuImageRenderer;
use blitz_dom::{util::Color, Document as _, DocumentConfig, Point};
use blitz_html::HtmlDocument;
use blitz_net::Provider;
use blitz_paint::paint_scene;
use blitz_traits::net::{NetHandler, NetProvider, Request};
use blitz_traits::shell::{ColorScheme, Viewport};
use peniko::kurbo::Rect;
use peniko::Fill;

/// Trace output is capped: an unbounded naive drain loop once wrote 1.12 GB.
const TRACE_PASSES: u32 = 8;

fn main() {
    // The net provider spawns a tokio task for every sub-resource fetch, so the
    // document has to be driven from inside a runtime. Without this, any
    // document with an <img> aborts with `there is no reactor running` before
    // layout starts.
    // BLITZ_RUNTIME selects the tokio flavour. `current` is what the shipped
    // CLI uses (cheaper, deterministic); `multi` reproduces the regime of
    // upstream's examples/screenshot.rs, which is `#[tokio::main]`.
    let multi = std::env::var("BLITZ_RUNTIME").as_deref() == Ok("multi");
    let mut builder = if multi {
        tokio::runtime::Builder::new_multi_thread()
    } else {
        tokio::runtime::Builder::new_current_thread()
    };
    builder
        .enable_all()
        .build()
        .expect("tokio runtime")
        .block_on(real_main());
}

/// The text `--help` prints. It lives next to the parser so a flag cannot be
/// added to one and forgotten in the other: `tools/test_exit_codes.py` asserts
/// every flag named here is accepted, and that `--help` exits 0.
fn help_text() -> String {
    format!(
        "{name} {version}\n\
         HTML + CSS to PNG, with no browser.\n\
         \n\
         USAGE:\n\
         \x20 pageplate <html> <out.png> <width> <height> [scale] [--document] [--fragment] [--full]\n\
         \x20 pageplate --clip <html> <outdir> <width> <height> <scale> <selector> [--document] [--fragment] [--per-element]\n\
         \n\
         MODES:\n\
         \x20 (default)     one PNG of the viewport\n\
         \x20 --full        one PNG, canvas as tall as the whole document, painted\n\
         \x20               in row bands and streamed straight into the encoder, so\n\
         \x20               total page height has no ceiling (BLITZ_BAND_MIB,\n\
         \x20               default 4). A document holding a position:fixed element\n\
         \x20               is painted as ONE canvas instead, because a banded paint\n\
         \x20               would repeat that element down the image.\n\
         \x20 --clip        one PNG per selector match, written as clip_NNN.png, by\n\
         \x20               one of two strategies:\n\
         \x20                 (no flag)      paint the document in row bands, then cut\n\
         \x20                                each frame out of the band that holds it.\n\
         \x20                                The band is as wide as the WIDEST match, so\n\
         \x20                                a frame wider than the viewport is not cut.\n\
         \x20                                Peak memory is one band, not the page\n\
         \x20                                (BLITZ_BAND_MIB, default 4).\n\
         \x20                 --per-element  paint each frame on its OWN element-sized\n\
         \x20                                canvas, viewport scrolled to it. Peak memory\n\
         \x20                                stays flat and total page height stops\n\
         \x20                                mattering.\n\
         \x20               Either strategy refuses a canvas wider than 65408 px or taller\n\
         \x20               than 65532 px with exit 5: past that the renderer truncates the\n\
         \x20               dimension through a u16 and then panics with an index error.\n\
         \n\
         ARGS:\n\
         \x20 <width> <height>   viewport in CSS pixels (scale multiplies the output)\n\
         \x20 <scale>            device pixels per CSS pixel; 1 is 1:1\n\
         \x20 <selector>         CSS selector; one PNG per match\n\
         \n\
         FLAGS:\n\
         \x20 --document    treat the file as a full document (default: auto-detected)\n\
         \x20 --fragment    treat the file as a fragment and wrap it\n\
         \x20 -h, --help    print this text and exit 0\n\
         \x20 -V, --version print the version and exit 0\n\
         \n\
         EXIT CODES:\n\
         \x20 0    the PNG, or the clip directory, was written\n\
         \x20 2    wrong argument count, an unknown flag, --clip not first, --per-element\n\
         \x20      outside --clip, or --full with --clip\n\
         \x20 3    the selector matched nothing, or every match had no layout box or\n\
         \x20      zero area (script, style, display:none)\n\
         \x20 4    the output was written but is known to be incomplete: a sub-resource\n\
         \x20      failed to fetch or was refused as too large, an asset fetch hit the\n\
         \x20      deadline, or a frame past 65408 px wide was cut to that limit\n\
         \x20 5    the canvas would exceed the renderer limit (65408 wide, 65532 tall);\n\
         \x20      nothing was written\n\
         \x20 101  a panic: an unparsable number, an invalid selector, an unreadable input\n",
        name = env!("CARGO_PKG_NAME"),
        version = env!("CARGO_PKG_VERSION")
    )
}

/// Restore the REQUESTED url on every resource response, and make a failed
/// fetch observable at all.
///
/// `blitz_net::Provider::fetch` reports the url a response ended at, i.e. the
/// FINAL url after any HTTP redirects. `Document::load_resource` then keys its
/// image cache on that string — but `Document::pending_images`, the registry of
/// nodes waiting for a picture, is keyed by the url the element ASKED for. On a
/// redirect the two disagree, `pending_images.remove(final_url)` returns an
/// empty list, and the bytes are dropped after decoding successfully: a blank
/// box, exit code 0, nothing on stderr. Measured against a local server that
/// answers `302` with a real PNG — 795 bytes of all-white output, and both hops
/// in the server log. Rewriting the url back to the requested one before the
/// handler sees it makes both sides of that map agree, and needs no fork.
///
/// The same wrapper is where a failed fetch becomes visible. `Provider::fetch`
/// swallows the error: it reports it through `tracing` (compiled out here) and
/// never calls the handler, so a `404` leaves the node pending, the box blank
/// and the exit code 0 — a valid PNG of the wrong thing. Its public
/// `fetch_with_callback` hands back the `Result`, which is the only place the
/// failure is observable without forking the dependency.
///
/// It also owns the in-flight count. `Provider::count` derives it from the
/// refcount of its waker `Arc`, which `fetch` clones for the life of a request;
/// `fetch_with_callback` does not clone it, so forwarding that count would
/// report an empty queue while fetches were still running. The drain loop reads
/// this counter, so the counter has to be ours.
struct RequestedUrlProvider {
    inner: Arc<Provider>,
    inflight: Arc<AtomicUsize>,
    failed: Arc<Mutex<Vec<String>>>,
    max_image_px: u64,
}

impl RequestedUrlProvider {
    fn new(inner: Arc<Provider>) -> Self {
        Self {
            inner,
            inflight: Arc::new(AtomicUsize::new(0)),
            failed: Arc::new(Mutex::new(Vec::new())),
            max_image_px: max_image_px(),
        }
    }

    /// In-flight request count, read by the drain loop in `real_main`.
    fn count(&self) -> usize {
        self.inflight.load(Ordering::SeqCst)
    }

    fn is_empty(&self) -> bool {
        self.count() == 0
    }

    /// One line per sub-resource that failed or was refused, in completion
    /// order. Read after painting, where a non-empty list decides exit code 4.
    fn failures(&self) -> Vec<String> {
        self.failed.lock().map(|v| v.clone()).unwrap_or_default()
    }
}

impl NetProvider for RequestedUrlProvider {
    fn fetch(&self, _doc_id: usize, request: Request, handler: Box<dyn NetHandler>) {
        let requested = request.url.to_string();
        let inflight = Arc::clone(&self.inflight);
        let failed = Arc::clone(&self.failed);
        let max_image_px = self.max_image_px;
        // Counted before the task is spawned, so the drain loop can never see
        // an empty queue between this call and the spawned task starting.
        inflight.fetch_add(1, Ordering::SeqCst);
        self.inner.fetch_with_callback(
            request,
            Box::new(move |result| {
                match result {
                    Ok((_final_url, bytes)) => match image_pixels(&bytes) {
                        // Dropping the bytes leaves the node pending, which
                        // paints the same blank box a failed fetch does — but
                        // now the run says so and exits 4 instead of 0.
                        Some((w, h))
                            if max_image_px > 0
                                && u64::from(w) * u64::from(h) > max_image_px =>
                        {
                            if let Ok(mut v) = failed.lock() {
                                v.push(format!(
                                    "{requested}: {w}x{h} image is {:.1} MP, above the {} MP cap \
                                     (BLITZ_MAX_IMAGE_MP=0 disables it)",
                                    (u64::from(w) * u64::from(h)) as f64 / 1_000_000.0,
                                    max_image_px / 1_000_000,
                                ));
                            }
                        }
                        _ => handler.bytes(requested, bytes),
                    },
                    Err(e) => {
                        if let Ok(mut v) = failed.lock() {
                            v.push(format!("{requested}: {e}"));
                        }
                    }
                }
                inflight.fetch_sub(1, Ordering::SeqCst);
            }),
        );
    }

    fn is_noop(&self) -> bool {
        self.inner.is_noop()
    }
}

/// Decoded-pixel cap for one sub-resource image, in pixels. Read from
/// `BLITZ_MAX_IMAGE_MP` (megapixels, `0` disables the cap).
///
/// 128 MP is ~488 MiB of RGBA. Above what a document plausibly embeds — a
/// 9000x9000 PNG is 81 MP and renders in 2.4 s on the released binary — and far
/// below what a tiny file can demand: a 60-byte PNG header claiming 30000x30000
/// allocates 3.6 GB before a single pixel is decoded, and on a host without the
/// headroom that allocation aborts the process with 134, which is
/// indistinguishable from a segfault and names no file.
const MAX_IMAGE_MP_DEFAULT: u64 = 128;

fn max_image_px() -> u64 {
    let mp: u64 = std::env::var("BLITZ_MAX_IMAGE_MP")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(MAX_IMAGE_MP_DEFAULT);
    mp.saturating_mul(1_000_000)
}

/// Pixel dimensions from an image header, without decoding one pixel.
///
/// Only the four formats the renderer actually decodes are recognised. That set
/// is measured, not assumed: rendering one file of each on the released binary
/// paints PNG, JPEG, GIF and WebP, while BMP and TIFF paint nothing, so those
/// two cannot allocate a buffer however large their header claims.
///
/// Anything unrecognised returns `None` and is passed through uncapped. A cap
/// that guesses is worse than no cap: refusing a document that renders today is
/// a regression, while an uncapped format is the behaviour it already had.
fn image_pixels(bytes: &[u8]) -> Option<(u32, u32)> {
    if bytes.len() >= 24 && bytes.starts_with(b"\x89PNG\r\n\x1a\n") && bytes[12..].starts_with(b"IHDR")
    {
        let w = u32::from_be_bytes([bytes[16], bytes[17], bytes[18], bytes[19]]);
        let h = u32::from_be_bytes([bytes[20], bytes[21], bytes[22], bytes[23]]);
        return Some((w, h));
    }
    if bytes.len() >= 10 && (bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a")) {
        let w = u16::from_le_bytes([bytes[6], bytes[7]]) as u32;
        let h = u16::from_le_bytes([bytes[8], bytes[9]]) as u32;
        return Some((w, h));
    }
    if bytes.len() >= 30 && bytes.starts_with(b"RIFF") && bytes[8..].starts_with(b"WEBP") {
        // Extended format: canvas size is 24-bit, stored minus one.
        if bytes[12..].starts_with(b"VP8X") {
            let w = 1 + u32::from_le_bytes([bytes[24], bytes[25], bytes[26], 0]);
            let h = 1 + u32::from_le_bytes([bytes[27], bytes[28], bytes[29], 0]);
            return Some((w, h));
        }
        // Lossy: the frame header carries two 14-bit dimensions.
        if bytes[12..].starts_with(b"VP8 ") {
            let w = (u16::from_le_bytes([bytes[26], bytes[27]]) & 0x3FFF) as u32;
            let h = (u16::from_le_bytes([bytes[28], bytes[29]]) & 0x3FFF) as u32;
            return Some((w, h));
        }
        // Lossless: one little-endian word holds (w-1) then (h-1), 14 bits each.
        if bytes[12..].starts_with(b"VP8L") {
            let bits = u32::from_le_bytes([bytes[21], bytes[22], bytes[23], bytes[24]]);
            return Some((1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)));
        }
        return None;
    }
    jpeg_pixels(bytes)
}

/// Walk JPEG markers to the frame header, which is the only place the
/// dimensions appear.
fn jpeg_pixels(bytes: &[u8]) -> Option<(u32, u32)> {
    if bytes.len() < 4 || bytes[0] != 0xFF || bytes[1] != 0xD8 {
        return None;
    }
    let mut i = 2usize;
    while i + 9 <= bytes.len() {
        if bytes[i] != 0xFF {
            i += 1;
            continue;
        }
        let marker = bytes[i + 1];
        // Standalone markers carry no length field.
        if marker == 0xD8 || marker == 0x01 || (0xD0..=0xD7).contains(&marker) {
            i += 2;
            continue;
        }
        // Fill bytes: 0xFF may repeat before the marker itself.
        if marker == 0xFF {
            i += 1;
            continue;
        }
        let is_sof = matches!(
            marker,
            0xC0 | 0xC1 | 0xC2 | 0xC3 | 0xC5 | 0xC6 | 0xC7 | 0xC9 | 0xCA | 0xCB | 0xCD | 0xCE | 0xCF
        );
        if is_sof {
            let h = u16::from_be_bytes([bytes[i + 5], bytes[i + 6]]) as u32;
            let w = u16::from_be_bytes([bytes[i + 7], bytes[i + 8]]) as u32;
            return Some((w, h));
        }
        let seglen = u16::from_be_bytes([bytes[i + 2], bytes[i + 3]]) as usize;
        if seglen < 2 {
            return None;
        }
        i += 2 + seglen;
    }
    None
}

/// Generic CSS font keywords, which name a class of face rather than a face.
///
/// A document that asks for one of these gets whatever the platform picks, by
/// design, so the substitution is the request being honoured and not a defect.
const GENERIC_FAMILIES: &[&str] = &[
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "ui-serif",
    "ui-sans-serif",
    "ui-monospace",
    "ui-rounded",
    "math",
    "emoji",
    "fangsong",
];

/// Split a computed `font-family` value into its family names, unquoted.
fn split_families(value: &str) -> Vec<String> {
    value
        .split(',')
        .map(|part| {
            let trimmed = part.trim();
            let unquoted = trimmed
                .strip_prefix('"')
                .and_then(|s| s.strip_suffix('"'))
                .or_else(|| trimmed.strip_prefix('\'').and_then(|s| s.strip_suffix('\'')))
                .unwrap_or(trimmed);
            unquoted.trim().to_string()
        })
        .filter(|name| !name.is_empty())
        .collect()
}

/// Families the document asks for that no installed font provides.
///
/// fontconfig always resolves something, so a host without the named family
/// gets a plausible PNG of text in a face the document did not ask for, at exit
/// 0 and with nothing on stderr. Measured on `0.3.0-beta.8`: a document asking
/// for `'Absolutely Nonexistent Font XYZ 9999'` painted **8238 non-white
/// pixels** of DejaVu Sans. This reports every such family once, and the exit
/// code stays 0 because the output is still a render of the document.
///
/// The check reads the computed `font-family` of every element rather than the
/// CSS text, so it sees inline styles, stylesheets and inheritance alike. It is
/// deliberately conservative in three ways, each of which would otherwise
/// report a document that renders correctly:
///
///   * a generic keyword is skipped (see `GENERIC_FAMILIES`);
///   * a family that resolves is skipped;
///   * `@font-face` families are skipped. A document that embeds its own face
///     registers it into ITS collection when the file is fetched, and a system
///     collection cannot see that registration. Those families are collected
///     from the document's stylesheet rules and passed in.
///
/// Returns the missing families and the number of installed families the
/// instrument could see. A zero count means there is no ground truth to compare
/// against — no fonts installed, or system discovery unavailable — and the
/// caller must not read the empty list as a clean document.
fn missing_font_families(document: &HtmlDocument, declared: &[String]) -> (Vec<String>, usize) {
    let mut ctx = blitz_dom::FontContext::new();
    let installed = ctx.collection.family_ids().count();
    if installed == 0 {
        return (Vec::new(), 0);
    }
    let mut missing: Vec<String> = Vec::new();
    for (node_id, node) in document.tree().iter() {
        if !node.is_element() {
            continue;
        }
        for family in split_families(&document.resolved_style_value(node_id, "font-family")) {
            if GENERIC_FAMILIES.iter().any(|g| g.eq_ignore_ascii_case(&family)) {
                continue;
            }
            if declared.iter().any(|d| d.eq_ignore_ascii_case(&family)) {
                continue;
            }
            if ctx.collection.family_id(&family).is_some() {
                continue;
            }
            if !missing.iter().any(|m| m.eq_ignore_ascii_case(&family)) {
                missing.push(family);
            }
        }
    }
    (missing, installed)
}

/// Family names declared by `@font-face` rules anywhere in the document.
///
/// `Document::load_resource` registers a fetched face under the CSS-declared
/// name, so a family this returns is present in the document's collection even
/// though a system collection has never heard of it. Reporting those would
/// flag every document that embeds a webfont.
fn font_face_families(document: &HtmlDocument) -> Vec<String> {
    let mut families: Vec<String> = Vec::new();
    for owner in document.stylesheet_owner_nodes() {
        // Top-level rules only: `@font-face` nested inside `@media` is legal,
        // and one level covers the shape this guard exists for. A miss costs a
        // spurious warning, never a wrong render, and the exit code is 0 either
        // way.
        let count = document
            .stylesheet_rule_count(owner, &[])
            .unwrap_or_default();
        for index in 0..count {
            let Some(info) = document.stylesheet_rule_info(owner, &[index]) else {
                continue;
            };
            if info.interface != "CSSFontFaceRule" {
                continue;
            }
            let lower = info.css_text.to_ascii_lowercase();
            let Some(at) = lower.find("font-family") else {
                continue;
            };
            let rest = &info.css_text[at + "font-family".len()..];
            let Some(colon) = rest.find(':') else {
                continue;
            };
            let after = &rest[colon + 1..];
            let value = after.split([';', '}']).next().unwrap_or("").trim();
            for family in split_families(value) {
                if !families.iter().any(|f| f.eq_ignore_ascii_case(&family)) {
                    families.push(family);
                }
            }
        }
    }
    families
}

/// Remove `clip_NNN.png` frames past `keep`, left in `dir` by an earlier, longer
/// run.
///
/// Clip numbering restarts at 1 every run, so a re-render that matches FEWER
/// elements leaves the tail of the previous dump behind: the caller sees
/// `clip_001..clip_003` beside stale `clip_004..clip_009` and cannot tell a
/// short dump from a complete one. The pattern is exact (`clip_` + digits +
/// `.png`), so nothing else in the directory is touched, and the count is
/// reported rather than removed silently.
///
/// `keep` is why this takes an argument rather than clearing everything (#35).
/// The sweep used to run BEFORE the paint, so a run that then refused — every
/// match boxless, a band over the canvas limit — had already destroyed the last
/// good dump and named no file in the exit code. It now runs after the paint and
/// removes only the tail this run did not write, so a run that paints nothing
/// touches nothing, and a shorter re-render still leaves no stale frames.
fn remove_stale_clip_frames(dir: &Path, keep: usize) -> usize {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return 0;
    };
    let mut removed = 0usize;
    for entry in entries.flatten() {
        let os_name = entry.file_name();
        let Some(name) = os_name.to_str() else { continue };
        let Some(rest) = name.strip_prefix("clip_") else {
            continue;
        };
        let Some(stem) = rest.strip_suffix(".png") else {
            continue;
        };
        if stem.is_empty() || !stem.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        // Keep what this run wrote. Parsed rather than compared as text, so
        // `clip_7.png` from a hand-named file is judged by its number.
        match stem.parse::<usize>() {
            Ok(n) if n <= keep => continue,
            _ => {}
        }
        if std::fs::remove_file(entry.path()).is_ok() {
            removed += 1;
        }
    }
    removed
}

/// True when `out` resolves to the same file as `input`.
///
/// `pageplate in.html in.html` used to overwrite its own source and exit 0: the
/// document was read into memory first, so the render succeeded and the input
/// was gone (#36). A lexical compare is not enough — `./t.html` and `t.html`, a
/// symlink, and a `..` segment all name one file — so both sides are resolved.
///
/// The output usually does not exist yet, which `canonicalize` refuses; its
/// parent is resolved instead and the file name re-attached, so a symlinked
/// directory is still followed.
fn same_file(input: &Path, out: &Path) -> bool {
    fn resolve(p: &Path) -> Option<PathBuf> {
        if let Ok(c) = p.canonicalize() {
            return Some(c);
        }
        let name = p.file_name()?;
        let parent = match p.parent() {
            Some(d) if !d.as_os_str().is_empty() => d,
            _ => Path::new("."),
        };
        Some(parent.canonicalize().ok()?.join(name))
    }
    match (resolve(input), resolve(out)) {
        (Some(a), Some(b)) => a == b,
        _ => false,
    }
}

async fn real_main() {
    let args: Vec<String> = env::args().collect();

    // `--help` and `--version` are the two flags every CLI reader types before
    // reading a README, and both used to exit 2 with "unknown flag". A usage
    // request is not an error: it is the one exit that must be 0, or `docker
    // run image --help` and every `cmd --help | head` pipeline reads as broken.
    //
    // Scanned over EVERY argument, not just argv[1]: a reader who types
    // `pageplate in.html out.png 794 900 --help` is asking for the same text,
    // and honouring it only in first position made the flag look broken from
    // the one place it is easiest to type. No positional is a flag, so a scan
    // cannot steal one; `--help` wins if both are present.
    if args.iter().skip(1).any(|a| a == "--help" || a == "-h") {
        print!("{}", help_text());
        std::process::exit(0);
    }
    if args.iter().skip(1).any(|a| a == "--version" || a == "-V") {
        println!("{} {}", env!("CARGO_PKG_NAME"), env!("CARGO_PKG_VERSION"));
        std::process::exit(0);
    }

    let clip = args.get(1).map(|a| a == "--clip").unwrap_or(false);
    let as_document_flag = args.iter().any(|a| a == "--document");
    let as_fragment = args.iter().any(|a| a == "--fragment");
    // Clip mode: paint one element-sized canvas per match, with the viewport
    // scrolled to that element, instead of one full-height canvas that is
    // cropped afterwards. Same picture — 0/75/58/125 pixels of 3.69 M differ by
    // one channel level on the demo deck — for a fraction of the peak memory.
    //
    // `--scroll` named the mechanism, not the choice: every strategy here
    // scrolls. `--per-element` says what it paints, and is the exact inverse of
    // the default's ONE document-height canvas. `--scroll` stays accepted so no
    // existing caller breaks, and stays out of `--help`: a documented alias is a
    // second name to keep true.
    let use_per_element = args.iter().any(|a| a == "--per-element" || a == "--scroll");
    let full_page = args.iter().any(|a| a == "--full");

    // Positional args start after the program name, plus the `--clip` marker.
    let pos: Vec<&String> = args
        .iter()
        .skip(if clip { 2 } else { 1 })
        .filter(|a| !a.starts_with("--"))
        .collect();

    let usage = if clip {
        "usage: pageplate --clip <html> <outdir> <width> <height> <scale> <selector> [--document] [--fragment] [--per-element]"
    } else {
        "usage: pageplate <html> <out.png> <width> <height> [scale] [--document] [--fragment] [--full]"
    };

    // A flag we do not implement is an error, not noise to be filtered away.
    // `--documnet` used to pass straight through the positional filter, so a
    // typo rendered a FRAGMENT of a full document and wrote a plausible PNG of
    // the wrong thing with nothing on stderr. `--clip` additionally shifts the
    // positionals by one, so it is only a mode when it leads.
    for (i, a) in args.iter().enumerate().skip(1) {
        // A single-dash argument is an error. `-q` in a trailing position used
        // to be collected as a surplus positional and silently dropped, so a
        // typo'd short flag read as "it ran fine"; `-x` in the html slot panicked
        // on a file named `-x` instead of naming the flag (#39). `-h` and `-V`
        // never reach here — they are handled above and exit 0.
        if a.starts_with('-') && !a.starts_with("--") {
            eprintln!("unknown flag {a:?}: short flags other than -h and -V are not supported");
            eprintln!("{usage}");
            std::process::exit(2);
        }
        if !a.starts_with("--") {
            continue;
        }
        if !matches!(
            a.as_str(),
            "--clip" | "--document" | "--fragment" | "--per-element" | "--scroll" | "--full"
        ) {
            eprintln!("unknown flag {a:?}");
            eprintln!("{usage}");
            std::process::exit(2);
        }
        if a == "--clip" && i != 1 {
            eprintln!("--clip must be the first argument");
            eprintln!("{usage}");
            std::process::exit(2);
        }
        // `--per-element` (and its `--scroll` alias) is a clip-mode reading and
        // nothing else reads it. Accepting it in single-image mode and ignoring
        // it made the flag look honoured while the output was the plain
        // full-document render — the same "silently did something else" class as
        // an unknown flag, so it is an error here too.
        if (a == "--per-element" || a == "--scroll") && !clip {
            eprintln!("--per-element only applies to --clip mode");
            eprintln!("{usage}");
            std::process::exit(2);
        }
        // `--full` asks for the document's own height as the canvas. In clip
        // mode the canvas is already derived from the matched elements, so the
        // flag would name nothing; accepting it there would be the same
        // "looks honoured, changes nothing" defect as `--per-element` outside clip.
        if a == "--full" && clip {
            eprintln!("--full only applies to single-image mode");
            eprintln!("{usage}");
            std::process::exit(2);
        }
    }

    // Surplus positionals are an error, not padding. `pageplate in.html out.png
    // 1280 720 2 7` rendered at exit 0 and dropped the `7`, and a caller cannot
    // tell that from a value that was honoured (#39). The count is exact in both
    // modes: clip takes six (html, outdir, width, height, scale, selector),
    // single-image four or five (scale optional). This runs AFTER the flag loop
    // on purpose, so a stray `-q` is reported as an unknown flag rather than
    // counted as one extra positional — both are exit 2, one names the cause.
    let max_pos = if clip { 6 } else { 5 };
    if pos.len() > max_pos {
        eprintln!(
            "{} unexpected extra argument(s): {:?}",
            pos.len() - max_pos,
            &pos[max_pos..]
        );
        eprintln!("{usage}");
        std::process::exit(2);
    }

    // Both flags name a reading; together they name none. Silently preferring
    // one would make the other look like it had been honoured.
    if as_document_flag && as_fragment {
        eprintln!("--document and --fragment are mutually exclusive");
        eprintln!("{usage}");
        std::process::exit(2);
    }

    let (html_path, out_path, width, height, scale, selector) = if clip {
        if pos.len() < 6 {
            eprintln!("{usage}");
            std::process::exit(2);
        }
        (
            pos[0].as_str(),
            pos[1].as_str(),
            pos[2].parse::<u32>().expect("width"),
            pos[3].parse::<u32>().expect("height"),
            pos[4].parse::<f32>().expect("scale"),
            Some(pos[5].as_str()),
        )
    } else {
        if pos.len() < 4 {
            eprintln!("{usage}");
            std::process::exit(2);
        }
        (
            pos[0].as_str(),
            pos[1].as_str(),
            pos[2].parse::<u32>().expect("width"),
            pos[3].parse::<u32>().expect("height"),
            pos.get(4)
                .map(|s| s.parse::<f32>().expect("scale"))
                .unwrap_or(1.0),
            None,
        )
    };

    // Refuse to write the render over its own source (#36). The document is read
    // into memory below, so the render would SUCCEED and destroy the input:
    // exit 0, a plausible PNG, and the source gone. Clip mode writes into a
    // directory, so only single-image mode can collide.
    if selector.is_none() && same_file(Path::new(html_path), Path::new(out_path)) {
        eprintln!("refusing to write the output over the input: {html_path:?}");
        std::process::exit(2);
    }

    let started = Instant::now();
    let source = std::fs::read_to_string(html_path).expect("read html");
    // A file that declares its own document root is rendered as one; a fragment
    // is wrapped. The flags force either reading.
    let as_document = !as_fragment && (as_document_flag || declares_document_root(&source));
    let doc_html = if as_document {
        source
    } else {
        let wrapped = format!(
            "<html><head><style>html,body{{margin:0;padding:0;background:#ffffff;}}</style></head><body>{source}</body></html>"
        );
        // The wrapper already copied the text, so `source` is dead from here —
        // but it stayed alive to the end of `main`, holding a second copy of the
        // whole file for the entire render.
        drop(source);
        wrapped
    };
    let base_url = format!(
        "file://{}",
        Path::new(html_path)
            .canonicalize()
            .expect("canonicalize path")
            .display()
    );

    // The viewport is physical pixels: Blitz divides window_size by the scale to
    // get the CSS viewport, which is how device_scale_factor behaves in a
    // browser (1280x720 CSS at scale 2 => 2560x1440 physical).
    let phys_w = (width as f32 * scale).round() as u32;
    let phys_h = (height as f32 * scale).round() as u32;

    // Wrap the stock provider so a redirected resource still reaches the
    // node that asked for it; see `RequestedUrlProvider`.
    let net = Arc::new(RequestedUrlProvider::new(Arc::new(Provider::new(None))));
    let mut document = HtmlDocument::from_html(
        &doc_html,
        DocumentConfig {
            base_url: Some(base_url),
            net_provider: Some(Arc::clone(&net) as _),
            viewport: Some(Viewport::new(phys_w, phys_h, scale, ColorScheme::Light)),
            ..Default::default()
        },
    );
    let parsed = started.elapsed();

    // Drain any sub-resource fetches, then resolve style and layout.
    //
    // BLITZ_DRAIN selects the loop shape so the documented pattern and the one
    // that actually works can be compared on the SAME binary:
    //   naive  - the loop body from examples/screenshot.rs WITHOUT its trailing
    //            resolve: resolve, then a single is_empty() check, no yield
    //   naive2 - the same loop plus that trailing resolve, i.e. what both
    //            upstream examples actually ship (screenshot.rs:98,
    //            paint_bench.rs:111)
    //   yield  - as naive, but yields to the runtime between iterations
    //   quiet  - four consecutive empty polls, each preceded by a yield and a
    //            short sleep (the default, and what the shipped build uses)
    //
    // `naive` loses images on BOTH runtime flavours, for opposite reasons; see
    // tools/drain_probe.py for the measured table. `yield_now()` alone does not
    // fix it: the loop can exit with the asset delivered to the document's
    // channel and not yet ingested, and only resolve() drains that channel.
    let drain_mode = std::env::var("BLITZ_DRAIN").unwrap_or_else(|_| "quiet".to_string());
    let layout_started = Instant::now();
    let deadline_secs: u64 = std::env::var("BLITZ_DEADLINE_SECS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(120);
    let deadline = layout_started + std::time::Duration::from_secs(deadline_secs);
    let mut quiet = 0u32;
    let mut pass = 0u32;
    let trace = std::env::var("BLITZ_TRACE").is_ok();
    // Set when the drain loop gives up on a slow asset. The paint still runs —
    // a partial PNG beats no PNG for diagnosis — but the run exits 4, not 0.
    let mut asset_deadline_hit = false;
    loop {
        if trace && pass < TRACE_PASSES {
            eprintln!("pass {pass}: before resolve count={} empty={}", net.count(), net.is_empty());
        }
        document.resolve(0.0);
        if trace && pass < TRACE_PASSES {
            eprintln!("pass {pass}: after  resolve count={} empty={}", net.count(), net.is_empty());
        }
        pass += 1;
        match drain_mode.as_str() {
            "naive" | "naive2" => {
                if net.is_empty() {
                    break;
                }
            }
            "yield" => {
                if net.is_empty() {
                    break;
                }
                tokio::task::yield_now().await;
            }
            _ => {
                if net.is_empty() {
                    quiet += 1;
                    if quiet >= 4 {
                        break;
                    }
                } else {
                    quiet = 0;
                }
                tokio::task::yield_now().await;
                tokio::time::sleep(std::time::Duration::from_millis(2)).await;
            }
        }
        if Instant::now() > deadline {
            // This used to print a warning and exit 0 with a plausible PNG of a
            // document whose images never arrived — the one failure mode with
            // no exit code at all. The output is still written (diagnosis is
            // worth more than nothing) but the run now exits 4.
            eprintln!(
                "warning: asset fetch deadline reached after {deadline_secs}s; \
                 output will be written but may be missing images"
            );
            asset_deadline_hit = true;
            break;
        }
    }
    if trace && pass >= TRACE_PASSES {
        eprintln!("(trace capped at {TRACE_PASSES} passes; loop ran {pass})");
    }
    // One trailing resolve, for the shipped loop and for naive2.
    //
    // resolve() is the only thing that drains the document's resource channel:
    // it starts with handle_messages(), which try_recv()s the loaded asset and
    // applies it. is_empty() reports the provider's in-flight request count — now
    // our own counter, not the waker Arc refcount — which is a different queue
    // from the document's channel, so the loop can break with the image fetched
    // and delivered but not yet ingested. Both upstream examples do this
    // (screenshot.rs:98,
    // paint_bench.rs:111) — measured, not assumed.
    //
    // `naive` and `yield` deliberately stay unpatched so tools/drain_probe.py
    // keeps reproducing the broken table.
    if drain_mode != "naive" && drain_mode != "yield" {
        document.resolve(0.0);
    }
    let laid_out = layout_started.elapsed();

    // A missing font is substituted silently: fontconfig always resolves
    // something, so the render is a plausible PNG of text in a face the
    // document did not ask for. Reporting is the whole fix — the caller can
    // decide — so the exit code stays 0, and a host where no fonts are visible
    // says so rather than reading an empty list as a clean document.
    let declared_faces = font_face_families(&document);
    let (missing_families, installed_families) = missing_font_families(&document, &declared_faces);
    if installed_families == 0 {
        eprintln!(
            "warning: no installed fonts were visible to the font check; \
             font substitution cannot be detected on this host"
        );
    } else {
        for family in &missing_families {
            eprintln!(
                "warning: font-family {family:?} is not installed; \
                 the renderer substituted one"
            );
        }
    }

    let paint_started = Instant::now();
    // Frames cut to the canvas width. A cut frame is a plausible PNG of part of
    // the element with rc=0 — the class this CLI keeps closing — so the count is
    // reported and the run exits 4.
    let mut clipped_frames = 0usize;
    // A document taller than the canvas is CUT: a valid PNG of the top of the
    // page, and `--full` is the documented escape. The cut used to be silent, so
    // this records the document's own height to report it after the fact.
    let mut cut_content_h: Option<u32> = None;
    match selector {
        None => {
            // Clip mode creates its output directory (`create_dir_all` below),
            // so the single-image path must too: `out/card.png` with no `out/`
            // used to panic 101 with "create png: No such file or directory",
            // which reads as a renderer fault rather than a missing directory.
            if let Some(parent) = std::path::Path::new(out_path).parent() {
                if !parent.as_os_str().is_empty() {
                    std::fs::create_dir_all(parent).expect("create outdir");
                }
            }
            // Without `--full` the canvas is exactly the requested viewport, so a
            // document taller than `height` is CUT: a plausible PNG of the top of
            // the page. `--full` asks for the document's own scroll height
            // instead. The default stays the viewport so existing goldens do not
            // move, but the cut is no longer silent — the document's height is
            // measured either way, reported as `content_h` on the timing line,
            // and named in a warning. The exit code stays 0: whether a knowingly
            // partial render should be fatal is caller policy (#10), the same
            // question #17 asks about a cut width.
            let content_h =
                ((document.root_element().scroll_height() * scale).round() as u32).max(phys_h);
            if !full_page && content_h > phys_h {
                cut_content_h = Some(content_h);
            }
            let out_phys_h = if full_page { content_h } else { phys_h };
            if full_page {
                // Banded + streamed: the canvas is never materialised whole, so
                // the document height stops being a memory limit or a ceiling.
                write_full_banded(&mut document, out_path, phys_w, out_phys_h, scale, trace);
            } else {
                refuse_oversized_canvas(
                    phys_w,
                    out_phys_h,
                    "reduce <height>, or pass --full to paint the whole document in bands",
                );
                let buffer = paint_region(&mut document, 0, 0, phys_w, out_phys_h, scale);
                let tmp = temp_output_path(Path::new(out_path));
                write_png(
                    File::create(&tmp).expect("create png"),
                    &buffer,
                    phys_w,
                    out_phys_h,
                );
                finish_atomic(&tmp, Path::new(out_path));
            }
        }
        Some(sel) => {
            let ids = document
                .query_selector_all(sel)
                .unwrap_or_else(|e| panic!("bad selector {sel:?}: {e:?}"));
            if ids.is_empty() {
                eprintln!("no match for selector {sel:?}");
                std::process::exit(3);
            }
            std::fs::create_dir_all(out_path).expect("create outdir");
            // The stale-frame sweep deliberately does NOT run here (#35). It used
            // to, so that a crash mid-emit could not leave a mixed dump — but the
            // cost was that a run which then painted NOTHING (every match
            // boxless, a band over the canvas limit) had already deleted the last
            // good dump, and the exit code named no file. It now runs after the
            // paint, over the tail this run did not write.
            let s = scale as f64;

            // Every matched element's box, in document coordinates, at scroll 0.
            //
            // Elements with no layout box (`<br>`, `<hr>`, `<wbr>`, `<script>`,
            // `<style>`, …) have no geometry, and `*` matches them in any real
            // document. They are SKIPPED, not fatal: a clip dump is a diagnostic,
            // so one unlayoutable element must not destroy the other 300 boxes.
            // The skip is reported rather than silent, and `clip_NNN` numbering
            // stays contiguous over the emitted boxes, so a caller can tell a
            // short dump from a complete one.
            let mut skipped = 0usize;
            // A box that EXISTS but has no area is the same outcome as no box:
            // nothing to paint. `display:none`, `<script>`, `<style>` and
            // `<template>` all report a 0x0 rect rather than none, and the
            // `.max(1)` below then produced a 68-byte 1x1 PNG at exit 0 — a
            // success code for a render that painted nothing (#38).
            let mut zero_area = 0usize;
            let rects: Vec<_> = ids
                .iter()
                .filter_map(|id| match document.get_client_bounding_rect(*id) {
                    Some(rect) if rect.width > 0.0 && rect.height > 0.0 => Some(rect),
                    Some(_) => {
                        zero_area += 1;
                        None
                    }
                    None => {
                        skipped += 1;
                        None
                    }
                })
                .collect();
            if skipped > 0 {
                eprintln!(
                    "skipped {skipped} of {} matched element(s): no layout box",
                    ids.len()
                );
            }
            if zero_area > 0 {
                eprintln!(
                    "skipped {zero_area} of {} matched element(s): zero-area box",
                    ids.len()
                );
            }
            if rects.is_empty() {
                eprintln!(
                    "no matched element for selector {sel:?} has a layout box with a non-zero area"
                );
                std::process::exit(3);
            }

            if use_per_element {
                // One canvas per element, sized to the element, painted at zero
                // offset with the viewport scrolled to the element's top-left.
                //
                // `set_viewport_scroll` is a PAINT-time offset (`origin =
                // root_pos - viewport_scroll`), not a layout input, so the
                // document is laid out exactly once and `vh` units still
                // resolve against the requested viewport.
                for (i, rect) in rects.iter().enumerate() {
                    let w = (((rect.width * s).round() as u32).max(1));
                    let h = (((rect.height * s).round() as u32).max(1));
                    refuse_oversized_canvas(
                        w,
                        h,
                        "--per-element paints one element-sized canvas per match, so total page height stops mattering",
                    );
                    document.set_viewport_scroll(Point { x: rect.x, y: rect.y });
                    let buffer = paint_region(&mut document, 0, 0, w, h, scale);
                    println!(
                        "clip_{:03} css x={:.2} y={:.2} w={:.2} h={:.2}  physical {}x{} (per-element)",
                        i + 1,
                        rect.x,
                        rect.y,
                        rect.width,
                        rect.height,
                        w,
                        h
                    );
                    let path = Path::new(out_path).join(format!("clip_{:03}.png", i + 1));
                    let tmp = temp_output_path(&path);
                    write_png(File::create(&tmp).expect("create png"), &buffer, w, h);
                    finish_atomic(&tmp, &path);
                }
                document.set_viewport_scroll(Point::ZERO);
            } else {
                // Paint the document ONCE and crop each element out of it,
                // rather than painting one region per element.
                //
                // Two reasons, both measured on the real deck:
                //   * the viewport must stay at the requested size, because `vh` units
                //     resolve against it — enlarging it to the document height would
                //     relayout the page (the deck has 3 `vh` uses);
                //   * a non-zero paint ORIGIN is not reliable for replaced content:
                //     with the origin set, slides whose photos sit below the first
                //     viewport lost them. Scrolling the viewport and painting at 0,0
                //     is a DIFFERENT mechanism and is safe: measured byte-identical
                //     to the single-canvas crop on 30/30 frames whose photos sit up
                //     to 17,400 px below the first viewport. Byte-identity there is
                //     a property of THAT deck, not of the mechanism: on a
                //     glyph-dense document (40 lines of 64 px bold) the same
                //     comparison differs by up to 1 level on 426-433 px at scale 2,
                //     and by up to 1 level on a handful of pixels at scale 1.
                //
                // Peak memory is the canvas: width x height x 4, so painting a whole
                // document at scale 2 on a 20-slide deck asked for 1.1 GiB. Bands keep
                // it flat: paint a strip of rows, cut out the frames that fall inside
                // it, repeat. Each strip is scrolled into place, never painted at a
                // non-zero origin, and its top is an exact integer physical row, so
                // the pixels are the ones a single canvas would have produced rather
                // than a resample.
                //
                // The canvas is the viewport, widened to the widest frame (#17).
                //
                // An element wider than the viewport is laid out and painted past
                // it, so a canvas exactly `phys_w` wide CUT the frame: a 1600 px
                // element in an 800 px viewport came out 800x100 with half its
                // content missing. Painting is not layout — `paint_scene` takes
                // the surface size as an argument and builds its root clip from
                // it (`blitz-paint` `render.rs`, `viewport_clip_rect`), while the
                // document's own viewport is untouched — so widening the surface
                // paints the overflow and leaves `vh` units resolving against the
                // requested viewport.
                //
                // The cost is one canvas width for EVERY band, so the row budget
                // is derived from the widened width: peak memory stays one band,
                // at the widest frame's width rather than the viewport's.
                let widest = rects
                    .iter()
                    .map(|r| {
                        ((r.x * s).round() as u32)
                            .saturating_add(((r.width * s).round() as u32).max(1))
                    })
                    .max()
                    .unwrap_or(phys_w);
                // Clamped to the renderer's own limit. Past it nothing can be
                // painted at all, so that residual case is still a cut and still
                // exits 4 — the widening removes the cut where the width is
                // paintable, not the accounting.
                let canvas_w = widest.max(phys_w).min(MAX_CANVAS_W);
                let band_rows = band_height(canvas_w);

                // One scratch buffer for every frame, reused across the loop.
                // Allocating it inside the loop churned a frame-sized block per
                // slide — 14.7 MiB at 2560x1440 scale 2 — on a deck that can
                // hold 200 of them.
                let mut out: Vec<u8> = Vec::new();

                // Frame index -> band. Bands are contiguous row ranges built from the
                // frames' own physical extents, so every frame lies wholly inside the
                // band it is cut from. The extents are computed HERE, where the concrete
                // box type is already known, so `plan_bands` names no renderer type and
                // stays a pure function of integers.
                let extents: Vec<(u32, u32)> = rects
                    .iter()
                    .map(|r| {
                        let top = (r.y * s).round() as u32;
                        let bottom = top + ((r.height * s).round() as u32).max(1);
                        (top, bottom)
                    })
                    .collect();
                let (bands, band_of) = plan_bands(&extents, band_rows);
                // The plan is otherwise invisible from outside: peak RSS says
                // "something was small", never "the budget was 4 MiB". Trace is
                // opt-in, so naming it here costs a quiet run nothing and lets a
                // test assert the default instead of trusting a comment.
                if trace {
                    eprintln!(
                        "trace: clip bands={} band_rows={} frames={}",
                        bands.len(),
                        band_rows,
                        rects.len()
                    );
                }
                // Refused before anything is painted, so a limit breach writes nothing.
                for (_, band_h) in &bands {
                    refuse_oversized_canvas(
                        canvas_w,
                        *band_h,
                        "lower BLITZ_BAND_MIB, or reduce <scale>",
                    );
                }

                let mut painted: usize = usize::MAX;
                let mut canvas: Vec<u8> = Vec::new();

                for (i, rect) in rects.iter().enumerate() {
                    let b = band_of[i];
                    if b != painted {
                        let (top, band_h) = bands[b];
                        document.set_viewport_scroll(Point { x: 0.0, y: top as f64 / s });
                        canvas = paint_region(&mut document, 0, 0, canvas_w, band_h, scale);
                        painted = b;
                    }
                    let (top, band_h) = bands[b];
                    let x = ((rect.x * s).round() as u32).min(canvas_w.saturating_sub(1));
                    let y = ((rect.y * s).round() as u32).saturating_sub(top);
                    // The canvas is the widest frame's width, so a frame is cut
                    // here only when that width is past the renderer's limit and
                    // `canvas_w` had to be clamped. Before #17 the canvas was the
                    // viewport, and this was the common case: a 1600 px element in
                    // an 800 px viewport produced an 800x100 PNG at rc=0 with half
                    // the element missing and nothing on stderr.
                    let want_w = ((rect.width * s).round() as u32).max(1);
                    if x.saturating_add(want_w) > canvas_w {
                        clipped_frames += 1;
                        eprintln!(
                            "warning: clip_{:03} is {want_w} px wide but the canvas is {canvas_w} px; the frame is cut (--per-element keeps the full width)",
                            i + 1
                        );
                    }
                    let w = want_w.min(canvas_w - x);
                    let h = (((rect.height * s).round() as u32).max(1)).min(band_h.saturating_sub(y));
                    // `top + y` is the ABSOLUTE document row, so a reader sees the
                    // same coordinates whether the run was banded or not.
                    println!(
                        "clip_{:03} css x={:.2} y={:.2} w={:.2} h={:.2}  physical {}x{} at {},{}",
                        i + 1,
                        rect.x,
                        rect.y,
                        rect.width,
                        rect.height,
                        w,
                        h,
                        x,
                        top + y
                    );
                    crop_into(&mut out, &canvas, canvas_w, x, y, w, h);
                    let path = Path::new(out_path).join(format!("clip_{:03}.png", i + 1));
                    let tmp = temp_output_path(&path);
                    write_png(File::create(&tmp).expect("create png"), &out, w, h);
                    finish_atomic(&tmp, &path);
                }
                document.set_viewport_scroll(Point::ZERO);
            }
            // Swept AFTER the paint (#35), over the tail this run did not write.
            // Every frame above is on disk under its final name already (each one
            // written to a temp and renamed, #26), so nothing here can be mistaken
            // for a frame of this run, and a run that painted nothing never
            // reaches this line.
            let stale = remove_stale_clip_frames(Path::new(out_path), rects.len());
            if stale > 0 {
                eprintln!("removed {stale} stale clip frame(s) from a previous run");
            }
        }
    }
    let painted = paint_started.elapsed();

    // `content_h` appears only when the document was taller than the canvas, so a
    // log reader sees the truncation without parsing the warning text below.
    let cut_field = match cut_content_h {
        Some(h) => format!(" content_h={h} canvas_h={phys_h}"),
        None => String::new(),
    };
    eprintln!(
        "TIMING parse_ms={} layout_ms={} paint_ms={} total_ms={} scale={} mode={}{}",
        parsed.as_millis(),
        laid_out.as_millis(),
        painted.as_millis(),
        started.elapsed().as_millis(),
        scale,
        if as_document { "document" } else { "fragment" },
        cut_field
    );

    // The document is taller than the canvas: the PNG is valid, the exit code is
    // 0, and without this line nothing says the bottom of the page is missing.
    if let Some(h) = cut_content_h {
        eprintln!(
            "warning: document is {h}px tall but the canvas is {phys_h}px: \
             the bottom {}px were cut (pass --full to paint the whole document)",
            h - phys_h
        );
    }

    // A sub-resource that never arrived used to leave a blank box and exit 0:
    // a valid PNG of the wrong thing, indistinguishable from a render where the
    // image loaded. Refused-by-cap and failed-to-fetch land in the same list,
    // because to the caller they are the same fact — the picture is incomplete.
    let failed_assets = net.failures();
    if !failed_assets.is_empty() {
        for line in &failed_assets {
            eprintln!("warning: sub-resource failed: {line}");
        }
        eprintln!(
            "error: exiting 4: {} sub-resource(s) missing or refused (see the warnings above)",
            failed_assets.len()
        );
        std::process::exit(4);
    }

    // Reported after the diagnostics, so a caller still gets the timing that
    // says how far the run got before giving up.
    if clipped_frames > 0 {
        eprintln!(
            "error: exiting 4: {clipped_frames} frame(s) cut to the canvas width (see the warning above)"
        );
        std::process::exit(4);
    }

    // After the diagnostics, so a caller still gets the timing that says how
    // far the run got before the asset deadline.
    if asset_deadline_hit {
        eprintln!("error: exiting 4: assets may be missing (see the warning above)");
        std::process::exit(4);
    }
}

/// The renderer truncates a canvas dimension through a `u16` and then panics on
/// the resulting out-of-range index, leaving a partial dump on disk. Both
/// limits are measured on the released binary, not derived:
///
///   * height 65532 renders, 65533 panics inside `vello_common` util.rs:238;
///   * width 65408 renders, 65410 panics inside `vello_cpu` span.rs:88.
///
/// Refusing before painting turns "half a dump and a panic" into one documented
/// code with nothing written.
const MAX_CANVAS_W: u32 = 65408;
const MAX_CANVAS_H: u32 = 65532;

/// Byte budget for one banded canvas, in MiB. `BLITZ_BAND_MIB=0` disables
/// banding, which reproduces the single-canvas behaviour including its ceiling.
///
/// 4 rather than 64 or 16: on a deck whose band plans actually differ
/// (400 frames x 100 px, 1280 wide) the median of nine interleaved runs is
/// 1 MiB / 200 bands / 31.1 MiB / 2.01 s, 4 MiB / 50 / 37.3 / 1.77,
/// 16 MiB / 13 / 61.0 / 1.78. A smaller budget buys 6 MiB of peak and pays
/// 0.24 s for 150 extra repaints, so 4 is the knee, not 1.
/// A deck where every frame is taller than the budget cannot tell these apart
/// at all: `plan_bands` gives such a frame a band of its own, so 1, 4 and 16
/// produce one identical plan. That is why an earlier measurement ranked them
/// and was wrong.
const BAND_BUDGET_MIB_DEFAULT: usize = 4;

/// Refuse a canvas the renderer cannot paint, naming the limit and the remedy.
///
/// Exit 5 rather than 4: code 4 means "written, but known incomplete", and this
/// path writes nothing at all.
fn refuse_oversized_canvas(w: u32, h: u32, tall_hint: &str) {
    if w <= MAX_CANVAS_W && h <= MAX_CANVAS_H {
        return;
    }
    eprintln!(
        "error: canvas {w}x{h} exceeds the renderer limit ({MAX_CANVAS_W}x{MAX_CANVAS_H})"
    );
    // The remedy depends on the path, so the caller supplies it: a hint that
    // names a flag which cannot help is worse than no hint.
    if h > MAX_CANVAS_H {
        eprintln!("hint: {tall_hint}");
    }
    std::process::exit(5);
}

/// Rows per band, derived from a byte budget so peak memory stays flat at any
/// scale: at 2560 px wide a 4 MiB band is 409 rows, and the same budget at
/// scale 2 buys half as many rows instead of twice the memory. The budget is a
/// target, not a cap: a single frame taller than one band gets a band of its
/// own, so peak memory is the larger of one band and the tallest frame.
fn band_height(phys_w: u32) -> u32 {
    let mib: usize = std::env::var("BLITZ_BAND_MIB")
        .ok()
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or(BAND_BUDGET_MIB_DEFAULT);
    if mib == 0 {
        return u32::MAX;
    }
    let row_bytes = (phys_w as usize).saturating_mul(4).max(1);
    let rows = mib.saturating_mul(1024 * 1024) / row_bytes;
    (rows.max(1).min(MAX_CANVAS_H as usize)) as u32
}

/// Split the frames into contiguous row bands, each at most `band_rows` tall.
///
/// `extents[i]` is frame `i`'s `(top_phys, bottom_phys)`, computed by the caller.
/// Returns `(bands, band_of)`: `bands[b]` is `(top_phys, height_phys)` and
/// `band_of[i]` is the band holding frame `i`. A band's top is an exact integer
/// physical row and the viewport is scrolled to `top / scale` CSS px, so the
/// strip is an exact integer translation of the single-canvas render — not a
/// resample, which would move the parity baseline.
fn plan_bands(extents: &[(u32, u32)], band_rows: u32) -> (Vec<(u32, u32)>, Vec<usize>) {
    let mut order: Vec<usize> = (0..extents.len()).collect();
    order.sort_by_key(|&i| extents[i].0);

    let mut bands: Vec<(u32, u32)> = Vec::new();
    let mut band_of = vec![0usize; extents.len()];
    let mut top = 0u32;
    let mut bottom = 0u32;
    let mut open = false;

    for &i in &order {
        let (t, b) = extents[i];
        if open && b.saturating_sub(top) > band_rows {
            bands.push((top, (bottom - top).max(1)));
            open = false;
        }
        if open {
            bottom = bottom.max(b);
        } else {
            top = t;
            bottom = b;
            open = true;
        }
        band_of[i] = bands.len();
    }
    if open {
        bands.push((top, (bottom - top).max(1)));
    }
    (bands, band_of)
}

/// Copy the `w`x`h` frame at (`x`, `y`) out of a `canvas_w`-wide canvas into
/// `out`.
fn crop_into(out: &mut Vec<u8>, canvas: &[u8], canvas_w: u32, x: u32, y: u32, w: u32, h: u32) {
    // The byte offset is computed in usize: `(row * width + x) * 4` overflows u32
    // on a canvas near the dimension limits, where the pixel count alone is 4.28
    // G. `w * h * 4` has the same trap, so the capacity is sized in usize too.
    out.clear();
    out.reserve(w as usize * h as usize * 4);
    for row in 0..h {
        let start = ((y as usize + row as usize) * canvas_w as usize + x as usize) * 4;
        out.extend_from_slice(&canvas[start..start + (w as usize * 4)]);
    }
}

/// Paint a `w`x`h` physical-pixel region whose top-left is at (`x`, `y`) in
/// document coordinates.
fn paint_region(
    document: &mut HtmlDocument,
    x: u32,
    y: u32,
    w: u32,
    h: u32,
    scale: f32,
) -> Vec<u8> {
    render_to_buffer::<VelloCpuImageRenderer, _>(
        |scene| {
            scene.fill(
                Fill::NonZero,
                Default::default(),
                Color::WHITE,
                Default::default(),
                &Rect::new(0.0, 0.0, w as f64, h as f64),
            );
            paint_scene(scene, document.as_mut(), scale as f64, w, h, x, y);
        },
        w,
        h,
    )
}

/// True when any node is `position: fixed`.
///
/// This decides whether `--full` may be painted in row bands at all. A fixed
/// element is anchored to the VIEWPORT, so scrolling the viewport to a band's
/// top repaints it at that band's top: banding a `--full` render would stamp
/// the header down the image. Proven, not reasoned: a document with one
/// `position:fixed` header rendered with `BLITZ_BAND_MIB=1` carried the header
/// in all six `--clip` frames (`pixel(50,50) = (255,0,0)` in each, 39,301 red
/// pixels in the top region of every one).
///
/// The comparison is typed against `taffy::Position::Fixed`, which is what
/// blitz's own paint tree does (`paint_tree.rs:42`). Matching on a `Debug`
/// string would pass silently if the variant were renamed.
fn has_viewport_anchored(document: &HtmlDocument) -> bool {
    document
        .tree()
        .iter()
        .any(|(_, n)| n.taffy_position() == taffy::Position::Fixed)
}

/// Paint a `--full` render in row bands, streaming each band's rows straight
/// into the PNG encoder.
///
/// The single-canvas path allocates `width * document_height * 4` twice — once
/// in the renderer and once in the encoder — and refuses anything taller than
/// `MAX_CANVAS_H`. This path never holds more than one band: peak memory is the
/// band budget (4 MiB default) plus the row buffers, and the document height
/// stops mattering, so the 65,532 px ceiling is lifted for `--full`.
///
/// One renderer and one `Vec` are reused across bands (`resize` + `render_to_vec`)
/// rather than rebuilt per band, so allocation stops being per-band work.
///
/// Correctness note: each band is painted at a non-zero scroll offset, so glyph
/// coverage is evaluated at a different coordinate magnitude than in the
/// single-canvas render. The measured effect is bounded, and it is NOT a seam:
///
///   * **max channel delta 1**, never more, at scales 1, 2 and 3;
///   * **100 %** of the differing pixels sit on rows that carry ink;
///   * the count of them within two rows of a band boundary matches the count
///     boundary rows would claim by area alone — scale 3, 1 MiB: **37 observed,
///     37.7 expected** — so they are not concentrated at the seams;
///   * a glyph cut exactly by a band boundary reassembles with **0 differing
///     pixels** at all three scales. That is the direct test of the seam
///     question, and it is why overlapping the bands would buy nothing.
///
/// The residual is 8-bit coverage rounding at glyph edges, two orders below the
/// gate's tolerance of 8, and it is the tolerance `tools/test_bands.py` asserts.
/// A `position:fixed` element is different in kind rather than degree: it would
/// be repainted at every band's top, so banding refuses instead of accepting
/// that.
fn write_full_banded(
    document: &mut HtmlDocument,
    out_path: &str,
    phys_w: u32,
    out_phys_h: u32,
    scale: f32,
    trace: bool,
) {
    let band_rows = band_height(phys_w);
    let anchored = has_viewport_anchored(document);
    // A fixed element makes banding WRONG, not merely imprecise: it would be
    // repainted at every band's top. Fall back to one canvas and say so, rather
    // than emit a plausible PNG of the wrong thing.
    let banded = band_rows != u32::MAX && !anchored;
    if anchored && band_rows != u32::MAX {
        eprintln!(
            "warning: document has a position:fixed element; painting one canvas \
             (banding would repeat it down the image). Set BLITZ_BAND_MIB=0 to silence."
        );
    }

    let bands: Vec<(u32, u32)> = if banded {
        let mut v = Vec::new();
        let mut top = 0u32;
        while top < out_phys_h {
            let h = band_rows.min(out_phys_h - top);
            v.push((top, h));
            top += h;
        }
        v
    } else {
        vec![(0, out_phys_h)]
    };
    // The remedy differs by reason, and a hint naming a flag that cannot help is
    // worse than no hint: when banding is off because the budget is 0, telling
    // the reader to "set BLITZ_BAND_MIB=0" is circular; when a fixed element
    // blocked it, banding is not available at all.
    let tall_hint = if banded {
        "each band is already under the limit, so this is the WIDTH: reduce <width> or <scale>"
    } else if anchored {
        "a position:fixed element cannot be banded: reduce <height> or <scale>"
    } else {
        "banding is switched off: set BLITZ_BAND_MIB above 0 to paint the page in bands"
    };
    // Refused before anything is painted, so a limit breach writes nothing.
    for (_, h) in &bands {
        refuse_oversized_canvas(phys_w, *h, tall_hint);
    }
    if trace {
        eprintln!(
            "trace: full bands={} band_rows={} height={} anchored={}",
            bands.len(),
            band_rows,
            out_phys_h,
            anchored
        );
    }

    let s = scale as f64;
    // Streamed into a temp name and renamed at the end (#26). This is the
    // LONGEST write the renderer performs, so it is the one a supervisor kill or
    // an OOM is most likely to catch mid-stream — and the one that used to leave
    // the largest truncated file under the real name.
    let out = Path::new(out_path);
    let tmp = temp_output_path(out);
    let mut encoder = png::Encoder::new(
        File::create(&tmp).expect("create png"),
        phys_w,
        out_phys_h,
    );
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    // `into_stream_writer` lives on the post-header `Writer`, not on `Encoder`
    // (png 0.18.1 `encoder.rs:1086`, inside `impl Writer<W>` at :533).
    let writer = encoder.write_header().expect("png stream header");
    let mut sw = writer.into_stream_writer().expect("png stream writer");

    let mut renderer = VelloCpuImageRenderer::new(phys_w, bands[0].1);
    let mut buf: Vec<u8> = Vec::new();
    for (top, h) in &bands {
        if banded {
            document.set_viewport_scroll(Point {
                x: 0.0,
                y: *top as f64 / s,
            });
        }
        renderer.resize(phys_w, *h);
        renderer.render_to_vec(
            |scene| {
                scene.fill(
                    Fill::NonZero,
                    Default::default(),
                    Color::WHITE,
                    Default::default(),
                    &Rect::new(0.0, 0.0, phys_w as f64, *h as f64),
                );
                paint_scene(scene, document.as_mut(), scale as f64, phys_w, *h, 0, 0);
            },
            &mut buf,
        );
        // Exactly this band's rows: `render_to_vec` resized `buf` to `phys_w * h * 4`,
        // and the stream's total must equal `phys_w * out_phys_h * 4` or the PNG
        // is short and every decoder rejects it.
        sw.write_all(&buf).expect("png rows");
    }
    if banded {
        document.set_viewport_scroll(Point::ZERO);
    }
    sw.finish().expect("png finish");
    finish_atomic(&tmp, out);
}

fn write_png<W: std::io::Write>(writer: W, buffer: &[u8], width: u32, height: u32) {
    let mut encoder = png::Encoder::new(writer, width, height);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    let mut writer = encoder.write_header().expect("png header");
    writer.write_image_data(buffer).expect("png data");
    writer.finish().expect("png finish");
}

/// A sibling temp path for `out`, in the SAME directory so the rename is atomic
/// on any POSIX filesystem, under a name no reader can mistake for the output.
///
/// The pid is in the name so two concurrent runs aimed at one path cannot adopt
/// each other's partial, and the leading dot keeps it out of a `clip_*` glob.
fn temp_output_path(out: &Path) -> PathBuf {
    let name = out
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "out".to_string());
    out.with_file_name(format!(".{name}.partial-{}", std::process::id()))
}

/// Move a finished temp file onto `out` (#26).
///
/// The renderer used to stream the encoder straight into the FINAL path, so any
/// interruption left a truncated PNG under the real name: a file that exists,
/// has a PNG signature, and is not the document. Measured on `0.4.0`, a `SIGKILL`
/// 0.45 s into a 2.5 s render left **69,869 bytes of a 589,369-byte image** at
/// the final path with no `IEND` — 11.9 % of a real PNG. A caller then diffs a
/// partial image against a golden and reports a regression that never happened.
///
/// `sync_all` guards power loss and the rename guards truncation; both, because a
/// rename of unsynced data can still land empty after a crash.
fn finish_atomic(tmp: &Path, out: &Path) {
    if let Ok(f) = File::open(tmp) {
        let _ = f.sync_all();
    }
    std::fs::rename(tmp, out).expect("rename the finished render into place");
}

/// True when `source` declares its own document root.
///
/// Detection reads the first tag name after a UTF-8 BOM, leading whitespace and
/// any leading `<!-- … -->` comments, and matches it case-insensitively against
/// `!doctype`, `html`, `head` and `body`. Everything else — `<div>`, `<style>`,
/// `<h1>`, a bare text node — is a fragment and gets wrapped as before.
///
/// Wrapping a real document is not merely untidy: the wrapper's
/// `html{background:#ffffff}` reset wins the canvas over the document's own
/// `body{background:…}` — the common idiom — so the page comes out white with
/// identical geometry and nothing on stderr to say so. Measured on the r2
/// binary: a `body{background:#eef}` document differs from its `--document`
/// render in 70.4 % of pixels, and a file opening at `<body …>` in 89.9 %.
fn declares_document_root(source: &str) -> bool {
    let mut rest = source.strip_prefix('\u{feff}').unwrap_or(source);
    loop {
        rest = rest.trim_start();
        let Some(after) = rest.strip_prefix("<!--") else {
            break;
        };
        // An unterminated comment means the rest of the file is inside it, so
        // there is no first tag to read.
        let Some(end) = after.find("-->") else {
            return false;
        };
        rest = &after[end + 3..];
    }
    let Some(after) = rest.strip_prefix('<') else {
        return false;
    };
    let name: String = after
        .chars()
        .take_while(|c| c.is_ascii_alphanumeric() || *c == '!')
        .collect();
    matches!(
        name.to_ascii_lowercase().as_str(),
        "!doctype" | "html" | "head" | "body"
    )
}
