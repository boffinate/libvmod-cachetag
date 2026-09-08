#!/usr/bin/env python3
"""Generate deterministic volatile interned-set growth and drain workloads.

The normal benchmark generator owns the comparison matrix.  This focused
generator keeps the expensive final-reference unlink path isolated while the
interned-set header experiment is evaluated.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from generate_cachetag_benchmark_vtc import (
    vinyl_arg,
    write_allocator_environment,
    write_backend,
    write_backend_vcl,
    write_driver,
    write_stats_capture,
)


DEFAULT_FOLDS = (5, 64, 256)
DRAIN_TAG = "intern:lifecycle:drain"
COUNTER_SEGMENT = "CACHETAG.vcl1_tags_intern_lifecycle"


def parse_folds(raw: str) -> tuple[int, ...]:
    try:
        folds = tuple(int(value.strip()) for value in raw.split(",") if value.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--folds must be comma-separated integers") from exc
    if not folds:
        raise argparse.ArgumentTypeError("--folds must not be empty")
    if len(set(folds)) != len(folds):
        raise argparse.ArgumentTypeError("--folds must not repeat a value")
    if any(folds < 2 or folds > 512 for folds in folds):
        raise argparse.ArgumentTypeError("--folds values must be in [2, 512]")
    return folds


def write_phase_marker(f, prefix: str, phase: str, event: str) -> None:
    path = f"/results/phase-markers/{prefix}.{phase}.{event}"
    f.write(
        'shell "mkdir -p /results/phase-markers; '
        f"printf 'time_unix_nano=%s\\nphase={phase}\\nevent={event}\\n' $(date +%s%N) > {path}\"\n"
    )


def write_vsc_flush(f, name: str, expected_objects: int) -> None:
    f.write(f"client {name} {{\n")
    f.write("\ttxreq -url /__bench_objects\n")
    f.write("\trxresp\n")
    f.write("\texpect resp.status == 204\n")
    f.write(f"\texpect resp.http.X-Bench-Objects == {expected_objects}\n")
    f.write("} -run\n\n")


def write_expectations(
    f,
    objects: int,
    folds: int,
    sets: int,
    refs: int,
    hits: int,
    misses: int,
    expect_timing: bool,
    set_header_bytes: int,
    drained: bool,
) -> None:
    values = {
        "volatile_objects": 0 if drained else objects,
        "volatile_edges": 0 if drained else objects * folds,
        "volatile_interned_sets": 0 if drained else sets,
        "volatile_interned_set_refs": 0 if drained else refs,
        "volatile_interned_set_bytes": 0 if drained else sets * (set_header_bytes + 8 * folds),
    }
    for counter, value in values.items():
        f.write(f"vinyl v1 -expect {COUNTER_SEGMENT}.{counter} == {value}\n")
    if not drained:
        f.write(f"vinyl v1 -expect {COUNTER_SEGMENT}.volatile_interned_set_hits == {hits}\n")
        f.write(f"vinyl v1 -expect {COUNTER_SEGMENT}.volatile_interned_set_misses == {misses}\n")
        if expect_timing:
            f.write(f"vinyl v1 -expect {COUNTER_SEGMENT}.volatile_interned_table_grow_calls > 0\n")
    f.write("\n")


def write_stage(
    f,
    prefix: str,
    objects: int,
    folds: int,
    profile: str,
    driver_tags: int,
    driver_command: str,
    clients: int,
    sets: int,
    refs: int,
    hits: int,
    misses: int,
    expect_timing: bool,
    set_header_bytes: int,
) -> None:
    stage = f"{prefix}_folds_{folds}" if profile == "low-fanout-unique" else f"{prefix}_shared"
    write_driver(
        f, objects, "cachetag-load", profile, driver_tags, f"{stage}_load", driver_command,
        purge_key=DRAIN_TAG,
        env=(
            f"BENCH_CLIENTS={clients} "
            "BENCH_VALIDATE_TAG_SHAPE=1 "
            "BENCH_PHASE_MARKER_DIR=/results/phase-markers "
            f"BENCH_PHASE_MARKER_PREFIX={stage}_load"
        ),
    )
    write_vsc_flush(f, f"c_loaded_{folds if profile == 'low-fanout-unique' else 'shared'}", objects)
    write_expectations(f, objects, folds, sets, refs, hits, misses, expect_timing, set_header_bytes, drained=False)
    write_stats_capture(f, "v1", "CACHETAG.* -f MAIN.*", f"/results/{stage}_loaded.stats")
    write_phase_marker(f, stage, "drain", "start")
    f.write(f"client c_drain_{folds if profile == 'low-fanout-unique' else 'shared'} {{\n")
    f.write(f'\ttxreq -req PURGE -hdr "Key: {DRAIN_TAG}"\n')
    f.write("\trxresp\n")
    f.write("\texpect resp.status == 200\n")
    f.write("\texpect resp.http.Purged == -1\n")
    f.write('\ttxreq -req COMPACT -url "/__bench_compact"\n')
    f.write("\trxresp\n")
    f.write("\texpect resp.status == 200\n")
    f.write(f"\texpect resp.http.Compacted == {objects}\n")
    f.write("} -run\n\n")
    write_phase_marker(f, stage, "drain", "end")
    write_vsc_flush(f, f"c_drained_{folds if profile == 'low-fanout-unique' else 'shared'}", 0)
    write_expectations(f, objects, folds, 0, 0, hits, misses, False, set_header_bytes, drained=True)
    write_stats_capture(f, "v1", "CACHETAG.* -f MAIN.*", f"/results/{stage}_drained.stats")


def write_stage_restart(f) -> None:
    # Compact frees sets but retains the grown bucket table. Restarting the
    # volatile child is the only way to make each fold width prove its own
    # growth path instead of inheriting the first stage's capacity.
    f.write("vinyl v1 -stop\n")
    f.write('shell "vinyladm -n ${v1_name} panic.clear || true"\n')
    f.write("vinyl v1 -start\n\n")


def render_workload(
    objects: int,
    folds: tuple[int, ...],
    storage: str,
    vinyl_threads: int,
    vinyl_thread_pools: int,
    driver_command: str,
    backend_command: str,
    backend_host: str,
    backend_port: int,
    backend_body_bytes: int,
    clients: int,
    set_header_bytes: int,
    expect_timing: bool,
) -> str:
    prefix = "cachetag_intern_lifecycle"
    out: list[str] = []

    class Writer:
        def write(self, text: str) -> None:
            out.append(text)

    f = Writer()
    f.write(f'vtest "volatile intern lifecycle: {objects} unique and shared interned sets"\n\n')
    write_allocator_environment(f)
    write_backend(f, backend_body_bytes, backend_command, backend_host, backend_port)
    f.write(
        f'vinyl v1 -arg "{vinyl_arg(storage, vinyl_threads, "-p vmod_path=${pwd}/.libs:")}" -vcl {{\n'
    )
    write_backend_vcl(f, backend_host, backend_port)
    f.write("\timport cachetag;\n\n")
    f.write("\tsub vcl_init {\n")
    f.write('\t\tnew tags = cachetag.namespace("intern_lifecycle", interning = true, sweep_interval = 0s);\n')
    f.write("\t}\n\n")
    f.write("\tsub vcl_recv {\n")
    f.write('\t\tif (req.url == "/__bench_objects") {\n')
    f.write("\t\t\tset req.http.X-Bench-Objects = tags.objects();\n")
    f.write("\t\t\treturn (synth(204));\n")
    f.write("\t\t}\n")
    f.write('\t\tif (req.url == "/__bench_sync") {\n')
    f.write("\t\t\tset req.http.X-Bench-Sync = tags.pending();\n")
    f.write("\t\t\treturn (synth(204));\n")
    f.write("\t\t}\n")
    f.write('\t\tif (req.method == "PURGE") {\n')
    f.write("\t\t\tset req.http.Purged = tags.purge(req.http.Key, mode = hard);\n")
    f.write("\t\t\treturn (synth(200));\n")
    f.write("\t\t}\n")
    f.write('\t\tif (req.method == "COMPACT" && req.url == "/__bench_compact") {\n')
    f.write("\t\t\tset req.http.Compacted = tags.compact();\n")
    f.write("\t\t\treturn (synth(200));\n")
    f.write("\t\t}\n")
    f.write("\t}\n\n")
    f.write("\tsub vcl_backend_response {\n")
    f.write('\t\ttags.add_header(bereq.http.X-Cache-Tags, sep = " ");\n')
    f.write(f'\t\ttags.add("{DRAIN_TAG}");\n')
    f.write("\t\tset beresp.ttl = 1h;\n")
    f.write("\t\tset beresp.grace = 0s;\n")
    f.write("\t\tset beresp.keep = 0s;\n")
    f.write("\t}\n\n")
    f.write("\tsub vcl_hit {\n")
    f.write("\t\tif (tags.stale()) { return (restart); }\n")
    f.write('\t\tset req.http.X-Bench-Cache = "hit";\n')
    f.write("\t}\n\n")
    f.write("\tsub vcl_miss {\n")
    f.write('\t\tif (req.http.X-Bench-Resident-Probe == "1") { return (synth(503)); }\n')
    f.write("\t}\n\n")
    f.write("\tsub vcl_deliver {\n")
    f.write('\t\tif (req.http.X-Bench-Cache == "hit") {\n')
    f.write('\t\t\tset resp.http.X-Bench-Cache = "hit";\n')
    f.write("\t\t} else {\n")
    f.write('\t\t\tset resp.http.X-Bench-Cache = "miss";\n')
    f.write("\t\t}\n")
    f.write("\t\tunset req.http.X-Bench-Cache;\n")
    f.write("\t}\n\n")
    f.write("\tsub vcl_synth {\n")
    f.write("\t\tset resp.http.X-Bench-Objects = req.http.X-Bench-Objects;\n")
    f.write("\t\tset resp.http.X-Bench-Sync = req.http.X-Bench-Sync;\n")
    f.write("\t\tset resp.http.Purged = req.http.Purged;\n")
    f.write("\t\tset resp.http.Compacted = req.http.Compacted;\n")
    f.write("\t\treturn (deliver);\n")
    f.write("\t}\n")
    f.write("} -start\n\n")
    for stage_folds in folds:
        write_stage(
            f, prefix, objects, stage_folds, "low-fanout-unique", stage_folds - 1,
            driver_command, clients, objects, objects, 0, objects, expect_timing, set_header_bytes,
        )
        write_stage_restart(f)
    # The driver's five shared tags plus the fixed drain tag make one six-fold
    # set. This separates refcount decrements from the high-cardinality unlink
    # stages without changing the driver or treating a cache hit as a load.
    write_stage(
        f, prefix, objects, 6, "interning-shared-five", 5, driver_command, clients,
        1, objects, objects - 1, 1, False, set_header_bytes,
    )
    write_vsc_flush(f, "c_post", 0)
    write_stats_capture(f, "v1", "CACHETAG.* -f MAIN.*", f"/results/{prefix}_post.stats")
    f.write("vinyl v1 -expect n_lru_nuked == 0\n")
    f.write("vinyl v1 -stop\n")
    f.write('shell "vinyladm -n ${v1_name} panic.clear || true"\n')
    f.write("process p_backend -stop\n")
    return "".join(out)


def write_manifest(out_dir: Path, path: Path, objects: int, folds: tuple[int, ...], set_header_bytes: int, expect_timing: bool) -> None:
    material = f"{path.name}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\0"
    (out_dir / "intern-lifecycle.env").write_text(
        "".join(
            (
                "schema=intern-lifecycle-v1\n",
                "workload_class=development-screen\n",
                "storage_kind=default\n",
                "persistence=0\n",
                "membership_mode=interned\n",
                f"objects={objects}\n",
                f"folds={','.join(str(fold) for fold in folds)}\n",
                "shared_block_folds=6\n",
                f"drain_tag={DRAIN_TAG}\n",
                "driver_profile=low-fanout-unique\n",
                "driver_tags_per_object=folds_minus_one\n",
                f"set_header_bytes={set_header_bytes}\n",
                f"expect_benchmark_timing={int(expect_timing)}\n",
                "excluded_storage=fellow\n",
                "excluded_behavior=concurrent_warm_hits_during_drain\n",
                f"rendered_workloads_sha256={hashlib.sha256(material.encode('ascii')).hexdigest()}\n",
                f"generator_sha256={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}\n",
            )
        ),
        encoding="ascii",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--objects", type=int, default=100000)
    parser.add_argument("--folds", type=parse_folds, default=DEFAULT_FOLDS)
    parser.add_argument("--storage", default="2G")
    parser.add_argument("--vinyl-thread-pool-max", type=int, default=16)
    parser.add_argument("--vinyl-thread-pools", type=int, default=2)
    parser.add_argument("--clients", type=int, default=1)
    parser.add_argument("--set-header-bytes", choices=(24, 32), type=int, default=24)
    parser.add_argument("--expect-benchmark-timing", action="store_true")
    parser.add_argument("--driver-command", default="/work/cachetag-http-workload-driver")
    parser.add_argument("--backend-command", default="/work/cachetag-benchmark-backend")
    parser.add_argument("--backend-host", default="127.0.0.1")
    parser.add_argument("--backend-port", type=int, default=18080)
    parser.add_argument("--backend-body-bytes", type=int, default=2)
    args = parser.parse_args()
    if args.objects <= 0:
        parser.error("--objects must be positive")
    if not args.storage:
        parser.error("--storage must not be empty")
    if args.vinyl_thread_pool_max <= 0 or args.vinyl_thread_pools <= 0:
        parser.error("Vinyl thread counts must be positive")
    if args.clients <= 0:
        parser.error("--clients must be positive")
    if not args.driver_command or not args.backend_command or not args.backend_host:
        parser.error("driver, backend command, and backend host must not be empty")
    if args.backend_port <= 0 or args.backend_body_bytes < 0:
        parser.error("backend port must be positive and body bytes non-negative")

    os.environ["BENCH_VINYL_THREAD_POOLS"] = str(args.vinyl_thread_pools)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / "cachetag_intern_lifecycle.vtc"
    path.write_text(
        render_workload(
            args.objects,
            args.folds,
            args.storage,
            args.vinyl_thread_pool_max,
            args.vinyl_thread_pools,
            args.driver_command,
            args.backend_command,
            args.backend_host,
            args.backend_port,
            args.backend_body_bytes,
            args.clients,
            args.set_header_bytes,
            args.expect_benchmark_timing,
        ),
        encoding="ascii",
    )
    write_manifest(args.out_dir, path, args.objects, args.folds, args.set_header_bytes, args.expect_benchmark_timing)


if __name__ == "__main__":
    main()
