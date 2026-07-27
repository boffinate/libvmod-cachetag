#!/bin/sh
set -eu

usage() {
	cat <<'EOF'
Usage: scripts/test-with-vinyl-cache.sh [VINYL_CACHE_SRC]

Build a compatible Vinyl Cache checkout in Docker, install it into a temporary
prefix, then build and test this repository as a standalone VMOD against that
installed Vinyl development surface. This script does not copy files into the
Vinyl source tree.

Environment:
  VINYL_CACHE_SRC       Vinyl Cache source checkout (default: ../vinyl-cache)
  VINYL_DOCKER_IMAGE    Docker image with Vinyl build dependencies
                        (default: vinyl-cache-ubuntu-build)
  CACHE_TAG_BUILD_PROFILE
                        Vinyl/VMOD build profile (default: diagnostic)
                          diagnostic  developer warnings, -O0 debug symbols,
                                      stack protector deliberately OFF,
                                      persistent storage built
                          production  distro-style hardening: stack protector,
                                      FORTIFY_SOURCE, full RELRO, PIE, -O2,
                                      no developer-only warning policy, and a
                                      mandatory ELF hardening inspection
  CACHE_TAG_CHECK_TARGET make target to run after bootstrap (default: distcheck)
  CACHE_TAG_TESTS       optional TESTS override for make check, for example:
                        vtc/cachetag_c00000.vtc
  CACHE_TAG_FAILURE_LOG_LINES
                        src/test-suite.log lines to print on failure
                        (default: 260)
EOF
}

case "${1:-}" in
	-h|--help)
		usage
		exit 0
		;;
esac

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
default_src="$repo_dir/../vinyl-cache"
vinyl_src=${1:-${VINYL_CACHE_SRC:-$default_src}}
vinyl_src=$(CDPATH= cd -- "$vinyl_src" && pwd)

image=${VINYL_DOCKER_IMAGE:-vinyl-cache-ubuntu-build}
check_target=${CACHE_TAG_CHECK_TARGET:-distcheck}
cachetag_tests=${CACHE_TAG_TESTS:-}
failure_log_lines=${CACHE_TAG_FAILURE_LOG_LINES:-260}
build_profile=${CACHE_TAG_BUILD_PROFILE:-diagnostic}

case "$build_profile" in
	diagnostic|production)
		;;
	*)
		printf 'unknown CACHE_TAG_BUILD_PROFILE: %s (expected diagnostic or production)\n' \
			"$build_profile" >&2
		exit 2
		;;
esac

docker run --rm \
	-v "$vinyl_src:/vinyl-src:ro" \
	-v "$repo_dir:/cachetag-host:ro" \
	-e "CACHE_TAG_BUILD_PROFILE=$build_profile" \
	-e "CACHE_TAG_CHECK_TARGET=$check_target" \
	-e "CACHE_TAG_TESTS=$cachetag_tests" \
	-e "CACHE_TAG_FAILURE_LOG_LINES=$failure_log_lines" \
	"$image" \
	bash -lc '
set -euo pipefail

prefix=/tmp/vinyl-prefix
vinyl_build=/tmp/vinyl-build
vinyl_src_copy=/tmp/vinyl-src-copy
cachetag_src=/tmp/cachetag-src

rm -rf "$prefix" "$vinyl_build" "$vinyl_src_copy" "$cachetag_src"
mkdir -p "$prefix" "$vinyl_build" "$vinyl_src_copy" "$cachetag_src"

#
# Build profiles.
#
# The two profiles are built from two separate literal flag lists. The
# production branch never derives its arguments from the diagnostic list, and
# never edits a shared string, so a diagnostic-only option such as
# --disable-stack-protector cannot leak into a production build by accident.
#
# Note on Vinyl configure.ac: its stack-protector options do NOT mean what they
# look like, in either direction.
#
#   DEVELOPER_CFLAGS is seeded from wflags.py, whose DESIRABLE_OPTIONS already
#   contain -fstack-protector-strong, and DEVELOPER_CFLAGS reaches CFLAGS only
#   when --enable-developer-warnings is given. --enable-stack-protector (default
#   yes) merely appends a further -fstack-protector to that same variable.
#
# Consequences:
#   * diagnostic profile: --disable-stack-protector is effectively a NO-OP,
#     because --enable-developer-warnings pulls in wflags.py, which has already
#     added -fstack-protector-strong.
#   * a naive production profile that just drops --disable-stack-protector and
#     --enable-developer-warnings gets NO stack protector from Vinyl at all,
#     while configure still reports the stack protector as enabled.
#
# So the safe-looking configuration is the unhardened one. The production
# profile therefore sets the hardening flags explicitly in CFLAGS/CPPFLAGS/
# LDFLAGS, the way a distro package build (dpkg-buildflags / redhat-rpm-config)
# does, and then verifies the result by inspecting the built ELF objects, which
# is the only check that neither configure nor the toolchain default can fool.
#
declare -a vinyl_configure_args
profile_cppflags=""
profile_cflags=""
profile_ldflags=""
hardening_inspection=0

