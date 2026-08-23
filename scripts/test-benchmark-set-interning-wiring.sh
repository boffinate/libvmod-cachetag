#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
script=$repo_dir/scripts/benchmark-cachetag-vmod.sh

sh -n "$script"

grep -F 'BENCH_RUNTIME_SET_INTERNING must be 0 or 1' "$script" >/dev/null
grep -F 'BENCH_LEGACY_SET_INTERNING must be 0 or 1 for legacy generation' "$script" >/dev/null
grep -F -- '--label "org.cachetag.benchmark.runtime_set_interning=${bench_runtime_set_interning:-none}"' "$script" >/dev/null
grep -F -- '-e "BENCH_RUNTIME_SET_INTERNING=$bench_runtime_set_interning"' "$script" >/dev/null
grep -F 'legacy:0:) cachetag_configure_arg=--disable-set-interning ;;' "$script" >/dev/null
grep -F 'legacy:1:) cachetag_configure_arg=--enable-set-interning ;;' "$script" >/dev/null
grep -F 'BUILD_PROVENANCE_CODE_GENERATION="$BENCH_CODE_GENERATION"' "$script" >/dev/null
grep -F 'BUILD_PROVENANCE_CACHETAG_CONFIGURE_ARGS="$cachetag_configure_args"' "$script" >/dev/null
grep -F 'printf "bench_runtime_set_interning_rendered=%s\n" "$rendered_runtime_set_interning"' "$script" >/dev/null
grep -F 'build_dir="$build_dir/$benchmark_build_arm"' "$script" >/dev/null
grep -F 'legacy generation requires VMOD_BUILD_SRC to name the frozen legacy checkout' "$script" >/dev/null
grep -F 'decision_cohort_fingerprint=' "$script" >/dev/null
grep -F 'printf "cachetag_configure_args=%s\n" "$cachetag_configure_args"' "$script" >/dev/null

echo 'benchmark set-interning wiring checks passed'
