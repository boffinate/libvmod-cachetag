#!/bin/sh
set -eu

root=${1:?usage: capture_persistence_files.sh ROOT OUTPUT}
out=${2:?usage: capture_persistence_files.sh ROOT OUTPUT}

test -d "$root"
tmp="$out.tmp"
trap 'rm -f "$tmp"' EXIT HUP INT TERM

printf 'schema=stream1-persistence-v1\n' > "$tmp"
printf 'file_count=%s\n' "$(find "$root" -type f | wc -l)" >> "$tmp"
printf 'wal_file_count=%s\n' "$(find "$root" -name '*.vtw' -type f | wc -l)" >> "$tmp"
printf 'checkpoint_file_count=%s\n' "$(find "$root" -maxdepth 1 -name 'checkpoint-*.vtc' | wc -l)" >> "$tmp"
printf 'logical_bytes=%s\n' "$(du -sb "$root" | awk '{print $1}')" >> "$tmp"
printf 'allocated_bytes=%s\n' "$(du -sB1 "$root" | awk '{print $1}')" >> "$tmp"
find "$root" -type f -printf 'file\t%P\t%s\n' | sort >> "$tmp"
mv "$tmp" "$out"
