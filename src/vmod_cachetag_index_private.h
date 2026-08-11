/*-
 * SPDX-License-Identifier: MPL-2.0
 */

#ifndef VMOD_TAG_INDEX_PRIVATE_H
#define VMOD_TAG_INDEX_PRIVATE_H

#include <pthread.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>

#include "cachetag_util.h"
#include "vmod_cachetag_index.h"

#define TAG_INDEX_MAGIC 0x74616769
#define TAG_OBJECT_SEGMENTS 32
#define TAG_RESIZE_BATCH_YIELD_SEC 0.0001
#if CACHE_TAG_SET_INTERNING
#define TAG_INTERN_LOOKUP_FIRST_MAX_FOLDS 8U
#endif

struct cachetag_objent;
struct cachetag_side_bucket;
struct cachetag_purgemap;
#if CACHE_TAG_SET_INTERNING
struct cachetag_interned_set;
#endif

/*
 * Epoch reader gate: a phase bit plus a per-phase reader count.  Readers pin
 * the current phase (acquire) and release it (release); a writer flips the
 * phase and then drains the retiring slot.  The drain strategy (polling vs
 * cond-wait) is left to each instance; only the phase/reader state and the
 * acquire/release code are shared here.
 */
struct cachetag_epoch_gate {
	unsigned phase;
	uint64_t readers[2];
};

static inline unsigned
cachetag_epoch_gate_acquire(struct cachetag_epoch_gate *gate, int *stablep)
{
	unsigned slot;

	slot = __atomic_load_n(&gate->phase, __ATOMIC_SEQ_CST) & 1U;
	(void)__atomic_add_fetch(&gate->readers[slot], 1, __ATOMIC_SEQ_CST);
	*stablep = (__atomic_load_n(&gate->phase, __ATOMIC_SEQ_CST) & 1U) == slot;
	return (slot);
}

static inline uint64_t
cachetag_epoch_gate_release(struct cachetag_epoch_gate *gate, unsigned slot)
{

	return (__atomic_sub_fetch(&gate->readers[slot], 1, __ATOMIC_SEQ_CST));
}

struct cachetag_side_table {
	struct cachetag_side_bucket *map;
	size_t buckets;
	size_t tombstones;
	size_t live;
};

struct cachetag_sweep_observation {
	uint64_t scanned;
	uint64_t killed;
	uint64_t reduced;
	uint64_t aborted;
	uint64_t batches;
	uint64_t obj_mtx_wait_usec;
	uint64_t obj_mtx_hold_usec;
	uint64_t obj_mtx_wait_max_usec;
	uint64_t obj_mtx_hold_max_usec;
	uint64_t batch_hold_over_2ms;
	uint64_t batch_hold_over_5ms;
	uint64_t batch_hold_over_10ms;
	uint64_t unlocked_gap_usec;
	uint64_t per_object_max_usec;
	uint64_t batch_scanned_max;
	uint64_t remaining;
	uint64_t objects_before;
	uint64_t objects_after;
	uint64_t object_slots_before;
	uint64_t object_slots_after;
	uint64_t object_bytes_before;
	uint64_t object_bytes_after;
	uint64_t side_buckets_before;
	uint64_t side_buckets_after;
	uint64_t side_bytes_before;
	uint64_t side_bytes_after;
};

struct cachetag_index {
	unsigned magic;
	char *namespace;
	size_t namespace_len;
	struct cachetag_limits limits;
	struct cachetag_wal *wal;
	struct cachetag_purgemap *purgemap_data;

	pthread_mutex_t obj_mtx;
	struct cachetag_objent *object_segments[TAG_OBJECT_SEGMENTS];
	size_t nobjects;
	size_t capobjects;
	/*
	 * The side map and dense segmented object vector are always accessed with
	 * obj_mtx held.  There are no lock-free readers or exported object
	 * handles, so a stale slot generation cannot outlive the mutex-protected
	 * lookup which resolves an objcore to its current dense slot.
	 */
#if CACHE_TAG_SET_INTERNING
	/*
	 * Hash-consed multi-fold membership sets. The registry is guarded by
	 * obj_mtx like the dense object table and side map. Hits and misses survive
	 * detach_all; the live gauges drain with the objects.
	 */
	struct cachetag_interned_set **intern_buckets;
	size_t intern_nbuckets;
	struct cachetag_interned_set **intern_old_buckets;
	size_t intern_old_nbuckets;
	size_t intern_migrate_cursor;
	uint64_t intern_generation;
	unsigned intern_migration_active;
	size_t intern_detached_set_bytes;
	size_t intern_detached_table_bytes;
	size_t intern_sets;
	size_t intern_refs;
	size_t intern_bytes;
	size_t intern_overflow_sets;
	uint64_t intern_hits;
	uint64_t intern_misses;
	struct cachetag_timing_counters intern_acquire_timing;
	struct cachetag_timing_counters intern_table_grow_timing;
	struct cachetag_timing_counters intern_set_alloc_timing;
	struct cachetag_timing_counters intern_candidate_alloc_timing;
	struct cachetag_timing_counters intern_table_alloc_timing;
#endif

