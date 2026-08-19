#!/bin/sh
# Fail-closed build provenance for benchmark artifacts (BR-016).
#
# Usage:
#   build_provenance.sh identity <cachetag-src> <vinyl-src> <slash-src|none> <xkey-src|none> <storage-kind> <ignored>
#   build_provenance.sh record <cachetag-src> <vinyl-src> <slash-src|none> <xkey-src|none> <storage-kind> <out-file>
#   build_provenance.sh verify <cachetag-src> <vinyl-src> <slash-src|none> <xkey-src|none> <storage-kind> <provenance-file>
#
# A comparative row is only valid when every mounted source is a clean Git
# checkout and the cached binaries, reviewed compatibility artifact, build
# commands, flags, Dockerfile, and local image identity match the record.
set -eu

PINNED_XKEY_COMMIT=7abe0e2a59a685b4ea8626ff1a3fe9c60a037368
provenance_mode=${BUILD_PROVENANCE_MODE:-strict}
case "$provenance_mode" in
	strict|development) ;;
	*) echo "BUILD_PROVENANCE_MODE must be strict or development" >&2; exit 2 ;;
esac

sha() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum
	else
		shasum -a 256
	fi
}

sha_file() {
	test -f "$1" || die "required artifact is missing: $1"
	sha < "$1" | awk '{print $1}'
}

die() {
	echo "BUILD PROVENANCE FAILURE [BR-016]: $*" >&2
	exit 1
}

trust_git_directory() {
	trusted_dir=$(CDPATH= cd -- "$1" && pwd -P) ||
		die "cannot resolve source directory: $1"
	git config --global --get-all safe.directory 2>/dev/null | grep -Fx "$trusted_dir" >/dev/null ||
		git config --global --add safe.directory "$trusted_dir" ||
		die "cannot mark source as a trusted Git worktree: $trusted_dir"
}

require_clean_git() {
	name=$1
	dir=$2
	test -d "$dir" || die "$name source directory does not exist: $dir"
	trust_git_directory "$dir"
	git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
		die "$name source is not a Git worktree: $dir"
	status=$(git -C "$dir" status --porcelain=v1 --untracked-files=all)
	test -z "$status" || die "$name source is dirty or has untracked files"
}

require_git() {
	name=$1
	dir=$2
	test -d "$dir" || die "$name source directory does not exist: $dir"
	# Docker runs benchmark commands as root while bind-mounted checkouts often
	# retain their host UID. Trust only this explicit source path so Git can
	# inspect it; the clean-tree and pinned-revision checks below remain strict.
	trust_git_directory "$dir"
	git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
		die "$name source is not a Git worktree: $dir"
}

dirty_state() {
	name=$1
	dir=$2
	require_git "$name" "$dir"
	status=$(git -C "$dir" status --porcelain=v1 --untracked-files=all)
	if [ -z "$status" ]; then printf clean; else printf dirty; fi
}

check_source_policy() {
	name=$1
	dir=$2
	require_git "$name" "$dir"
	state=$(dirty_state "$name" "$dir")
	if [ "$provenance_mode" = strict ] && [ "$state" != clean ]; then
		die "$name source is dirty or has untracked files"
	fi
}

git_value() {
	dir=$1
	field=$2
	case "$field" in
	commit) git -C "$dir" rev-parse --verify HEAD^{commit} ;;
	tree) git -C "$dir" rev-parse --verify HEAD^{tree} ;;
	diff) git -C "$dir" diff --binary HEAD | sha | awk '{print $1}' ;;
	tree_manifest) git -C "$dir" ls-tree -r -z HEAD | sha | awk '{print $1}' ;;
	*) die "unknown Git identity field: $field" ;;
	esac
}

