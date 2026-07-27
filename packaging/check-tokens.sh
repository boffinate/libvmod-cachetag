#!/bin/sh
#
# check-tokens.sh -- validate substitution tokens in the packaging templates.
#
# Two modes:
#
#   check-tokens.sh --templates [PATH...]
#       Every @TOKEN@ found must be declared below. Catches typos and
#       undocumented tokens in the checked-in templates. Defaults to the
#       packaging tree next to this script.
#
#   check-tokens.sh --substituted PATH...
#       No @TOKEN@ may remain. Run this on the generated packaging tree
#       immediately before dpkg-buildpackage or rpmbuild, so an unsubstituted
#       template can never reach a build.
#
#   check-tokens.sh --list
#       Print the declared token names.
#
# This script is deliberately self-contained: the manifest-to-metadata
# generator lives elsewhere and owns the substitution itself.

set -eu

TOKENS='
COHORT_ID
CACHETAG_VERSION
PACKAGE_REVISION
VINYL_PACKAGE_VERSION
VINYL_STRICT_ABI
VINYL_VRT
VINYL_VMODDIR
SOURCE_URL
MAINTAINER_NAME
MAINTAINER_EMAIL
DEBIAN_VERSION
DEBIAN_DISTRIBUTION
DEBIAN_DATE
RPM_CHANGELOG_DATE
'

self_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

usage() {
	echo "usage: $0 --templates [PATH...] | --substituted PATH... | --list" >&2
	exit 2
}

# Print every distinct @TOKEN@ occurrence as "file:token".
scan() {
	find "$@" -type f ! -name 'check-tokens.sh' -print |
	while read -r file; do
		grep -o '@[A-Z0-9_]\{1,\}@' "$file" 2>/dev/null |
		sed 's/^@//; s/@$//' |
		sort -u |
		sed "s|^|$file:|"
	done
}

declared() {
	echo "$TOKENS" | grep -v '^$'
}

mode=${1:---templates}
[ $# -gt 0 ] && shift

case "$mode" in
--list)
	declared
	;;
--templates)
	[ $# -eq 0 ] && set -- "$self_dir"
	status=0
	scan "$@" | while IFS=: read -r file token; do
		if ! declared | grep -qx "$token"; then
			echo "E: $file: undeclared token @$token@" >&2
			status=1
		fi
		[ "$status" -eq 0 ] || exit 1
	done || status=1
	if [ "$status" -eq 0 ]; then
		echo "OK: all tokens in $* are declared"
	fi
	exit "$status"
	;;
--substituted)
	[ $# -eq 0 ] && usage
	found=$(scan "$@" || true)
	if [ -n "$found" ]; then
		echo "E: unsubstituted tokens remain:" >&2
		echo "$found" | sed 's/^/E:   /' >&2
		exit 1
	fi
	echo "OK: no unsubstituted tokens in $*"
	;;
*)
	usage
	;;
esac
