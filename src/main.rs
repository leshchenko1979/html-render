//! Render HTML to PNG at a fixed viewport, with optional device-scale and
//! per-element clipping, so Blitz can be measured against Chromium the way the
//! deck pipeline actually uses it.
//!
//! Usage (the crate builds `target/release/render`; the release renames it to
//! `html-render`, which is the name both strings below use):
//!   html-render <html> <out.png> <width> <height> [scale] [--document] [--fragment]
//!   html-render --clip <html> <outdir> <width> <height> <scale> <selector> [--document] [--fragment] [--scroll]
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
use std::path::Path;
use std::sync::Arc;
use std::time::Instant;

use anyrender::{render_to_buffer, PaintScene as _};
use anyrender_vello_cpu::VelloCpuImageRenderer;
use blitz_dom::{util::Color, Document as _, DocumentConfig, Point};
use blitz_html::HtmlDocument;
use blitz_net::Provider;
use blitz_paint::paint_scene;
use blitz_traits::net::{Bytes, NetHandler, NetProvider, Request};
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
        "html-render {version}\n\
         HTML + CSS to PNG, with no browser.\n\
         \n\
         USAGE:\n\
         \x20 html-render <html> <out.png> <width> <height> [scale] [--document] [--fragment] [--full]\n\
         \x20 html-render --clip <html> <outdir> <width> <height> <scale> <selector> [--document] [--fragment] [--scroll]\n\
         \n\
         MODES:\n\
         \x20 (default)     one PNG of the viewport\n\
         \x20 --full        one PNG, canvas as tall as the whole document\n\
         \x20 --clip        one PNG per selector match, written as clip_NN.png, by\n\
         \x20               one of two strategies:\n\
         \x20                 (no flag)   paint ONE document-height canvas, then cut each\n\
         \x20                             frame out of it. Peak memory grows with page\n\
         \x20                             height; a canvas 65535 px tall or taller fails.\n\
         \x20                 --scroll    paint each frame on its OWN element-sized canvas,\n\
         \x20                             viewport scrolled to it. Peak memory stays flat\n\
         \x20                             and total page height stops mattering, though\n\
         \x20                             one element past 65535 px still hits the same\n\
         \x20                             canvas limit.\n\
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
         \x20 2    wrong argument count, an unknown flag, --clip not first, --scroll outside\n\
         \x20      --clip, or --full with --clip\n\
         \x20 3    the selector matched nothing, or every match had no layout box\n\
         \x20 4    an asset fetch hit the deadline; the output was written but may be incomplete\n\
         \x20 101  a panic: an unparsable number, an invalid selector, an unreadable input\n",
        version = env!("CARGO_PKG_VERSION")
    )
}

/// Restore the REQUESTED url on every resource response.
///
/// `blitz_net::Provider::fetch` reports the url a response ended at, i.e. the
/// FINAL url after any HTTP redirects. `Document::load_resource` then keys its
/// image cache on that string — but `Document::pending_images`, the registry of
/// nodes waiting for a picture, is keyed by the url the element ASKED for. On a
/// redirect the two disagree, `pending_images.remove(final_url)` returns an
/// empty list, and the bytes are dropped after decoding successfully: a blank
/// box, exit code 0, nothing on stderr. Measured against a local server that
/// answers `302` with a real PNG — 795 bytes of all-white output, and both hops
/// in the server log.
///
/// Rewriting the url back to the requested one before the handler sees it makes
/// both sides of that map agree, and needs no fork of the dependency.
struct RequestedUrlProvider {
    inner: Arc<Provider>,
}

impl RequestedUrlProvider {
    fn new(inner: Arc<Provider>) -> Self {
        Self { inner }
    }

    /// In-flight request count. `Provider` exposes this as an inherent method
    /// derived from its waker's refcount (`count`, `is_empty`), not through the
    /// `NetProvider` trait, so a wrapper has to forward them by hand or the
    /// drain loop loses the only signal that tells it the fetches are done.
    fn count(&self) -> usize {
        self.inner.count()
    }

    fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }
}

impl NetProvider for RequestedUrlProvider {
    fn fetch(&self, doc_id: usize, request: Request, handler: Box<dyn NetHandler>) {
        let requested = request.url.to_string();
        self.inner.fetch(
            doc_id,
            request,
            Box::new(RequestedUrlHandler {
                requested,
                inner: handler,
            }),
        );
    }

    fn is_noop(&self) -> bool {
        self.inner.is_noop()
    }
}

struct RequestedUrlHandler {
    requested: String,
    inner: Box<dyn NetHandler>,
}

impl NetHandler for RequestedUrlHandler {
    fn bytes(self: Box<Self>, _resolved_url: String, bytes: Bytes) {
        let Self { requested, inner } = *self;
        inner.bytes(requested, bytes);
    }
}

/// Remove `clip_NN.png` files left in `dir` by an earlier, longer run.
///
/// Clip numbering restarts at 1 every run, so a re-render that matches FEWER
/// elements used to leave the tail of the previous dump behind: the caller saw
/// `clip_01..clip_03` beside stale `clip_04..clip_09` and could not tell a
/// short dump from a complete one. The pattern is exact (`clip_` + digits +
/// `.png`), so nothing else in the directory is touched, and the count is
/// reported rather than removed silently.
fn remove_stale_clip_frames(dir: &Path) -> usize {
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
        if std::fs::remove_file(entry.path()).is_ok() {
            removed += 1;
        }
    }
    removed
}

