/*-
 * SPDX-License-Identifier: MPL-2.0
 *
 * Purge-history map for cachetag.
 */

#include "config.h"

#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/*
 * Vinyl renamed its installed internal header (cache/cache_vinyld.h ->
 * cache/cache_int.h, upstream 6d36364cc1). Accept either so the VMOD builds
 * against both the 9.0.1 release and current trunk. Drop the fallback branch
 * when configure.ac's VINYL_PREREQ rises past that rename.
 */
#if defined(__has_include) && __has_include("cache/cache_int.h")
#  include "cache/cache_int.h"
#else
#  include "cache/cache_vinyld.h"
#endif
#include "miniobj.h"
#include "vtim.h"

#include "vmod_cachetag_index_private.h"

#define TAG_PURGEMAP_INITIAL_SLOTS	16
#define TAG_PURGEMAP_MAX_LOAD_PERCENT	70
#define TAG_PURGEMAP_EMPTY_MIN_DIVISOR	8
#define TAG_PURGEMAP_EMPTY		0
#define TAG_PURGEMAP_TOMBSTONE		UINT64_MAX

struct cachetag_purgemap_entry {
	uint64_t	fold;
	uint64_t	hard_seq;
	uint64_t	soft_seq;
};

struct cachetag_purgemap_table {
	size_t nslot;
	unsigned retire_reader_slot;
	struct cachetag_purgemap_entry entries[];
};

struct cachetag_purgemap {
	struct cachetag_purgemap_table *table;
	struct cachetag_epoch_gate reader_gate;
	pthread_mutex_t reader_mtx;
	pthread_cond_t reader_cond;
	size_t nentry;
	size_t ntombstone;
	uint64_t seq;
	uint64_t hard_floor;
	uint64_t soft_floor;
};

static struct cachetag_purgemap *cachetag_purgemap_get(struct cachetag_index *);
static void cachetag_purgemap_account(struct cachetag_index *,
    const struct cachetag_purgemap *, uint64_t, uint64_t);
static void cachetag_purgemap_try_reclaim(struct cachetag_index *,
    struct cachetag_purgemap *);

static void
cachetag_counter_note_max(uint64_t *maxp, uint64_t value)
{

	if (value > *maxp)
		*maxp = value;
}

