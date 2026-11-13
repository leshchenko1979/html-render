#!/usr/bin/env python3
"""Control for the RSS instrument: does it see a process TREE, or one process?

The whole reason tools/measure_rss.py exists is that both earlier instruments
reported the largest single process rather than the sum. A test that only checks
"the number is not zero" cannot tell those apart, so this one runs a command
whose tree size is known by construction:

    parent (small) -> 3 children, each holding ~50 MiB

A tree-summing sampler must report roughly the total. A per-process instrument
must report roughly one child. The gap between them IS the defect, so the test
asserts BOTH readings on the SAME command, plus the ratio: a sampler that
happened to report a big number for the wrong reason would still have to explain
why `--rss-mode max` reads a third of it.

    python3 tools/test_measure_rss.py

Exit 0 = the instrument discriminates. Exit 1 = it does not.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_rss  # noqa: E402

MIB = 50
CHILDREN = 3
# Each child allocates MIB and touches every page: `bytearray(n)` alone may be
# lazily mapped, so the write loop is what makes the pages resident.
CHILD = """
import sys, time
n = int(sys.argv[1]) * 1024 * 1024
buf = bytearray(n)
for i in range(0, n, 4096):
    buf[i] = 1
time.sleep(float(sys.argv[2]))
"""

# The parent stays small on purpose. If it held the memory itself, a per-process
# instrument reading the parent would also report the total and this control
# would pass for the wrong reason. The child's SOURCE is read here and passed to
# `-c` as code: `-c <path>` would execute the path string, which is a syntax
# error the child dies of instantly — the first draft of this test did exactly
# that and read 52 MiB with no children alive.
PARENT = """
import subprocess, sys, time
src = open(sys.argv[1]).read()
kids = [subprocess.Popen([sys.executable, "-c", src, sys.argv[2], sys.argv[3]])
        for _ in range(int(sys.argv[4]))]
time.sleep(float(sys.argv[5]))
for k in kids:
    k.wait()
"""


def main():
    with tempfile.TemporaryDirectory() as tmp:
        child_py = os.path.join(tmp, "child.py")
        parent_py = os.path.join(tmp, "parent.py")
        open(child_py, "w").write(CHILD)
        open(parent_py, "w").write(PARENT)

        cmd = [sys.executable, parent_py, child_py, str(MIB), "3.0", str(CHILDREN), "2.0"]

        tree_rc, _o, _e, tree_wall, tree_kib = measure_rss.run_measured(cmd, mode="tree")
        max_rc, _o2, _e2, max_wall, max_kib = measure_rss.run_measured(cmd, mode="max")

    tree_mib = tree_kib / 1024.0
    max_mib = max_kib / 1024.0
    fails = []

    print("command: %d children x %d MiB, both readings on the same command"
          % (CHILDREN, MIB))
    print("  tree  rc=%d %6.1f MiB  (%.2f s)" % (tree_rc, tree_mib, tree_wall))
    print("  max   rc=%d %6.1f MiB  (%.2f s)" % (max_rc, max_mib, max_wall))
    print("  want  tree ~%d MiB (sum), max ~%d MiB (one child)" % (MIB * CHILDREN, MIB))

    if tree_rc != 0 or max_rc != 0:
        fails.append("a leg exited non-zero (tree rc=%d, max rc=%d)" % (tree_rc, max_rc))

    # The sum, with room for interpreter overhead and a 25 ms sampling period.
    lo, hi = MIB * CHILDREN * 0.85, MIB * CHILDREN * 2.0
    if not lo <= tree_mib <= hi:
        fails.append(
            "tree reading %.1f MiB is outside %.0f-%.0f MiB: the sampler is not "
            "summing the tree" % (tree_mib, lo, hi)
        )
    # One process, same room.
    if not MIB * 0.85 <= max_mib <= MIB * 1.8:
        fails.append(
            "max reading %.1f MiB is outside %.0f-%.0f MiB: the old instrument "
            "should see ONE child, so a value near the tree total means this leg "
            "is not measuring what it claims"
            % (max_mib, MIB * 0.85, MIB * 1.8)
        )
    # The discriminating property, as its own assertion so a future refactor
    # cannot quietly make the two modes the same thing.
    if max_mib > 0 and tree_mib < max_mib * 2.0:
        fails.append(
            "the two modes agree (tree %.1f vs max %.1f MiB): the instrument no "
            "longer discriminates a tree from a process" % (tree_mib, max_mib)
        )

    if fails:
        print("\nFAIL")
        for f in fails:
            print("  " + f)
        return 1
    print("\nPASS  ratio tree/max = %.2fx" % (tree_mib / max_mib if max_mib else 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
