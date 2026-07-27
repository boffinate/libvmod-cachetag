#!/bin/sh
set -eu

cycle=$1
stats_path=$2
memory_path=$3
snapshot_path=$4
tripwire_required=$5

index_bytes=$(awk '$1 == "CACHETAG.vcl1_tags_bench.index_memory_bytes" {print $2; exit}' "$stats_path")
if test -z "$index_bytes" && test "$tripwire_required" -eq 0; then
	index_bytes=0
fi
case "$index_bytes" in
	''|*[!0-9]*)
		echo "invalid index_memory_bytes=$index_bytes" >&2
		exit 1
		;;
esac

vinyltest_pid=
for proc in /proc/[0-9]*; do
	test -r "$proc/comm" || continue
	comm=$(cat "$proc/comm")
	if test "$comm" = vinyltest; then
		vinyltest_pid=$(basename "$proc")
		break
	fi
done
if test -z "$vinyltest_pid"; then
	echo "missing vinyltest process" >&2
	exit 1
fi

selected_pid=
selected_rss=0
selected_ppid=
selected_comm=
selected_exe=
fault_state_path=$(dirname "$snapshot_path")/phase6_faults.previous
{
	echo "cycle=$cycle"
	printf 'index_memory_bytes=%s\n' "$index_bytes"
	printf 'vinyltest_pid=%s\n' "$vinyltest_pid"
	for proc in /proc/[0-9]*; do
		test -r "$proc/comm" || continue
		pid=$(basename "$proc")
		comm=$(cat "$proc/comm")
		exe=$(readlink "$proc/exe" 2>/dev/null || true)
		exe_base=$(basename "$exe")
		if test "$comm" != vinyld && test "$exe_base" != vinyld; then
			continue
		fi
		test -r "$proc/status" || continue
		ppid=$(awk '/^PPid:/ {print $2; exit}' "$proc/status" || true)
		rss=$(awk '/^VmRSS:/ {print $2; exit}' "$proc/status" || true)
		case "$ppid" in
			''|*[!0-9]*) ppid=0;;
		esac
		case "$rss" in
			''|*[!0-9]*) rss=0;;
		esac

		in_tree=0
		ancestor=$ppid
		hops=0
		while test "$ancestor" -gt 1 && test "$hops" -lt 64; do
			if test "$ancestor" = "$vinyltest_pid"; then
				in_tree=1
				break
			fi
			parent_status=/proc/$ancestor/status
			test -r "$parent_status" || break
			next=$(awk '/^PPid:/ {print $2; exit}' "$parent_status" || true)
			test -n "$next" || break
			test "$next" != "$ancestor" || break
			ancestor=$next
			hops=$((hops + 1))
		done

		printf 'candidate_pid=%s candidate_ppid=%s candidate_comm=%s candidate_vmrss_kb=%s candidate_in_vinyltest_tree=%s candidate_exe=%s\n' \
			"$pid" "$ppid" "$comm" "$rss" "$in_tree" "$exe"
		if test "$in_tree" -eq 1 && test -r "$proc/smaps_rollup" && test "$rss" -ge "$selected_rss"; then
			selected_pid=$pid
			selected_rss=$rss
			selected_ppid=$ppid
			selected_comm=$comm
			selected_exe=$exe
		fi
	done

	if test -z "$selected_pid"; then
		echo "missing vinyld descendant" >&2
		exit 1
	fi
	printf 'selected_pid=%s\nselected_ppid=%s\nselected_comm=%s\nselected_exe=%s\nselected_vmrss_kb=%s\n' \
		"$selected_pid" "$selected_ppid" "$selected_comm" "$selected_exe" "$selected_rss"
	printf 'allocator_environment='
	tr '\000' '\n' < "/proc/$selected_pid/environ" 2>/dev/null |
		awk -F= '$1 == "MALLOC_CONF" || $1 == "MALLOC_ARENA_MAX" || $1 == "MALLOC_TRIM_THRESHOLD_" {printf "%s%s=%s", sep, $1, substr($0, length($1) + 2); sep=","}'
	printf '\n'

	if test "$tripwire_required" -eq 1; then
		required_rss=$(( (index_bytes + 1023) / 1024 ))
		if test "$selected_rss" -lt "$required_rss"; then
			printf 'tripwire=fail required_rss_kb=%s selected_vmrss_kb=%s\n' "$required_rss" "$selected_rss" >&2
			exit 1
		fi
		printf 'tripwire=pass required_rss_kb=%s selected_vmrss_kb=%s\n' "$required_rss" "$selected_rss"
	else
		printf 'tripwire=not-required\n'
	fi

	awk '/^VmRSS:/ {print}' "/proc/$selected_pid/status"
	cat "/proc/$selected_pid/smaps_rollup"

	set -- $(awk '{print $10, $12}' "/proc/$selected_pid/stat")
	proc_minflt=$1
	proc_majflt=$2
	cgroup_memory_stat=
	for candidate in /sys/fs/cgroup/memory.stat /sys/fs/cgroup/memory/memory.stat; do
		if test -r "$candidate"; then
			cgroup_memory_stat=$candidate
			break
		fi
	done
	cgroup_pgfault=0
	cgroup_pgmajfault=0
	cgroup_pglazyfree=0
	cgroup_pglazyfreed=0
	if test -n "$cgroup_memory_stat"; then
		cgroup_pgfault=$(awk '$1 == "pgfault" {print $2; exit}' "$cgroup_memory_stat")
		cgroup_pgmajfault=$(awk '$1 == "pgmajfault" {print $2; exit}' "$cgroup_memory_stat")
		cgroup_pglazyfree=$(awk '$1 == "pglazyfree" {print $2; exit}' "$cgroup_memory_stat")
		cgroup_pglazyfreed=$(awk '$1 == "pglazyfreed" {print $2; exit}' "$cgroup_memory_stat")
	fi
	: "${cgroup_pgfault:=0}" "${cgroup_pgmajfault:=0}" "${cgroup_pglazyfree:=0}" "${cgroup_pglazyfreed:=0}"
	printf 'proc_minflt=%s\nproc_majflt=%s\n' "$proc_minflt" "$proc_majflt"
	printf 'cgroup_memory_stat_path=%s\n' "$cgroup_memory_stat"
	printf 'cgroup_pgfault=%s\ncgroup_pgmajfault=%s\ncgroup_pglazyfree=%s\ncgroup_pglazyfreed=%s\n' \
		"$cgroup_pgfault" "$cgroup_pgmajfault" "$cgroup_pglazyfree" "$cgroup_pglazyfreed"
	if test -r "$fault_state_path"; then
		. "$fault_state_path"
		printf 'proc_minflt_delta=%s\nproc_majflt_delta=%s\n' \
			"$((proc_minflt - previous_proc_minflt))" "$((proc_majflt - previous_proc_majflt))"
		printf 'cgroup_pgfault_delta=%s\ncgroup_pgmajfault_delta=%s\ncgroup_pglazyfree_delta=%s\ncgroup_pglazyfreed_delta=%s\n' \
			"$((cgroup_pgfault - previous_cgroup_pgfault))" \
			"$((cgroup_pgmajfault - previous_cgroup_pgmajfault))" \
			"$((cgroup_pglazyfree - previous_cgroup_pglazyfree))" \
			"$((cgroup_pglazyfreed - previous_cgroup_pglazyfreed))"
	else
		printf 'proc_minflt_delta=baseline\nproc_majflt_delta=baseline\n'
		printf 'cgroup_pgfault_delta=baseline\ncgroup_pgmajfault_delta=baseline\ncgroup_pglazyfree_delta=baseline\ncgroup_pglazyfreed_delta=baseline\n'
	fi
} > "$memory_path"

cat "/proc/$selected_pid/smaps" > "$memory_path.smaps"

{
	printf 'previous_proc_minflt=%s\n' "$proc_minflt"
	printf 'previous_proc_majflt=%s\n' "$proc_majflt"
	printf 'previous_cgroup_pgfault=%s\n' "$cgroup_pgfault"
	printf 'previous_cgroup_pgmajfault=%s\n' "$cgroup_pgmajfault"
	printf 'previous_cgroup_pglazyfree=%s\n' "$cgroup_pglazyfree"
	printf 'previous_cgroup_pglazyfreed=%s\n' "$cgroup_pglazyfreed"
} > "$fault_state_path"

touch "$snapshot_path"
