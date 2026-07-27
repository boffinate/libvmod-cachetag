#!/bin/sh
set -eu

usage() {
	cat <<'EOF'
Usage:
  scripts/render-remote-differential-profile.sh user@host CACHE_RESULT_DIR XKEY_RESULT_DIR [RUN]

Generate remote post-processing artifacts from two benchmark result directories
that each contain one perf.data file and a symbols/ tree. The script writes:

  - perf diff text
  - individual folded stacks
  - individual flamegraphs
  - differential folded stacks
  - differential flamegraph
  - perf annotate reports for a default symbol list

The result directories must exist on the remote host. RUN defaults to 1 and is
used only in the output directory label.

Environment:
  CACHE_TAG_REMOTE_DIR        remote workspace root for FlameGraph lookup
                              (default: cachetag-bench)
  CACHE_TAG_ANNOTATE_SYMBOLS  comma-separated symbol list for perf annotate
                              (default: cachetag_find_locked,cachetag_mem_key_get,strcmp,pthread_mutex_lock,pthread_mutex_unlock,malloc,_int_malloc)
EOF
}

quote() {
	printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

target=${1:-}
cache_dir=${2:-}
xkey_dir=${3:-}
run=${4:-1}
remote_dir=${CACHE_TAG_REMOTE_DIR:-cachetag-bench}
annotate_symbols=${CACHE_TAG_ANNOTATE_SYMBOLS:-cachetag_find_locked,cachetag_mem_key_get,strcmp,pthread_mutex_lock,pthread_mutex_unlock,malloc,_int_malloc}

if [ -z "$target" ] || [ -z "$cache_dir" ] || [ -z "$xkey_dir" ]; then
	usage >&2
	exit 2
fi

ssh "$target" "REMOTE_DIR=$(quote "$remote_dir") CACHE_DIR=$(quote "$cache_dir") XKEY_DIR=$(quote "$xkey_dir") RUN_LABEL=$(quote "$run") ANNOTATE_SYMBOLS=$(quote "$annotate_symbols"); if [ \"\${REMOTE_DIR#/}\" = \"\$REMOTE_DIR\" ]; then REMOTE_DIR=\$HOME/\$REMOTE_DIR; fi; export REMOTE_DIR CACHE_DIR XKEY_DIR RUN_LABEL ANNOTATE_SYMBOLS; sh -s" <<'EOF'
set -eu

prime_perf_buildid_cache() {
	symbols_dir=$1
	if [ ! -d "$symbols_dir" ] || ! command -v perf >/dev/null 2>&1; then
		return
	fi
	find "$symbols_dir" -type f -print | sort | while read -r path; do
		case "$(file -b "$path" 2>/dev/null || true)" in
			ELF*)
				perf buildid-cache -a "$path" >/dev/null 2>&1 || true
				;;
		esac
	done
}

find_perf_data() {
	dir=$1
	find "$dir" -maxdepth 1 -name '*.perf.data' -type f | sort | head -n 1
}

cache_perf=$(find_perf_data "$CACHE_DIR")
xkey_perf=$(find_perf_data "$XKEY_DIR")

if [ -z "$cache_perf" ] || [ ! -f "$cache_perf" ]; then
	echo "missing perf.data in cachetag result dir: $CACHE_DIR" >&2
	exit 1
fi
if [ -z "$xkey_perf" ] || [ ! -f "$xkey_perf" ]; then
	echo "missing perf.data in xkey result dir: $XKEY_DIR" >&2
	exit 1
fi

flamegraph_dir=$REMOTE_DIR/tools/FlameGraph
if [ ! -x "$flamegraph_dir/flamegraph.pl" ] || [ ! -x "$flamegraph_dir/stackcollapse-perf.pl" ] || [ ! -x "$flamegraph_dir/difffolded.pl" ]; then
	echo "FlameGraph tools not found under $flamegraph_dir" >&2
	exit 1
fi

prime_perf_buildid_cache "$CACHE_DIR/symbols"
prime_perf_buildid_cache "$XKEY_DIR/symbols"

analysis_dir="$CACHE_DIR/analysis-vs-$(basename "$XKEY_DIR")-run-$RUN_LABEL"
mkdir -p "$analysis_dir"

perf diff --force --sort comm,dso,symbol "$xkey_perf" "$cache_perf" \
	> "$analysis_dir/perf-diff.txt" 2> "$analysis_dir/perf-diff.err" || true

perf script -i "$cache_perf" 2> "$analysis_dir/cachetag.perf-script.err" | \
	"$flamegraph_dir/stackcollapse-perf.pl" > "$analysis_dir/cachetag.folded"
perf script -i "$xkey_perf" 2> "$analysis_dir/xkey.perf-script.err" | \
	"$flamegraph_dir/stackcollapse-perf.pl" > "$analysis_dir/xkey.folded"

"$flamegraph_dir/flamegraph.pl" "$analysis_dir/cachetag.folded" \
	> "$analysis_dir/cachetag.svg"
"$flamegraph_dir/flamegraph.pl" "$analysis_dir/xkey.folded" \
	> "$analysis_dir/xkey.svg"
"$flamegraph_dir/difffolded.pl" "$analysis_dir/xkey.folded" \
	"$analysis_dir/cachetag.folded" > "$analysis_dir/diff.folded"
"$flamegraph_dir/flamegraph.pl" --color=diff "$analysis_dir/diff.folded" \
	> "$analysis_dir/diff.svg"

old_ifs=$IFS
IFS=,
set -- $ANNOTATE_SYMBOLS
IFS=$old_ifs
for sym in "$@"; do
	perf annotate --stdio -i "$cache_perf" --symbol "$sym" \
		> "$analysis_dir/cachetag.annotate.$sym.txt" \
		2> "$analysis_dir/cachetag.annotate.$sym.err" || true
	perf annotate --stdio -i "$xkey_perf" --symbol "$sym" \
		> "$analysis_dir/xkey.annotate.$sym.txt" \
		2> "$analysis_dir/xkey.annotate.$sym.err" || true
done

printf '%s\n' "$analysis_dir"
EOF
