#!/bin/bash
#
# Produce the canonical cachetag source release archive, deterministically.
#
# This is the release-oriented counterpart to scripts/test-with-vinyl-cache.sh.
# The test harness is a *test* harness: it builds whatever happens to be in the
# mutable sibling ../vinyl-cache checkout, into a throwaway prefix. That is a
# fine contract for testing and a bad one for a release, where the exact bytes
# of every input have to be recorded.
#
# This script therefore:
#
#   1. requires an explicitly pinned Vinyl input -- either a source tarball, or
#      an immutable commit resolved in a Vinyl checkout via `git archive` -- and
#      records its SHA-256 and (for git) its commit id;
#   2. builds that pinned Vinyl into a content-addressed Docker volume,
#      replicating the container steps of the authoritative harness;
#   3. runs the Default-storage `distcheck` against it, which is what produces
#      and validates libvmod-cachetag-X.Y.Z.tar.gz;
#   4. repacks the validated tarball deterministically (sorted, root-owned,
#      numeric owners, mtimes pinned to SOURCE_DATE_EPOCH, gzip without a
#      timestamp), because automake's `make dist` does not do that itself;
#   5. optionally repeats 3-4 in a second container and compares the digests;
#   6. proves the archive is buildable on its own, in a fresh container, with
#      no bootstrap/autoreconf step;
#   7. writes the archives plus a JSON metadata sidecar into a gitignored
#      output directory.
#
# scripts/test-with-vinyl-cache.sh is deliberately NOT modified or invoked. Its
# container steps and its source-copy exclude list are copied here verbatim so
# that the release path and the test path cannot silently diverge in one
# direction only; if the harness's exclude list changes, this copy must be
# updated to match.
#
set -euo pipefail

usage() {
	cat <<'EOF'
Usage: scripts/release-source-archive.sh --vinyl-tarball FILE  [options]
       scripts/release-source-archive.sh --vinyl-git DIR [--vinyl-ref REF] [options]

Produce the canonical, deterministic libvmod-cachetag source release archive.

A pinned Vinyl source input is mandatory. There is deliberately no default:
the sibling ../vinyl-cache checkout is mutable, and a release archive must not
be produced against an input that cannot be named exactly.

Pinned Vinyl input (exactly one form required):
  --vinyl-tarball FILE   a Vinyl source archive (.tar, .tar.gz, .tgz).
                         Recorded by SHA-256.
  --vinyl-git DIR        a Vinyl git checkout. The ref is resolved to an
                         immutable commit id and exported with `git archive`;
                         the checkout is only ever read. Recorded by commit id
                         and by the SHA-256 of the exported archive.
  --vinyl-ref REF        ref to resolve in --vinyl-git (default: HEAD).

Release rigor:
  --release              refuse to run unless the working tree is clean and
                         HEAD carries an annotated tag vX.Y.Z matching AC_INIT.
  (default)              dev mode: a dirty tree is permitted and the output is
                         stamped "dev-build-from <commit>[ +dirty]".

Options:
  -o, --output-dir DIR   output directory (default: release/dist)
      --runs N           produce the archive N times from the same inputs and
                         compare digests (default: 1; use 2 for a determinism
                         proof). Run 1 is the canonical output.
      --check-target T   make target that produces/validates the archive
                         (default: distcheck)
      --from-archive-target T
                         make target for the from-archive rebuild proof
                         (default: check; "none" skips the proof)
      --from-archive-tests LIST
                         TESTS override for the from-archive rebuild, e.g.
                         vtc/cachetag_c00000.vtc
      --build-profile P  diagnostic (default) or production; mirrors
                         CACHE_TAG_BUILD_PROFILE in the test harness
      --image IMAGE      Docker image (default: vinyl-cache-ubuntu-build)
      --rebuild-vinyl    rebuild the pinned Vinyl prefix even if a matching
                         cached one exists
      --keep-work        keep the intermediate work directory
      --from-archive-only
                         skip archive production and re-run only the
                         from-archive rebuild proof against the archive already
                         in the output directory. Used to re-run a quarantined
                         flake without discarding the first result: the original
                         metadata and logs are left untouched and the re-run is
                         recorded separately.
      --failure-log-lines N
                         test-suite.log lines to print on failure (default:
                         2000). Complete logs are always preserved under
                         <output>/failure-logs/ regardless of this value.
  -h, --help             this text

Output (all gitignored):
  <output>/libvmod-cachetag-X.Y.Z.tar.gz          canonical deterministic archive
  <output>/libvmod-cachetag-X.Y.Z.tar.gz.sha256
  <output>/libvmod-cachetag-X.Y.Z.dist-raw.tar.gz the unmodified `make dist`
                                                  output, kept for diffing
  <output>/libvmod-cachetag-X.Y.Z.metadata.json   pinned inputs and digests
  <output>/logs/                                  container logs
EOF
}

# Complete test-suite logs are copied out of the work directory on *any* fatal
# exit, not only on the success path. A run that dies in archive production is
# exactly the run whose logs are needed to classify the failure, and the work
# directory it left behind is not an interface anyone should have to know about.
preserve_failure_logs() {
	[ -n "${work_dir:-}" ] || return 0
	[ -d "$work_dir/failure-logs" ] || return 0
	[ -n "$(ls -A "$work_dir/failure-logs" 2>/dev/null)" ] || return 0
	mkdir -p "$output_dir/failure-logs"
	cp -R "$work_dir/failure-logs/." "$output_dir/failure-logs/" 2>/dev/null || return 0
	printf 'complete failure logs preserved: %s\n' "$output_dir/failure-logs" >&2
}

die() {
	printf 'release-source-archive: %s\n' "$*" >&2
	preserve_failure_logs
	exit 1
}
note() { printf '\n===== %s =====\n' "$*"; }

#
# Every container this script starts runs as root, so everything it writes into
# the bind-mounted work directory is root-owned on a Linux host, and the host
# side then cannot delete it ("rm: cannot remove ...: Permission denied").
# macOS hides this: the Docker VM maps container-root writes back to the
# invoking user, which is why the local lane never hit it and CI did on its
# first real run. Hand ownership back through a container, since only root
# inside one can chown these files.
#
reown_work() {
	[ -d "$1" ] || return 0
	if [ "$(id -u)" -ne 0 ]; then
		_reown_abs=$(CDPATH= cd -- "$1" && pwd)
		docker run --rm -v "$_reown_abs:/reown" "$image" \
			chown -R "$(id -u):$(id -g)" /reown >/dev/null 2>&1 || true
	fi
	return 0
}

