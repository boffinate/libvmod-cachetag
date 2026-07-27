/*-
 * SPDX-License-Identifier: MPL-2.0
 */

#include "config.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "cachetag_util.h"
#include "vmod_cachetag_wal.h"

struct replay_seen {
	uint64_t	count;
	uint64_t	seq[16];
	uint8_t		type[16];
	uint64_t	payload_len[16];
};

struct checkpoint_seen {
	struct cachetag_wal_checkpoint_meta meta;
	struct cachetag_wal_checkpoint_entry entries[16];
	uint64_t count;
};

struct checkpoint_source {
	const struct cachetag_wal_checkpoint_entry *entries;
	uint64_t count;
	uint64_t cursor;
};

static void
fail_at(const char *file, int line, const char *expr)
{

	fprintf(stderr, "%s:%d: check failed: %s (errno=%d)\n",
	    file, line, expr, errno);
	exit(1);
}

#define CHECK(expr) do {						\
	if (!(expr))							\
		fail_at(__FILE__, __LINE__, #expr);			\
} while (0)

static char *
path_join(const char *a, const char *b)
{
	char *p;

	p = cachetag_path_join(a, b);
	CHECK(p != NULL);
	return (p);
}

static char *
wal_file_path(const char *root, uint64_t first_seq)
{
	char name[64];
	char *wal_dir, *path;
	int n;

	n = snprintf(name, sizeof name, "%020" PRIu64 ".vtw", first_seq);
	CHECK(n > 0 && (size_t)n < sizeof name);
	wal_dir = path_join(root, "wal");
	path = path_join(wal_dir, name);
	free(wal_dir);
	return (path);
}

static char *
make_root(void)
{
	char tmpl[] = "/tmp/cachetag-wal-test.XXXXXX";
	char *p;

	CHECK(mkdtemp(tmpl) != NULL);
	p = strdup(tmpl);
	CHECK(p != NULL);
	return (p);
}

static void
remove_tree(const char *path)
{
	DIR *d;
	struct dirent *de;
	char *child;

	d = opendir(path);
	if (d != NULL) {
		while ((de = readdir(d)) != NULL) {
			if (strcmp(de->d_name, ".") == 0 ||
			    strcmp(de->d_name, "..") == 0)
				continue;
			child = path_join(path, de->d_name);
			remove_tree(child);
			free(child);
		}
		CHECK(closedir(d) == 0);
		CHECK(rmdir(path) == 0);
		return;
	}
	CHECK(unlink(path) == 0 || errno == ENOENT);
}

static void
write_bytes(const char *path, const void *bytes, size_t len)
{
	int fd;

	fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0640);
	CHECK(fd >= 0);
	CHECK(write(fd, bytes, len) == (ssize_t)len);
	CHECK(close(fd) == 0);
}

static void
append_bytes(const char *path, const void *bytes, size_t len)
{
	int fd;

	fd = open(path, O_WRONLY | O_APPEND);
	CHECK(fd >= 0);
	CHECK(write(fd, bytes, len) == (ssize_t)len);
	CHECK(close(fd) == 0);
}

static int
replay_cb(void *priv, const struct cachetag_wal_record *rec)
{
	struct replay_seen *seen;

	seen = priv;
	CHECK(seen->count < sizeof seen->seq / sizeof seen->seq[0]);
	seen->seq[seen->count] = rec->sequence;
	seen->type[seen->count] = rec->type;
	seen->payload_len[seen->count] = rec->payload_len;
	seen->count++;
	return (0);
}

static int
checkpoint_next_cb(void *priv, struct cachetag_wal_checkpoint_entry *entry)
{
	struct checkpoint_source *source;

	source = priv;
	if (source->cursor == source->count)
		return (0);
	*entry = source->entries[source->cursor++];
	return (1);
}

static int
checkpoint_fail_cb(void *priv, struct cachetag_wal_checkpoint_entry *entry)
{

	(void)priv;
	(void)entry;
	return (-EIO);
}

static int
checkpoint_begin_cb(void *priv,
    const struct cachetag_wal_checkpoint_meta *meta)
{
	struct checkpoint_seen *seen;

	seen = priv;
	seen->meta = *meta;
	return (0);
}

static int
checkpoint_entry_cb(void *priv,
    const struct cachetag_wal_checkpoint_entry *entry)
{
	struct checkpoint_seen *seen;

	seen = priv;
	CHECK(seen->count < sizeof seen->entries / sizeof seen->entries[0]);
	seen->entries[seen->count++] = *entry;
	return (0);
}

