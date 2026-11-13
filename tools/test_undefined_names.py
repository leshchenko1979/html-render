#!/usr/bin/env python3
"""Catch a name that is referenced but defined nowhere.

`python3 -m py_compile` accepts this, and so does every linter configured to
look at style:

    def main():
        ...
        return result          # `result` was removed by a refactor

That is exactly how `tools/run_parity.py` shipped a `NameError` to CI: the
corpus gate printed PASS, then the function crashed on its own return
statement, so the job went red for a reason that had nothing to do with the
corpus. A green gate followed by a traceback is worse than a red gate, because
the report is written before the crash and reads as a measurement.

This walks every scope with `symtable` and reports a name that is read, is not
local, is not a parameter, is not declared `global`/`nonlocal`, and is not bound
at module level or in `builtins`. Stdlib only, and the target is never imported.

Usage:
  test_undefined_names.py [--self-test] [--quiet] [path ...]

  (no path)     scan tools/
  --self-test   run the contract legs, including a scan of the real tree
"""

import argparse
import builtins
import os
import symtable
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUILTINS = set(dir(builtins))


def module_names(table):
    """Names bound at module scope: assignments, imports, defs, classes."""
    return {
        sym.get_name()
        for sym in table.get_symbols()
        if sym.is_assigned() or sym.is_imported() or sym.is_namespace()
    }


def walk(table, known, out, path):
    """Report referenced names that resolve to nothing, then recurse."""
    for sym in table.get_symbols():
        name = sym.get_name()
        if not sym.is_referenced():
            continue
        if (
            sym.is_parameter()
            or sym.is_local()
            or sym.is_free()
            or sym.is_imported()
            or sym.is_assigned()
            or sym.is_namespace()
        ):
            continue
        if name in known or name in BUILTINS:
            continue
        # `__class__`, `__file__` and friends are injected by the interpreter.
        if name.startswith("__") and name.endswith("__"):
            continue
        out.append((path, table.get_name(), name))

    for child in table.get_children():
        walk(child, known, out, path)


def scan(path):
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    top = symtable.symtable(source, path, "exec")
    out = []
    walk(top, module_names(top), out, path)
    return out


def targets(paths):
    files = []
    for target in paths:
        if os.path.isdir(target):
            for name in sorted(os.listdir(target)):
                if name.endswith(".py"):
                    files.append(os.path.join(target, name))
        else:
            files.append(target)
    return files


def report(problems, quiet=False):
    if not quiet:
        for path, scope, name in problems:
            print(f"{os.path.relpath(path)}: in {scope}(): '{name}' is read but defined nowhere")
    return problems


# --- contract legs ---------------------------------------------------------

fails = []
ran = 0


def leg(name, ok, detail=""):
    global ran
    ran += 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        fails.append(f"{name}: {detail}")


def self_test():
    with tempfile.TemporaryDirectory() as tmp:
        # NEGATIVE CONTROL: the exact shape that shipped to CI.
        bug = os.path.join(tmp, "bug.py")
        with open(bug, "w") as fh:
            fh.write("def main():\n    x = 1\n    return result\n")
        got = scan(bug)
        leg(
            "a return of a name that was removed is caught",
            [g[2] for g in got] == ["result"],
            f"got {got!r}",
        )

        # NEGATIVE CONTROL: a typo in a call, not a return.
        typo = os.path.join(tmp, "typo.py")
        with open(typo, "w") as fh:
            fh.write("def f():\n    return len(seq)\n")
        leg(
            "a misspelled global is caught",
            [g[2] for g in scan(typo)] == ["seq"],
            f"got {scan(typo)!r}",
        )

        # POSITIVE CONTROLS: every legitimate way for a name to resolve.
        ok = os.path.join(tmp, "ok.py")
        with open(ok, "w") as fh:
            fh.write(
                "import os\n"
                "K = 2\n"
                "def outer():\n"
                "    local = 1\n"
                "    def inner():\n"
                "        return local + K + len(os.sep) + abs(local)\n"
                "    return inner\n"
                "class C:\n"
                "    attr = K\n"
                "    def m(self):\n"
                "        return self.attr\n"
            )
        leg("locals, closures, globals, imports, builtins and classes all pass",
            scan(ok) == [], f"got {scan(ok)!r}")

        # A `global` declaration must not be read as a local.
        glob = os.path.join(tmp, "glob.py")
        with open(glob, "w") as fh:
            fh.write("N = 0\ndef bump():\n    global N\n    N = N + 1\n")
        leg("a `global` rebind passes", scan(glob) == [], f"got {scan(glob)!r}")

    # The leg that matters: the tree CI is about to build.
    files = targets([HERE])
    problems = report(scan_all(files), quiet=True)
    leg(f"the shipped tree is clean ({len(files)} files)", problems == [], f"{problems!r}")

    print()
    if fails:
        print(f"legs run: {len(fails)} FAILED")
        for f in fails:
            print(f"  {f}")
        return 1
    print(f"legs run: {ran} / PASS")
    return 0


def scan_all(files):
    out = []
    for path in files:
        out.extend(scan(path))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="files or directories (default: tools/)")
    ap.add_argument("--self-test", action="store_true", help="run the contract legs")
    ap.add_argument("--quiet", action="store_true", help="only the verdict line")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    files = targets(args.paths or [HERE])
    problems = report(scan_all(files), quiet=args.quiet)
    print(f"\n{len(files)} file(s) scanned, {len(problems)} undefined name(s)")
    print("FAIL" if problems else "PASS")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
