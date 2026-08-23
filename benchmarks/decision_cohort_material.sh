#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
	echo "usage: $0 SYSTEM_ENV BUILD_PROVENANCE_ENV" >&2
	exit 2
fi

system_env=$1
provenance_env=$2

emit_required() {
	file=$1
	key=$2
	value=$(sed -n "s/^${key}=//p" "$file" | head -n 1)
	if [ -z "$value" ]; then
		echo "decision cohort input missing: ${key} in ${file}" >&2
		exit 1
	fi
	printf '%s=%s\n' "$key" "$value"
}

for key in boot_id hostname cpu_model cpu_topology cpu_smt_siblings cpu_scaling_governors cpu_boost_state kernel nproc mem_total_kb; do
	emit_required "$system_env" "$key"
done

# Candidate and legacy VMOD sources intentionally differ. Their build hashes
# are checked per arm, while these inputs define the common decision cohort.
for key in harness_input_sha256 vinyl_build_input_sha256 dockerfile_sha256 docker_image_id build_cflags build_cppflags build_ldflags; do
	emit_required "$provenance_env" "$key"
done
