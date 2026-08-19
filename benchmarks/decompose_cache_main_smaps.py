#!/usr/bin/env python3
"""Decompose a captured cache-main ``/proc/<pid>/smaps`` file.

The result is deliberately a mapping classification, not an allocator report.
In particular, an anonymous mapping is not evidence that cachetag or Vinyl
owns the bytes in it.  Every mapping is assigned to exactly one category so
that the category PSS/RSS totals can be checked against the parsed smaps
totals.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence


SCHEMA = "cache-main-smaps-decomposition-v1"
CATEGORIES = (
    "heap",
    "anonymous",
    "thread_stacks",
    "executable_shared_libraries",
    "vinyl_cachetag",
    "storage_file",
    "unknown_file_backed",
)

# Linux smaps headers have six whitespace-delimited fields followed by an
# optional pathname.  The pathname may contain spaces, so it is retained by
# the final capture group.
MAP_HEADER = re.compile(
    r"^(?P<start>[0-9a-fA-F]+)-(?P<end>[0-9a-fA-F]+)\s+"
    r"(?P<perms>\S+)\s+(?P<offset>[0-9a-fA-F]+)\s+"
    r"(?P<dev>\S+)\s+(?P<inode>\d+)(?:\s+(?P<path>.*?))?\s*$"
)
SMAPS_VALUE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_]*):\s+(?P<value>\d+)\s+kB\s*$")
SMAPS_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]*:\s+.*$")
STACK_PATH = re.compile(r"^\[stack(?::\d+)?\]$")
ANONYMOUS_SPECIAL = re.compile(r"^\[(?:anon(?::.*)?|vvar(?:_vclock)?|vsyscall)\]$")
EXECUTABLE_SPECIAL = {"[vdso]"}


class SmapsParseError(ValueError):
    """The input is not a complete, parseable smaps capture."""


@dataclass
class Mapping:
    start: int
    end: int
    permissions: str
    offset: int
    device: str
    inode: int
    pathname: str
    line_number: int
    values: dict[str, int] = field(default_factory=dict)

    @property
    def size_kb(self) -> int:
        return self.values["Size"]

    @property
    def rss_kb(self) -> int:
        return self.values["Rss"]

    @property
    def pss_kb(self) -> int:
        return self.values["Pss"]


def _finish_mapping(mapping: Mapping | None, line_number: int) -> Mapping | None:
    if mapping is None:
        return None
    missing = [key for key in ("Size", "Rss", "Pss") if key not in mapping.values]
    if missing:
        raise SmapsParseError(
            f"line {mapping.line_number}: mapping {mapping.pathname or '<anonymous>'} "
            f"is missing {', '.join(missing)}"
        )
    if mapping.end <= mapping.start:
        raise SmapsParseError(f"line {mapping.line_number}: invalid mapping range")
    range_size_kb = (mapping.end - mapping.start) // 1024
    if mapping.size_kb != range_size_kb:
        raise SmapsParseError(
            f"line {mapping.line_number}: Size {mapping.size_kb} kB does not match "
            f"mapping range {range_size_kb} kB"
        )
    if mapping.pss_kb > mapping.rss_kb:
        raise SmapsParseError(
            f"line {mapping.line_number}: Pss {mapping.pss_kb} kB exceeds Rss {mapping.rss_kb} kB"
        )
    return mapping


def parse_smaps(text: str) -> list[Mapping]:
    """Parse full smaps text and fail closed on incomplete mapping records."""

    mappings: list[Mapping] = []
    current: Mapping | None = None
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.rstrip("\n")
        if not line.strip():
            continue
        header = MAP_HEADER.match(line)
        if header:
            finished = _finish_mapping(current, line_number)
            if finished is not None:
                mappings.append(finished)
            groups = header.groupdict()
            current = Mapping(
                start=int(groups["start"], 16),
                end=int(groups["end"], 16),
                permissions=groups["perms"],
                offset=int(groups["offset"], 16),
                device=groups["dev"],
                inode=int(groups["inode"]),
                pathname=(groups["path"] or "").strip(),
                line_number=line_number,
            )
            continue
        if current is None:
            raise SmapsParseError(f"line {line_number}: expected a mapping header")
        value = SMAPS_VALUE.match(line)
        if value:
            current.values[value.group("key")] = int(value.group("value"))
            continue
        # VmFlags, THPeligible, ProtectionKey, and future non-kB smaps fields
        # are intentionally ignored; the required Size/Rss/Pss fields above
        # still make an incomplete mapping fail closed.
        if SMAPS_FIELD.match(line):
            continue
        raise SmapsParseError(f"line {line_number}: unrecognised smaps line: {line!r}")

    finished = _finish_mapping(current, len(text.splitlines()) + 1)
    if finished is not None:
        mappings.append(finished)
    if not mappings:
        raise SmapsParseError("smaps capture contains no mappings")
    return mappings


def _matches_storage(pathname: str, storage_patterns: Sequence[str]) -> bool:
    lower = pathname.lower()
    if any(pattern.lower() in lower for pattern in storage_patterns):
        return True
    # These are conservative filename markers used by the benchmark storage
    # backends.  Generic file-backed mappings remain residue unless the caller
    # supplies an explicit --storage-pattern.
    basename = lower.rsplit("/", 1)[-1]
    return (
        basename.startswith(("storage", "fellow", "buddy"))
        or basename.endswith((".wal", ".seg", ".db", ".storage"))
    )


def classify_mapping(pathname: str, permissions: str, storage_patterns: Sequence[str] = ()) -> tuple[str, str]:
    """Return one category and an audit-friendly reason for a mapping."""

    path = pathname.strip()
    lower = path.lower()
    if path == "[heap]":
        return "heap", "special [heap] mapping"
    if STACK_PATH.match(path):
        return "thread_stacks", "special thread stack mapping"
    if path in EXECUTABLE_SPECIAL or (path and (path == "[vsyscall]")):
        return "executable_shared_libraries", "kernel executable mapping"
    if not path or path.startswith("anon_inode:") or ANONYMOUS_SPECIAL.match(path):
        return "anonymous", "anonymous or kernel special mapping"

    if _matches_storage(path, storage_patterns):
        return "storage_file", "storage pathname marker"

    basename = lower.rsplit("/", 1)[-1]
    if (
        basename.startswith(("vinyld", "cache-main", "vinyltest"))
        or "libvmod_cachetag" in basename
        or "libvmod-cachetag" in basename
        or "/libvmod/" in lower
    ):
        return "vinyl_cachetag", "Vinyl/cachetag executable or VMOD pathname"

    if "x" in permissions or re.search(r"\.so(?:\.\d+)*$", basename):
        return "executable_shared_libraries", "executable or shared-library mapping"
    return "unknown_file_backed", "unmatched file-backed pathname"


def _empty_totals() -> dict[str, int]:
    return {"mappings": 0, "size_kb": 0, "rss_kb": 0, "pss_kb": 0}


def decompose_mappings(
    mappings: Iterable[Mapping], storage_patterns: Sequence[str] = ()
) -> dict[str, object]:
    """Build JSON-serialisable category totals and conservation checks."""

    categories = {category: _empty_totals() for category in CATEGORIES}
    details: list[dict[str, object]] = []
    parsed = list(mappings)
    for mapping in parsed:
        category, reason = classify_mapping(mapping.pathname, mapping.permissions, storage_patterns)
        totals = categories[category]
        totals["mappings"] += 1
        totals["size_kb"] += mapping.size_kb
        totals["rss_kb"] += mapping.rss_kb
        totals["pss_kb"] += mapping.pss_kb
        details.append(
            {
                "range": f"{mapping.start:x}-{mapping.end:x}",
                "permissions": mapping.permissions,
                "pathname": mapping.pathname,
                "category": category,
                "reason": reason,
                "size_kb": mapping.size_kb,
                "rss_kb": mapping.rss_kb,
                "pss_kb": mapping.pss_kb,
            }
        )

    # Keep an independently accumulated parsed total.  Comparing category
    # totals with a total derived from those same categories would make the
    # conservation checks tautological.
    total = _empty_totals()
    for mapping in parsed:
        total["mappings"] += 1
        total["size_kb"] += mapping.size_kb
        total["rss_kb"] += mapping.rss_kb
        total["pss_kb"] += mapping.pss_kb
    category_sum = {
        key: sum(values[key] for values in categories.values()) for key in total
    }
    checks = {
        "category_mapping_count_conserved": category_sum["mappings"] == total["mappings"],
        "category_size_conserved": category_sum["size_kb"] == total["size_kb"],
        "category_rss_conserved": category_sum["rss_kb"] == total["rss_kb"],
        "category_pss_conserved": category_sum["pss_kb"] == total["pss_kb"],
        "pss_not_greater_than_rss": total["pss_kb"] <= total["rss_kb"],
    }
    return {
        "schema": SCHEMA,
        "mapping_count": len(parsed),
        "categories": categories,
        "totals": total,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "mappings": details,
    }


def decompose_file(path: Path, storage_patterns: Sequence[str] = ()) -> dict[str, object]:
    result = decompose_mappings(
        parse_smaps(path.read_text(encoding="utf-8", errors="replace")), storage_patterns
    )
    result["source_smaps"] = str(path)
    return result


def _text_report(result: dict[str, object]) -> str:
    lines = [f"schema={result['schema']}", f"mapping_count={result['mapping_count']}"]
    totals = result["totals"]
    assert isinstance(totals, dict)
    lines.append(
        "totals="
        + " ".join(f"{key}={totals[key]}" for key in ("size_kb", "rss_kb", "pss_kb"))
    )
    categories = result["categories"]
    assert isinstance(categories, dict)
    for category in CATEGORIES:
        values = categories[category]
        lines.append(
            f"{category}="
            + " ".join(f"{key}={values[key]}" for key in ("mappings", "size_kb", "rss_kb", "pss_kb"))
        )
    lines.append(f"all_checks_pass={int(bool(result['all_checks_pass']))}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("smaps", type=Path, help="captured full cache-main smaps file")
    parser.add_argument("--storage-pattern", action="append", default=[], help="additional case-insensitive storage pathname marker")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args(argv)
    try:
        result = decompose_file(args.smaps, args.storage_pattern)
    except (OSError, SmapsParseError) as exc:
        print(f"decompose_cache_main_smaps: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(_text_report(result), end="")
    return 0 if result["all_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