static void
cachetag_purgemap_note_sweep(struct cachetag_index *idx,
    const struct cachetag_sweep_observation *sweep, uint64_t sweep_total)
{

	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.sweep_passes++;
	idx->counters.sweep_aborts += sweep->aborted;
	idx->counters.sweep_scanned += sweep->scanned;
	idx->counters.sweep_killed += sweep->killed;
	idx->counters.sweep_reduced += sweep->reduced;
	idx->counters.sweep_batches += sweep->batches;
	idx->counters.sweep_last_batches = sweep->batches;
	idx->counters.sweep_batch_hold_over_2ms += sweep->batch_hold_over_2ms;
	idx->counters.sweep_batch_hold_over_5ms += sweep->batch_hold_over_5ms;
	idx->counters.sweep_batch_hold_over_10ms += sweep->batch_hold_over_10ms;
	cachetag_counter_note_max(&idx->counters.sweep_batch_scanned_max,
	    sweep->batch_scanned_max);
	idx->counters.sweep_remaining = sweep->remaining;
	idx->counters.sweep_obj_mtx_wait_usec += sweep->obj_mtx_wait_usec;
	idx->counters.sweep_obj_mtx_wait_last_usec = sweep->obj_mtx_wait_max_usec;
	cachetag_counter_note_max(&idx->counters.sweep_obj_mtx_wait_max_usec,
	    sweep->obj_mtx_wait_max_usec);
	idx->counters.sweep_obj_mtx_hold_usec += sweep->obj_mtx_hold_usec;
	idx->counters.sweep_obj_mtx_hold_last_usec = sweep->obj_mtx_hold_max_usec;
	cachetag_counter_note_max(&idx->counters.sweep_obj_mtx_hold_max_usec,
	    sweep->obj_mtx_hold_max_usec);
	idx->counters.sweep_unlocked_gap_usec += sweep->unlocked_gap_usec;
	idx->counters.sweep_unlocked_gap_last_usec = sweep->unlocked_gap_usec;
	cachetag_counter_note_max(&idx->counters.sweep_per_object_max_usec,
	    sweep->per_object_max_usec);
	idx->counters.sweep_total_usec += sweep_total;
	idx->counters.sweep_total_last_usec = sweep_total;
	cachetag_counter_note_max(&idx->counters.sweep_total_max_usec,
	    sweep_total);
	idx->counters.sweep_last_scanned = sweep->scanned;
	idx->counters.sweep_last_killed = sweep->killed;
	idx->counters.sweep_last_reduced = sweep->reduced;
	idx->counters.sweep_last_objects_before = sweep->objects_before;
	idx->counters.sweep_last_objects_after = sweep->objects_after;
	idx->counters.sweep_last_object_slots_before =
	    sweep->object_slots_before;
	idx->counters.sweep_last_object_slots_after = sweep->object_slots_after;
	idx->counters.sweep_last_object_bytes_before =
	    sweep->object_bytes_before;
	idx->counters.sweep_last_object_bytes_after = sweep->object_bytes_after;
	idx->counters.sweep_last_side_buckets_before =
	    sweep->side_buckets_before;
	idx->counters.sweep_last_side_buckets_after = sweep->side_buckets_after;
	idx->counters.sweep_last_side_bytes_before = sweep->side_bytes_before;
	idx->counters.sweep_last_side_bytes_after = sweep->side_bytes_after;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static uint64_t
cachetag_purgemap_table_bytes(const struct cachetag_purgemap_table *tbl)
{

	if (tbl == NULL)
		return (0);
	return (sizeof *tbl + (uint64_t)tbl->nslot * sizeof *tbl->entries);
}

static size_t
cachetag_purgemap_empty_slots(const struct cachetag_purgemap *pm,
    const struct cachetag_purgemap_table *tbl)
{
	size_t occupied;

	if (tbl == NULL)
		return (0);
	occupied = __atomic_load_n(&pm->nentry, __ATOMIC_ACQUIRE) +
	    __atomic_load_n(&pm->ntombstone, __ATOMIC_ACQUIRE);
	if (tbl->nslot > occupied)
		return (tbl->nslot - occupied);
	return (0);
}

static int
cachetag_purgemap_needs_same_size_rebuild(const struct cachetag_purgemap *pm,
    const struct cachetag_purgemap_table *tbl)
{
	size_t threshold;

	if (tbl == NULL)
		return (0);
	threshold = tbl->nslot / TAG_PURGEMAP_EMPTY_MIN_DIVISOR;
	if (threshold == 0)
		threshold = 1;
	if (cachetag_purgemap_empty_slots(pm, tbl) < threshold)
		return (1);
	if (__atomic_load_n(&pm->ntombstone, __ATOMIC_ACQUIRE) >= threshold)
		return (1);
	return (0);
}

static void
cachetag_purgemap_account(struct cachetag_index *idx,
    const struct cachetag_purgemap *pm, uint64_t add_bytes, uint64_t sub_bytes)
{
	const struct cachetag_purgemap_table *tbl;
	uint64_t bytes, entries, tombstones, slots;

	tbl = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
	entries = __atomic_load_n(&pm->nentry, __ATOMIC_ACQUIRE);
	tombstones = __atomic_load_n(&pm->ntombstone, __ATOMIC_ACQUIRE);
	slots = tbl != NULL ? tbl->nslot : 0;
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	if (sub_bytes > 0) {
		if (idx->counters.purgemap_bytes >= sub_bytes)
			idx->counters.purgemap_bytes -= sub_bytes;
		else
			idx->counters.purgemap_bytes = 0;
		if (idx->counters.index_memory_bytes >= sub_bytes)
			idx->counters.index_memory_bytes -= sub_bytes;
		else
			idx->counters.index_memory_bytes = 0;
	}
	if (add_bytes > 0) {
		idx->counters.purgemap_bytes += add_bytes;
		idx->counters.index_memory_bytes += add_bytes;
	}
	bytes = idx->counters.purgemap_bytes;
	idx->counters.purgemap_entries = entries;
	idx->counters.purgemap_table_slots = slots;
	idx->counters.purgemap_tombstones = tombstones;
	idx->counters.purgemap_empty_slots = slots > entries + tombstones ?
	    slots - entries - tombstones : 0;
	idx->counters.purgemap_hard_floor =
	    __atomic_load_n(&pm->hard_floor, __ATOMIC_ACQUIRE);
	idx->counters.purgemap_soft_floor =
	    __atomic_load_n(&pm->soft_floor, __ATOMIC_ACQUIRE);
	idx->counters.purgemap_seq = __atomic_load_n(&pm->seq,
	    __ATOMIC_ACQUIRE);
	idx->counters.purgemap_bytes = bytes;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static struct cachetag_purgemap *
cachetag_purgemap_get(struct cachetag_index *idx)
{
	struct cachetag_purgemap *pm;

	pm = cachetag_purgemap_data(idx);
	if (pm != NULL)
		return (pm);
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	pm = cachetag_purgemap_data(idx);
	if (pm != NULL) {
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		return (pm);
	}
	pm = calloc(1, sizeof *pm);
	if (pm == NULL) {
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		return (NULL);
	}
	PTOK(pthread_mutex_init(&pm->reader_mtx, NULL));
	PTOK(pthread_cond_init(&pm->reader_cond, NULL));
	cachetag_purgemap_data_set(idx, pm);
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
	cachetag_purgemap_account(idx, pm, sizeof *pm, 0);
	return (pm);
}

int
cachetag_publication_enter(struct cachetag_index *idx,
    unsigned *phasep, uint64_t *seqp)
{
	struct cachetag_purgemap *pm;
	unsigned phase;

	AN(phasep);
	AN(seqp);
	pm = cachetag_purgemap_get(idx);
	if (pm == NULL)
		return (ENOMEM);
	for (;;) {
		int stable;

		phase = cachetag_epoch_gate_acquire(&idx->publication_gate,
		    &stable);
		if (stable)
			break;
		(void)cachetag_epoch_gate_release(&idx->publication_gate, phase);
	}
	*phasep = phase;
	*seqp = __atomic_load_n(&pm->seq, __ATOMIC_ACQUIRE);
	cachetag_counter_add(idx, &idx->counters.publication_acquires, 1);
	return (0);
}

void
cachetag_publication_exit(struct cachetag_index *idx, unsigned phase)
{
	assert(phase < 2);
	assert(__atomic_load_n(&idx->publication_gate.readers[phase],
	    __ATOMIC_SEQ_CST) > 0);
	(void)cachetag_epoch_gate_release(&idx->publication_gate, phase);
	cachetag_counter_add(idx, &idx->counters.publication_releases, 1);
}

void
cachetag_death(struct cachetag_index *idx, struct objcore *oc)
{

	cachetag_record_invalidate(idx, oc);
}

int
cachetag_persist_enabled(struct cachetag_index *idx)
{

	return (cachetag_wal_enabled(idx->wal));
}

int
cachetag_persist_prepare(struct cachetag_index *idx)
{

	if (__atomic_exchange_n(&idx->test_fail_next_persist_prepare, 0,
	    __ATOMIC_ACQ_REL))
		return (EIO);
	return (cachetag_wal_prepare(idx->wal));
}

static struct cachetag_purgemap_table *
cachetag_purgemap_table_new(size_t nslot)
{
	struct cachetag_purgemap_table *tbl;

	tbl = calloc(1, sizeof *tbl + nslot * sizeof *tbl->entries);
	if (tbl == NULL)
		return (NULL);
	tbl->nslot = nslot;
	return (tbl);
}

static int
cachetag_purgemap_table_insert_existing(struct cachetag_purgemap_table *tbl,
    const struct cachetag_purgemap_entry *src)
{
	struct cachetag_purgemap_entry *ent;
	size_t mask, pos;

	AN(tbl);
	AN(src);
	mask = tbl->nslot - 1;
	for (pos = src->fold & mask;; pos = (pos + 1) & mask) {
		ent = &tbl->entries[pos];
		if (ent->fold == TAG_PURGEMAP_EMPTY) {
			ent->hard_seq = src->hard_seq;
			ent->soft_seq = src->soft_seq;
			__atomic_store_n(&ent->fold, src->fold, __ATOMIC_RELEASE);
			return (0);
		}
	}
}

static int
cachetag_purgemap_rebuild_locked(struct cachetag_index *idx,
    struct cachetag_purgemap *pm, size_t minslot, int grow,
    struct cachetag_purgemap_table **retiredp)
{
	struct cachetag_purgemap_table *old, *tbl;
	size_t nslot, u;

	(void)idx;
	AN(retiredp);
	old = pm->table;
	nslot = old == NULL ? TAG_PURGEMAP_INITIAL_SLOTS :
	    (grow ? old->nslot * 2 : old->nslot);
	while (nslot < minslot)
		nslot *= 2;
	tbl = cachetag_purgemap_table_new(nslot);
	if (tbl == NULL)
		return (ENOMEM);
	if (old != NULL) {
		for (u = 0; u < old->nslot; u++) {
			if (__atomic_load_n(&old->entries[u].fold,
			    __ATOMIC_ACQUIRE) != TAG_PURGEMAP_EMPTY &&
			    __atomic_load_n(&old->entries[u].fold,
			    __ATOMIC_ACQUIRE) != TAG_PURGEMAP_TOMBSTONE)
				AZ(cachetag_purgemap_table_insert_existing(tbl,
				    &old->entries[u]));
		}
	}
	if (old != NULL)
		old->retire_reader_slot = __atomic_load_n(&pm->reader_gate.phase,
		    __ATOMIC_SEQ_CST) & 1U;
	__atomic_store_n(&pm->table, tbl, __ATOMIC_RELEASE);
	if (old != NULL)
		(void)__atomic_xor_fetch(&pm->reader_gate.phase, 1U,
		    __ATOMIC_SEQ_CST);
	__atomic_store_n(&pm->ntombstone, 0, __ATOMIC_RELEASE);
	*retiredp = old;
	cachetag_purgemap_account(idx, pm, cachetag_purgemap_table_bytes(tbl),
	    0);
	cachetag_counter_add(idx, grow ? &idx->counters.purgemap_rebuilds_grow :
	    &idx->counters.purgemap_rebuilds_same_size, 1);
	return (0);
}

static void
cachetag_purgemap_retire_after_commit(struct cachetag_index *idx,
    struct cachetag_purgemap_table *tbl)
{
	struct cachetag_purgemap *pm;

	if (tbl == NULL)
		return;
	pm = cachetag_purgemap_data(idx);
	AN(pm);
	PTOK(pthread_mutex_lock(&pm->reader_mtx));
	while (__atomic_load_n(&pm->reader_gate.readers[tbl->retire_reader_slot],
	    __ATOMIC_SEQ_CST) != 0)
		PTOK(pthread_cond_wait(&pm->reader_cond, &pm->reader_mtx));
	PTOK(pthread_mutex_unlock(&pm->reader_mtx));
	cachetag_purgemap_account(idx, pm, 0,
	    cachetag_purgemap_table_bytes(tbl));
	free(tbl);
}

static struct cachetag_purgemap_table *
cachetag_purgemap_prune_rollback_locked(struct cachetag_index *idx,
    struct cachetag_purgemap *pm, struct cachetag_purgemap_table *original,
    size_t nentry, size_t ntombstone, uint64_t hard_floor,
    uint64_t soft_floor, size_t pruned)
{
	struct cachetag_purgemap_table *discarded;

	discarded = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
	AN(discarded);
	discarded->retire_reader_slot = __atomic_load_n(&pm->reader_gate.phase,
	    __ATOMIC_SEQ_CST) & 1U;
	__atomic_store_n(&pm->table, original, __ATOMIC_RELEASE);
	(void)__atomic_xor_fetch(&pm->reader_gate.phase, 1U, __ATOMIC_SEQ_CST);
	__atomic_store_n(&pm->nentry, nentry, __ATOMIC_RELEASE);
	__atomic_store_n(&pm->ntombstone, ntombstone, __ATOMIC_RELEASE);
	__atomic_store_n(&pm->hard_floor, hard_floor, __ATOMIC_RELEASE);
	__atomic_store_n(&pm->soft_floor, soft_floor, __ATOMIC_RELEASE);
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	if (idx->counters.purgemap_prunes != 0)
		idx->counters.purgemap_prunes--;
	if (idx->counters.purgemap_pruned_entries >= pruned)
		idx->counters.purgemap_pruned_entries -= pruned;
	if (idx->counters.purgemap_rebuilds_shrink != 0)
		idx->counters.purgemap_rebuilds_shrink--;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	cachetag_purgemap_account(idx, pm, 0, 0);
	return (discarded);
}

static inline int
cachetag_purgemap_entry_survives_cutoff(struct cachetag_purgemap_entry *ent,
    uint64_t cutoff)
{

	if (ent->fold == TAG_PURGEMAP_EMPTY ||
	    ent->fold == TAG_PURGEMAP_TOMBSTONE)
		return (0);
	if (ent->hard_seq <= cutoff)
		ent->hard_seq = 0;
	if (ent->soft_seq <= cutoff)
		ent->soft_seq = 0;
	return (ent->hard_seq != 0 || ent->soft_seq != 0);
}

static int
cachetag_purgemap_reclaim_locked(struct cachetag_index *idx,
    struct cachetag_purgemap *pm, uint64_t cutoff,
    struct cachetag_purgemap_table **retiredp)
{
	struct cachetag_purgemap_table *old, *tbl = NULL;
	struct cachetag_purgemap_entry ent;
	uint64_t oldbytes, newbytes;
	size_t kept = 0, nslot = TAG_PURGEMAP_INITIAL_SLOTS, u;

	AN(retiredp);
	*retiredp = NULL;
	old = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
	if (old != NULL) {
		for (u = 0; u < old->nslot; u++) {
			ent = old->entries[u];
			if (cachetag_purgemap_entry_survives_cutoff(&ent, cutoff))
				kept++;
		}
	}
	while (kept * 100 > nslot * TAG_PURGEMAP_MAX_LOAD_PERCENT)
		nslot *= 2;
	if (kept != 0) {
		tbl = cachetag_purgemap_table_new(nslot);
		if (tbl == NULL)
			return (ENOMEM);
		for (u = 0; u < old->nslot; u++) {
			ent = old->entries[u];
			if (cachetag_purgemap_entry_survives_cutoff(&ent, cutoff))
				AZ(cachetag_purgemap_table_insert_existing(tbl, &ent));
		}
	}
	oldbytes = cachetag_purgemap_table_bytes(old);
	newbytes = cachetag_purgemap_table_bytes(tbl);
	if (old != NULL)
		old->retire_reader_slot = __atomic_load_n(&pm->reader_gate.phase,
		    __ATOMIC_SEQ_CST) & 1U;
	__atomic_store_n(&pm->table, tbl, __ATOMIC_RELEASE);
	if (old != NULL)
		(void)__atomic_xor_fetch(&pm->reader_gate.phase, 1U,
		    __ATOMIC_SEQ_CST);
	__atomic_store_n(&pm->nentry, kept, __ATOMIC_RELEASE);
	__atomic_store_n(&pm->ntombstone, 0, __ATOMIC_RELEASE);
	if (__atomic_load_n(&pm->hard_floor, __ATOMIC_ACQUIRE) <= cutoff)
		__atomic_store_n(&pm->hard_floor, 0, __ATOMIC_RELEASE);
	if (__atomic_load_n(&pm->soft_floor, __ATOMIC_ACQUIRE) <= cutoff)
		__atomic_store_n(&pm->soft_floor, 0, __ATOMIC_RELEASE);
	*retiredp = old;
	cachetag_purgemap_account(idx, pm, newbytes, 0);
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.purgemap_auto_reclaim_passes++;
	idx->counters.purgemap_rebuilds_shrink++;
	if (oldbytes > newbytes)
		idx->counters.purgemap_auto_reclaimed_bytes += oldbytes - newbytes;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	return (0);
}

static int
cachetag_purgemap_cert_begin(struct cachetag_index *idx,
    struct cachetag_purgemap *pm, uint64_t *cutoffp, unsigned *phasep)
{
	if (cachetag_persist_enabled(idx))
		return (0);
	cachetag_purgemap_try_reclaim(idx, pm);
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	if (idx->publication_blocked) {
		if (__atomic_load_n(&idx->publication_gate.readers[
		    idx->publication_blocked_phase], __ATOMIC_SEQ_CST) != 0) {
			PTOK(pthread_mutex_unlock(&idx->purge_mtx));
			return (0);
		}
		idx->publication_blocked = 0;
	}
	if (idx->reclaim_pending) {
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		return (0);
	}
	*cutoffp = __atomic_load_n(&pm->seq, __ATOMIC_ACQUIRE);
	*phasep = __atomic_load_n(&idx->publication_gate.phase,
	    __ATOMIC_SEQ_CST) & 1U;
	(void)__atomic_xor_fetch(&idx->publication_gate.phase, 1U,
	    __ATOMIC_SEQ_CST);
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
	return (1);
}

static void
cachetag_purgemap_cert_finish(struct cachetag_index *idx,
    struct cachetag_purgemap *pm, uint64_t cutoff, unsigned phase,
    uint64_t aborted)
{
	if (aborted) {
		PTOK(pthread_mutex_lock(&idx->purge_mtx));
		idx->publication_blocked = 1;
		idx->publication_blocked_phase = phase;
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		return;
	}
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	assert(!idx->reclaim_pending);
	idx->reclaim_cutoff = cutoff;
	idx->reclaim_phase = phase;
	idx->reclaim_pending = 1;
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
	cachetag_purgemap_try_reclaim(idx, pm);
}

static void
cachetag_purgemap_try_reclaim(struct cachetag_index *idx,
    struct cachetag_purgemap *pm)
{
	struct cachetag_purgemap_table *retired = NULL;
	struct cachetag_purgemap_table *tbl;
	uint64_t before_bytes, after_bytes, defer_usec, filter_usec;
	uint64_t filter_start, filter_end, oldbytes, newbytes;
	uint64_t before_slots, after_slots;
	size_t before, after;

	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	if (!idx->reclaim_pending) {
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		return;
	}
	if (__atomic_load_n(&idx->publication_gate.readers[idx->reclaim_phase],
	    __ATOMIC_SEQ_CST) != 0) {
		if (idx->reclaim_defer_start_usec == 0)
			idx->reclaim_defer_start_usec = cachetag_now_usec();
		cachetag_counter_add(idx,
		    &idx->counters.purgemap_auto_reclaim_deferred_pending, 1);
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		return;
	}
	before = pm->nentry;
	tbl = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
	before_slots = tbl != NULL ? tbl->nslot : 0;
	before_bytes = cachetag_purgemap_table_bytes(tbl);
	filter_start = cachetag_now_usec();
	if (cachetag_purgemap_reclaim_locked(idx, pm, idx->reclaim_cutoff,
	    &retired) == 0) {
		filter_end = cachetag_now_usec();
		after = pm->nentry;
		tbl = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
		after_slots = tbl != NULL ? tbl->nslot : 0;
		after_bytes = cachetag_purgemap_table_bytes(tbl);
		oldbytes = cachetag_purgemap_table_bytes(retired);
		newbytes = after_bytes;
		filter_usec = cachetag_elapsed_usec(filter_start, filter_end);
		defer_usec = 0;
		if (idx->reclaim_defer_start_usec != 0) {
			defer_usec = cachetag_elapsed_usec(
			    idx->reclaim_defer_start_usec, filter_start);
			idx->reclaim_defer_start_usec = 0;
		}
		if (before > after)
			cachetag_counter_add(idx,
			    &idx->counters.purgemap_auto_reclaimed_entries,
			    before - after);
		PTOK(pthread_mutex_lock(&idx->counter_mtx));
		idx->counters.purgemap_auto_reclaim_filter_usec +=
		    filter_usec;
		idx->counters.purgemap_auto_reclaim_filter_last_usec =
		    filter_usec;
		cachetag_counter_note_max(
		    &idx->counters.purgemap_auto_reclaim_filter_max_usec,
		    filter_usec);
		idx->counters.purgemap_auto_reclaim_transient_bytes =
		    oldbytes + newbytes;
		cachetag_counter_note_max(
		    &idx->counters.purgemap_auto_reclaim_transient_max_bytes,
		    oldbytes + newbytes);
		idx->counters.purgemap_auto_reclaim_table_slots_before =
		    before_slots;
		idx->counters.purgemap_auto_reclaim_table_slots_after =
		    after_slots;
		idx->counters.purgemap_auto_reclaim_table_bytes_before =
		    before_bytes;
		idx->counters.purgemap_auto_reclaim_table_bytes_after =
		    after_bytes;
		if (defer_usec != 0) {
			idx->counters.purgemap_auto_reclaim_defer_usec +=
			    defer_usec;
			idx->counters.purgemap_auto_reclaim_defer_last_usec =
			    defer_usec;
			cachetag_counter_note_max(
			    &idx->counters.purgemap_auto_reclaim_defer_max_usec,
			    defer_usec);
		}
		PTOK(pthread_mutex_unlock(&idx->counter_mtx));
		idx->reclaim_pending = 0;
	}
	cachetag_purgemap_retire_after_commit(idx, retired);
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
}

static struct cachetag_purgemap_table *
cachetag_purgemap_reader_enter(struct cachetag_purgemap *pm, unsigned *slotp)
{
	unsigned slot;
	int stable;

	AN(slotp);
	for (;;) {
		slot = cachetag_epoch_gate_acquire(&pm->reader_gate, &stable);
		if (stable) {
			*slotp = slot;
			return (__atomic_load_n(&pm->table, __ATOMIC_ACQUIRE));
		}
		if (cachetag_epoch_gate_release(&pm->reader_gate, slot) == 0) {
			PTOK(pthread_mutex_lock(&pm->reader_mtx));
			PTOK(pthread_cond_broadcast(&pm->reader_cond));
			PTOK(pthread_mutex_unlock(&pm->reader_mtx));
		}
	}
}

static void
cachetag_purgemap_reader_exit(struct cachetag_purgemap *pm, unsigned slot)
{

	assert(slot < 2);
	if (cachetag_epoch_gate_release(&pm->reader_gate, slot) == 0) {
		PTOK(pthread_mutex_lock(&pm->reader_mtx));
		PTOK(pthread_cond_broadcast(&pm->reader_cond));
		PTOK(pthread_mutex_unlock(&pm->reader_mtx));
	}
}

static int
cachetag_purgemap_prepare_upsert_locked(struct cachetag_index *idx,
    struct cachetag_purgemap *pm,
    struct cachetag_purgemap_table **retiredp)
{
	struct cachetag_purgemap_table *tbl;
	int r;

	AN(retiredp);
	*retiredp = NULL;
	tbl = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
	if (cachetag_purgemap_needs_same_size_rebuild(pm, tbl)) {
		r = cachetag_purgemap_rebuild_locked(idx, pm, tbl->nslot, 0,
		    retiredp);
		if (r != 0)
			return (r);
		tbl = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
	} else if (tbl == NULL || (__atomic_load_n(&pm->nentry,
	    __ATOMIC_ACQUIRE) + 1) * 100 >
	    tbl->nslot * TAG_PURGEMAP_MAX_LOAD_PERCENT) {
		r = cachetag_purgemap_rebuild_locked(idx, pm,
		    tbl == NULL ? TAG_PURGEMAP_INITIAL_SLOTS : tbl->nslot * 2,
		    1, retiredp);
		if (r != 0)
			return (r);
		tbl = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
	}
	AN(tbl);
	return (0);
}

static void
cachetag_purgemap_apply_upsert_locked(struct cachetag_purgemap *pm,
    uint64_t fold, uint64_t seq, enum cachetag_purge_mode mode, int *createdp)
{
	struct cachetag_purgemap_table *tbl;
	struct cachetag_purgemap_entry *ent, *first_tombstone = NULL;
	struct cachetag_purgemap_entry *insert_ent = NULL;
	size_t mask, pos;
	uint64_t seen;

	AN(createdp);
	*createdp = 0;
	tbl = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
	AN(tbl);
	mask = tbl->nslot - 1;
	for (pos = fold & mask;; pos = (pos + 1) & mask) {
		ent = &tbl->entries[pos];
		seen = __atomic_load_n(&ent->fold, __ATOMIC_ACQUIRE);
		if (seen == fold) {
			if (mode == TAG_PURGE_HARD &&
			    __atomic_load_n(&ent->hard_seq, __ATOMIC_RELAXED) <
			    seq)
				__atomic_store_n(&ent->hard_seq, seq,
				    __ATOMIC_RELEASE);
			else if (mode == TAG_PURGE_SOFT &&
			    __atomic_load_n(&ent->soft_seq, __ATOMIC_RELAXED) <
			    seq)
				__atomic_store_n(&ent->soft_seq, seq,
				    __ATOMIC_RELEASE);
			return;
		}
		if (seen == TAG_PURGEMAP_TOMBSTONE) {
			if (first_tombstone == NULL)
				first_tombstone = ent;
			continue;
		}
		if (seen == TAG_PURGEMAP_EMPTY) {
			insert_ent = first_tombstone != NULL ? first_tombstone : ent;
			break;
		}
	}
	if (mode == TAG_PURGE_HARD) {
		insert_ent->hard_seq = seq;
		insert_ent->soft_seq = 0;
	} else {
		insert_ent->hard_seq = 0;
		insert_ent->soft_seq = seq;
	}
	if (first_tombstone != NULL)
		(void)__atomic_sub_fetch(&pm->ntombstone, 1, __ATOMIC_RELEASE);
	__atomic_store_n(&insert_ent->fold, fold, __ATOMIC_RELEASE);
	(void)__atomic_add_fetch(&pm->nentry, 1, __ATOMIC_RELEASE);
	*createdp = 1;
}

static int
cachetag_purgemap_upsert_locked(struct cachetag_index *idx,
    struct cachetag_purgemap *pm, uint64_t fold, uint64_t seq,
    enum cachetag_purge_mode mode, int *createdp,
    struct cachetag_purgemap_table **retiredp)
{
	int r;

	r = cachetag_purgemap_prepare_upsert_locked(idx, pm, retiredp);
	if (r != 0)
		return (r);
	cachetag_purgemap_apply_upsert_locked(pm, fold, seq, mode, createdp);
	return (0);
}

static uint64_t
cachetag_purgemap_entry_newest(const struct cachetag_purgemap_entry *ent)
{
	uint64_t hard_seq, soft_seq;

	hard_seq = __atomic_load_n(&ent->hard_seq, __ATOMIC_RELAXED);
	soft_seq = __atomic_load_n(&ent->soft_seq, __ATOMIC_RELAXED);
	return (hard_seq > soft_seq ? hard_seq : soft_seq);
}

static uint64_t
cachetag_purgemap_select(uint64_t *values, size_t n, size_t kth)
{
	uint64_t pivot, tmp;
	size_t left, right, i, j;

	assert(n != 0 && kth < n);
	left = 0;
	right = n - 1;
	for (;;) {
		if (left == right)
			return (values[left]);
		pivot = values[left + (right - left) / 2];
		i = left;
		j = right;
		for (;;) {
			while (values[i] < pivot)
				i++;
			while (values[j] > pivot)
				j--;
			if (i >= j)
				break;
			tmp = values[i];
			values[i++] = values[j];
			values[j--] = tmp;
		}
		if (kth <= j)
			right = j;
		else
			left = j + 1;
	}
}

static int
cachetag_purgemap_prune_locked(struct cachetag_index *idx,
    struct cachetag_purgemap *pm, size_t *prunedp,
    struct cachetag_purgemap_table **retiredp)
{
	struct cachetag_purgemap_table *tbl, *replacement;
	struct cachetag_purgemap_entry ent;
	uint64_t *newest;
	uint64_t cutoff, hard_floor, soft_floor, seen, newbytes;
	size_t target, pruned, need, kept, nslot, u;

	AN(prunedp);
	AN(retiredp);
	*prunedp = 0;
	*retiredp = NULL;
	if (idx->limits.purgemap_history_max_entries == 0 ||
	    pm->nentry <= idx->limits.purgemap_history_max_entries)
		return (0);
	tbl = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
	if (tbl == NULL)
		return (0);
	target = (size_t)((idx->limits.purgemap_history_max_entries * 75) / 100);
	if (target >= idx->limits.purgemap_history_max_entries)
		target = (size_t)idx->limits.purgemap_history_max_entries - 1;
	need = pm->nentry > target ? pm->nentry - target : 0;
	if (need == 0)
		return (0);
	if (pm->nentry > SIZE_MAX / sizeof *newest)
		return (EFBIG);
	newest = malloc(pm->nentry * sizeof *newest);
	if (newest == NULL)
		return (ENOMEM);
	kept = 0;
	for (u = 0; u < tbl->nslot; u++) {
		seen = __atomic_load_n(&tbl->entries[u].fold, __ATOMIC_ACQUIRE);
		if (seen == TAG_PURGEMAP_EMPTY || seen == TAG_PURGEMAP_TOMBSTONE)
			continue;
		newest[kept++] = cachetag_purgemap_entry_newest(&tbl->entries[u]);
	}
	if (kept != pm->nentry || need > kept) {
		free(newest);
		return (EINVAL);
	}
	cutoff = cachetag_purgemap_select(newest, kept, need - 1);
	free(newest);
	kept -= need;
	nslot = TAG_PURGEMAP_INITIAL_SLOTS;
	while (kept * 100 > nslot * TAG_PURGEMAP_MAX_LOAD_PERCENT)
		nslot *= 2;
	replacement = cachetag_purgemap_table_new(nslot);
	if (replacement == NULL)
		return (ENOMEM);
	hard_floor = __atomic_load_n(&pm->hard_floor, __ATOMIC_ACQUIRE);
	soft_floor = __atomic_load_n(&pm->soft_floor, __ATOMIC_ACQUIRE);
	pruned = 0;
	for (u = 0; u < tbl->nslot; u++) {
		ent = tbl->entries[u];
		if (ent.fold == TAG_PURGEMAP_EMPTY ||
		    ent.fold == TAG_PURGEMAP_TOMBSTONE)
			continue;
		if (cachetag_purgemap_entry_newest(&ent) <= cutoff) {
			if (ent.hard_seq > hard_floor)
				hard_floor = ent.hard_seq;
			if (ent.soft_seq > soft_floor)
				soft_floor = ent.soft_seq;
			pruned++;
		} else
			AZ(cachetag_purgemap_table_insert_existing(replacement, &ent));
	}
	if (pruned != need) {
		free(replacement);
		return (EINVAL);
	}
	tbl->retire_reader_slot = __atomic_load_n(&pm->reader_gate.phase,
	    __ATOMIC_SEQ_CST) & 1U;
	__atomic_store_n(&pm->table, replacement, __ATOMIC_RELEASE);
	(void)__atomic_xor_fetch(&pm->reader_gate.phase, 1U, __ATOMIC_SEQ_CST);
	__atomic_store_n(&pm->nentry, kept, __ATOMIC_RELEASE);
	__atomic_store_n(&pm->ntombstone, 0, __ATOMIC_RELEASE);
	__atomic_store_n(&pm->hard_floor, hard_floor, __ATOMIC_RELEASE);
	__atomic_store_n(&pm->soft_floor, soft_floor, __ATOMIC_RELEASE);
	newbytes = cachetag_purgemap_table_bytes(replacement);
	cachetag_purgemap_account(idx, pm, newbytes, 0);
	*retiredp = tbl;
	*prunedp = pruned;
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.purgemap_prunes++;
	idx->counters.purgemap_pruned_entries += pruned;
	idx->counters.purgemap_rebuilds_shrink++;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	return (0);
}

int
cachetag_registration_snapshot(struct cachetag_index *idx,
    const char *key, struct cachetag_registration_snapshot *snap)
{
	struct cachetag_purgemap *pm;
	int r;

	r = cachetag_digest_snapshot(idx, key, snap);
	if (r != 0)
		return (r);
	pm = cachetag_purgemap_data(idx);
	if (pm != NULL)
		snap->reg_seq = __atomic_load_n(&pm->seq, __ATOMIC_ACQUIRE);
	return (0);
}

int
cachetag_generation(struct cachetag_index *idx, const char *key,
    uint64_t *generationp)
{
	struct cachetag_registration_snapshot snap;
	struct cachetag_purgemap *pm;
	struct cachetag_purgemap_table *tbl;
	const struct cachetag_purgemap_entry *ent;
	uint64_t fold, seen, hard_seq, soft_seq;
	size_t mask, pos;
	unsigned reader_slot;
	int r;

	AN(generationp);
	*generationp = 0;
	r = cachetag_digest_snapshot(idx, key, &snap);
	if (r != 0)
		return (r);
	pm = cachetag_purgemap_data(idx);
	if (pm == NULL)
		return (0);
	fold = cachetag_fold_digest(snap.digest_hi, snap.digest_lo);
	tbl = cachetag_purgemap_reader_enter(pm, &reader_slot);
	if (tbl == NULL)
		goto out;
	mask = tbl->nslot - 1;
	for (pos = fold & mask;; pos = (pos + 1) & mask) {
		ent = &tbl->entries[pos];
		seen = __atomic_load_n(&ent->fold, __ATOMIC_ACQUIRE);
		if (seen == TAG_PURGEMAP_EMPTY)
			break;
		if (seen == TAG_PURGEMAP_TOMBSTONE || seen != fold)
			continue;
		hard_seq = __atomic_load_n(&ent->hard_seq, __ATOMIC_RELAXED);
		soft_seq = __atomic_load_n(&ent->soft_seq, __ATOMIC_RELAXED);
		*generationp = hard_seq > soft_seq ? hard_seq : soft_seq;
		break;
	}
out:
	cachetag_purgemap_reader_exit(pm, reader_slot);
	return (0);
}

/*
 * Read-only diagnostic getters. They report live purge-map state: seq and
 * nentry are the same atomics the purge path maintains, and the table gauges
 * are read under the reader gate so a concurrent rebuild cannot free the
 * table mid-read. A namespace that never published a purge has no purge map
 * and reports zeroes.
 */

uint64_t
cachetag_purgemap_seq(struct cachetag_index *idx)
{
	struct cachetag_purgemap *pm;

	pm = cachetag_purgemap_data(idx);
	if (pm == NULL)
		return (0);
	return (__atomic_load_n(&pm->seq, __ATOMIC_ACQUIRE));
}

uint64_t
cachetag_purgemap_entry_count(struct cachetag_index *idx)
{
	struct cachetag_purgemap *pm;

	pm = cachetag_purgemap_data(idx);
	if (pm == NULL)
		return (0);
	return (__atomic_load_n(&pm->nentry, __ATOMIC_ACQUIRE));
}

uint64_t
cachetag_purgemap_slot_count(struct cachetag_index *idx)
{
	struct cachetag_purgemap *pm;
	const struct cachetag_purgemap_table *tbl;
	uint64_t nslot = 0;
	unsigned reader_slot;

	pm = cachetag_purgemap_data(idx);
	if (pm == NULL)
		return (0);
	tbl = cachetag_purgemap_reader_enter(pm, &reader_slot);
	if (tbl != NULL)
		nslot = tbl->nslot;
	cachetag_purgemap_reader_exit(pm, reader_slot);
	return (nslot);
}

uint64_t
cachetag_purgemap_byte_count(struct cachetag_index *idx)
{
	struct cachetag_purgemap *pm;
	const struct cachetag_purgemap_table *tbl;
	uint64_t bytes;
	unsigned reader_slot;

	pm = cachetag_purgemap_data(idx);
	if (pm == NULL)
		return (0);
	tbl = cachetag_purgemap_reader_enter(pm, &reader_slot);
	bytes = cachetag_purgemap_table_bytes(tbl);
	cachetag_purgemap_reader_exit(pm, reader_slot);
	return (bytes);
}

static enum cachetag_purgemap_probe_result
cachetag_purgemap_probe_one(const struct cachetag_purgemap_table *tbl,
    uint64_t reg_seq, uint64_t fold)
{
	const struct cachetag_purgemap_entry *ent;
	uint64_t seen, hard_seq, soft_seq;
	size_t mask, pos;

	if (tbl == NULL)
		return (TAG_PM_PROBE_NONE);
	mask = tbl->nslot - 1;
	for (pos = fold & mask;; pos = (pos + 1) & mask) {
		ent = &tbl->entries[pos];
		seen = __atomic_load_n(&ent->fold, __ATOMIC_ACQUIRE);
		if (seen == TAG_PURGEMAP_EMPTY)
			return (TAG_PM_PROBE_NONE);
		if (seen == TAG_PURGEMAP_TOMBSTONE || seen != fold)
			continue;
		hard_seq = __atomic_load_n(&ent->hard_seq, __ATOMIC_RELAXED);
		if (hard_seq > reg_seq)
			return (TAG_PM_PROBE_HARD);
		soft_seq = __atomic_load_n(&ent->soft_seq, __ATOMIC_RELAXED);
		return (soft_seq > reg_seq ? TAG_PM_PROBE_SOFT :
		    TAG_PM_PROBE_NONE);
	}
}

enum cachetag_purgemap_probe_result
cachetag_purgemap_probe_reader_guarded(struct cachetag_purgemap *pm,
    uint64_t reg_seq,
    const uint64_t *folds, unsigned nfolds)
{
	struct cachetag_purgemap_table *tbl;
	enum cachetag_purgemap_probe_result one, result;
	uint64_t hard_floor, soft_floor;
	unsigned reader_slot, u;
	int soft = 0;

	if (__atomic_load_n(&pm->nentry, __ATOMIC_ACQUIRE) == 0 &&
	    __atomic_load_n(&pm->hard_floor, __ATOMIC_ACQUIRE) == 0 &&
	    __atomic_load_n(&pm->soft_floor, __ATOMIC_ACQUIRE) == 0)
		return (TAG_PM_PROBE_NONE);
	tbl = cachetag_purgemap_reader_enter(pm, &reader_slot);
	for (u = 0; u < nfolds; u++) {
		one = cachetag_purgemap_probe_one(tbl, reg_seq, folds[u]);
		if (one == TAG_PM_PROBE_HARD) {
			result = one;
			goto out;
		}
		if (one == TAG_PM_PROBE_SOFT)
			soft = 1;
	}
	hard_floor = __atomic_load_n(&pm->hard_floor, __ATOMIC_ACQUIRE);
	soft_floor = __atomic_load_n(&pm->soft_floor, __ATOMIC_ACQUIRE);
	if (hard_floor > reg_seq)
		result = TAG_PM_PROBE_HARD;
	else if (soft || soft_floor > reg_seq)
		result = TAG_PM_PROBE_SOFT;
	else
		result = TAG_PM_PROBE_NONE;
out:
	cachetag_purgemap_reader_exit(pm, reader_slot);
	return (result);
}

int
cachetag_purgemap_probe_serialized(struct cachetag_index *idx,
    uint64_t reg_seq, const unsigned char *folds, unsigned nfolds)
{
	struct cachetag_purgemap *pm;
	struct cachetag_purgemap_table *tbl;
	uint64_t hard_floor, soft_floor, fold;
	enum cachetag_purgemap_probe_result result;
	enum cachetag_purgemap_probe_result one;
	unsigned reader_slot, u;
	int soft = 0;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	AN(folds);
	pm = cachetag_purgemap_data(idx);
	if (pm == NULL)
		return (TAG_PM_PROBE_NONE);
	/* Decode one fold at a time: stale() probes serialized object metadata on
	 * the hot path, so this path must remain allocation-free regardless of the
	 * number of tags on the object. */
	if (__atomic_load_n(&pm->nentry, __ATOMIC_ACQUIRE) == 0 &&
	    __atomic_load_n(&pm->hard_floor, __ATOMIC_ACQUIRE) == 0 &&
	    __atomic_load_n(&pm->soft_floor, __ATOMIC_ACQUIRE) == 0)
		return (TAG_PM_PROBE_NONE);
	tbl = cachetag_purgemap_reader_enter(pm, &reader_slot);
	for (u = 0; u < nfolds; u++) {
		fold = cachetag_le64dec(folds + (size_t)u * 8);
		one = cachetag_purgemap_probe_one(tbl, reg_seq, fold);
		if (one == TAG_PM_PROBE_HARD) {
			result = one;
			goto out;
		}
		if (one == TAG_PM_PROBE_SOFT)
			soft = 1;
	}
	hard_floor = __atomic_load_n(&pm->hard_floor, __ATOMIC_ACQUIRE);
	soft_floor = __atomic_load_n(&pm->soft_floor, __ATOMIC_ACQUIRE);
	if (hard_floor > reg_seq)
		result = TAG_PM_PROBE_HARD;
	else if (soft || soft_floor > reg_seq)
		result = TAG_PM_PROBE_SOFT;
	else
		result = TAG_PM_PROBE_NONE;
out:
	cachetag_purgemap_reader_exit(pm, reader_slot);
	return (result);
}

int
cachetag_purgemap_probe_snapshots(struct cachetag_index *idx,
    const struct cachetag_registration_snapshot *keys, unsigned nkeys,
    enum cachetag_purge_mode *modep)
{
	struct cachetag_purgemap *pm;
	uint64_t inline_folds[TAG_INLINE_KEYS], *folds = inline_folds;
	uint64_t reg_seq;
	enum cachetag_purgemap_probe_result result;
	unsigned u;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	AN(keys);
	if (modep != NULL)
		*modep = (enum cachetag_purge_mode)-1;
	if (nkeys == 0)
		return (0);
	pm = cachetag_purgemap_data(idx);
	/* The purgemap is lazy: no allocation means no purge can exist yet. */
	if (pm == NULL)
		return (0);
	reg_seq = keys[0].reg_seq;
	for (u = 1; u < nkeys; u++) {
		if (keys[u].reg_seq != reg_seq)
			return (EINVAL);
	}
	if (nkeys > TAG_INLINE_KEYS) {
		folds = calloc(nkeys, sizeof *folds);
		if (folds == NULL)
			return (ENOMEM);
	}
	for (u = 0; u < nkeys; u++)
		folds[u] = cachetag_fold_digest(keys[u].digest_hi,
		    keys[u].digest_lo);
	result = cachetag_purgemap_probe_reader_guarded(pm, reg_seq, folds,
	    nkeys);
	if (folds != inline_folds)
		free(folds);
	if (result == TAG_PM_PROBE_HARD) {
		if (modep != NULL)
			*modep = TAG_PURGE_HARD;
	} else if (result == TAG_PM_PROBE_SOFT) {
		if (modep != NULL)
			*modep = TAG_PURGE_SOFT;
	}
	return (0);
}

int
cachetag_attach(struct cachetag_index *idx, struct objcore *oc,
    const struct cachetag_registration_snapshot *keys, unsigned nkeys,
    enum cachetag_purge_mode *attach_purge)
{
	struct cachetag_purgemap *pm;
	void *fold_storage;
#if CACHE_TAG_SET_INTERNING
	uint64_t inline_folds[TAG_INTERN_LOOKUP_FIRST_MAX_FOLDS];
#else
	uint64_t inline_one;
#endif
	uint64_t *folds;
	uint64_t reg_seq = 0;
	unsigned u;
	int r;

	pm = cachetag_purgemap_get(idx);
	if (pm == NULL)
		goto fail_closed;
	if (nkeys > 1) {
	#if CACHE_TAG_SET_INTERNING
		if (nkeys <= TAG_INTERN_LOOKUP_FIRST_MAX_FOLDS) {
			fold_storage = NULL;
			folds = inline_folds;
		} else {
			fold_storage = cachetag_intern_candidate_alloc(idx, nkeys);
			if (fold_storage == NULL)
				goto fail_closed;
			folds = cachetag_fold_storage_values(fold_storage, nkeys);
		}
	#else
		fold_storage = cachetag_fold_storage_alloc(nkeys);
		if (fold_storage == NULL)
			goto fail_closed;
		folds = cachetag_fold_storage_values(fold_storage, nkeys);
	#endif
		if (folds == NULL) {
			cachetag_fold_storage_free(fold_storage, nkeys);
			goto fail_closed;
		}
	} else {
	#if CACHE_TAG_SET_INTERNING
		fold_storage = NULL;
		folds = inline_folds;
	#else
		fold_storage = &inline_one;
		folds = &inline_one;
	#endif
	}
	for (u = 0; u < nkeys; u++) {
		folds[u] = cachetag_fold_digest(keys[u].digest_hi,
		    keys[u].digest_lo);
		if (reg_seq == 0)
			reg_seq = keys[u].reg_seq;
	}
	r = cachetag_record_attach_purgemap_take(idx, oc, fold_storage, folds, nkeys,
	    reg_seq, attach_purge);
#if CACHE_TAG_SET_INTERNING
	if (r != 0)
		goto fail_closed;
#else
	if (r != 0) {
		cachetag_fold_storage_free(fold_storage, nkeys);
		goto fail_closed;
	}
#endif
	return (0);

fail_closed:
	cachetag_counter_add(idx, &idx->counters.volatile_attach_failures, 1);
	if (attach_purge != NULL)
		*attach_purge = TAG_PURGE_HARD;
	return (0);
}

struct cachetag_purgemap_checkpoint_iterator {
	const struct cachetag_purgemap_table *table;
	size_t cursor;
};

static int
cachetag_purgemap_checkpoint_next(void *priv,
    struct cachetag_wal_checkpoint_entry *checkpoint)
{
	struct cachetag_purgemap_checkpoint_iterator *iter;
	const struct cachetag_purgemap_entry *ent;

	iter = priv;
	if (iter->table == NULL)
		return (0);
	while (iter->cursor < iter->table->nslot) {
		ent = &iter->table->entries[iter->cursor++];
		if (ent->fold == TAG_PURGEMAP_EMPTY ||
		    ent->fold == TAG_PURGEMAP_TOMBSTONE)
			continue;
		checkpoint->fold = ent->fold;
		checkpoint->hard_sequence = ent->hard_seq;
		checkpoint->soft_sequence = ent->soft_seq;
		return (1);
	}
	return (0);
}

static int
cachetag_purgemap_checkpoint_locked(struct cachetag_index *idx,
    struct cachetag_purgemap *pm, int force)
{
	struct cachetag_purgemap_checkpoint_iterator iter;
	struct cachetag_wal_checkpoint_meta meta;

	if (!cachetag_persist_enabled(idx) ||
	    (!force && !cachetag_wal_checkpoint_due(idx->wal)))
		return (0);
	memset(&meta, 0, sizeof meta);
	meta.purge_sequence = __atomic_load_n(&pm->seq, __ATOMIC_ACQUIRE);
	meta.hard_floor = __atomic_load_n(&pm->hard_floor, __ATOMIC_ACQUIRE);
	meta.soft_floor = __atomic_load_n(&pm->soft_floor, __ATOMIC_ACQUIRE);
	meta.entries = __atomic_load_n(&pm->nentry, __ATOMIC_ACQUIRE);
	iter.table = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
	iter.cursor = 0;
	if (iter.table == NULL && meta.entries != 0)
		return (EINVAL);
	return (cachetag_wal_checkpoint(idx->wal, &meta,
	    cachetag_purgemap_checkpoint_next, &iter));
}

int
cachetag_purgemap_checkpoint(struct cachetag_index *idx, int force)
{
	struct cachetag_purgemap *pm;
	int r;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	pm = cachetag_purgemap_get(idx);
	if (pm == NULL)
		return (ENOMEM);
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	r = cachetag_purgemap_checkpoint_locked(idx, pm, force);
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
	return (r);
}

static int
cachetag_purgemap_prune_checkpoint_locked(struct cachetag_index *idx,
    struct cachetag_purgemap *pm, int force_checkpoint,
    struct cachetag_purgemap_table **retiredp)
{
	struct cachetag_purgemap_table *retired = NULL, *prune_original;
	uint64_t old_hard_floor, old_soft_floor;
	size_t old_nentry, old_ntombstone, pruned;
	int r;

	AN(retiredp);
	prune_original = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
	old_nentry = pm->nentry;
	old_ntombstone = pm->ntombstone;
	old_hard_floor = pm->hard_floor;
	old_soft_floor = pm->soft_floor;
	r = cachetag_purgemap_prune_locked(idx, pm, &pruned, &retired);
	if (r == 0 && cachetag_persist_enabled(idx)) {
		r = cachetag_purgemap_checkpoint_locked(idx, pm,
		    force_checkpoint || pruned != 0);
		if (r != 0 && pruned != 0) {
			assert(retired == prune_original);
			retired = cachetag_purgemap_prune_rollback_locked(idx, pm,
			    prune_original, old_nentry, old_ntombstone,
			    old_hard_floor, old_soft_floor, pruned);
		}
	}
	*retiredp = retired;
	return (r);
}

static uint64_t
cachetag_purgemap_run_sweep(struct cachetag_index *idx,
    struct cachetag_purgemap *pm, uint64_t sweep_start)
{
	struct cachetag_sweep_observation sweep;
	uint64_t cutoff = 0, sweep_total;
	unsigned phase = 0;
	int cert;

	cert = cachetag_purgemap_cert_begin(idx, pm, &cutoff, &phase);
	cachetag_record_sweep_purgemap(idx, &sweep);
	sweep_total = cachetag_elapsed_usec(sweep_start, cachetag_now_usec());
	cachetag_purgemap_note_sweep(idx, &sweep, sweep_total);
	if (cert)
		cachetag_purgemap_cert_finish(idx, pm, cutoff, phase,
		    sweep.aborted);
	cachetag_record_shrink(idx);
	return (sweep.killed + sweep.reduced);
}

uint64_t
cachetag_compact_all(struct cachetag_index *idx)
{
	struct cachetag_purgemap *pm;
	struct cachetag_purgemap_table *retired = NULL, *tbl;
	uint64_t sweep_start, killed_reduced;
	int r;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	pm = cachetag_purgemap_data(idx);
	if (pm == NULL) {
		cachetag_record_shrink(idx);
		return (0);
	}
	sweep_start = cachetag_now_usec();
	PTOK(pthread_mutex_lock(&idx->sweep_mtx));
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	r = cachetag_purgemap_prune_checkpoint_locked(idx, pm, 1, &retired);
	cachetag_purgemap_retire_after_commit(idx, retired);
	retired = NULL;
	tbl = __atomic_load_n(&pm->table, __ATOMIC_ACQUIRE);
	if (r == 0 && cachetag_purgemap_needs_same_size_rebuild(pm, tbl))
		r = cachetag_purgemap_rebuild_locked(idx, pm, tbl->nslot, 0,
		    &retired);
	cachetag_purgemap_retire_after_commit(idx, retired);
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
	if (r != 0) {
		PTOK(pthread_mutex_unlock(&idx->sweep_mtx));
		return (0);
	}
	killed_reduced = cachetag_purgemap_run_sweep(idx, pm, sweep_start);
	PTOK(pthread_mutex_unlock(&idx->sweep_mtx));
	return (killed_reduced);
}

int
cachetag_purge(struct cachetag_index *idx, const char *key,
    enum cachetag_purge_mode mode)
{
	struct cachetag_purgemap *pm;
	struct cachetag_purgemap_table *retired = NULL;
	struct cachetag_registration_snapshot snap;
	uint64_t cur, seq, fold;
	int created, r;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	r = cachetag_digest_snapshot(idx, key, &snap);
	if (r == EINVAL) {
		cachetag_counter_add(idx, &idx->counters.parse_errors, 1);
		cachetag_counter_add(idx, &idx->counters.limit_rejections, 1);
		return (-3);
	}
	if (r != 0) {
		cachetag_counter_add(idx, &idx->counters.limit_rejections, 1);
		return (-2);
	}
	pm = cachetag_purgemap_get(idx);
	if (pm == NULL) {
		cachetag_counter_add(idx, &idx->counters.limit_rejections, 1);
		return (-2);
	}
	fold = cachetag_fold_digest(snap.digest_hi, snap.digest_lo);
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	r = cachetag_purgemap_prepare_upsert_locked(idx, pm, &retired);
	if (r != 0) {
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		cachetag_counter_add(idx, &idx->counters.limit_rejections, 1);
		return (-2);
	}
	cur = __atomic_load_n(&pm->seq, __ATOMIC_ACQUIRE);
	if (cur == UINT64_MAX) {
		cachetag_purgemap_retire_after_commit(idx, retired);
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		cachetag_counter_add(idx, &idx->counters.limit_rejections, 1);
		return (-2);
	}
	seq = cur + 1;
	r = cachetag_persist_key_purge_digest(idx, snap.digest_hi,
	    snap.digest_lo, mode, seq);
	if (r != 0) {
		cachetag_purgemap_retire_after_commit(idx, retired);
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		return (-4);
	}
	__atomic_store_n(&pm->seq, seq, __ATOMIC_RELEASE);
	cachetag_purgemap_apply_upsert_locked(pm, fold, seq, mode, &created);
	cachetag_purgemap_retire_after_commit(idx, retired);
	retired = NULL;
	(void)cachetag_purgemap_prune_checkpoint_locked(idx, pm, 0, &retired);
	if (mode == TAG_PURGE_HARD)
		PTOK(pthread_cond_signal(&idx->sweep_cond));
	cachetag_purgemap_retire_after_commit(idx, retired);
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
	(void)created;
	cachetag_purgemap_account(idx, pm, 0, 0);
	return (-1);
}

int
cachetag_stale(struct worker *wrk, struct cachetag_index *idx,
    struct objcore *oc, cachetag_pending_probe_f *pending_probe,
    void *pending_priv)
{
	struct cachetag_purgemap *pm;
	enum cachetag_purge_mode direct_mode;
	enum cachetag_purgemap_probe_result probe;
	int found, pending_found, r;

	cachetag_note_stale_call(idx);
	if (oc != NULL && (oc->flags & OC_F_DYING)) {
		cachetag_note_stale_detected(idx);
		return (1);
	}
	pm = cachetag_purgemap_data(idx);
	probe = TAG_PM_PROBE_NONE;
	found = 0;
	if (pm != NULL)
		probe = cachetag_record_probe_purgemap(idx, oc, &found);
	if (!found && oc != NULL && pending_probe != NULL) {
		direct_mode = (enum cachetag_purge_mode)-1;
		pending_found = 0;
		r = pending_probe(pending_priv, oc, &direct_mode,
		    &pending_found);
		if (pending_found) {
			found = 1;
			if (r != 0 || direct_mode == TAG_PURGE_HARD)
				probe = TAG_PM_PROBE_HARD;
			else if (direct_mode == TAG_PURGE_SOFT)
				probe = TAG_PM_PROBE_SOFT;
		} else if (pm != NULL) {
			/* The insert callback may have attached after the first lookup. */
			probe = cachetag_record_probe_purgemap(idx, oc, &found);
		}
	}
	if (!found && oc != NULL && cachetag_persist_enabled(idx)) {
		direct_mode = (enum cachetag_purge_mode)-1;
		r = cachetag_fellow_attr_probe(wrk, idx, oc, &direct_mode);
		if (r != 0 || direct_mode == TAG_PURGE_HARD)
			probe = TAG_PM_PROBE_HARD;
		else if (direct_mode == TAG_PURGE_SOFT)
			probe = TAG_PM_PROBE_SOFT;
	}
	if (probe == TAG_PM_PROBE_HARD) {
		cachetag_counter_add(idx, &idx->counters.purgemap_probe_hard_hits,
		    1);
		cachetag_note_stale_detected(idx);
		if (oc != NULL)
			cachetag_death(idx, oc);
		return (1);
	}
	if (probe == TAG_PM_PROBE_SOFT) {
		cachetag_counter_add(idx, &idx->counters.purgemap_probe_soft_hits,
		    1);
		if (oc != NULL)
			EXP_Reduce(oc, VTIM_real(),
			    0, NAN, NAN);
	}
	return (0);
}

int
cachetag_purgemap_replay(struct cachetag_index *idx,
    const struct cachetag_wal_record *record)
{
	struct cachetag_purgemap *pm;
	struct cachetag_purgemap_table *retired = NULL;
	uint64_t digest_hi, digest_lo, seq, fold, cur;
	enum cachetag_purge_mode mode;
	size_t pruned;
	int created, r;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	AN(record);
	r = cachetag_decode_key_purge_record(record->payload,
	    record->payload_len, &digest_hi, &digest_lo, &mode, &seq);
	if (r != 0)
		return (r);
	pm = cachetag_purgemap_get(idx);
	if (pm == NULL)
		return (ENOMEM);
	fold = cachetag_fold_digest(digest_hi, digest_lo);
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	cur = __atomic_load_n(&pm->seq, __ATOMIC_ACQUIRE);
	if (seq <= cur) {
		r = EINVAL;
	} else {
		r = cachetag_purgemap_upsert_locked(idx, pm, fold, seq, mode,
		    &created, &retired);
		if (r == 0) {
			__atomic_store_n(&pm->seq, seq, __ATOMIC_RELEASE);
			cachetag_purgemap_retire_after_commit(idx, retired);
			retired = NULL;
			r = cachetag_purgemap_prune_locked(idx, pm, &pruned,
			    &retired);
		}
	}
	(void)created;
	cachetag_purgemap_retire_after_commit(idx, retired);
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
	if (r == 0)
		cachetag_purgemap_account(idx, pm, 0, 0);
	return (r);
}

int
cachetag_purgemap_checkpoint_begin(struct cachetag_index *idx,
    const struct cachetag_wal_checkpoint_meta *checkpoint)
{
	struct cachetag_purgemap *pm;
	struct cachetag_purgemap_table *tbl;
	size_t nslot;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	AN(checkpoint);
	if (checkpoint->purge_sequence == UINT64_MAX ||
	    checkpoint->hard_floor > checkpoint->purge_sequence ||
	    checkpoint->soft_floor > checkpoint->purge_sequence ||
	    checkpoint->entries > SIZE_MAX)
		return (EINVAL);
	pm = cachetag_purgemap_get(idx);
	if (pm == NULL)
		return (ENOMEM);
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	if (pm->table != NULL || pm->nentry != 0 || pm->seq != 0) {
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		return (EINVAL);
	}
	nslot = TAG_PURGEMAP_INITIAL_SLOTS;
	while (checkpoint->entries * 100 >
	    (uint64_t)nslot * TAG_PURGEMAP_MAX_LOAD_PERCENT) {
		if (nslot > SIZE_MAX / 2) {
			PTOK(pthread_mutex_unlock(&idx->purge_mtx));
			return (EFBIG);
		}
		nslot *= 2;
	}
	tbl = checkpoint->entries != 0 ?
	    cachetag_purgemap_table_new(nslot) : NULL;
	if (checkpoint->entries != 0 && tbl == NULL) {
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		return (ENOMEM);
	}
	__atomic_store_n(&pm->table, tbl, __ATOMIC_RELEASE);
	__atomic_store_n(&pm->hard_floor, checkpoint->hard_floor,
	    __ATOMIC_RELEASE);
	__atomic_store_n(&pm->soft_floor, checkpoint->soft_floor,
	    __ATOMIC_RELEASE);
	__atomic_store_n(&pm->seq, checkpoint->purge_sequence,
	    __ATOMIC_RELEASE);
	cachetag_purgemap_account(idx, pm, cachetag_purgemap_table_bytes(tbl), 0);
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
	return (0);
}

int
cachetag_purgemap_checkpoint_entry(struct cachetag_index *idx,
    const struct cachetag_wal_checkpoint_entry *checkpoint)
{
	struct cachetag_purgemap *pm;
	struct cachetag_purgemap_table *tbl;
	struct cachetag_purgemap_entry ent;
	size_t mask, pos;
	uint64_t seen;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	AN(checkpoint);
	pm = cachetag_purgemap_data(idx);
	if (pm == NULL)
		return (EINVAL);
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	tbl = pm->table;
	if (tbl == NULL || pm->nentry >= tbl->nslot) {
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		return (EINVAL);
	}
	mask = tbl->nslot - 1;
	for (pos = checkpoint->fold & mask;; pos = (pos + 1) & mask) {
		seen = tbl->entries[pos].fold;
		if (seen == checkpoint->fold) {
			PTOK(pthread_mutex_unlock(&idx->purge_mtx));
			return (EINVAL);
		}
		if (seen == TAG_PURGEMAP_EMPTY)
			break;
	}
	ent.fold = checkpoint->fold;
	ent.hard_seq = checkpoint->hard_sequence;
	ent.soft_seq = checkpoint->soft_sequence;
	AZ(cachetag_purgemap_table_insert_existing(tbl, &ent));
	(void)__atomic_add_fetch(&pm->nentry, 1, __ATOMIC_RELEASE);
	cachetag_purgemap_account(idx, pm, 0, 0);
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
	return (0);
}

void *
cachetag_purgemap_sweep_thread(struct worker *wrk, void *priv)
{
	struct cachetag_index *idx;
	struct cachetag_purgemap *pm;
	struct timespec ts;
	uint64_t sweep_start;
	unsigned resize;
	int stop;
	double interval;

	CHECK_OBJ_NOTNULL(wrk, WORKER_MAGIC);
	CAST_OBJ_NOTNULL(idx, priv, TAG_INDEX_MAGIC);
	interval = idx->limits.purgemap_sweep_interval;
	while (1) {
		PTOK(pthread_mutex_lock(&idx->purge_mtx));
		cachetag_counter_add(idx, &idx->counters.sweep_iterations, 1);
		if (interval <= 0) {
			while (!idx->sweep_stop && !idx->resize_wakeup) {
				if (idx->resize_wakeup_at_usec != 0) {
					uint64_t now;

					now = cachetag_now_usec();
					if (idx->resize_wakeup_at_usec <= now) {
						idx->resize_wakeup = 1;
						idx->resize_wakeup_at_usec = 0;
						break;
					}
					ts = VTIM_timespec(VTIM_real() +
					    (double)(idx->resize_wakeup_at_usec -
					    now) / 1000000.0);
					(void)pthread_cond_timedwait(
					    &idx->sweep_cond, &idx->purge_mtx,
					    &ts);
					cachetag_counter_add(idx,
					    &idx->counters.sweep_wakeups, 1);
				} else {
					PTOK(pthread_cond_wait(&idx->sweep_cond,
					    &idx->purge_mtx));
					cachetag_counter_add(idx,
					    &idx->counters.sweep_wakeups, 1);
				}
			}
		} else if (!idx->resize_wakeup) {
			uint64_t delay_usec, now;

			delay_usec = (uint64_t)(interval * 1000000.0);
			if (idx->resize_wakeup_at_usec != 0) {
				now = cachetag_now_usec();
				if (idx->resize_wakeup_at_usec <= now) {
					idx->resize_wakeup = 1;
					idx->resize_wakeup_at_usec = 0;
				} else if (idx->resize_wakeup_at_usec - now <
				    delay_usec) {
					delay_usec = idx->resize_wakeup_at_usec -
					    now;
				}
			}
			ts = VTIM_timespec(VTIM_real() +
			    (double)delay_usec / 1000000.0);
			(void)pthread_cond_timedwait(&idx->sweep_cond,
			    &idx->purge_mtx, &ts);
			cachetag_counter_add(idx, &idx->counters.sweep_wakeups, 1);
			if (!idx->resize_wakeup &&
			    idx->resize_wakeup_at_usec != 0 &&
			    idx->resize_wakeup_at_usec <= cachetag_now_usec()) {
				idx->resize_wakeup = 1;
				idx->resize_wakeup_at_usec = 0;
			}
		}
		if (idx->sweep_stop) {
			PTOK(pthread_mutex_unlock(&idx->purge_mtx));
			break;
		}
		resize = idx->resize_wakeup;
		idx->resize_wakeup = 0;
		if (resize)
			idx->resize_wakeup_at_usec = 0;
		PTOK(pthread_mutex_unlock(&idx->purge_mtx));
		if (resize) {
			while (cachetag_resize_maintenance(idx)) {
				VTIM_sleep(TAG_RESIZE_BATCH_YIELD_SEC);
				PTOK(pthread_mutex_lock(&idx->purge_mtx));
				stop = idx->sweep_stop;
				PTOK(pthread_mutex_unlock(&idx->purge_mtx));
				if (stop)
					break;
			}
			continue;
		}
		if (interval <= 0)
			continue;
		pm = cachetag_purgemap_data(idx);
		if (pm == NULL) {
			while (cachetag_resize_maintenance(idx))
				VTIM_sleep(TAG_RESIZE_BATCH_YIELD_SEC);
			continue;
		}
		sweep_start = cachetag_now_usec();
		PTOK(pthread_mutex_lock(&idx->sweep_mtx));
		(void)cachetag_purgemap_run_sweep(idx, pm, sweep_start);
		PTOK(pthread_mutex_unlock(&idx->sweep_mtx));
		while (cachetag_resize_maintenance(idx))
			VTIM_sleep(TAG_RESIZE_BATCH_YIELD_SEC);
	}
	return (NULL);
}

void
cachetag_purgemap_destroy(struct cachetag_index *idx)
{
	struct cachetag_purgemap *pm;

	pm = cachetag_purgemap_data(idx);
	if (pm == NULL)
		return;
	if (pm->table != NULL)
		free(pm->table);
	PTOK(pthread_cond_destroy(&pm->reader_cond));
	PTOK(pthread_mutex_destroy(&pm->reader_mtx));
	free(pm);
	cachetag_purgemap_data_set(idx, NULL);
}
