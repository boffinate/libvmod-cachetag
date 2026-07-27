#!/bin/sh
# Build-provenance record/verify for the benchmark build cache (rules/BR-016).
#
# A SKIP_BUILD=1 benchmark run must prove the cached build was compiled from
# the sources it claims to measure. `record` hashes the build inputs at build
# time; `verify` recomputes the hashes at reuse time and fails loud on any
# mismatch so a stale or wrong-arm build cache cannot silently poison a row.
#
# Usage:
#   build_provenance.sh record <cachetag-src> <vinyl-src> <slash-src|none> <storage-kind> <out-file>
#   build_provenance.sh verify <cachetag-src> <vinyl-src> <slash-src|none> <storage-kind> <provenance-file>
#
# verify honors ALLOW_STALE_BUILD=1 to downgrade failures to warnings for a
# deliberate, explicitly acknowledged reuse of a stale build.
set -eu

sha() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum
	else
		shasum -a 256
	fi
}

# Content hash of the compiled inputs under a source tree. Only paths that
# feed the container build are hashed, so docs/harness-only edits do not
# invalidate a build cache; build outputs are pruned so a dirty host tree
# hashes the same as its clean copy.
tree_hash() {
	dir=$1
	shift
	(
		cd "$dir"
		paths=""
		for candidate in "$@"; do
			if [ -e "$candidate" ]; then
				paths="$paths $candidate"
			fi
		done
		if [ -z "$paths" ]; then
			echo "none"
			return 0
		fi
		# shellcheck disable=SC2086
		find $paths \
			\( -name .libs -o -name .deps -o -name .git -o -name autom4te.cache \) -prune -o \
			-type f \
			! -name "*.o" ! -name "*.lo" ! -name "*.la" ! -name "*.so" \
			! -name "Makefile" ! -name "Makefile.in" ! -name "*.pyc" \
			-print | LC_ALL=C sort | tr "\n" "\0" | xargs -0 cat | sha | cut -d" " -f1
	)
}

CACHETAG_HASH_PATHS="src configure.ac Makefile.am bootstrap patches"
VINYL_HASH_PATHS="bin lib include vmod configure.ac Makefile.am"
SLASH_HASH_PATHS="src include configure.ac Makefile.am bootstrap"

mode=${1:?mode (record|verify) required}
cachetag_src=${2:?cachetag source dir required}
vinyl_src=${3:?vinyl source dir required}
slash_src=${4:?slash source dir or none required}
storage_kind=${5:?storage kind required}
provenance_file=${6:?provenance file path required}

# shellcheck disable=SC2086
cachetag_hash=$(tree_hash "$cachetag_src" $CACHETAG_HASH_PATHS)
# shellcheck disable=SC2086
vinyl_hash=$(tree_hash "$vinyl_src" $VINYL_HASH_PATHS)
if [ "$slash_src" != none ]; then
	# shellcheck disable=SC2086
	slash_hash=$(tree_hash "$slash_src" $SLASH_HASH_PATHS)
else
	slash_hash=none
fi

case "$mode" in
record)
	{
		echo "build_provenance_version=1"
		echo "build_time_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		echo "build_storage_kind=$storage_kind"
		echo "build_slash_built=$([ "$slash_hash" != none ] && echo 1 || echo 0)"
		echo "cachetag_build_input_sha256=$cachetag_hash"
		echo "vinyl_build_input_sha256=$vinyl_hash"
		echo "slash_build_input_sha256=$slash_hash"
	} > "$provenance_file"
	echo "build provenance recorded: $provenance_file"
	;;
verify)
	allow=${ALLOW_STALE_BUILD:-0}
	stale=0
	fail() {
		echo "BUILD PROVENANCE MISMATCH [BR-016]: $1" >&2
		if [ "$allow" = 1 ]; then
			stale=1
			echo "ALLOW_STALE_BUILD=1 set: continuing with a stale build cache; label this row stale-build in the campaign note." >&2
		else
			echo "The build cache does not match the sources this row claims to measure (benchmarks/rules/BR-016-build-cache-contamination.md)." >&2
			echo "Rerun without SKIP_BUILD=1 to rebuild, or set CACHE_TAG_ALLOW_STALE_BUILD=1 for a deliberate stale reuse." >&2
			exit 1
		fi
	}
	if [ ! -f "$provenance_file" ]; then
		fail "no provenance file at $provenance_file (build cache predates provenance recording)"
	fi
	if [ -f "$provenance_file" ]; then
		recorded_cachetag=$(sed -n "s/^cachetag_build_input_sha256=//p" "$provenance_file")
		recorded_vinyl=$(sed -n "s/^vinyl_build_input_sha256=//p" "$provenance_file")
		recorded_slash=$(sed -n "s/^slash_build_input_sha256=//p" "$provenance_file")
		recorded_slash_built=$(sed -n "s/^build_slash_built=//p" "$provenance_file")
		if [ "$recorded_cachetag" != "$cachetag_hash" ]; then
			fail "cachetag build inputs changed since the cached build (recorded ${recorded_cachetag:-absent}, current $cachetag_hash)"
		fi
		if [ "$recorded_vinyl" != "$vinyl_hash" ]; then
			fail "vinyl build inputs changed since the cached build (recorded ${recorded_vinyl:-absent}, current $vinyl_hash)"
		fi
		case "$storage_kind" in
		fellow|buddy)
			if [ "$recorded_slash_built" != 1 ]; then
				fail "storage kind $storage_kind requested but the cached build did not build Slash"
			fi
			if [ "$slash_hash" != none ] && [ "$recorded_slash" != "$slash_hash" ]; then
				fail "slash build inputs changed since the cached build (recorded ${recorded_slash:-absent}, current $slash_hash)"
			fi
			;;
		esac
		if [ "$stale" = 0 ]; then
			echo "build provenance verified: $provenance_file"
		fi
	fi
	;;
*)
	echo "unknown mode: $mode (expected record or verify)" >&2
	exit 2
	;;
esac
