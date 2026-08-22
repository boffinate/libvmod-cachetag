/*-
 * SPDX-License-Identifier: MPL-2.0
 *
 * Namespace core and volatile purge-map membership.
 */

#include "config.h"

#include <errno.h>
#include <limits.h>
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

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
#define XXH_STATIC_LINKING_ONLY
#include "xxhash.h"

struct cachetag_objent {
	struct objcore *oc;
	uint64_t reg_seq;
	union {
#if CACHE_TAG_SET_INTERNING
		struct cachetag_interned_set *set;
#else
		uint64_t *vector;
#endif
		uint64_t inline_one;
	} membership;
};

_Static_assert(sizeof(struct cachetag_objent) == 24,
	"object entry must remain 24 bytes");

#define TAG_OBJCOUNT_INVALID 0U
#define TAG_OBJCOUNT_DIRECT_MAX 254U
#define TAG_OBJCOUNT_OVERFLOW UINT8_MAX
#define TAG_FOLD_STORAGE_MAGIC UINT32_C(0x63746676)

#if CACHE_TAG_SET_INTERNING
#define TAG_INTERNED_SET_MAGIC UINT32_C(0x63747369)
#define TAG_INTERN_INITIAL_BUCKETS 64U
#define TAG_INTERN_MIGRATE_STEPS 4U
#define TAG_RESIZE_INTERN_BATCH_STEPS 64U

struct cachetag_interned_set {
	uint32_t magic;
	unsigned nfolds;
	uint64_t hash;
	uint64_t refs;
	struct cachetag_interned_set *next;
	uint64_t folds[];
};

_Static_assert(sizeof(struct cachetag_interned_set) == 32,
	"interned set header must remain 32 bytes");
#endif

struct cachetag_fold_storage_header {
	uint32_t magic;
	unsigned nfolds;
	uint64_t folds[];
};

_Static_assert(sizeof(unsigned) == sizeof(uint32_t),
	"overflow fold count requires a 32-bit unsigned");
_Static_assert(offsetof(struct cachetag_fold_storage_header, folds) == 8,
	"overflow fold header must remain 8 bytes");

void *
cachetag_fold_storage_alloc(unsigned nfolds)
{
	#if CACHE_TAG_SET_INTERNING
	struct cachetag_interned_set *set;
	#else
	struct cachetag_fold_storage_header *header;
	#endif
	size_t bytes;

	if (nfolds <= 1)
		return (NULL);
	#if CACHE_TAG_SET_INTERNING
	if ((size_t)nfolds >
	    (SIZE_MAX - sizeof *set) / sizeof(uint64_t))
		return (NULL);
	bytes = sizeof *set + (size_t)nfolds * sizeof(uint64_t);
	set = calloc(1, bytes);
	if (set == NULL)
		return (NULL);
	set->magic = TAG_INTERNED_SET_MAGIC;
	set->nfolds = nfolds;
	return (set);
	#else
	if (nfolds <= TAG_OBJCOUNT_DIRECT_MAX) {
		if ((size_t)nfolds > SIZE_MAX / sizeof(uint64_t))
			return (NULL);
		return (calloc(nfolds, sizeof(uint64_t)));
	}
	if ((size_t)nfolds >
	    (SIZE_MAX - sizeof *header) / sizeof(uint64_t))
		return (NULL);
	bytes = sizeof *header + (size_t)nfolds * sizeof(uint64_t);
	header = calloc(1, bytes);
	if (header == NULL)
		return (NULL);
	header->magic = TAG_FOLD_STORAGE_MAGIC;
	header->nfolds = nfolds;
	return (header);
	#endif
}

uint64_t *
cachetag_fold_storage_values(void *storage, unsigned nfolds)
{
	#if CACHE_TAG_SET_INTERNING
	struct cachetag_interned_set *set;
	#else
	struct cachetag_fold_storage_header *header;
	#endif

	if (storage == NULL || nfolds <= 1)
		return (NULL);
	#if CACHE_TAG_SET_INTERNING
	set = storage;
	if (set->magic != TAG_INTERNED_SET_MAGIC || set->nfolds != nfolds)
		return (NULL);
	return (set->folds);
	#else
	if (nfolds <= TAG_OBJCOUNT_DIRECT_MAX)
		return (storage);
	header = storage;
	if (header->magic != TAG_FOLD_STORAGE_MAGIC ||
	    header->nfolds != nfolds)
		return (NULL);
	return (header->folds);
	#endif
}

void
cachetag_fold_storage_free(void *storage, unsigned nfolds)
{
	if (nfolds > 1)
		free(storage);
}

#if CACHE_TAG_SET_INTERNING

/*
 * Process-local hash-consed membership sets. Every multi-fold membership
 * references one refcounted node; folds are sorted so registrations in
 * different call order share a node. The registry is guarded by obj_mtx,
 * along with the dense table and side map. It is volatile by construction:
 * restart creates an empty registry and a node dies with its last object.
 */

static size_t
cachetag_interned_set_bytes(const struct cachetag_interned_set *set)
{

	return (sizeof *set + (size_t)set->nfolds * sizeof(uint64_t));
}

static int
cachetag_fold_cmp(const void *va, const void *vb)
{
	uint64_t a, b;

	memcpy(&a, va, sizeof a);
	memcpy(&b, vb, sizeof b);
	if (a < b)
		return (-1);
	return (a > b);
}

static uint64_t
cachetag_intern_hash(const uint64_t *folds, unsigned nfolds)
{

	return (XXH3_64bits(folds, (size_t)nfolds * sizeof(uint64_t)));
}

static void
cachetag_intern_sort(uint64_t *folds, unsigned nfolds)
{
	uint64_t fold;
	unsigned i, j;

	if (nfolds > TAG_INTERN_LOOKUP_FIRST_MAX_FOLDS) {
		qsort(folds, nfolds, sizeof *folds, cachetag_fold_cmp);
		return;
	}
	for (i = 1; i < nfolds; i++) {
		fold = folds[i];
		j = i;
		while (j > 0 && folds[j - 1] > fold) {
			folds[j] = folds[j - 1];
			j--;
		}
		folds[j] = fold;
	}
}

static struct cachetag_interned_set **
cachetag_intern_bucket_for(struct cachetag_interned_set **buckets,
    size_t nbuckets, uint64_t hash)
{

	AN(buckets);
	assert(nbuckets > 0);
	return (&buckets[hash & (nbuckets - 1)]);
}

static void
cachetag_note_intern_timing(struct cachetag_index *idx,
    struct cachetag_timing_counters *counters, uint64_t usec)
{

	if (!idx->benchmark_obj_mtx_timing)
		return;
	counters->calls++;
	counters->usec += usec;
	if (usec > counters->max_usec)
		counters->max_usec = usec;
	counters->over_50us += usec > 50;
	counters->over_250us += usec > 250;
	counters->over_1ms += usec > 1000;
	counters->over_10ms += usec > 10000;
}

void *
cachetag_intern_candidate_alloc(struct cachetag_index *idx, unsigned nfolds)
{
	void *candidate;
	uint64_t started, usec;

	started = idx->benchmark_obj_mtx_timing ? cachetag_now_usec() : 0;
	if (__atomic_exchange_n(&idx->test_fail_next_intern_alloc, 0,
	    __ATOMIC_ACQ_REL))
		candidate = NULL;
	else
		candidate = cachetag_fold_storage_alloc(nfolds);
	if (!idx->benchmark_obj_mtx_timing)
		return (candidate);
	usec = cachetag_elapsed_usec(started, cachetag_now_usec());
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	cachetag_note_intern_timing(idx, &idx->intern_candidate_alloc_timing,
	    usec);
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	return (candidate);
}

