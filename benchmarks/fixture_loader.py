#!/usr/bin/env python3
"""Versioned, deterministic benchmark-fixture validation.

The production CMS source trace is deliberately not bundled with this
repository.  This module makes that absence explicit: a fixture whose manifest
has no public payload or expected fingerprint cannot be loaded for a measured
row.  It is not acceptable to replace it with a synthetic approximation while
retaining the ``cms-trace-static-v1`` name.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = "benchmark-fixture-v1"


class FixtureError(ValueError):
    """A fixture is incomplete, malformed, or does not match its manifest."""


@dataclass(frozen=True)
class FixtureRecord:
    object_id: str
    tags: tuple[str, ...]

    @property
    def canonical_tags(self) -> str:
        return " ".join(self.tags)


def canonicalize_tags(tags: object) -> tuple[str, ...]:
    if not isinstance(tags, list) or not tags:
        raise FixtureError("tags must be a non-empty JSON array")
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str) or not tag or tag != tag.strip() or any(c.isspace() for c in tag):
            raise FixtureError("each tag must be a non-empty whitespace-free string")
        if tag in seen:
            raise FixtureError(f"duplicate tag: {tag}")
        seen.add(tag)
        normalized.append(tag)
    # The input order is canonical and is therefore part of the stored-header
    # byte contract.  Do not sort it: xkey receives this exact value.
    return tuple(normalized)


def parse_record(raw: object) -> FixtureRecord:
    if not isinstance(raw, dict) or set(raw) != {"id", "tags"}:
        raise FixtureError("each record must contain exactly id and tags")
    object_id = raw["id"]
    if not isinstance(object_id, str) or not object_id or any(c.isspace() for c in object_id):
        raise FixtureError("id must be a non-empty whitespace-free string")
    return FixtureRecord(object_id, canonicalize_tags(raw["tags"]))


def fixture_fingerprint(records: Iterable[FixtureRecord]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(record.object_id.encode("utf-8"))
        digest.update(b"\t")
        digest.update(record.canonical_tags.encode("utf-8"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def ordered_value_fingerprint(records: Iterable[FixtureRecord]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(record.object_id.encode("utf-8"))
        digest.update(b"\t")
        digest.update(record.canonical_tags.encode("utf-8"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def load_jsonl(path: Path) -> list[FixtureRecord]:
    records: list[FixtureRecord] = []
    ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            raise FixtureError(f"{path}:{line_number}: blank records are not allowed")
        try:
            record = parse_record(json.loads(line))
        except json.JSONDecodeError as exc:
            raise FixtureError(f"{path}:{line_number}: invalid JSON") from exc
        if record.object_id in ids:
            raise FixtureError(f"{path}:{line_number}: duplicate id {record.object_id}")
        ids.add(record.object_id)
        records.append(record)
    if not records:
        raise FixtureError(f"{path}: no records")
    return records


def fixture_metrics(records: Iterable[FixtureRecord]) -> dict[str, object]:
    materialized = list(records)
    tags = [tag for record in materialized for tag in record.tags]
    tag_universe = set(tags)
    values = [record.canonical_tags for record in materialized]
    fanout = Counter(tags)
    tags_per_object = Counter(len(record.tags) for record in materialized)
    exact_sets = Counter(record.canonical_tags for record in materialized)
    fanout_histogram = Counter(fanout.values())
    set_reuse_histogram = Counter(exact_sets.values())
    return {
        "objects": len(materialized),
        "relationships": len(tags),
        "tag_universe": len(tag_universe),
        "max_tags_per_object": max(len(record.tags) for record in materialized),
        "max_tag_bytes": max(len(tag.encode("utf-8")) for tag in tags),
        "max_serialized_tag_value_bytes": max(len(value.encode("utf-8")) for value in values),
        "header_value_bytes": sum(len(value.encode("utf-8")) for value in values),
        "max_fanout": max(fanout.values()),
        "tags_per_object_histogram": {str(key): tags_per_object[key] for key in sorted(tags_per_object)},
        "fanout_histogram": {str(key): fanout_histogram[key] for key in sorted(fanout_histogram)},
        "exact_tag_sets": len(exact_sets),
        "exact_set_reuse_histogram": {str(key): set_reuse_histogram[key] for key in sorted(set_reuse_histogram)},
        "fixture_fingerprint": fixture_fingerprint(materialized),
        "ordered_value_fingerprint": ordered_value_fingerprint(materialized),
    }


def load_manifest(path: Path) -> dict[str, object]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"cannot read manifest {path}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA_VERSION:
        raise FixtureError(f"{path}: unsupported fixture schema")
    return manifest


def validate_fixture(manifest_path: Path, payload_path: Path | None = None) -> dict[str, object]:
    manifest = load_manifest(manifest_path)
    fixture_name = manifest.get("fixture")
    expected = manifest.get("expected")
    if not isinstance(fixture_name, str):
        raise FixtureError(f"{manifest_path}: missing fixture name")
    if manifest.get("payload_status") != "available":
        reason = manifest.get("payload_blocker", "payload unavailable")
        raise FixtureError(f"{fixture_name}: fixture is not executable: {reason}")
    if not isinstance(expected, dict):
        raise FixtureError(f"{manifest_path}: missing expected section")
    if payload_path is None:
        relative = manifest.get("payload")
        if not isinstance(relative, str):
            raise FixtureError(f"{fixture_name}: payload path missing")
        payload_path = manifest_path.parent / relative
    records = load_jsonl(payload_path)
    metrics = fixture_metrics(records)
    for key, expected_value in expected.items():
        if key not in metrics:
            continue
        if metrics[key] != expected_value:
            raise FixtureError(
                f"{fixture_name}: {key} mismatch: got {metrics[key]!r}, expected {expected_value!r}"
            )
    return metrics
