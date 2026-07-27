/*-
 * SPDX-License-Identifier: MPL-2.0
 *
 * Secondary-key tag WAL.
 */

#ifndef VMOD_TAG_WAL_H
#define VMOD_TAG_WAL_H

#include <stdint.h>
#include <stddef.h>

enum cachetag_wal_fsync {
	TAG_WAL_FSYNC_STRICT,
	TAG_WAL_FSYNC_GROUPED
};

enum cachetag_wal_record_type {
	TAG_REPLAY_KEY_PURGE = 3
};

struct cachetag_wal;

struct cachetag_wal_record {
	uint8_t type;
	uint64_t sequence;
	const void *payload;
	uint64_t payload_len;
};

typedef int cachetag_wal_replay_f(void *, const struct cachetag_wal_record *);

struct cachetag_wal_checkpoint_meta {
	uint64_t purge_sequence;
	uint64_t wal_sequence;
	uint64_t hard_floor;
	uint64_t soft_floor;
	uint64_t entries;
};

struct cachetag_wal_checkpoint_entry {
	uint64_t fold;
	uint64_t hard_sequence;
	uint64_t soft_sequence;
};

typedef int cachetag_wal_checkpoint_next_f(void *,
    struct cachetag_wal_checkpoint_entry *);
typedef int cachetag_wal_checkpoint_begin_f(void *,
    const struct cachetag_wal_checkpoint_meta *);
typedef int cachetag_wal_checkpoint_entry_f(void *,
    const struct cachetag_wal_checkpoint_entry *);

struct cachetag_wal_stats {
	uint64_t records;
	uint64_t bytes;
	uint64_t failures;
	uint64_t checkpoint_entries;
	uint64_t checkpoint_wal_sequence;
	uint64_t checkpoint_bytes;
	uint64_t checkpoint_publications;
	uint64_t checkpoint_segments_collected;
	uint64_t orphan_files_collected;
	uint64_t replay_records;
	unsigned degraded;
};

struct cachetag_wal *cachetag_wal_new(const char *, const char *, enum cachetag_wal_fsync,
    uint64_t);
void cachetag_wal_delete(struct cachetag_wal **);
int cachetag_wal_enabled(const struct cachetag_wal *);
int cachetag_wal_degraded(const struct cachetag_wal *);
int cachetag_wal_prepare(struct cachetag_wal *);
int cachetag_wal_append(struct cachetag_wal *, uint8_t, const void *,
    uint64_t, uint64_t *);
int cachetag_wal_checkpoint_due(struct cachetag_wal *);
int cachetag_wal_recovery_checkpoint_due(struct cachetag_wal *);
int cachetag_wal_checkpoint(struct cachetag_wal *,
    struct cachetag_wal_checkpoint_meta *, cachetag_wal_checkpoint_next_f *,
    void *);
int cachetag_wal_replay(struct cachetag_wal *, cachetag_wal_replay_f *,
    cachetag_wal_checkpoint_begin_f *, cachetag_wal_checkpoint_entry_f *,
    void *);
void cachetag_wal_snapshot(struct cachetag_wal *, struct cachetag_wal_stats *);

#endif
