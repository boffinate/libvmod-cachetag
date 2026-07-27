#!/bin/sh
set -eu

usage() {
	cat <<'EOF'
Usage: scripts/build-benchmark-image.sh

Build the local Docker image used by the libvmod-cachetag test and benchmark
harnesses.

Environment:
  VINYL_DOCKER_IMAGE  Docker image name (default: vinyl-cache-ubuntu-build)
  DOCKER_BUILD_PULL   1 to pull a fresh Ubuntu base image (default: 1)
  DOCKER_BUILD_NO_CACHE
                      1 to rebuild every layer (default: 0)
EOF
}

case "${1:-}" in
	-h|--help)
		usage
		exit 0
		;;
esac

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image=${VINYL_DOCKER_IMAGE:-vinyl-cache-ubuntu-build}
dockerfile="$repo_dir/docker/vinyl-cache-ubuntu-build.Dockerfile"
pull=${DOCKER_BUILD_PULL:-1}
no_cache=${DOCKER_BUILD_NO_CACHE:-0}

args=
if [ "$pull" = 1 ]; then
	args="$args --pull"
fi
if [ "$no_cache" = 1 ]; then
	args="$args --no-cache"
fi

# shellcheck disable=SC2086
docker build $args -t "$image" -f "$dockerfile" "$repo_dir/docker"
