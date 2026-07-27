#!/bin/sh
set -eu

usage() {
	cat <<'EOF'
Usage:
  scripts/setup-remote-profiling-tools.sh user@host [REMOTE_DIR]

Install host-side profiling tools on a Debian/Ubuntu remote benchmark server
and clone/update Brendan Gregg's FlameGraph tools under REMOTE_DIR/tools.

The script installs what the host repository offers from:
  - linux-perf or linux-tools/linux-cloud-tools packages
  - bpftrace
  - bpfcc-tools
  - git

REMOTE_DIR defaults to cachetag-bench relative to the SSH user's home.
EOF
}

quote() {
	printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

target=${1:-}
remote_dir=${2:-cachetag-bench}

if [ -z "$target" ]; then
	usage >&2
	exit 2
fi

ssh "$target" "REMOTE_DIR=$(quote "$remote_dir"); if [ \"\${REMOTE_DIR#/}\" = \"\$REMOTE_DIR\" ]; then REMOTE_DIR=\$HOME/\$REMOTE_DIR; fi; export REMOTE_DIR; sh -s" <<'EOF'
set -eu

if [ -r /etc/os-release ]; then
	. /etc/os-release
	case "${ID:-}:${ID_LIKE:-}" in
		debian:*|ubuntu:*|*:debian*) ;;
		*) echo "remote host must be Debian/Ubuntu-like" >&2; exit 1 ;;
	esac
fi

sudo_cmd=
if [ "$(id -u)" != 0 ]; then
	if command -v sudo >/dev/null 2>&1; then
		sudo_cmd="sudo -n"
	else
		echo "remote profiling setup needs root or passwordless sudo" >&2
		exit 1
	fi
fi

have_pkg() {
	apt-cache show "$1" >/dev/null 2>&1
}

append_pkg() {
	pkg=$1
	if have_pkg "$pkg"; then
		packages="$packages $pkg"
	fi
}

$sudo_cmd apt-get update

packages="ca-certificates git procps"
append_pkg linux-perf
append_pkg linux-tools-common
append_pkg linux-tools-generic
append_pkg linux-cloud-tools-generic
append_pkg "linux-tools-$(uname -r)"
append_pkg "linux-cloud-tools-$(uname -r)"
append_pkg "linux-headers-$(uname -r)"
append_pkg linux-headers-generic
append_pkg bpftrace
append_pkg bpfcc-tools

DEBIAN_FRONTEND=noninteractive $sudo_cmd apt-get install -y $packages

mkdir -p "$REMOTE_DIR/tools"
if [ ! -d "$REMOTE_DIR/tools/FlameGraph/.git" ]; then
	git clone --depth 1 https://github.com/brendangregg/FlameGraph.git \
		"$REMOTE_DIR/tools/FlameGraph"
else
	git -C "$REMOTE_DIR/tools/FlameGraph" pull --ff-only
fi

printf 'remote_dir=%s\n' "$REMOTE_DIR"
if command -v perf >/dev/null 2>&1; then
	printf 'perf=%s\n' "$(perf --version 2>/dev/null || printf unknown)"
else
	printf 'perf=missing\n'
fi
if command -v bpftrace >/dev/null 2>&1; then
	printf 'bpftrace=%s\n' "$(bpftrace --version 2>/dev/null || printf unknown)"
else
	printf 'bpftrace=missing\n'
fi
if command -v offcputime-bpfcc >/dev/null 2>&1; then
	printf 'offcputime-bpfcc=available\n'
else
	printf 'offcputime-bpfcc=missing\n'
fi
printf 'flamegraph_dir=%s\n' "$REMOTE_DIR/tools/FlameGraph"
EOF
