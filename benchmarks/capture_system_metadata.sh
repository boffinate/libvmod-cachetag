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
kv hostname "$(hostname 2>/dev/null || printf unknown)"

if [ -r /proc/sys/kernel/random/boot_id ]; then
	kv boot_id "$(first_line /proc/sys/kernel/random/boot_id)"
else
	kv boot_id unavailable
fi

if [ -r /proc/self/status ]; then
	kv self_cpus_allowed_list "$(awk -F: '/^Cpus_allowed_list:/ { sub(/^[ \t]+/, "", $2); print $2 }' /proc/self/status)"
fi

for identity in product_uuid product_name product_serial board_asset_tag; do
	identity_file=/sys/class/dmi/id/$identity
	if [ -r "$identity_file" ]; then
		kv "instance_$identity" "$(first_line "$identity_file")"
	fi
done

if [ -r /proc/cpuinfo ]; then
	kv cpu_model "$(awk -F: '/model name|Hardware/ { sub(/^[ \t]+/, "", $2); print $2; exit }' /proc/cpuinfo)"
	kv cpu_sockets "$(awk -F: '/physical id/ { ids[$2]=1 } END { n=0; for (id in ids) n++; if (n) print n; }' /proc/cpuinfo)"
	kv cpu_cores "$(awk -F: '/cpu cores/ { sub(/^[ \t]+/, "", $2); print $2; exit }' /proc/cpuinfo)"
fi

if command -v lscpu >/dev/null 2>&1; then
	kv cpu_topology "$(lscpu -p=CPU,CORE,SOCKET,NODE,ONLINE 2>/dev/null | awk '!/^#/ { rows = rows (rows ? ";" : "") $0 } END { print rows }')"
	kv numa_nodes "$(lscpu 2>/dev/null | awk -F: '/^NUMA node\(s\):/ { sub(/^[ \t]+/, "", $2); print $2 }')"
fi

smt_siblings=$(for cpu_dir in /sys/devices/system/cpu/cpu[0-9]*; do
	[ -d "$cpu_dir" ] || continue
	cpu=${cpu_dir##*cpu}
	sibling_file=$cpu_dir/topology/thread_siblings_list
	[ -r "$sibling_file" ] || continue
	printf 'cpu%s:%s\n' "$cpu" "$(first_line "$sibling_file")"
done | sort -V | paste -sd';' -)
kv cpu_smt_siblings "${smt_siblings:-unavailable}"

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
frequency_state=$(for cpu_dir in /sys/devices/system/cpu/cpu[0-9]*; do
	[ -d "$cpu_dir" ] || continue
	cpu=${cpu_dir##*cpu}
	freq_dir=$cpu_dir/cpufreq
	[ -d "$freq_dir" ] || continue
	governor=$(first_line "$freq_dir/scaling_governor")
	cur=$(first_line "$freq_dir/scaling_cur_freq")
	min=$(first_line "$freq_dir/scaling_min_freq")
	max=$(first_line "$freq_dir/scaling_max_freq")
	printf 'cpu%s:governor=%s,cur=%s,min=%s,max=%s\n' "$cpu" "${governor:-unavailable}" "${cur:-unavailable}" "${min:-unavailable}" "${max:-unavailable}"
done | sort -V | paste -sd';' -)
kv cpu_frequency_state "${frequency_state:-unavailable}"
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

if [ -r /sys/kernel/mm/transparent_hugepage/enabled ]; then
	kv transparent_hugepage_enabled "$(first_line /sys/kernel/mm/transparent_hugepage/enabled)"
fi
if [ -r /sys/kernel/mm/transparent_hugepage/defrag ]; then
	kv transparent_hugepage_defrag "$(first_line /sys/kernel/mm/transparent_hugepage/defrag)"
fi

if command -v docker >/dev/null 2>&1; then
	kv docker_version "$(docker --version 2>/dev/null || true)"
fi

sort "$tmp" > "$out"