async fn real_main() {
    let args: Vec<String> = env::args().collect();

    // `--help` and `--version` are the two flags every CLI reader types before
    // reading a README, and both used to exit 2 with "unknown flag". A usage
    // request is not an error: it is the one exit that must be 0, or `docker
    // run image --help` and every `cmd --help | head` pipeline reads as broken.
    //
    // Scanned over EVERY argument, not just argv[1]: a reader who types
    // `html-render in.html out.png 794 900 --help` is asking for the same text,
    // and honouring it only in first position made the flag look broken from
    // the one place it is easiest to type. No positional is a flag, so a scan
    // cannot steal one; `--help` wins if both are present.
    if args.iter().skip(1).any(|a| a == "--help" || a == "-h") {
        print!("{}", help_text());
        std::process::exit(0);
    }
    if args.iter().skip(1).any(|a| a == "--version" || a == "-V") {
        println!("html-render {}", env!("CARGO_PKG_VERSION"));
        std::process::exit(0);
    }

    let clip = args.get(1).map(|a| a == "--clip").unwrap_or(false);
    let as_document_flag = args.iter().any(|a| a == "--document");
    let as_fragment = args.iter().any(|a| a == "--fragment");
    // Clip mode: paint one element-sized canvas per match, with the viewport
    // scrolled to that element, instead of one full-height canvas that is
    // cropped afterwards. Same picture — 0/75/58/125 pixels of 3.69 M differ by
    // one channel level on the demo deck — for a fraction of the peak memory.
    let use_scroll = args.iter().any(|a| a == "--scroll");
    let full_page = args.iter().any(|a| a == "--full");

    // Positional args start after the program name, plus the `--clip` marker.
    let pos: Vec<&String> = args
        .iter()
        .skip(if clip { 2 } else { 1 })
        .filter(|a| !a.starts_with("--"))
        .collect();

    let usage = if clip {
        "usage: html-render --clip <html> <outdir> <width> <height> <scale> <selector> [--document] [--fragment] [--scroll]"
    } else {
        "usage: html-render <html> <out.png> <width> <height> [scale] [--document] [--fragment] [--full]"
    };

    // A flag we do not implement is an error, not noise to be filtered away.
    // `--documnet` used to pass straight through the positional filter, so a
    // typo rendered a FRAGMENT of a full document and wrote a plausible PNG of
    // the wrong thing with nothing on stderr. `--clip` additionally shifts the
    // positionals by one, so it is only a mode when it leads.
    for (i, a) in args.iter().enumerate().skip(1) {
        if !a.starts_with("--") {
            continue;
        }
        if !matches!(
            a.as_str(),
            "--clip" | "--document" | "--fragment" | "--scroll" | "--full"
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
        // `--scroll` is a clip-mode reading and nothing else reads it. Accepting
        // it in single-image mode and ignoring it made the flag look honoured
        // while the output was the plain full-document render — the same
        // "silently did something else" class as an unknown flag, so it is an
        // error here too. The non-clip usage line does not list it.
        if a == "--scroll" && !clip {
            eprintln!("--scroll only applies to --clip mode");
            eprintln!("{usage}");
            std::process::exit(2);
        }
        // `--full` asks for the document's own height as the canvas. In clip
        // mode the canvas is already derived from the matched elements, so the
        // flag would name nothing; accepting it there would be the same
        // "looks honoured, changes nothing" defect as `--scroll` outside clip.
        if a == "--full" && clip {
            eprintln!("--full only applies to single-image mode");
            eprintln!("{usage}");
            std::process::exit(2);
        }
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

    let started = Instant::now();
    let source = std::fs::read_to_string(html_path).expect("read html");
    // A file that declares its own document root is rendered as one; a fragment
    // is wrapped. The flags force either reading.
    let as_document = !as_fragment && (as_document_flag || declares_document_root(&source));
    let doc_html = if as_document {
        source
    } else {
        format!(
            "<html><head><style>html,body{{margin:0;padding:0;background:#ffffff;}}</style></head><body>{source}</body></html>"
        )
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
    // applies it. is_empty() reports the PROVIDER's waker Arc count, a different
    // queue, so the loop can break with the image fetched and delivered but not
    // yet ingested. Both upstream examples do this (screenshot.rs:98,
    // paint_bench.rs:111) — measured, not assumed.
    //
    // `naive` and `yield` deliberately stay unpatched so tools/drain_probe.py
    // keeps reproducing the broken table.
    if drain_mode != "naive" && drain_mode != "yield" {
        document.resolve(0.0);
    }
    let laid_out = layout_started.elapsed();

    let paint_started = Instant::now();
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
            // Without `--full` the canvas is exactly the requested viewport,
            // so a document taller than `height` is CUT with rc=0 — a plausible
            // PNG of the top of the page and nothing on stderr. `--full` asks
            // for the document's own scroll height instead; the default stays
            // the viewport so existing goldens do not move.
            let out_phys_h = if full_page {
                let css_h = document.root_element().scroll_height();
                ((css_h * scale).round() as u32).max(phys_h)
            } else {
                phys_h
            };
            let buffer = paint_region(&mut document, 0, 0, phys_w, out_phys_h, scale);
            write_png(
                File::create(out_path).expect("create png"),
                &buffer,
                phys_w,
                out_phys_h,
            );
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
            // Swept BEFORE painting: a crash mid-emit must not leave a mixed
            // dump of this run's frames and the previous run's tail.
            let stale = remove_stale_clip_frames(Path::new(out_path));
            if stale > 0 {
                eprintln!("removed {stale} stale clip frame(s) from a previous run");
            }
            let s = scale as f64;

            // Every matched element's box, in document coordinates, at scroll 0.
            //
            // Elements with no layout box (`<br>`, `<hr>`, `<wbr>`, `<script>`,
            // `<style>`, …) have no geometry, and `*` matches them in any real
            // document. They are SKIPPED, not fatal: a clip dump is a diagnostic,
            // so one unlayoutable element must not destroy the other 300 boxes.
            // The skip is reported rather than silent, and `clip_NN` numbering
            // stays contiguous over the emitted boxes, so a caller can tell a
            // short dump from a complete one.
            let mut skipped = 0usize;
            let rects: Vec<_> = ids
                .iter()
                .filter_map(|id| match document.get_client_bounding_rect(*id) {
                    Some(rect) => Some(rect),
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
            if rects.is_empty() {
                eprintln!("no matched element for selector {sel:?} has a layout box");
                std::process::exit(3);
            }

            if use_scroll {
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
                    document.set_viewport_scroll(Point { x: rect.x, y: rect.y });
                    let buffer = paint_region(&mut document, 0, 0, w, h, scale);
                    println!(
                        "clip_{:02} css x={:.2} y={:.2} w={:.2} h={:.2}  physical {}x{} (scroll)",
                        i + 1,
                        rect.x,
                        rect.y,
                        rect.width,
                        rect.height,
                        w,
                        h
                    );
                    let path = Path::new(out_path).join(format!("clip_{:02}.png", i + 1));
                    write_png(File::create(&path).expect("create png"), &buffer, w, h);
                }
                document.set_viewport_scroll(Point::ZERO);
            } else {
                // Paint ONE full-height canvas at zero offset and crop each element out
                // of it, rather than painting one region per element.
                //
                // Two reasons, both measured on the real deck:
                //   * the viewport must stay at the requested size, because `vh` units
                //     resolve against it — enlarging it to the document height would
                //     relayout the page (the deck has 3 `vh` uses);
                //   * a non-zero paint offset is not reliable for replaced content: with
                //     the offset set, slides whose photos sit below the first viewport
                //     lost them, while the same slides were exact when cropped from a
                //     full-document render.
                //
                // The canvas is physical px; the element boxes are CSS px.
                let doc_phys_h = rects
                    .iter()
                    .map(|r| ((r.y + r.height) * s).round() as u32)
                    .max()
                    .unwrap_or(phys_h)
                    .max(phys_h);
                let canvas = paint_region(&mut document, 0, 0, phys_w, doc_phys_h, scale);

                for (i, rect) in rects.iter().enumerate() {
                    let x = ((rect.x * s).round() as u32).min(phys_w.saturating_sub(1));
                    let y = ((rect.y * s).round() as u32).min(doc_phys_h.saturating_sub(1));
                    let w = (((rect.width * s).round() as u32).max(1)).min(phys_w - x);
                    let h = (((rect.height * s).round() as u32).max(1)).min(doc_phys_h - y);
                    println!(
                        "clip_{:02} css x={:.2} y={:.2} w={:.2} h={:.2}  physical {}x{} at {},{}",
                        i + 1,
                        rect.x,
                        rect.y,
                        rect.width,
                        rect.height,
                        w,
                        h,
                        x,
                        y
                    );
                    let mut out = Vec::with_capacity((w * h * 4) as usize);
                    for row in 0..h {
                        let start = (((y + row) * phys_w + x) * 4) as usize;
                        out.extend_from_slice(&canvas[start..start + (w * 4) as usize]);
                    }
                    let path = Path::new(out_path).join(format!("clip_{:02}.png", i + 1));
                    write_png(File::create(&path).expect("create png"), &out, w, h);
                }
            }
        }
    }
    let painted = paint_started.elapsed();

    eprintln!(
        "TIMING parse_ms={} layout_ms={} paint_ms={} total_ms={} scale={} mode={}",
        parsed.as_millis(),
        laid_out.as_millis(),
        painted.as_millis(),
        started.elapsed().as_millis(),
        scale,
        if as_document { "document" } else { "fragment" }
    );

    // After the diagnostics, so a caller still gets the timing that says how
    // far the run got before the asset deadline.
    if asset_deadline_hit {
        eprintln!("error: exiting 4: assets may be missing (see the warning above)");
        std::process::exit(4);
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

fn write_png<W: std::io::Write>(writer: W, buffer: &[u8], width: u32, height: u32) {
    let mut encoder = png::Encoder::new(writer, width, height);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    let mut writer = encoder.write_header().expect("png header");
    writer.write_image_data(buffer).expect("png data");
    writer.finish().expect("png finish");
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
