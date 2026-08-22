#!/bin/sh
# Verify the exact, unmodified varnish-modules source used for xkey rows.
set -eu

expected_commit=7abe0e2a59a685b4ea8626ff1a3fe9c60a037368
src=${1:?usage: verify-source.sh <varnish-modules-source>}

fail() {
	echo "xkey source verification failed: $*" >&2
	exit 1
}

# Docker runs as root while the read-only xkey checkout may retain its host
# UID. Trust this explicit mount path only; all source integrity checks remain
# fail-closed below.
git config --global --add safe.directory "$src" ||
	fail "cannot mark source as a trusted Git worktree: $src"
git -C "$src" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
	fail "not a Git worktree: $src"
status=$(git -C "$src" status --porcelain=v1 --untracked-files=all)
test -z "$status" || fail "source is dirty or has untracked files"
commit=$(git -C "$src" rev-parse --verify HEAD^{commit})
test "$commit" = "$expected_commit" ||
	fail "expected 0.28.0 commit $expected_commit, got $commit"
tag=$(git -C "$src" describe --tags --exact-match HEAD 2>/dev/null || true)
test "$tag" = 0.28.0 || fail "expected exact 0.28.0 tag, got ${tag:-none}"

printf 'xkey_source_commit=%s\n' "$commit"
printf 'xkey_source_tree=%s\n' "$(git -C "$src" rev-parse HEAD^{tree})"
printf 'xkey_source_diff_sha256=%s\n' "$(git -C "$src" diff --binary HEAD | sha256sum | awk '{print $1}')"