# Content hash of the inputs copied into the benchmark build. Path names and
# per-file hashes are included, avoiding ambiguous concatenation hashes.
tree_hash() {
	dir=$1
	shift
	(
		cd "$dir"
		found=0
		for candidate in "$@"; do
			if [ -e "$candidate" ]; then
				found=1
			fi
		done
		if [ "$found" = 0 ]; then
			printf none
			return
		fi
		for candidate in "$@"; do
			if [ -e "$candidate" ]; then
				find "$candidate" \
					\( -name .libs -o -name .deps -o -name .git -o -name autom4te.cache -o -name results -o -name remote-results \) -prune -o \
					-type f \
					! -name '*.o' ! -name '*.lo' ! -name '*.la' ! -name '*.so' \
					! -name Makefile ! -name Makefile.in ! -name '*.pyc' \
					-print
			fi
		done | LC_ALL=C sort | while IFS= read -r path; do
			printf '%s\0%s\0' "$path" "$(sha < "$path" | awk '{print $1}')"
		done | sha | awk '{print $1}'
	)
}

source_fields() {
	name=$1
	dir=$2
	if [ "$dir" = none ]; then
		printf '%s_dirty_state=not-applicable\n%s_git_commit=none\n%s_git_tree=none\n%s_git_tree_manifest_sha256=none\n%s_git_diff_sha256=none\n' "$name" "$name" "$name" "$name" "$name"
		return
	fi
	check_source_policy "$name" "$dir"
	if [ "$name" = xkey ]; then
		commit=$(git_value "$dir" commit)
		test "$commit" = "$PINNED_XKEY_COMMIT" ||
			die "xkey must be varnish-modules 0.28.0 at $PINNED_XKEY_COMMIT (got $commit)"
		tag=$(git -C "$dir" describe --tags --exact-match HEAD 2>/dev/null || true)
		test "$tag" = 0.28.0 || die "xkey HEAD must carry the 0.28.0 tag (got ${tag:-none})"
	fi
	printf '%s_dirty_state=%s\n' "$name" "$(dirty_state "$name" "$dir")"
	printf '%s_git_commit=%s\n' "$name" "$(git_value "$dir" commit)"
	printf '%s_git_tree=%s\n' "$name" "$(git_value "$dir" tree)"
	printf '%s_git_tree_manifest_sha256=%s\n' "$name" "$(git_value "$dir" tree_manifest)"
	printf '%s_git_diff_sha256=%s\n' "$name" "$(git_value "$dir" diff)"
}

source_value() {
	name=$1
	dir=$2
	field=$3
	if [ "$dir" = none ]; then
		case "$field" in
		dirty_state) printf not-applicable ;;
		*) printf none ;;
		esac
		return
	fi
	check_source_policy "$name" "$dir"
	if [ "$name" = xkey ]; then
		commit=$(git_value "$dir" commit)
		test "$commit" = "$PINNED_XKEY_COMMIT" ||
			die "xkey must be varnish-modules 0.28.0 at $PINNED_XKEY_COMMIT (got $commit)"
		tag=$(git -C "$dir" describe --tags --exact-match HEAD 2>/dev/null || true)
		test "$tag" = 0.28.0 || die "xkey HEAD must carry the 0.28.0 tag (got ${tag:-none})"
	fi
	case "$field" in
	dirty_state) dirty_state "$name" "$dir" ;;
	git_commit) git_value "$dir" commit ;;
	git_tree) git_value "$dir" tree ;;
	git_tree_manifest_sha256) git_value "$dir" tree_manifest ;;
	git_diff_sha256) git_value "$dir" diff ;;
	*) die "unknown source identity field: $field" ;;
	esac
}

tool_version() {
	case "$1" in
		cc) cc --version | sed -n '1p' ;;
		ld) ld --version | sed -n '1p' ;;
		make) make --version | sed -n '1p' ;;
		python3) python3 --version | sed -n '1p' ;;
		go) go version ;;
		git) git --version ;;
		*) die "unknown tool: $1" ;;
	esac
}

require_env() {
	eval "value=\${$1:-}"
	test -n "$value" || die "required environment variable $1 is unset"
	printf '%s' "$value"
}