static char *
find_checkpoint(const char *root)
{
	DIR *d;
	struct dirent *de;
	char *path;

	d = opendir(root);
	CHECK(d != NULL);
	path = NULL;
	while ((de = readdir(d)) != NULL) {
		if (strncmp(de->d_name, "checkpoint-", 11) != 0 ||
		    strstr(de->d_name, ".vtc") == NULL)
			continue;
		CHECK(path == NULL);
		path = path_join(root, de->d_name);
	}
	CHECK(closedir(d) == 0);
	CHECK(path != NULL);
	return (path);
}

static void
append_record(struct cachetag_wal *wal, uint8_t type, const char *payload,
    uint64_t expected_seq)
{
	uint64_t seq = 0;

	CHECK(cachetag_wal_append(wal, type, payload, strlen(payload),
	    &seq) == 0);
	CHECK(seq == expected_seq);
}

static void
test_append_replays_without_boundary_publish(void)
{
	struct cachetag_wal *wal;
	struct replay_seen seen;
	char *root;

	root = make_root();
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_prepare(wal) == 0);
	append_record(wal, 1, "first", 1);
	cachetag_wal_delete(&wal);

	memset(&seen, 0, sizeof seen);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, replay_cb, NULL, NULL, &seen) == 0);
	CHECK(seen.count == 1);
	CHECK(seen.seq[0] == 1);
	CHECK(seen.type[0] == 1);
	CHECK(seen.payload_len[0] == 5);
	cachetag_wal_delete(&wal);
	remove_tree(root);
	free(root);
}

static void
test_empty_or_torn_orphan_without_manifest_is_cold_start(void)
{
	struct cachetag_wal *wal;
	struct replay_seen seen;
	char *root, *wal_dir, *path;
	const char partial[] = "VTW1";

	root = make_root();
	wal_dir = path_join(root, "wal");
	CHECK(cachetag_mkdir_existing(wal_dir) == 0);
	path = wal_file_path(root, 1);
	write_bytes(path, "", 0);
	memset(&seen, 0, sizeof seen);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, replay_cb, NULL, NULL, &seen) == 0);
	CHECK(seen.count == 0);
	CHECK(!cachetag_wal_degraded(wal));
	cachetag_wal_delete(&wal);
	CHECK(access(path, F_OK) != 0 && errno == ENOENT);

	write_bytes(path, partial, sizeof partial - 1);
	memset(&seen, 0, sizeof seen);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, replay_cb, NULL, NULL, &seen) == 0);
	CHECK(seen.count == 0);
	CHECK(!cachetag_wal_degraded(wal));
	cachetag_wal_delete(&wal);
	CHECK(access(path, F_OK) != 0 && errno == ENOENT);

	free(path);
	free(wal_dir);
	remove_tree(root);
	free(root);
}

static void
test_valid_orphan_without_manifest_degrades(void)
{
	struct cachetag_wal *wal;
	struct replay_seen seen;
	char *root, *manifest;

	root = make_root();
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_prepare(wal) == 0);
	append_record(wal, 1, "first", 1);
	cachetag_wal_delete(&wal);
	manifest = path_join(root, "manifest");
	CHECK(unlink(manifest) == 0);
	free(manifest);

	memset(&seen, 0, sizeof seen);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, replay_cb, NULL, NULL, &seen) != 0);
	CHECK(cachetag_wal_degraded(wal));
	cachetag_wal_delete(&wal);
	remove_tree(root);
	free(root);
}

static void
test_torn_active_tail_is_truncated_before_append(void)
{
	struct cachetag_wal *wal;
	struct replay_seen seen;
	char *root, *path;
	const char garbage[] = "not-a-valid-wal-record";

	root = make_root();
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_prepare(wal) == 0);
	append_record(wal, 1, "first", 1);
	cachetag_wal_delete(&wal);
	path = wal_file_path(root, 1);
	append_bytes(path, garbage, sizeof garbage - 1);
	free(path);

	memset(&seen, 0, sizeof seen);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, replay_cb, NULL, NULL, &seen) == 0);
	CHECK(seen.count == 1);
	CHECK(seen.seq[0] == 1);
	append_record(wal, 2, "second", 2);
	cachetag_wal_delete(&wal);

	memset(&seen, 0, sizeof seen);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, replay_cb, NULL, NULL, &seen) == 0);
	CHECK(seen.count == 2);
	CHECK(seen.seq[0] == 1);
	CHECK(seen.seq[1] == 2);
	cachetag_wal_delete(&wal);
	remove_tree(root);
	free(root);
}

