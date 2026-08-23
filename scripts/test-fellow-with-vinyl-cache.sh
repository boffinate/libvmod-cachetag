#!/bin/sh
set -eu

usage() {
	cat <<'EOF'
Usage: scripts/test-fellow-with-vinyl-cache.sh [VINYL_CACHE_SRC]

Build Vinyl, patched Slash/Fellow, and this standalone cachetag VMOD in Docker,
then run cachetag Fellow VTCs. This script does not copy files into the Vinyl
source tree.

Environment:
  VINYL_CACHE_SRC       Vinyl Cache source checkout (default: ../vinyl-cache)
  SLASH_SRC             Slash source checkout (default: ../slash)
  VINYL_DOCKER_IMAGE    Docker image with Vinyl build dependencies
                        (default: vinyl-cache-ubuntu-build)
  CACHE_TAG_FELLOW_TESTS optional space-separated VTC list. Defaults to
                        generated Fellow-storage copies of the explicit
                        storage-agnostic lifecycle/race list, then the
                        explicit persistent FDO and SIGKILL lists below
  CACHE_TAG_FELLOW_COMMON_TESTS optional space-separated common VTC list used
                        when CACHE_TAG_FELLOW_TESTS is not set. Defaults to
                        the explicit storage-agnostic lifecycle/race list
  CACHE_TAG_FELLOW_SLASH_CHECK run focused Fellow-local unit tests before the
                        VMOD VTCs (default: 1; set to 0 to skip)
  CACHE_TAG_FELLOW_SLASH_TESTS optional space-separated Slash test list.
                        Defaults to both cache tests, the non-witness log test,
                        and bitf_segmentation_test.
EOF
}

case "${1:-}" in
	-h|--help)
		usage
		exit 0
		;;
esac

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
default_vinyl_src="$repo_dir/../vinyl-cache"
vinyl_src=${1:-${VINYL_CACHE_SRC:-$default_vinyl_src}}
vinyl_src=$(CDPATH= cd -- "$vinyl_src" && pwd)

default_slash_src="$repo_dir/../slash"
slash_src=${SLASH_SRC:-$default_slash_src}
slash_src=$(CDPATH= cd -- "$slash_src" && pwd)

image=${VINYL_DOCKER_IMAGE:-vinyl-cache-ubuntu-build}
fellow_tests=${CACHE_TAG_FELLOW_TESTS:-}
fellow_common_tests=${CACHE_TAG_FELLOW_COMMON_TESTS:-}
fellow_slash_check=${CACHE_TAG_FELLOW_SLASH_CHECK:-1}
fellow_slash_tests=${CACHE_TAG_FELLOW_SLASH_TESTS:-fellow_cache_test_ndebug fellow_cache_test fellow_log_test_ndebug bitf_segmentation_test}

docker run --rm \
	-v "$vinyl_src:/vinyl-src:ro" \
	-v "$slash_src:/slash-host:ro" \
	-v "$repo_dir:/cachetag-host:ro" \
	-e "CACHE_TAG_FELLOW_TESTS=$fellow_tests" \
	-e "CACHE_TAG_FELLOW_COMMON_TESTS=$fellow_common_tests" \
	-e "CACHE_TAG_FELLOW_SLASH_CHECK=$fellow_slash_check" \
	-e "CACHE_TAG_FELLOW_SLASH_TESTS=$fellow_slash_tests" \
	"$image" \
	bash -lc '
set -euo pipefail

prefix=/tmp/vinyl-prefix
vinyl_build=/tmp/vinyl-build
vinyl_src_copy=/tmp/vinyl-src-copy
slash_src=/tmp/slash-src
cachetag_src=/tmp/cachetag-src

rm -rf "$prefix" "$vinyl_build" "$vinyl_src_copy" "$slash_src" "$cachetag_src"
mkdir -p "$prefix" "$vinyl_build" "$vinyl_src_copy" "$slash_src" "$cachetag_src"

tar -C /vinyl-src -cf - . | tar -C "$vinyl_src_copy" -xf -

cd "$vinyl_build"
(
	cd "$vinyl_src_copy"
	sh ./autogen.sh
)
"$vinyl_src_copy"/configure --prefix="$prefix" --with-unwind \
	--enable-developer-warnings --enable-debugging-symbols \
	--disable-stack-protector --with-persistent-storage
make -j"$(nproc)"
make install

tar -C /slash-host --exclude=.git -cf - . | tar -C "$slash_src" -xf -
cd "$slash_src"

if [ -f src/fellow_cachetag.c ] ||
    grep -q "DLE_META_ADD" src/tbl/dle.h 2>/dev/null; then
	echo "obsolete cachetag DLE patch detected in Slash checkout; use a clean 7be4126 base or the replacement object-attr patch stack" >&2
	exit 1
fi