require_flag_env() {
	eval "marker=\${$1+x}"
	test "$marker" = x || die "required environment variable $1 is unset"
	eval "value=\${$1-}"
	if test -n "$value"; then
		printf '%s' "$value"
	else
		printf none
	fi
}

read_field() {
	key=$1
	file=$2
	value=$(sed -n "s/^${key}=//p" "$file")
	test -n "$value" || die "provenance field is absent: $key"
	printf '%s' "$value"
}

emit_record() {
	cachetag_src=$1
	vinyl_src=$2
	slash_src=$3
	xkey_src=$4
	storage_kind=$5

	compat=$(require_env BUILD_PROVENANCE_XKEY_COMPAT_ARTIFACT)
	xkey_config=$(require_env BUILD_PROVENANCE_XKEY_CONFIG_ARTIFACT)
	cachetag_binary=$(require_env BUILD_PROVENANCE_CACHETAG_BINARY)
	vinyl_binary=$(require_env BUILD_PROVENANCE_VINYL_BINARY)
	xkey_binary=$(require_env BUILD_PROVENANCE_XKEY_BINARY)
	commands=$(require_env BUILD_PROVENANCE_BUILD_COMMANDS_FILE)
	dockerfile=$(require_env BUILD_PROVENANCE_DOCKERFILE)
	image_ref=$(require_env BUILD_PROVENANCE_IMAGE_REF)
	image_id=$(require_env BUILD_PROVENANCE_IMAGE_ID)
	build_cflags=$(require_flag_env BUILD_PROVENANCE_CFLAGS)
	build_cppflags=$(require_flag_env BUILD_PROVENANCE_CPPFLAGS)
	build_ldflags=$(require_flag_env BUILD_PROVENANCE_LDFLAGS)
	set_interning=$(require_env BUILD_PROVENANCE_SET_INTERNING)
	cachetag_configure_args=$(require_env BUILD_PROVENANCE_CACHETAG_CONFIGURE_ARGS)
	case "$set_interning:$cachetag_configure_args" in
	0:--disable-set-interning|1:--enable-set-interning) ;;
	*) die "set interning selection and configure arguments disagree" ;;
	esac

	printf 'build_provenance_version=4\n'
	printf 'build_provenance_mode=%s\n' "$provenance_mode"
	if [ "$provenance_mode" = strict ]; then
		printf 'build_provenance_eligible=1\n'
		printf 'build_provenance_ineligibility_reason=none\n'
	else
		printf 'build_provenance_eligible=0\n'
		printf 'build_provenance_ineligibility_reason=development_mode\n'
	fi
	printf 'build_time_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	printf 'build_storage_kind=%s\n' "$storage_kind"
	printf 'xkey_pinned_commit=%s\n' "$PINNED_XKEY_COMMIT"
	source_fields cachetag "$cachetag_src"
	source_fields vinyl "$vinyl_src"
	source_fields slash "$slash_src"
	source_fields xkey "$xkey_src"
	printf 'cachetag_build_input_sha256=%s\n' "$(tree_hash "$cachetag_src" src configure.ac Makefile.am bootstrap patches benchmarks scripts docker)"
	printf 'vinyl_build_input_sha256=%s\n' "$(tree_hash "$vinyl_src" bin lib include vmod configure.ac Makefile.am)"
	if [ "$slash_src" = none ]; then
		printf 'slash_build_input_sha256=none\n'
	else
		printf 'slash_build_input_sha256=%s\n' "$(tree_hash "$slash_src" src include configure.ac Makefile.am bootstrap)"
	fi
	if [ "$xkey_src" = none ]; then
		printf 'xkey_build_input_sha256=none\n'
	else
		printf 'xkey_build_input_sha256=%s\n' "$(tree_hash "$xkey_src" src configure.ac Makefile.am bootstrap)"
	fi
	printf 'xkey_compat_artifact_sha256=%s\n' "$(sha_file "$compat")"
	printf 'xkey_config_sha256=%s\n' "$(sha_file "$xkey_config")"
	printf 'cachetag_binary_sha256=%s\n' "$(sha_file "$cachetag_binary")"
	printf 'vinyl_binary_sha256=%s\n' "$(sha_file "$vinyl_binary")"
	printf 'xkey_binary_sha256=%s\n' "$(sha_file "$xkey_binary")"
	printf 'build_commands_sha256=%s\n' "$(sha_file "$commands")"
	printf 'build_cflags=%s\n' "$build_cflags"
	printf 'build_cppflags=%s\n' "$build_cppflags"
	printf 'build_ldflags=%s\n' "$build_ldflags"
	printf 'bench_set_interning=%s\n' "$set_interning"
	printf 'cachetag_configure_args=%s\n' "$cachetag_configure_args"
	printf 'dockerfile_sha256=%s\n' "$(sha_file "$dockerfile")"
	printf 'docker_image_ref=%s\n' "$image_ref"
	printf 'docker_image_id=%s\n' "$image_id"
	for tool in cc ld make python3 go git; do
		printf 'tool_%s=%s\n' "$tool" "$(tool_version "$tool")"
	done
}

