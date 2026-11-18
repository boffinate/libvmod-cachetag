#!/bin/sh
set -eu
repo_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
case "${1:-}" in
    -h|--help) exec "$repo_dir/scripts/test-with-vcache.sh" --help ;;
esac
exec "$repo_dir/scripts/test-with-vcache.sh" vinyl "${1:-${VINYL_CACHE_SRC:-$repo_dir/../vinyl-cache}}"
