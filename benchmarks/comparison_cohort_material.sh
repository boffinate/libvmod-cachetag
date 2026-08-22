#!/bin/sh
# Emit only invariant host/build inputs for the comparison cohort hash.
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
		echo "comparison cohort input missing: ${key} in ${file}" >&2
		exit 1
	fi
	printf '%s=%s\n' "$key" "$value"
}

for key in \
	boot_id hostname cpu_model cpu_topology cpu_smt_siblings \
	cpu_scaling_governors cpu_boost_state kernel nproc mem_total_kb
do
	emit_required "$system_env" "$key"
done

for key in \
	vinyl_build_input_sha256 cachetag_build_input_sha256 xkey_build_input_sha256 \
	xkey_compat_artifact_sha256 xkey_config_sha256 dockerfile_sha256 \
	docker_image_id build_cflags build_cppflags build_ldflags
do
	emit_required "$provenance_env" "$key"
done