sha256_file() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | cut -d' ' -f1
	else
		shasum -a 256 "$1" | cut -d' ' -f1
	fi
}

#
# Run a docker step, tee its output to a log, and return DOCKER's exit status.
#
# This exists because of a real bug: `docker run ... | tee log` under
# `set -o pipefail` returns the pipeline's rightmost non-zero status, so a
# failing container aborted the whole script through `set -e` before the
# metadata sidecar was ever written -- the run's own failure destroyed the
# record of what it had produced. Checking ${PIPESTATUS[0]} after the pipeline
# does not help: set -e fires first. The status must be captured with -e off.
#
docker_step() {
	local log=$1; shift
	local st=0
	set +e
	"$@" 2>&1 | tee "$log"
	st=${PIPESTATUS[0]}
	set -e
	return "$st"
}

json_escape() {
	printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

#
# Arguments.
#
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

vinyl_tarball=""
vinyl_git=""
vinyl_ref="HEAD"
mode="dev"
output_dir=""
runs=1
check_target="distcheck"
from_archive_target="check"
from_archive_tests=""
build_profile="diagnostic"
image="${VINYL_DOCKER_IMAGE:-vinyl-cache-ubuntu-build}"
rebuild_vinyl=0
keep_work=0
from_archive_only=0
failure_log_lines=2000

while [ $# -gt 0 ]; do
	case "$1" in
		--vinyl-tarball) vinyl_tarball=${2:?--vinyl-tarball needs a FILE}; shift 2 ;;
		--vinyl-git) vinyl_git=${2:?--vinyl-git needs a DIR}; shift 2 ;;
		--vinyl-ref) vinyl_ref=${2:?--vinyl-ref needs a REF}; shift 2 ;;
		--release) mode="release"; shift ;;
		-o|--output-dir) output_dir=${2:?--output-dir needs a DIR}; shift 2 ;;
		--runs) runs=${2:?--runs needs a number}; shift 2 ;;
		--check-target) check_target=${2:?--check-target needs a target}; shift 2 ;;
		--from-archive-target) from_archive_target=${2:?--from-archive-target needs a target}; shift 2 ;;
		--from-archive-tests) from_archive_tests=${2:?--from-archive-tests needs a list}; shift 2 ;;
		--build-profile) build_profile=${2:?--build-profile needs a profile}; shift 2 ;;
		--image) image=${2:?--image needs an image}; shift 2 ;;
		--rebuild-vinyl) rebuild_vinyl=1; shift ;;
		--from-archive-only) from_archive_only=1; shift ;;
		--failure-log-lines) failure_log_lines=${2:?--failure-log-lines needs a number}; shift 2 ;;
		--keep-work) keep_work=1; shift ;;
		-h|--help) usage; exit 0 ;;
		*) usage >&2; die "unknown argument: $1" ;;
	esac
done

case "$build_profile" in
	diagnostic|production) ;;
	*) die "unknown --build-profile: $build_profile (expected diagnostic or production)" ;;
esac

case "$runs" in
	''|*[!0-9]*) die "--runs must be a positive integer" ;;
esac
[ "$runs" -ge 1 ] || die "--runs must be at least 1"

if [ -n "$vinyl_tarball" ] && [ -n "$vinyl_git" ]; then
	die "--vinyl-tarball and --vinyl-git are mutually exclusive"
fi
if [ -z "$vinyl_tarball" ] && [ -z "$vinyl_git" ]; then
	usage >&2
	die "a pinned Vinyl input is required: --vinyl-tarball FILE or --vinyl-git DIR.
       There is no default. The sibling ../vinyl-cache working tree is mutable and
       must not be consumed by a release build without recording an exact commit.
       To pin the sibling checkout's current HEAD, use:
           scripts/release-source-archive.sh --vinyl-git ../vinyl-cache"
fi

[ -n "$output_dir" ] || output_dir="$repo_dir/release/dist"
mkdir -p "$output_dir"
output_dir=$(CDPATH= cd -- "$output_dir" && pwd)

# Written before anything else lands here, so no intermediate state of this
# directory is ever visible to git.
cat > "$output_dir/.gitignore" <<'EOF'
# Release build output. Nothing here is ever committed: these are artifacts of
# scripts/release-source-archive.sh, reproducible from the pinned inputs
# recorded in their own metadata sidecar.
*
EOF

