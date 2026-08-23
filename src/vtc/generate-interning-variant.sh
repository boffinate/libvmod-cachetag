#!/bin/sh

set -eu

src=$1
dst=$2
tmp=$dst.tmp

awk '
BEGIN { in_namespace = 0 }
{
	line = $0
	if (index(line, "cachetag.namespace(") != 0)
		in_namespace = 1
	if (in_namespace != 0) {
		close_at = index(line, ");")
		if (close_at != 0) {
			line = substr(line, 1, close_at - 1) ", interning = true" substr(line, close_at)
			in_namespace = 0
		}
	}
	print line
}
END {
	if (in_namespace != 0)
		exit 1
}
' "$src" > "$tmp"

mv "$tmp" "$dst"
