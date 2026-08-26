#!/bin/sh
set -eu

cpu_root=${1:-/sys/devices/system/cpu}
online_cpus=0

fail() {
	printf 'cpu_governor_gate=fail\ncpu_governor_gate_reason=%s\n' "$1" >&2
	exit 1
}

for cpu_dir in "$cpu_root"/cpu[0-9]*; do
	[ -d "$cpu_dir" ] || continue
	if [ -r "$cpu_dir/online" ] && [ "$(sed -n '1p' "$cpu_dir/online")" != 1 ]; then
		continue
	fi
	online_cpus=$((online_cpus + 1))
	governor_file=$cpu_dir/cpufreq/scaling_governor
	if [ ! -r "$governor_file" ]; then
		fail "performance benchmark requires a readable scaling governor for ${cpu_dir##*/}"
	fi
	governor=$(sed -n '1p' "$governor_file") || fail "performance benchmark could not read scaling governor for ${cpu_dir##*/}"
	if [ "$governor" != performance ]; then
		fail "performance benchmark requires performance governor on ${cpu_dir##*/}, found ${governor:-empty}"
	fi
done

if [ "$online_cpus" -eq 0 ]; then
	fail "performance benchmark could not identify any online CPUs"
fi

printf 'cpu_governor_gate=pass\ncpu_governor_gate_online_cpus=%s\n' "$online_cpus"
