#!/bin/bash
# Run through the Docker harness: these checks use the installed engine SDK.
set -euo pipefail
family=${1:?expected engine flavor}
source_dir=${2:?expected configured VMOD source directory}
PKG_CONFIG=${PKG_CONFIG:-pkg-config}
# Isolate this SDK so a custom image with both engines still exercises the
# missing-engine and automatic-selection cases deterministically.
sdk_pcdir=$("$PKG_CONFIG" --variable=pcfiledir "${family}api")
export PKG_CONFIG_LIBDIR="$sdk_pcdir"
export PKG_CONFIG_PATH="$sdk_pcdir"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp -a "$source_dir" "$work/src"
cd "$work/src"
run_logged() {
    if ! "$@" >"$work/command.log" 2>&1; then
        cat "$work/command.log" >&2
        return 1
    fi
}
run_logged make distclean
run_logged ./bootstrap
grep -F "CACHETAG_ENGINE = $family" Makefile
run_logged make -j2
run_logged make check TESTS='cachetag_wal_test cachetag_counter_parity.sh vtc/cachetag_c00000.vtc'
printf 'PASS: automatic SDK selection and production smoke (%s)\n' "$family"
reject() {
    local needle=$1
    shift
    if "$@" >"$work/reject.log" 2>&1; then
        echo "unexpected configure success: $*" >&2
        exit 1
    fi
    if ! grep -F "$needle" "$work/reject.log"; then
        cat "$work/reject.log" >&2
        return 1
    fi
}
case "$family" in vinyl) other=varnish ;; varnish) other=vinyl ;; esac
reject 'no supported vcache project found' ./configure --with-vcache="$other"
printf 'PASS: missing requested engine rejected\n'
incdir=$("$PKG_CONFIG" --variable=pkgincludedir "${family}api")
version=$("$PKG_CONFIG" --modversion "${family}api")
abi=$(sed -n 's/^#define VMOD_ABI_Version ".* \([^ ]*\)"/\1/p' "$incdir/vmod_abi.h")
[ -n "$abi" ]
package_version=$(./configure --version | sed -n '1s/.* //p')
cat >"$work/manifest.yml" <<EOF
status: candidate
cohort: local-contract
$family:
  version: $version
  strict_abi: $abi
cachetag:
  version: $package_version
EOF
run_logged ./configure --with-vcache="$family" --with-release-manifest="$work/manifest.yml"
grep -F 'ok (cohort local-contract' "$work/command.log"
sed 's/strict_abi:.*/strict_abi: invalid/' "$work/manifest.yml" >"$work/bad.yml"
reject 'strict VMOD ABI mismatch' ./configure --with-vcache="$family" --with-release-manifest="$work/bad.yml"
sed "s/^$family:/$other:/" "$work/manifest.yml" >"$work/bad.yml"
reject "does not define $family.version" ./configure --with-vcache="$family" --with-release-manifest="$work/bad.yml"
sed "s/version: $version/version: 0.0.0/" "$work/manifest.yml" >"$work/bad.yml"
reject 'Engine version mismatch' ./configure --with-vcache="$family" --with-release-manifest="$work/bad.yml"
printf 'PASS: matching manifest accepted; wrong ABI, family, and version rejected\n'
mkdir "$work/old-sdk"
pcdir=$("$PKG_CONFIG" --variable=pcfiledir "${family}api")
sed 's/^Version:.*/Version: 9.0.0/' "$pcdir/${family}api.pc" >"$work/old-sdk/${family}api.pc"
export PKG_CONFIG_PATH="$work/old-sdk:$PKG_CONFIG_PATH"
reject 'lower than minimum version 9.1.0' ./configure --with-vcache="$family"
reject 'development SDK is required' ./bootstrap --with-vcache="$family"
printf 'PASS: 9.0 SDK rejected by configure and bootstrap\n'
sed 's/^Version:.*/Version: trunk/' "$pcdir/${family}api.pc" >"$work/old-sdk/${family}api.pc"
run_logged ./bootstrap --with-vcache="$family"
grep -F "reports the non-numeric version 'trunk'" "$work/command.log"
printf 'PASS: development SDK accepted with strict-ABI warning\n'
