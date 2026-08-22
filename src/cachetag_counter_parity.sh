#!/bin/sh
#-
# SPDX-License-Identifier: MPL-2.0
#
# Counter-surface parity check.
#
# src/cachetag.vsc is the input to vsctool and the authority on the published
# counter surface.  src/cachetag_counters.h is the one inventory the C side is
# generated from: the struct, the VSC publish body and the family fan-out all
# expand from it.  This test fails if the two drift -- an added, removed,
# renamed, reordered, retyped or re-levelled counter on either side, or an
# edited oneliner, produces a diff.
#
# Only the name, order, type, level and oneliner are compared.  Four entries in
# cachetag.vsc carry extra prose paragraphs after the :oneliner: line; that
# prose is documentation for the generated RST and lives in cachetag.vsc alone.
# The extraction below ignores indented continuation lines on purpose.  Do not
# "fix" that.
#
# Environment (set from src/Makefile.am's AM_TESTS_ENVIRONMENT):
#   CACHETAG_SRCDIR        directory holding cachetag.vsc (VPATH-safe)
#   CACHETAG_COUNTER_DUMP  path to the built cachetag_counter_dump binary

set -e

srcdir="${CACHETAG_SRCDIR:-.}"
vsc="$srcdir/cachetag.vsc"
dump="${CACHETAG_COUNTER_DUMP:-./cachetag_counter_dump}"

if [ ! -f "$vsc" ]; then
	echo "cachetag_counter_parity: $vsc not found (set CACHETAG_SRCDIR)" >&2
	exit 1
fi

if [ ! -x "$dump" ]; then
	echo "cachetag_counter_parity: $dump missing or not executable" \
	    "(set CACHETAG_COUNTER_DUMP)" >&2
	exit 1
fi

tmp=`mktemp -d` || exit 1
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

"$dump" > "$tmp/got.txt"

awk '
function flush() {
	if (!have)
		return
	if (name == "" || type == "" || level == "" || oneliner == "") {
		printf "cachetag_counter_parity: incomplete entry \"%s\": type=\"%s\" level=\"%s\" oneliner=\"%s\"\n", name, type, level, oneliner > "/dev/stderr"
		bad = 1
	}
	printf "%s\t%s\t%s\t%s\n", name, type, level, oneliner
	have = 0
}
/^\.\. vinyl_vsc:: / {
	flush()
	name = $0
	sub(/^\.\. vinyl_vsc:: [ \t]*/, "", name)
	type = ""
	level = ""
	oneliner = ""
	have = 1
	next
}
/^\.\. vinyl_vsc_begin::/ {
	flush()
	have = 0
	next
}
have && /^[ \t]*:type:/ {
	v = $0
	sub(/^[ \t]*:type:[ \t]*/, "", v)
	type = v
	next
}
have && /^[ \t]*:level:/ {
	v = $0
	sub(/^[ \t]*:level:[ \t]*/, "", v)
	level = v
	next
}
have && /^[ \t]*:oneliner:/ {
	v = $0
	sub(/^[ \t]*:oneliner:[ \t]*/, "", v)
	oneliner = v
	next
}
END {
	flush()
	if (bad)
		exit 1
}
' "$vsc" > "$tmp/want.txt"

if ! diff -u "$tmp/want.txt" "$tmp/got.txt"; then
	echo "cachetag_counter_parity: cachetag.vsc and cachetag_counters.h" \
	    "disagree (-want: cachetag.vsc, +got: cachetag_counters.h)" >&2
	exit 1
fi

n=`wc -l < "$tmp/want.txt" | tr -d ' '`
echo "counter parity OK: $n counters"
