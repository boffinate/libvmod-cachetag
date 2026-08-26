#!/usr/bin/env python3
"""Generate deterministic synthetic bound fixtures, never a CMS substitute."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fixture_loader import SCHEMA_VERSION, FixtureRecord, fixture_metrics


SCENARIOS = (
    "mostly-unique-bound",
    "mostly-shared-bound",
    "uniform-cyclic",
    "hot-set",
    "ordinary-body-4k",
)


def bound_tag(kind: str, obj: int, slot: int, tags_per_object: int, tag_length_class: str) -> str:
    if tag_length_class == "short":
        if kind == "unique":
            return f"u{obj * tags_per_object + slot:x}"
        return f"s{slot:x}"
    if tag_length_class == "long":
        if kind == "unique":
            return f"benchmark-long-cachetag-unique-object-{obj:010d}-slot-{slot:02d}-edge"
        return f"benchmark-long-cachetag-shared-slot-{slot:02d}-global-edge"
    if kind == "unique":
        return f"bench-default-unique-object-{obj:010d}-slot-{slot:02d}"
    return f"bench-default-shared-slot-{slot:02d}-global"


def records_for(scenario: str, objects: int, tags_per_object: int, tag_length_class: str = "default") -> list[FixtureRecord]:
    rows: list[FixtureRecord] = []
    shared_universe = max(tags_per_object, min(64, max(1, objects // 8)))
    for obj in range(objects):
        if scenario == "mostly-unique-bound":
            tags = tuple(bound_tag("unique", obj, slot, tags_per_object, tag_length_class) for slot in range(tags_per_object))
        elif scenario == "mostly-shared-bound":
            tags = tuple(
                bound_tag("shared", obj, slot % shared_universe, tags_per_object, tag_length_class)
                for slot in range(tags_per_object)
            )
        elif scenario == "uniform-cyclic":
            tags = tuple(f"uniform:{(obj + slot) % max(objects, tags_per_object)}" for slot in range(tags_per_object))
        elif scenario == "hot-set":  # one fixed hot tag plus deterministic tail tags.
            tags = ("hot:0",) + tuple(f"hot-tail:{(obj + slot) % shared_universe}" for slot in range(1, tags_per_object))
        else:
            # The ordinary-body lane changes body size, not tag geometry.  Use
            # a declared uniform-cyclic synthetic fixture so its C/X rows have
            # the same versioned work-volume contract as the 2-byte lane.
            tags = tuple(f"uniform:{(obj + slot) % max(objects, tags_per_object)}" for slot in range(tags_per_object))
        rows.append(FixtureRecord(f"object:{obj:08d}", tags))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--objects", type=int, default=1000)
    parser.add_argument("--tags-per-object", type=int, default=4)
    parser.add_argument("--tag-length-class", choices=("short", "default", "long"), default="default")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.objects <= 0 or args.tags_per_object <= 0:
        raise SystemExit("--objects and --tags-per-object must be positive")
    records = records_for(args.scenario, args.objects, args.tags_per_object, args.tag_length_class)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    payload = args.out_dir / f"{args.scenario}.jsonl"
    payload.write_text("".join(json.dumps({"id": row.object_id, "tags": list(row.tags)}, separators=(",", ":")) + "\n" for row in records), encoding="utf-8")
    metrics = fixture_metrics(records)
    manifest = {
        "schema": SCHEMA_VERSION,
        "fixture": args.scenario,
        "kind": "synthetic-sensitivity" if args.scenario == "ordinary-body-4k" else "synthetic-bound",
        "access_pattern": (
            "uniform-cyclic"
            if args.scenario in {"uniform-cyclic", "ordinary-body-4k"}
            else args.scenario
            if args.scenario == "hot-set"
            else "declared-by-row"
        ),
        "payload_status": "available",
        "payload": payload.name,
        "expected": metrics,
    }
    (args.out_dir / f"{args.scenario}.manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