static void
cachetag_note_intern_table_alloc(struct cachetag_index *idx, uint64_t usec,
    int failed)
{

	if (!idx->benchmark_obj_mtx_timing && !failed)
		return;
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	cachetag_note_intern_timing(idx, &idx->intern_table_alloc_timing, usec);
	idx->counters.volatile_interned_table_alloc_failures += failed != 0;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

struct cachetag_intern_cleanup {
	struct cachetag_interned_set *unpublished;
	struct cachetag_interned_set *sets;
	size_t set_bytes;
	struct {
		struct cachetag_interned_set **buckets;
		size_t nbuckets;
	} tables[2];
	unsigned ntables;
	size_t table_bytes;
	unsigned inventory;
};

static size_t
cachetag_intern_table_bytes(size_t nbuckets)
{

	return (nbuckets * sizeof(struct cachetag_interned_set *));
}

static void
cachetag_intern_cleanup_add_set_locked(struct cachetag_index *idx,
    struct cachetag_intern_cleanup *cleanup, struct cachetag_interned_set *set)
{
	size_t bytes;

	AN(cleanup);
	AN(set);
	bytes = cachetag_interned_set_bytes(set);
	set->next = cleanup->sets;
	cleanup->sets = set;
	cleanup->set_bytes += bytes;
	idx->intern_detached_set_bytes += bytes;
}

static void
cachetag_intern_cleanup_add_table_locked(struct cachetag_index *idx,
    struct cachetag_intern_cleanup *cleanup,
    struct cachetag_interned_set **buckets, size_t nbuckets)
{
	size_t bytes;

	if (buckets == NULL)
		return;
	AN(cleanup);
	assert(cleanup->ntables < sizeof cleanup->tables / sizeof cleanup->tables[0]);
	bytes = cachetag_intern_table_bytes(nbuckets);
	cleanup->tables[cleanup->ntables].buckets = buckets;
	cleanup->tables[cleanup->ntables].nbuckets = nbuckets;
	cleanup->ntables++;
	cleanup->table_bytes += bytes;
	idx->intern_detached_table_bytes += bytes;
}

static void
cachetag_intern_cleanup_free(struct cachetag_index *idx,
    struct cachetag_intern_cleanup *cleanup)
{
	struct cachetag_interned_set *set, *next;
	unsigned u;
	size_t b;

	if (cleanup->unpublished != NULL)
		cachetag_counter_add(idx,
		    &idx->counters.volatile_interned_candidate_discards, 1);
	if (cleanup->unpublished != NULL)
		free(cleanup->unpublished);
	for (set = cleanup->sets; set != NULL; set = next) {
		next = set->next;
		free(set);
	}
	if (cleanup->inventory) {
		for (u = 0; u < cleanup->ntables; u++) {
			for (b = 0; b < cleanup->tables[u].nbuckets; b++) {
				for (set = cleanup->tables[u].buckets[b]; set != NULL;
				    set = next) {
					next = set->next;
					free(set);
				}
			}
		}
	}
	for (u = 0; u < cleanup->ntables; u++)
		free(cleanup->tables[u].buckets);
	if (cleanup->set_bytes != 0 || cleanup->table_bytes != 0) {
		PTOK(pthread_mutex_lock(&idx->obj_mtx));
		assert(idx->intern_detached_set_bytes >= cleanup->set_bytes);
		assert(idx->intern_detached_table_bytes >= cleanup->table_bytes);
		idx->intern_detached_set_bytes -= cleanup->set_bytes;
		idx->intern_detached_table_bytes -= cleanup->table_bytes;
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	}
	memset(cleanup, 0, sizeof *cleanup);
}

static struct cachetag_interned_set *
cachetag_intern_lookup_table_locked(struct cachetag_interned_set **buckets,
    size_t nbuckets, const uint64_t *folds, unsigned nfolds, uint64_t hash)
{
	struct cachetag_interned_set *set;

	if (buckets == NULL)
		return (NULL);
	for (set = *cachetag_intern_bucket_for(buckets, nbuckets, hash);
	    set != NULL; set = set->next) {
		if (set->hash == hash && set->nfolds == nfolds &&
		    memcmp(set->folds, folds,
		    (size_t)nfolds * sizeof(uint64_t)) == 0)
			return (set);
	}
	return (NULL);
}

static struct cachetag_interned_set *
cachetag_intern_lookup_locked(struct cachetag_index *idx,
    const uint64_t *folds, unsigned nfolds, uint64_t hash)
{
	struct cachetag_interned_set *set;

	set = cachetag_intern_lookup_table_locked(idx->intern_buckets,
	    idx->intern_nbuckets, folds, nfolds, hash);
	if (set == NULL)
		set = cachetag_intern_lookup_table_locked(idx->intern_old_buckets,
		    idx->intern_old_nbuckets, folds, nfolds, hash);
	if (set != NULL) {
		set->refs++;
		idx->intern_refs++;
		idx->intern_hits++;
	}
	return (set);
}

static int
cachetag_intern_prepare_insert_locked(struct cachetag_index *idx,
    size_t *bucketsp)
{
	if (bucketsp != NULL)
		*bucketsp = 0;
	if (idx->intern_buckets == NULL) {
		if (bucketsp != NULL)
			*bucketsp = idx->test_intern_initial_buckets != 0 ?
			    idx->test_intern_initial_buckets : TAG_INTERN_INITIAL_BUCKETS;
		return (EAGAIN);
	}
	if (idx->intern_sets + 1 <= idx->intern_nbuckets ||
	    idx->intern_migration_active)
		return (0);
	if (idx->intern_nbuckets > SIZE_MAX / 2 ||
	    idx->intern_nbuckets * 2 > SIZE_MAX / sizeof *idx->intern_buckets)
		return (0);
	if (bucketsp != NULL)
		*bucketsp = idx->intern_nbuckets * 2;
	return (EAGAIN);
}

static int
cachetag_intern_publish_table_locked(struct cachetag_index *idx,
    struct cachetag_interned_set **buckets, size_t nbuckets,
    uint64_t generation)
{
	if (buckets == NULL || nbuckets == 0 ||
	    (nbuckets & (nbuckets - 1)) != 0 || idx->intern_migration_active ||
	    generation != idx->intern_generation)
		return (EINVAL);
	if (idx->intern_buckets == NULL) {
		if (nbuckets != (idx->test_intern_initial_buckets != 0 ?
		    idx->test_intern_initial_buckets : TAG_INTERN_INITIAL_BUCKETS))
			return (ESTALE);
		idx->intern_buckets = buckets;
		idx->intern_nbuckets = nbuckets;
		idx->intern_generation++;
		return (0);
	}
	if (nbuckets != idx->intern_nbuckets * 2 ||
	    idx->intern_sets + 1 <= idx->intern_nbuckets)
		return (ESTALE);
	idx->intern_old_buckets = idx->intern_buckets;
	idx->intern_old_nbuckets = idx->intern_nbuckets;
	idx->intern_buckets = buckets;
	idx->intern_nbuckets = nbuckets;
	idx->intern_migrate_cursor = 0;
	idx->intern_migration_active = 1;
	idx->intern_generation++;
	return (0);
}

static struct cachetag_interned_set **
cachetag_intern_alloc_buckets(struct cachetag_index *idx, size_t buckets)
{

	if (__atomic_exchange_n(&idx->test_fail_next_intern_table_alloc, 0,
	    __ATOMIC_ACQ_REL))
		return (NULL);
	return (calloc(buckets, sizeof(struct cachetag_interned_set *)));
}

static void
cachetag_intern_migrate_locked(struct cachetag_index *idx, size_t steps,
    struct cachetag_intern_cleanup *cleanup)
{
	struct cachetag_interned_set **head, *set;

	while (idx->intern_migration_active && steps-- != 0) {
		if (idx->intern_migrate_cursor == idx->intern_old_nbuckets) {
			cachetag_intern_cleanup_add_table_locked(idx, cleanup,
			    idx->intern_old_buckets, idx->intern_old_nbuckets);
			idx->intern_old_buckets = NULL;
			idx->intern_old_nbuckets = 0;
			idx->intern_migrate_cursor = 0;
			idx->intern_migration_active = 0;
			break;
		}
		head = &idx->intern_old_buckets[idx->intern_migrate_cursor];
		if (*head == NULL) {
			idx->intern_migrate_cursor++;
			continue;
		}
		set = *head;
		*head = set->next;
		head = cachetag_intern_bucket_for(idx->intern_buckets,
		    idx->intern_nbuckets, set->hash);
		set->next = *head;
		*head = set;
	}
	if (idx->intern_migration_active &&
	    idx->intern_migrate_cursor == idx->intern_old_nbuckets) {
		cachetag_intern_cleanup_add_table_locked(idx, cleanup,
		    idx->intern_old_buckets, idx->intern_old_nbuckets);
		idx->intern_old_buckets = NULL;
		idx->intern_old_nbuckets = 0;
		idx->intern_migrate_cursor = 0;
		idx->intern_migration_active = 0;
	}
}

static struct cachetag_interned_set *
cachetag_intern_publish_locked(struct cachetag_index *idx,
    struct cachetag_interned_set *candidate)
{
	struct cachetag_interned_set **head;

	AN(candidate);
	assert(candidate->refs == 0);
	head = cachetag_intern_bucket_for(idx->intern_buckets,
	    idx->intern_nbuckets, candidate->hash);
	candidate->refs = 1;
	candidate->next = *head;
	*head = candidate;
	idx->intern_sets++;
	idx->intern_refs++;
	idx->intern_misses++;
	idx->intern_bytes += cachetag_interned_set_bytes(candidate);
	idx->intern_overflow_sets += candidate->nfolds >= TAG_OBJCOUNT_OVERFLOW;
	return (candidate);
}

static void
cachetag_intern_release_locked(struct cachetag_index *idx,
    struct cachetag_interned_set *set, struct cachetag_intern_cleanup *cleanup)
{
	struct cachetag_interned_set **link;

	AN(set);
	assert(set->magic == TAG_INTERNED_SET_MAGIC);
	assert(set->refs > 0);
	assert(idx->intern_refs > 0);
	idx->intern_refs--;
	if (--set->refs > 0)
		return;
	link = NULL;
	if (idx->intern_buckets != NULL) {
		for (link = cachetag_intern_bucket_for(idx->intern_buckets,
		    idx->intern_nbuckets, set->hash); *link != NULL;
		    link = &(*link)->next) {
			if (*link == set)
				break;
		}
	}
	if ((link == NULL || *link == NULL) && idx->intern_old_buckets != NULL) {
		for (link = cachetag_intern_bucket_for(idx->intern_old_buckets,
		    idx->intern_old_nbuckets, set->hash); *link != NULL;
		    link = &(*link)->next) {
			if (*link == set)
				break;
		}
	}
	AN(link);
	AN(*link);
	*link = set->next;
	assert(idx->intern_sets > 0);
	idx->intern_sets--;
	assert(idx->intern_bytes >= cachetag_interned_set_bytes(set));
	idx->intern_bytes -= cachetag_interned_set_bytes(set);
	if (set->nfolds >= TAG_OBJCOUNT_OVERFLOW) {
		assert(idx->intern_overflow_sets > 0);
		idx->intern_overflow_sets--;
	}
	cachetag_intern_cleanup_add_set_locked(idx, cleanup, set);
}

static void
cachetag_intern_detach_all_locked(struct cachetag_index *idx,
    struct cachetag_intern_cleanup *cleanup)
{

	cachetag_intern_cleanup_add_table_locked(idx, cleanup,
	    idx->intern_buckets, idx->intern_nbuckets);
	cachetag_intern_cleanup_add_table_locked(idx, cleanup,
	    idx->intern_old_buckets, idx->intern_old_nbuckets);
	cleanup->set_bytes += idx->intern_bytes;
	idx->intern_detached_set_bytes += idx->intern_bytes;
	cleanup->inventory = 1;
	idx->intern_generation++;
	idx->intern_buckets = NULL;
	idx->intern_nbuckets = 0;
	idx->intern_old_buckets = NULL;
	idx->intern_old_nbuckets = 0;
	idx->intern_migrate_cursor = 0;
	idx->intern_migration_active = 0;
	idx->intern_sets = 0;
	idx->intern_refs = 0;
	idx->intern_bytes = 0;
	idx->intern_overflow_sets = 0;
}

#endif /* CACHE_TAG_SET_INTERNING */

#define TAG_OBJECT_SEGMENT0_SLOTS 64U

static size_t
cachetag_object_segment_slots(unsigned segment)
{

	assert(segment < TAG_OBJECT_SEGMENTS);
	if (segment == 0 || segment == 1)
		return (TAG_OBJECT_SEGMENT0_SLOTS);
	return ((size_t)TAG_OBJECT_SEGMENT0_SLOTS << (segment - 1));
}

static size_t
cachetag_object_segment_base(unsigned segment)
{

	assert(segment < TAG_OBJECT_SEGMENTS);
	if (segment == 0)
		return (0);
	return ((size_t)TAG_OBJECT_SEGMENT0_SLOTS << (segment - 1));
}

static size_t
cachetag_object_capacity_for_segments(unsigned nsegments)
{

	assert(nsegments <= TAG_OBJECT_SEGMENTS);
	if (nsegments == 0)
		return (0);
	if (nsegments == 1)
		return (TAG_OBJECT_SEGMENT0_SLOTS);
	return ((size_t)TAG_OBJECT_SEGMENT0_SLOTS << (nsegments - 1));
}

static unsigned
cachetag_object_segment_count_for_capacity(size_t capacity)
{
	unsigned nsegments;

	if (capacity == 0)
		return (0);
	for (nsegments = 1; nsegments <= TAG_OBJECT_SEGMENTS; nsegments++) {
		if (cachetag_object_capacity_for_segments(nsegments) >= capacity)
			return (nsegments);
	}
	return (TAG_OBJECT_SEGMENTS + 1);
}

static unsigned
cachetag_object_segment_for_slot(size_t slot)
{
	unsigned segment;
	unsigned long long value;

	if (slot < TAG_OBJECT_SEGMENT0_SLOTS)
		return (0);
	/*
	 * Segment 0 covers [0, 64).  For all later segments, shifting away
	 * those six low bits turns each segment into a power-of-two bucket:
	 * [2^n, 2^(n + 1)) maps to segment n + 1.  Thus the bit width of the
	 * shifted value is the segment index.  The zero case is handled above,
	 * so clzll is defined.
	 */
	value = (unsigned long long)(slot >> 6);
	segment = (unsigned)(sizeof value * CHAR_BIT - __builtin_clzll(value));
	if (segment >= TAG_OBJECT_SEGMENTS)
		WRONG("object slot outside segmented directory");
	return (segment);
}

static struct cachetag_objent *
cachetag_object_at(const struct cachetag_index *idx, size_t slot)
{
	unsigned segment;
	size_t base;

	assert(slot < idx->capobjects);
	segment = cachetag_object_segment_for_slot(slot);
	base = cachetag_object_segment_base(segment);
	AN(idx->object_segments[segment]);
	return (&idx->object_segments[segment][slot - base]);
}

static uint8_t *
cachetag_object_count_at(const struct cachetag_index *idx, size_t slot)
{
	struct cachetag_objent *segment_entries;
	uint8_t *segment_counts;
	unsigned segment;
	size_t base;

	assert(slot < idx->capobjects);
	segment = cachetag_object_segment_for_slot(slot);
	base = cachetag_object_segment_base(segment);
	segment_entries = idx->object_segments[segment];
	AN(segment_entries);
	segment_counts = (uint8_t *)(void *)(segment_entries +
	    cachetag_object_segment_slots(segment));
	return (&segment_counts[slot - base]);
}

static unsigned
cachetag_objent_nfolds(const struct cachetag_index *idx, size_t slot)
{
#if CACHE_TAG_SET_INTERNING
	const struct cachetag_interned_set *set;
#else
	const struct cachetag_fold_storage_header *header;
#endif
	const struct cachetag_objent *ent;
	unsigned code;

	ent = cachetag_object_at(idx, slot);
	code = *cachetag_object_count_at(idx, slot);
	if (code == TAG_OBJCOUNT_INVALID)
		return (0);
#if CACHE_TAG_SET_INTERNING
	if (code == 1)
		return (1);
	set = ent->membership.set;
	if (set == NULL || set->magic != TAG_INTERNED_SET_MAGIC)
		return (0);
	if (code != TAG_OBJCOUNT_OVERFLOW)
		return (set->nfolds == code ? code : 0);
	if (set->nfolds < TAG_OBJCOUNT_OVERFLOW ||
	    set->nfolds > idx->limits.max_keys_per_object)
		return (0);
	return (set->nfolds);
#else
	if (code != TAG_OBJCOUNT_OVERFLOW)
		return (code);
	header = (const void *)ent->membership.vector;
	if (header == NULL || header->magic != TAG_FOLD_STORAGE_MAGIC ||
	    header->nfolds < TAG_OBJCOUNT_OVERFLOW ||
	    header->nfolds > idx->limits.max_keys_per_object)
		return (0);
	return (header->nfolds);
#endif
}

static const uint64_t *
cachetag_objent_folds(const struct cachetag_index *idx, size_t slot)
{
#if !CACHE_TAG_SET_INTERNING
	const struct cachetag_fold_storage_header *header;
#endif
	const struct cachetag_objent *ent;
	unsigned nfolds;

	ent = cachetag_object_at(idx, slot);
	nfolds = cachetag_objent_nfolds(idx, slot);
	if (nfolds == 0)
		return (NULL);
	if (nfolds == 1)
		return (&ent->membership.inline_one);
#if CACHE_TAG_SET_INTERNING
	return (ent->membership.set->folds);
#else
	if (nfolds <= TAG_OBJCOUNT_DIRECT_MAX)
		return (ent->membership.vector);
	header = (const void *)ent->membership.vector;
	return (header->folds);
#endif
}

#if !CACHE_TAG_SET_INTERNING
static void
cachetag_objent_dispose(struct cachetag_index *idx, size_t slot)
{
	struct cachetag_objent *ent;
	unsigned nfolds;

	ent = cachetag_object_at(idx, slot);
	nfolds = cachetag_objent_nfolds(idx, slot);
	if (nfolds > 1)
		cachetag_fold_storage_free(ent->membership.vector, nfolds);
	*cachetag_object_count_at(idx, slot) = TAG_OBJCOUNT_INVALID;
}
#endif

static size_t
cachetag_object_table_bytes(const struct cachetag_index *idx)
{

	return (idx->capobjects * sizeof(struct cachetag_objent));
}

static size_t
cachetag_object_count_sidecar_bytes(const struct cachetag_index *idx)
{

	return (idx->capobjects * sizeof(uint8_t));
}

static size_t
cachetag_object_storage_bytes(const struct cachetag_index *idx)
{

	return (cachetag_object_table_bytes(idx) +
	    cachetag_object_count_sidecar_bytes(idx));
}

static int
cachetag_object_segment_allocation_bytes(unsigned segment, size_t *bytesp)
{
	size_t slots;

	slots = cachetag_object_segment_slots(segment);
	if (slots > SIZE_MAX /
	    (sizeof(struct cachetag_objent) + sizeof(uint8_t)))
		return (0);
	if (bytesp != NULL)
		*bytesp = slots *
		    (sizeof(struct cachetag_objent) + sizeof(uint8_t));
	return (1);
}

static struct cachetag_objent *
cachetag_object_segment_alloc(struct cachetag_index *idx, unsigned segment)
{
	size_t bytes;

	if (!cachetag_object_segment_allocation_bytes(segment, &bytes))
		return (NULL);
	if (__atomic_exchange_n(&idx->test_fail_next_object_segment_alloc, 0,
	    __ATOMIC_ACQ_REL))
		return (NULL);
	return (calloc(1, bytes));
}

static void
cachetag_object_free_segments(struct cachetag_objent **segments,
    unsigned nsegments)
{
	unsigned u;

	for (u = 0; u < nsegments; u++)
		free(segments[u]);
}

static unsigned
cachetag_object_detach_segments_locked(struct cachetag_index *idx,
    size_t target_capacity, struct cachetag_objent **detached)
{
	unsigned old_segments, new_segments, n, u;

	assert(target_capacity <= idx->capobjects);
	assert(idx->nobjects <= target_capacity);
	old_segments = cachetag_object_segment_count_for_capacity(idx->capobjects);
	new_segments = cachetag_object_segment_count_for_capacity(target_capacity);
	assert(old_segments <= TAG_OBJECT_SEGMENTS);
	assert(new_segments <= old_segments);
	n = 0;
	for (u = new_segments; u < old_segments; u++) {
		detached[n++] = idx->object_segments[u];
		idx->object_segments[u] = NULL;
	}
	idx->capobjects = target_capacity;
	return (n);
}

enum cachetag_side_bucket_state {
	TAG_SIDE_EMPTY = 0,
	TAG_SIDE_LIVE = 1,
	TAG_SIDE_TOMBSTONE = 2
};

struct cachetag_side_bucket {
	uint32_t fingerprint;
	uint32_t slot_code;
};

_Static_assert(sizeof(struct cachetag_side_bucket) == 8,
	"fingerprint side bucket must remain 8 bytes");

#define TAG_SIDE_SLOT_EMPTY 0U
#define TAG_SIDE_SLOT_TOMBSTONE UINT32_MAX
#define TAG_SIDE_MAX_OBJECTS ((size_t)UINT32_MAX - 1U)

static enum cachetag_side_bucket_state
cachetag_side_bucket_state(const struct cachetag_side_bucket *bucket)
{

	if (bucket->slot_code == TAG_SIDE_SLOT_EMPTY)
		return (TAG_SIDE_EMPTY);
	if (bucket->slot_code == TAG_SIDE_SLOT_TOMBSTONE)
		return (TAG_SIDE_TOMBSTONE);
	return (TAG_SIDE_LIVE);
}

static int
cachetag_side_slot_encode(size_t slot, uint32_t *code)
{

	if (slot >= TAG_SIDE_MAX_OBJECTS)
		return (0);
	if (code != NULL)
		*code = (uint32_t)slot + 1U;
	return (1);
}

static int
cachetag_side_slot_decode(uint32_t code, size_t *slot)
{

	if (code == TAG_SIDE_SLOT_EMPTY || code == TAG_SIDE_SLOT_TOMBSTONE)
		return (0);
	if (slot != NULL)
		*slot = (size_t)code - 1U;
	return (1);
}

#define TAG_SIDE_DEFAULT_BUCKETS 64U
#define TAG_SIDE_SOFT_GROW_LOAD_NUMERATOR 5U
#define TAG_SIDE_SOFT_GROW_LOAD_DENOMINATOR 8U
#define TAG_SIDE_HARD_GROW_LOAD_NUMERATOR 7U
#define TAG_SIDE_HARD_GROW_LOAD_DENOMINATOR 10U
#define TAG_SIDE_GROW_MIN_RUNWAY 32U
#define TAG_OBJECT_GROW_RUNWAY_DIVISOR 8U
#define TAG_OBJECT_GROW_RUNWAY_ABSOLUTE 32768U
#define TAG_RESIZE_SIDE_BATCH_BUCKETS 8192U
#define TAG_RESIZE_LOW_WATER_OBSERVE_USEC UINT64_C(5000000)
#define TAG_RESIZE_LOW_WATER_REARM_USEC UINT64_C(100000)
#define TAG_RESIZE_LOW_WATER_GROW_TOLERANCE_DIVISOR 8U
enum cachetag_side_migration_reason {
	TAG_SIDE_MIGRATION_NONE = 0,
	TAG_SIDE_MIGRATION_GROW,
	TAG_SIDE_MIGRATION_REBUILD,
	TAG_SIDE_MIGRATION_SHRINK,
	TAG_SIDE_MIGRATION_ROLLBACK
};

enum cachetag_resize_vsc_state {
	TAG_RESIZE_VSC_IDLE = 0,
	TAG_RESIZE_VSC_SIDE_MIGRATION,
	TAG_RESIZE_VSC_LOW_WATER_OBSERVE,
	TAG_RESIZE_VSC_LOW_WATER_READY
};

enum cachetag_resize_low_water_cancel_reason {
	TAG_RESIZE_LOW_WATER_CANCEL_NONE = 0,
	TAG_RESIZE_LOW_WATER_CANCEL_REFILL_OVERRUN,
	TAG_RESIZE_LOW_WATER_CANCEL_OBJECT_GROWTH,
	TAG_RESIZE_LOW_WATER_CANCEL_SIDE_GROWTH,
	TAG_RESIZE_LOW_WATER_CANCEL_NO_RECLAIM,
	TAG_RESIZE_LOW_WATER_CANCEL_ALLOC_FAILURE
};

struct cachetag_side_location {
	struct cachetag_side_table *table;
	struct cachetag_side_bucket *bucket;
	size_t slot;
};

void
cachetag_limits_default(struct cachetag_limits *lim)
{
	AN(lim);
	memset(lim, 0, sizeof *lim);
	lim->max_keys_per_object = TAG_DEFAULT_MAX_KEYS_PER_OBJECT;
	lim->max_key_length = TAG_DEFAULT_MAX_KEY_LENGTH;
	lim->max_header_bytes = TAG_DEFAULT_MAX_HEADER_BYTES;
	lim->purgemap_sweep_interval = TAG_DEFAULT_PURGEMAP_SWEEP_INTERVAL;
	lim->purgemap_sweep_batch_objects =
	    TAG_DEFAULT_PURGEMAP_SWEEP_BATCH_OBJECTS;
	lim->purgemap_sweep_batch_usec =
	    TAG_DEFAULT_PURGEMAP_SWEEP_BATCH_USEC;
	lim->purgemap_sweep_batch_yield_usec =
	    TAG_DEFAULT_PURGEMAP_SWEEP_BATCH_YIELD_USEC;
}

static void
cachetag_digest(const struct cachetag_index *idx, const char *key, size_t len,
    uint64_t *hi, uint64_t *lo)
{
	XXH3_state_t state;
	XXH128_hash_t h;
	const char nul = '\0';
	AZ(XXH3_128bits_reset(&state));
	AZ(XXH3_128bits_update(&state, idx->namespace, idx->namespace_len));
	AZ(XXH3_128bits_update(&state, &nul, 1));
	AZ(XXH3_128bits_update(&state, key, len));
	h = XXH3_128bits_digest(&state);
	*hi = h.high64;
	*lo = h.low64;
}

int
cachetag_digest_snapshot(struct cachetag_index *idx, const char *key,
    struct cachetag_registration_snapshot *snap)
{
	size_t len;
	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	if (key == NULL || *key == '\0')
		return (EINVAL);
	len = strlen(key);
	if (len > idx->limits.max_key_length)
		return (E2BIG);
	memset(snap, 0, sizeof *snap);
	cachetag_digest(idx, key, len, &snap->digest_hi, &snap->digest_lo);
	return (0);
}

uint64_t
cachetag_fold_digest(uint64_t hi, uint64_t lo)
{
	uint64_t fold = hi ^ lo;
	return (fold == 0 || fold == UINT64_MAX ? 1 : fold);
}

void
cachetag_namespace_digest(const struct cachetag_index *idx, uint64_t *hi,
    uint64_t *lo)
{
	XXH128_hash_t h;
	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	h = XXH3_128bits(idx->namespace, idx->namespace_len);
	*hi = h.high64;
	*lo = h.low64;
}

struct cachetag_purgemap *
cachetag_purgemap_data(const struct cachetag_index *idx)
{
	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	return (__atomic_load_n(&idx->purgemap_data, __ATOMIC_ACQUIRE));
}

void
cachetag_purgemap_data_set(struct cachetag_index *idx,
    struct cachetag_purgemap *data)
{
	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	__atomic_store_n(&idx->purgemap_data, data, __ATOMIC_RELEASE);
}

void
cachetag_counter_add(struct cachetag_index *idx, uint64_t *counter, uint64_t n)
{
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	*counter += n;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static void
cachetag_note_object_emergency_segment(struct cachetag_index *idx,
    size_t old_capacity)
{

	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.object_emergency_segment_allocations++;
	if (old_capacity > idx->counters.object_emergency_segment_old_capacity_max)
		idx->counters.object_emergency_segment_old_capacity_max = old_capacity;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static void
cachetag_note_attach_side_grow(struct cachetag_index *idx, size_t old_buckets)
{

	/* A zero old table is lazy first allocation, not an emergency grow. */
	if (old_buckets == 0)
		return;
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.side_resize_attach_grow_publishes++;
	if (old_buckets > idx->counters.side_resize_attach_grow_old_buckets_max)
		idx->counters.side_resize_attach_grow_old_buckets_max = old_buckets;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

void
cachetag_note_stale_call(struct cachetag_index *idx)
{
	cachetag_counter_add(idx, &idx->counters.stale_calls, 1);
}

void
cachetag_note_stale_detected(struct cachetag_index *idx)
{
	cachetag_counter_add(idx, &idx->counters.stale_detected, 1);
}

static size_t cachetag_object_low_water_target_locked(
    const struct cachetag_index *);
static size_t cachetag_side_low_water_target_locked(
    const struct cachetag_index *);

static void
cachetag_account_objects_locked(struct cachetag_index *idx)
{
	size_t active_bytes, count_bytes, object_bytes, side_buckets, side_bytes;
	size_t side_primary_bytes, side_retiring_bytes;
	uint64_t now;

	side_buckets = idx->side_primary.buckets + idx->side_retiring.buckets;
	side_bytes = side_buckets * sizeof(struct cachetag_side_bucket);
	side_primary_bytes =
	    idx->side_primary.buckets * sizeof(struct cachetag_side_bucket);
	side_retiring_bytes =
	    idx->side_retiring.buckets * sizeof(struct cachetag_side_bucket);
	object_bytes = cachetag_object_table_bytes(idx);
	count_bytes = cachetag_object_count_sidecar_bytes(idx);
	active_bytes = object_bytes + count_bytes + side_primary_bytes;
	now = cachetag_now_usec();
	idx->counters.volatile_objects = idx->nobjects;
	idx->counters.volatile_side_table_buckets = side_buckets;
	idx->counters.volatile_side_table_bytes = side_bytes;
	idx->counters.volatile_object_table_slots = idx->capobjects;
	idx->counters.volatile_object_table_bytes = object_bytes;
	idx->counters.volatile_object_count_sidecar_bytes = count_bytes;
	idx->counters.object_segments =
	    cachetag_object_segment_count_for_capacity(idx->capobjects);
	idx->counters.object_published_slots = idx->capobjects;
	idx->counters.object_published_bytes = object_bytes;
	idx->counters.object_count_published_bytes = count_bytes;
	idx->counters.side_primary_buckets = idx->side_primary.buckets;
	idx->counters.side_primary_bytes = side_primary_bytes;
	idx->counters.side_primary_live = idx->side_primary.live;
	idx->counters.side_primary_tombstones = idx->side_primary.tombstones;
	idx->counters.side_retiring_buckets = idx->side_retiring.buckets;
	idx->counters.side_retiring_bytes = side_retiring_bytes;
	idx->counters.side_retiring_live = idx->side_retiring.live;
	idx->counters.side_retiring_tombstones = idx->side_retiring.tombstones;
	if (idx->side_migration_active) {
		idx->counters.side_resize_state = TAG_RESIZE_VSC_SIDE_MIGRATION;
		idx->counters.side_resize_reason = idx->side_migration_reason;
	} else if (idx->resize_low_water_active) {
		if (idx->resize_low_water_force ||
		    cachetag_elapsed_usec(idx->resize_low_water_start_usec, now) >=
		    TAG_RESIZE_LOW_WATER_OBSERVE_USEC)
			idx->counters.side_resize_state =
			    TAG_RESIZE_VSC_LOW_WATER_READY;
		else
			idx->counters.side_resize_state =
			    TAG_RESIZE_VSC_LOW_WATER_OBSERVE;
		idx->counters.side_resize_reason = TAG_SIDE_MIGRATION_SHRINK;
	} else {
		idx->counters.side_resize_state = TAG_RESIZE_VSC_IDLE;
		idx->counters.side_resize_reason = TAG_SIDE_MIGRATION_NONE;
	}
	idx->counters.side_migration_buckets_remaining =
	    idx->side_migration_active &&
	    idx->side_retiring.buckets > idx->side_migrate_cursor ?
	    idx->side_retiring.buckets - idx->side_migrate_cursor : 0;
	idx->counters.side_migration_live_remaining =
	    idx->side_migration_active ? idx->side_retiring.live : 0;
	idx->counters.resize_low_water_active =
	    idx->resize_low_water_active != 0;
	idx->counters.resize_low_water_elapsed_usec =
	    idx->resize_low_water_active ?
	    cachetag_elapsed_usec(idx->resize_low_water_start_usec, now) : 0;
	idx->counters.resize_low_water_observed_live =
	    idx->resize_low_water_active ? idx->resize_low_water_live : 0;
	idx->counters.resize_low_water_target_objects =
	    idx->resize_low_water_active ? cachetag_object_low_water_target_locked(idx) : 0;
	idx->counters.resize_low_water_target_side_buckets =
	    idx->resize_low_water_active ? cachetag_side_low_water_target_locked(idx) : 0;
	idx->counters.resize_active_bytes = active_bytes;
	idx->counters.resize_retiring_bytes = side_retiring_bytes;
	idx->counters.resize_detached_bytes = idx->resize_detached_bytes;
	idx->counters.resize_reconciled_bytes = active_bytes +
	    side_retiring_bytes + idx->resize_detached_bytes;
#if CACHE_TAG_SET_INTERNING
	idx->counters.volatile_interned_sets = idx->intern_sets;
	idx->counters.volatile_interned_set_refs = idx->intern_refs;
	idx->counters.volatile_interned_set_hits = idx->intern_hits;
	idx->counters.volatile_interned_set_misses = idx->intern_misses;
	idx->counters.volatile_interned_set_bytes = idx->intern_bytes;
	idx->counters.volatile_interned_table_bytes =
	    cachetag_intern_table_bytes(idx->intern_nbuckets) +
	    cachetag_intern_table_bytes(idx->intern_old_nbuckets);
	idx->counters.volatile_interned_migration_active =
	    idx->intern_migration_active;
	idx->counters.volatile_interned_old_table_bytes =
	    cachetag_intern_table_bytes(idx->intern_old_nbuckets);
	idx->counters.volatile_interned_detached_set_bytes =
	    idx->intern_detached_set_bytes;
	idx->counters.volatile_interned_detached_table_bytes =
	    idx->intern_detached_table_bytes;
	/*
	 * The intern_*_timing accumulators are published by the generated
	 * fan-out in cachetag_snapshot_counters(), not copied wholesale here.
	 */
	idx->counters.volatile_object_count_overflow_bytes =
	    idx->intern_overflow_sets * sizeof(struct cachetag_interned_set);
	idx->counters.index_memory_bytes = sizeof *idx + idx->namespace_len + 1 +
		object_bytes + count_bytes +
		side_bytes +
		idx->resize_detached_bytes +
		idx->counters.volatile_interned_set_bytes +
		idx->counters.volatile_interned_table_bytes +
		idx->counters.volatile_interned_detached_set_bytes +
		idx->counters.volatile_interned_detached_table_bytes +
		idx->counters.purgemap_bytes;
#else
	idx->counters.volatile_interned_sets = 0;
	idx->counters.volatile_interned_set_refs = 0;
	idx->counters.volatile_interned_set_hits = 0;
	idx->counters.volatile_interned_set_misses = 0;
	idx->counters.volatile_interned_set_bytes = 0;
	idx->counters.volatile_interned_table_bytes = 0;
	idx->counters.volatile_interned_migration_active = 0;
	idx->counters.volatile_interned_old_table_bytes = 0;
	idx->counters.volatile_interned_detached_set_bytes = 0;
	idx->counters.volatile_interned_detached_table_bytes = 0;
	idx->counters.volatile_interned_table_alloc_failures = 0;
	idx->counters.volatile_interned_table_grow_failures = 0;
	idx->counters.volatile_interned_candidate_discards = 0;
	/*
	 * The volatile_interned_* timing fields need no zeroing: their
	 * accumulators live on the index, this build never writes them, and
	 * the fields are zero from ALLOC_OBJ.
	 */
	idx->counters.index_memory_bytes = sizeof *idx + idx->namespace_len + 1 +
		object_bytes + count_bytes +
		side_bytes +
		idx->resize_detached_bytes +
		(idx->counters.volatile_edges -
		idx->counters.volatile_inline_folds) * sizeof(uint64_t) +
		idx->counters.volatile_object_count_overflow_bytes +
		idx->counters.purgemap_bytes;
#endif
}

enum cachetag_request_lock_category {
	TAG_REQUEST_LOCK_PROBE,
	TAG_REQUEST_LOCK_ATTACH,
	TAG_REQUEST_LOCK_INVALIDATE
};

static void
cachetag_note_request_wait_locked(struct cachetag_index *idx,
    enum cachetag_request_lock_category category, uint64_t wait_usec)
{
	struct cachetag_lockwait_counters *counters;

	switch (category) {
	case TAG_REQUEST_LOCK_PROBE:
		counters = &idx->lockwait_request_probe;
		break;
	case TAG_REQUEST_LOCK_ATTACH:
		counters = &idx->lockwait_request_attach;
		break;
	default:
		counters = &idx->lockwait_request_invalidate;
		break;
	}
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	counters->calls++;
	counters->wait_usec += wait_usec;
	if (wait_usec > counters->wait_max_usec)
		counters->wait_max_usec = wait_usec;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static void
cachetag_request_obj_lock(struct cachetag_index *idx,
    enum cachetag_request_lock_category category)
{
	uint64_t start, acquired;

	if (!idx->benchmark_obj_mtx_timing) {
		PTOK(pthread_mutex_lock(&idx->obj_mtx));
		return;
	}
	start = cachetag_now_usec();
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	acquired = cachetag_now_usec();
	cachetag_note_request_wait_locked(idx, category,
	    cachetag_elapsed_usec(start, acquired));
}

static void
cachetag_note_resize(struct cachetag_index *idx,
    struct cachetag_resize_counters *counters, size_t old_capacity,
    size_t new_capacity, uint64_t usec, int failed, int compact_active)
{
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	counters->calls++;
	counters->usec += usec;
	if (usec > counters->max_usec)
		counters->max_usec = usec;
	counters->failures += failed != 0;
	counters->compact_active_calls += compact_active != 0;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static void
cachetag_note_side_rehash(struct cachetag_index *idx, size_t old_capacity,
    size_t new_capacity, uint64_t usec, int failed)
{
	if (new_capacity < old_capacity) {
		cachetag_note_resize(idx, &idx->resize_side_shrink_rehash,
		    old_capacity, new_capacity, usec, failed, idx->sweep_active);
	} else {
		cachetag_note_resize(idx, &idx->resize_side_grow_rehash,
		    old_capacity, new_capacity, usec, failed, idx->sweep_active);
	}
}

static void
cachetag_note_record_shrink(struct cachetag_index *idx, uint64_t wait_usec,
    uint64_t hold_usec)
{
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.record_shrink_calls++;
	idx->counters.record_shrink_obj_mtx_wait_usec += wait_usec;
	if (wait_usec > idx->counters.record_shrink_obj_mtx_wait_max_usec)
		idx->counters.record_shrink_obj_mtx_wait_max_usec = wait_usec;
	idx->counters.record_shrink_obj_mtx_hold_usec += hold_usec;
	idx->counters.record_shrink_obj_mtx_hold_last_usec = hold_usec;
	if (hold_usec > idx->counters.record_shrink_obj_mtx_hold_max_usec)
		idx->counters.record_shrink_obj_mtx_hold_max_usec = hold_usec;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static void
cachetag_note_resize_batch_lock(struct cachetag_index *idx, uint64_t wait_usec,
    uint64_t hold_usec)
{

	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.resize_batch_obj_mtx_wait_usec += wait_usec;
	idx->counters.resize_batch_obj_mtx_wait_last_usec = wait_usec;
	if (wait_usec > idx->counters.resize_batch_obj_mtx_wait_max_usec)
		idx->counters.resize_batch_obj_mtx_wait_max_usec = wait_usec;
	idx->counters.resize_batch_obj_mtx_hold_usec += hold_usec;
	idx->counters.resize_batch_obj_mtx_hold_last_usec = hold_usec;
	if (hold_usec > idx->counters.resize_batch_obj_mtx_hold_max_usec)
		idx->counters.resize_batch_obj_mtx_hold_max_usec = hold_usec;
	idx->counters.resize_batch_obj_mtx_hold_over_2ms += hold_usec > 2000;
	idx->counters.resize_batch_obj_mtx_hold_over_5ms += hold_usec > 5000;
	idx->counters.resize_batch_obj_mtx_hold_over_10ms += hold_usec > 10000;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static void
cachetag_note_object_segment_alloc(struct cachetag_index *idx, uint64_t usec,
    int failed)
{

	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.object_segment_alloc_usec += usec;
	idx->counters.object_segment_alloc_last_usec = usec;
	if (usec > idx->counters.object_segment_alloc_max_usec)
		idx->counters.object_segment_alloc_max_usec = usec;
	idx->counters.object_segment_alloc_failures += failed != 0;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static void
cachetag_note_object_segment_free(struct cachetag_index *idx, uint64_t usec)
{

	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.object_segment_free_usec += usec;
	idx->counters.object_segment_free_last_usec = usec;
	if (usec > idx->counters.object_segment_free_max_usec)
		idx->counters.object_segment_free_max_usec = usec;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static void
cachetag_note_side_destination_alloc(struct cachetag_index *idx, uint64_t usec,
    int failed)
{

	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.side_destination_alloc_usec += usec;
	idx->counters.side_destination_alloc_last_usec = usec;
	if (usec > idx->counters.side_destination_alloc_max_usec)
		idx->counters.side_destination_alloc_max_usec = usec;
	idx->counters.side_destination_alloc_failures += failed != 0;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static void
cachetag_note_side_retired_free(struct cachetag_index *idx, uint64_t usec)
{

	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.side_retired_free_usec += usec;
	idx->counters.side_retired_free_last_usec = usec;
	if (usec > idx->counters.side_retired_free_max_usec)
		idx->counters.side_retired_free_max_usec = usec;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static void
cachetag_untrack_detached_bytes(struct cachetag_index *idx, size_t bytes)
{

	if (bytes == 0)
		return;
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	assert(idx->resize_detached_bytes >= bytes);
	idx->resize_detached_bytes -= bytes;
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
}

static uint64_t
cachetag_side_hash(const struct objcore *oc)
{
	uint64_t x = (uint64_t)(uintptr_t)oc;
	/* SplitMix64 finalizer: pointer alignment must not collapse the probe. */
	x ^= x >> 30;
	x *= UINT64_C(0xbf58476d1ce4e5b9);
	x ^= x >> 27;
	x *= UINT64_C(0x94d049bb133111eb);
	return (x ^ (x >> 31));
}

static uint32_t
cachetag_side_fingerprint(const struct cachetag_index *idx, uint64_t hash)
{
	uint32_t fingerprint;
	unsigned bits;

	bits = idx->test_side_fingerprint_bits;
	assert(bits <= 32);
	fingerprint = (uint32_t)(hash >> 32);
	if (bits == 32)
		return (fingerprint);
	if (bits == 0)
		return (0);
	return (fingerprint & (UINT32_MAX >> (32U - bits)));
}

static size_t
cachetag_side_table_bucket_count(const struct cachetag_index *idx)
{

	return (idx->side_primary.buckets + idx->side_retiring.buckets);
}

static size_t
cachetag_side_table_bytes(const struct cachetag_index *idx)
{

	return (cachetag_side_table_bucket_count(idx) *
	    sizeof(struct cachetag_side_bucket));
}

static void
cachetag_side_table_clear(struct cachetag_side_table *table)
{

	memset(table, 0, sizeof *table);
}

static int
cachetag_side_table_find_locked(struct cachetag_index *idx,
    struct cachetag_side_table *table, const struct objcore *oc,
    struct cachetag_side_location *loc)
{
	const struct cachetag_objent *ent;
	struct cachetag_side_bucket *b;
	enum cachetag_side_bucket_state state;
	uint32_t fingerprint;
	uint64_t hash;
	size_t at, n, slot;

	if (table->buckets == 0 || table->map == NULL)
		return (0);
	if ((table->buckets & (table->buckets - 1)) != 0) {
		return (-1);
	}
	hash = cachetag_side_hash(oc);
	fingerprint = cachetag_side_fingerprint(idx, hash);
	at = (size_t)(hash & (table->buckets - 1));
	for (n = 0; n < table->buckets; n++) {
		b = &table->map[at];
		state = cachetag_side_bucket_state(b);
		if (state == TAG_SIDE_EMPTY)
			return (0);
		if (state == TAG_SIDE_LIVE && b->fingerprint == fingerprint) {
			if (!cachetag_side_slot_decode(b->slot_code, &slot) ||
			    slot >= idx->nobjects)
				return (-1);
			ent = cachetag_object_at(idx, slot);
			if (ent->oc != oc) {
				if (ent->oc == NULL)
					return (-1);
				goto next;
			}
			if (loc != NULL) {
				loc->table = table;
				loc->bucket = b;
				loc->slot = slot;
			}
			return (1);
		}
next:
		at = (at + 1) & (table->buckets - 1);
	}
	return (-1);
}

/* Return 1 when found, 0 when absent, and -1 on invariant failure. */
static int
cachetag_side_find_locked(struct cachetag_index *idx, const struct objcore *oc,
    struct cachetag_side_location *loc)
{
	struct cachetag_side_location primary, retiring;
	int r;

	if (loc != NULL)
		memset(loc, 0, sizeof *loc);
	if (oc == NULL)
		return (0);
	if (idx->nobjects != idx->side_primary.live + idx->side_retiring.live) {
		return (-1);
	}
	r = cachetag_side_table_find_locked(idx, &idx->side_primary, oc,
	    &primary);
	if (r != 0)
		goto found_or_error;
	r = cachetag_side_table_find_locked(idx, &idx->side_retiring, oc,
	    &retiring);
	if (r <= 0)
		return (r);
	if (loc != NULL)
		*loc = retiring;
	return (1);
found_or_error:
	if (r < 0)
		return (r);
	if (idx->side_retiring.map != NULL &&
	    cachetag_side_table_find_locked(idx, &idx->side_retiring, oc,
	    &retiring) != 0) {
		return (-1);
	}
	if (loc != NULL)
		*loc = primary;
	return (1);
}

static int
cachetag_side_table_insert_locked(struct cachetag_index *idx,
    struct cachetag_side_table *table, struct objcore *oc, size_t slot)
{
	const struct cachetag_objent *ent;
	struct cachetag_side_bucket *b;
	enum cachetag_side_bucket_state state;
	uint32_t fingerprint, slot_code;
	uint64_t hash;
	size_t decoded;
	size_t at, n, first_tomb = SIZE_MAX;

	if (table->buckets == 0 || table->map == NULL)
		return (EFAULT);
	if (!cachetag_side_slot_encode(slot, &slot_code))
		return (EOVERFLOW);
	hash = cachetag_side_hash(oc);
	fingerprint = cachetag_side_fingerprint(idx, hash);
	at = (size_t)(hash & (table->buckets - 1));
	for (n = 0; n < table->buckets; n++) {
		b = &table->map[at];
		state = cachetag_side_bucket_state(b);
		if (state == TAG_SIDE_LIVE && b->fingerprint == fingerprint) {
			if (!cachetag_side_slot_decode(b->slot_code, &decoded) ||
			    decoded >= idx->nobjects)
				return (EFAULT);
			ent = cachetag_object_at(idx, decoded);
			if (ent->oc == oc)
				return (EEXIST);
			if (ent->oc == NULL)
				return (EFAULT);
		}
		if (state == TAG_SIDE_TOMBSTONE && first_tomb == SIZE_MAX)
			first_tomb = at;
		if (state == TAG_SIDE_EMPTY) {
			if (first_tomb != SIZE_MAX)
				at = first_tomb;
			b = &table->map[at];
			if (cachetag_side_bucket_state(b) == TAG_SIDE_TOMBSTONE)
				table->tombstones--;
			b->fingerprint = fingerprint;
			b->slot_code = slot_code;
			table->live++;
			return (0);
		}
		at = (at + 1) & (table->buckets - 1);
	}
	return (EFAULT);
}

static void
cachetag_side_table_tombstone_locked(struct cachetag_side_table *table,
    struct cachetag_side_bucket *bucket)
{

	assert(cachetag_side_bucket_state(bucket) == TAG_SIDE_LIVE);
	assert(table->live > 0);
	bucket->fingerprint = 0;
	bucket->slot_code = TAG_SIDE_SLOT_TOMBSTONE;
	table->live--;
	table->tombstones++;
}

static int
cachetag_side_migrate_some_locked(struct cachetag_index *idx,
    size_t max_inspected, struct cachetag_side_bucket **detached)
{
	const struct cachetag_objent *ent;
	struct cachetag_side_bucket *b;
	enum cachetag_side_bucket_state state;
	size_t slot;
	size_t inspected;
	int r;

	if (detached != NULL)
		*detached = NULL;
	if (!idx->side_migration_active)
		return (0);
	if (idx->side_retiring.map == NULL || idx->side_retiring.buckets == 0 ||
	    idx->side_primary.map == NULL || idx->side_primary.buckets == 0)
		return (EFAULT);
	if (max_inspected == 0)
		return (0);
	for (inspected = 0; inspected < max_inspected &&
	    idx->side_migrate_cursor < idx->side_retiring.buckets;
	    inspected++, idx->side_migrate_cursor++) {
		b = &idx->side_retiring.map[idx->side_migrate_cursor];
		state = cachetag_side_bucket_state(b);
		if (state != TAG_SIDE_LIVE)
			continue;
		if (!cachetag_side_slot_decode(b->slot_code, &slot) ||
		    slot >= idx->nobjects)
			return (EFAULT);
		ent = cachetag_object_at(idx, slot);
		if (ent->oc == NULL)
			return (EFAULT);
		r = cachetag_side_table_insert_locked(idx, &idx->side_primary,
		    ent->oc, slot);
		if (r != 0)
			return (r);
		cachetag_side_table_tombstone_locked(&idx->side_retiring, b);
	}
	if (idx->side_migrate_cursor == idx->side_retiring.buckets) {
		if (idx->side_retiring.live != 0)
			return (EFAULT);
		if (detached != NULL)
			*detached = idx->side_retiring.map;
		cachetag_side_table_clear(&idx->side_retiring);
		idx->side_migrate_cursor = 0;
		idx->side_migration_active = 0;
		idx->side_migration_reason = TAG_SIDE_MIGRATION_NONE;
		idx->side_migration_auto = 0;
		cachetag_counter_add(idx,
		    &idx->counters.side_migration_completions, 1);
	}
	return (0);
}

static int
cachetag_side_publish_migration_locked(struct cachetag_index *idx,
    struct cachetag_side_bucket *map, size_t buckets, unsigned reason,
    int count_growth)
{
	struct cachetag_side_table retiring;
	size_t old_buckets;

	if (map == NULL || buckets == 0 || (buckets & (buckets - 1)) != 0)
		return (EINVAL);
	if (idx->side_migration_active)
		return (EBUSY);
	old_buckets = idx->side_primary.buckets;
	retiring = idx->side_primary;
	idx->side_primary.map = map;
	idx->side_primary.buckets = buckets;
	idx->side_primary.tombstones = 0;
	idx->side_primary.live = 0;
	if (retiring.map != NULL && retiring.buckets != 0) {
		idx->side_retiring = retiring;
		idx->side_migrate_cursor = 0;
		idx->side_migration_active = 1;
		idx->side_migration_reason = reason;
		idx->side_migration_auto = 0;
	} else {
		cachetag_side_table_clear(&idx->side_retiring);
		idx->side_migrate_cursor = 0;
		idx->side_migration_active = 0;
		idx->side_migration_reason = TAG_SIDE_MIGRATION_NONE;
		idx->side_migration_auto = 0;
	}
	if (count_growth && buckets > old_buckets)
		cachetag_counter_add(idx, &idx->counters.volatile_side_table_grows,
		    1);
	if (reason == TAG_SIDE_MIGRATION_GROW)
		cachetag_counter_add(idx,
		    &idx->counters.side_resize_grow_publishes, 1);
	else if (reason == TAG_SIDE_MIGRATION_REBUILD)
		cachetag_counter_add(idx,
		    &idx->counters.side_resize_rebuild_publishes, 1);
	else if (reason == TAG_SIDE_MIGRATION_SHRINK)
		cachetag_counter_add(idx,
		    &idx->counters.side_resize_shrink_publishes, 1);
	return (0);
}

static int
cachetag_side_rollback_shrink_locked(struct cachetag_index *idx)
{
	struct cachetag_side_table small;

	if (!idx->side_migration_active ||
	    idx->side_migration_reason != TAG_SIDE_MIGRATION_SHRINK ||
	    idx->side_retiring.map == NULL || idx->side_retiring.buckets == 0)
		return (0);
	small = idx->side_primary;
	idx->side_primary = idx->side_retiring;
	idx->side_retiring = small;
	idx->side_migrate_cursor = 0;
	idx->side_migration_reason = TAG_SIDE_MIGRATION_ROLLBACK;
	idx->side_migration_auto = 1;
	cachetag_counter_add(idx,
	    &idx->counters.side_resize_shrink_rollbacks, 1);
	return (1);
}

static int
cachetag_side_prepare_insert_locked(struct cachetag_index *idx,
    size_t *bucketsp, unsigned *reasonp, struct cachetag_side_bucket **detached)
{
	size_t occupied, buckets;

	if (bucketsp != NULL)
		*bucketsp = 0;
	if (reasonp != NULL)
		*reasonp = TAG_SIDE_MIGRATION_NONE;
	if (detached != NULL)
		*detached = NULL;
	if (idx->side_primary.buckets == 0) {
		if (bucketsp != NULL)
			*bucketsp = TAG_SIDE_DEFAULT_BUCKETS;
		if (reasonp != NULL)
			*reasonp = TAG_SIDE_MIGRATION_GROW;
		return (EAGAIN);
	}
again:
	occupied = idx->side_primary.live + idx->side_primary.tombstones;
	/* Keep at least one empty bucket and leave probe chains short. */
	if (occupied + 1 <= idx->side_primary.buckets *
	    TAG_SIDE_HARD_GROW_LOAD_NUMERATOR /
	    TAG_SIDE_HARD_GROW_LOAD_DENOMINATOR)
		return (0);
	if (idx->side_migration_active) {
		if (cachetag_side_rollback_shrink_locked(idx))
			goto again;
		return (EBUSY);
	}
	if (idx->side_primary.tombstones > idx->side_primary.buckets / 4 &&
	    idx->side_primary.live + 1 <= idx->side_primary.buckets *
	    TAG_SIDE_HARD_GROW_LOAD_NUMERATOR /
	    TAG_SIDE_HARD_GROW_LOAD_DENOMINATOR) {
		buckets = idx->side_primary.buckets;
		if (reasonp != NULL)
			*reasonp = TAG_SIDE_MIGRATION_REBUILD;
	} else {
		if (idx->side_primary.buckets > SIZE_MAX / 2)
			return (EOVERFLOW);
		buckets = idx->side_primary.buckets * 2;
		if (reasonp != NULL)
			*reasonp = TAG_SIDE_MIGRATION_GROW;
	}
	if (bucketsp != NULL)
		*bucketsp = buckets;
	return (EAGAIN);
}

static void
cachetag_side_detach_all_locked(struct cachetag_index *idx,
    struct cachetag_side_bucket **primary, struct cachetag_side_bucket **retiring)
{

	if (primary != NULL)
		*primary = idx->side_primary.map;
	if (retiring != NULL)
		*retiring = idx->side_retiring.map;
	cachetag_side_table_clear(&idx->side_primary);
	cachetag_side_table_clear(&idx->side_retiring);
	idx->side_migrate_cursor = 0;
	idx->side_migration_active = 0;
	idx->side_migration_reason = TAG_SIDE_MIGRATION_NONE;
	idx->side_migration_auto = 0;
}

static struct cachetag_side_bucket *
cachetag_side_alloc_map(struct cachetag_index *idx, size_t buckets)
{

	if (__atomic_exchange_n(&idx->test_fail_next_side_migration_alloc, 0,
	    __ATOMIC_ACQ_REL))
		return (NULL);
	return (calloc(buckets, sizeof(struct cachetag_side_bucket)));
}

static int
cachetag_side_publish_allocated_locked(struct cachetag_index *idx,
    struct cachetag_side_bucket *map, size_t buckets, unsigned reason)
{

	if (idx->side_migration_active)
		return (EBUSY);
	if (reason == TAG_SIDE_MIGRATION_GROW &&
	    idx->side_primary.buckets != 0 &&
	    buckets <= idx->side_primary.buckets)
		return (ESTALE);
	if (reason == TAG_SIDE_MIGRATION_REBUILD &&
	    buckets != idx->side_primary.buckets)
		return (ESTALE);
	if (reason == TAG_SIDE_MIGRATION_SHRINK &&
	    buckets >= idx->side_primary.buckets)
		return (ESTALE);
	return (cachetag_side_publish_migration_locked(idx, map, buckets, reason,
	    reason == TAG_SIDE_MIGRATION_GROW));
}

static int
cachetag_side_publish_allocated_for_insert_locked(struct cachetag_index *idx,
    struct cachetag_side_bucket *map, size_t buckets, unsigned reason)
{
	size_t occupied;
	int r;

	if (idx->side_primary.buckets == 0) {
		if (buckets != TAG_SIDE_DEFAULT_BUCKETS)
			return (ESTALE);
		r = cachetag_side_publish_migration_locked(idx, map, buckets,
		    reason, 1);
		if (r == 0)
			idx->side_migration_auto = idx->side_migration_active;
		return (r);
	}
	if (idx->side_migration_active)
		return (EBUSY);
	occupied = idx->side_primary.live + idx->side_primary.tombstones;
	if (occupied + 1 <= idx->side_primary.buckets *
	    TAG_SIDE_HARD_GROW_LOAD_NUMERATOR /
	    TAG_SIDE_HARD_GROW_LOAD_DENOMINATOR)
		return (ESTALE);
	if (reason == TAG_SIDE_MIGRATION_REBUILD) {
		if (buckets != idx->side_primary.buckets)
			return (ESTALE);
	} else if (reason == TAG_SIDE_MIGRATION_GROW) {
		if (buckets != idx->side_primary.buckets * 2)
			return (ESTALE);
	} else
		return (EINVAL);
	r = cachetag_side_publish_migration_locked(idx, map, buckets, reason,
	    reason == TAG_SIDE_MIGRATION_GROW);
	if (r == 0)
		idx->side_migration_auto = idx->side_migration_active;
	return (r);
}

static int cachetag_side_soft_resize_locked(struct cachetag_index *, size_t *,
    unsigned *);

static size_t
cachetag_object_growth_runway(size_t capacity)
{
	size_t runway;

	runway = capacity / TAG_OBJECT_GROW_RUNWAY_DIVISOR;
	if (runway > TAG_OBJECT_GROW_RUNWAY_ABSOLUTE)
		runway = TAG_OBJECT_GROW_RUNWAY_ABSOLUTE;
	return (runway);
}

static size_t
cachetag_side_soft_grow_limit(size_t buckets)
{
	size_t hard_limit, soft_limit;

	hard_limit = buckets * TAG_SIDE_HARD_GROW_LOAD_NUMERATOR /
	    TAG_SIDE_HARD_GROW_LOAD_DENOMINATOR;
	soft_limit = buckets * TAG_SIDE_SOFT_GROW_LOAD_NUMERATOR /
	    TAG_SIDE_SOFT_GROW_LOAD_DENOMINATOR;
	if (buckets < TAG_SIDE_DEFAULT_BUCKETS)
		return (soft_limit);
	if (hard_limit <= TAG_SIDE_GROW_MIN_RUNWAY)
		return (1);
	if (soft_limit > hard_limit - TAG_SIDE_GROW_MIN_RUNWAY)
		soft_limit = hard_limit - TAG_SIDE_GROW_MIN_RUNWAY;
	return (soft_limit);
}

static int
cachetag_object_soft_growth_locked(struct cachetag_index *idx,
    unsigned *segment_indexp, size_t *old_capacityp, size_t *new_capacityp,
    size_t *segment_slotsp)
{
	unsigned segment_index;
	size_t capobjects;

	if (idx->capobjects == 0)
		return (0);
	assert(cachetag_object_growth_runway(idx->capobjects) <=
	    idx->capobjects);
	if (idx->nobjects < idx->capobjects -
	    cachetag_object_growth_runway(idx->capobjects))
		return (0);
	segment_index = cachetag_object_segment_count_for_capacity(
	    idx->capobjects);
	if (segment_index >= TAG_OBJECT_SEGMENTS)
		return (0);
	if (idx->object_segments[segment_index] != NULL)
		return (0);
	capobjects = cachetag_object_capacity_for_segments(segment_index + 1);
	if (segment_indexp != NULL)
		*segment_indexp = segment_index;
	if (old_capacityp != NULL)
		*old_capacityp = idx->capobjects;
	if (new_capacityp != NULL)
		*new_capacityp = capobjects;
	if (segment_slotsp != NULL)
		*segment_slotsp = cachetag_object_segment_slots(segment_index);
	return (1);
}

static size_t
cachetag_object_low_water_target_locked(const struct cachetag_index *idx)
{
	size_t cap;

	if (idx->nobjects == 0)
		return (0);
	if (idx->nobjects > idx->capobjects / 4)
		return (idx->capobjects);
	cap = TAG_OBJECT_SEGMENT0_SLOTS;
	while (cap < idx->nobjects * 2)
		cap *= 2;
	return (cap);
}

static size_t
cachetag_side_low_water_target_locked(const struct cachetag_index *idx)
{
	size_t buckets;

	if (idx->nobjects == 0)
		return (0);
	if (idx->side_primary.buckets == 0 || idx->side_migration_active)
		return (idx->side_primary.buckets);
	buckets = TAG_SIDE_DEFAULT_BUCKETS;
	while (idx->nobjects > buckets / 4) {
		if (buckets > SIZE_MAX / 2)
			return (idx->side_primary.buckets);
		buckets *= 2;
	}
	return (buckets);
}

static int
cachetag_low_water_overrun_locked(const struct cachetag_index *idx)
{
	size_t tolerance, ceiling;

	if (!idx->resize_low_water_active)
		return (0);
	if (idx->resize_low_water_live == 0)
		return (idx->nobjects != 0);
	tolerance = idx->resize_low_water_live /
	    TAG_RESIZE_LOW_WATER_GROW_TOLERANCE_DIVISOR;
	if (tolerance == 0)
		tolerance = 1;
	ceiling = idx->resize_low_water_live + tolerance;
	return (idx->nobjects > ceiling);
}

static void
cachetag_low_water_cancel_locked(struct cachetag_index *idx, unsigned reason)
{

	if (idx->resize_low_water_active &&
	    reason != TAG_RESIZE_LOW_WATER_CANCEL_NONE) {
		PTOK(pthread_mutex_lock(&idx->counter_mtx));
		idx->counters.resize_low_water_cancellations++;
		if (reason == TAG_RESIZE_LOW_WATER_CANCEL_REFILL_OVERRUN)
			idx->counters.side_resize_shrink_cancellations++;
		PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	}
	idx->resize_low_water_active = 0;
	idx->resize_low_water_start_usec = 0;
	idx->resize_low_water_live = 0;
	idx->resize_low_water_force = 0;
}

static void
cachetag_low_water_start_locked(struct cachetag_index *idx, uint64_t start_usec,
    unsigned rearm)
{

	idx->resize_low_water_active = 1;
	idx->resize_low_water_start_usec = start_usec;
	idx->resize_low_water_live = idx->nobjects;
	idx->resize_low_water_force = 0;
	idx->resize_low_water_rearm_at_usec = 0;
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	if (rearm)
		idx->counters.resize_low_water_rearms++;
	else
		idx->counters.resize_low_water_starts++;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
}

static uint64_t
cachetag_low_water_rearm_locked(struct cachetag_index *idx, uint64_t now)
{

	idx->resize_low_water_rearm_at_usec =
	    now + TAG_RESIZE_LOW_WATER_REARM_USEC;
	return (idx->resize_low_water_rearm_at_usec);
}

static uint64_t
cachetag_low_water_wake_at_locked(const struct cachetag_index *idx)
{

	if (idx->resize_low_water_active && !idx->resize_low_water_force)
		return (idx->resize_low_water_start_usec +
		    TAG_RESIZE_LOW_WATER_OBSERVE_USEC);
	if (!idx->resize_low_water_active &&
	    idx->resize_low_water_rearm_at_usec != 0)
		return (idx->resize_low_water_rearm_at_usec);
	return (0);
}

static uint64_t
cachetag_low_water_wake_at(struct cachetag_index *idx)
{
	uint64_t wake_at;

	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	wake_at = cachetag_low_water_wake_at_locked(idx);
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	return (wake_at);
}

static int
cachetag_low_water_ready_locked(struct cachetag_index *idx)
{
	uint64_t now;
	size_t object_target, side_target;
	unsigned side_reason;

	if (!idx->resize_low_water_active)
		return (0);
	if (cachetag_low_water_overrun_locked(idx)) {
		now = cachetag_now_usec();
		cachetag_low_water_cancel_locked(idx,
		    TAG_RESIZE_LOW_WATER_CANCEL_REFILL_OVERRUN);
		(void)cachetag_low_water_rearm_locked(idx, now);
		return (0);
	}
	if (cachetag_object_soft_growth_locked(idx, NULL, NULL, NULL, NULL)) {
		cachetag_low_water_cancel_locked(idx,
		    TAG_RESIZE_LOW_WATER_CANCEL_OBJECT_GROWTH);
		return (0);
	}
	side_reason = TAG_SIDE_MIGRATION_NONE;
	if (cachetag_side_soft_resize_locked(idx, NULL, &side_reason) &&
	    side_reason == TAG_SIDE_MIGRATION_GROW) {
		cachetag_low_water_cancel_locked(idx,
		    TAG_RESIZE_LOW_WATER_CANCEL_SIDE_GROWTH);
		return (0);
	}
	now = cachetag_now_usec();
	if (!idx->resize_low_water_force &&
	    cachetag_elapsed_usec(idx->resize_low_water_start_usec, now) <
	    TAG_RESIZE_LOW_WATER_OBSERVE_USEC)
		return (0);
	object_target = cachetag_object_low_water_target_locked(idx);
	side_target = cachetag_side_low_water_target_locked(idx);
	if ((idx->nobjects == 0 &&
	    (idx->capobjects != 0 ||
	    cachetag_side_table_bucket_count(idx) != 0)) ||
	    object_target < idx->capobjects ||
	    (!idx->side_migration_active &&
	    side_target < idx->side_primary.buckets))
		return (1);
	cachetag_low_water_cancel_locked(idx,
	    TAG_RESIZE_LOW_WATER_CANCEL_NO_RECLAIM);
	return (0);
}

static int
cachetag_low_water_promote_rearm(struct cachetag_index *idx)
{
	uint64_t now;
	int promoted;

	promoted = 0;
	now = cachetag_now_usec();
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	if (!idx->resize_low_water_active &&
	    idx->resize_low_water_rearm_at_usec != 0 &&
	    idx->resize_low_water_rearm_at_usec <= now) {
		cachetag_low_water_start_locked(idx, now, 1);
		promoted = 1;
	}
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	return (promoted);
}

static int
cachetag_side_soft_resize_locked(struct cachetag_index *idx, size_t *bucketsp,
    unsigned *reasonp)
{
	size_t occupied;

	if (idx->side_migration_active || idx->side_primary.buckets == 0)
		return (0);
	if (idx->side_primary.tombstones > idx->side_primary.buckets / 4 &&
	    idx->side_primary.live <= idx->side_primary.buckets *
	    TAG_SIDE_HARD_GROW_LOAD_NUMERATOR /
	    TAG_SIDE_HARD_GROW_LOAD_DENOMINATOR) {
		if (bucketsp != NULL)
			*bucketsp = idx->side_primary.buckets;
		if (reasonp != NULL)
			*reasonp = TAG_SIDE_MIGRATION_REBUILD;
		return (1);
	}
	occupied = idx->side_primary.live + idx->side_primary.tombstones;
	if (occupied <
	    cachetag_side_soft_grow_limit(idx->side_primary.buckets))
		return (0);
	if (idx->side_primary.buckets > SIZE_MAX / 2)
		return (0);
	if (bucketsp != NULL)
		*bucketsp = idx->side_primary.buckets * 2;
	if (reasonp != NULL)
		*reasonp = TAG_SIDE_MIGRATION_GROW;
	return (1);
}

static int
cachetag_resize_needs_work_locked(struct cachetag_index *idx)
{

#if CACHE_TAG_SET_INTERNING
	if (idx->intern_migration_active)
		return (1);
#endif
	if (idx->side_migration_active && idx->side_migration_auto)
		return (1);
	if (cachetag_object_soft_growth_locked(idx, NULL, NULL, NULL, NULL))
		return (1);
	if (cachetag_low_water_ready_locked(idx))
		return (1);
	return (cachetag_side_soft_resize_locked(idx, NULL, NULL));
}

#if CACHE_TAG_SET_INTERNING
static int
cachetag_resize_migrate_intern_batch(struct cachetag_index *idx)
{
	struct cachetag_intern_cleanup cleanup;
	uint64_t migrate_started;
	int did;

	memset(&cleanup, 0, sizeof cleanup);
	did = 0;
	PTOK(pthread_mutex_lock(&idx->sweep_mtx));
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	if (idx->intern_migration_active && !idx->test_intern_worker_hold) {
		migrate_started = idx->benchmark_obj_mtx_timing ?
		    cachetag_now_usec() : 0;
		cachetag_intern_migrate_locked(idx, TAG_RESIZE_INTERN_BATCH_STEPS,
		    &cleanup);
		if (idx->benchmark_obj_mtx_timing)
			cachetag_note_intern_timing(idx,
			    &idx->intern_table_grow_timing,
			    cachetag_elapsed_usec(migrate_started,
			    cachetag_now_usec()));
		did = 1;
	}
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	PTOK(pthread_mutex_unlock(&idx->sweep_mtx));
	cachetag_intern_cleanup_free(idx, &cleanup);
	return (did);
}
#endif

#if CACHE_TAG_SET_INTERNING
static void
cachetag_intern_attach_cleanup(struct cachetag_index *idx,
    struct cachetag_intern_cleanup *cleanup,
    struct cachetag_interned_set *candidate, int wake_if_migrating)
{
	int resize_wake;

	cleanup->unpublished = candidate;
	cachetag_intern_cleanup_free(idx, cleanup);
	resize_wake = 0;
	if (wake_if_migrating) {
		PTOK(pthread_mutex_lock(&idx->obj_mtx));
		resize_wake = idx->intern_migration_active != 0;
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	}
	if (resize_wake)
		cachetag_resize_wake(idx);
}
#endif

void
cachetag_resize_wake(struct cachetag_index *idx)
{

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	idx->resize_wakeup = 1;
	idx->resize_wakeup_at_usec = 0;
	PTOK(pthread_cond_signal(&idx->sweep_cond));
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
}

void
cachetag_resize_wake_at(struct cachetag_index *idx, uint64_t wake_at_usec)
{
	uint64_t now;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	now = cachetag_now_usec();
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	if (wake_at_usec <= now) {
		idx->resize_wakeup = 1;
		idx->resize_wakeup_at_usec = 0;
	} else if (idx->resize_wakeup_at_usec == 0 ||
	    wake_at_usec < idx->resize_wakeup_at_usec) {
		idx->resize_wakeup_at_usec = wake_at_usec;
	}
	PTOK(pthread_cond_signal(&idx->sweep_cond));
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
}

static void
cachetag_resize_broadcast(struct cachetag_index *idx)
{

	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	PTOK(pthread_cond_broadcast(&idx->sweep_cond));
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
}

static int
cachetag_resize_publish_object_segment(struct cachetag_index *idx)
{
	struct cachetag_objent *segment;
	size_t cap, old_capacity;
	uint64_t lock_acquired, lock_released, lock_started;
	uint64_t resize_started, resize_usec;
	unsigned segment_index;
	int published;

	segment_index = 0;
	old_capacity = cap = 0;
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	if (!cachetag_object_soft_growth_locked(idx, &segment_index,
	    &old_capacity, &cap, NULL)) {
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		return (0);
	}
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	if (!cachetag_object_segment_allocation_bytes(segment_index, NULL)) {
		cachetag_note_resize(idx, &idx->resize_object_grow,
		    old_capacity, cap, 0, 1, 0);
		return (0);
	}
	resize_started = cachetag_now_usec();
	segment = cachetag_object_segment_alloc(idx, segment_index);
	resize_usec = cachetag_elapsed_usec(resize_started, cachetag_now_usec());
	cachetag_note_object_segment_alloc(idx, resize_usec, segment == NULL);
	if (segment == NULL) {
		cachetag_note_resize(idx, &idx->resize_object_grow,
		    old_capacity, cap, resize_usec, 1, 0);
		return (0);
	}
	published = 0;
	PTOK(pthread_mutex_lock(&idx->sweep_mtx));
	lock_started = cachetag_now_usec();
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	lock_acquired = cachetag_now_usec();
	if (idx->capobjects == old_capacity &&
	    idx->object_segments[segment_index] == NULL) {
		idx->object_segments[segment_index] = segment;
		idx->capobjects = cap;
		segment = NULL;
		published = 1;
		cachetag_note_resize(idx, &idx->resize_object_grow,
		    old_capacity, cap, resize_usec, 0, idx->sweep_active);
		cachetag_counter_add(idx,
		    &idx->counters.object_segment_grow_publishes, 1);
	}
	lock_released = cachetag_now_usec();
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	PTOK(pthread_mutex_unlock(&idx->sweep_mtx));
	cachetag_note_resize_batch_lock(idx,
	    cachetag_elapsed_usec(lock_started, lock_acquired),
	    cachetag_elapsed_usec(lock_acquired, lock_released));
	free(segment);
	return (published);
}

static int
cachetag_resize_publish_side_destination(struct cachetag_index *idx)
{
	struct cachetag_side_bucket *map;
	size_t buckets, old_buckets, current_buckets;
	uint64_t lock_acquired, lock_released, lock_started;
	uint64_t resize_started, resize_usec;
	unsigned reason, current_reason;
	int published, r;

	buckets = old_buckets = 0;
	reason = TAG_SIDE_MIGRATION_NONE;
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	if (!cachetag_side_soft_resize_locked(idx, &buckets, &reason)) {
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		return (0);
	}
	old_buckets = idx->side_primary.buckets;
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	resize_started = cachetag_now_usec();
	map = cachetag_side_alloc_map(idx, buckets);
	resize_usec = cachetag_elapsed_usec(resize_started, cachetag_now_usec());
	cachetag_note_side_destination_alloc(idx, resize_usec, map == NULL);
	if (map == NULL) {
		cachetag_note_side_rehash(idx, old_buckets, buckets,
		    resize_usec, 1);
		return (0);
	}
	published = 0;
	PTOK(pthread_mutex_lock(&idx->sweep_mtx));
	lock_started = cachetag_now_usec();
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	lock_acquired = cachetag_now_usec();
	current_buckets = 0;
	current_reason = TAG_SIDE_MIGRATION_NONE;
	if (cachetag_side_soft_resize_locked(idx, &current_buckets,
	    &current_reason) && current_buckets == buckets &&
	    current_reason == reason) {
		r = cachetag_side_publish_allocated_locked(idx, map, buckets,
		    reason);
		if (r == 0) {
			map = NULL;
			idx->side_migration_auto = idx->side_migration_active;
			published = 1;
			cachetag_note_side_rehash(idx, old_buckets, buckets,
			    resize_usec, 0);
		}
	}
	lock_released = cachetag_now_usec();
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	PTOK(pthread_mutex_unlock(&idx->sweep_mtx));
	cachetag_note_resize_batch_lock(idx,
	    cachetag_elapsed_usec(lock_started, lock_acquired),
	    cachetag_elapsed_usec(lock_acquired, lock_released));
	free(map);
	return (published);
}

static int
cachetag_resize_migrate_side_batch(struct cachetag_index *idx)
{
	struct cachetag_side_bucket *detached;
	size_t before_cursor, before_live, before_retiring_buckets;
	size_t detached_bytes, inspected, moved;
	uint64_t free_started, free_usec;
	uint64_t lock_acquired, lock_released, lock_started;
	int locked_batch, more, r;

	detached = NULL;
	detached_bytes = 0;
	locked_batch = 0;
	more = 0;
	PTOK(pthread_mutex_lock(&idx->sweep_mtx));
	lock_started = cachetag_now_usec();
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	lock_acquired = cachetag_now_usec();
	if (idx->side_migration_active && idx->side_migration_auto) {
		locked_batch = 1;
		if (idx->side_migration_reason == TAG_SIDE_MIGRATION_SHRINK &&
		    (cachetag_low_water_overrun_locked(idx) ||
		    cachetag_side_soft_resize_locked(idx, NULL, NULL)))
			(void)cachetag_side_rollback_shrink_locked(idx);
		before_cursor = idx->side_migrate_cursor;
		before_live = idx->side_retiring.live;
		before_retiring_buckets = idx->side_retiring.buckets;
		r = cachetag_side_migrate_some_locked(idx,
		    TAG_RESIZE_SIDE_BATCH_BUCKETS, &detached);
		if (r == 0) {
			more = idx->side_migration_active != 0;
			if (detached != NULL)
				inspected = before_retiring_buckets - before_cursor;
			else
				inspected = idx->side_migrate_cursor >= before_cursor ?
				    idx->side_migrate_cursor - before_cursor :
				    before_retiring_buckets - before_cursor;
			moved = before_live >= idx->side_retiring.live ?
			    before_live - idx->side_retiring.live : 0;
			if (detached != NULL)
				detached_bytes = before_retiring_buckets *
				    sizeof(struct cachetag_side_bucket);
			PTOK(pthread_mutex_lock(&idx->counter_mtx));
			idx->counters.side_migration_batches++;
			idx->counters.side_migration_inspected_buckets += inspected;
			idx->counters.side_migration_moved_entries += moved;
			idx->resize_detached_bytes += detached_bytes;
			PTOK(pthread_mutex_unlock(&idx->counter_mtx));
		} else {
			idx->side_migration_auto = 0;
		}
	}
	lock_released = cachetag_now_usec();
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	PTOK(pthread_mutex_unlock(&idx->sweep_mtx));
	if (locked_batch && lock_released >= lock_acquired)
		cachetag_note_resize_batch_lock(idx,
		    cachetag_elapsed_usec(lock_started, lock_acquired),
		    cachetag_elapsed_usec(lock_acquired, lock_released));
	free_started = cachetag_now_usec();
	free(detached);
	free_usec = cachetag_elapsed_usec(free_started, cachetag_now_usec());
	if (detached != NULL) {
		cachetag_note_side_retired_free(idx, free_usec);
		cachetag_untrack_detached_bytes(idx, detached_bytes);
	}
	return (more || detached != NULL);
}

static int
cachetag_resize_apply_low_water(struct cachetag_index *idx)
{
	struct cachetag_objent *detached_segments[TAG_OBJECT_SEGMENTS];
	struct cachetag_side_bucket *detached_primary, *detached_retiring, *map;
	size_t buckets, current_buckets, object_target, old_capacity;
	size_t detached_bytes, object_detached_bytes, side_detached_bytes;
	uint64_t free_started, free_usec;
	uint64_t lock_acquired, lock_released, lock_started;
	uint64_t resize_started, resize_usec;
	unsigned ndetached_segments;
	int did, r;

	memset(detached_segments, 0, sizeof detached_segments);
	detached_primary = NULL;
	detached_retiring = NULL;
	map = NULL;
	detached_bytes = 0;
	ndetached_segments = 0;
	did = 0;
	PTOK(pthread_mutex_lock(&idx->sweep_mtx));
	lock_started = cachetag_now_usec();
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	lock_acquired = cachetag_now_usec();
	if (!cachetag_low_water_ready_locked(idx)) {
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		PTOK(pthread_mutex_unlock(&idx->sweep_mtx));
		return (0);
	}
	if (idx->nobjects == 0 &&
	    (idx->capobjects != 0 ||
	    cachetag_side_table_bucket_count(idx) != 0)) {
		old_capacity = idx->capobjects +
		    cachetag_side_table_bucket_count(idx);
		object_detached_bytes = cachetag_object_storage_bytes(idx);
		side_detached_bytes = cachetag_side_table_bucket_count(idx) *
		    sizeof(struct cachetag_side_bucket);
		detached_bytes = object_detached_bytes + side_detached_bytes;
		resize_started = cachetag_now_usec();
		ndetached_segments =
		    cachetag_object_detach_segments_locked(idx, 0,
		    detached_segments);
		cachetag_side_detach_all_locked(idx, &detached_primary,
		    &detached_retiring);
		cachetag_low_water_cancel_locked(idx,
		    TAG_RESIZE_LOW_WATER_CANCEL_NONE);
		PTOK(pthread_mutex_lock(&idx->counter_mtx));
		idx->counters.volatile_object_table_shrinks++;
		idx->counters.volatile_side_table_shrinks++;
		if (ndetached_segments != 0)
			idx->counters.object_segment_detach_batches++;
		idx->resize_detached_bytes += detached_bytes;
		PTOK(pthread_mutex_unlock(&idx->counter_mtx));
		cachetag_note_resize(idx, &idx->resize_zero_container_free,
		    old_capacity, 0,
		    cachetag_elapsed_usec(resize_started, cachetag_now_usec()),
		    0, idx->sweep_active);
		did = 1;
		lock_released = cachetag_now_usec();
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		PTOK(pthread_mutex_unlock(&idx->sweep_mtx));
		cachetag_note_resize_batch_lock(idx,
		    cachetag_elapsed_usec(lock_started, lock_acquired),
		    cachetag_elapsed_usec(lock_acquired, lock_released));
		free_started = cachetag_now_usec();
		free(detached_primary);
		free(detached_retiring);
		free_usec = cachetag_elapsed_usec(free_started,
		    cachetag_now_usec());
		if (side_detached_bytes != 0)
			cachetag_note_side_retired_free(idx, free_usec);
		free_started = cachetag_now_usec();
		cachetag_object_free_segments(detached_segments,
		    ndetached_segments);
		free_usec = cachetag_elapsed_usec(free_started,
		    cachetag_now_usec());
		if (object_detached_bytes != 0)
			cachetag_note_object_segment_free(idx, free_usec);
		cachetag_untrack_detached_bytes(idx, detached_bytes);
		return (did);
	}
	object_target = cachetag_object_low_water_target_locked(idx);
	if (object_target < idx->capobjects) {
		old_capacity = idx->capobjects;
		object_detached_bytes = (old_capacity - object_target) *
		    (sizeof(struct cachetag_objent) + sizeof(uint8_t));
		detached_bytes = object_detached_bytes;
		resize_started = cachetag_now_usec();
		ndetached_segments =
		    cachetag_object_detach_segments_locked(idx, object_target,
		    detached_segments);
		cachetag_note_resize(idx, &idx->resize_object_shrink,
		    old_capacity, object_target,
		    cachetag_elapsed_usec(resize_started, cachetag_now_usec()),
		    0, idx->sweep_active);
		cachetag_counter_add(idx,
		    &idx->counters.volatile_object_table_shrinks, 1);
		PTOK(pthread_mutex_lock(&idx->counter_mtx));
		if (ndetached_segments != 0)
			idx->counters.object_segment_detach_batches++;
		idx->resize_detached_bytes += detached_bytes;
		PTOK(pthread_mutex_unlock(&idx->counter_mtx));
		did = 1;
	}
	if (did) {
		lock_released = cachetag_now_usec();
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		PTOK(pthread_mutex_unlock(&idx->sweep_mtx));
		cachetag_note_resize_batch_lock(idx,
		    cachetag_elapsed_usec(lock_started, lock_acquired),
		    cachetag_elapsed_usec(lock_acquired, lock_released));
		free_started = cachetag_now_usec();
		cachetag_object_free_segments(detached_segments,
		    ndetached_segments);
		free_usec = cachetag_elapsed_usec(free_started,
		    cachetag_now_usec());
		if (detached_bytes != 0)
			cachetag_note_object_segment_free(idx, free_usec);
		cachetag_untrack_detached_bytes(idx, detached_bytes);
		return (did);
	}
	buckets = cachetag_side_low_water_target_locked(idx);
	if (buckets >= idx->side_primary.buckets) {
		cachetag_low_water_cancel_locked(idx,
		    TAG_RESIZE_LOW_WATER_CANCEL_NO_RECLAIM);
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		PTOK(pthread_mutex_unlock(&idx->sweep_mtx));
		return (0);
	}
	current_buckets = idx->side_primary.buckets;
	lock_released = cachetag_now_usec();
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	PTOK(pthread_mutex_unlock(&idx->sweep_mtx));

	resize_started = cachetag_now_usec();
	map = cachetag_side_alloc_map(idx, buckets);
	resize_usec = cachetag_elapsed_usec(resize_started, cachetag_now_usec());
	cachetag_note_side_destination_alloc(idx, resize_usec, map == NULL);
	if (map == NULL) {
		cachetag_note_side_rehash(idx, current_buckets, buckets,
		    resize_usec, 1);
		PTOK(pthread_mutex_lock(&idx->obj_mtx));
		cachetag_low_water_cancel_locked(idx,
		    TAG_RESIZE_LOW_WATER_CANCEL_ALLOC_FAILURE);
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		return (0);
	}

	PTOK(pthread_mutex_lock(&idx->sweep_mtx));
	lock_started = cachetag_now_usec();
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	lock_acquired = cachetag_now_usec();
	if (cachetag_low_water_ready_locked(idx) &&
	    !idx->side_migration_active &&
	    cachetag_side_low_water_target_locked(idx) == buckets &&
	    idx->side_primary.buckets == current_buckets) {
		r = cachetag_side_publish_allocated_locked(idx, map, buckets,
		    TAG_SIDE_MIGRATION_SHRINK);
		if (r == 0) {
			map = NULL;
			idx->side_migration_auto = idx->side_migration_active;
			cachetag_note_side_rehash(idx, current_buckets, buckets,
			    resize_usec, 0);
			cachetag_counter_add(idx,
			    &idx->counters.volatile_side_table_shrinks, 1);
			did = 1;
		}
	}
	lock_released = cachetag_now_usec();
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	PTOK(pthread_mutex_unlock(&idx->sweep_mtx));
	cachetag_note_resize_batch_lock(idx,
	    cachetag_elapsed_usec(lock_started, lock_acquired),
	    cachetag_elapsed_usec(lock_acquired, lock_released));
	free(map);
	return (did);
}

int
cachetag_resize_maintenance(struct cachetag_index *idx)
{
	int did;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	(void)cachetag_low_water_promote_rearm(idx);
	#if CACHE_TAG_SET_INTERNING
	did = cachetag_resize_migrate_intern_batch(idx);
	#else
	did = 0;
	#endif
	if (!did)
		did = cachetag_resize_migrate_side_batch(idx);
	if (!did)
		did = cachetag_resize_publish_object_segment(idx);
	if (!did)
		did = cachetag_resize_apply_low_water(idx);
	if (!did)
		did = cachetag_resize_publish_side_destination(idx);
	if (did)
		cachetag_resize_broadcast(idx);
	else {
		uint64_t wake_at;

		wake_at = cachetag_low_water_wake_at(idx);
		if (wake_at != 0)
			cachetag_resize_wake_at(idx, wake_at);
	}
	return (did);
}

/* Remove one dense slot and repair the swapped object's map entry. */
static int
cachetag_side_remove_locked(struct cachetag_index *idx, size_t slot)
{
	struct cachetag_side_location loc, moved_loc;
	struct cachetag_objent *dst, *src;
	struct objcore *oc, *moved_oc;
	int found;
	size_t boundary, last, remaining;

	if (slot >= idx->nobjects)
		return (EFAULT);
	last = idx->nobjects - 1;
	oc = cachetag_object_at(idx, slot)->oc;
	found = cachetag_side_find_locked(idx, oc, &loc);
	if (found <= 0)
		return (EFAULT);
	if (!idx->sweep_active || slot >= idx->sweep_remaining) {
		if (slot != last) {
			moved_oc = cachetag_object_at(idx, last)->oc;
			found = cachetag_side_find_locked(idx, moved_oc, &moved_loc);
			if (found <= 0 ||
			    moved_loc.bucket == loc.bucket)
				return (EFAULT);
			if (moved_loc.slot != last)
				return (EFAULT);
			dst = cachetag_object_at(idx, slot);
			src = cachetag_object_at(idx, last);
			*dst = *src;
			*cachetag_object_count_at(idx, slot) =
			    *cachetag_object_count_at(idx, last);
			if (!cachetag_side_slot_encode(slot,
			    &moved_loc.bucket->slot_code))
				return (EFAULT);
		}
		goto removed;
	}
	remaining = idx->sweep_remaining;
	if (remaining == 0 || remaining > idx->nobjects)
		return (EFAULT);
	boundary = remaining - 1;
	if (slot != boundary) {
		moved_oc = cachetag_object_at(idx, boundary)->oc;
		found = cachetag_side_find_locked(idx, moved_oc, &moved_loc);
		if (found <= 0 || moved_loc.bucket == loc.bucket)
			return (EFAULT);
		if (moved_loc.slot != boundary)
			return (EFAULT);
		dst = cachetag_object_at(idx, slot);
		src = cachetag_object_at(idx, boundary);
		*dst = *src;
		*cachetag_object_count_at(idx, slot) =
		    *cachetag_object_count_at(idx, boundary);
		if (!cachetag_side_slot_encode(slot,
		    &moved_loc.bucket->slot_code))
			return (EFAULT);
	}
	if (boundary != last) {
		moved_oc = cachetag_object_at(idx, last)->oc;
		found = cachetag_side_find_locked(idx, moved_oc, &moved_loc);
		if (found <= 0 || moved_loc.bucket == loc.bucket)
			return (EFAULT);
		if (moved_loc.slot != last)
			return (EFAULT);
		dst = cachetag_object_at(idx, boundary);
		src = cachetag_object_at(idx, last);
		*dst = *src;
		*cachetag_object_count_at(idx, boundary) =
		    *cachetag_object_count_at(idx, last);
		if (!cachetag_side_slot_encode(boundary,
		    &moved_loc.bucket->slot_code))
			return (EFAULT);
	}
	idx->sweep_remaining--;
removed:
	cachetag_side_table_tombstone_locked(loc.table, loc.bucket);
	*cachetag_object_count_at(idx, last) = TAG_OBJCOUNT_INVALID;
	idx->nobjects--;
	return (0);
}

int
cachetag_record_attach_purgemap_take(struct cachetag_index *idx,
    struct objcore *oc, void *fold_storage, uint64_t *fold_values,
    unsigned nfolds, uint64_t reg_seq, enum cachetag_purge_mode *modep)
{
	struct cachetag_side_bucket *allocated_side_map, *detached_side_map;
	struct cachetag_objent *ent, *segment;
	struct cachetag_side_location loc;
#if CACHE_TAG_SET_INTERNING
	struct cachetag_intern_cleanup cleanup;
	struct cachetag_interned_set *candidate, *set;
	struct cachetag_interned_set **allocated_intern_buckets;
#endif
	struct cachetag_purgemap *pm;
	const uint64_t *folds;
#if CACHE_TAG_SET_INTERNING
	uint64_t *scratch;
#endif
	size_t cap, old_capacity, old_side_buckets, side_buckets;
	uint64_t low_water_wake_at, resize_started, resize_usec;
#if CACHE_TAG_SET_INTERNING
	size_t intern_buckets;
	uint64_t acquire_started, intern_generation, intern_hash, migrate_started;
#endif
	unsigned segment_index, side_reason;
	int found, r, resize_wake;
#if CACHE_TAG_SET_INTERNING
	int intern_grow_failed, intern_migrate_budget_used;
#endif

	if (modep != NULL)
		*modep = (enum cachetag_purge_mode)-1;
	if (nfolds == 0 || nfolds > idx->limits.max_keys_per_object) {
#if CACHE_TAG_SET_INTERNING
		cachetag_fold_storage_free(fold_storage, nfolds);
#endif
		return (E2BIG);
	}
#if CACHE_TAG_SET_INTERNING
	memset(&cleanup, 0, sizeof cleanup);
	allocated_intern_buckets = NULL;
	candidate = NULL;
	intern_hash = 0;
	if (nfolds == 1) {
		folds = fold_values;
	} else {
		/* Canonicalize borrowed folds before obj_mtx. */
		candidate = fold_storage;
		scratch = fold_values;
		if (scratch == NULL) {
			cachetag_fold_storage_free(candidate, nfolds);
			return (EINVAL);
		}
		cachetag_intern_sort(scratch, nfolds);
		intern_hash = cachetag_intern_hash(scratch, nfolds);
		if (candidate != NULL)
			candidate->hash = intern_hash;
		folds = scratch;
	}
#else
	folds = fold_values;
#endif
	if (folds == NULL)
		return (EINVAL);
	segment = NULL;
	allocated_side_map = NULL;
	detached_side_map = NULL;
	resize_wake = 0;
	#if CACHE_TAG_SET_INTERNING
	intern_grow_failed = 0;
	intern_migrate_budget_used = 0;
	#endif
	low_water_wake_at = 0;
	cachetag_request_obj_lock(idx, TAG_REQUEST_LOCK_ATTACH);
again:
	if (__atomic_exchange_n(&idx->test_force_next_attach_slot_overflow,
	    0, __ATOMIC_ACQ_REL) || idx->nobjects >= TAG_SIDE_MAX_OBJECTS) {
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	#if CACHE_TAG_SET_INTERNING
		cachetag_intern_attach_cleanup(idx, &cleanup, candidate,
		    1);
	#endif
		return (EOVERFLOW);
	}
	found = cachetag_side_find_locked(idx, oc, &loc);
	if (found < 0) {
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	#if CACHE_TAG_SET_INTERNING
		cachetag_intern_attach_cleanup(idx, &cleanup, candidate,
		    1);
	#endif
		return (EFAULT);
	}
	if (found > 0) {
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	#if CACHE_TAG_SET_INTERNING
		cachetag_intern_attach_cleanup(idx, &cleanup, candidate,
		    1);
	#endif
		return (EEXIST);
	}
	if (idx->nobjects == idx->capobjects) {
		old_capacity = idx->capobjects;
		segment_index =
		    cachetag_object_segment_count_for_capacity(old_capacity);
		if (segment_index >= TAG_OBJECT_SEGMENTS) {
			cachetag_note_resize(idx, &idx->resize_object_grow,
			    idx->capobjects, idx->capobjects, 0, 1,
			    idx->sweep_active);
			PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		#if CACHE_TAG_SET_INTERNING
			cachetag_intern_attach_cleanup(idx, &cleanup, candidate,
			    1);
		#endif
			return (EOVERFLOW);
		}
		cap = cachetag_object_capacity_for_segments(segment_index + 1);
		if (!cachetag_object_segment_allocation_bytes(segment_index,
		    NULL)) {
			cachetag_note_resize(idx, &idx->resize_object_grow,
			    idx->capobjects, cap, 0, 1, idx->sweep_active);
			PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		#if CACHE_TAG_SET_INTERNING
			cachetag_intern_attach_cleanup(idx, &cleanup, candidate,
			    1);
		#endif
			return (EOVERFLOW);
		}
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		resize_started = cachetag_now_usec();
		segment = cachetag_object_segment_alloc(idx, segment_index);
		resize_usec = cachetag_elapsed_usec(resize_started,
		    cachetag_now_usec());
		cachetag_note_object_segment_alloc(idx, resize_usec,
		    segment == NULL);
		if (segment == NULL) {
			cachetag_note_resize(idx, &idx->resize_object_grow,
			    old_capacity, cap, resize_usec, 1, 0);
		#if CACHE_TAG_SET_INTERNING
			cachetag_intern_attach_cleanup(idx, &cleanup, candidate,
			    1);
		#endif
			return (ENOMEM);
		}
		PTOK(pthread_mutex_lock(&idx->obj_mtx));
		if (idx->capobjects == old_capacity &&
		    idx->object_segments[segment_index] == NULL) {
			idx->object_segments[segment_index] = segment;
			idx->capobjects = cap;
			segment = NULL;
			cachetag_note_resize(idx, &idx->resize_object_grow,
			    old_capacity, cap, resize_usec, 0,
			    idx->sweep_active);
			cachetag_note_object_emergency_segment(idx, old_capacity);
			cachetag_counter_add(idx,
			    &idx->counters.object_segment_grow_publishes, 1);
			goto again;
		}
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		free(segment);
		segment = NULL;
		PTOK(pthread_mutex_lock(&idx->obj_mtx));
		goto again;
	}
	r = cachetag_side_prepare_insert_locked(idx, &side_buckets,
	    &side_reason, &detached_side_map);
	if (r == EAGAIN) {
		old_side_buckets = idx->side_primary.buckets;
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		free(detached_side_map);
		detached_side_map = NULL;
		resize_started = cachetag_now_usec();
		allocated_side_map = cachetag_side_alloc_map(idx, side_buckets);
		resize_usec = cachetag_elapsed_usec(resize_started,
		    cachetag_now_usec());
		cachetag_note_side_destination_alloc(idx, resize_usec,
		    allocated_side_map == NULL);
		if (allocated_side_map == NULL) {
			cachetag_note_side_rehash(idx, old_side_buckets,
			    side_buckets, resize_usec, 1);
		#if CACHE_TAG_SET_INTERNING
			cachetag_intern_attach_cleanup(idx, &cleanup, candidate,
			    1);
		#endif
			return (ENOMEM);
		}
		PTOK(pthread_mutex_lock(&idx->obj_mtx));
		r = cachetag_side_publish_allocated_for_insert_locked(idx,
		    allocated_side_map, side_buckets, side_reason);
		if (r == 0) {
			allocated_side_map = NULL;
			cachetag_note_side_rehash(idx, old_side_buckets,
			    side_buckets, resize_usec, 0);
			if (side_reason == TAG_SIDE_MIGRATION_GROW)
				cachetag_note_attach_side_grow(idx, old_side_buckets);
			goto again;
		}
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		free(allocated_side_map);
		allocated_side_map = NULL;
		PTOK(pthread_mutex_lock(&idx->obj_mtx));
		goto again;
	}
	if (r != 0) {
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		free(detached_side_map);
	#if CACHE_TAG_SET_INTERNING
		cachetag_intern_attach_cleanup(idx, &cleanup, candidate,
		    1);
	#endif
		return (r);
	}
#if CACHE_TAG_SET_INTERNING
	/*
	 * Allocation and population happened before obj_mtx.  This part only
	 * publishes an already prepared table/candidate, or advances a bounded
	 * migration step.
	 */
	acquire_started = idx->benchmark_obj_mtx_timing ? cachetag_now_usec() : 0;
	if (!intern_migrate_budget_used && idx->intern_migration_active) {
		migrate_started = idx->benchmark_obj_mtx_timing ?
		    cachetag_now_usec() : 0;
		cachetag_intern_migrate_locked(idx, TAG_INTERN_MIGRATE_STEPS,
		    &cleanup);
		intern_migrate_budget_used = 1;
		if (idx->benchmark_obj_mtx_timing)
			cachetag_note_intern_timing(idx,
			    &idx->intern_table_grow_timing,
			    cachetag_elapsed_usec(migrate_started,
			    cachetag_now_usec()));
	}
	set = nfolds > 1 ? cachetag_intern_lookup_locked(idx, folds, nfolds,
	    intern_hash) : NULL;
	if (nfolds > 1 && set == NULL) {
		r = intern_grow_failed ? 0 :
		    cachetag_intern_prepare_insert_locked(idx, &intern_buckets);
		if (r == EAGAIN) {
			intern_generation = idx->intern_generation;
			PTOK(pthread_mutex_unlock(&idx->obj_mtx));
			resize_started = idx->benchmark_obj_mtx_timing ?
			    cachetag_now_usec() : 0;
			allocated_intern_buckets = cachetag_intern_alloc_buckets(idx,
			    intern_buckets);
			cachetag_note_intern_table_alloc(idx,
			    idx->benchmark_obj_mtx_timing ? cachetag_elapsed_usec(
			    resize_started, cachetag_now_usec()) : 0,
			    allocated_intern_buckets == NULL);
			if (allocated_intern_buckets == NULL) {
				PTOK(pthread_mutex_lock(&idx->obj_mtx));
				if (idx->intern_buckets == NULL) {
					PTOK(pthread_mutex_unlock(&idx->obj_mtx));
					free(detached_side_map);
					cachetag_intern_attach_cleanup(idx, &cleanup,
					    candidate, 1);
					return (ENOMEM);
				}
				PTOK(pthread_mutex_lock(&idx->counter_mtx));
				idx->counters.volatile_interned_table_grow_failures++;
				PTOK(pthread_mutex_unlock(&idx->counter_mtx));
				intern_grow_failed = 1;
				PTOK(pthread_mutex_unlock(&idx->obj_mtx));
				PTOK(pthread_mutex_lock(&idx->obj_mtx));
				goto again;
			}
			PTOK(pthread_mutex_lock(&idx->obj_mtx));
			r = cachetag_intern_publish_table_locked(idx,
			    allocated_intern_buckets, intern_buckets,
			    intern_generation);
			if (r == 0) {
				allocated_intern_buckets = NULL;
				resize_wake = 1;
				goto again;
			}
			PTOK(pthread_mutex_unlock(&idx->obj_mtx));
			free(allocated_intern_buckets);
			allocated_intern_buckets = NULL;
			PTOK(pthread_mutex_lock(&idx->obj_mtx));
			goto again;
		}
		if (r != 0 || idx->intern_buckets == NULL) {
			PTOK(pthread_mutex_unlock(&idx->obj_mtx));
			free(detached_side_map);
			cachetag_intern_attach_cleanup(idx, &cleanup, candidate,
			    1);
			return (r != 0 ? r : ENOMEM);
		}
		if (candidate == NULL) {
			PTOK(pthread_mutex_unlock(&idx->obj_mtx));
			candidate = cachetag_intern_candidate_alloc(idx, nfolds);
			if (candidate == NULL) {
				free(detached_side_map);
				cachetag_intern_attach_cleanup(idx, &cleanup, NULL, 1);
				return (ENOMEM);
			}
			scratch = cachetag_fold_storage_values(candidate, nfolds);
			AN(scratch);
			memcpy(scratch, folds, (size_t)nfolds * sizeof *scratch);
			candidate->hash = intern_hash;
			folds = scratch;
			PTOK(pthread_mutex_lock(&idx->obj_mtx));
			goto again;
		}
		set = cachetag_intern_publish_locked(idx, candidate);
		candidate = NULL;
	}
	if (idx->benchmark_obj_mtx_timing)
		cachetag_note_intern_timing(idx, &idx->intern_acquire_timing,
		    cachetag_elapsed_usec(acquire_started, cachetag_now_usec()));
#endif
	ent = cachetag_object_at(idx, idx->nobjects);
	ent->oc = oc;
	ent->reg_seq = reg_seq;
	if (nfolds == 1) {
		ent->membership.inline_one = folds[0];
	} else {
#if CACHE_TAG_SET_INTERNING
		ent->membership.set = set;
#else
		ent->membership.vector = fold_storage;
#endif
	}
	*cachetag_object_count_at(idx, idx->nobjects) =
	    nfolds <= TAG_OBJCOUNT_DIRECT_MAX ? (uint8_t)nfolds :
	    TAG_OBJCOUNT_OVERFLOW;
	r = cachetag_side_table_insert_locked(idx, &idx->side_primary, oc,
	    idx->nobjects);
	if (r != 0) {
#if CACHE_TAG_SET_INTERNING
		if (nfolds > 1)
			cachetag_intern_release_locked(idx, ent->membership.set, &cleanup);
#endif
		*cachetag_object_count_at(idx, idx->nobjects) =
		    TAG_OBJCOUNT_INVALID;
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		free(detached_side_map);
	#if CACHE_TAG_SET_INTERNING
		cachetag_intern_attach_cleanup(idx, &cleanup, candidate,
		    1);
	#endif
		return (r);
	}
	idx->nobjects++;
	if (idx->sweep_active)
		assert(idx->sweep_remaining <= idx->nobjects);
	if (cachetag_low_water_overrun_locked(idx)) {
		resize_started = cachetag_now_usec();
		cachetag_low_water_cancel_locked(idx,
		    TAG_RESIZE_LOW_WATER_CANCEL_REFILL_OVERRUN);
		(void)cachetag_low_water_rearm_locked(idx, resize_started);
		low_water_wake_at = cachetag_low_water_wake_at_locked(idx);
	} else if (!idx->resize_low_water_active &&
	    idx->resize_low_water_rearm_at_usec != 0) {
		resize_started = cachetag_now_usec();
		(void)cachetag_low_water_rearm_locked(idx, resize_started);
		low_water_wake_at = cachetag_low_water_wake_at_locked(idx);
	}
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.volatile_edges += nfolds;
	idx->counters.volatile_inline_folds += nfolds == 1;
#if !CACHE_TAG_SET_INTERNING
	idx->counters.volatile_object_count_overflow_bytes +=
	    nfolds >= TAG_OBJCOUNT_OVERFLOW ?
	    sizeof(struct cachetag_fold_storage_header) : 0;
#endif
	idx->counters.volatile_attached++;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	pm = cachetag_purgemap_data(idx);
	AN(pm);
	switch (cachetag_purgemap_probe_reader_guarded(pm, reg_seq,
	    cachetag_objent_folds(idx, idx->nobjects - 1), nfolds)) {
	case TAG_PM_PROBE_HARD:
		cachetag_counter_add(idx,
		    &idx->counters.purgemap_insert_probe_hits, 1);
		if (modep != NULL)
			*modep = TAG_PURGE_HARD;
		break;
	case TAG_PM_PROBE_SOFT:
		cachetag_counter_add(idx,
		    &idx->counters.purgemap_insert_probe_hits, 1);
		if (modep != NULL)
			*modep = TAG_PURGE_SOFT;
		break;
	default:
		break;
	}
	resize_wake = cachetag_resize_needs_work_locked(idx);
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	free(detached_side_map);
#if CACHE_TAG_SET_INTERNING
	cachetag_intern_attach_cleanup(idx, &cleanup, candidate, 0);
#endif
	if (resize_wake)
		cachetag_resize_wake(idx);
	else if (low_water_wake_at != 0)
		cachetag_resize_wake_at(idx, low_water_wake_at);
	return (0);
}

void
cachetag_record_invalidate(struct cachetag_index *idx, struct objcore *oc)
{
	struct cachetag_side_location loc;
	int found;
#if CACHE_TAG_SET_INTERNING
	struct cachetag_intern_cleanup cleanup;
	struct cachetag_interned_set *set;
#else
	void *fold_storage;
#endif
	unsigned nfolds;
	struct cachetag_objent *ent;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	if (oc == NULL)
		return;
#if CACHE_TAG_SET_INTERNING
	memset(&cleanup, 0, sizeof cleanup);
#endif
	cachetag_request_obj_lock(idx, TAG_REQUEST_LOCK_INVALIDATE);
	found = cachetag_side_find_locked(idx, oc, &loc);
	if (found < 0) {
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		return; /* fail closed on a corrupted side-map invariant */
	}
	if (found > 0) {
		/* The bucket stores the dense slot, not an exported handle. */
		ent = cachetag_object_at(idx, loc.slot);
		nfolds = cachetag_objent_nfolds(idx, loc.slot);
		if (nfolds == 0) {
			PTOK(pthread_mutex_unlock(&idx->obj_mtx));
			return;
		}
		/* Capture before swap-remove relocates another entry into this slot. */
#if CACHE_TAG_SET_INTERNING
		set = nfolds > 1 ? ent->membership.set : NULL;
#else
		fold_storage = nfolds > 1 ? ent->membership.vector : NULL;
#endif
		if (cachetag_side_remove_locked(idx, loc.slot) != 0) {
			PTOK(pthread_mutex_unlock(&idx->obj_mtx));
			return; /* fail closed on a corrupted side-map invariant */
		}
#if CACHE_TAG_SET_INTERNING
		cachetag_intern_migrate_locked(idx, TAG_INTERN_MIGRATE_STEPS,
		    &cleanup);
		if (set != NULL)
			cachetag_intern_release_locked(idx, set, &cleanup);
#else
		cachetag_fold_storage_free(fold_storage, nfolds);
#endif
		PTOK(pthread_mutex_lock(&idx->counter_mtx));
		assert(idx->counters.volatile_edges >= nfolds);
		idx->counters.volatile_edges -= nfolds;
		idx->counters.volatile_inline_folds -= nfolds == 1;
#if !CACHE_TAG_SET_INTERNING
		idx->counters.volatile_object_count_overflow_bytes -=
		    nfolds >= TAG_OBJCOUNT_OVERFLOW ?
		    sizeof(struct cachetag_fold_storage_header) : 0;
#endif
		PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	}
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
#if CACHE_TAG_SET_INTERNING
	cachetag_intern_cleanup_free(idx, &cleanup);
#endif
}

void
cachetag_record_shrink(struct cachetag_index *idx)
{
	uint64_t lock_started, lock_acquired, lock_ended, wake_at;
	int wake_now;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	wake_at = 0;
	wake_now = 0;
	lock_started = cachetag_now_usec();
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	lock_acquired = cachetag_now_usec();
	if (idx->sweep_active) {
		PTOK(pthread_mutex_lock(&idx->counter_mtx));
		idx->counters.sweep_deferred_shrinks++;
		PTOK(pthread_mutex_unlock(&idx->counter_mtx));
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		cachetag_note_record_shrink(idx,
		    cachetag_elapsed_usec(lock_started, lock_acquired),
		    cachetag_elapsed_usec(lock_acquired, cachetag_now_usec()));
		return;
	}
	if (!idx->resize_low_water_active) {
		cachetag_low_water_start_locked(idx, lock_acquired, 0);
	} else if (idx->nobjects < idx->resize_low_water_live) {
		idx->resize_low_water_live = idx->nobjects;
		PTOK(pthread_mutex_lock(&idx->counter_mtx));
		idx->counters.resize_low_water_restarts++;
		PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	}
	if (cachetag_low_water_overrun_locked(idx)) {
		cachetag_low_water_cancel_locked(idx,
		    TAG_RESIZE_LOW_WATER_CANCEL_REFILL_OVERRUN);
		wake_at = cachetag_low_water_rearm_locked(idx,
		    cachetag_now_usec());
	} else if (cachetag_low_water_ready_locked(idx)) {
		wake_now = 1;
	} else if (idx->resize_low_water_active) {
		wake_at = cachetag_low_water_wake_at_locked(idx);
	}
	lock_ended = cachetag_now_usec();
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	cachetag_note_record_shrink(idx,
	    cachetag_elapsed_usec(lock_started, lock_acquired),
	    cachetag_elapsed_usec(lock_acquired, lock_ended));
	if (wake_now)
		cachetag_resize_wake(idx);
	else if (wake_at != 0)
		cachetag_resize_wake_at(idx, wake_at);
}

enum cachetag_purgemap_probe_result
cachetag_record_probe_purgemap(struct cachetag_index *idx,
    const struct objcore *oc, int *foundp)
{
	struct cachetag_side_location loc;
	struct cachetag_purgemap *pm;
	int found;
	enum cachetag_purgemap_probe_result r = TAG_PM_PROBE_NONE;
	struct cachetag_objent *ent;
	const uint64_t *folds;
	unsigned nfolds;

	if (foundp != NULL)
		*foundp = 0;
	if (oc == NULL)
		return (r);
	pm = cachetag_purgemap_data(idx);
	AN(pm);
	cachetag_request_obj_lock(idx, TAG_REQUEST_LOCK_PROBE);
	found = cachetag_side_find_locked(idx, oc, &loc);
	if (found < 0) {
		if (foundp != NULL)
			*foundp = 1;
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		return (TAG_PM_PROBE_HARD);
	}
	if (found > 0) {
		if (foundp != NULL)
			*foundp = 1;
		ent = cachetag_object_at(idx, loc.slot);
		nfolds = cachetag_objent_nfolds(idx, loc.slot);
		folds = cachetag_objent_folds(idx, loc.slot);
		r = nfolds == 0 || folds == NULL ? TAG_PM_PROBE_HARD :
		    cachetag_purgemap_probe_reader_guarded(pm, ent->reg_seq,
		    folds, nfolds);
	}
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	return (r);
}

void
cachetag_record_sweep_purgemap(struct cachetag_index *idx,
    struct cachetag_sweep_observation *obs)
{
	struct cachetag_purgemap *pm;
	size_t batch_scanned, u;
	uint64_t batch_objects, batch_start, batch_usec, batch_usecs;
	uint64_t gap_start, lock_start, lock_acquired, lock_released;
	uint64_t op_start, op_usec, wait_usec, yield_usecs;
#if CACHE_TAG_SET_INTERNING
	struct cachetag_intern_cleanup cleanup;
#endif
	int done = 0;

	AN(obs);
	pm = cachetag_purgemap_data(idx);
	AN(pm);
	memset(obs, 0, sizeof *obs);
#if CACHE_TAG_SET_INTERNING
	memset(&cleanup, 0, sizeof cleanup);
#endif
	batch_objects = idx->limits.purgemap_sweep_batch_objects;
	if (batch_objects == 0)
		batch_objects = 1;
	batch_usecs = idx->limits.purgemap_sweep_batch_usec;
	if (batch_usecs == 0)
		batch_usecs = 1;
	yield_usecs = idx->limits.purgemap_sweep_batch_yield_usec;
	lock_start = cachetag_now_usec();
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	lock_acquired = cachetag_now_usec();
	wait_usec = cachetag_elapsed_usec(lock_start, lock_acquired);
	obs->obj_mtx_wait_usec += wait_usec;
	obs->obj_mtx_wait_max_usec = wait_usec;
	obs->objects_before = idx->nobjects;
	obs->object_slots_before = idx->capobjects;
	obs->object_bytes_before = cachetag_object_table_bytes(idx);
	obs->side_buckets_before = cachetag_side_table_bucket_count(idx);
	obs->side_bytes_before = cachetag_side_table_bytes(idx);
	if (__atomic_exchange_n(&idx->test_abort_next_sweep, 0,
		    __ATOMIC_ACQ_REL)) {
		obs->aborted = 1;
		goto out;
	}
	assert(!idx->sweep_active);
	idx->sweep_active = 1;
	idx->sweep_remaining = idx->nobjects;
	while (!done) {
		batch_scanned = 0;
		batch_start = cachetag_now_usec();
		obs->batches++;
		while (idx->sweep_remaining > 0) {
			enum cachetag_purgemap_probe_result r;
			struct cachetag_objent *ent;
			const uint64_t *folds;
#if CACHE_TAG_SET_INTERNING
			struct cachetag_interned_set *set;
#else
			void *fold_storage;
#endif
			struct objcore *oc;
			unsigned nfolds;

			if (__atomic_load_n(&idx->sweep_stop, __ATOMIC_ACQUIRE)) {
				obs->aborted = 1;
				break;
			}
			u = idx->sweep_remaining - 1;
			ent = cachetag_object_at(idx, u);
			op_start = cachetag_now_usec();
			nfolds = cachetag_objent_nfolds(idx, u);
			folds = cachetag_objent_folds(idx, u);
			r = nfolds == 0 || folds == NULL ? TAG_PM_PROBE_HARD :
			    cachetag_purgemap_probe_reader_guarded(pm, ent->reg_seq,
			    folds, nfolds);
			obs->scanned++;
			batch_scanned++;
			if (r == TAG_PM_PROBE_HARD) {
				oc = ent->oc;
				/* Capture before swap-remove relocates another entry. */
#if CACHE_TAG_SET_INTERNING
				set = nfolds > 1 ? ent->membership.set : NULL;
#else
				fold_storage = nfolds > 1 ?
				    ent->membership.vector : NULL;
#endif
				if (cachetag_side_remove_locked(idx, u) != 0) {
					obs->aborted = 1;
					break; /* fail closed: keep the object indexed */
				}
				HSH_Kill(oc);
#if CACHE_TAG_SET_INTERNING
				if (set != NULL)
					cachetag_intern_release_locked(idx, set, &cleanup);
#else
				cachetag_fold_storage_free(fold_storage, nfolds);
#endif
				PTOK(pthread_mutex_lock(&idx->counter_mtx));
				assert(idx->counters.volatile_edges >= nfolds);
				idx->counters.volatile_edges -= nfolds;
				idx->counters.volatile_inline_folds -= nfolds == 1;
#if !CACHE_TAG_SET_INTERNING
				idx->counters.volatile_object_count_overflow_bytes -=
				    nfolds >= TAG_OBJCOUNT_OVERFLOW ?
				    sizeof(struct cachetag_fold_storage_header) : 0;
#endif
				PTOK(pthread_mutex_unlock(&idx->counter_mtx));
				obs->killed++;
			} else {
				if (r == TAG_PM_PROBE_SOFT) {
					EXP_Reduce(ent->oc, VTIM_real(), 0,
					    NAN, NAN);
					obs->reduced++;
				}
				idx->sweep_remaining--;
			}
			op_usec = cachetag_elapsed_usec(op_start, cachetag_now_usec());
			if (op_usec > obs->per_object_max_usec)
				obs->per_object_max_usec = op_usec;
			if (batch_scanned > obs->batch_scanned_max)
				obs->batch_scanned_max = batch_scanned;
			if (idx->sweep_remaining == 0)
				break;
			if (batch_scanned >= batch_objects)
				break;
			batch_usec = cachetag_elapsed_usec(batch_start,
			    cachetag_now_usec());
			if (batch_usec >= batch_usecs)
				break;
		}
		if (obs->aborted || idx->sweep_remaining == 0)
			done = 1;
		obs->remaining = idx->sweep_remaining;
		PTOK(pthread_mutex_lock(&idx->counter_mtx));
		idx->counters.sweep_remaining = idx->sweep_remaining;
		cachetag_account_objects_locked(idx);
		PTOK(pthread_mutex_unlock(&idx->counter_mtx));
		lock_released = cachetag_now_usec();
		batch_usec = cachetag_elapsed_usec(lock_acquired, lock_released);
		obs->obj_mtx_hold_usec += batch_usec;
		if (batch_usec > obs->obj_mtx_hold_max_usec)
			obs->obj_mtx_hold_max_usec = batch_usec;
		obs->batch_hold_over_2ms += batch_usec > 2000;
		obs->batch_hold_over_5ms += batch_usec > 5000;
		obs->batch_hold_over_10ms += batch_usec > 10000;
		if (done)
			break;
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	#if CACHE_TAG_SET_INTERNING
		cachetag_intern_cleanup_free(idx, &cleanup);
	#endif
		gap_start = cachetag_now_usec();
		if (yield_usecs != 0)
			VTIM_sleep((double)yield_usecs / 1000000.0);
		lock_start = cachetag_now_usec();
		PTOK(pthread_mutex_lock(&idx->obj_mtx));
		lock_acquired = cachetag_now_usec();
		obs->unlocked_gap_usec += cachetag_elapsed_usec(gap_start,
		    lock_acquired);
		wait_usec = cachetag_elapsed_usec(lock_start, lock_acquired);
		obs->obj_mtx_wait_usec += wait_usec;
		if (wait_usec > obs->obj_mtx_wait_max_usec)
			obs->obj_mtx_wait_max_usec = wait_usec;
	}
out:
	if (obs->batches == 0) {
		lock_released = cachetag_now_usec();
		batch_usec = cachetag_elapsed_usec(lock_acquired, lock_released);
		obs->obj_mtx_hold_usec += batch_usec;
		obs->obj_mtx_hold_max_usec = batch_usec;
		obs->batch_hold_over_2ms += batch_usec > 2000;
		obs->batch_hold_over_5ms += batch_usec > 5000;
		obs->batch_hold_over_10ms += batch_usec > 10000;
	}
	if (idx->sweep_active) {
		idx->sweep_active = 0;
		idx->sweep_remaining = 0;
	}
	obs->objects_after = idx->nobjects;
	obs->object_slots_after = idx->capobjects;
	obs->object_bytes_after = cachetag_object_table_bytes(idx);
	obs->side_buckets_after = cachetag_side_table_bucket_count(idx);
	obs->side_bytes_after = cachetag_side_table_bytes(idx);
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.sweep_remaining = 0;
	cachetag_account_objects_locked(idx);
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
#if CACHE_TAG_SET_INTERNING
	cachetag_intern_cleanup_free(idx, &cleanup);
#endif
}

struct cachetag_index *
cachetag_index_new(const char *ns, const struct cachetag_limits *limits,
    const struct cachetag_persist_config *persist)
{
	struct cachetag_index *idx;

	if (ns == NULL || *ns == '\0')
		ns = "default";
	ALLOC_OBJ(idx, TAG_INDEX_MAGIC);
	if (idx == NULL)
		return (NULL);
	idx->namespace = strdup(ns);
	if (idx->namespace == NULL) {
		FREE_OBJ(idx);
		return (NULL);
	}
	idx->namespace_len = strlen(ns);
	idx->test_side_fingerprint_bits = 32;
	idx->benchmark_obj_mtx_timing =
	    getenv("CACHE_TAG_BENCH_INSTRUMENT_OBJ_MTX") != NULL &&
	    strcmp(getenv("CACHE_TAG_BENCH_INSTRUMENT_OBJ_MTX"), "0") != 0;
	idx->counters.index_memory_bytes = sizeof *idx + idx->namespace_len + 1;
	if (limits != NULL)
		idx->limits = *limits;
	else
		cachetag_limits_default(&idx->limits);
	if (persist != NULL && persist->path != NULL && *persist->path != '\0') {
		idx->wal = cachetag_wal_new(persist->path, ns, persist->wal_fsync,
		    persist->wal_segment_bytes);
		if (idx->wal == NULL) {
			free(idx->namespace);
			FREE_OBJ(idx);
			return (NULL);
		}
	}
	PTOK(pthread_mutex_init(&idx->obj_mtx, NULL));
	PTOK(pthread_mutex_init(&idx->counter_mtx, NULL));
	PTOK(pthread_mutex_init(&idx->purge_mtx, NULL));
	PTOK(pthread_cond_init(&idx->sweep_cond, NULL));
	PTOK(pthread_mutex_init(&idx->sweep_mtx, NULL));
	PTOK(pthread_mutex_init(&idx->replay_mtx, NULL));
	return (idx);
}

void
cachetag_index_delete(struct cachetag_index **idxp)
{
	struct cachetag_objent *detached_segments[TAG_OBJECT_SEGMENTS];
	struct cachetag_side_bucket *detached_primary, *detached_retiring;
	struct cachetag_index *idx;
	unsigned ndetached_segments;
	#if CACHE_TAG_SET_INTERNING
	struct cachetag_intern_cleanup cleanup;
	#endif
	#if !CACHE_TAG_SET_INTERNING
	size_t u;
	#endif

	TAKE_OBJ_NOTNULL(idx, idxp, TAG_INDEX_MAGIC);
	memset(detached_segments, 0, sizeof detached_segments);
	detached_primary = NULL;
	detached_retiring = NULL;
	ndetached_segments = 0;
	#if CACHE_TAG_SET_INTERNING
	memset(&cleanup, 0, sizeof cleanup);
	#endif
	cachetag_index_stop(idx);
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	#if CACHE_TAG_SET_INTERNING
	cachetag_intern_detach_all_locked(idx, &cleanup);
	#else
	for (u = 0; u < idx->nobjects; u++)
		cachetag_objent_dispose(idx, u);
#endif
	ndetached_segments = cachetag_object_detach_segments_locked(idx, 0,
	    detached_segments);
	cachetag_side_detach_all_locked(idx, &detached_primary,
	    &detached_retiring);
	idx->nobjects = idx->capobjects = 0;
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	#if CACHE_TAG_SET_INTERNING
	cachetag_intern_cleanup_free(idx, &cleanup);
	#endif
	free(detached_primary);
	free(detached_retiring);
	cachetag_object_free_segments(detached_segments, ndetached_segments);
	cachetag_purgemap_destroy(idx);
	cachetag_wal_delete(&idx->wal);
	pthread_mutex_destroy(&idx->replay_mtx);
	pthread_cond_destroy(&idx->sweep_cond);
	pthread_mutex_destroy(&idx->sweep_mtx);
	pthread_mutex_destroy(&idx->purge_mtx);
	pthread_mutex_destroy(&idx->counter_mtx);
	pthread_mutex_destroy(&idx->obj_mtx);
	free(idx->namespace);
	FREE_OBJ(idx);
}

int
cachetag_index_start(struct cachetag_index *idx)
{
	int r;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	r = cachetag_persist_replay(idx);
	if (r != 0)
		return (r);
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	if (!idx->sweep_running) {
		idx->sweep_running = 1;
		idx->sweep_stop = 0;
		WRK_BgThread(&idx->sweep_thread, "cachetag-purgemap",
		    cachetag_purgemap_sweep_thread, idx);
	}
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
	return (0);
}

void
cachetag_index_stop(struct cachetag_index *idx)
{
	unsigned running;

	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	running = idx->sweep_running;
	if (running) {
		idx->sweep_stop = 1;
		PTOK(pthread_cond_signal(&idx->sweep_cond));
	}
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
	if (running)
		PTOK(pthread_join(idx->sweep_thread, NULL));
	PTOK(pthread_mutex_lock(&idx->purge_mtx));
	idx->sweep_running = 0;
	PTOK(pthread_mutex_unlock(&idx->purge_mtx));
}

void
cachetag_index_detach_all(struct cachetag_index *idx)
{
	struct cachetag_objent *detached_segments[TAG_OBJECT_SEGMENTS];
	struct cachetag_side_bucket *detached_primary, *detached_retiring;
	unsigned ndetached_segments;
	#if CACHE_TAG_SET_INTERNING
	struct cachetag_intern_cleanup cleanup;
	#endif
	#if !CACHE_TAG_SET_INTERNING
	size_t u;
	#endif

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	memset(detached_segments, 0, sizeof detached_segments);
	detached_primary = NULL;
	detached_retiring = NULL;
	ndetached_segments = 0;
	#if CACHE_TAG_SET_INTERNING
	memset(&cleanup, 0, sizeof cleanup);
	#endif
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	#if CACHE_TAG_SET_INTERNING
	cachetag_intern_detach_all_locked(idx, &cleanup);
	#else
	for (u = 0; u < idx->nobjects; u++)
		cachetag_objent_dispose(idx, u);
#endif
	ndetached_segments = cachetag_object_detach_segments_locked(idx, 0,
	    detached_segments);
	cachetag_side_detach_all_locked(idx, &detached_primary,
	    &detached_retiring);
	idx->nobjects = 0;
	idx->capobjects = 0;
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	idx->counters.volatile_edges = 0;
	idx->counters.volatile_inline_folds = 0;
	idx->counters.volatile_object_count_overflow_bytes = 0;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	#if CACHE_TAG_SET_INTERNING
	cachetag_intern_cleanup_free(idx, &cleanup);
	#endif
	free(detached_primary);
	free(detached_retiring);
	cachetag_object_free_segments(detached_segments, ndetached_segments);
}

static uint64_t
cachetag_counter_read(struct cachetag_index *idx, uint64_t *counter)
{
	uint64_t n;

	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	n = *counter;
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	return (n);
}

uint64_t
cachetag_object_count(struct cachetag_index *idx)
{
	uint64_t n;

	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	n = idx->nobjects;
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	return (n);
}

uint64_t
cachetag_edge_count(struct cachetag_index *idx)
{

	return (cachetag_counter_read(idx, &idx->counters.volatile_edges));
}

void
cachetag_snapshot_counters(struct cachetag_index *idx,
    struct cachetag_counters *counters)
{
	struct cachetag_wal_stats wal;

	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	PTOK(pthread_mutex_lock(&idx->counter_mtx));
	cachetag_account_objects_locked(idx);
	*counters = idx->counters;
	/*
	 * Fan the family accumulators out into the flat published struct,
	 * still under counter_mtx and obj_mtx.  Same lock scope and the same
	 * words as the wholesale substruct copies this replaced.
	 */
#define CACHETAG_FANOUT_LOCKWAIT_MEMBER(g, i, m)			\
	counters->g##_obj_mtx_##m = idx->i.m;
#define CACHETAG_FANOUT_LOCKWAIT_GROUP(g, i)				\
	CACHETAG_LOCKWAIT_MEMBERS(CACHETAG_FANOUT_LOCKWAIT_MEMBER, g, i)
	CACHETAG_LOCKWAIT_GROUPS(CACHETAG_FANOUT_LOCKWAIT_GROUP)
#undef CACHETAG_FANOUT_LOCKWAIT_GROUP
#undef CACHETAG_FANOUT_LOCKWAIT_MEMBER

#define CACHETAG_FANOUT_RESIZE_MEMBER(g, i, m)				\
	counters->g##_##m = idx->i.m;
#define CACHETAG_FANOUT_RESIZE_GROUP(g, i)				\
	CACHETAG_RESIZE_MEMBERS(CACHETAG_FANOUT_RESIZE_MEMBER, g, i)
	CACHETAG_RESIZE_GROUPS(CACHETAG_FANOUT_RESIZE_GROUP)
#undef CACHETAG_FANOUT_RESIZE_GROUP
#undef CACHETAG_FANOUT_RESIZE_MEMBER

#if CACHE_TAG_SET_INTERNING
	/*
	 * Only guarded family: the intern_*_timing accumulators exist only in
	 * a set-interning build.  Elsewhere the fields stay zero from
	 * ALLOC_OBJ, which is what the memset()s in the non-interning branch
	 * of cachetag_account_objects_locked() used to spell out.
	 */
#define CACHETAG_FANOUT_TIMING_MEMBER(g, i, m)				\
	counters->g##_##m = idx->i.m;
#define CACHETAG_FANOUT_TIMING_GROUP(g, i, MEMBERS)			\
	MEMBERS(CACHETAG_FANOUT_TIMING_MEMBER, g, i)
	CACHETAG_TIMING_GROUPS(CACHETAG_FANOUT_TIMING_GROUP)
#undef CACHETAG_FANOUT_TIMING_GROUP
#undef CACHETAG_FANOUT_TIMING_MEMBER
#endif
	PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	counters->publication_phase = __atomic_load_n(
	    &idx->publication_gate.phase, __ATOMIC_SEQ_CST) & 1U;
	counters->publication_readers_phase0 =
	    __atomic_load_n(&idx->publication_gate.readers[0], __ATOMIC_SEQ_CST);
	counters->publication_readers_phase1 =
	    __atomic_load_n(&idx->publication_gate.readers[1], __ATOMIC_SEQ_CST);
	counters->reclaim_pending =
	    __atomic_load_n(&idx->reclaim_pending, __ATOMIC_ACQUIRE);
	counters->reclaim_phase =
	    __atomic_load_n(&idx->reclaim_phase, __ATOMIC_ACQUIRE) & 1U;
	cachetag_wal_snapshot(idx->wal, &wal);
	counters->persist_wal_records = wal.records;
	counters->persist_wal_bytes = wal.bytes;
	counters->persist_checkpoint_entries = wal.checkpoint_entries;
	counters->persist_checkpoint_wal_sequence =
	    wal.checkpoint_wal_sequence;
	counters->persist_checkpoint_bytes = wal.checkpoint_bytes;
	counters->persist_checkpoint_publications = wal.checkpoint_publications;
	counters->persist_checkpoint_segments_collected =
	    wal.checkpoint_segments_collected;
	counters->persist_orphan_files_collected = wal.orphan_files_collected;
	counters->persist_replay_records = wal.replay_records;
	counters->persist_failures += wal.failures;
	counters->persist_degraded = wal.degraded;
}

const struct cachetag_limits *
cachetag_get_limits(const struct cachetag_index *idx)
{

	return (&idx->limits);
}

const char *
cachetag_namespace_name(const struct cachetag_index *idx)
{

	return (idx->namespace);
}

void
cachetag_note_fellow_metric(struct cachetag_index *idx,
    enum cachetag_fellow_metric metric, uint64_t n)
{
	uint64_t *counter;

	switch (metric) {
	case TAG_FELLOW_ATTR_OBJECTS_WRITTEN:
		counter = &idx->counters.purgemap_fellow_attr_objects_written;
		break;
	case TAG_FELLOW_ATTR_BYTES_WRITTEN:
		counter = &idx->counters.purgemap_fellow_attr_bytes_written;
		break;
	case TAG_FELLOW_DIRECT_PROBES:
		counter = &idx->counters.purgemap_fellow_direct_probes;
		break;
	case TAG_FELLOW_ATTR_ABSENT:
		counter = &idx->counters.purgemap_fellow_attr_absent;
		break;
	case TAG_FELLOW_ATTR_INVALID:
		counter = &idx->counters.purgemap_fellow_attr_invalid;
		break;
	case TAG_FELLOW_ATTR_READ_FAILURES:
		counter = &idx->counters.purgemap_fellow_attr_read_failures;
		break;
	case TAG_FELLOW_NAMESPACE_RECORDS_PROBED:
		counter = &idx->counters.purgemap_fellow_namespace_records_probed;
		break;
	case TAG_FELLOW_STORE_INVARIANT_FAILURES:
		counter = &idx->counters.purgemap_fellow_store_invariant_failures;
		break;
	case TAG_FELLOW_VOLATILE_FALLBACK_ATTACHES:
		counter = &idx->counters.purgemap_volatile_fallback_attaches;
		break;
	default:
		WRONG("unknown Fellow metric");
	}
	cachetag_counter_add(idx, counter, n);
}

int
cachetag_test_fail_next_key_purge_wal(struct cachetag_index *idx)
{

	__atomic_store_n(&idx->test_fail_next_key_purge_wal, 1,
	    __ATOMIC_RELEASE);
	return (1);
}

int
cachetag_test_fail_next_persist_prepare(struct cachetag_index *idx)
{

	__atomic_store_n(&idx->test_fail_next_persist_prepare, 1,
	    __ATOMIC_RELEASE);
	return (1);
}

int
cachetag_test_abort_next_sweep(struct cachetag_index *idx)
{

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	__atomic_store_n(&idx->test_abort_next_sweep, 1, __ATOMIC_RELEASE);
	return (1);
}

int
cachetag_test_force_next_attach_slot_overflow(struct cachetag_index *idx)
{

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	__atomic_store_n(&idx->test_force_next_attach_slot_overflow, 1,
	    __ATOMIC_RELEASE);
	return (1);
}

int
cachetag_test_fail_next_object_segment_alloc(struct cachetag_index *idx)
{

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	__atomic_store_n(&idx->test_fail_next_object_segment_alloc, 1,
	    __ATOMIC_RELEASE);
	return (1);
}

int
cachetag_test_structural_limits(struct cachetag_index *idx)
{
	struct cachetag_index corrupt_idx;
	struct {
		struct cachetag_objent entries[TAG_OBJECT_SEGMENT0_SLOTS];
		uint8_t counts[TAG_OBJECT_SEGMENT0_SLOTS];
	} corrupt_segment;
	struct cachetag_side_bucket corrupt_buckets[2];
	struct cachetag_side_table corrupt_table;
	struct objcore *probe_oc;
#if CACHE_TAG_SET_INTERNING
	struct cachetag_interned_set *direct_set, *overflow_set;
#else
	void *direct_storage, *overflow_storage;
	uint64_t *values;
#endif
	uint64_t hash;
	uint32_t code;
	unsigned segment;
	unsigned nsegments;
	size_t base, decoded, slots;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	/*
	 * The two reserved side codes leave UINT32_MAX - 1 live slots, so
	 * segment 26 is the last
	 * segment reachable by a live object even when the directory reserves
	 * additional slots for future growth.
	 */
	nsegments = TAG_OBJECT_SEGMENTS < 27 ? TAG_OBJECT_SEGMENTS : 27;
	for (segment = 0; segment < nsegments; segment++) {
		base = cachetag_object_segment_base(segment);
		slots = cachetag_object_segment_slots(segment);
		if (cachetag_object_segment_for_slot(base) != segment ||
		    cachetag_object_segment_for_slot(base + slots - 1) != segment)
			return (0);
		if (segment != 0 &&
		    cachetag_object_segment_for_slot(base - 1) != segment - 1)
			return (0);
	}
	if (!cachetag_side_slot_encode(0, &code) || code != 1U ||
	    !cachetag_side_slot_decode(code, &decoded) || decoded != 0 ||
	    !cachetag_side_slot_encode(TAG_SIDE_MAX_OBJECTS - 1U, &code) ||
	    code != UINT32_MAX - 1U ||
	    !cachetag_side_slot_decode(code, &decoded) ||
	    decoded != TAG_SIDE_MAX_OBJECTS - 1U ||
	    cachetag_side_slot_encode(TAG_SIDE_MAX_OBJECTS, &code) ||
	    cachetag_side_slot_decode(TAG_SIDE_SLOT_EMPTY, &decoded) ||
	    cachetag_side_slot_decode(TAG_SIDE_SLOT_TOMBSTONE, &decoded))
		return (0);
	memset(&corrupt_idx, 0, sizeof corrupt_idx);
	memset(&corrupt_segment, 0, sizeof corrupt_segment);
	memset(corrupt_buckets, 0, sizeof corrupt_buckets);
	memset(&corrupt_table, 0, sizeof corrupt_table);
	probe_oc = (struct objcore *)(void *)&corrupt_idx;
	corrupt_idx.nobjects = 1;
	corrupt_idx.capobjects = TAG_OBJECT_SEGMENT0_SLOTS;
	corrupt_idx.object_segments[0] = corrupt_segment.entries;
	corrupt_idx.limits.max_keys_per_object = UINT_MAX;
	corrupt_idx.test_side_fingerprint_bits = 32;
	corrupt_table.map = corrupt_buckets;
	corrupt_table.buckets = 2;
	corrupt_table.live = 1;
	hash = cachetag_side_hash(probe_oc);
	corrupt_buckets[hash & 1U].fingerprint = cachetag_side_fingerprint(
	    &corrupt_idx,
	    hash);
	corrupt_buckets[hash & 1U].slot_code = 1;
	if (cachetag_side_table_find_locked(&corrupt_idx, &corrupt_table,
	    probe_oc, NULL) != -1 ||
	    cachetag_side_table_insert_locked(&corrupt_idx, &corrupt_table,
	    probe_oc, 0) != EFAULT)
		return (0);
	if (sizeof corrupt_segment != TAG_OBJECT_SEGMENT0_SLOTS *
	    (sizeof(struct cachetag_objent) + sizeof(uint8_t)) ||
	    cachetag_object_count_at(&corrupt_idx, 0) !=
	    &corrupt_segment.counts[0] ||
	    cachetag_objent_nfolds(&corrupt_idx, 0) != 0)
		return (0);
	corrupt_segment.counts[0] = 1;
	corrupt_segment.entries[0].membership.inline_one = UINT64_C(0x11);
	if (cachetag_objent_nfolds(&corrupt_idx, 0) != 1 ||
	    cachetag_objent_folds(&corrupt_idx, 0) == NULL ||
	    cachetag_objent_folds(&corrupt_idx, 0)[0] != UINT64_C(0x11))
		return (0);
#if CACHE_TAG_SET_INTERNING
	direct_set = malloc(sizeof *direct_set +
	    (size_t)TAG_OBJCOUNT_DIRECT_MAX * sizeof(uint64_t));
	if (direct_set == NULL)
		return (0);
	direct_set->magic = TAG_INTERNED_SET_MAGIC;
	direct_set->nfolds = TAG_OBJCOUNT_DIRECT_MAX;
	direct_set->hash = 0;
	direct_set->refs = 1;
	direct_set->next = NULL;
	direct_set->folds[TAG_OBJCOUNT_DIRECT_MAX - 1] = UINT64_C(0x254);
	corrupt_segment.entries[0].membership.set = direct_set;
	corrupt_segment.counts[0] = TAG_OBJCOUNT_DIRECT_MAX;
	if (cachetag_objent_nfolds(&corrupt_idx, 0) !=
	    TAG_OBJCOUNT_DIRECT_MAX ||
	    cachetag_objent_folds(&corrupt_idx, 0)
	    [TAG_OBJCOUNT_DIRECT_MAX - 1] != UINT64_C(0x254)) {
		free(direct_set);
		return (0);
	}
	/* A direct count that disagrees with the set must fail closed. */
	corrupt_segment.counts[0] = TAG_OBJCOUNT_DIRECT_MAX - 1;
	if (cachetag_objent_nfolds(&corrupt_idx, 0) != 0) {
		free(direct_set);
		return (0);
	}
	free(direct_set);
	overflow_set = malloc(sizeof *overflow_set +
	    (size_t)TAG_OBJCOUNT_OVERFLOW * sizeof(uint64_t));
	if (overflow_set == NULL)
		return (0);
	overflow_set->magic = TAG_INTERNED_SET_MAGIC;
	overflow_set->nfolds = TAG_OBJCOUNT_OVERFLOW;
	overflow_set->hash = 0;
	overflow_set->refs = 1;
	overflow_set->next = NULL;
	overflow_set->folds[TAG_OBJCOUNT_OVERFLOW - 1] = UINT64_C(0x255);
	corrupt_segment.entries[0].membership.set = overflow_set;
	corrupt_segment.entries[0].reg_seq = UINT64_C(0xfedcba9876543210);
	corrupt_segment.counts[0] = TAG_OBJCOUNT_OVERFLOW;
	corrupt_segment.entries[1] = corrupt_segment.entries[0];
	corrupt_segment.counts[1] = corrupt_segment.counts[0];
	if (cachetag_objent_nfolds(&corrupt_idx, 1) !=
	    TAG_OBJCOUNT_OVERFLOW ||
	    cachetag_objent_folds(&corrupt_idx, 1)
	    [TAG_OBJCOUNT_OVERFLOW - 1] != UINT64_C(0x255) ||
	    corrupt_segment.entries[1].reg_seq !=
	    UINT64_C(0xfedcba9876543210)) {
		free(overflow_set);
		return (0);
	}
	free(overflow_set);
#else
	direct_storage = cachetag_fold_storage_alloc(TAG_OBJCOUNT_DIRECT_MAX);
	if (direct_storage == NULL)
		return (0);
	values = cachetag_fold_storage_values(direct_storage,
	    TAG_OBJCOUNT_DIRECT_MAX);
	if (values == NULL) {
		cachetag_fold_storage_free(direct_storage,
		    TAG_OBJCOUNT_DIRECT_MAX);
		return (0);
	}
	values[TAG_OBJCOUNT_DIRECT_MAX - 1] = UINT64_C(0x254);
	corrupt_segment.entries[0].membership.vector = direct_storage;
	corrupt_segment.counts[0] = TAG_OBJCOUNT_DIRECT_MAX;
	if (cachetag_objent_nfolds(&corrupt_idx, 0) !=
	    TAG_OBJCOUNT_DIRECT_MAX ||
	    cachetag_objent_folds(&corrupt_idx, 0)
	    [TAG_OBJCOUNT_DIRECT_MAX - 1] != UINT64_C(0x254)) {
		cachetag_fold_storage_free(direct_storage,
		    TAG_OBJCOUNT_DIRECT_MAX);
		return (0);
	}
	cachetag_fold_storage_free(direct_storage, TAG_OBJCOUNT_DIRECT_MAX);
	overflow_storage = cachetag_fold_storage_alloc(TAG_OBJCOUNT_OVERFLOW);
	if (overflow_storage == NULL)
		return (0);
	values = cachetag_fold_storage_values(overflow_storage,
	    TAG_OBJCOUNT_OVERFLOW);
	if (values == NULL) {
		cachetag_fold_storage_free(overflow_storage,
		    TAG_OBJCOUNT_OVERFLOW);
		return (0);
	}
	values[TAG_OBJCOUNT_OVERFLOW - 1] = UINT64_C(0x255);
	corrupt_segment.entries[0].membership.vector = overflow_storage;
	corrupt_segment.entries[0].reg_seq = UINT64_C(0xfedcba9876543210);
	corrupt_segment.counts[0] = TAG_OBJCOUNT_OVERFLOW;
	corrupt_segment.entries[1] = corrupt_segment.entries[0];
	corrupt_segment.counts[1] = corrupt_segment.counts[0];
	if (cachetag_objent_nfolds(&corrupt_idx, 1) !=
	    TAG_OBJCOUNT_OVERFLOW ||
	    cachetag_objent_folds(&corrupt_idx, 1)
	    [TAG_OBJCOUNT_OVERFLOW - 1] != UINT64_C(0x255) ||
	    corrupt_segment.entries[1].reg_seq !=
	    UINT64_C(0xfedcba9876543210)) {
		cachetag_fold_storage_free(overflow_storage,
		    TAG_OBJCOUNT_OVERFLOW);
		return (0);
	}
	cachetag_fold_storage_free(overflow_storage, TAG_OBJCOUNT_OVERFLOW);
#endif
	return (sizeof(struct cachetag_objent) == 24 &&
	    sizeof(struct cachetag_fold_storage_header) == 8 &&
#if CACHE_TAG_SET_INTERNING
	    sizeof(struct cachetag_interned_set) == 32 &&
#endif
	    sizeof(*corrupt_segment.counts) == 1 &&
	    sizeof(struct cachetag_side_bucket) == 8 &&
	    sizeof(((struct cachetag_side_bucket *)0)->fingerprint) ==
	    sizeof(uint32_t) &&
	    sizeof(((struct cachetag_side_bucket *)0)->slot_code) ==
	    sizeof(uint32_t) &&
	    TAG_OBJECT_SEGMENTS >= 27 &&
	    TAG_SIDE_SOFT_GROW_LOAD_NUMERATOR == 5U &&
	    TAG_SIDE_SOFT_GROW_LOAD_DENOMINATOR == 8U &&
	    TAG_SIDE_HARD_GROW_LOAD_NUMERATOR == 7U &&
	    TAG_SIDE_HARD_GROW_LOAD_DENOMINATOR == 10U &&
	    TAG_SIDE_GROW_MIN_RUNWAY == 32U &&
	    cachetag_side_soft_grow_limit(16) == 10 &&
	    cachetag_side_soft_grow_limit(64) == 12 &&
	    cachetag_side_soft_grow_limit(128) == 57 &&
	    cachetag_side_soft_grow_limit(512) == 320 &&
	    cachetag_side_soft_grow_limit(16777216) == 10485760 &&
	    cachetag_object_growth_runway(64) == 8 &&
	    cachetag_object_growth_runway(1048576) == 32768 &&
	    cachetag_object_growth_runway(4194304) == 32768 &&
	    cachetag_object_segment_slots(0) == 64 &&
	    cachetag_object_segment_slots(1) == 64 &&
	    cachetag_object_segment_base(1) == 64 &&
	    cachetag_object_segment_base(2) == 128 &&
	    cachetag_object_capacity_for_segments(1) == 64 &&
	    cachetag_object_capacity_for_segments(2) == 128);
}

int
cachetag_test_side_fingerprint_bits(struct cachetag_index *idx, uint32_t bits)
{
	int accepted;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	if (bits > 32)
		return (0);
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	accepted = idx->nobjects == 0 && idx->side_primary.live == 0 &&
	    idx->side_retiring.live == 0 && !idx->side_migration_active;
	if (accepted)
		idx->test_side_fingerprint_bits = bits;
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	return (accepted);
}

int
cachetag_test_side_start_migration(struct cachetag_index *idx, uint32_t buckets)
{
	struct cachetag_side_bucket *map;
	uint64_t started, usec;
	size_t old_buckets;
	unsigned reason;
	int r;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	if (buckets == 0 || (buckets & (buckets - 1U)) != 0)
		return (0);
	started = cachetag_now_usec();
	map = cachetag_side_alloc_map(idx, buckets);
	usec = cachetag_elapsed_usec(started, cachetag_now_usec());
	cachetag_note_side_destination_alloc(idx, usec, map == NULL);
	if (map == NULL)
		return (0);
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	old_buckets = idx->side_primary.buckets;
	if (idx->side_migration_active || idx->side_primary.map == NULL) {
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		free(map);
		return (0);
	}
	if (buckets > idx->side_primary.buckets)
		reason = TAG_SIDE_MIGRATION_GROW;
	else if (buckets < idx->side_primary.buckets)
		reason = TAG_SIDE_MIGRATION_SHRINK;
	else
		reason = TAG_SIDE_MIGRATION_REBUILD;
	r = cachetag_side_publish_allocated_locked(idx, map, buckets, reason);
	if (r != 0) {
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		free(map);
		cachetag_note_side_rehash(idx, old_buckets, buckets, usec, 1);
		return (0);
	}
	cachetag_note_side_rehash(idx, old_buckets, buckets, usec, 0);
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	return (1);
}

int
cachetag_test_side_migrate_buckets(struct cachetag_index *idx, uint32_t buckets)
{
	struct cachetag_side_bucket *detached;
	size_t before_cursor, before_live, before_retiring_buckets;
	size_t detached_bytes, inspected, moved;
	uint64_t free_started, free_usec;
	int r;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	if (buckets == 0)
		return (0);
	detached = NULL;
	detached_bytes = 0;
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	if (!idx->side_migration_active) {
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		return (0);
	}
	before_cursor = idx->side_migrate_cursor;
	before_live = idx->side_retiring.live;
	before_retiring_buckets = idx->side_retiring.buckets;
	r = cachetag_side_migrate_some_locked(idx, buckets, &detached);
	if (r == 0) {
		if (detached != NULL)
			inspected = before_retiring_buckets - before_cursor;
		else
			inspected = idx->side_migrate_cursor >= before_cursor ?
			    idx->side_migrate_cursor - before_cursor :
			    before_retiring_buckets - before_cursor;
		moved = before_live >= idx->side_retiring.live ?
		    before_live - idx->side_retiring.live : 0;
		if (detached != NULL)
			detached_bytes = before_retiring_buckets *
			    sizeof(struct cachetag_side_bucket);
		PTOK(pthread_mutex_lock(&idx->counter_mtx));
		idx->counters.side_migration_batches++;
		idx->counters.side_migration_inspected_buckets += inspected;
		idx->counters.side_migration_moved_entries += moved;
		idx->resize_detached_bytes += detached_bytes;
		PTOK(pthread_mutex_unlock(&idx->counter_mtx));
	}
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	free_started = cachetag_now_usec();
	free(detached);
	free_usec = cachetag_elapsed_usec(free_started, cachetag_now_usec());
	if (detached != NULL) {
		cachetag_note_side_retired_free(idx, free_usec);
		cachetag_untrack_detached_bytes(idx, detached_bytes);
	}
	return (r == 0 ? 1 : -r);
}

int
cachetag_test_side_migration_active(struct cachetag_index *idx)
{
	int active;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	active = idx->side_migration_active != 0;
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	return (active);
}

int
cachetag_test_side_table_buckets(struct cachetag_index *idx)
{
	size_t buckets;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	buckets = cachetag_side_table_bucket_count(idx);
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	if (buckets > INT_MAX)
		return (-EOVERFLOW);
	return ((int)buckets);
}

int
cachetag_test_fail_next_side_migration_alloc(struct cachetag_index *idx)
{

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	__atomic_store_n(&idx->test_fail_next_side_migration_alloc, 1,
	    __ATOMIC_RELEASE);
	return (1);
}

#if CACHE_TAG_SET_INTERNING

int
cachetag_test_fail_next_intern_alloc(struct cachetag_index *idx)
{

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	__atomic_store_n(&idx->test_fail_next_intern_alloc, 1,
	    __ATOMIC_RELEASE);
	return (1);
}

int
cachetag_test_intern_initial_buckets(struct cachetag_index *idx, uint32_t n)
{
	int accepted;

	if (n == 0 || (n & (n - 1U)) != 0)
		return (0);
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	accepted = idx->intern_buckets == NULL && idx->intern_old_buckets == NULL &&
	    idx->intern_sets == 0;
	if (accepted)
		idx->test_intern_initial_buckets = n;
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	return (accepted);
}

int
cachetag_test_fail_next_intern_table_alloc(struct cachetag_index *idx)
{

	__atomic_store_n(&idx->test_fail_next_intern_table_alloc, 1,
	    __ATOMIC_RELEASE);
	return (1);
}

int
cachetag_test_intern_migration_active(struct cachetag_index *idx)
{
	int active;

	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	active = idx->intern_migration_active != 0;
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	return (active);
}

int
cachetag_test_intern_worker_hold(struct cachetag_index *idx, int hold)
{

	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	idx->test_intern_worker_hold = hold != 0;
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	if (!hold)
		cachetag_resize_wake(idx);
	return (1);
}

int
cachetag_test_intern_migrate_buckets(struct cachetag_index *idx, uint32_t n)
{
	struct cachetag_intern_cleanup cleanup;
	int active;

	if (n == 0)
		return (0);
	if (n > TAG_INTERN_MIGRATE_STEPS)
		n = TAG_INTERN_MIGRATE_STEPS;
	memset(&cleanup, 0, sizeof cleanup);
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	active = idx->intern_migration_active != 0;
	if (active)
		cachetag_intern_migrate_locked(idx, n, &cleanup);
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	cachetag_intern_cleanup_free(idx, &cleanup);
	return (active);
}

int
cachetag_test_intern_active_buckets(struct cachetag_index *idx)
{
	size_t n;

	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	n = idx->intern_nbuckets;
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	return (n > INT_MAX ? -EOVERFLOW : (int)n);
}

int
cachetag_test_intern_old_buckets(struct cachetag_index *idx)
{
	size_t n;

	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	n = idx->intern_old_nbuckets;
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	return (n > INT_MAX ? -EOVERFLOW : (int)n);
}

#endif /* CACHE_TAG_SET_INTERNING */

int
cachetag_test_resize_low_water_ready(struct cachetag_index *idx)
{
	int active;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	active = idx->resize_low_water_active != 0;
	if (active) {
		idx->resize_low_water_live = idx->nobjects;
		idx->resize_low_water_start_usec = cachetag_now_usec();
		idx->resize_low_water_force = 1;
	}
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	if (active) {
		cachetag_resize_wake(idx);
		while (cachetag_resize_maintenance(idx))
			VTIM_sleep(TAG_RESIZE_BATCH_YIELD_SEC);
	}
	return (active);
}

int
cachetag_test_resize_worker_drain(struct cachetag_index *idx, uint32_t timeout_ms)
{
	uint64_t deadline, now;
	int idle;

	CHECK_OBJ_NOTNULL(idx, TAG_INDEX_MAGIC);
	now = cachetag_now_usec();
	deadline = now + (uint64_t)timeout_ms * 1000;
	for (;;) {
		PTOK(pthread_mutex_lock(&idx->obj_mtx));
		if (idx->side_migration_active)
			idx->side_migration_auto = 1;
		if (idx->resize_low_water_active)
			idx->resize_low_water_force = 1;
		idle = !cachetag_resize_needs_work_locked(idx);
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		if (idle)
			return (1);
		(void)cachetag_resize_maintenance(idx);
		cachetag_resize_wake(idx);
		VTIM_sleep(0.001);
		now = cachetag_now_usec();
		if (timeout_ms == 0 || now >= deadline)
			return (0);
	}
}

int
cachetag_test_side_initial_buckets(struct cachetag_index *idx, uint32_t n)
{
	struct cachetag_side_bucket *map;

	if (n == 0 || (n & (n - 1U)) != 0)
		return (0);
	map = calloc(n, sizeof *map);
	if (map == NULL)
		return (0);
	PTOK(pthread_mutex_lock(&idx->obj_mtx));
	if (idx->nobjects != 0 || idx->capobjects != 0 ||
	    idx->side_primary.map != NULL || idx->side_retiring.map != NULL ||
	    idx->side_migration_active) {
		PTOK(pthread_mutex_unlock(&idx->obj_mtx));
		free(map);
		return (0);
	}
	idx->side_primary.map = map;
	idx->side_primary.buckets = n;
	idx->side_primary.tombstones = 0;
	idx->side_primary.live = 0;
	PTOK(pthread_mutex_unlock(&idx->obj_mtx));
	return (1);
}

void
cachetag_count_limit_rejection(struct cachetag_index *idx)
{

	cachetag_counter_add(idx, &idx->counters.limit_rejections, 1);
}

void
cachetag_count_parse_error(struct cachetag_index *idx)
{

	cachetag_counter_add(idx, &idx->counters.parse_errors, 1);
}

int
cachetag_persist_key_purge_digest(struct cachetag_index *idx,
    uint64_t digest_hi, uint64_t digest_lo, enum cachetag_purge_mode mode,
    uint64_t seq)
{
	unsigned char payload[32];
	uint64_t wal_seq;

	if (!cachetag_wal_enabled(idx->wal))
		return (0);
	if (__atomic_exchange_n(&idx->test_fail_next_key_purge_wal, 0,
	    __ATOMIC_ACQ_REL)) {
		cachetag_counter_add(idx, &idx->counters.persist_failures, 1);
		return (EIO);
	}
	cachetag_le16enc(payload, 2);
	payload[2] = mode == TAG_PURGE_HARD ? 1 : 2;
	payload[3] = 0;
	memset(payload + 4, 0, 4);
	cachetag_le64enc(payload + 8, digest_hi);
	cachetag_le64enc(payload + 16, digest_lo);
	cachetag_le64enc(payload + 24, seq);
	if (cachetag_wal_append(idx->wal, TAG_REPLAY_KEY_PURGE, payload,
	    sizeof payload, &wal_seq) != 0)
		return (EIO);
	return (0);
}

int
cachetag_decode_key_purge_record(const void *payload, uint64_t payload_len,
    uint64_t *digest_hi, uint64_t *digest_lo, enum cachetag_purge_mode *mode,
    uint64_t *seq)
{
	const unsigned char *p = payload;

	if (payload_len != 32 || cachetag_le16dec(p) != 2)
		return (EINVAL);
	*digest_hi = cachetag_le64dec(p + 8);
	*digest_lo = cachetag_le64dec(p + 16);
	*seq = cachetag_le64dec(p + 24);
	if (*seq == 0 || *seq == UINT64_MAX)
		return (EINVAL);
	if (p[2] == 1)
		*mode = TAG_PURGE_HARD;
	else if (p[2] == 2)
		*mode = TAG_PURGE_SOFT;
	else
		return (EINVAL);
	return (0);
}

static int
cachetag_wal_replay_cb(void *priv, const struct cachetag_wal_record *record)
{
	struct cachetag_index *idx = priv;
	int r;

	if (record->type != TAG_REPLAY_KEY_PURGE)
		return (0);
	r = cachetag_purgemap_replay(idx, record);
	if (r == 0)
		cachetag_counter_add(idx, &idx->counters.fellow_replayed_records, 1);
	return (r);
}

static int
cachetag_wal_checkpoint_begin_cb(void *priv,
    const struct cachetag_wal_checkpoint_meta *checkpoint)
{

	return (cachetag_purgemap_checkpoint_begin(priv, checkpoint));
}

static int
cachetag_wal_checkpoint_entry_cb(void *priv,
    const struct cachetag_wal_checkpoint_entry *checkpoint)
{

	return (cachetag_purgemap_checkpoint_entry(priv, checkpoint));
}

int
cachetag_persist_replay(struct cachetag_index *idx)
{
	int r;

	if (!cachetag_wal_enabled(idx->wal))
		return (0);
	PTOK(pthread_mutex_lock(&idx->replay_mtx));
	if (idx->replay_done) {
		PTOK(pthread_mutex_unlock(&idx->replay_mtx));
		return (0);
	}
	PTOK(pthread_mutex_unlock(&idx->replay_mtx));
	r = cachetag_wal_replay(idx->wal, cachetag_wal_replay_cb,
	    cachetag_wal_checkpoint_begin_cb, cachetag_wal_checkpoint_entry_cb,
	    idx);
	if (r == 0 && cachetag_wal_recovery_checkpoint_due(idx->wal))
		r = cachetag_purgemap_checkpoint(idx, 1);
	if (r == 0) {
		PTOK(pthread_mutex_lock(&idx->replay_mtx));
		idx->replay_done = 1;
		PTOK(pthread_mutex_unlock(&idx->replay_mtx));
	}
	return (r);
}
