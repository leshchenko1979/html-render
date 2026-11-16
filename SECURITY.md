# Security

## What rendering does to the machine

It renders HTML and CSS to PNG. **No JavaScript engine is compiled in**, so a
document cannot execute code — that is the point of the design, and it removes
the largest attack surface a headless browser has.

**No-JS is not the same thing as a sandbox.** Two things happen while rendering:

1. **`http(s)://` sub-resources are fetched at render time, with no allowlist.**
   An `<img>`, a `background-image`, a `@font-face`, a CSS `url()` or a
   `<link rel="stylesheet">` in the document becomes an outbound request from the
   machine running the renderer. The stylesheet is the widest of these: it hands
   the request to the CSS parser (Stylo), not the image decoder.
   Measured: a document referencing `http://127.0.0.1:8765/red.png` produced one
   `GET` in that server's access log and a painted `(200,0,0)` block in the
   output. So a document you did not write can make the renderer reach hosts
   *you* can reach — loopback, link-local, private networks, cloud metadata
   endpoints. If you render untrusted HTML, do it where that reachability is
   intended: a network-namespaced container, a throwaway VM, or behind an egress
   proxy that filters by destination. The proxy does see the traffic — the
   request honours `http_proxy` (measured: the proxy logged
   `GET http://example.invalid/x.png`) — but **do not key an allowlist on the
   `User-Agent`**: it identifies as a desktop browser
   (`Mozilla/5.0 … Firefox/81.0`), so a UA-based rule will not tell a render
   apart from a person.

   One failure mode here is silent and worth knowing before you trust an exit
   code: a `200` whose body is not an image paints an empty box and exits `0`,
   because the decode failure happens inside Blitz, after the bytes were
   accepted. A `404` and a refused redirect are loud — exit `4`, with the
   resource named on stderr. A missed deadline also exits `4`, but names no
   resource: it says only that asset fetching stopped, so an output that is
   missing images cannot be traced to a URL from the log alone. Decoded size is capped at 128 MP by
   `BLITZ_MAX_IMAGE_MP`, so one image cannot expand past roughly 488 MiB of
   RGBA; the whole render can, and `tools/memory_baseline.json` pins the
   measured worst case at 809.5 MiB. Verify the artefact, not the status code.
2. **`file://` is readable.** The document's base URL is `file://` of its own
   path, so relative resources resolve on disk, and an absolute `file://` URL in
   the markup is fetched the same way. A document can pull local files into the
   render, and can probe for their existence by whether a box paints.

There is no sandbox: no seccomp profile, no capability drop, no chroot, no user
switch. The process runs with the privileges of whoever invoked it, and writes
wherever that user can write.

## Reporting

Open a [private security advisory](https://github.com/leshchenko1979/pageplate/security/advisories/new)
rather than a public issue. One maintainer, no SLA. Worth reporting: a crash on
input that is not obviously malformed, unbounded memory or CPU from a small
document, a path that writes outside the requested output, and anything that
turns a document into code execution — that would be a defect in Blitz, but
routing it is still ours.

## Out of scope

- **A document fetching a URL it names.** That is the documented behaviour
  above, not a vulnerability. Constrain it at the network layer.
- **Resource exhaustion from a deliberately huge document.** A 10,000-row table
  will use the memory it needs. Bound it with your own timeout and memory limit.
- **Pixels that differ from Chromium.** A fidelity gap is a correctness issue,
  reported as a normal issue — see the [Corpus](GATES.md#corpus) section.

## This repository's own supply chain

Every `uses:` in `.github/workflows/` is pinned to a full commit SHA, with the
version in a trailing comment so a reader still knows what it was. A tag can be
re-pointed at a different commit; a SHA cannot, and a silently swapped action is
the shape of most CI compromises.

`dtolnay/rust-toolchain` is the single exception, and it is not an oversight: that
action's **ref name is its input**. `@stable` means "install stable", and a bare
SHA arrives with no toolchain spec and fails on `'toolchain' is a required
input`. It is pinned to a `master` commit with `toolchain: stable` passed
explicitly, so the code is frozen and the toolchain still tracks stable.

`.github/dependabot.yml` keeps the pins current. Minor and patch bumps arrive as
one grouped PR; majors arrive separately, because both `checkout` and
`upload-artifact` v7 changed runner and module requirements and a major is not
something to merge unread.