verify_record() {
	cachetag_src=$1
	vinyl_src=$2
	slash_src=$3
	xkey_src=$4
	storage_kind=$5
	file=$6
	test -f "$file" || die "no provenance file at $file"
	test "$(read_field build_provenance_version "$file")" = 4 || die "unsupported provenance version"
	test "$(read_field build_provenance_mode "$file")" = "$provenance_mode" || die "provenance mode changed"
	if [ "$provenance_mode" = strict ]; then
		test "$(read_field build_provenance_eligible "$file")" = 1 || die "cached build is not comparison-eligible"
	fi
	test "$(read_field build_storage_kind "$file")" = "$storage_kind" || die "storage kind changed"
	test "$(read_field xkey_pinned_commit "$file")" = "$PINNED_XKEY_COMMIT" || die "xkey pin changed"

	for source in cachetag vinyl slash xkey; do
		eval "source_dir=\${${source}_src}"
		for field in dirty_state git_commit git_tree git_tree_manifest_sha256 git_diff_sha256; do
			current=$(source_value "$source" "$source_dir" "$field")
			recorded=$(read_field "${source}_${field}" "$file")
			test "$current" = "$recorded" || die "$source $field changed"
		done
	done

	check_hash() {
		key=$1
		current=$2
		recorded=$(read_field "$key" "$file")
		test "$current" = "$recorded" || die "$key changed"
	}
	check_hash cachetag_build_input_sha256 "$(tree_hash "$cachetag_src" src configure.ac Makefile.am bootstrap patches benchmarks scripts docker)"
	check_hash vinyl_build_input_sha256 "$(tree_hash "$vinyl_src" bin lib include vmod configure.ac Makefile.am)"
	if [ "$slash_src" = none ]; then slash_hash=none; else slash_hash=$(tree_hash "$slash_src" src include configure.ac Makefile.am bootstrap); fi
	if [ "$xkey_src" = none ]; then xkey_hash=none; else xkey_hash=$(tree_hash "$xkey_src" src configure.ac Makefile.am bootstrap); fi
	check_hash slash_build_input_sha256 "$slash_hash"
	check_hash xkey_build_input_sha256 "$xkey_hash"

	compat=$(require_env BUILD_PROVENANCE_XKEY_COMPAT_ARTIFACT)
	xkey_config=$(require_env BUILD_PROVENANCE_XKEY_CONFIG_ARTIFACT)
	cachetag_binary=$(require_env BUILD_PROVENANCE_CACHETAG_BINARY)
	vinyl_binary=$(require_env BUILD_PROVENANCE_VINYL_BINARY)
	xkey_binary=$(require_env BUILD_PROVENANCE_XKEY_BINARY)
	commands=$(require_env BUILD_PROVENANCE_BUILD_COMMANDS_FILE)
	dockerfile=$(require_env BUILD_PROVENANCE_DOCKERFILE)
	check_hash xkey_compat_artifact_sha256 "$(sha_file "$compat")"
	check_hash xkey_config_sha256 "$(sha_file "$xkey_config")"
	check_hash cachetag_binary_sha256 "$(sha_file "$cachetag_binary")"
	check_hash vinyl_binary_sha256 "$(sha_file "$vinyl_binary")"
	check_hash xkey_binary_sha256 "$(sha_file "$xkey_binary")"
	check_hash build_commands_sha256 "$(sha_file "$commands")"
	for key in BUILD_PROVENANCE_CFLAGS BUILD_PROVENANCE_CPPFLAGS BUILD_PROVENANCE_LDFLAGS; do
		value=$(require_flag_env "$key")
		record_key=$(printf '%s' "$key" | sed 's/^BUILD_PROVENANCE_/build_/; y/ABCDEFGHIJKLMNOPQRSTUVWXYZ/abcdefghijklmnopqrstuvwxyz/')
		test "$value" = "$(read_field "$record_key" "$file")" || die "$record_key changed"
	done
	set_interning=$(require_env BUILD_PROVENANCE_SET_INTERNING)
	cachetag_configure_args=$(require_env BUILD_PROVENANCE_CACHETAG_CONFIGURE_ARGS)
	case "$set_interning:$cachetag_configure_args" in
	0:--disable-set-interning|1:--enable-set-interning) ;;
	*) die "set interning selection and configure arguments disagree" ;;
	esac
	test "$set_interning" = "$(read_field bench_set_interning "$file")" || die "bench_set_interning changed"
	test "$cachetag_configure_args" = "$(read_field cachetag_configure_args "$file")" || die "cachetag_configure_args changed"
	check_hash dockerfile_sha256 "$(sha_file "$dockerfile")"
	test "$(require_env BUILD_PROVENANCE_IMAGE_REF)" = "$(read_field docker_image_ref "$file")" || die "Docker image reference changed"
	test "$(require_env BUILD_PROVENANCE_IMAGE_ID)" = "$(read_field docker_image_id "$file")" || die "Docker image ID changed"
	for tool in cc ld make python3 go git; do
		test "$(tool_version "$tool")" = "$(read_field "tool_$tool" "$file")" || die "tool version changed: $tool"
	done
	echo "build provenance verified: $file"
}