probe_cc_flag() {
	local flag=$1
	printf "int main(void){return 0;}\n" > /tmp/cc-probe.c
	gcc -Werror "$flag" -o /tmp/cc-probe.out /tmp/cc-probe.c >/dev/null 2>&1
}

case "$CACHE_TAG_BUILD_PROFILE" in
diagnostic)
	# Historic harness configuration, unchanged. No CFLAGS/LDFLAGS override:
	# autoconf and Vinyl configure.ac pick their own values exactly as before.
	vinyl_configure_args=(
		"--prefix=$prefix"
		--with-unwind
		--enable-developer-warnings
		--enable-debugging-symbols
		--disable-stack-protector
		--with-persistent-storage
	)
	;;
production)
	# Distro package configuration.
	#
	# --with-unwind is kept: it is a production panic-backtrace facility, not a
	#   debug-only toggle, and Vinyl auto-detects it. Passing it explicitly makes
	#   the built feature set a declared input instead of a silent function of
	#   what happens to be installed in the buildroot.
	# --with-persistent-storage is dropped: it is upstream-deprecated, no distro
	#   ships it, the first package milestone is Default-storage-only, and no
	#   test in the blocking suite uses it.
	# Deliberately absent: --enable-developer-warnings, --enable-debugging-symbols
	#   (which would force -O0 -fno-inline), and --disable-stack-protector.
	vinyl_configure_args=(
		"--prefix=$prefix"
		--with-unwind
	)

	profile_cppflags="-Wdate-time -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=3"
	profile_cflags="-g -O2 -fstack-protector-strong -Wformat -Werror=format-security"
	profile_ldflags="-Wl,-z,relro -Wl,-z,now"

	# Architecture-dependent hardening, probed rather than assumed.
	for opt_flag in -fstack-clash-protection -mbranch-protection=standard -fcf-protection=full; do
		if probe_cc_flag "$opt_flag"; then
			profile_cflags="$profile_cflags $opt_flag"
		else
			printf "hardening: compiler does not support %s, skipping\n" "$opt_flag"
		fi
	done

	# PIE is left to the toolchain default (Ubuntu gcc is --enable-default-pie).
	# Forcing -pie into a global LDFLAGS breaks libtool shared-library links, so
	# this is the same choice dpkg-buildflags makes on PIE-by-default targets:
	# rely on the default, then verify it by ELF inspection below.
	hardening_inspection=1
	;;
esac

printf "\n===== build profile: %s =====\n" "$CACHE_TAG_BUILD_PROFILE"
printf "vinyl configure command:\n  %s/configure" "$vinyl_src_copy"
printf " %s" "${vinyl_configure_args[@]}"
printf "\n"
printf "profile CPPFLAGS: %s\n" "${profile_cppflags:-<unset, autoconf default>}"
printf "profile CFLAGS:   %s\n" "${profile_cflags:-<unset, autoconf default>}"
printf "profile LDFLAGS:  %s\n" "${profile_ldflags:-<unset, autoconf default>}"
printf "hardening inspection: %s\n\n" \
	"$([ "$hardening_inspection" -eq 1 ] && echo enabled || echo "skipped (diagnostic profile makes no hardening guarantees)")"

report_effective_flags() {
	local label=$1 makefile=$2
	printf "\n----- effective build flags: %s -----\n" "$label"
	if [ -f "$makefile" ]; then
		grep -E "^(CC|CFLAGS|CPPFLAGS|LDFLAGS|WFLAGS|AM_CFLAGS|AM_LDFLAGS) *=" "$makefile" || true
	else
		printf "no makefile at %s\n" "$makefile"
	fi
	printf -- "----- end effective build flags: %s -----\n\n" "$label"
}

