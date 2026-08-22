#!/bin/sh
# Docker-only negative tests for xkey source and benchmark provenance gates.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image=${VINYL_DOCKER_IMAGE:-vinyl-cache-ubuntu-build}
docker_cmd=${DOCKER:-docker}
xkey_src=${XKEY_SRC:-"$repo_dir/../varnish-modules"}
xkey_src=$(CDPATH= cd -- "$xkey_src" && pwd)

$docker_cmd run --rm \
	-v "$repo_dir:/cachetag-host:ro" \
	-v "$xkey_src:/xkey-host:ro" \
	"$image" bash -lc '
set -euo pipefail
work=/tmp/xkey-provenance-test
rm -rf "$work"
mkdir -p "$work"/{cachetag,vinyl,wrong-xkey,artifacts}
for repo in cachetag vinyl wrong-xkey; do
  git -C "$work/$repo" init -q
  git -C "$work/$repo" config user.email provenance@example.invalid
  git -C "$work/$repo" config user.name provenance
  mkdir -p "$work/$repo/src"
  printf "%s\\n" "$repo" > "$work/$repo/src/input.c"
  git -C "$work/$repo" add .
  git -C "$work/$repo" commit -qm initial
done
git -C "$work/wrong-xkey" tag 0.28.0
mkdir -p "$work/owned-xkey"
tar -C /xkey-host -cf - . | tar -C "$work/owned-xkey" -xf -
chown -R 1000:1000 "$work/owned-xkey"
: > "$work/verify-gitconfig"
if env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$work/verify-gitconfig" \
  git -C "$work/owned-xkey" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "expected Git to reject the UID-1000 xkey checkout before safe.directory" >&2
  exit 1
fi
env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$work/verify-gitconfig" \
  /cachetag-host/benchmarks/xkey/verify-source.sh "$work/owned-xkey" > "$work/owned-xkey-source.env"
env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$work/verify-gitconfig" \
  git config --global --get-all safe.directory | grep -Fx "$work/owned-xkey" >/dev/null
grep -qx "xkey_source_commit=7abe0e2a59a685b4ea8626ff1a3fe9c60a037368" "$work/owned-xkey-source.env"
printf x > "$work/artifacts/cachetag.so"
printf x > "$work/artifacts/vinyld"
printf x > "$work/artifacts/xkey.so"
printf x > "$work/artifacts/commands.log"
printf x > "$work/artifacts/compat.h"
printf x > "$work/artifacts/Dockerfile"
xkey_config_source=/cachetag-host/benchmarks/xkey/config/xkey-benchmark-config-v1.h
test -f "$xkey_config_source"
mkdir -p "$work/xkey-build/config"
cp "$xkey_config_source" "$work/xkey-build/config/config.h"
cmp -s "$xkey_config_source" "$work/xkey-build/config/config.h"
common_env=(
  BUILD_PROVENANCE_XKEY_COMPAT_ARTIFACT="$work/artifacts/compat.h"
  BUILD_PROVENANCE_XKEY_CONFIG_ARTIFACT="$xkey_config_source"
  BUILD_PROVENANCE_CACHETAG_BINARY="$work/artifacts/cachetag.so"
  BUILD_PROVENANCE_VINYL_BINARY="$work/artifacts/vinyld"
  BUILD_PROVENANCE_XKEY_BINARY="$work/artifacts/xkey.so"
  BUILD_PROVENANCE_BUILD_COMMANDS_FILE="$work/artifacts/commands.log"
  BUILD_PROVENANCE_DOCKERFILE="$work/artifacts/Dockerfile"
  BUILD_PROVENANCE_IMAGE_REF=test-image
  BUILD_PROVENANCE_IMAGE_ID=sha256:test
  BUILD_PROVENANCE_CFLAGS=-O2
  BUILD_PROVENANCE_CPPFLAGS=-DTEST
  BUILD_PROVENANCE_LDFLAGS=-Wl,test
)
if env "${common_env[@]}" sh /cachetag-host/benchmarks/build_provenance.sh record "$work/cachetag" "$work/vinyl" none "$work/wrong-xkey" default "$work/provenance.env"; then
  echo "expected wrong xkey pin to fail" >&2
  exit 1
fi
: > "$work/provenance-gitconfig"
env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$work/provenance-gitconfig" "${common_env[@]}" \
  sh /cachetag-host/benchmarks/build_provenance.sh record "$work/cachetag" "$work/vinyl" none "$work/owned-xkey" default "$work/owned-provenance.env"
env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$work/provenance-gitconfig" \
  git config --global --get-all safe.directory | grep -Fx "$work/owned-xkey" >/dev/null
: > "$work/provenance-verify-gitconfig"
env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$work/provenance-verify-gitconfig" "${common_env[@]}" \
  sh /cachetag-host/benchmarks/build_provenance.sh verify "$work/cachetag" "$work/vinyl" none "$work/owned-xkey" default "$work/owned-provenance.env"
env "${common_env[@]}" sh /cachetag-host/benchmarks/build_provenance.sh record "$work/cachetag" "$work/vinyl" none /xkey-host default "$work/provenance.env"
printf dirty > "$work/cachetag/untracked"
if env "${common_env[@]}" sh /cachetag-host/benchmarks/build_provenance.sh verify "$work/cachetag" "$work/vinyl" none /xkey-host default "$work/provenance.env"; then
  echo "expected dirty build input source to fail" >&2
  exit 1
fi
env BUILD_PROVENANCE_MODE=development "${common_env[@]}" sh /cachetag-host/benchmarks/build_provenance.sh record "$work/cachetag" "$work/vinyl" none /xkey-host default "$work/development.env"
grep -qx 'build_provenance_eligible=0' "$work/development.env"
grep -qx 'cachetag_dirty_state=dirty' "$work/development.env"
echo "xkey provenance negative tests passed"
'