mode=${1:?mode (record|verify) required}
cachetag_src=${2:?cachetag source dir required}
vinyl_src=${3:?vinyl source dir required}
slash_src=${4:?slash source dir or none required}
xkey_src=${5:?xkey source dir or none required}
storage_kind=${6:?storage kind required}
provenance_file=${7:?provenance file path required}

case "$mode" in
identity)
	# Preflight before a costly Docker build. source_fields is deliberately used
	# rather than a best-effort Git label: it rejects dirty trees and validates
	# the xkey pin/tag.
	source_fields cachetag "$cachetag_src" >/dev/null
	source_fields vinyl "$vinyl_src" >/dev/null
	source_fields slash "$slash_src" >/dev/null
	source_fields xkey "$xkey_src" >/dev/null
	echo "build provenance source identity verified"
	;;
record)
	tmp_file="${provenance_file}.tmp.$$"
	emit_record "$cachetag_src" "$vinyl_src" "$slash_src" "$xkey_src" "$storage_kind" > "$tmp_file"
	mv "$tmp_file" "$provenance_file"
	echo "build provenance recorded: $provenance_file"
	;;
verify)
	verify_record "$cachetag_src" "$vinyl_src" "$slash_src" "$xkey_src" "$storage_kind" "$provenance_file"
	;;
*)
	echo "unknown mode: $mode (expected record or verify)" >&2
	exit 2
	;;
esac