tar -C /vinyl-src -cf - . | tar -C "$vinyl_src_copy" -xf -

cd "$vinyl_build"
(
	cd "$vinyl_src_copy"
	sh ./autogen.sh
)
if [ "$CACHE_TAG_BUILD_PROFILE" = production ]; then
	CPPFLAGS="$profile_cppflags" \
	CFLAGS="$profile_cflags" \
	LDFLAGS="$profile_ldflags" \
	"$vinyl_src_copy"/configure "${vinyl_configure_args[@]}"
else
	"$vinyl_src_copy"/configure "${vinyl_configure_args[@]}"
fi
report_effective_flags "vinyl" "$vinyl_build/Makefile"
make -j"$(nproc)"
make install

tar -C /cachetag-host \
	--exclude=.git \
	--exclude=devdocs \
	--exclude=Makefile \
	--exclude=Makefile.in \
	--exclude=aclocal.m4 \
	--exclude=autom4te.cache \
	--exclude=benchmarks/remote-results \
	--exclude=benchmarks/results \
	--exclude=build-aux \
	--exclude=config.h \
	--exclude=config.h.in \
	--exclude=config.log \
	--exclude=config.status \
	--exclude=configure \
	--exclude="configure~" \
	--exclude=.deps \
	--exclude=.libs \
	--exclude="_build" \
	--exclude=libtool \
	--exclude="libvmod-cachetag-*" \
	--exclude=m4 \
	--exclude=tests \
	--exclude="*.la" \
	--exclude="*.lo" \
	--exclude="*.o" \
	--exclude="*.tar.gz" \
	--exclude=vmod_vcs_version.txt \
	-cf - . | tar -C "$cachetag_src" -xf -

export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:$prefix/lib/aarch64-linux-gnu/pkgconfig:$prefix/lib/x86_64-linux-gnu/pkgconfig"
export PATH="$prefix/sbin:$prefix/bin:$PATH"
export LD_LIBRARY_PATH="$prefix/lib:$prefix/lib/vinyl-cache:${LD_LIBRARY_PATH:-}"

cd "$cachetag_src"
if [ "$CACHE_TAG_BUILD_PROFILE" = production ]; then
	printf "cachetag bootstrap command:\n  ./bootstrap --prefix=%s\n" "$prefix"
	CPPFLAGS="$profile_cppflags" \
	CFLAGS="$profile_cflags" \
	LDFLAGS="$profile_ldflags" \
	./bootstrap --prefix="$prefix"
else
	./bootstrap --prefix="$prefix"
fi
report_effective_flags "cachetag" "$cachetag_src/src/Makefile"
make -j"$(nproc)"

#
# Production hardening inspection.
#
# Runs after the VMOD builds. Its verdict is remembered and applied to the exit
# status after the behaviour suite has run, so one invocation yields both the
# test result and the hardening result; a failing property still fails the run.
#
hardening_failed=0

