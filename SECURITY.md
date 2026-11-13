# Security

## What rendering does to the machine

It renders HTML and CSS to PNG. **No JavaScript engine is compiled in**, so a
document cannot execute code — that is the point of the design, and it removes
the largest attack surface a headless browser has.

**No-JS is not the same thing as a sandbox.** Two things happen while rendering:

1. **`http(s)://` sub-resources are fetched at render time, with no allowlist.**
   An `<img>`, a `background-image`, a `@font-face` or a CSS `url()` in the
   document becomes an outbound request from the machine running the renderer.
   Measured: a document referencing `http://127.0.0.1:8765/red.png` produced one
   `GET` in that server's access log and a painted `(200,0,0)` block in the
   output. So a document you did not write can make the renderer reach hosts
   *you* can reach — loopback, link-local, private networks, cloud metadata
   endpoints. If you render untrusted HTML, do it where that reachability is
   intended: a network-namespaced container, a throwaway VM, or behind an egress
   proxy that filters by destination.
2. **`file://` is readable.** The document's base URL is `file://` of its own
   path, so relative resources resolve on disk, and an absolute `file://` URL in
   the markup is fetched the same way. A document can pull local files into the
   render, and can probe for their existence by whether a box paints.

There is no sandbox: no seccomp profile, no capability drop, no chroot, no user
switch. The process runs with the privileges of whoever invoked it, and writes
wherever that user can write.

## Reporting

Open a [private security advisory](https://github.com/leshchenko1979/html-render/security/advisories/new)
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
  reported as a normal issue — see the [Corpus](README.md#corpus) section.
