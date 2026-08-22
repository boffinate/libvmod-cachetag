#!/bin/sh
# Capture one explicit benchmark endpoint for the provenanced cache-main PID.
#
# Arguments: endpoint output-prefix.  The Phase 6 helper owns its historical
# cycle/fault schema; this endpoint helper is intentionally independent so
# post-load, post-load-confirmation, post-warm diagnostic, and lifecycle
# endpoints all carry the same identity fields.
set -eu

endpoint=$1
prefix=$2
case "$endpoint" in
	post_load|post_load_confirmation|post_warm_diagnostic|post_invalidation|lifecycle) ;;
	*) echo "unknown cache-main endpoint: $endpoint" >&2; exit 2;;
esac

result=${prefix}.${endpoint}.cache-main
stats=${result}.identity
rollup=${result}.smaps_rollup
maps=${result}.maps
smaps=${result}.smaps

vinyltest_pid=
for proc in /proc/[0-9]*; do
	test -r "$proc/comm" || continue
	test "$(cat "$proc/comm")" = vinyltest || continue
	vinyltest_pid=${proc##*/}
	break
done
test -n "$vinyltest_pid" || { echo "missing vinyltest process" >&2; exit 1; }

selected_pid=
for proc in /proc/[0-9]*; do
	test -r "$proc/comm" && test -r "$proc/exe" || continue
	test "$(cat "$proc/comm")" = cache-main || continue
	test "$(basename "$(readlink "$proc/exe")")" = vinyld || continue
	pid=${proc##*/}
	ancestor=$(awk '/^PPid:/ {print $2; exit}' "$proc/status")
	for hops in $(seq 1 64); do
		test "$ancestor" -gt 1 || break
		if test "$ancestor" = "$vinyltest_pid"; then selected_pid=$pid; break 2; fi
		ancestor=$(awk '/^PPid:/ {print $2; exit}' "/proc/$ancestor/status" 2>/dev/null || echo 0)
	done
done
test -n "$selected_pid" || { echo "missing cache-main descendant" >&2; exit 1; }

starttime=$(awk '{print $22; exit}' "/proc/$selected_pid/stat")
boot_id=$(cat /proc/sys/kernel/random/boot_id)
test -n "$starttime" && test -n "$boot_id" || { echo "incomplete cache-main identity" >&2; exit 1; }
{
	printf 'schema=cache-main-memory-v1\nendpoint=%s\nvinyltest_pid=%s\nselected_pid=%s\n' "$endpoint" "$vinyltest_pid" "$selected_pid"
	printf 'selected_starttime_ticks=%s\nselected_comm=cache-main\nselected_exe=%s\nboot_id=%s\nidentity_valid=1\n' \
		"$starttime" "$(readlink "/proc/$selected_pid/exe")" "$boot_id"
	awk '/^PPid:|^VmRSS:|^Cpus_allowed_list:/ {print}' "/proc/$selected_pid/status"
} > "$stats"
cat "/proc/$selected_pid/smaps_rollup" > "$rollup"
cat "/proc/$selected_pid/maps" > "$maps"
cat "/proc/$selected_pid/smaps" > "$smaps"

end_starttime=$(awk '{print $22; exit}' "/proc/$selected_pid/stat" 2>/dev/null || true)
end_comm=$(cat "/proc/$selected_pid/comm" 2>/dev/null || true)
end_exe=$(basename "$(readlink "/proc/$selected_pid/exe" 2>/dev/null || true)")
if test "$end_starttime" != "$starttime" || test "$end_comm" != cache-main || test "$end_exe" != vinyld; then
	printf 'identity_valid=0\nidentity_post_capture_reason=pid_identity_changed\n' >> "$stats"
	echo "cache-main identity changed during endpoint capture" >&2
	exit 1
fi
printf 'identity_post_capture_valid=1\n' >> "$stats"
