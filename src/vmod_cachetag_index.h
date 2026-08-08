/*-
 * SPDX-License-Identifier: MPL-2.0
 *
 * Secondary-key cachetag VMOD memory index.
 */

#ifndef VMOD_TAG_INDEX_H
#define VMOD_TAG_INDEX_H

#include <stdint.h>

#include "vmod_cachetag_types.h"
#include "vmod_cachetag_wal.h"

struct objcore;
struct worker;

#define TAG_DEFAULT_MAX_KEYS_PER_OBJECT	512
#define TAG_DEFAULT_MAX_KEY_LENGTH	1024
#define TAG_DEFAULT_MAX_HEADER_BYTES	(16 * 1024)
#define TAG_DEFAULT_PURGEMAP_SWEEP_INTERVAL 60.0
#define TAG_DEFAULT_PURGEMAP_HISTORY_MAX_ENTRIES 1000000
#define TAG_DEFAULT_PURGEMAP_SWEEP_BATCH_OBJECTS 8192
#define TAG_DEFAULT_PURGEMAP_SWEEP_BATCH_USEC 250
#define TAG_DEFAULT_PURGEMAP_SWEEP_BATCH_YIELD_USEC 100

struct cachetag_limits {
	unsigned max_keys_per_object;
	unsigned max_key_length;
	unsigned max_header_bytes;
	double purgemap_sweep_interval;
	uint64_t purgemap_history_max_entries;
	uint64_t purgemap_sweep_batch_objects;
	uint64_t purgemap_sweep_batch_usec;
	uint64_t purgemap_sweep_batch_yield_usec;
};

struct cachetag_persist_config {
	const char *path;
	enum cachetag_wal_fsync wal_fsync;
	uint64_t wal_segment_bytes;
};

enum cachetag_purgemap_probe_result {
	TAG_PM_PROBE_NONE,
	TAG_PM_PROBE_HARD,
	TAG_PM_PROBE_SOFT
};

enum cachetag_fellow_metric {
	TAG_FELLOW_ATTR_OBJECTS_WRITTEN,
	TAG_FELLOW_ATTR_BYTES_WRITTEN,
	TAG_FELLOW_DIRECT_PROBES,
	TAG_FELLOW_ATTR_ABSENT,
	TAG_FELLOW_ATTR_INVALID,
	TAG_FELLOW_ATTR_READ_FAILURES,
	TAG_FELLOW_NAMESPACE_RECORDS_PROBED,
	TAG_FELLOW_STORE_INVARIANT_FAILURES,
	TAG_FELLOW_VOLATILE_FALLBACK_ATTACHES
};

struct cachetag_index;

typedef int cachetag_pending_probe_f(void *, struct objcore *,
    enum cachetag_purge_mode *, int *);

void cachetag_limits_default(struct cachetag_limits *);
struct cachetag_index *cachetag_index_new(const char *, const struct cachetag_limits *,
    const struct cachetag_persist_config *);
void cachetag_index_delete(struct cachetag_index **);
int cachetag_index_start(struct cachetag_index *);
void cachetag_index_stop(struct cachetag_index *);
void cachetag_index_detach_all(struct cachetag_index *);

int cachetag_registration_snapshot(struct cachetag_index *, const char *,
    struct cachetag_registration_snapshot *);
int cachetag_publication_enter(struct cachetag_index *, unsigned *, uint64_t *);
void cachetag_publication_exit(struct cachetag_index *, unsigned);
int cachetag_attach(struct cachetag_index *, struct objcore *,
    const struct cachetag_registration_snapshot *, unsigned,
    enum cachetag_purge_mode *);
void cachetag_death(struct cachetag_index *, struct objcore *);

int cachetag_purge(struct cachetag_index *, const char *,
    enum cachetag_purge_mode);
int cachetag_generation(struct cachetag_index *, const char *, uint64_t *);
int cachetag_stale(struct worker *, struct cachetag_index *, struct objcore *,
    cachetag_pending_probe_f *, void *);

uint64_t cachetag_object_count(struct cachetag_index *);
uint64_t cachetag_edge_count(struct cachetag_index *);
uint64_t cachetag_purgemap_seq(struct cachetag_index *);
uint64_t cachetag_purgemap_entry_count(struct cachetag_index *);
uint64_t cachetag_purgemap_slot_count(struct cachetag_index *);
uint64_t cachetag_purgemap_byte_count(struct cachetag_index *);
void cachetag_snapshot_counters(struct cachetag_index *, struct cachetag_counters *);
const struct cachetag_limits *cachetag_get_limits(const struct cachetag_index *);
const char *cachetag_namespace_name(const struct cachetag_index *);
void cachetag_namespace_digest(const struct cachetag_index *, uint64_t *,
    uint64_t *);
uint64_t cachetag_fold_digest(uint64_t, uint64_t);
void cachetag_note_fellow_metric(struct cachetag_index *,
    enum cachetag_fellow_metric, uint64_t);
uint64_t cachetag_compact_all(struct cachetag_index *);
int cachetag_test_fail_next_key_purge_wal(struct cachetag_index *);
int cachetag_test_fail_next_persist_prepare(struct cachetag_index *);
int cachetag_test_side_initial_buckets(struct cachetag_index *, uint32_t);
int cachetag_test_abort_next_sweep(struct cachetag_index *);
int cachetag_test_force_next_attach_slot_overflow(struct cachetag_index *);
int cachetag_test_fail_next_object_segment_alloc(struct cachetag_index *);
int cachetag_test_structural_limits(struct cachetag_index *);
int cachetag_test_side_fingerprint_bits(struct cachetag_index *, uint32_t);
int cachetag_test_side_start_migration(struct cachetag_index *, uint32_t);
int cachetag_test_side_migrate_buckets(struct cachetag_index *, uint32_t);
int cachetag_test_side_migration_active(struct cachetag_index *);
int cachetag_test_side_table_buckets(struct cachetag_index *);
int cachetag_test_fail_next_side_migration_alloc(struct cachetag_index *);
int cachetag_test_resize_low_water_ready(struct cachetag_index *);
int cachetag_test_resize_worker_drain(struct cachetag_index *, uint32_t);
void cachetag_count_limit_rejection(struct cachetag_index *);
void cachetag_count_parse_error(struct cachetag_index *);
int cachetag_persist_enabled(struct cachetag_index *);
int cachetag_persist_prepare(struct cachetag_index *);
int cachetag_persist_key_purge_digest(struct cachetag_index *, uint64_t,
    uint64_t, enum cachetag_purge_mode, uint64_t);
int cachetag_decode_key_purge_record(const void *, uint64_t, uint64_t *,
    uint64_t *, enum cachetag_purge_mode *, uint64_t *);
int cachetag_persist_replay(struct cachetag_index *);
int cachetag_purgemap_checkpoint(struct cachetag_index *, int);
int cachetag_purgemap_probe_snapshots(struct cachetag_index *,
    const struct cachetag_registration_snapshot *, unsigned,
    enum cachetag_purge_mode *);
int cachetag_purgemap_probe_serialized(struct cachetag_index *, uint64_t,
    const unsigned char *, unsigned);

/* Implemented by the VMOD/Fellow integration layer. */
int cachetag_fellow_attr_probe(struct worker *, struct cachetag_index *,
    struct objcore *, enum cachetag_purge_mode *);

#endif
