#!/usr/bin/env python3
"""Fold `perf script` stacks into one line per sample.

Output line: `comm;outermost;...;innermost` so the file stays small enough to
retain in full and aggregate later, unlike the multi-line script head. Kernel
frames that perf cannot resolve inside the container are labelled [kernel].
"""
import re
import sys

FRAME = re.compile(r"^\s+([0-9a-f]+)\s+(.+?)\s+\((.*)\)\s*$")


def fold(lines):
    comm = None
    stack = []
    for line in lines:
        if not line.strip():
            if comm is not None:
                yield comm + ";" + ";".join(reversed(stack))
            comm = None
            stack = []
            continue
        if not line[0].isspace():
            if comm is not None:
                yield comm + ";" + ";".join(reversed(stack))
            comm = line.split()[0]
            stack = []
            continue
        match = FRAME.match(line)
        if match is None or comm is None:
            continue
        addr, sym, _dso = match.groups()
        sym = re.sub(r"\+0x[0-9a-f]+$", "", sym)
        if sym == "[unknown]" and addr.startswith("ffffffff"):
            sym = "[kernel]"
        stack.append(sym)
    if comm is not None:
        yield comm + ";" + ";".join(reversed(stack))


def main():
    try:
        for folded in fold(sys.stdin):
            sys.stdout.write(folded + "\n")
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