static void
test_rotation_manifest_reaches_unpublished_active_record(void)
{
	struct cachetag_wal *wal;
	struct replay_seen seen;
	char *root, *path1, *path2;

	root = make_root();
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 80);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_prepare(wal) == 0);
	append_record(wal, 1, "record-one-payload", 1);
	append_record(wal, 2, "record-two-payload", 2);
	cachetag_wal_delete(&wal);

	path1 = wal_file_path(root, 1);
	path2 = wal_file_path(root, 2);
	CHECK(access(path1, F_OK) == 0);
	CHECK(access(path2, F_OK) == 0);
	free(path1);
	free(path2);

	memset(&seen, 0, sizeof seen);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 80);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, replay_cb, NULL, NULL, &seen) == 0);
	CHECK(seen.count == 2);
	CHECK(seen.seq[0] == 1);
	CHECK(seen.seq[1] == 2);
	cachetag_wal_delete(&wal);
	remove_tree(root);
	free(root);
}

static void
test_checkpoint_replays_then_collects_covered_and_orphan_files(void)
{
	static const struct cachetag_wal_checkpoint_entry entries[] = {
		{ 101, 9, 0 },
		{ 202, 0, 10 }
	};
	struct cachetag_wal_checkpoint_meta meta;
	struct cachetag_wal_stats stats;
	struct checkpoint_source source;
	struct checkpoint_seen checkpoint;
	struct replay_seen replay;
	struct cachetag_wal *wal;
	char *root, *covered, *orphan_checkpoint, *orphan_wal;
	const char junk[] = "orphan";

	root = make_root();
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 80);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_prepare(wal) == 0);
	append_record(wal, 1, "record-one-payload", 1);
	append_record(wal, 1, "record-two-payload", 2);
	append_record(wal, 1, "record-three-payload", 3);
	cachetag_wal_delete(&wal);

	memset(&replay, 0, sizeof replay);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 80);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, replay_cb, NULL, NULL, &replay) == 0);
	CHECK(replay.count == 3);
	memset(&meta, 0, sizeof meta);
	meta.purge_sequence = 10;
	meta.hard_floor = 4;
	meta.soft_floor = 5;
	meta.entries = 2;
	source.entries = entries;
	source.count = 2;
	source.cursor = 0;
	CHECK(cachetag_wal_checkpoint(wal, &meta, checkpoint_next_cb,
	    &source) == 0);
	CHECK(meta.wal_sequence == 3);
	cachetag_wal_snapshot(wal, &stats);
	CHECK(stats.checkpoint_entries == 2);
	CHECK(stats.checkpoint_wal_sequence == 3);
	CHECK(stats.checkpoint_publications == 1);
	CHECK(stats.checkpoint_segments_collected >= 1);
	covered = wal_file_path(root, 1);
	CHECK(access(covered, F_OK) != 0 && errno == ENOENT);
	free(covered);
	append_record(wal, 2, "after-checkpoint", 4);
	cachetag_wal_delete(&wal);

	orphan_checkpoint = path_join(root,
	    "checkpoint-99999999999999999999.vtc");
	write_bytes(orphan_checkpoint, junk, sizeof junk - 1);
	orphan_wal = wal_file_path(root, 999);
	write_bytes(orphan_wal, junk, sizeof junk - 1);
	memset(&checkpoint, 0, sizeof checkpoint);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 80);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, NULL, checkpoint_begin_cb,
	    checkpoint_entry_cb, &checkpoint) == 0);
	CHECK(checkpoint.meta.purge_sequence == 10);
	CHECK(checkpoint.meta.wal_sequence == 3);
	CHECK(checkpoint.meta.hard_floor == 4);
	CHECK(checkpoint.meta.soft_floor == 5);
	CHECK(checkpoint.count == 2);
	CHECK(checkpoint.entries[0].fold == 101);
	CHECK(checkpoint.entries[1].fold == 202);
	CHECK(access(orphan_checkpoint, F_OK) != 0 && errno == ENOENT);
	CHECK(access(orphan_wal, F_OK) != 0 && errno == ENOENT);
	cachetag_wal_snapshot(wal, &stats);
	CHECK(stats.orphan_files_collected == 2);
	CHECK(stats.replay_records == 1);
	cachetag_wal_delete(&wal);

	free(orphan_wal);
	free(orphan_checkpoint);
	remove_tree(root);
	free(root);
}

