#!/bin/sh
# Static checks for the local set-interning benchmark arm wiring.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
script=$repo_dir/scripts/benchmark-cachetag-vmod.sh

sh -n "$script"

grep -F 'BENCH_SET_INTERNING must be 0 or 1' "$script" >/dev/null
grep -F -- '--label "org.cachetag.benchmark.set_interning=$bench_set_interning"' "$script" >/dev/null
grep -F -- '-e "BENCH_SET_INTERNING=$bench_set_interning"' "$script" >/dev/null
grep -F '0) cachetag_configure_args=--disable-set-interning ;;' "$script" >/dev/null
grep -F '1) cachetag_configure_args=--enable-set-interning ;;' "$script" >/dev/null
grep -F 'BUILD_PROVENANCE_SET_INTERNING="$BENCH_SET_INTERNING"' "$script" >/dev/null
grep -F 'BUILD_PROVENANCE_CACHETAG_CONFIGURE_ARGS="$cachetag_configure_args"' "$script" >/dev/null
grep -F 'printf "bench_set_interning=%s\n" "$BENCH_SET_INTERNING"' "$script" >/dev/null
grep -F 'printf "cachetag_configure_args=%s\n" "$cachetag_configure_args"' "$script" >/dev/null

echo 'benchmark set-interning wiring checks passed'