#
# Stage 0: cachetag release identity.
#
cachetag_version=$(awk '
	/^AC_INIT\(/ {
		n = split($0, parts, /\[|\]/)
		# AC_INIT([name], [version]) -> parts: "AC_INIT(", name, ", ", version
		print parts[4]
		exit
	}' "$repo_dir/configure.ac")
[ -n "$cachetag_version" ] || die "could not read the package version from configure.ac"

archive_name="libvmod-cachetag-$cachetag_version.tar.gz"
distdir_name="libvmod-cachetag-$cachetag_version"

git -C "$repo_dir" rev-parse --git-dir >/dev/null 2>&1 ||
	die "$repo_dir is not a git checkout; a release archive needs a commit to pin"

cachetag_commit=$(git -C "$repo_dir" rev-parse HEAD)
cachetag_commit_short=$(git -C "$repo_dir" rev-parse --short=12 HEAD)

# SOURCE_DATE_EPOCH comes from the committer date of the release commit, per the
# plan's Reproducibility section. Committer date rather than author date: it is
# the timestamp of the commit object that the tag actually points at, and it is
# what rebasing/amending updates.
source_date_epoch=$(git -C "$repo_dir" show -s --format=%ct HEAD)
source_date_utc=$(TZ=UTC0 date -u -r "$source_date_epoch" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null ||
	TZ=UTC0 date -u -d "@$source_date_epoch" '+%Y-%m-%dT%H:%M:%SZ')

dirty_output=$(git -C "$repo_dir" status --porcelain)
if [ -n "$dirty_output" ]; then
	worktree_dirty=true
else
	worktree_dirty=false
fi

head_tag=""
head_tag_kind=""
for t in $(git -C "$repo_dir" tag --points-at HEAD 2>/dev/null); do
	if [ "$t" = "v$cachetag_version" ]; then
		head_tag=$t
		head_tag_kind=$(git -C "$repo_dir" cat-file -t "refs/tags/$t" 2>/dev/null || echo unknown)
		break
	fi
done

if [ "$mode" = "release" ]; then
	release_problems=""
	if [ "$worktree_dirty" = true ]; then
		release_problems="$release_problems
  - the working tree is dirty. A release archive must be reproducible from the
    tagged commit alone, so uncommitted or untracked files are refused:
$(printf '%s\n' "$dirty_output" | sed 's/^/      /')"
	fi
	if [ -z "$head_tag" ]; then
		release_problems="$release_problems
  - HEAD ($cachetag_commit_short) does not carry the tag v$cachetag_version
    required by AC_INIT([libvmod-cachetag], [$cachetag_version]).
    Tags currently on HEAD: $(git -C "$repo_dir" tag --points-at HEAD | tr '\n' ' ' | sed 's/ $//')"
	elif [ "$head_tag_kind" != "tag" ]; then
		release_problems="$release_problems
  - the tag v$cachetag_version on HEAD is a $head_tag_kind (lightweight) tag.
    The release contract requires an annotated, protected tag."
	fi
	if [ -n "$release_problems" ]; then
		printf 'release-source-archive: --release refused; this tree is not releasable:\n%s\n\n' \
			"$release_problems" >&2
		printf 'Re-run without --release to produce a dev archive stamped as non-release.\n' >&2
		exit 3
	fi
	release_stamp="release v$cachetag_version from $cachetag_commit"
else
	if [ "$worktree_dirty" = true ]; then
		release_stamp="dev-build-from $cachetag_commit +dirty"
	else
		release_stamp="dev-build-from $cachetag_commit"
	fi
fi

command -v docker >/dev/null 2>&1 || die "docker is not on PATH"
docker image inspect "$image" >/dev/null 2>&1 ||
	die "docker image not found: $image"
image_id=$(docker image inspect --format '{{.Id}}' "$image")

#
# Stage 1: pin the Vinyl input.
#
work_dir="$output_dir/work"
reown_work "$work_dir"
rm -rf "$work_dir"
mkdir -p "$work_dir/container" "$work_dir/dist" "$work_dir/failure-logs" "$output_dir/logs"

vinyl_kind=""
vinyl_commit=""
vinyl_source_path=""
vinyl_source_basename=""

if [ -n "$vinyl_git" ]; then
	vinyl_git=$(CDPATH= cd -- "$vinyl_git" && pwd)
	git -C "$vinyl_git" rev-parse --git-dir >/dev/null 2>&1 ||
		die "$vinyl_git is not a git checkout"
	vinyl_commit=$(git -C "$vinyl_git" rev-parse --verify "$vinyl_ref^{commit}" 2>/dev/null) ||
		die "cannot resolve $vinyl_ref in $vinyl_git"
	vinyl_kind="git-archive"
	vinyl_source_basename="vinyl-source-${vinyl_commit}.tar"
	vinyl_source_path="$work_dir/$vinyl_source_basename"
	note "pinning Vinyl input: $vinyl_git at $vinyl_ref -> $vinyl_commit"

	#
	# git archive only ever reads the checkout, but it does NOT descend into
	# submodules -- and Vinyl's build needs bin/vinyltest/vtest2, so a bare
	# `git archive` of the superproject fails configure with
	# "vtest2 seems to be missing". Each submodule is therefore exported
	# separately at the commit the superproject tree pins it to, which is just
	# as immutable as the superproject commit, and the pieces are assembled
	# into one deterministic tar.
	#
	pin_dir="$work_dir/pin"
	mkdir -p "$pin_dir"
	vinyl_commit_epoch=$(git -C "$vinyl_git" show -s --format=%ct "$vinyl_commit")
	git -C "$vinyl_git" archive --format=tar --prefix="vinyl-src/" "$vinyl_commit" \
		> "$pin_dir/00-superproject.tar"

	git -C "$vinyl_git" ls-tree -r "$vinyl_commit" |
		awk '$2 == "commit" { print $3" "$4 }' | sort -k2,2 > "$work_dir/vinyl-submodules.txt"

	sub_idx=1
	while read -r sub_commit sub_path; do
		[ -n "${sub_path:-}" ] || continue
		[ -d "$vinyl_git/$sub_path/.git" ] || [ -f "$vinyl_git/$sub_path/.git" ] ||
			die "submodule $sub_path is not checked out in $vinyl_git; a pinned release input cannot be exported from it"
		git -C "$vinyl_git/$sub_path" cat-file -e "$sub_commit^{commit}" 2>/dev/null ||
			die "submodule $sub_path does not contain the commit $sub_commit pinned by the superproject"
		if [ -n "$(git -C "$vinyl_git/$sub_path" ls-tree -r "$sub_commit" | awk '$2 == "commit"')" ]; then
			die "submodule $sub_path has nested submodules; this script pins only one level"
		fi
		printf 'pinned submodule   : %s at %s\n' "$sub_path" "$sub_commit"
		git -C "$vinyl_git/$sub_path" archive --format=tar \
			--prefix="vinyl-src/$sub_path/" "$sub_commit" \
			> "$pin_dir/$(printf '%02d' "$sub_idx")-$(printf '%s' "$sub_path" | tr '/' '_').tar"
		sub_idx=$((sub_idx + 1))
	done < "$work_dir/vinyl-submodules.txt"

	# Assemble the pieces into one deterministic tar. GNU tar is used for the
	# same normalisation applied to the release archive itself, so the pinned
	# Vinyl input has a stable digest regardless of which host exported it and
	# regardless of how many submodules had to be spliced in.
	docker run --rm \
		-v "$work_dir:/work" \
		-e "VINYL_SOURCE_BASENAME=$vinyl_source_basename" \
		-e "VINYL_COMMIT_EPOCH=$vinyl_commit_epoch" \
		"$image" \
		bash -c '
set -euo pipefail
export LC_ALL=C
rm -rf /tmp/pin
mkdir -p /tmp/pin
for t in /work/pin/*.tar; do
	tar -C /tmp/pin -xf "$t"
done
tar --format=gnu --sort=name --owner=0 --group=0 --numeric-owner \
	--mtime="@$VINYL_COMMIT_EPOCH" \
	-C /tmp/pin -cf "/work/$VINYL_SOURCE_BASENAME" vinyl-src
' > "$output_dir/logs/pin-assemble.log" 2>&1 ||
		die "assembling the pinned Vinyl source archive failed (see logs/pin-assemble.log)"
	rm -rf "$pin_dir"
else
	[ -f "$vinyl_tarball" ] || die "no such file: $vinyl_tarball"
	vinyl_kind="tarball"
	vinyl_source_basename=$(basename "$vinyl_tarball")
	vinyl_source_path="$work_dir/$vinyl_source_basename"
	cp "$vinyl_tarball" "$vinyl_source_path"
	note "pinning Vinyl input: tarball $vinyl_tarball"
fi

vinyl_sha256=$(sha256_file "$vinyl_source_path")
vinyl_sha256_short=$(printf '%s' "$vinyl_sha256" | cut -c1-12)

printf 'vinyl input kind   : %s\n' "$vinyl_kind"
[ -n "$vinyl_commit" ] && printf 'vinyl commit       : %s\n' "$vinyl_commit"
printf 'vinyl archive      : %s\n' "$vinyl_source_basename"
printf 'vinyl sha256       : %s\n' "$vinyl_sha256"
printf 'cachetag version   : %s\n' "$cachetag_version"
printf 'cachetag commit    : %s\n' "$cachetag_commit"
printf 'SOURCE_DATE_EPOCH  : %s (%s)\n' "$source_date_epoch" "$source_date_utc"
printf 'mode               : %s (%s)\n' "$mode" "$release_stamp"
printf 'build profile      : %s\n' "$build_profile"
printf 'runs               : %s\n' "$runs"

#
# Container step scripts.
#
# They live in the work directory, which is bind-mounted read/write at /work.
# Keeping them as files rather than `bash -lc '...'` strings avoids a second
# level of shell quoting inside an already quoted heredoc.
#

cat > "$work_dir/container/profile.sh" <<'CONTAINER_PROFILE'
# Build profiles, copied from scripts/test-with-vinyl-cache.sh.
#
# The two profiles are two separate literal flag lists. The production branch
# never derives its arguments from the diagnostic list, so a diagnostic-only
# option such as --disable-stack-protector cannot leak into a production build.
# See devdocs/docs/20260724_2033_note_step-6-build-profiles.md for why the production
# profile states the hardening flags explicitly instead of relying on Vinyl's
# --enable-stack-protector, which is a no-op without --enable-developer-warnings.

declare -a vinyl_configure_args
profile_cppflags=""
profile_cflags=""
profile_ldflags=""

probe_cc_flag() {
	local flag=$1
	printf "int main(void){return 0;}\n" > /tmp/cc-probe.c
	gcc -Werror "$flag" -o /tmp/cc-probe.out /tmp/cc-probe.c >/dev/null 2>&1
}

case "$CACHE_TAG_BUILD_PROFILE" in
diagnostic)
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
	vinyl_configure_args=(
		"--prefix=$prefix"
		--with-unwind
	)
	profile_cppflags="-Wdate-time -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=3"
	profile_cflags="-g -O2 -fstack-protector-strong -Wformat -Werror=format-security"
	profile_ldflags="-Wl,-z,relro -Wl,-z,now"
	for opt_flag in -fstack-clash-protection -mbranch-protection=standard -fcf-protection=full; do
		if probe_cc_flag "$opt_flag"; then
			profile_cflags="$profile_cflags $opt_flag"
		else
			printf "hardening: compiler does not support %s, skipping\n" "$opt_flag"
		fi
	done
	;;
esac
CONTAINER_PROFILE

cat > "$work_dir/container/vinyl-build.sh" <<'CONTAINER_VINYL'
#!/bin/bash
set -euo pipefail

prefix=/tmp/vinyl-prefix
vinyl_src_copy=/tmp/vinyl-src-copy
vinyl_build=/tmp/vinyl-build

. /work/container/profile.sh

rm -rf "$vinyl_src_copy" "$vinyl_build"
mkdir -p "$vinyl_src_copy" "$vinyl_build"

printf '\n===== extracting pinned Vinyl source: %s =====\n' "$VINYL_SOURCE_BASENAME"
case "$VINYL_SOURCE_BASENAME" in
	*.tar.gz|*.tgz) tar -C "$vinyl_src_copy" -xzf "/work/$VINYL_SOURCE_BASENAME" ;;
	*.tar)          tar -C "$vinyl_src_copy" -xf  "/work/$VINYL_SOURCE_BASENAME" ;;
	*) printf 'unsupported Vinyl archive form: %s\n' "$VINYL_SOURCE_BASENAME" >&2; exit 2 ;;
esac

# A source archive normally has a single top-level directory. Accept both that
# and a flat archive rather than assuming a prefix.
mapfile -t top < <(ls -A "$vinyl_src_copy")
if [ "${#top[@]}" -eq 1 ] && [ -d "$vinyl_src_copy/${top[0]}" ]; then
	vinyl_root="$vinyl_src_copy/${top[0]}"
else
	vinyl_root="$vinyl_src_copy"
fi
printf 'pinned Vinyl source root: %s\n' "$vinyl_root"

printf '\n===== build profile: %s =====\n' "$CACHE_TAG_BUILD_PROFILE"
printf 'vinyl configure command:\n  %s/configure' "$vinyl_root"
printf ' %s' "${vinyl_configure_args[@]}"
printf '\n'
printf 'profile CPPFLAGS: %s\n' "${profile_cppflags:-<unset, autoconf default>}"
printf 'profile CFLAGS:   %s\n' "${profile_cflags:-<unset, autoconf default>}"
printf 'profile LDFLAGS:  %s\n' "${profile_ldflags:-<unset, autoconf default>}"

rm -rf "${prefix:?}"/* "${prefix:?}"/.cachetag-* 2>/dev/null || true
mkdir -p "$prefix"

cd "$vinyl_root"
sh ./autogen.sh

cd "$vinyl_build"
if [ "$CACHE_TAG_BUILD_PROFILE" = production ]; then
	CPPFLAGS="$profile_cppflags" \
	CFLAGS="$profile_cflags" \
	LDFLAGS="$profile_ldflags" \
	"$vinyl_root"/configure "${vinyl_configure_args[@]}"
else
	"$vinyl_root"/configure "${vinyl_configure_args[@]}"
fi
make -j"$(nproc)"
make install

printf '%s %s\n' "$VINYL_SHA256" "$CACHE_TAG_BUILD_PROFILE" > "$prefix/.cachetag-release-stamp"
printf '\n===== pinned Vinyl prefix ready: %s =====\n' "$prefix"
pkg-config --modversion vinylapi || true
CONTAINER_VINYL

cat > "$work_dir/container/archive.sh" <<'CONTAINER_ARCHIVE'
#!/bin/bash
#
# Produce and validate one source archive, then repack it deterministically.
#
set -euo pipefail

prefix=/tmp/vinyl-prefix
cachetag_src=/tmp/cachetag-src
run_out="/work/run$RUN_ID"

. /work/container/profile.sh

rm -rf "$cachetag_src"
mkdir -p "$cachetag_src" "$run_out"

#
# Source copy. The exclude list below is copied verbatim from
# scripts/test-with-vinyl-cache.sh, which is authoritative, with exactly one
# addition: ./release/dist, this script's own gitignored output directory. That
# directory does not exist during a harness run; excluding it keeps a previous
# run's artifacts and logs out of the tree the archive is built from, which is a
# precondition for the determinism comparison.
#
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
	--exclude=./release/dist \
	-cf - . | tar -C "$cachetag_src" -xf -

export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:$prefix/lib/aarch64-linux-gnu/pkgconfig:$prefix/lib/x86_64-linux-gnu/pkgconfig"
export PATH="$prefix/sbin:$prefix/bin:$PATH"
export LD_LIBRARY_PATH="$prefix/lib:$prefix/lib/vinyl-cache:${LD_LIBRARY_PATH:-}"
export SOURCE_DATE_EPOCH
export TZ=UTC
export LC_ALL=C

#
# VCS provenance for the built VMOD.
#
# vmodtool.py records a VCS revision in the compiled module's .vmod_vcs ELF
# section. It obtains it from `git -C <srcdir> rev-parse HEAD`, and falls back
# to a file named src/vmod_vcs_version.txt when that fails.
#
# Both of those go wrong in a container build, and the second one goes wrong
# silently. The tree is copied without .git, so the git lookup always fails.
# The fallback file is gitignored but is NOT in the harness's exclude list, so
# whatever stale copy a previous host-local build left in the working tree gets
# copied in and believed. On this tree that was a commit 269 revisions behind
# HEAD, left there on 18 June -- so an ordinary build does not record "NOGIT",
# it records a confident and completely wrong commit id.
#
# Writing the file unconditionally here (overwrite, not create-if-missing)
# makes the release build's binary carry the real cachetag commit whether or
# not a stale one was copied in. It changes nothing in the distributed tarball:
# the file is neither in EXTRA_DIST nor a build product that make dist ships,
# which the two identical determinism digests confirm.
#
printf '%s' "$CACHETAG_COMMIT" > "$cachetag_src/src/vmod_vcs_version.txt"

cd "$cachetag_src"
if [ "$CACHE_TAG_BUILD_PROFILE" = production ]; then
	CPPFLAGS="$profile_cppflags" \
	CFLAGS="$profile_cflags" \
	LDFLAGS="$profile_ldflags" \
	./bootstrap --prefix="$prefix"
else
	./bootstrap --prefix="$prefix"
fi

make -j"$(nproc)"

# The complete logs are copied out as well as printed. A truncated window is
# how the first failing expectation gets lost, which the plan's flake policy
# calls out explicitly: a failure nobody can classify is an ignored failure.
dump_failure_logs() {
	local dest="/work/failure-logs/$LOG_TAG"
	mkdir -p "$dest"
	local i=0
	find . -type f -name test-suite.log -print | while IFS= read -r log; do
		i=$((i + 1))
		cp "$log" "$dest/$i-$(printf '%s' "$log" | tr '/' '_')" 2>/dev/null || true
		printf '\n===== %s =====\n' "$log" >&2
		sed -n "1,${FAILURE_LOG_LINES}p" "$log" >&2
	done
	printf '\ncomplete test-suite logs preserved under %s\n' "$dest" >&2
}

printf '\n===== make %s (run %s) =====\n' "$CHECK_TARGET" "$RUN_ID"
if ! make "$CHECK_TARGET"; then
	dump_failure_logs
	exit 1
fi

raw="$cachetag_src/$ARCHIVE_NAME"
[ -f "$raw" ] || { printf 'no archive produced at %s\n' "$raw" >&2; exit 1; }

#
# Deterministic repack.
#
# `make dist` is validated by distcheck but is not reproducible on its own: it
# records the build user/group, the working-tree mtimes, and a gzip header
# timestamp, and it emits members in the order automake happened to assemble
# DISTFILES. The accepted technique is to let distcheck validate the dist, then
# repack the *verified* tree with normalised metadata. The member set is not
# touched -- only ownership, order, timestamps and the gzip header.
#
repack=/tmp/repack
rm -rf "$repack"
mkdir -p "$repack"
tar -C "$repack" -xzf "$raw"
[ -d "$repack/$DISTDIR_NAME" ] || {
	printf 'unexpected archive layout: no %s in %s\n' "$DISTDIR_NAME" "$raw" >&2
	exit 1
}

# --format=ustar is the format automake's own `make dist` rule emits
# (tardir=... && tar --format=ustar -chf -). Keeping it means the repack
# changes only member metadata and ordering, never the archive format.
tar --format=ustar \
	--sort=name \
	--owner=0 --group=0 --numeric-owner \
	--mtime="@$SOURCE_DATE_EPOCH" \
	-C "$repack" -cf - "$DISTDIR_NAME" \
	| gzip -9n > "$run_out/$ARCHIVE_NAME"

cp "$raw" "$run_out/${DISTDIR_NAME}.dist-raw.tar.gz"

# Evidence for the determinism comparison: a full member listing with the
# normalised metadata, and a per-member content digest that is independent of
# tar metadata entirely.
tar -tvf "$run_out/$ARCHIVE_NAME" | sort > "$run_out/members.txt"
( cd "$repack/$DISTDIR_NAME" && find . -type f -print0 | sort -z |
	xargs -0 sha256sum ) > "$run_out/content-digests.txt"

sha256sum "$run_out/$ARCHIVE_NAME" | cut -d' ' -f1 > "$run_out/archive.sha256"
sha256sum "$run_out/${DISTDIR_NAME}.dist-raw.tar.gz" | cut -d' ' -f1 > "$run_out/dist-raw.sha256"

printf '\n===== run %s archive =====\n' "$RUN_ID"
printf 'members       : %s\n' "$(wc -l < "$run_out/members.txt" | tr -d ' ')"
printf 'sha256        : %s\n' "$(cat "$run_out/archive.sha256")"
printf 'dist-raw sha  : %s\n' "$(cat "$run_out/dist-raw.sha256")"
CONTAINER_ARCHIVE

cat > "$work_dir/container/from-archive.sh" <<'CONTAINER_FROM'
#!/bin/bash
#
# Prove the archive is buildable on its own, with no bootstrap/autoreconf.
#
set -euo pipefail

prefix=/tmp/vinyl-prefix
tree=/tmp/from-archive

rm -rf "$tree"
mkdir -p "$tree"
tar -C "$tree" -xzf "/work/dist/$ARCHIVE_NAME"
cd "$tree/$DISTDIR_NAME"

[ -f configure ] || { printf 'archive has no generated configure\n' >&2; exit 1; }
[ -f Makefile.in ] || { printf 'archive has no generated Makefile.in\n' >&2; exit 1; }
[ -f src/Makefile.in ] || { printf 'archive has no generated src/Makefile.in\n' >&2; exit 1; }

export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:$prefix/lib/aarch64-linux-gnu/pkgconfig:$prefix/lib/x86_64-linux-gnu/pkgconfig"
export PATH="$prefix/sbin:$prefix/bin:$PATH"
export LD_LIBRARY_PATH="$prefix/lib:$prefix/lib/vinyl-cache:${LD_LIBRARY_PATH:-}"
export SOURCE_DATE_EPOCH
export TZ=UTC

# Tripwire: if anything in this build reaches for the autotools, the archive is
# not self-contained and the build must fail loudly rather than quietly
# regenerating what it was supposed to ship.
autotools_off=(
	ACLOCAL=/bin/false
	AUTOCONF=/bin/false
	AUTOHEADER=/bin/false
	AUTOMAKE=/bin/false
	AUTORECONF=/bin/false
	LIBTOOLIZE=/bin/false
)

printf '\n===== configure from archive (no bootstrap) =====\n'
./configure --prefix="$prefix" "${autotools_off[@]}"

printf '\n===== make from archive =====\n'
make -j"$(nproc)" "${autotools_off[@]}"

# The complete logs are copied out as well as printed. A truncated window is
# how the first failing expectation gets lost, which the plan's flake policy
# calls out explicitly: a failure nobody can classify is an ignored failure.
dump_failure_logs() {
	local dest="/work/failure-logs/$LOG_TAG"
	mkdir -p "$dest"
	local i=0
	find . -type f -name test-suite.log -print | while IFS= read -r log; do
		i=$((i + 1))
		cp "$log" "$dest/$i-$(printf '%s' "$log" | tr '/' '_')" 2>/dev/null || true
		printf '\n===== %s =====\n' "$log" >&2
		sed -n "1,${FAILURE_LOG_LINES}p" "$log" >&2
	done
	printf '\ncomplete test-suite logs preserved under %s\n' "$dest" >&2
}

printf '\n===== make %s from archive =====\n' "$FROM_ARCHIVE_TARGET"
if [ -n "${FROM_ARCHIVE_TESTS:-}" ]; then
	if ! make "$FROM_ARCHIVE_TARGET" TESTS="$FROM_ARCHIVE_TESTS" "${autotools_off[@]}"; then
		dump_failure_logs
		exit 1
	fi
else
	if ! make "$FROM_ARCHIVE_TARGET" "${autotools_off[@]}"; then
		dump_failure_logs
		exit 1
	fi
fi

printf '\n===== from-archive build OK =====\n'
printf 'built VMOD: %s\n' "$(ls -l src/.libs/libvmod_cachetag.so 2>/dev/null || echo '<none>')"
CONTAINER_FROM

chmod +x "$work_dir"/container/*.sh

#
# Stage 2: pinned Vinyl prefix, in a content-addressed Docker volume.
#
# The volume name embeds the pinned source digest and the build profile, so a
# prefix can only ever be reused for the exact inputs that produced it. A stamp
# file inside the volume is checked as well, so an interrupted build cannot be
# mistaken for a complete one.
#
vinyl_volume="cachetag-relsrc-${vinyl_sha256_short}-${build_profile}"
vinyl_stamp_want="$vinyl_sha256 $build_profile"

vinyl_cached=0
if [ "$rebuild_vinyl" -eq 0 ] && docker volume inspect "$vinyl_volume" >/dev/null 2>&1; then
	got=$(docker run --rm -v "$vinyl_volume:/tmp/vinyl-prefix:ro" "$image" \
		bash -c 'cat /tmp/vinyl-prefix/.cachetag-release-stamp 2>/dev/null || true' 2>/dev/null || true)
	if [ "$got" = "$vinyl_stamp_want" ]; then
		vinyl_cached=1
	fi
fi

if [ "$vinyl_cached" -eq 1 ]; then
	note "reusing pinned Vinyl prefix volume $vinyl_volume"
else
	note "building pinned Vinyl into volume $vinyl_volume"
	docker volume rm "$vinyl_volume" >/dev/null 2>&1 || true
	docker volume create "$vinyl_volume" >/dev/null
	docker_step "$output_dir/logs/vinyl-build.log" \
		docker run --rm \
		-v "$work_dir:/work" \
		-v "$vinyl_volume:/tmp/vinyl-prefix" \
		-e "CACHE_TAG_BUILD_PROFILE=$build_profile" \
		-e "VINYL_SOURCE_BASENAME=$vinyl_source_basename" \
		-e "VINYL_SHA256=$vinyl_sha256" \
		-e "SOURCE_DATE_EPOCH=$source_date_epoch" \
		"$image" \
		bash /work/container/vinyl-build.sh ||
		die "pinned Vinyl build failed (see logs/vinyl-build.log)"
fi

#
# Stage 3: archive production, once per run.
#
# Skipped entirely in --from-archive-only mode, which re-runs only the rebuild
# proof against the archive already published in the output directory.
#
run_sha=""
run_dist_raw_sha=""
n=1
while [ "$from_archive_only" -eq 0 ] && [ "$n" -le "$runs" ]; do
	note "archive production run $n of $runs (make $check_target)"
	docker_step "$output_dir/logs/archive-run$n.log" \
		docker run --rm \
		-v "$repo_dir:/cachetag-host:ro" \
		-v "$work_dir:/work" \
		-v "$vinyl_volume:/tmp/vinyl-prefix:ro" \
		-e "CACHE_TAG_BUILD_PROFILE=$build_profile" \
		-e "SOURCE_DATE_EPOCH=$source_date_epoch" \
		-e "CACHETAG_COMMIT=$cachetag_commit" \
		-e "ARCHIVE_NAME=$archive_name" \
		-e "DISTDIR_NAME=$distdir_name" \
		-e "CHECK_TARGET=$check_target" \
		-e "RUN_ID=$n" \
		-e "LOG_TAG=archive-run$n" \
		-e "FAILURE_LOG_LINES=$failure_log_lines" \
		"$image" \
		bash /work/container/archive.sh ||
		die "archive production run $n failed (see logs/archive-run$n.log)"

	this_sha=$(cat "$work_dir/run$n/archive.sha256")
	this_raw=$(cat "$work_dir/run$n/dist-raw.sha256")
	run_sha="$run_sha $this_sha"
	run_dist_raw_sha="$run_dist_raw_sha $this_raw"
	n=$((n + 1))
done

if [ "$from_archive_only" -eq 1 ]; then
	[ -f "$output_dir/$archive_name" ] ||
		die "--from-archive-only needs an existing archive at $output_dir/$archive_name"
	cp "$output_dir/$archive_name" "$work_dir/dist/$archive_name"
	archive_sha256=$(sha256_file "$work_dir/dist/$archive_name")
	dist_raw_sha256=""
	archive_size=$(wc -c < "$work_dir/dist/$archive_name" | tr -d ' ')
	member_count=""
	note "--from-archive-only: re-proving the published archive $archive_sha256"
else
	# Run 1 is canonical.
	cp "$work_dir/run1/$archive_name" "$work_dir/dist/$archive_name"
	cp "$work_dir/run1/${distdir_name}.dist-raw.tar.gz" "$work_dir/dist/"

	archive_sha256=$(sha256_file "$work_dir/dist/$archive_name")
	dist_raw_sha256=$(sha256_file "$work_dir/dist/${distdir_name}.dist-raw.tar.gz")
	archive_size=$(wc -c < "$work_dir/dist/$archive_name" | tr -d ' ')
	member_count=$(wc -l < "$work_dir/run1/members.txt" | tr -d ' ')
fi

#
# Stage 4: determinism comparison.
#
determinism_identical="n/a"
if [ "$from_archive_only" -eq 0 ] && [ "$runs" -ge 2 ]; then
	note "determinism comparison across $runs runs"
	first=""
	identical=true
	for s in $run_sha; do
		if [ -z "$first" ]; then first=$s; elif [ "$s" != "$first" ]; then identical=false; fi
	done
	i=1
	for s in $run_sha; do
		printf 'run %s archive sha256: %s\n' "$i" "$s"
		i=$((i + 1))
	done
	i=1
	for s in $run_dist_raw_sha; do
		printf 'run %s make-dist raw  : %s\n' "$i" "$s"
		i=$((i + 1))
	done
	if [ "$identical" = true ]; then
		determinism_identical="true"
		printf '\nDETERMINISM: PASS -- every run produced the same archive digest\n'
	else
		determinism_identical="false"
		printf '\nDETERMINISM: FAIL -- digests differ; diffing run 1 against run 2\n'
		mkdir -p "$output_dir/determinism"
		diff -u "$work_dir/run1/members.txt" "$work_dir/run2/members.txt" \
			> "$output_dir/determinism/members.diff" || true
		diff -u "$work_dir/run1/content-digests.txt" "$work_dir/run2/content-digests.txt" \
			> "$output_dir/determinism/content-digests.diff" || true
		cp "$work_dir/run2/$archive_name" "$output_dir/determinism/run2-$archive_name"
		printf 'member metadata diff : %s\n' "$output_dir/determinism/members.diff"
		printf 'file content diff    : %s\n' "$output_dir/determinism/content-digests.diff"
		sed -n '1,60p' "$output_dir/determinism/content-digests.diff" || true
	fi
fi

#
# Stage 5: publish the archive, then rebuild from it in a fresh container.
#
if [ "$from_archive_only" -eq 0 ]; then
	cp "$work_dir/dist/$archive_name" "$output_dir/$archive_name"
	cp "$work_dir/dist/${distdir_name}.dist-raw.tar.gz" "$output_dir/${distdir_name}.dist-raw.tar.gz"
	cp "$work_dir/run1/members.txt" "$output_dir/${distdir_name}.members.txt"
	printf '%s  %s\n' "$archive_sha256" "$archive_name" > "$output_dir/$archive_name.sha256"
	from_archive_log_tag="from-archive"
else
	# Never overwrite the first result: the quarantine policy requires the
	# original failure to stay visible alongside any re-run.
	rerun=1
	while [ -e "$output_dir/logs/from-archive-rerun$rerun.log" ]; do
		rerun=$((rerun + 1))
	done
	from_archive_log_tag="from-archive-rerun$rerun"
fi

from_archive_result="skipped"
if [ "$from_archive_target" != "none" ]; then
	note "from-archive rebuild proof in a fresh container (make $from_archive_target)"
	from_archive_status=0
	docker_step "$output_dir/logs/$from_archive_log_tag.log" \
		docker run --rm \
		-v "$work_dir:/work" \
		-v "$vinyl_volume:/tmp/vinyl-prefix:ro" \
		-e "ARCHIVE_NAME=$archive_name" \
		-e "DISTDIR_NAME=$distdir_name" \
		-e "FROM_ARCHIVE_TARGET=$from_archive_target" \
		-e "FROM_ARCHIVE_TESTS=$from_archive_tests" \
		-e "SOURCE_DATE_EPOCH=$source_date_epoch" \
		-e "LOG_TAG=$from_archive_log_tag" \
		-e "FAILURE_LOG_LINES=$failure_log_lines" \
		"$image" \
		bash /work/container/from-archive.sh || from_archive_status=$?
	# Deliberately NOT fatal here: a failure must still reach the metadata
	# sidecar and the summary below, and only then set the exit status.
	if [ "$from_archive_status" -eq 0 ]; then
		from_archive_result="pass"
	else
		from_archive_result="fail"
	fi
fi

#
# Preserve any complete failure logs produced inside the containers.
#
preserve_failure_logs

#
# Stage 6: metadata sidecar.
#
# In --from-archive-only mode the original metadata is left exactly as it was --
# the first result must stay visible -- and the re-run gets its own small record.
#
if [ "$from_archive_only" -eq 1 ]; then
	rerun_metadata="$output_dir/${distdir_name}.${from_archive_log_tag}.json"
	{
		printf '{\n'
		printf '  "schema": "cachetag-from-archive-rerun/v1",\n'
		printf '  "note": "re-run of the from-archive rebuild proof only; the archive and its original metadata are unchanged",\n'
		printf '  "archive": "%s",\n' "$archive_name"
		printf '  "archive_sha256": "%s",\n' "$archive_sha256"
		printf '  "target": "%s",\n' "$from_archive_target"
		printf '  "tests": "%s",\n' "$(json_escape "$from_archive_tests")"
		printf '  "log": "logs/%s.log",\n' "$from_archive_log_tag"
		printf '  "result": "%s"\n' "$from_archive_result"
		printf '}\n'
	} > "$rerun_metadata"
	note "from-archive re-run"
	printf 'archive        : %s\n' "$output_dir/$archive_name"
	printf 'sha256         : %s\n' "$archive_sha256"
	printf 'result         : %s\n' "$from_archive_result"
	printf 'record         : %s\n' "$rerun_metadata"
	if [ "$keep_work" -eq 0 ]; then reown_work "$work_dir"; rm -rf "$work_dir"; fi
	[ "$from_archive_result" = "pass" ] || die "the archive could not be built on its own; see logs/$from_archive_log_tag.log"
	exit 0
fi

metadata="$output_dir/${distdir_name}.metadata.json"
{
	printf '{\n'
	printf '  "schema": "cachetag-source-archive/v1",\n'
	printf '  "generated_by": "scripts/release-source-archive.sh",\n'
	printf '  "mode": "%s",\n' "$mode"
	printf '  "release_stamp": "%s",\n' "$(json_escape "$release_stamp")"
	printf '  "cachetag": {\n'
	printf '    "version": "%s",\n' "$cachetag_version"
	printf '    "git_commit": "%s",\n' "$cachetag_commit"
	printf '    "worktree_dirty": %s,\n' "$worktree_dirty"
	printf '    "head_tag": %s,\n' "$([ -n "$head_tag" ] && printf '"%s"' "$head_tag" || printf null)"
	printf '    "source_date_epoch": %s,\n' "$source_date_epoch"
	printf '    "source_date_utc": "%s"\n' "$source_date_utc"
	printf '  },\n'
	printf '  "vinyl_input": {\n'
	printf '    "kind": "%s",\n' "$vinyl_kind"
	printf '    "checkout": %s,\n' "$([ -n "$vinyl_git" ] && printf '"%s"' "$(json_escape "$vinyl_git")" || printf null)"
	printf '    "ref": %s,\n' "$([ -n "$vinyl_git" ] && printf '"%s"' "$(json_escape "$vinyl_ref")" || printf null)"
	printf '    "git_commit": %s,\n' "$([ -n "$vinyl_commit" ] && printf '"%s"' "$vinyl_commit" || printf null)"
	printf '    "source_archive": "%s",\n' "$(json_escape "$vinyl_source_basename")"
	printf '    "source_sha256": "%s",\n' "$vinyl_sha256"
	printf '    "submodules": ['
	sep=""
	if [ -s "$work_dir/vinyl-submodules.txt" ]; then
		while read -r sub_commit sub_path; do
			[ -n "${sub_path:-}" ] || continue
			printf '%s{"path": "%s", "commit": "%s"}' \
				"$sep" "$(json_escape "$sub_path")" "$sub_commit"
			sep=", "
		done < "$work_dir/vinyl-submodules.txt"
	fi
	printf ']\n'
	printf '  },\n'
	printf '  "build": {\n'
	printf '    "image": "%s",\n' "$(json_escape "$image")"
	printf '    "image_id": "%s",\n' "$image_id"
	printf '    "profile": "%s",\n' "$build_profile"
	printf '    "check_target": "%s",\n' "$check_target"
	printf '    "vinyl_prefix_volume": "%s"\n' "$vinyl_volume"
	printf '  },\n'
	printf '  "archive": {\n'
	printf '    "filename": "%s",\n' "$archive_name"
	printf '    "sha256": "%s",\n' "$archive_sha256"
	printf '    "bytes": %s,\n' "$archive_size"
	printf '    "members": %s,\n' "$member_count"
	printf '    "dist_raw_sha256": "%s",\n' "$dist_raw_sha256"
	printf '    "normalization": {\n'
	printf '      "tar_format": "ustar (same format make dist emits)",\n'
	printf '      "member_order": "--sort=name",\n'
	printf '      "owner": "uid 0 / gid 0, --numeric-owner",\n'
	printf '      "mtime": "every member set to SOURCE_DATE_EPOCH (%s)",\n' "$source_date_epoch"
	printf '      "gzip": "gzip -9n (no name, no timestamp in the header)",\n'
	printf '      "modes": "left as make dist produced them"\n'
	printf '    }\n'
	printf '  },\n'
	printf '  "determinism": {\n'
	printf '    "runs": %s,\n' "$runs"
	printf '    "identical": %s,\n' "$([ "$determinism_identical" = "n/a" ] && printf null || printf '%s' "$determinism_identical")"
	printf '    "archive_sha256": ['
	sep=""
	for s in $run_sha; do printf '%s"%s"' "$sep" "$s"; sep=", "; done
	printf '],\n'
	printf '    "dist_raw_sha256": ['
	sep=""
	for s in $run_dist_raw_sha; do printf '%s"%s"' "$sep" "$s"; sep=", "; done
	printf ']\n'
	printf '  },\n'
	printf '  "from_archive_build": {\n'
	printf '    "target": "%s",\n' "$from_archive_target"
	printf '    "tests": "%s",\n' "$(json_escape "$from_archive_tests")"
	printf '    "result": "%s",\n' "$from_archive_result"
	printf '    "autotools_disabled": true\n'
	printf '  }\n'
	printf '}\n'
} > "$metadata"

if [ "$keep_work" -eq 0 ]; then
	reown_work "$work_dir"
	rm -rf "$work_dir"
else
	printf '\nwork directory kept: %s\n' "$work_dir"
fi

note "release source archive"
printf 'archive        : %s\n' "$output_dir/$archive_name"
printf 'sha256         : %s\n' "$archive_sha256"
printf 'members        : %s\n' "$member_count"
printf 'metadata       : %s\n' "$metadata"
printf 'mode           : %s (%s)\n' "$mode" "$release_stamp"
printf 'determinism    : %s\n' "$determinism_identical"
printf 'from-archive   : %s\n' "$from_archive_result"

if [ "$determinism_identical" = "false" ]; then
	die "archive production is not reproducible; see $output_dir/determinism/"
fi
if [ "$from_archive_result" = "fail" ]; then
	die "the archive could not be built on its own; see logs/from-archive.log"
fi
exit 0
