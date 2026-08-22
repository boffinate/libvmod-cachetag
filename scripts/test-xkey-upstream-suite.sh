#!/bin/sh
# Run varnish-modules' unmodified xkey VTC suite on its pinned supported
# Varnish release. This is a preflight gate, not a Vinyl comparison row.
set -eu

usage() {
	cat <<'EOF'
Usage: scripts/test-xkey-upstream-suite.sh [VARNISH_MODULES_SRC]

Runs the 14 upstream xkey VTCs from unmodified varnish-modules 0.28.0
(7abe0e2a59a685b4ea8626ff1a3fe9c60a037368) in a local, prebuilt
cachetag-xkey-varnish-9.0.0 Docker image. The script never builds or pulls an
image; build docker/xkey-varnish-9.0.0.Dockerfile locally first.

Environment:
  XKEY_UPSTREAM_IMAGE    Local runner image (default: cachetag-xkey-varnish-9.0.0)
  DOCKER                 Docker command (default: docker)
  XKEY_SUITE_RESULTS_DIR Directory for suite provenance (default: /private/tmp/xkey-upstream-suite-<timestamp>)
EOF
}

case "${1:-}" in
-h|--help) usage; exit 0 ;;
esac

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
xkey_src=${1:-${XKEY_SRC:-"$repo_dir/../varnish-modules"}}
xkey_src=$(CDPATH= cd -- "$xkey_src" && pwd)
image=${XKEY_UPSTREAM_IMAGE:-cachetag-xkey-varnish-9.0.0}
docker_cmd=${DOCKER:-docker}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
results_dir=${XKEY_SUITE_RESULTS_DIR:-"/private/tmp/xkey-upstream-suite-$timestamp"}

"$repo_dir/benchmarks/xkey/verify-source.sh" "$xkey_src"
image_id=$($docker_cmd image inspect --format '{{.Id}}' "$image" 2>/dev/null) || {
	echo "xkey upstream-suite image is unavailable locally: $image" >&2
	echo "Build docker/xkey-varnish-9.0.0.Dockerfile locally; this runner will not pull or build it." >&2
	exit 1
}
mkdir -p "$results_dir"

$docker_cmd run --rm \
	-v "$xkey_src:/xkey-host:ro" \
	-v "$repo_dir:/cachetag-host:ro" \
	-v "$results_dir:/results" \
	-e "XKEY_IMAGE_ID=$image_id" \
	"$image" \
	bash -lc '
set -euo pipefail
test -f /cachetag-host/docker/xkey-varnish-9.0.0.Dockerfile
varnishd -V 2>&1 | grep -Eq "varnish-9\.0\.0([^0-9]|$)" || {
  echo "runner image is not the pinned supported Varnish 9.0.0 release" >&2
  exit 1
}
/cachetag-host/benchmarks/xkey/verify-source.sh /xkey-host
mkdir -p /work/xkey-src
tar -C /xkey-host --exclude=.git -cf - . | tar -C /work/xkey-src -xf -
cd /work/xkey-src
./bootstrap
./configure
make -j"$(nproc)" V=1 | tee /results/xkey-upstream-build.log
make -C src check TESTS="tests/xkey/test01.vtc tests/xkey/test02.vtc tests/xkey/test03.vtc tests/xkey/test04.vtc tests/xkey/test05.vtc tests/xkey/test06.vtc tests/xkey/test07.vtc tests/xkey/test08.vtc tests/xkey/test09.vtc tests/xkey/test10.vtc tests/xkey/test11.vtc tests/xkey/test12.vtc tests/xkey/test13.vtc tests/xkey/test14.vtc" V=1 | tee /results/xkey-upstream-suite.log
{
  printf "xkey_commit=%s\\n" "$(git -C /xkey-host rev-parse HEAD)"
  printf "xkey_tree=%s\\n" "$(git -C /xkey-host rev-parse HEAD^{tree})"
  printf "xkey_worktree_status=clean\\n"
  printf "runner_image_id=%s\\n" "$XKEY_IMAGE_ID"
  sha256sum /cachetag-host/docker/xkey-varnish-9.0.0.Dockerfile
} > /results/xkey-upstream-suite-provenance.txt
'