	struct cachetag_side_table side_primary;
	struct cachetag_side_table side_retiring;
	size_t side_migrate_cursor;
	unsigned side_migration_active;
	unsigned side_migration_reason;
	unsigned side_migration_auto;
	unsigned resize_wakeup;
	uint64_t resize_wakeup_at_usec;
	unsigned resize_low_water_active;
	uint64_t resize_low_water_start_usec;
	size_t resize_low_water_live;
	unsigned resize_low_water_force;
	uint64_t resize_low_water_rearm_at_usec;
	size_t resize_detached_bytes;

	pthread_mutex_t counter_mtx;
	struct cachetag_counters counters;
	struct cachetag_epoch_gate publication_gate;
	pthread_mutex_t purge_mtx;
	pthread_cond_t sweep_cond;
	pthread_mutex_t sweep_mtx;
	pthread_t sweep_thread;
	unsigned sweep_running;
	unsigned sweep_stop;
	unsigned sweep_active;
	size_t sweep_remaining;
	unsigned reclaim_pending;
	unsigned reclaim_phase;
	uint64_t reclaim_cutoff;
	uint64_t reclaim_defer_start_usec;
	unsigned publication_blocked;
	unsigned publication_blocked_phase;
	pthread_mutex_t replay_mtx;
	unsigned replay_done;
	unsigned test_fail_next_key_purge_wal;
	unsigned test_fail_next_persist_prepare;
	unsigned test_abort_next_sweep;
	unsigned test_force_next_attach_slot_overflow;
	unsigned test_fail_next_object_segment_alloc;
	unsigned test_fail_next_side_migration_alloc;
#if CACHE_TAG_SET_INTERNING
	unsigned test_fail_next_intern_alloc;
	unsigned test_fail_next_intern_table_alloc;
	unsigned test_intern_worker_hold;
	size_t test_intern_initial_buckets;
#endif
	unsigned test_side_fingerprint_bits;
	unsigned benchmark_obj_mtx_timing;
};

static inline uint64_t
cachetag_now_usec(void)
{
	struct timespec ts;
	uint64_t usec;

	if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
		return (0);
	usec = (uint64_t)ts.tv_sec * UINT64_C(1000000);
	usec += (uint64_t)ts.tv_nsec / UINT64_C(1000);
	return (usec);
}

static inline uint64_t
cachetag_elapsed_usec(uint64_t start, uint64_t end)
{

	if (start == 0 || end <= start)
		return (0);
	return (end - start);
}

int cachetag_digest_snapshot(struct cachetag_index *, const char *,
    struct cachetag_registration_snapshot *);
struct cachetag_purgemap *cachetag_purgemap_data(const struct cachetag_index *);
void cachetag_purgemap_data_set(struct cachetag_index *,
    struct cachetag_purgemap *);
void cachetag_counter_add(struct cachetag_index *, uint64_t *, uint64_t);
void cachetag_note_stale_call(struct cachetag_index *);
void cachetag_note_stale_detected(struct cachetag_index *);
/*
 * Without set interning, a successful multi-fold attach takes ownership of
 * storage; the caller retains it on failure and for one fold. With set
 * interning, candidate is an optional unpublished complete set consumed on
 * every return path; folds is borrowed and may be stack-backed.
 */
int cachetag_record_attach_purgemap_take(struct cachetag_index *,
    struct objcore *, void *, uint64_t *, unsigned, uint64_t,
    enum cachetag_purge_mode *);
void *cachetag_fold_storage_alloc(unsigned);
uint64_t *cachetag_fold_storage_values(void *, unsigned);
void cachetag_fold_storage_free(void *, unsigned);
#if CACHE_TAG_SET_INTERNING
void *cachetag_intern_candidate_alloc(struct cachetag_index *, unsigned);
#endif
void cachetag_record_invalidate(struct cachetag_index *, struct objcore *);
void cachetag_record_shrink(struct cachetag_index *);
enum cachetag_purgemap_probe_result cachetag_record_probe_purgemap(
    struct cachetag_index *, const struct objcore *, int *);
void cachetag_record_sweep_purgemap(struct cachetag_index *,
    struct cachetag_sweep_observation *);
/* Acquires and releases the purgemap reader epoch; callers hold no gate. */
enum cachetag_purgemap_probe_result cachetag_purgemap_probe_reader_guarded(
    struct cachetag_purgemap *, uint64_t, const uint64_t *, unsigned);
void cachetag_resize_wake(struct cachetag_index *);
void cachetag_resize_wake_at(struct cachetag_index *, uint64_t);
int cachetag_resize_maintenance(struct cachetag_index *);
int cachetag_purgemap_replay(struct cachetag_index *,
    const struct cachetag_wal_record *);
int cachetag_purgemap_checkpoint_begin(struct cachetag_index *,
    const struct cachetag_wal_checkpoint_meta *);
int cachetag_purgemap_checkpoint_entry(struct cachetag_index *,
    const struct cachetag_wal_checkpoint_entry *);
void *cachetag_purgemap_sweep_thread(struct worker *, void *);
void cachetag_purgemap_destroy(struct cachetag_index *);

#endif