inspect_hardening() {
	local path=$1 kind=$2
	local dynsyms relro flags1 elftype
	local prop_ok

	printf "\n----- hardening inspection: %s (%s) -----\n" "$path" "$kind"
	if [ ! -f "$path" ]; then
		printf "FAIL  file-present        missing: %s\n" "$path"
		hardening_failed=1
		return 0
	fi

	dynsyms=$(readelf -W --dyn-syms --syms "$path" 2>/dev/null || true)
	relro=$(readelf -W -l "$path" 2>/dev/null || true)
	flags1=$(readelf -W -d "$path" 2>/dev/null || true)
	elftype=$(readelf -W -h "$path" 2>/dev/null | grep -E "^ *Type:" || true)

	check_prop() {
		local name=$1 ok=$2 detail=$3
		if [ "$ok" -eq 0 ]; then
			printf "PASS  %-18s %s\n" "$name" "$detail"
		else
			printf "FAIL  %-18s %s\n" "$name" "$detail"
			hardening_failed=1
		fi
	}

	# These use bash pattern matching rather than "printf ... | grep -q".
	# That is deliberate and load-bearing: readelf output for vinyld is
	# megabytes, grep -q exits on the first match, printf then dies of SIGPIPE,
	# and under set -o pipefail the pipeline reports 141 even though the symbol
	# WAS found. That produced a false FAIL for __stack_chk_fail (which sits at
	# dynamic symbol index 33, so grep always exited early) while the
	# read-to-completion checks below passed. Do not reintroduce a pipeline here.

	# 1. Stack canary.
	if [[ $dynsyms == *__stack_chk_fail* ]]; then
		check_prop "stack-protector" 0 "__stack_chk_fail referenced"
	else
		check_prop "stack-protector" 1 "no __stack_chk_fail reference"
	fi

	# 2. GNU_RELRO segment.
	if [[ $relro == *GNU_RELRO* ]]; then
		check_prop "relro-segment" 0 "GNU_RELRO present"
	else
		check_prop "relro-segment" 1 "GNU_RELRO absent"
	fi

	# 3. BIND_NOW (full RELRO together with property 2).
	if [[ $flags1 == *BIND_NOW* || $flags1 =~ FLAGS_1.*NOW ]]; then
		check_prop "bind-now" 0 "BIND_NOW/NOW set (full RELRO)"
	else
		check_prop "bind-now" 1 "BIND_NOW/NOW absent (partial RELRO only)"
	fi

	# 4. PIE for executables, position-independent code for shared objects.
	if [[ $elftype == *DYN* ]]; then
		if [ "$kind" = executable ]; then
			check_prop "pie" 0 "ELF type DYN"
		else
			check_prop "pic" 0 "ELF type DYN (shared object)"
		fi
	else
		check_prop "pie/pic" 1 "ELF type is not DYN: $elftype"
	fi

	# 5. FORTIFY_SOURCE.
	#
	# Match only symbol names that END in _chk, so that __stack_chk_fail (which
	# is evidence for property 1, not for FORTIFY) cannot be miscounted here.
	# The || true matters: with set -o pipefail a no-match grep would otherwise
	# abort the inspection instead of reporting a FAIL.
	prop_ok=$( { printf "%s" "$dynsyms" | grep -oE "__[a-z0-9_]+_chk\b" || true; } | sort -u | tr "\n" " ")
	if [ -n "$prop_ok" ]; then
		check_prop "fortify-source" 0 "$prop_ok"
	else
		check_prop "fortify-source" 1 "no __*_chk symbols referenced"
	fi

	printf -- "----- end hardening inspection: %s -----\n" "$path"
}

if [ "$hardening_inspection" -eq 1 ]; then
	vmod_so="$cachetag_src/src/.libs/libvmod_cachetag.so"
	vinyld_bin=$(command -v vinyld || true)
	if [ -z "$vinyld_bin" ]; then
		vinyld_bin="$prefix/sbin/vinyld"
	fi

	printf "\n===== production hardening inspection =====\n"
	printf "toolchain: %s\n" "$(gcc --version | head -1)"
	printf "target:    %s\n" "$(gcc -dumpmachine)"
	inspect_hardening "$vmod_so" shared-object
	inspect_hardening "$vinyld_bin" executable
	if [ "$hardening_failed" -eq 0 ]; then
		printf "\nHARDENING INSPECTION: PASS (all properties present on both objects)\n"
	else
		printf "\nHARDENING INSPECTION: FAIL (see FAIL lines above)\n"
	fi
	printf "===== end production hardening inspection =====\n\n"
fi

dump_failure_logs() {
	find . -type f -name test-suite.log -print | while IFS= read -r log; do
		printf "\n===== %s =====\n" "$log" >&2
		sed -n "1,${CACHE_TAG_FAILURE_LOG_LINES}p" "$log" >&2
	done
}
tests_failed=0
if [ -n "${CACHE_TAG_TESTS}" ]; then
	if ! make "${CACHE_TAG_CHECK_TARGET}" TESTS="${CACHE_TAG_TESTS}"; then
		dump_failure_logs
		tests_failed=1
	fi
else
	if ! make "${CACHE_TAG_CHECK_TARGET}"; then
		dump_failure_logs
		tests_failed=1
	fi
fi

if [ "$tests_failed" -ne 0 ]; then
	exit 1
fi
if [ "$hardening_failed" -ne 0 ]; then
	printf "production hardening inspection failed; see the FAIL lines above\n" >&2
	exit 1
fi
'