apply_fellow_patch_stack() {
	local patch_dir=$1
	local patch tmp status
	local -a patches reverse_patches

	shopt -s nullglob
	patches=("$patch_dir"/*.patch)
	shopt -u nullglob
	if [ "${#patches[@]}" -eq 0 ]; then
		return 0
	fi

	reverse_patches=()
	for ((i = ${#patches[@]} - 1; i >= 0; i--)); do
		reverse_patches+=("${patches[$i]}")
	done

	tmp=$(mktemp -d)
	status=0
	tar -C . -cf - . | tar -C "$tmp" -xf -
	(
		cd "$tmp"
		for patch in "${reverse_patches[@]}"; do
			git apply --reverse --check -C0 "$patch" >/dev/null 2>&1 || exit 1
			git apply --reverse -C0 "$patch" >/dev/null 2>&1 || exit 1
		done
		for patch in "${patches[@]}"; do
			git apply --check "$patch" >/dev/null 2>&1 || exit 1
			git apply "$patch" >/dev/null 2>&1 || exit 1
		done
	) || status=$?
	rm -rf "$tmp"
	if [ "$status" -eq 0 ]; then
		printf "slash patch stack already applied: %s (%u patches)\n" "$patch_dir" "${#patches[@]}"
		return 0
	fi

	for patch in "${patches[@]}"; do
		if git apply --check "$patch" >/dev/null 2>&1; then
			git apply "$patch"
		elif git apply --reverse --check -C0 "$patch" >/dev/null 2>&1; then
			printf "slash patch already applied: %s\n" "$(basename "$patch")"
		else
			git apply "$patch"
		fi
	done
}
apply_fellow_patch_stack /cachetag-host/patches/fellow
mkdir -p m4
cp "$vinyl_src_copy"/m4/ax_*.m4 m4/
cat > m4/ax_execinfo.m4 <<'"'"'M4EOF'"'"'
AC_DEFUN([AX_EXECINFO], [
	AC_CHECK_HEADERS([execinfo.h])
	AC_SEARCH_LIBS([backtrace], [execinfo], [$1], [$2])
])
M4EOF
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:$prefix/lib/aarch64-linux-gnu/pkgconfig:$prefix/lib/x86_64-linux-gnu/pkgconfig"
export PATH="$prefix/sbin:$prefix/bin:$PATH"
export LD_LIBRARY_PATH="$prefix/lib:$prefix/lib/vinyl-cache:${LD_LIBRARY_PATH:-}"
slash_build_cflags="-I$vinyl_build/include -I$vinyl_build/lib/libvsc"
CPPFLAGS="${CPPFLAGS:-} $slash_build_cflags" \
CFLAGS="${CFLAGS:-} $slash_build_cflags" \
VINYLSRC="$vinyl_src_copy" ./bootstrap --prefix="$prefix"
make -j"$(nproc)"
if [ "$CACHE_TAG_FELLOW_SLASH_CHECK" != 0 ]; then
	if ! make check TESTS="$CACHE_TAG_FELLOW_SLASH_TESTS"; then
		if [ -f src/test-suite.log ]; then
			printf "%s\n" "--- Slash focused test-suite.log ---" >&2
			sed -n "1,240p" src/test-suite.log >&2
		fi
		for log in src/fellow_cache_test_ndebug.log src/fellow_cache_test.log \
		    src/fellow_log_test_ndebug.log src/fellow_log_test.log \
		    src/bitf_segmentation_test.log; do
			if [ -f "$log" ]; then
				printf "%s\n" "--- tail: $log ---" >&2
				tail -n 120 "$log" >&2
			fi
		done
		exit 1
	fi
fi

tar -C /cachetag-host \
	--exclude=.git \
	--exclude=Makefile \
	--exclude=Makefile.in \
	--exclude=aclocal.m4 \
	--exclude=autom4te.cache \
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

cd "$cachetag_src"
# The Fellow VTC matrix uses test-hook and diagnostic VCL methods, so build
# the full diagnostic surface.
./bootstrap --prefix="$prefix" --enable-demo-diagnostics --enable-test-hooks
make -j"$(nproc)"

slash_vmod="$slash_src/src/.libs/libvmod_slash.so"
vmod_path="$cachetag_src/src/.libs:$slash_src/src/.libs:$prefix/lib/vinyl-cache/vmods:$prefix/lib/vmods"
vcl_path="$cachetag_src/vcl:$vinyl_src_copy/etc:$prefix/share/vinyl-cache/vcl"
fellow_common_dir="$cachetag_src/src/vtc/fellow-common"

cachetag_fellow_common_test() {
	src=$1
	base=$(basename "$src")
	dst="$fellow_common_dir/$base"
	mkdir -p "$fellow_common_dir"
	awk -v stv="\${tmpdir}/fellow-common-$base.stv" '"'"'
		/^vinyl[[:space:]]+[[:alnum:]_]+[[:space:]]+-vcl/ {
			sub(/ -vcl/, " -arg \"-E${libvmod_slash}\" -arg \"-sfellow=fellow," stv ",100MB,1MB,64KB\" -vcl")
		}
		/^[[:space:]]*import cachetag;/ {
			print
			print "\timport slash from \"${libvmod_slash}\";"
			next
		}
		/^[[:space:]]*sub vcl_init[[:space:]]*\{/ {
			print
			print "\t\tslash.tune_fellow(storage.fellow);"
			next
		}
		{ print }
	'"'"' "$src" > "$dst"
	printf "%s\n" "$dst"
}

if [ -n "${CACHE_TAG_FELLOW_TESTS}" ]; then
	tests="${CACHE_TAG_FELLOW_TESTS}"
else
	if [ -n "${CACHE_TAG_FELLOW_COMMON_TESTS}" ]; then
		common_tests="${CACHE_TAG_FELLOW_COMMON_TESTS}"
	else
		common_tests="
			src/vtc/cachetag_c00002.vtc
			src/vtc/cachetag_c00006.vtc
			src/vtc/cachetag_c00008.vtc
			src/vtc/cachetag_c00009.vtc
			src/vtc/cachetag_c00010.vtc
			src/vtc/cachetag_c00015.vtc
			src/vtc/cachetag_c00018.vtc
			src/vtc/cachetag_c00019.vtc
				src/vtc/cachetag_c00021.vtc
				src/vtc/cachetag_c00022.vtc
				src/vtc/cachetag_c00023.vtc
				src/vtc/cachetag_c00026.vtc
				src/vtc/cachetag_c00027.vtc
				src/vtc/cachetag_r00001.vtc
			src/vtc/cachetag_r00002.vtc
			src/vtc/cachetag_r00003.vtc
			src/vtc/cachetag_r00004.vtc
			src/vtc/cachetag_r00005.vtc
				src/vtc/cachetag_r00006.vtc
				src/vtc/cachetag_r00008.vtc
				src/vtc/cachetag_r00009.vtc
				src/vtc/cachetag_pm00039.vtc
				src/vtc/cachetag_pm00038.vtc
			"
	fi
	tests=""
	for t in $common_tests; do
		case "$t" in
			/*) ;;
			*) t="$cachetag_src/$t" ;;
		esac
		tests="$tests $(cachetag_fellow_common_test "$t")"
	done
	tests="$tests
		$cachetag_src/src/vtc/cachetag_p00000.vtc
		$cachetag_src/src/vtc/cachetag_p00001.vtc
		$cachetag_src/src/vtc/cachetag_p00002.vtc
		$cachetag_src/src/vtc/cachetag_p00003.vtc
		$cachetag_src/src/vtc/cachetag_p00004.vtc
		$cachetag_src/src/vtc/cachetag_p00005.vtc
		$cachetag_src/src/vtc/cachetag_p00006.vtc
		$cachetag_src/src/vtc/cachetag_p00007.vtc
		$cachetag_src/src/vtc/cachetag_p00008.vtc
		$cachetag_src/src/vtc/cachetag_p00009.vtc
		$cachetag_src/src/vtc/cachetag_p00010.vtc
		$cachetag_src/src/vtc/cachetag_p00011.vtc
		$cachetag_src/src/vtc/cachetag_p00012.vtc
		$cachetag_src/src/vtc/cachetag_p00013.vtc
		$cachetag_src/src/vtc/cachetag_p00014.vtc
		$cachetag_src/src/vtc/cachetag_p00015.vtc
		$cachetag_src/src/vtc/cachetag_p00016.vtc
		$cachetag_src/src/vtc/cachetag_p00017.vtc
		$cachetag_src/src/vtc/cachetag_p00018.vtc
		$cachetag_src/src/vtc/cachetag_p00019.vtc
		$cachetag_src/src/vtc/cachetag_p00020.vtc
		$cachetag_src/src/vtc/cachetag_p00021.vtc
		$cachetag_src/src/vtc/cachetag_p00022.vtc
		$cachetag_src/src/vtc/cachetag_p00023.vtc
		$cachetag_src/src/vtc/cachetag_p00090.vtc
		$cachetag_src/src/vtc/cachetag_x00000.vtc"
fi

if [ -z "$tests" ]; then
	echo "No cachetag Fellow tests found" >&2
	exit 1
fi

cd "$vinyl_build"
for t in $tests; do
	case "$t" in
		/*) ;;
		*) t="$cachetag_src/$t" ;;
	esac
	printf "cachetag fellow harness: %s\n" "$t"
	"$vinyl_build/bin/vinyltest/vinyltest" -v \
		-D "topbuild=$vinyl_build" \
		-D "topsrc=/vinyl-src" \
		-D "libvmod_slash=$slash_vmod" \
		-p "vmod_path=$vmod_path" \
		-p "vcl_path=$vcl_path" \
		"$t"
done
'
