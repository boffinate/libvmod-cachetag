#!/bin/sh
set -eu

out=${1:-/dev/stdout}
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT HUP INT TERM

kv() {
	key=$1
	shift
	printf '%s=%s\n' "$key" "$*" >> "$tmp"
}

first_line() {
	file=$1
	if [ -r "$file" ]; then
		sed -n '1p' "$file"
	fi
}

kv date_utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
kv uname "$(uname -a)"
kv kernel "$(uname -r)"
kv machine "$(uname -m)"
kv nproc "$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || printf unknown)"

if [ -r /proc/cpuinfo ]; then
	kv cpu_model "$(awk -F: '/model name|Hardware/ { sub(/^[ \t]+/, "", $2); print $2; exit }' /proc/cpuinfo)"
	kv cpu_sockets "$(awk -F: '/physical id/ { ids[$2]=1 } END { n=0; for (id in ids) n++; if (n) print n; }' /proc/cpuinfo)"
	kv cpu_cores "$(awk -F: '/cpu cores/ { sub(/^[ \t]+/, "", $2); print $2; exit }' /proc/cpuinfo)"
fi

if [ -r /proc/meminfo ]; then
	kv mem_total_kb "$(awk '/MemTotal:/ { print $2 }' /proc/meminfo)"
	kv mem_available_kb "$(awk '/MemAvailable:/ { print $2 }' /proc/meminfo)"
	kv swap_total_kb "$(awk '/SwapTotal:/ { print $2 }' /proc/meminfo)"
	kv swap_free_kb "$(awk '/SwapFree:/ { print $2 }' /proc/meminfo)"
	kv swap_cached_kb "$(awk '/SwapCached:/ { print $2 }' /proc/meminfo)"
fi

if [ -r /proc/vmstat ]; then
	kv vmstat_pswpin "$(awk '/^pswpin / { print $2 }' /proc/vmstat)"
	kv vmstat_pswpout "$(awk '/^pswpout / { print $2 }' /proc/vmstat)"
	kv vmstat_pgmajfault "$(awk '/^pgmajfault / { print $2 }' /proc/vmstat)"
fi

if [ -r /etc/os-release ]; then
	kv os_pretty_name "$(awk -F= '/^PRETTY_NAME=/ { gsub(/^"|"$/, "", $2); print $2 }' /etc/os-release)"
fi

if [ -r /proc/sys/kernel/perf_event_paranoid ]; then
	kv perf_event_paranoid "$(first_line /proc/sys/kernel/perf_event_paranoid)"
fi

governors=$(for file in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do
	[ -r "$file" ] && first_line "$file"
done | sort -u | paste -sd, -)
kv cpu_scaling_governors "${governors:-unavailable}"
if [ -r /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq ]; then
	kv cpu0_scaling_cur_freq_khz "$(first_line /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq)"
else
	kv cpu0_scaling_cur_freq_khz unavailable
fi
if [ -r /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq ]; then
	kv cpu0_scaling_max_freq_khz "$(first_line /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq)"
else
	kv cpu0_scaling_max_freq_khz unavailable
fi
if [ -r /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq ]; then
	kv cpu0_cpuinfo_max_freq_khz "$(first_line /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq)"
else
	kv cpu0_cpuinfo_max_freq_khz unavailable
fi
if [ -r /sys/devices/system/cpu/cpufreq/boost ]; then
	kv cpu_boost_state "cpufreq:$(first_line /sys/devices/system/cpu/cpufreq/boost)"
elif [ -r /sys/devices/system/cpu/intel_pstate/no_turbo ]; then
	kv cpu_boost_state "intel_pstate_no_turbo:$(first_line /sys/devices/system/cpu/intel_pstate/no_turbo)"
else
	kv cpu_boost_state unavailable
fi

if [ -r /proc/self/cgroup ]; then
	kv cgroup "$(tr '\n' ';' < /proc/self/cgroup)"
fi

if command -v docker >/dev/null 2>&1; then
	kv docker_version "$(docker --version 2>/dev/null || true)"
fi

sort "$tmp" > "$out"
