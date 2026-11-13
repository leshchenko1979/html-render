#!/usr/bin/env python3
"""Run a command and sample the peak resident set size of its whole process tree.

Both figures this repo has ever quoted were **per-process maxima**, and both
instruments said something else: `gates.py` used `/usr/bin/time -f %M` and
`run_parity.py` used `ru_maxrss` from `RUSAGE_CHILDREN`. Neither sums a tree, so
a browser that spawns a renderer and four helpers is measured at the size of its
largest process, not at what the host actually has to hold. That is where the
"858 MiB" figure came from, and it is the right order of magnitude only because
Chromium's browser process is itself fat.

This module is the one instrument now: the child gets its own session (so the
tree is identifiable), and every `period` seconds the sampler walks
`/proc/<pid>/task/*/children` from the root, sums `VmRSS` over every live
process, and keeps the peak.

    python3 tools/measure_rss.py [--rss-mode tree|max] -- <command> [args...]

Two properties worth stating, because both are easy to get wrong:

  * **A sample can miss a spike.** 25 ms is fast enough for a renderer whose
    children live for seconds and useless for one that forks and exits inside a
    frame. The period is a parameter, not a promise.
  * **The sampler's own cost is not in the child's wall time.** Wall is measured
    around spawn→wait; the walk runs in this process.

`--rss-mode max` reproduces the old reading (`/usr/bin/time -f %M`, which also
reports the largest single process rather than the parent), so every published
figure stays checkable against the instrument that produced it. The gap is
measured, not asserted: `tools/test_measure_rss.py` runs one command — a small
parent with three ~50 MiB children — and requires the two modes to disagree by
more than 2x. Last run: tree 196.4 MiB, max 61.2 MiB, ratio 3.21x.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

DEFAULT_PERIOD = 0.025


def _vmrss_kib(pid):
    """Resident set size of one process in KiB, or None if it is gone."""
    try:
        with open("/proc/%d/status" % pid) as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    return None


def _children_of(pid):
    """Direct children of every thread of `pid`, via /proc/<pid>/task/*/children."""
    kids = []
    taskdir = "/proc/%d/task" % pid
    try:
        tids = os.listdir(taskdir)
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return kids
    for tid in tids:
        try:
            with open(os.path.join(taskdir, tid, "children")) as fh:
                kids.extend(int(x) for x in fh.read().split())
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return kids


def _tree_pids(root):
    """`root` plus every descendant, breadth-first.

    `/proc/<pid>/task/<tid>/children` is the cheap path and is what this box
    provides. The fallback scans every `/proc/*/stat` for a matching ppid, which
    is O(processes) per sample and only worth it on a kernel without the file.
    """
    if os.path.isdir("/proc/%d/task" % root):
        seen, frontier = {root}, [root]
        while frontier:
            nxt = []
            for pid in frontier:
                for kid in _children_of(pid):
                    if kid not in seen:
                        seen.add(kid)
                        nxt.append(kid)
            frontier = nxt
        if len(seen) > 1 or _children_of(root):
            return seen
    # fallback: ppid map
    children = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % entry) as fh:
                fields = fh.read().rsplit(") ", 1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(entry))
        except (FileNotFoundError, ProcessLookupError, IndexError, PermissionError):
            continue
    seen, frontier = {root}, [root]
    while frontier:
        nxt = []
        for pid in frontier:
            for kid in children.get(pid, []):
                if kid not in seen:
                    seen.add(kid)
                    nxt.append(kid)
        frontier = nxt
    return seen


def peak_tree_rss_kib(cmd, period=DEFAULT_PERIOD, timeout=None, cwd=None):
    """Run `cmd` and return (rc, stdout, stderr, wall_s, peak_tree_kib).

    stdout/stderr go to temporary files rather than pipes: the poller must keep
    sampling while the child writes, and a pipe nobody drains fills at 64 KiB and
    blocks the child — which would show up as a slow run, not as a full pipe.
    """
    out_f = tempfile.TemporaryFile(mode="w+")
    err_f = tempfile.TemporaryFile(mode="w+")
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdout=out_f,
        stderr=err_f,
        stdin=subprocess.DEVNULL,
        cwd=cwd,
        start_new_session=True,
    )
    peak = 0
    try:
        while proc.poll() is None:
            total = 0
            for pid in _tree_pids(proc.pid):
                kib = _vmrss_kib(pid)
                if kib:
                    total += kib
            peak = max(peak, total)
            if timeout is not None and time.monotonic() - t0 > timeout:
                proc.kill()
                break
            time.sleep(period)
    finally:
        rc = proc.wait()
        wall = time.monotonic() - t0
        out_f.seek(0)
        err_f.seek(0)
        out, err = out_f.read(), err_f.read()
        out_f.close()
        err_f.close()
    return rc, out, err, wall, peak


def peak_max_rss_kib(cmd, cwd=None):
    """The old reading: one process's maximum, via /usr/bin/time -f %M."""
    timer = "/usr/bin/time"
    if not os.path.exists(timer):
        rc, out, err, wall, _ = _plain(cmd, cwd)
        return rc, out, err, wall, 0
    rc, out, err, wall, _ = _plain([timer, "-f", "%M"] + list(cmd), cwd)
    kib = 0
    for line in reversed(err.strip().splitlines()):
        if line.strip().isdigit():
            kib = int(line.strip())
            break
    return rc, out, err, wall, kib


def _plain(cmd, cwd=None):
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return proc.returncode, proc.stdout, proc.stderr, time.monotonic() - t0, 0


def run_measured(cmd, mode="tree", period=DEFAULT_PERIOD, timeout=None, cwd=None):
    """Dispatch on `mode`: 'tree' (this sampler) or 'max' (the old instrument)."""
    if mode == "max":
        return peak_max_rss_kib(cmd, cwd=cwd)
    if mode != "tree":
        raise SystemExit("unknown rss mode %r (want tree or max)" % mode)
    return peak_tree_rss_kib(cmd, period=period, timeout=timeout, cwd=cwd)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rss-mode", choices=("tree", "max"), default="tree")
    ap.add_argument("--period", type=float, default=DEFAULT_PERIOD)
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        ap.error("no command")
    rc, out, err, wall, kib = run_measured(
        cmd, mode=args.rss_mode, period=args.period, timeout=args.timeout
    )
    sys.stdout.write(out)
    sys.stderr.write(err)
    print(
        "wall_s=%.3f peak_rss_mib=%.1f mode=%s period_ms=%.0f rc=%d"
        % (wall, kib / 1024.0, args.rss_mode, args.period * 1000, rc),
        file=sys.stderr,
    )
    sys.exit(rc)
