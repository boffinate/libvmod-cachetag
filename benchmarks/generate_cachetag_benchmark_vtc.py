#!/usr/bin/env python3
"""Generate varnishtest workloads for cachetag VMOD performance baselines."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


PHASED_PURGE_PROFILES = (
    "uniform-tags",
    "zipfian-tags",
    "cms-entity-list",
    "extreme-high-fanout",
    "low-fanout-unique",
    "explicit-purge",
    "single-shared-tag",
    "single-unique-tag",
    "ten-unique-tags",
    "five-unique-five-shared",
    "cutover-mostly-unique",
    "cutover-mostly-shared",
    "cutover-mixed",
)
SPECIAL_PROFILES = (
    "untagged-fellow-load",
    "short-ttl-high-churn",
    "rotating-tag-churn",
    "rotating-tag-churn-deterministic-full",
    "rotating-tag-churn-deterministic-incremental",
    "bulk-purge-bursts",
    "concurrent",
    "purge-storm",
    "purged-cold-residency",
    "populated-map-warm",
    "stream1-checkpoint-overlap",
    "phase4-sweep-latency",
    "phase4-refill-control",
    "phase5-held-short",
    "phase5-held-multi",
    "phase5-held-cap",
    "phase5-held-shutdown",
    "phase5-nohold-short",
    "phase5-nohold-multi",
    "phase5-nohold-cap",
    "phase6-fill-drain",
    "eviction",
)
RESTART_PROFILES = (
    "fellow-restart-idle-memory",
    "fellow-restart-first-touch",
    "fellow-restart-cold-purge",
    "fellow-restart-hot-purge",
)
ALL_PROFILES = (*PHASED_PURGE_PROFILES, *SPECIAL_PROFILES)
FIXED_TAGS_PER_OBJECT = {
    "single-shared-tag": 1,
    "single-unique-tag": 1,
    "ten-unique-tags": 10,
    "five-unique-five-shared": 10,
}


def profile_title(profile: str) -> str:
    return profile.replace("-", " ")


def profile_slug(profile: str) -> str:
    return profile.replace("-", "_")


def implementation_slug(implementation: str) -> str:
    return implementation.replace("-", "_")


def env_flag(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes", "on"}


def fixed_tags_per_object(profile: str, configured: int) -> int:
    return FIXED_TAGS_PER_OBJECT.get(profile, configured)


def tag_length_class() -> str:
    value = os.getenv("BENCH_TAG_LENGTH_CLASS", "default")
    if value not in {"short", "default", "long"}:
        raise SystemExit("BENCH_TAG_LENGTH_CLASS must be short, default, or long")
    return value


def cutover_tag(kind: str, obj: int, slot: int, tags_per_object: int = 20) -> str:
    length_class = tag_length_class()
    if length_class == "short":
        if kind == "unique":
            return f"u{base36(obj * max(tags_per_object, 1) + slot)}"
        if kind == "shared":
            return f"s{slot:x}"
        unique_per_object = max(tags_per_object // 2, 1)
        return f"m{base36(obj * unique_per_object + slot)}"
    if length_class == "long":
        if kind == "unique":
            return f"benchmark-long-cachetag-unique-object-{obj:010d}-slot-{slot:02d}-edge"
        if kind == "shared":
            return f"benchmark-long-cachetag-shared-slot-{slot:02d}-global-edge"
        return f"benchmark-long-cachetag-mixed-object-{obj:010d}-slot-{slot:02d}-edge"
    if kind == "unique":
        return f"bench-default-unique-object-{obj:010d}-slot-{slot:02d}"
    if kind == "shared":
        return f"bench-default-shared-slot-{slot:02d}-global"
    return f"bench-default-mixed-object-{obj:010d}-slot-{slot:02d}"


def base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    parts = []
    while value > 0:
        value, rem = divmod(value, 36)
        parts.append(digits[rem])
    return "".join(reversed(parts))


def profile_ttl(profile: str, override: str = "") -> str:
    if override:
        return override
    if profile in {"short-ttl-high-churn", "rotating-tag-churn", "rotating-tag-churn-deterministic-full"}:
        return "1s"
    if profile == "rotating-tag-churn-deterministic-incremental":
        return "5s"
    return "1h"


def selected_profiles(raw: str) -> tuple[str, ...]:
    if raw == "all":
        return ALL_PROFILES
    profiles = tuple(profile.strip() for profile in raw.split(",") if profile.strip())
    if not profiles:
        raise SystemExit("--profile must not be empty")
    invalid = [profile for profile in profiles if profile not in ALL_PROFILES]
    if invalid:
        invalid = [profile for profile in profiles if profile not in (*ALL_PROFILES, *RESTART_PROFILES)]
    if invalid:
        valid = ", ".join((*ALL_PROFILES, *RESTART_PROFILES, "all"))
        raise SystemExit(f"unknown --profile value(s): {', '.join(invalid)}; valid values: {valid}")
    return profiles


def vinyl_arg(
    storage: str,
    vinyl_threads: int,
    extra: str = "",
    *,
    storage_kind: str = "default",
    fellow_size: str = "1GB",
    fellow_segment_size: str = "1MB",
    fellow_block_size: str = "64KB",
    fellow_stv_path: str = "${tmpdir}/bench-fellow.stv",
    buddy_size: str = "1GB",
    slash_vmod_path: str = "",
    timeout_idle: str = "",
    backend_idle_timeout: str = "",
) -> str:
    # Vinyl's thread_pool_max and thread_pool_min are *per pool*. Keep the
    # number of pools explicit as well so a client sweep has a stable server
    # worker envelope rather than inheriting Vinyl's default.
    thread_pools = int(os.getenv("BENCH_VINYL_THREAD_POOLS", "2"))
    if thread_pools <= 0:
        raise SystemExit("BENCH_VINYL_THREAD_POOLS must be positive")
    thread_min = min(vinyl_threads, 10)
    if storage_kind == "fellow":
        storage_arg = (
            f"-sfellow=fellow,{fellow_stv_path},{fellow_size},"
            f"{fellow_segment_size},{fellow_block_size}"
        )
    elif storage_kind == "buddy":
        storage_arg = f"-sbuddy=buddy,{buddy_size}"
    else:
        storage_arg = f"-sdefault,{storage}"
    parts = [
        storage_arg,
        # vinyltest starts vinyld with debug=+vtc_mode, which is useful for
        # tests but throttles backend fetch throughput enough to dominate
        # benchmark results.
        "-p debug=none",
        f"-p thread_pools={thread_pools}",
        f"-p thread_pool_min={thread_min}",
        f"-p thread_pool_max={vinyl_threads}",
    ]
    if storage_kind == "fellow":
        parts.append("-jnone")
    if storage_kind in {"fellow", "buddy"}:
        parts.append(f"-E{slash_vmod_path}")
    if timeout_idle:
        parts.append(f"-p timeout_idle={timeout_idle}")
    if backend_idle_timeout:
        parts.append(f"-p backend_idle_timeout={backend_idle_timeout}")
    if extra:
        parts.append(extra)
    return " ".join(parts)


def write_slash_import(f, storage_kind: str, slash_vmod_path: str) -> None:
    if storage_kind in {"fellow", "buddy"}:
        f.write(f'\timport slash from "{slash_vmod_path}";\n')


def write_slash_tuning(f, storage_kind: str, buddy_reserve_chunks: int) -> None:
    if storage_kind == "fellow":
        f.write("\t\tslash.tune_fellow(storage.fellow);\n")
    elif storage_kind == "buddy":
        f.write(f"\t\tslash.tune_buddy(storage.buddy, reserve_chunks = {buddy_reserve_chunks});\n")


def write_backend(f, body_bytes: int, backend_command: str, backend_host: str, backend_port: int) -> None:
    f.write(
        "process p_backend "
        f"\"{backend_command} {backend_host} {backend_port} {body_bytes}\" -start\n"
    )
    f.write('process p_backend -expect-text 0 0 "ready"\n\n')


def write_backend_vcl(f, backend_host: str, backend_port: int) -> None:
    f.write(f'\tbackend default {{ .host = "{backend_host}"; .port = "{backend_port}"; }}\n\n')


def write_allocator_environment(f) -> None:
    malloc_conf = os.getenv("BENCH_MALLOC_CONF", "")
    malloc_arena_max = os.getenv("BENCH_MALLOC_ARENA_MAX", "")
    malloc_trim_threshold = os.getenv("BENCH_MALLOC_TRIM_THRESHOLD_", "")
    if malloc_conf:
        f.write(f'setenv MALLOC_CONF "abort:true,junk:true,{malloc_conf}"\n')
    if malloc_arena_max:
        f.write(f'setenv MALLOC_ARENA_MAX "{malloc_arena_max}"\n')
    if malloc_trim_threshold:
        f.write(f'setenv MALLOC_TRIM_THRESHOLD_ "{malloc_trim_threshold}"\n')
    if malloc_conf or malloc_arena_max or malloc_trim_threshold:
        f.write("\n")


def default_purge_key(profile: str) -> str:
    return {
        "uniform-tags": "u:0",
        "zipfian-tags": "z:1",
        "cms-entity-list": "list:frontpage",
        "extreme-high-fanout": "site",
        "low-fanout-unique": "group:0",
        "explicit-purge": "site",
        "single-shared-tag": "hot:global",
        "single-unique-tag": "u0:0",
        "ten-unique-tags": "u0:0",
        "five-unique-five-shared": "shared:0",
        "cutover-mostly-unique": cutover_tag("unique", 0, 0),
        "cutover-mostly-shared": cutover_tag("shared", 0, 0),
        "cutover-mixed": cutover_tag("shared", 0, 0),
        "bulk-purge-bursts": "bucket:0",
        "concurrent": "site",
        "purge-storm": "site",
        "purged-cold-residency": "site",
        "populated-map-warm": "site",
        "phase4-sweep-latency": "site",
        "phase4-refill-control": "site",
        "phase5-held-short": "site",
        "phase5-held-multi": "site",
        "phase5-held-cap": "site",
        "phase5-held-shutdown": "site",
        "phase5-nohold-short": "site",
        "phase5-nohold-multi": "site",
        "phase5-nohold-cap": "site",
        "phase6-fill-drain": "phase6:full:0",
    }.get(profile, "site")


def write_driver(
    f,
    objects: int,
    mode: str,
    profile: str,
    tags_per_object: int,
    name: str,
    driver_command: str,
    purge_key: str | None = None,
    env: str = "",
    wait: bool = True,
) -> None:
    key = purge_key if purge_key is not None else default_purge_key(profile)
    env_parts = []
    length_class = os.getenv("BENCH_TAG_LENGTH_CLASS", "")
    if length_class:
        env_parts.append(f"BENCH_TAG_LENGTH_CLASS={length_class}")
    validate_tag_shape = os.getenv("BENCH_VALIDATE_TAG_SHAPE", "")
    if validate_tag_shape:
        env_parts.append(f"BENCH_VALIDATE_TAG_SHAPE={validate_tag_shape}")
    # Every driver invocation writes phase boundaries. The metrics sampler
    # consumes these markers to retain phase-aligned process CPU deltas.
    if "BENCH_PHASE_MARKER_DIR=" not in env:
        env_parts.extend(
            (
                "BENCH_PHASE_MARKER_DIR=/results/phase-markers",
                f"BENCH_PHASE_MARKER_PREFIX={name}",
            )
        )
    if env:
        env_parts.append(env)
    env = " ".join(env_parts)
    command_prefix = f"{env} " if env else ""
    action = "run" if wait else "start"
    f.write(
        "process p1 -log "
        f"\"{command_prefix}{driver_command} ${{v1_addr}} ${{v1_port}} {objects} {mode} "
        f"{profile} {tags_per_object} {key} /results/{name}.driver\" -{action}\n\n"
    )


def write_phase4_marker_snapshot(
    f, prefix: str, marker: str, stats_filter: str, stats_suffix: str, event: str = "end"
) -> None:
    marker_path = f"/results/phase-markers/{prefix}.{marker}.{event}"
    f.write(
        f'shell "i=0; while [ $i -lt 360000 ]; do test -f {marker_path} && exit 0; '
        'i=$((i + 1)); sleep 0.01; done; echo timed out waiting for Phase 4 marker; exit 1"\n'
    )
    write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_{stats_suffix}.stats")


def write_phase5_marker_snapshot(
    f, prefix: str, marker: str, stats_filter: str, stats_suffix: str, event: str = "end"
) -> None:
    marker_path = f"/results/phase-markers/{prefix}.{marker}.{event}"
    f.write(
        f'shell "i=0; while [ $i -lt 360000 ]; do test -f {marker_path} && exit 0; '
        'i=$((i + 1)); sleep 0.01; done; echo timed out waiting for Phase 5 marker; exit 1"\n'
    )
    write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_{stats_suffix}.stats")


def write_phase6_cycle_snapshot(
    f, prefix: str, cycle: int, stats_filter: str, require_tripwire: bool = True
) -> None:
    marker = f"phase6_cycle_{cycle:02d}"
    marker_path = f"/results/phase-markers/{prefix}.{marker}.end"
    snapshot_path = f"/results/phase-markers/{prefix}.{marker}.snapshot"
    stats_path = f"/results/{prefix}_{marker}_end.stats"
    memory_path = f"/results/{prefix}_{marker}_end.phase6_memory"
    f.write(
        f'shell "i=0; while [ $i -lt 360000 ]; do test -f {marker_path} && exit 0; '
        'i=$((i + 1)); sleep 0.01; done; '
        'echo timed out waiting for Phase 6 cycle marker; exit 1"\n'
    )
    write_stats_capture(f, "v1", f"{stats_filter} -f SMA.* -f MEMPOOL.*", stats_path)
    tripwire_required = require_tripwire and cycle in {1, 8}
    f.write(
        f'shell "sh /cachetag-host/benchmarks/capture_phase6_memory.sh '
        f'{cycle:02d} {stats_path} {memory_path} {snapshot_path} '
        f'{1 if tripwire_required else 0}"\n'
    )
    return


def write_client_purge(
    f,
    name: str,
    profile: str,
    purge_key: str | None = None,
) -> None:
    key = purge_key if purge_key is not None else default_purge_key(profile)
    f.write(f"client {name} {{\n")
    f.write(f'\ttxreq -req PURGE -hdr "Key: {key}"\n')
    f.write("\trxresp\n")
    f.write("\texpect resp.status == 200\n")
    f.write("\texpect resp.http.Purged == -1\n")
    f.write("} -run\n\n")


def write_shutdown_drain(f, seconds: float) -> None:
    if seconds > 0:
        f.write(f"delay {seconds:g}\n")


def write_fellow_cachetag_vcl_unload(f) -> None:
    f.write(
        'shell "i=0; while [ $i -lt 180 ]; do set -- $(vinylstat -1 -n ${v1_name} '
        "-f MAIN.exp_mailed -f MAIN.exp_received | awk "
        "'/MAIN.exp_mailed/{m=$2} /MAIN.exp_received/{r=$2} END{print m, r}'); "
        'if [ \\"$1\\" = \\"$2\\" ]; then exit 0; fi; i=$((i + 1)); sleep 1; '
        'done; echo expiry backlog not drained: mailed=$1 received=$2; exit 1"\n\n'
    )
    f.write("vinyl v1 -vcl {\n\tbackend none none;\n}\n\n")
    f.write('vinyl v1 -cliok "vcl.discard vcl1"\n\n')


def write_benchmark_teardown(
    f, shutdown_drain_seconds: float, expect_fellow_close_panic: bool = False
) -> None:
    write_shutdown_drain(f, shutdown_drain_seconds)
    if expect_fellow_close_panic:
        f.write("vinyl v1 -expectexit 0x40\n")
    f.write("vinyl v1 -stop\n")
    f.write('shell "vinyladm -n ${v1_name} panic.clear || true"\n')
    f.write("process p_backend -stop\n")


def write_stats_capture(f, instance: str, stats_filter: str, path: str) -> None:
    f.write(
        f'shell "vinylstat -1 -n ${{{instance}_name}} -f {stats_filter} '
        f'-f MAIN.n_object -f MAIN.n_lru_nuked > {path}"\n'
    )


def storage_stats_filter(base_filter: str, storage_kind: str) -> str:
    if storage_kind == "buddy":
        return f"{base_filter} -f BUDDY.*"
    if storage_kind == "fellow":
        return f"{base_filter} -f FELLOW.*"
    return base_filter


def is_phase5_profile(profile: str) -> bool:
    return profile in {
        "phase5-held-short",
        "phase5-held-multi",
        "phase5-held-cap",
        "phase5-held-shutdown",
        "phase5-nohold-short",
        "phase5-nohold-multi",
        "phase5-nohold-cap",
    }


def is_phase5_held_profile(profile: str) -> bool:
    return profile in {
        "phase5-held-short",
        "phase5-held-multi",
        "phase5-held-cap",
        "phase5-held-shutdown",
    }


def is_phase5_shutdown_profile(profile: str) -> bool:
    return profile == "phase5-held-shutdown"


def is_phase5_cap_profile(profile: str) -> bool:
    return profile in {"phase5-held-cap", "phase5-nohold-cap"}


def is_phase6_profile(profile: str) -> bool:
    return profile == "phase6-fill-drain"


def write_cachetag_vcl(
    f,
    implementation: str,
    profile: str,
    storage: str,
    ttl: str,
    vinyl_threads: int,
    backend_host: str,
    backend_port: int,
    storage_kind: str,
    fellow_size: str,
    fellow_segment_size: str,
    fellow_block_size: str,
    buddy_size: str,
    buddy_reserve_chunks: int,
    slash_vmod_path: str,
    timeout_idle: str,
    backend_idle_timeout: str,
    cachetag_persist: bool,
    cachetag_wal_fsync: str,
    cachetag_sweep_interval: str | None = None,
) -> None:
    namespace_args = []
    if cachetag_persist:
        namespace_args.append('persist_path = "${tmpdir}/cachetag-bench-persist"')
        namespace_args.append(f"wal_fsync = {cachetag_wal_fsync}")
    purge_history_max_entries = os.getenv("BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES", "")
    if purge_history_max_entries:
        namespace_args.append(f"purge_history_max_entries = {int(purge_history_max_entries)}")
    sweep_interval = os.getenv("BENCH_CACHE_TAG_SWEEP_INTERVAL", "")
    if not sweep_interval and cachetag_sweep_interval:
        sweep_interval = cachetag_sweep_interval
    if sweep_interval:
        namespace_args.append(f"sweep_interval = {sweep_interval}")
    sweep_batch_objects = os.getenv("BENCH_CACHE_TAG_SWEEP_BATCH_OBJECTS", "")
    if sweep_batch_objects:
        namespace_args.append(f"sweep_batch_objects = {int(sweep_batch_objects)}")
    sweep_batch_hold = os.getenv("BENCH_CACHE_TAG_SWEEP_BATCH_HOLD", "")
    if sweep_batch_hold:
        namespace_args.append(f"sweep_batch_hold = {sweep_batch_hold}")
    sweep_batch_yield = os.getenv("BENCH_CACHE_TAG_SWEEP_BATCH_YIELD", "")
    if sweep_batch_yield:
        namespace_args.append(f"sweep_batch_yield = {sweep_batch_yield}")
    namespace_arg_string = ""
    if namespace_args:
        namespace_arg_string = ", " + ", ".join(namespace_args)
    vmod_path = "${pwd}/.libs:"
    if is_phase5_held_profile(profile):
        vmod_path = "${pwd}/.libs:/work/prefix/lib/vinyl-cache/vmods:"
    f.write(
        f'vinyl v1 -arg "{vinyl_arg(storage, vinyl_threads, f"-p vmod_path={vmod_path}", storage_kind=storage_kind, fellow_size=fellow_size, fellow_segment_size=fellow_segment_size, fellow_block_size=fellow_block_size, buddy_size=buddy_size, slash_vmod_path=slash_vmod_path, timeout_idle=timeout_idle, backend_idle_timeout=backend_idle_timeout)}" -vcl {{\n'
    )
    write_backend_vcl(f, backend_host, backend_port)
    write_slash_import(f, storage_kind, slash_vmod_path)
    f.write("\timport cachetag;\n\n")
    if is_phase5_held_profile(profile):
        f.write("\timport vtc;\n\n")
    f.write("\tsub vcl_init {\n")
    write_slash_tuning(f, storage_kind, buddy_reserve_chunks)
    f.write(f'\t\tnew tags = cachetag.namespace("bench"{namespace_arg_string});\n')
    f.write("\t}\n\n")
    f.write("\tsub vcl_recv {\n")
    f.write('\t\tif (req.url == "/__bench_sync") {\n')
    f.write("\t\t\tset req.http.X-Bench-Sync = tags.pending();\n")
    f.write("\t\t\treturn (synth(204));\n")
    f.write("\t\t}\n")
    f.write('\t\tif (req.url == "/__bench_objects") {\n')
    f.write("\t\t\tset req.http.X-Bench-Objects = tags.objects();\n")
    f.write("\t\t\treturn (synth(204));\n")
    f.write("\t\t}\n")
    f.write('\t\tif (req.method == "PURGE") {\n')
    if profile == "bulk-purge-bursts":
        f.write("\t\t\tset req.http.purged =\n")
        f.write('\t\t\t    tags.purge_header(req.http.Key, sep = " ");\n')
    elif implementation.startswith("cachetag"):
        f.write('\t\t\tif (req.http.X-Bench-Purge-Mode == "soft") {\n')
        f.write("\t\t\t\tset req.http.purged =\n")
        f.write("\t\t\t\t    tags.purge(req.http.Key, mode = soft);\n")
        f.write("\t\t\t} else {\n")
        f.write("\t\t\t\tset req.http.purged =\n")
        f.write("\t\t\t\t    tags.purge(req.http.Key, mode = hard);\n")
        f.write("\t\t\t}\n")
    f.write("\t\t\treturn (synth(200));\n")
    f.write("\t\t}\n")
    f.write('\t\tif (req.method == "COMPACT" && req.url == "/__bench_compact") {\n')
    f.write("\t\t\tset req.http.compacted = tags.compact();\n")
    f.write("\t\t\treturn (synth(200));\n")
    f.write("\t\t}\n")
    f.write("\t}\n\n")
    f.write("\tsub vcl_backend_response {\n")
    f.write(f"\t\tset beresp.ttl = {ttl};\n")
    if is_phase6_profile(profile):
        f.write('\t\tif (bereq.http.X-Bench-Phase6-TTL == "short") {\n')
        f.write("\t\t\tset beresp.ttl = 1s;\n")
        f.write("\t\t}\n")
        f.write("\t\tset beresp.grace = 0s;\n")
        f.write("\t\tset beresp.keep = 0s;\n")
    if is_phase5_held_profile(profile):
        f.write('\t\tif (bereq.url == "/__bench_phase5_hold") {\n')
        f.write('\t\t\ttags.add("phase5:held");\n')
        f.write('\t\t\tvtc.barrier_sync("${b_phase5_held_sock}");\n')
        f.write('\t\t\tvtc.barrier_sync("${b_phase5_continue_sock}");\n')
        f.write("\t\t}\n")
    if profile != "untagged-fellow-load":
        f.write('\t\ttags.add_header(bereq.http.X-Cache-Tags, sep = " ");\n')
    f.write("\t}\n\n")
    f.write("\tsub vcl_hit {\n")
    if implementation == "cachetag":
        f.write("\t\tif (tags.stale()) {\n")
        f.write("\t\t\treturn (restart);\n")
        f.write("\t\t}\n")
    f.write('\t\tset req.http.X-Bench-Cache = "hit";\n')
    f.write("\t}\n\n")
    f.write("\tsub vcl_deliver {\n")
    f.write("\t\tif (req.http.X-Bench-Cache == \"hit\") {\n")
    f.write('\t\t\tset resp.http.X-Bench-Cache = "hit";\n')
    f.write("\t\t} else {\n")
    f.write('\t\t\tset resp.http.X-Bench-Cache = "miss";\n')
    f.write("\t\t}\n")
    f.write("\t\tunset req.http.X-Bench-Cache;\n")
    f.write("\t}\n\n")
    f.write("\tsub vcl_synth {\n")
    f.write("\t\tset resp.http.Purged = req.http.purged;\n")
    f.write("\t\tset resp.http.Compacted = req.http.compacted;\n")
    f.write("\t\tset resp.http.X-Bench-Sync = req.http.X-Bench-Sync;\n")
    f.write("\t\tset resp.http.X-Bench-Objects = req.http.X-Bench-Objects;\n")
    f.write("\t\treturn (deliver);\n")
    f.write("\t}\n")
    f.write("} -start\n\n")
    f.write('vinyl v1 -cliok "param.set vsl_mask none"\n\n')


def write_xkey_vcl(
    f,
    storage: str,
    ttl: str,
    vinyl_threads: int,
    backend_host: str,
    backend_port: int,
    storage_kind: str,
    fellow_size: str,
    fellow_segment_size: str,
    fellow_block_size: str,
    buddy_size: str,
    buddy_reserve_chunks: int,
    slash_vmod_path: str,
    timeout_idle: str,
    backend_idle_timeout: str,
) -> None:
    f.write(
        f'vinyl v1 -arg "{vinyl_arg(storage, vinyl_threads, storage_kind=storage_kind, fellow_size=fellow_size, fellow_segment_size=fellow_segment_size, fellow_block_size=fellow_block_size, buddy_size=buddy_size, slash_vmod_path=slash_vmod_path, timeout_idle=timeout_idle, backend_idle_timeout=backend_idle_timeout)}" -vcl {{\n'
    )
    write_backend_vcl(f, backend_host, backend_port)
    write_slash_import(f, storage_kind, slash_vmod_path)
    f.write('\timport xkey from "/results/xkey-build/libvmod_xkey.so";\n\n')
    if storage_kind in {"fellow", "buddy"}:
        f.write("\tsub vcl_init {\n")
        write_slash_tuning(f, storage_kind, buddy_reserve_chunks)
        f.write("\t}\n\n")
    f.write("\tsub vcl_recv {\n")
    f.write('\t\tif (req.method == "PURGE") {\n')
    f.write("\t\t\tset req.http.purged = xkey.purge(req.http.Key);\n")
    f.write("\t\t\treturn (synth(200));\n")
    f.write("\t\t}\n")
    f.write("\t}\n\n")
    f.write("\tsub vcl_backend_response {\n")
    f.write(f"\t\tset beresp.ttl = {ttl};\n")
    f.write("\t\tset beresp.http.xkey = bereq.http.X-Cache-Tags;\n")
    f.write("\t}\n\n")
    f.write("\tsub vcl_hit {\n")
    f.write('\t\tset req.http.X-Bench-Cache = "hit";\n')
    f.write("\t}\n\n")
    f.write("\tsub vcl_deliver {\n")
    f.write("\t\tif (req.http.X-Bench-Cache == \"hit\") {\n")
    f.write('\t\t\tset resp.http.X-Bench-Cache = "hit";\n')
    f.write("\t\t} else {\n")
    f.write('\t\t\tset resp.http.X-Bench-Cache = "miss";\n')
    f.write("\t\t}\n")
    f.write("\t\tunset req.http.X-Bench-Cache;\n")
    f.write("\t}\n\n")
    f.write("\tsub vcl_synth {\n")
    f.write("\t\tset resp.http.Purged = req.http.purged;\n")
    f.write("\t\treturn (deliver);\n")
    f.write("\t}\n")
    f.write("} -start\n\n")
    f.write('vinyl v1 -cliok "param.set vsl_mask none"\n\n')


def write_workload(
    path: Path,
    implementation: str,
    profile: str,
    objects: int,
    tags_per_object: int,
    storage: str,
    eviction_storage: str,
    vinyl_threads: int,
    driver_command: str,
    backend_command: str,
    backend_host: str,
    backend_port: int,
    backend_body_bytes: int,
    eviction_body_bytes: int,
    cold_residency_storage: str,
    cold_residency_body_bytes: int,
    storage_kind: str,
    fellow_size: str,
    fellow_segment_size: str,
    fellow_block_size: str,
    buddy_size: str,
    buddy_reserve_chunks: int,
    slash_vmod_path: str,
    timeout_idle: str,
    backend_idle_timeout: str,
    cachetag_persist: bool,
    cachetag_wal_fsync: str,
    shutdown_drain_seconds: float,
    allow_lru_nuked: bool,
    skip_purge: bool,
    ttl_override: str,
) -> None:
    prefix = f"{implementation_slug(implementation)}_{profile_slug(profile)}"
    driver_impl = implementation
    effective_tags = (
        0
        if profile == "untagged-fellow-load"
        else fixed_tags_per_object(profile, tags_per_object)
    )
    unload_fellow_cachetag = storage_kind == "fellow" and implementation.startswith("cachetag")
    with path.open("w", encoding="ascii") as f:
        f.write(
            f'vtest "{implementation} benchmark {profile_title(profile)}: '
            f'{objects} objects, {effective_tags} tags/object"\n\n'
        )
        if is_phase5_held_profile(profile):
            f.write("barrier b_phase5_held sock 2\n")
            f.write("barrier b_phase5_continue sock 2\n\n")
        if profile == "eviction":
            body_bytes = eviction_body_bytes
        elif profile == "purged-cold-residency" and cold_residency_body_bytes > 0:
            body_bytes = cold_residency_body_bytes
        else:
            body_bytes = backend_body_bytes
        write_backend(f, body_bytes, backend_command, backend_host, backend_port)
        write_allocator_environment(f)
        ttl = profile_ttl(profile, ttl_override)
        if profile == "eviction":
            workload_storage = eviction_storage
        elif profile == "purged-cold-residency" and cold_residency_storage:
            workload_storage = cold_residency_storage
        else:
            workload_storage = storage
        if implementation.startswith("cachetag"):
            sweep_interval = (
                "0s"
                if profile in {"phase4-sweep-latency", "phase4-refill-control", "phase6-fill-drain"}
                else "1s" if is_phase5_profile(profile) else None
            )
            write_cachetag_vcl(
                f,
                implementation,
                profile,
                workload_storage,
                ttl,
                vinyl_threads,
                backend_host,
                backend_port,
                storage_kind,
                fellow_size,
                fellow_segment_size,
                fellow_block_size,
                buddy_size,
                buddy_reserve_chunks,
                slash_vmod_path,
                timeout_idle,
                backend_idle_timeout,
                cachetag_persist,
                cachetag_wal_fsync,
                sweep_interval,
            )
            stats_filter = storage_stats_filter("CACHETAG.* -f MAIN.*", storage_kind)
        else:
            write_xkey_vcl(
                f,
                workload_storage,
                ttl,
                vinyl_threads,
                backend_host,
                backend_port,
                storage_kind,
                fellow_size,
                fellow_segment_size,
                fellow_block_size,
                buddy_size,
                buddy_reserve_chunks,
                slash_vmod_path,
                timeout_idle,
                backend_idle_timeout,
            )
            stats_filter = storage_stats_filter("XKEY.* -f MAIN.*", storage_kind)

        if profile == "untagged-fellow-load":
            if storage_kind != "fellow" or not cachetag_persist:
                raise SystemExit(
                    "untagged-fellow-load requires Fellow persistent cachetag"
                )
            write_driver(
                f,
                objects,
                "noindex",
                "noindex",
                0,
                prefix,
                driver_command,
                "none",
            )
            write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_post.stats")
            f.write(
                "vinyl v1 -expect CACHETAG.vcl1_tags_bench."
                "purgemap_fellow_attr_objects_written == 0\n"
            )
            f.write(
                "vinyl v1 -expect CACHETAG.vcl1_tags_bench."
                "purgemap_fellow_attr_bytes_written == 0\n"
            )
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.volatile_objects == 0\n")
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.volatile_edges == 0\n")
            if unload_fellow_cachetag:
                write_fellow_cachetag_vcl_unload(f)
            write_shutdown_drain(f, shutdown_drain_seconds)
            return

        if profile in PHASED_PURGE_PROFILES:
            write_driver(
                f,
                objects,
                f"{driver_impl}-load",
                profile,
                effective_tags,
                f"{prefix}_load",
                driver_command,
            )
            expected_attr_bytes_raw = os.getenv(
                "BENCH_EXPECT_FELLOW_ATTR_BYTES_PER_OBJECT", ""
            )
            if expected_attr_bytes_raw:
                expected_attr_bytes = int(expected_attr_bytes_raw)
                f.write(
                    "client c_stream6_vsc_flush {\n"
                    "\ttxreq -url /__bench_objects\n"
                    "\trxresp\n"
                    "\texpect resp.status == 204\n"
                    "} -run\n"
                )
            write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_pre_purge.stats")
            if expected_attr_bytes_raw:
                f.write(
                    "vinyl v1 -expect CACHETAG.vcl1_tags_bench."
                    f"purgemap_fellow_attr_objects_written == {objects}\n"
                )
                f.write(
                    "vinyl v1 -expect CACHETAG.vcl1_tags_bench."
                    "purgemap_fellow_attr_bytes_written == "
                    f"{objects * expected_attr_bytes}\n"
                )
            if not allow_lru_nuked:
                f.write('vinyl v1 -expect n_lru_nuked == 0\n')
            if implementation.startswith("cachetag") and not allow_lru_nuked:
                if storage_kind == "fellow" and cachetag_persist:
                    f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.volatile_objects == 0\n")
                    f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.volatile_edges == 0\n")
                else:
                    f.write(f"vinyl v1 -expect CACHETAG.vcl1_tags_bench.volatile_objects == {objects}\n")
                    f.write(
                        "vinyl v1 -expect "
                        f"CACHETAG.vcl1_tags_bench.volatile_edges == {objects * effective_tags}\n"
                    )
            if skip_purge:
                if unload_fellow_cachetag:
                    write_fellow_cachetag_vcl_unload(f)
                write_shutdown_drain(f, shutdown_drain_seconds)
                return
            write_driver(
                f,
                objects,
                f"{driver_impl}-purge",
                profile,
                effective_tags,
                f"{prefix}_purge",
                driver_command,
            )
            write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_post_purge.stats")
            if unload_fellow_cachetag:
                write_fellow_cachetag_vcl_unload(f)
            write_shutdown_drain(f, shutdown_drain_seconds)
            return

        if profile in {"phase4-sweep-latency", "phase4-refill-control"}:
            f.write(f'shell "rm -rf /results/phase-markers; mkdir -p /results/phase-markers"\n')
            write_driver(
                f,
                objects,
                f"{driver_impl}-{profile}",
                profile,
                effective_tags,
                prefix,
                driver_command,
                env=(
                    f"BENCH_PHASE_MARKER_DIR=/results/phase-markers "
                    f"BENCH_PHASE_MARKER_PREFIX={prefix}"
                ),
                wait=False,
            )
            write_phase4_marker_snapshot(f, prefix, "phase4_pre", stats_filter, "phase4_start", "start")
            write_phase4_marker_snapshot(f, prefix, "phase4_pre", stats_filter, "phase4_pre")
            write_phase4_marker_snapshot(f, prefix, "phase4_compact", stats_filter, "phase4_compact")
            write_phase4_marker_snapshot(f, prefix, "phase4_sweep", stats_filter, "phase4_refill")
            write_phase4_marker_snapshot(f, prefix, "phase4_post", stats_filter, "phase4_post")
            f.write("process p1 -wait\n\n")
        elif is_phase5_profile(profile):
            f.write(f'shell "rm -rf /results/phase-markers; mkdir -p /results/phase-markers"\n')
            write_driver(
                f,
                objects,
                f"{driver_impl}-{profile}",
                profile,
                effective_tags,
                prefix,
                driver_command,
                env=(
                    f"BENCH_PHASE_MARKER_DIR=/results/phase-markers "
                    f"BENCH_PHASE_MARKER_PREFIX={prefix}"
                ),
                wait=False,
            )
            write_phase5_marker_snapshot(f, prefix, "phase5_hold_fetch", stats_filter, "phase5_hold_fetch_start", "start")
            if is_phase5_held_profile(profile):
                f.write("barrier b_phase5_held sync\n")
                f.write(f'shell "touch /results/phase-markers/{prefix}.phase5_hold.active"\n')
                write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_phase5_hold_active.stats")
            else:
                write_phase5_marker_snapshot(f, prefix, "phase5_hold", stats_filter, "phase5_hold_active", "active")
            write_phase5_marker_snapshot(f, prefix, "phase5_held_load", stats_filter, "phase5_held_load_start", "start")
            write_phase5_marker_snapshot(f, prefix, "phase5_held_load", stats_filter, "phase5_held_load_end")
            if is_phase5_held_profile(profile):
                if is_phase5_shutdown_profile(profile):
                    write_phase5_marker_snapshot(
                        f, prefix, "phase5_shutdown", stats_filter, "phase5_shutdown_ready", "ready"
                    )
                    f.write(
                        f'vinyl v1 -cliok "vcl.inline vcl2 \\\"vcl 4.1; backend none none;\\\" auto"\n'
                    )
                    f.write('vinyl v1 -cliok "vcl.use vcl2"\n')
                    f.write(
                        "process p_shutdown -log {\n"
                        "\tt0=$(date +%s%N)\n"
                        f"\tprintf 'phase5_shutdown_cold_start_ns=%s\\n' \"$t0\" > /results/phase-markers/{prefix}.phase5_shutdown.cold.start\n"
                        "\tvinyladm -n ${v1_name} vcl.discard vcl1\n"
                        "\trc=$?\n"
                        "\tt1=$(date +%s%N)\n"
                        "\tduration_ns=$(expr \"$t1\" - \"$t0\")\n"
                        f"\tprintf 'phase5_shutdown_cold_wall_ns=%s\\n' \"$duration_ns\" > /results/{prefix}_phase5_shutdown_cold.env\n"
                        "\texit $rc\n"
                        "} -start\n"
                    )
                    f.write(
                        f'shell "i=0; while [ $i -lt 360000 ]; do test -f /results/phase-markers/{prefix}.phase5_shutdown.cold.start && exit 0; i=$((i + 1)); sleep 0.01; done; echo timed out waiting for shutdown discard start; exit 1"\n'
                    )
                    f.write(
                        f'shell "touch /results/phase-markers/{prefix}.phase5_shutdown.release"\n'
                    )
                    f.write("barrier b_phase5_continue sync\n")
                    f.write("process p_shutdown -expect-exit 0 -wait\n")
                    f.write("process p1 -wait\n")
                    f.write(
                        f'shell "test -s /results/{prefix}_phase5_shutdown_cold.env"\n'
                    )
                    f.write("vinyl v1 -stop\n")
                    f.write('shell "vinyladm -n ${v1_name} panic.clear || true"\n')
                    f.write("process p_backend -stop\n\n")
                    return
                write_phase5_marker_snapshot(
                    f, prefix, "phase5_hold_release", stats_filter, "phase5_pre_release", "start"
                )
                f.write("barrier b_phase5_continue sync\n")
            else:
                write_phase5_marker_snapshot(
                    f, prefix, "phase5_hold_release", stats_filter, "phase5_pre_release", "start"
                )
            write_phase5_marker_snapshot(f, prefix, "phase5_hold_release", stats_filter, "phase5_released")
            f.write("process p1 -wait\n\n")
        elif is_phase6_profile(profile):
            cycles = int(os.getenv("CHURN_CYCLES", "10"))
            if cycles < 10:
                raise SystemExit("phase6-fill-drain requires CHURN_CYCLES >= 10")
            f.write(f'shell "rm -rf /results/phase-markers; mkdir -p /results/phase-markers"\n')
            write_driver(
                f,
                objects,
                f"{driver_impl}-{profile}",
                profile,
                effective_tags,
                prefix,
                driver_command,
                env=(
                    f"BENCH_PHASE_MARKER_DIR=/results/phase-markers "
                    f"BENCH_PHASE_MARKER_PREFIX={prefix}"
                ),
                wait=False,
            )
            for cycle in range(cycles):
                write_phase6_cycle_snapshot(f, prefix, cycle, stats_filter)
            f.write("process p1 -wait\n\n")
        else:
            write_driver(
                f,
                objects,
                f"{driver_impl}-{profile}",
                profile,
                effective_tags,
                prefix,
                driver_command,
            )
        if profile in {"populated-map-warm", "stream1-checkpoint-overlap"} and implementation.startswith("cachetag"):
            write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_lifecycle_post.stats")
            if storage_kind == "fellow" and cachetag_persist:
                f.write(
                    f'shell "sh /cachetag-host/benchmarks/capture_persistence_files.sh '
                    f'${{tmpdir}}/cachetag-bench-persist /results/{prefix}.persistence"\n'
                )
            expectation = os.getenv("BENCH_STREAM1_EXPECT_CHECKPOINT", "")
            if expectation == "retained":
                f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.persist_checkpoint_publications >= 2\n")
                f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.persist_checkpoint_entries > 0\n")
                cap = os.getenv("BENCH_CACHE_TAG_PURGE_HISTORY_MAX_ENTRIES", "")
                if cap:
                    f.write(f"vinyl v1 -expect CACHETAG.vcl1_tags_bench.purgemap_entries <= {int(cap)}\n")
            elif expectation == "initial-only":
                f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.persist_checkpoint_publications == 1\n")
                f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.persist_checkpoint_entries == 0\n")
            elif expectation:
                raise SystemExit("BENCH_STREAM1_EXPECT_CHECKPOINT must be initial-only, retained, or unset")
        if is_phase5_profile(profile) and not is_phase5_shutdown_profile(profile):
            f.write(
                "client c_phase5_vsc_flush {\n"
                "\ttxreq -url /__bench_objects\n"
                "\trxresp\n"
                "\texpect resp.status == 204\n"
                "} -run\n"
            )
        if is_phase6_profile(profile):
            f.write(
                "client c_phase6_vsc_flush {\n"
                "\ttxreq -url /__bench_objects\n"
                "\trxresp\n"
                "\texpect resp.status == 204\n"
                "} -run\n"
            )
        phase6_stats_filter = (
            f"{stats_filter} -f SMA.* -f MEMPOOL.*"
            if is_phase6_profile(profile)
            else stats_filter
        )
        write_stats_capture(f, "v1", phase6_stats_filter, f"/results/{prefix}_post.stats")
        if profile == "phase4-sweep-latency" and implementation.startswith("cachetag"):
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.purgemap_entries == 0\n")
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.purgemap_auto_reclaim_passes >= 1\n")
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.sweep_passes >= 1\n")
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.sweep_killed > 0\n")
        if is_phase5_profile(profile) and implementation.startswith("cachetag"):
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.publication_readers_phase0 == 0\n")
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.publication_readers_phase1 == 0\n")
            f.write(
                'shell "set -- $(vinylstat -1 -n ${v1_name} '
                '-f CACHETAG.vcl1_tags_bench.publication_acquires '
                '-f CACHETAG.vcl1_tags_bench.publication_releases | awk '
                "'/publication_acquires/{a=$2} /publication_releases/{r=$2} END{print a, r}'); "
                'test \\"$1\\" = \\"$2\\""\n'
            )
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.purgemap_auto_reclaim_passes >= 1\n")
            if is_phase5_held_profile(profile):
                f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.purgemap_auto_reclaim_deferred_pending >= 1\n")
            if profile == "phase5-held-cap":
                f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.purgemap_prunes >= 1\n")
            if is_phase5_cap_profile(profile):
                f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.purgemap_entries <= 32\n")
        if is_phase6_profile(profile) and implementation.startswith("cachetag"):
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.volatile_objects == 0\n")
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.volatile_edges == 0\n")
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.volatile_object_table_slots == 0\n")
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.volatile_side_table_buckets == 0\n")
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.purgemap_entries == 0\n")
            f.write("vinyl v1 -expect n_lru_nuked > 0\n")
        if profile == "purge-storm" and env_flag(
            "BENCH_PURGEMAP_EXPECT_REBUILD"
        ):
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.purgemap_rebuilds_same_size >= 1\n")
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.purgemap_empty_slots > 0\n")
        if unload_fellow_cachetag:
            write_fellow_cachetag_vcl_unload(f)
        write_shutdown_drain(f, shutdown_drain_seconds)
        if profile == "eviction":
            f.write("vinyl v1 -expect n_lru_nuked > 0\n")
        elif not allow_lru_nuked and not is_phase6_profile(profile):
            f.write("vinyl v1 -expect n_lru_nuked == 0\n")


def write_noindex_vcl(
    f,
    storage: str,
    vinyl_threads: int,
    backend_host: str,
    backend_port: int,
    storage_kind: str,
    fellow_size: str,
    fellow_segment_size: str,
    fellow_block_size: str,
    buddy_size: str,
    buddy_reserve_chunks: int,
    slash_vmod_path: str,
    timeout_idle: str,
    backend_idle_timeout: str,
    phase6_bans: bool = False,
) -> None:
    f.write(
        f'vinyl v1 -arg "{vinyl_arg(storage, vinyl_threads, "-p vmod_path=${pwd}/.libs:", storage_kind=storage_kind, fellow_size=fellow_size, fellow_segment_size=fellow_segment_size, fellow_block_size=fellow_block_size, buddy_size=buddy_size, slash_vmod_path=slash_vmod_path, timeout_idle=timeout_idle, backend_idle_timeout=backend_idle_timeout)}" -vcl {{\n'
    )
    write_backend_vcl(f, backend_host, backend_port)
    if storage_kind in {"fellow", "buddy"}:
        write_slash_import(f, storage_kind, slash_vmod_path)
        f.write("\n")
        f.write("\tsub vcl_init {\n")
        write_slash_tuning(f, storage_kind, buddy_reserve_chunks)
        f.write("\t}\n\n")
    if phase6_bans:
        f.write("\tsub vcl_recv {\n")
        f.write('\t\tif (req.method == "BAN") {\n')
        f.write('\t\t\tban("obj.http.X-Bench-Phase6-Generation == " + req.http.X-Bench-Phase6-Generation);\n')
        f.write('\t\t\tset req.http.X-Bench-Ban = "accepted";\n')
        f.write("\t\t\treturn (synth(200));\n")
        f.write("\t\t}\n")
        f.write("\t}\n")
    f.write("\tsub vcl_backend_response {\n")
    f.write("\t\tset beresp.ttl = 1h;\n")
    if phase6_bans:
        f.write("\t\tset beresp.grace = 0s;\n")
        f.write("\t\tset beresp.keep = 0s;\n")
        f.write("\t\tset beresp.http.X-Bench-Phase6-Generation = bereq.http.X-Bench-Phase6-Generation;\n")
    f.write("\t}\n")
    f.write("\tsub vcl_hit {\n")
    f.write('\t\tset req.http.X-Bench-Cache = "hit";\n')
    f.write("\t}\n")
    f.write("\tsub vcl_deliver {\n")
    f.write("\t\tif (req.http.X-Bench-Cache == \"hit\") {\n")
    f.write('\t\t\tset resp.http.X-Bench-Cache = "hit";\n')
    f.write("\t\t} else {\n")
    f.write('\t\t\tset resp.http.X-Bench-Cache = "miss";\n')
    f.write("\t\t}\n")
    f.write("\t\tunset req.http.X-Bench-Cache;\n")
    f.write("\t}\n")
    if phase6_bans:
        f.write("\tsub vcl_synth {\n")
        f.write("\t\tset resp.http.X-Bench-Ban = req.http.X-Bench-Ban;\n")
        f.write("\t\treturn (deliver);\n")
        f.write("\t}\n")
    f.write("} -start\n\n")
    f.write('vinyl v1 -cliok "param.set vsl_mask none"\n\n')
    if phase6_bans:
        f.write('vinyl v1 -cliok "param.set ban_lurker_age 0"\n')
        f.write('vinyl v1 -cliok "param.set ban_lurker_batch 1000000"\n')
        f.write('vinyl v1 -cliok "param.set ban_lurker_sleep 0.001"\n\n')


def write_noindex_workload(
    path: Path,
    objects: int,
    storage: str,
    vinyl_threads: int,
    driver_command: str,
    backend_command: str,
    backend_host: str,
    backend_port: int,
    backend_body_bytes: int,
    storage_kind: str,
    fellow_size: str,
    fellow_segment_size: str,
    fellow_block_size: str,
    buddy_size: str,
    buddy_reserve_chunks: int,
    slash_vmod_path: str,
    timeout_idle: str,
    backend_idle_timeout: str,
    shutdown_drain_seconds: float,
) -> None:
    with path.open("w", encoding="ascii") as f:
        f.write(f'vtest "no-index request baseline: {objects} objects"\n\n')
        write_backend(f, backend_body_bytes, backend_command, backend_host, backend_port)
        write_allocator_environment(f)
        write_noindex_vcl(
            f,
            storage,
            vinyl_threads,
            backend_host,
            backend_port,
            storage_kind,
            fellow_size,
            fellow_segment_size,
            fellow_block_size,
            buddy_size,
            buddy_reserve_chunks,
            slash_vmod_path,
            timeout_idle,
            backend_idle_timeout,
        )
        write_driver(
            f,
            objects,
            "noindex",
            "noindex",
            0,
            "noindex_load",
            driver_command,
            "none",
        )
        f.write('shell "vinylstat -1 -n ${v1_name} -f MAIN.client_req -f MAIN.cache_miss -f MAIN.cache_hit -f MAIN.n_object -f MAIN.n_lru_nuked > /results/noindex_load.stats"\n')
        write_shutdown_drain(f, shutdown_drain_seconds)


def write_noindex_concurrent_workload(
    path: Path,
    objects: int,
    storage: str,
    vinyl_threads: int,
    driver_command: str,
    backend_command: str,
    backend_host: str,
    backend_port: int,
    backend_body_bytes: int,
    storage_kind: str,
    fellow_size: str,
    fellow_segment_size: str,
    fellow_block_size: str,
    buddy_size: str,
    buddy_reserve_chunks: int,
    slash_vmod_path: str,
    timeout_idle: str,
    backend_idle_timeout: str,
    shutdown_drain_seconds: float,
) -> None:
    with path.open("w", encoding="ascii") as f:
        f.write(f'vtest "no-index concurrent pressure baseline: {objects} objects"\n\n')
        write_backend(f, backend_body_bytes, backend_command, backend_host, backend_port)
        write_allocator_environment(f)
        write_noindex_vcl(
            f,
            storage,
            vinyl_threads,
            backend_host,
            backend_port,
            storage_kind,
            fellow_size,
            fellow_segment_size,
            fellow_block_size,
            buddy_size,
            buddy_reserve_chunks,
            slash_vmod_path,
            timeout_idle,
            backend_idle_timeout,
        )
        write_driver(
            f,
            objects,
            "noindex-concurrent",
            "concurrent",
            0,
            "noindex_concurrent",
            driver_command,
            "none",
        )
        f.write('shell "vinylstat -1 -n ${v1_name} -f MAIN.client_req -f MAIN.cache_miss -f MAIN.cache_hit -f MAIN.n_object -f MAIN.n_lru_nuked > /results/noindex_concurrent_post.stats"\n')
        write_shutdown_drain(f, shutdown_drain_seconds)


def write_noindex_phase6_drain_barrier(f, prefix: str, cycle: int, stage: str = "") -> None:
    suffix = f"_{stage}" if stage else ""
    marker = f"phase6_ban_{cycle:02d}{suffix}"
    requested = f"/results/phase-markers/{prefix}.{marker}.requested"
    drained = f"/results/phase-markers/{prefix}.{marker}.drained"
    f.write(
        f'shell "i=0; while [ $i -lt 360000 ]; do test -f {requested} && break; '
        'i=$((i + 1)); sleep 0.01; done; test -f '
        f'{requested}; i=0; while [ $i -lt 360000 ]; do '
        "n=$(vinylstat -1 -n ${v1_name} -f MAIN.n_object | awk '$1 ~ /MAIN.n_object/ {print $2; exit}'); "
        f'test x$n = x0 && touch {drained} && exit 0; '
        'i=$((i + 1)); sleep 0.01; done; echo timed out waiting for no-index ban drain; exit 1"\n'
    )


def write_noindex_phase6_workload(
    path: Path,
    objects: int,
    storage: str,
    vinyl_threads: int,
    driver_command: str,
    backend_command: str,
    backend_host: str,
    backend_port: int,
    backend_body_bytes: int,
    storage_kind: str,
    fellow_size: str,
    fellow_segment_size: str,
    fellow_block_size: str,
    buddy_size: str,
    buddy_reserve_chunks: int,
    slash_vmod_path: str,
    timeout_idle: str,
    backend_idle_timeout: str,
    shutdown_drain_seconds: float,
) -> None:
    prefix = "noindex_phase6_fill_drain"
    cycles = int(os.getenv("CHURN_CYCLES", "10"))
    with path.open("w", encoding="ascii") as f:
        f.write(f'vtest "no-index Phase 6 generation-ban drain: {objects} objects"\n\n')
        write_backend(f, backend_body_bytes, backend_command, backend_host, backend_port)
        write_allocator_environment(f)
        write_noindex_vcl(
            f,
            storage,
            vinyl_threads,
            backend_host,
            backend_port,
            storage_kind,
            fellow_size,
            fellow_segment_size,
            fellow_block_size,
            buddy_size,
            buddy_reserve_chunks,
            slash_vmod_path,
            timeout_idle,
            backend_idle_timeout,
            phase6_bans=True,
        )
        f.write('shell "rm -rf /results/phase-markers; mkdir -p /results/phase-markers"\n')
        write_driver(
            f,
            objects,
            "noindex-phase6-fill-drain",
            "phase6-fill-drain",
            0,
            prefix,
            driver_command,
            "none",
            env=(
                "BENCH_PHASE_MARKER_DIR=/results/phase-markers "
                f"BENCH_PHASE_MARKER_PREFIX={prefix}"
            ),
            wait=False,
        )
        stats_filter = storage_stats_filter("MAIN.*", storage_kind)
        for cycle in range(cycles):
            if cycle == 5:
                write_noindex_phase6_drain_barrier(f, prefix, cycle, "base")
            write_noindex_phase6_drain_barrier(f, prefix, cycle)
            write_phase6_cycle_snapshot(
                f, prefix, cycle, stats_filter, require_tripwire=False
            )
        f.write("process p1 -wait\n")
        write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_post.stats")
        f.write("vinyl v1 -expect n_object == 0\n")
        f.write("vinyl v1 -expect n_lru_nuked > 0\n")
        write_shutdown_drain(f, shutdown_drain_seconds)


def write_fellow_restart_workload(
    path: Path,
    implementation: str,
    restart_profile: str,
    tag_profile: str,
    touch_percent: int,
    objects: int,
    tags_per_object: int,
    storage: str,
    vinyl_threads: int,
    driver_command: str,
    backend_command: str,
    backend_host: str,
    backend_port: int,
    backend_body_bytes: int,
    fellow_size: str,
    fellow_segment_size: str,
    fellow_block_size: str,
    slash_vmod_path: str,
    timeout_idle: str,
    backend_idle_timeout: str,
    cachetag_wal_fsync: str,
    shutdown_drain_seconds: float,
    allow_lru_nuked: bool,
) -> None:
    prefix = f"{implementation_slug(implementation)}_{profile_slug(restart_profile)}"
    effective_tags = fixed_tags_per_object(tag_profile, tags_per_object)
    touch_objects = max(1, (objects * touch_percent) // 100)
    if touch_objects > objects:
        touch_objects = objects
    stats_filter = storage_stats_filter("CACHETAG.*", "fellow")
    sweep_interval = None
    if restart_profile == "fellow-restart-hot-purge":
        sweep_interval = "0s"
    with path.open("w", encoding="ascii") as f:
        f.write(
            f'vtest "persistent cachetag Fellow {profile_title(restart_profile)}: '
            f'{objects} objects, {effective_tags} tags/object, {tag_profile}, '
            'purge-map membership"\n\n'
        )
        write_backend(f, backend_body_bytes, backend_command, backend_host, backend_port)
        write_cachetag_vcl(
            f,
            implementation,
            tag_profile,
            storage,
            "1h",
            vinyl_threads,
            backend_host,
            backend_port,
            "fellow",
            fellow_size,
            fellow_segment_size,
            fellow_block_size,
            "1GB",
            0,
            slash_vmod_path,
            timeout_idle,
            backend_idle_timeout,
            True,
            cachetag_wal_fsync,
            sweep_interval,
        )
        write_driver(
            f,
            objects,
            "cachetag-load",
            tag_profile,
            effective_tags,
            f"{prefix}_load",
            driver_command,
        )
        write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_post_load.stats")
        if not allow_lru_nuked:
            f.write("vinyl v1 -expect n_lru_nuked == 0\n")
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.volatile_objects == 0\n")
            f.write("vinyl v1 -expect CACHETAG.vcl1_tags_bench.volatile_edges == 0\n")
        write_shutdown_drain(f, shutdown_drain_seconds)
        f.write("vinyl v1 -stop\n")
        f.write('shell "vinyladm -n ${v1_name} panic.clear || true"\n')
        f.write("vinyl v1 -start\n")
        write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_post_restart.stats")
        if restart_profile == "fellow-restart-idle-memory":
            write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_post.stats")
            write_benchmark_teardown(f, shutdown_drain_seconds)
            return
        if restart_profile == "fellow-restart-first-touch":
            write_driver(
                f,
                touch_objects,
                "cachetag-load",
                tag_profile,
                effective_tags,
                f"{prefix}_first_touch",
                driver_command,
            )
            write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_post_first_touch.stats")
            write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_post.stats")
            write_benchmark_teardown(f, shutdown_drain_seconds)
            return
        if restart_profile == "fellow-restart-hot-purge":
            write_client_purge(
                f,
                "c_cold_purge",
                tag_profile,
            )
        else:
            write_driver(
                f,
                objects,
                "cachetag-purge",
                tag_profile,
                effective_tags,
                f"{prefix}_cold_purge",
                driver_command,
            )
        write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_post_cold_purge.stats")
        if restart_profile == "fellow-restart-hot-purge":
            write_client_purge(
                f,
                "c_hot_purge",
                tag_profile,
            )
            write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_post_hot_purge.stats")
        write_stats_capture(f, "v1", stats_filter, f"/results/{prefix}_post.stats")
        write_benchmark_teardown(f, shutdown_drain_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--objects", type=int, default=1000)
    parser.add_argument("--tags-per-object", type=int, default=4)
    parser.add_argument("--storage", default="256m")
    parser.add_argument("--eviction-storage", default="1m")
    parser.add_argument("--cold-residency-storage", default="")
    parser.add_argument(
        "--vinyl-thread-pool-max",
        type=int,
        default=int(os.getenv("BENCH_VINYL_THREAD_POOL_MAX", "16")),
        help="maximum Vinyl workers in each pool",
    )
    parser.add_argument(
        "--vinyl-thread-pools",
        type=int,
        default=int(os.getenv("BENCH_VINYL_THREAD_POOLS", "2")),
        help="explicit Vinyl worker-pool count",
    )
    parser.add_argument("--driver-command", default="/work/cachetag-http-workload-driver")
    parser.add_argument("--backend-command", default="/work/cachetag-benchmark-backend")
    parser.add_argument("--backend-host", default="127.0.0.1")
    parser.add_argument("--backend-port", type=int, default=18080)
    parser.add_argument("--backend-body-bytes", type=int, default=2)
    parser.add_argument("--eviction-body-bytes", type=int, default=4096)
    parser.add_argument("--cold-residency-body-bytes", type=int, default=0)
    parser.add_argument("--storage-kind", choices=("default", "fellow", "buddy"), default="default")
    parser.add_argument("--fellow-size", default="1GB")
    parser.add_argument("--fellow-segment-size", default="1MB")
    parser.add_argument("--fellow-block-size", default="64KB")
    parser.add_argument("--buddy-size", default="1GB")
    parser.add_argument("--buddy-reserve-chunks", type=int, default=0)
    parser.add_argument("--slash-vmod-path", default="")
    parser.add_argument("--timeout-idle", default="")
    parser.add_argument("--backend-idle-timeout", default="")
    parser.add_argument("--cachetag-persist", action="store_true")
    parser.add_argument("--cachetag-wal-fsync", choices=("strict", "grouped"), default="strict")
    parser.add_argument("--shutdown-drain-seconds", type=float, default=0)
    parser.add_argument("--include-xkey", action="store_true")
    parser.add_argument("--skip-noindex", action="store_true")
    parser.add_argument(
        "--profile",
        default="explicit-purge",
        help="benchmark profile to generate, 'all', or a comma-separated profile list",
    )
    args = parser.parse_args()
    if args.objects <= 0:
        raise SystemExit("--objects must be positive")
    if args.tags_per_object <= 0:
        raise SystemExit("--tags-per-object must be positive")
    if not args.storage:
        raise SystemExit("--storage must not be empty")
    if not args.eviction_storage:
        raise SystemExit("--eviction-storage must not be empty")
    if args.cold_residency_storage == "":
        args.cold_residency_storage = args.storage
    if args.vinyl_thread_pool_max <= 0:
        raise SystemExit("--vinyl-thread-pool-max must be positive")
    if args.vinyl_thread_pools <= 0:
        raise SystemExit("--vinyl-thread-pools must be positive")
    os.environ["BENCH_VINYL_THREAD_POOLS"] = str(args.vinyl_thread_pools)
    if not args.driver_command:
        raise SystemExit("--driver-command must not be empty")
    if not args.backend_command:
        raise SystemExit("--backend-command must not be empty")
    if not args.backend_host:
        raise SystemExit("--backend-host must not be empty")
    if args.backend_port <= 0:
        raise SystemExit("--backend-port must be positive")
    if args.backend_body_bytes < 0:
        raise SystemExit("--backend-body-bytes must be non-negative")
    if args.eviction_body_bytes < 0:
        raise SystemExit("--eviction-body-bytes must be non-negative")
    if args.cold_residency_body_bytes < 0:
        raise SystemExit("--cold-residency-body-bytes must be non-negative")
    if args.shutdown_drain_seconds < 0:
        raise SystemExit("--shutdown-drain-seconds must be non-negative")
    if args.buddy_reserve_chunks < 0:
        raise SystemExit("--buddy-reserve-chunks must be non-negative")
    if args.storage_kind in {"fellow", "buddy"} and not args.slash_vmod_path:
        raise SystemExit("--slash-vmod-path is required with --storage-kind=fellow or --storage-kind=buddy")
    if args.timeout_idle and any(c.isspace() for c in args.timeout_idle):
        raise SystemExit("--timeout-idle must not contain whitespace")
    if args.backend_idle_timeout and any(c.isspace() for c in args.backend_idle_timeout):
        raise SystemExit("--backend-idle-timeout must not contain whitespace")
    allow_lru_nuked = os.getenv("BENCH_ALLOW_LRU_NUKED", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    skip_purge = os.getenv("BENCH_SKIP_PURGE", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    ttl_override = os.getenv("CACHE_TAG_BENCH_TTL", "")
    if ttl_override and any(c.isspace() for c in ttl_override):
        raise SystemExit("CACHE_TAG_BENCH_TTL must not contain whitespace")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    profiles = selected_profiles(args.profile)
    expected_attr_bytes_raw = os.getenv(
        "BENCH_EXPECT_FELLOW_ATTR_BYTES_PER_OBJECT", ""
    )
    if expected_attr_bytes_raw:
        try:
            expected_attr_bytes = int(expected_attr_bytes_raw)
        except ValueError as exc:
            raise SystemExit(
                "BENCH_EXPECT_FELLOW_ATTR_BYTES_PER_OBJECT must be a positive integer"
            ) from exc
        if expected_attr_bytes <= 0:
            raise SystemExit(
                "BENCH_EXPECT_FELLOW_ATTR_BYTES_PER_OBJECT must be a positive integer"
            )
        if args.objects * expected_attr_bytes > (1 << 64) - 1:
            raise SystemExit(
                "BENCH_EXPECT_FELLOW_ATTR_BYTES_PER_OBJECT total exceeds uint64"
            )
        if (
            args.storage_kind != "fellow"
            or not args.cachetag_persist
            or profiles != ("single-unique-tag",)
            or not skip_purge
            or args.include_xkey
            or not args.skip_noindex
        ):
            raise SystemExit(
                "BENCH_EXPECT_FELLOW_ATTR_BYTES_PER_OBJECT requires persistent Fellow "
                "single-unique-tag with purge, xkey, and noindex disabled"
            )
    if any(is_phase6_profile(profile) for profile in profiles) and args.tags_per_object < 4:
        raise SystemExit("phase6-fill-drain requires --tags-per-object >= 4")
    restart_profiles = [profile for profile in profiles if profile in RESTART_PROFILES]
    if restart_profiles:
        normal_profiles = [profile for profile in profiles if profile not in RESTART_PROFILES]
        if normal_profiles:
            raise SystemExit("restart profiles cannot be mixed with normal benchmark profiles")
        if args.storage_kind != "fellow":
            raise SystemExit("fellow restart profiles require --storage-kind=fellow")
        if not args.cachetag_persist:
            raise SystemExit("fellow restart profiles require --cachetag-persist")
        restart_tag_profile = os.getenv("BENCH_RESTART_TAG_PROFILE", "low-fanout-unique")
        if restart_tag_profile not in PHASED_PURGE_PROFILES:
            valid = ", ".join(PHASED_PURGE_PROFILES)
            raise SystemExit(f"BENCH_RESTART_TAG_PROFILE must be one of: {valid}")
        restart_touch_percent_raw = os.getenv("BENCH_RESTART_TOUCH_PERCENT", "10")
        restart_touch_percent = int(restart_touch_percent_raw)
        if restart_touch_percent <= 0 or restart_touch_percent > 100:
            raise SystemExit("BENCH_RESTART_TOUCH_PERCENT must be in 1..100")
        for restart_profile in restart_profiles:
            write_fellow_restart_workload(
                args.out_dir
                / f"cachetag_{profile_slug(restart_profile)}.vtc",
                "cachetag",
                restart_profile,
                restart_tag_profile,
                restart_touch_percent,
                args.objects,
                args.tags_per_object,
                args.storage,
                args.vinyl_thread_pool_max,
                args.driver_command,
                args.backend_command,
                args.backend_host,
                args.backend_port,
                args.backend_body_bytes,
                args.fellow_size,
                args.fellow_segment_size,
                args.fellow_block_size,
                args.slash_vmod_path,
                args.timeout_idle,
                args.backend_idle_timeout,
                args.cachetag_wal_fsync,
                args.shutdown_drain_seconds,
                allow_lru_nuked,
            )
        return
    if not args.skip_noindex:
        write_noindex_workload(
            args.out_dir / "noindex_load.vtc",
            args.objects,
            args.storage,
            args.vinyl_thread_pool_max,
            args.driver_command,
            args.backend_command,
            args.backend_host,
            args.backend_port,
            args.backend_body_bytes,
            args.storage_kind,
            args.fellow_size,
            args.fellow_segment_size,
            args.fellow_block_size,
            args.buddy_size,
            args.buddy_reserve_chunks,
            args.slash_vmod_path,
            args.timeout_idle,
            args.backend_idle_timeout,
            args.shutdown_drain_seconds,
        )
        if any(is_phase6_profile(profile) for profile in profiles):
            write_noindex_phase6_workload(
                args.out_dir / "noindex_phase6_fill_drain.vtc",
                args.objects,
                args.storage,
                args.vinyl_thread_pool_max,
                args.driver_command,
                args.backend_command,
                args.backend_host,
                args.backend_port,
                args.backend_body_bytes,
                args.storage_kind,
                args.fellow_size,
                args.fellow_segment_size,
                args.fellow_block_size,
                args.buddy_size,
                args.buddy_reserve_chunks,
                args.slash_vmod_path,
                args.timeout_idle,
                args.backend_idle_timeout,
                args.shutdown_drain_seconds,
            )
        if args.include_xkey and "concurrent" in profiles:
            write_noindex_concurrent_workload(
                args.out_dir / "noindex_concurrent.vtc",
                args.objects,
                args.storage,
                args.vinyl_thread_pool_max,
                args.driver_command,
                args.backend_command,
                args.backend_host,
                args.backend_port,
                args.backend_body_bytes,
                args.storage_kind,
                args.fellow_size,
                args.fellow_segment_size,
                args.fellow_block_size,
                args.buddy_size,
                args.buddy_reserve_chunks,
                args.slash_vmod_path,
                args.timeout_idle,
                args.backend_idle_timeout,
                args.shutdown_drain_seconds,
            )
    implementation_bases = ["cachetag"]
    for profile in profiles:
        for implementation in implementation_bases:
            write_workload(
                args.out_dir / f"{implementation_slug(implementation)}_{profile_slug(profile)}.vtc",
                implementation,
                profile,
                args.objects,
                args.tags_per_object,
                args.storage,
                args.eviction_storage,
                args.vinyl_thread_pool_max,
                args.driver_command,
                args.backend_command,
                args.backend_host,
                args.backend_port,
                args.backend_body_bytes,
                args.eviction_body_bytes,
                args.cold_residency_storage,
                args.cold_residency_body_bytes,
                args.storage_kind,
                args.fellow_size,
                args.fellow_segment_size,
                args.fellow_block_size,
                args.buddy_size,
                args.buddy_reserve_chunks,
                args.slash_vmod_path,
                args.timeout_idle,
                args.backend_idle_timeout,
                args.cachetag_persist,
                args.cachetag_wal_fsync,
                args.shutdown_drain_seconds,
                allow_lru_nuked,
                skip_purge,
                ttl_override,
            )
        if args.include_xkey and not is_phase6_profile(profile):
            write_workload(
                args.out_dir / f"xkey_{profile_slug(profile)}.vtc",
                "xkey",
                profile,
                args.objects,
                args.tags_per_object,
                args.storage,
                args.eviction_storage,
                args.vinyl_thread_pool_max,
                args.driver_command,
                args.backend_command,
                args.backend_host,
                args.backend_port,
                args.backend_body_bytes,
                args.eviction_body_bytes,
                args.cold_residency_storage,
                args.cold_residency_body_bytes,
                args.storage_kind,
                args.fellow_size,
                args.fellow_segment_size,
                args.fellow_block_size,
                args.buddy_size,
                args.buddy_reserve_chunks,
                args.slash_vmod_path,
                args.timeout_idle,
                args.backend_idle_timeout,
                args.cachetag_persist,
                args.cachetag_wal_fsync,
                args.shutdown_drain_seconds,
                allow_lru_nuked,
                skip_purge,
                ttl_override,
            )


if __name__ == "__main__":
    main()