static void
test_checkpoint_identity_and_checksum_fail_closed(void)
{
	static const struct cachetag_wal_checkpoint_entry entry = { 303, 7, 0 };
	struct cachetag_wal_checkpoint_meta meta;
	struct checkpoint_source source;
	struct checkpoint_seen checkpoint;
	struct cachetag_wal *wal;
	char *root, *path;
	unsigned char byte;
	int fd;

	root = make_root();
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_prepare(wal) == 0);
	append_record(wal, 1, "purge", 1);
	cachetag_wal_delete(&wal);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, NULL, NULL, NULL, NULL) == 0);
	memset(&meta, 0, sizeof meta);
	meta.purge_sequence = 7;
	meta.entries = 1;
	source.entries = &entry;
	source.count = 1;
	source.cursor = 0;
	CHECK(cachetag_wal_checkpoint(wal, &meta, checkpoint_next_cb,
	    &source) == 0);
	cachetag_wal_delete(&wal);

	wal = cachetag_wal_new(root, "other-namespace", TAG_WAL_FSYNC_STRICT,
	    4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, NULL, NULL, NULL, NULL) != 0);
	CHECK(cachetag_wal_degraded(wal));
	cachetag_wal_delete(&wal);

	path = find_checkpoint(root);
	fd = open(path, O_RDWR);
	CHECK(fd >= 0);
	CHECK(lseek(fd, 88, SEEK_SET) == 88);
	CHECK(read(fd, &byte, 1) == 1);
	byte ^= 0x40;
	CHECK(lseek(fd, 88, SEEK_SET) == 88);
	CHECK(write(fd, &byte, 1) == 1);
	CHECK(close(fd) == 0);
	free(path);
	memset(&checkpoint, 0, sizeof checkpoint);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, NULL, checkpoint_begin_cb,
	    checkpoint_entry_cb, &checkpoint) != 0);
	CHECK(cachetag_wal_degraded(wal));
	cachetag_wal_delete(&wal);
	remove_tree(root);
	free(root);
}

static void
test_failed_checkpoint_retains_manifest_wal_recovery(void)
{
	struct cachetag_wal_checkpoint_meta meta;
	struct replay_seen seen;
	struct cachetag_wal *wal;
	char *root, *tmp;

	root = make_root();
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_prepare(wal) == 0);
	append_record(wal, 1, "still-recoverable", 1);
	cachetag_wal_delete(&wal);
	memset(&seen, 0, sizeof seen);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, replay_cb, NULL, NULL, &seen) == 0);
	memset(&meta, 0, sizeof meta);
	meta.purge_sequence = 1;
	meta.entries = 1;
	CHECK(cachetag_wal_checkpoint(wal, &meta, checkpoint_fail_cb, NULL) != 0);
	CHECK(cachetag_wal_degraded(wal));
	cachetag_wal_delete(&wal);
	tmp = path_join(root, "checkpoint.tmp");
	CHECK(access(tmp, F_OK) == 0);
	free(tmp);

	memset(&seen, 0, sizeof seen);
	wal = cachetag_wal_new(root, "test", TAG_WAL_FSYNC_STRICT, 4096);
	CHECK(wal != NULL);
	CHECK(cachetag_wal_replay(wal, replay_cb, NULL, NULL, &seen) == 0);
	CHECK(seen.count == 1);
	CHECK(seen.seq[0] == 1);
	tmp = path_join(root, "checkpoint.tmp");
	CHECK(access(tmp, F_OK) != 0 && errno == ENOENT);
	free(tmp);
	cachetag_wal_delete(&wal);
	remove_tree(root);
	free(root);
}

int
main(void)
{

	test_append_replays_without_boundary_publish();
	test_empty_or_torn_orphan_without_manifest_is_cold_start();
	test_valid_orphan_without_manifest_degrades();
	test_torn_active_tail_is_truncated_before_append();
	test_rotation_manifest_reaches_unpublished_active_record();
	test_checkpoint_replays_then_collects_covered_and_orphan_files();
	test_checkpoint_identity_and_checksum_fail_closed();
	test_failed_checkpoint_retains_manifest_wal_recovery();
	return (0);
}
