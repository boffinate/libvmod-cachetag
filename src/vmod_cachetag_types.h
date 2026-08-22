/*-
 * SPDX-License-Identifier: MPL-2.0
 *
 * Shared purge-map types.  Cachetag has one invalidation implementation.
 */

#ifndef VMOD_TAG_TYPES_H
#define VMOD_TAG_TYPES_H

#include <stddef.h>
#include <stdint.h>

#include "cachetag_counters.h"

struct objcore;
struct cachetag_index;
struct worker;

enum cachetag_purge_mode {
	TAG_PURGE_HARD,
	TAG_PURGE_SOFT
};

struct cachetag_registration_snapshot {
	uint64_t digest_hi;
	uint64_t digest_lo;
	uint64_t reg_seq;
};

/*
 * Family accumulators.  These live on struct cachetag_index (never inside
 * struct cachetag_counters) and are fanned out into the published flat
 * struct by cachetag_snapshot_counters(), driven by the group and member
 * tables in cachetag_counters.h.
 */
struct cachetag_resize_counters {
	uint64_t calls;
	uint64_t usec;
	uint64_t max_usec;
	uint64_t failures;
	uint64_t compact_active_calls;
};

struct cachetag_lockwait_counters {
	uint64_t calls;
	uint64_t wait_usec;
	uint64_t wait_max_usec;
};

struct cachetag_timing_counters {
	uint64_t calls;
	uint64_t usec;
	uint64_t max_usec;
	uint64_t over_50us;
	uint64_t over_250us;
	uint64_t over_1ms;
	uint64_t over_10ms;
};

/*
 * The published counter surface, generated from the one inventory in
 * cachetag_counters.h.  Flat by construction: one uint64_t per schema entry,
 * in src/cachetag.vsc order.  The three family types above are index-side
 * accumulators only; cachetag_snapshot_counters() fans them out into the
 * flat fields below.
 */
#define CACHETAG_COUNTER_FIELD(n, t, l, o)	uint64_t n;
struct cachetag_counters {
	CACHETAG_COUNTERS(CACHETAG_COUNTER_FIELD)
};
#undef CACHETAG_COUNTER_FIELD

#define CACHETAG_COUNTER_ONE(n, t, l, o)	+ 1
enum { CACHETAG_COUNTER_FIELDS = 0 CACHETAG_COUNTERS(CACHETAG_COUNTER_ONE) };
#undef CACHETAG_COUNTER_ONE

/* Layout tripwire: no padding, no non-uint64_t member. */
_Static_assert(sizeof(struct cachetag_counters) ==
    (size_t)CACHETAG_COUNTER_FIELDS * sizeof(uint64_t),
    "struct cachetag_counters is not a flat uint64_t table");

/* Deliberate-change tripwire: bump only with a schema change. */
_Static_assert(CACHETAG_COUNTER_FIELDS == 245,
    "counter count changed: update cachetag.vsc, cachetag_counters.h and this assert together");

#endif
