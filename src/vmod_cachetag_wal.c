/*-
 * SPDX-License-Identifier: MPL-2.0
 */

#include "config.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "vdef.h"
#include "vas.h"
#include "miniobj.h"
#include "vsb.h"

#include "cachetag_util.h"
#include "vmod_cachetag_wal.h"
#define XXH_STATIC_LINKING_ONLY
#include "xxhash.h"

#define TAG_WAL_MAGIC		0x7461776c
#define TAG_WAL_HEADER_LEN	32
#define TAG_WAL_HEADER_CSUM	24
#define TAG_WAL_DEFAULT_SEGMENT_BYTES	(64ULL * 1024ULL * 1024ULL)
#define TAG_CHECKPOINT_HEADER_LEN	88
#define TAG_CHECKPOINT_ENTRY_LEN	24
#define TAG_CHECKPOINT_FOOTER_LEN	8
#define TAG_CHECKPOINT_WRITE_BUFFER_BYTES	(64U * 1024U)

struct cachetag_wal_segment {
	char			*relpath;
	uint64_t		first_seq;
	uint64_t		last_seq;
	uint64_t		bytes;
	uint64_t		records;
	struct cachetag_wal_segment	*next;
};

struct cachetag_wal {
	unsigned		magic;
	char			*root;
	char			*namespace;
	char			*wal_dir;
	int			fd;
	uint64_t		next_seq;
	uint64_t		segment_max_bytes;
	enum cachetag_wal_fsync	fsync_policy;
	pthread_mutex_t		mtx;
	uint64_t		records;
	uint64_t		bytes;
	uint64_t		failures;
	unsigned		degraded;
	unsigned		replayed;
	uint64_t		generation;
	uint64_t		root_id_hi;
	uint64_t		root_id_lo;
	char			*checkpoint_relpath;
	uint64_t		checkpoint_generation;
	uint64_t		checkpoint_purge_sequence;
	uint64_t		checkpoint_wal_sequence;
	uint64_t		checkpoint_hard_floor;
	uint64_t		checkpoint_soft_floor;
	uint64_t		checkpoint_entries;
	uint64_t		checkpoint_bytes;
	uint64_t		checkpoint_checksum;
	uint64_t		checkpoint_publications;
	uint64_t		checkpoint_segments_collected;
	uint64_t		orphan_files_collected;
	uint64_t		replay_records;
	struct cachetag_wal_segment	*segments;
	struct cachetag_wal_segment	*active_segment;
};

static uint64_t
cachetag_wal_checksum(const void *ptr, size_t len)
{

	return (XXH3_64bits(ptr, len));
}

static uint64_t
cachetag_wal_record_checksum(const unsigned char *hdr, const void *payload,
    uint64_t payload_len)
{
	XXH3_state_t state;

	AZ(XXH3_64bits_reset(&state));
	AZ(XXH3_64bits_update(&state, hdr, TAG_WAL_HEADER_CSUM));
	if (payload_len > 0)
		AZ(XXH3_64bits_update(&state, payload, (size_t)payload_len));
	return (XXH3_64bits_digest(&state));
}

static int
cachetag_wal_random_bytes(void *ptr, size_t len)
{
	unsigned char *p;
	ssize_t n;
	size_t off;
	int fd, r;

	fd = open("/dev/urandom", O_RDONLY);
	if (fd < 0)
		return (errno);
	p = ptr;
	off = 0;
	r = 0;
	while (off < len) {
		n = read(fd, p + off, len - off);
		if (n < 0) {
			if (errno == EINTR)
				continue;
			r = errno;
			break;
		}
		if (n == 0) {
			r = EIO;
			break;
		}
		off += (size_t)n;
	}
	if (close(fd) != 0 && r == 0)
		r = errno;
	return (r);
}

static int
cachetag_wal_identity_init(struct cachetag_wal *wal)
{
	unsigned char identity[16];
	int r;

	if (wal->root_id_hi != 0 || wal->root_id_lo != 0)
		return (0);
	r = cachetag_wal_random_bytes(identity, sizeof identity);
	if (r != 0)
		return (r);
	wal->root_id_hi = cachetag_le64dec(identity);
	wal->root_id_lo = cachetag_le64dec(identity + 8);
	if (wal->root_id_hi == 0 && wal->root_id_lo == 0)
		wal->root_id_lo = 1;
	return (0);
}

static void
cachetag_wal_segment_free(struct cachetag_wal_segment *seg)
{

	if (seg == NULL)
		return;
	free(seg->relpath);
	free(seg);
}

static void
cachetag_wal_segments_clear(struct cachetag_wal *wal)
{
	struct cachetag_wal_segment *seg, *seg2;

	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	for (seg = wal->segments; seg != NULL; seg = seg2) {
		seg2 = seg->next;
		cachetag_wal_segment_free(seg);
	}
	wal->segments = NULL;
	wal->active_segment = NULL;
}

static char *
cachetag_wal_segment_relpath(uint64_t first_seq)
{
	char name[64];
	int r;

	r = snprintf(name, sizeof name, "wal/%020" PRIu64 ".vtw", first_seq);
	if (r < 0 || (size_t)r >= sizeof name)
		return (NULL);
	return (strdup(name));
}

static char *
cachetag_wal_checkpoint_relpath(uint64_t generation)
{
	char name[64];
	int r;

	r = snprintf(name, sizeof name, "checkpoint-%020" PRIu64 ".vtc",
	    generation);
	if (r < 0 || (size_t)r >= sizeof name)
		return (NULL);
	return (strdup(name));
}

static char *
cachetag_wal_relpath_to_path(const char *root, const char *relpath)
{
	const char *p;

	if (relpath == NULL || relpath[0] == '\0' || relpath[0] == '/')
		return (NULL);
	for (p = relpath; *p != '\0';) {
		const char *slash = strchr(p, '/');
		size_t len = slash == NULL ? strlen(p) : (size_t)(slash - p);

		if (len == 0 || (len == 1 && p[0] == '.') ||
		    (len == 2 && p[0] == '.' && p[1] == '.'))
			return (NULL);
		if (slash == NULL)
			break;
		p = slash + 1;
	}
	return (cachetag_path_join(root, relpath));
}

static int
cachetag_has_suffix(const char *s, const char *suffix)
{
	size_t slen, suffix_len;

	AN(s);
	AN(suffix);
	slen = strlen(s);
	suffix_len = strlen(suffix);
	return (slen >= suffix_len &&
	    strcmp(s + slen - suffix_len, suffix) == 0);
}

static struct cachetag_wal_segment *
cachetag_wal_segment_new(uint64_t first_seq)
{
	struct cachetag_wal_segment *seg;

	seg = calloc(1, sizeof *seg);
	if (seg == NULL)
		return (NULL);
	seg->relpath = cachetag_wal_segment_relpath(first_seq);
	if (seg->relpath == NULL) {
		free(seg);
		return (NULL);
	}
	seg->first_seq = first_seq;
	return (seg);
}

static void
cachetag_wal_segment_append(struct cachetag_wal *wal, struct cachetag_wal_segment *seg)
{
	struct cachetag_wal_segment **segp;

	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	AN(seg);
	for (segp = &wal->segments; *segp != NULL; segp = &(*segp)->next)
		continue;
	*segp = seg;
	if (seg->last_seq == 0)
		wal->active_segment = seg;
}

static int cachetag_wal_fsync_dir(const char *);
static int cachetag_wal_manifest_publish_locked(struct cachetag_wal *);

static int
cachetag_wal_read(int fd, unsigned char *buf, size_t len, size_t *nreadp)
{
	ssize_t n;
	size_t off = 0;

	while (off < len) {
		n = read(fd, buf + off, len - off);
		if (n < 0) {
			if (errno == EINTR)
				continue;
			return (errno);
		}
		if (n == 0)
			break;
		off += (size_t)n;
	}
	*nreadp = off;
	return (0);
}

static int
cachetag_wal_file_has_valid_record(const char *path, int *has_recordp)
{
	unsigned char hdr[TAG_WAL_HEADER_LEN], *payload = NULL;
	uint64_t payload_len, stored, checksum;
	size_t nread;
	int fd, r = 0;

	AN(path);
	AN(has_recordp);
	*has_recordp = 0;
	fd = open(path, O_RDONLY);
	if (fd < 0)
		return (errno);
	r = cachetag_wal_read(fd, hdr, sizeof hdr, &nread);
	if (r != 0 || nread != sizeof hdr)
		goto out;
	if (memcmp(hdr, "VTW1", 4) != 0 || hdr[4] != 1)
		goto out;
	payload_len = cachetag_le64dec(hdr + 16);
	if (payload_len > SIZE_MAX - TAG_WAL_HEADER_CSUM)
		goto out;
	payload = payload_len > 0 ? malloc((size_t)payload_len) : NULL;
	if (payload_len > 0 && payload == NULL) {
		r = ENOMEM;
		goto out;
	}
	if (payload_len > 0) {
		r = cachetag_wal_read(fd, payload, (size_t)payload_len, &nread);
		if (r != 0 || nread != (size_t)payload_len)
			goto out;
	}
	stored = cachetag_le64dec(hdr + TAG_WAL_HEADER_CSUM);
	checksum = cachetag_wal_record_checksum(hdr, payload, payload_len);
	if (checksum == stored)
		*has_recordp = 1;
out:
	free(payload);
	if (close(fd) != 0 && r == 0)
		r = errno;
	return (r);
}

static int
cachetag_wal_orphan_files_have_records(const struct cachetag_wal *wal,
    int *has_recordp)
{
	DIR *d;
	struct dirent *de;
	char *path;
	int r = 0, cleanup_r = 0, removed = 0, has_record = 0;

	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	AN(has_recordp);
	*has_recordp = 0;
	d = opendir(wal->wal_dir);
	if (d == NULL && (errno == ENOENT || errno == ENOTDIR))
		return (0);
	if (d == NULL)
		return (errno);
	while ((de = readdir(d)) != NULL) {
		if (!cachetag_has_suffix(de->d_name, ".vtw"))
			continue;
		path = cachetag_path_join(wal->wal_dir, de->d_name);
		if (path == NULL) {
			r = ENOMEM;
			break;
		}
		r = cachetag_wal_file_has_valid_record(path, &has_record);
		if (r == 0 && has_record) {
			*has_recordp = 1;
			free(path);
			break;
		}
		if (r == 0 && unlink(path) != 0 && errno != ENOENT &&
		    cleanup_r == 0)
			cleanup_r = errno;
		else if (r == 0)
			removed = 1;
		free(path);
		if (r != 0)
			break;
	}
	(void)closedir(d);
	if (r == 0 && *has_recordp == 0 && cleanup_r == 0 && removed)
		cleanup_r = cachetag_wal_fsync_dir(wal->wal_dir);
	if (r == 0)
		r = cleanup_r;
	return (r);
}

struct cachetag_wal *
cachetag_wal_new(const char *root, const char *namespace,
    enum cachetag_wal_fsync fsync_policy, uint64_t segment_max_bytes)
{
	struct cachetag_wal *wal;

	if (root == NULL || *root == '\0')
		return (NULL);
	if (namespace == NULL || *namespace == '\0')
		namespace = "default";
	ALLOC_OBJ(wal, TAG_WAL_MAGIC);
	if (wal == NULL)
		return (NULL);
	wal->fd = -1;
	AZ(pthread_mutex_init(&wal->mtx, NULL));
	wal->root = strdup(root);
	if (wal->root == NULL) {
		cachetag_wal_delete(&wal);
		return (NULL);
	}
	wal->namespace = strdup(namespace);
	if (wal->namespace == NULL) {
		cachetag_wal_delete(&wal);
		return (NULL);
	}
	wal->wal_dir = cachetag_path_join(wal->root, "wal");
	if (wal->wal_dir == NULL) {
		cachetag_wal_delete(&wal);
		return (NULL);
	}
	wal->next_seq = 1;
	wal->active_segment = cachetag_wal_segment_new(1);
	if (wal->active_segment == NULL) {
		cachetag_wal_delete(&wal);
		return (NULL);
	}
	cachetag_wal_segment_append(wal, wal->active_segment);
	if (segment_max_bytes == 0)
		segment_max_bytes = TAG_WAL_DEFAULT_SEGMENT_BYTES;
	wal->segment_max_bytes = segment_max_bytes;
	wal->fsync_policy = fsync_policy;
	if (cachetag_wal_identity_init(wal) != 0) {
		cachetag_wal_delete(&wal);
		return (NULL);
	}
	return (wal);
}

void
cachetag_wal_delete(struct cachetag_wal **walp)
{
	struct cachetag_wal *wal;

	if (walp == NULL || *walp == NULL)
		return;
	TAKE_OBJ_NOTNULL(wal, walp, TAG_WAL_MAGIC);
	if (wal->fd >= 0)
		(void)close(wal->fd);
	AZ(pthread_mutex_destroy(&wal->mtx));
	cachetag_wal_segments_clear(wal);
	free(wal->checkpoint_relpath);
	free(wal->wal_dir);
	free(wal->namespace);
	free(wal->root);
	FREE_OBJ(wal);
}

int
cachetag_wal_enabled(const struct cachetag_wal *wal)
{

	return (wal != NULL);
}

int
cachetag_wal_degraded(const struct cachetag_wal *wal)
{

	return (wal != NULL && wal->degraded);
}

static int
cachetag_wal_fail_locked(struct cachetag_wal *wal, int err)
{

	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	wal->degraded = 1;
	wal->failures++;
	if (err == 0)
		err = EIO;
	return (err);
}

static int
cachetag_wal_prepare_locked(struct cachetag_wal *wal)
{
	struct stat st;
	char *active_path;
	int created = 0, r;

	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	if (wal->degraded)
		return (EIO);
	if (wal->fd >= 0)
		return (0);
	r = cachetag_mkdir_existing(wal->root);
	if (r != 0)
		return (cachetag_wal_fail_locked(wal, r));
	r = cachetag_mkdir_existing(wal->wal_dir);
	if (r != 0)
		return (cachetag_wal_fail_locked(wal, r));
	active_path = wal->active_segment != NULL ?
	    cachetag_wal_relpath_to_path(wal->root, wal->active_segment->relpath) :
	    NULL;
	if (active_path == NULL)
		return (cachetag_wal_fail_locked(wal, ENOMEM));
	if (stat(active_path, &st) != 0) {
		if (errno != ENOENT) {
			r = errno;
			free(active_path);
			return (cachetag_wal_fail_locked(wal, r));
		}
		created = 1;
	}
	wal->fd = open(active_path, O_WRONLY | O_CREAT | O_APPEND, 0640);
	free(active_path);
	if (wal->fd < 0)
		return (cachetag_wal_fail_locked(wal, errno));
	if (wal->replayed && wal->active_segment != NULL &&
	    wal->active_segment->last_seq == 0 && fstat(wal->fd, &st) == 0 &&
	    st.st_size >= 0 &&
	    (uintmax_t)st.st_size > wal->active_segment->bytes &&
	    ftruncate(wal->fd, (off_t)wal->active_segment->bytes) != 0)
		return (cachetag_wal_fail_locked(wal, errno));
	if (created) {
		r = cachetag_wal_fsync_dir(wal->wal_dir);
		if (r != 0)
			return (cachetag_wal_fail_locked(wal, r));
		r = cachetag_wal_manifest_publish_locked(wal);
		if (r != 0)
			return (r);
	}
	return (0);
}

int
cachetag_wal_prepare(struct cachetag_wal *wal)
{
	int r;

	if (wal == NULL)
		return (0);
	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	AZ(pthread_mutex_lock(&wal->mtx));
	r = cachetag_wal_prepare_locked(wal);
	AZ(pthread_mutex_unlock(&wal->mtx));
	return (r);
}

static int
cachetag_wal_write_all(int fd, const unsigned char *p, size_t len)
{
	ssize_t n;

	while (len > 0) {
		n = write(fd, p, len);
		if (n < 0) {
			if (errno == EINTR)
				continue;
			return (errno);
		}
		if (n == 0)
			return (EIO);
		p += n;
		len -= (size_t)n;
	}
	return (0);
}

static int
cachetag_wal_fsync_dir(const char *path)
{
	int fd, r = 0;

	fd = open(path, O_RDONLY | O_DIRECTORY);
	if (fd < 0)
		return (errno);
	if (fsync(fd) != 0)
		r = errno;
	if (close(fd) != 0 && r == 0)
		r = errno;
	return (r);
}

static int
cachetag_wal_manifest_publish_locked(struct cachetag_wal *wal)
{
	struct cachetag_wal_segment *seg;
	struct vsb *vsb = NULL;
	char *tmp_path, *path;
	char tail[40];
	uint64_t checksum;
	int fd = -1, r = 0, n;

	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	if (wal->degraded)
		return (EIO);
	tmp_path = cachetag_path_join(wal->root, "manifest.tmp");
	path = cachetag_path_join(wal->root, "manifest");
	if (tmp_path == NULL || path == NULL) {
		free(tmp_path);
		free(path);
		return (cachetag_wal_fail_locked(wal, ENOMEM));
	}
	vsb = VSB_new_auto();
	if (vsb == NULL) {
		r = ENOMEM;
		goto out;
	}
	VSB_cat(vsb, "magic=VTM1\n");
	VSB_cat(vsb, "version=1\n");
	VSB_printf(vsb, "namespace=%s\n", wal->namespace);
	VSB_printf(vsb, "root_id=%016" PRIx64 "%016" PRIx64 "\n",
	    wal->root_id_hi, wal->root_id_lo);
	VSB_printf(vsb, "generation=%" PRIu64 "\n", ++wal->generation);
	VSB_printf(vsb, "created_unix_nsec=%" PRIu64 "\n",
	    (uint64_t)time(NULL) * (uint64_t)1000000000);
	VSB_printf(vsb, "last_sequence=%" PRIu64 "\n",
	    wal->next_seq == 0 ? 0 : wal->next_seq - 1);
	if (wal->checkpoint_relpath != NULL)
		VSB_printf(vsb, "checkpoint=%s,%" PRIu64 ",%" PRIu64 ",%" PRIu64
		    ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64
		    ",%" PRIu64 "\n", wal->checkpoint_relpath,
		    wal->checkpoint_generation, wal->checkpoint_purge_sequence,
		    wal->checkpoint_wal_sequence, wal->checkpoint_hard_floor,
		    wal->checkpoint_soft_floor, wal->checkpoint_entries,
		    wal->checkpoint_bytes, wal->checkpoint_checksum);
	for (seg = wal->segments; seg != NULL; seg = seg->next) {
		if (seg != wal->active_segment &&
		    seg->bytes == 0 && seg->records == 0)
			continue;
		VSB_printf(vsb, "wal=%s,%" PRIu64 ",%" PRIu64 ",%" PRIu64
		    "\n", seg->relpath, seg->first_seq, seg->last_seq,
		    seg->bytes);
	}
	if (VSB_finish(vsb) != 0) {
		r = ENOMEM;
		goto out;
	}
	checksum = cachetag_wal_checksum(VSB_data(vsb), (size_t)VSB_len(vsb));
	n = snprintf(tail, sizeof tail, "checksum=xxh3:%016" PRIx64 "\n",
	    checksum);
	if (n < 0 || (size_t)n >= sizeof tail) {
		r = EIO;
		goto out;
	}
	fd = open(tmp_path, O_WRONLY | O_CREAT | O_TRUNC, 0640);
	if (fd < 0) {
		r = errno;
		goto out;
	}
	r = cachetag_wal_write_all(fd, (const unsigned char *)VSB_data(vsb),
	    (size_t)VSB_len(vsb));
	if (r == 0)
		r = cachetag_wal_write_all(fd, (const unsigned char *)tail,
		    (size_t)n);
	if (r != 0)
		goto out;
	if (fsync(fd) != 0) {
		r = errno;
		goto out;
	}
	if (close(fd) != 0) {
		fd = -1;
		r = errno;
		goto out;
	}
	fd = -1;
	if (rename(tmp_path, path) != 0) {
		r = errno;
		goto out;
	}
	r = cachetag_wal_fsync_dir(wal->root);
out:
	if (fd >= 0 && close(fd) != 0 && r == 0)
		r = errno;
	if (vsb != NULL)
		VSB_destroy(&vsb);
	free(tmp_path);
	free(path);
	if (r != 0)
		r = cachetag_wal_fail_locked(wal, r);
	return (r);
}

static int
cachetag_wal_rotate_locked(struct cachetag_wal *wal)
{
	struct cachetag_wal_segment *next_seg;
	char *next_path;
	uint64_t last_seq;
	int r;

	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	if (wal->active_segment == NULL || wal->active_segment->records == 0)
		return (0);
	last_seq = wal->next_seq - 1;
	next_seg = cachetag_wal_segment_new(wal->next_seq);
	if (next_seg == NULL)
		return (cachetag_wal_fail_locked(wal, ENOMEM));
	next_path = cachetag_wal_relpath_to_path(wal->root, next_seg->relpath);
	if (next_path == NULL) {
		cachetag_wal_segment_free(next_seg);
		return (cachetag_wal_fail_locked(wal, ENOMEM));
	}
	if (fsync(wal->fd) != 0) {
		r = errno;
		free(next_path);
		cachetag_wal_segment_free(next_seg);
		return (cachetag_wal_fail_locked(wal, r));
	}
	if (close(wal->fd) != 0) {
		wal->fd = -1;
		r = errno;
		free(next_path);
		cachetag_wal_segment_free(next_seg);
		return (cachetag_wal_fail_locked(wal, r));
	}
	wal->fd = -1;
	wal->active_segment->last_seq = last_seq;
	cachetag_wal_segment_append(wal, next_seg);
	wal->fd = open(next_path, O_WRONLY | O_CREAT | O_APPEND, 0640);
	free(next_path);
	if (wal->fd < 0)
		return (cachetag_wal_fail_locked(wal, errno));
	r = cachetag_wal_fsync_dir(wal->wal_dir);
	if (r != 0)
		return (cachetag_wal_fail_locked(wal, r));
	r = cachetag_wal_manifest_publish_locked(wal);
	if (r != 0)
		return (r);
	return (0);
}

int
cachetag_wal_append(struct cachetag_wal *wal, uint8_t type,
    const void *payload, uint64_t payload_len, uint64_t *seqp)
{
	unsigned char header[TAG_WAL_HEADER_LEN];
	uint64_t seq, checksum;
	size_t record_len;
	int r;

	if (wal == NULL) {
		if (seqp != NULL)
			*seqp = 0;
		return (0);
	}
	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	AZ(pthread_mutex_lock(&wal->mtx));
	r = cachetag_wal_prepare_locked(wal);
	if (r != 0)
		goto out_unlock;
	if (payload_len > 0 && payload == NULL) {
		r = cachetag_wal_fail_locked(wal, EINVAL);
		goto out_unlock;
	}
	if (payload_len > SIZE_MAX - TAG_WAL_HEADER_LEN) {
		r = cachetag_wal_fail_locked(wal, EFBIG);
		goto out_unlock;
	}
	record_len = TAG_WAL_HEADER_LEN + (size_t)payload_len;
	if (wal->active_segment != NULL && wal->active_segment->records > 0 &&
	    wal->active_segment->bytes + record_len > wal->segment_max_bytes) {
		r = cachetag_wal_rotate_locked(wal);
		if (r != 0)
			goto out_unlock;
	}
	memset(header, 0, sizeof header);
	header[0] = 'V';
	header[1] = 'T';
	header[2] = 'W';
	header[3] = '1';
	header[4] = 1;
	header[5] = type;
	seq = wal->next_seq++;
	cachetag_le64enc(header + 8, seq);
	cachetag_le64enc(header + 16, payload_len);
	checksum = cachetag_wal_record_checksum(header, payload, payload_len);
	cachetag_le64enc(header + TAG_WAL_HEADER_CSUM, checksum);
	r = cachetag_wal_write_all(wal->fd, header, TAG_WAL_HEADER_LEN);
	if (r == 0 && payload_len > 0)
		r = cachetag_wal_write_all(wal->fd,
		    (const unsigned char *)payload, (size_t)payload_len);
	/* Grouped remains a compatibility spelling until real group commit exists. */
	if (r == 0 && fsync(wal->fd) != 0)
		r = errno;
	if (r != 0)
		goto fail_unlock;
	wal->records++;
	wal->bytes += record_len;
	if (wal->active_segment != NULL) {
		wal->active_segment->records++;
		wal->active_segment->bytes += record_len;
	}
	if (seqp != NULL)
		*seqp = seq;
	AZ(pthread_mutex_unlock(&wal->mtx));
	return (0);
fail_unlock:
	r = cachetag_wal_fail_locked(wal, r);
out_unlock:
	AZ(pthread_mutex_unlock(&wal->mtx));
	return (r);
}

int
cachetag_wal_checkpoint_due(struct cachetag_wal *wal)
{
	struct cachetag_wal_segment *seg;
	int due;

	if (wal == NULL)
		return (0);
	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	due = 0;
	AZ(pthread_mutex_lock(&wal->mtx));
	for (seg = wal->segments; seg != NULL; seg = seg->next) {
		if (seg->last_seq != 0 &&
		    seg->last_seq > wal->checkpoint_wal_sequence) {
			due = 1;
			break;
		}
	}
	AZ(pthread_mutex_unlock(&wal->mtx));
	return (due);
}

int
cachetag_wal_recovery_checkpoint_due(struct cachetag_wal *wal)
{
	uint64_t last;
	int due;

	if (wal == NULL)
		return (0);
	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	AZ(pthread_mutex_lock(&wal->mtx));
	last = wal->next_seq == 0 ? 0 : wal->next_seq - 1;
	due = wal->checkpoint_relpath == NULL ||
	    last > wal->checkpoint_wal_sequence;
	AZ(pthread_mutex_unlock(&wal->mtx));
	return (due);
}

static uint64_t
cachetag_wal_namespace_digest(const struct cachetag_wal *wal)
{

	return (cachetag_wal_checksum(wal->namespace, strlen(wal->namespace)));
}

static void
cachetag_wal_checkpoint_header(struct cachetag_wal *wal,
    const struct cachetag_wal_checkpoint_meta *meta, uint64_t generation,
    unsigned char header[TAG_CHECKPOINT_HEADER_LEN])
{

	memset(header, 0, TAG_CHECKPOINT_HEADER_LEN);
	memcpy(header, "VTC1", 4);
	cachetag_le16enc(header + 4, 1);
	cachetag_le16enc(header + 6, TAG_CHECKPOINT_HEADER_LEN);
	cachetag_le64enc(header + 8, generation);
	cachetag_le64enc(header + 16, wal->root_id_hi);
	cachetag_le64enc(header + 24, wal->root_id_lo);
	cachetag_le64enc(header + 32, cachetag_wal_namespace_digest(wal));
	cachetag_le64enc(header + 40, meta->purge_sequence);
	cachetag_le64enc(header + 48, meta->wal_sequence);
	cachetag_le64enc(header + 56, meta->hard_floor);
	cachetag_le64enc(header + 64, meta->soft_floor);
	cachetag_le64enc(header + 72, meta->entries);
}

int
cachetag_wal_checkpoint(struct cachetag_wal *wal,
    struct cachetag_wal_checkpoint_meta *meta,
    cachetag_wal_checkpoint_next_f *next, void *priv)
{
	struct cachetag_wal_segment *seg, **segp, *collected = NULL, *seg2;
	struct cachetag_wal_checkpoint_entry ent;
	XXH3_state_t *state = NULL;
	unsigned char header[TAG_CHECKPOINT_HEADER_LEN];
	unsigned char encoded[TAG_CHECKPOINT_ENTRY_LEN], footer[8];
	unsigned char *write_buffer = NULL;
	char *tmp_path = NULL, *final_path = NULL, *final_rel = NULL;
	char *old_checkpoint = NULL, *path;
	uint64_t checksum, generation, emitted, bytes;
	size_t write_buffer_used = 0;
	int fd = -1, n, r;

	if (wal == NULL)
		return (0);
	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	AN(meta);
	AN(next);
	if (meta->entries >
	    (UINT64_MAX - TAG_CHECKPOINT_HEADER_LEN - TAG_CHECKPOINT_FOOTER_LEN) /
	    TAG_CHECKPOINT_ENTRY_LEN)
		return (EFBIG);
	AZ(pthread_mutex_lock(&wal->mtx));
	r = cachetag_wal_prepare_locked(wal);
	if (r != 0)
		goto out;
	if (!wal->replayed) {
		r = EBUSY;
		goto fail;
	}
	meta->wal_sequence = wal->next_seq == 0 ? 0 : wal->next_seq - 1;
	generation = wal->generation + 1;
	final_rel = cachetag_wal_checkpoint_relpath(generation);
	tmp_path = cachetag_path_join(wal->root, "checkpoint.tmp");
	final_path = final_rel != NULL ?
	    cachetag_wal_relpath_to_path(wal->root, final_rel) : NULL;
	if (final_rel == NULL || tmp_path == NULL || final_path == NULL) {
		r = ENOMEM;
		goto fail;
	}
	fd = open(tmp_path, O_WRONLY | O_CREAT | O_TRUNC, 0640);
	if (fd < 0) {
		r = errno;
		goto fail;
	}
	state = XXH3_createState();
	write_buffer = malloc(TAG_CHECKPOINT_WRITE_BUFFER_BYTES);
	if (state == NULL || write_buffer == NULL ||
	    XXH3_64bits_reset(state) == XXH_ERROR) {
		r = ENOMEM;
		goto fail;
	}
	cachetag_wal_checkpoint_header(wal, meta, generation, header);
	r = cachetag_wal_write_all(fd, header, sizeof header);
	if (r != 0)
		goto fail;
	if (XXH3_64bits_update(state, header, sizeof header) == XXH_ERROR) {
		r = EIO;
		goto fail;
	}
	for (emitted = 0;; emitted++) {
		n = next(priv, &ent);
		if (n == 0)
			break;
		if (n < 0) {
			r = -n;
			goto fail;
		}
		if (n != 1 || emitted >= meta->entries ||
		    ent.fold == 0 || ent.fold == UINT64_MAX ||
		    (ent.hard_sequence == 0 && ent.soft_sequence == 0) ||
		    ent.hard_sequence > meta->purge_sequence ||
		    ent.soft_sequence > meta->purge_sequence) {
			r = EINVAL;
			goto fail;
		}
		cachetag_le64enc(encoded, ent.fold);
		cachetag_le64enc(encoded + 8, ent.hard_sequence);
		cachetag_le64enc(encoded + 16, ent.soft_sequence);
		if (XXH3_64bits_update(state, encoded, sizeof encoded) == XXH_ERROR) {
			r = EIO;
			goto fail;
		}
		if (write_buffer_used + sizeof encoded >
		    TAG_CHECKPOINT_WRITE_BUFFER_BYTES) {
			r = cachetag_wal_write_all(fd, write_buffer,
			    write_buffer_used);
			if (r != 0)
				goto fail;
			write_buffer_used = 0;
		}
		memcpy(write_buffer + write_buffer_used, encoded, sizeof encoded);
		write_buffer_used += sizeof encoded;
	}
	if (emitted != meta->entries) {
		r = EINVAL;
		goto fail;
	}
	if (write_buffer_used != 0) {
		r = cachetag_wal_write_all(fd, write_buffer, write_buffer_used);
		if (r != 0)
			goto fail;
	}
	checksum = XXH3_64bits_digest(state);
	cachetag_le64enc(footer, checksum);
	r = cachetag_wal_write_all(fd, footer, sizeof footer);
	if (r != 0 || fsync(fd) != 0) {
		if (r == 0)
			r = errno;
		goto fail;
	}
	if (close(fd) != 0) {
		fd = -1;
		r = errno;
		goto fail;
	}
	fd = -1;
	if (rename(tmp_path, final_path) != 0) {
		r = errno;
		goto fail;
	}
	r = cachetag_wal_fsync_dir(wal->root);
	if (r != 0)
		goto fail;
	if (wal->active_segment != NULL && wal->active_segment->records != 0) {
		r = cachetag_wal_rotate_locked(wal);
		if (r != 0)
			goto fail;
	}
	for (segp = &wal->segments; *segp != NULL;) {
		seg = *segp;
		if (seg->last_seq != 0 &&
		    seg->last_seq <= meta->wal_sequence) {
			*segp = seg->next;
			seg->next = collected;
			collected = seg;
			continue;
		}
		segp = &seg->next;
	}
	old_checkpoint = wal->checkpoint_relpath;
	wal->checkpoint_relpath = final_rel;
	final_rel = NULL;
	wal->checkpoint_generation = generation;
	wal->checkpoint_purge_sequence = meta->purge_sequence;
	wal->checkpoint_wal_sequence = meta->wal_sequence;
	wal->checkpoint_hard_floor = meta->hard_floor;
	wal->checkpoint_soft_floor = meta->soft_floor;
	wal->checkpoint_entries = meta->entries;
	bytes = TAG_CHECKPOINT_HEADER_LEN +
	    meta->entries * TAG_CHECKPOINT_ENTRY_LEN + TAG_CHECKPOINT_FOOTER_LEN;
	wal->checkpoint_bytes = bytes;
	wal->checkpoint_checksum = checksum;
	r = cachetag_wal_manifest_publish_locked(wal);
	if (r != 0)
		goto out;
	wal->checkpoint_publications++;
	for (seg = collected; seg != NULL; seg = seg2) {
		seg2 = seg->next;
		path = cachetag_wal_relpath_to_path(wal->root, seg->relpath);
		if (path != NULL && (unlink(path) == 0 || errno == ENOENT))
			wal->checkpoint_segments_collected++;
		free(path);
		cachetag_wal_segment_free(seg);
	}
	collected = NULL;
	if (old_checkpoint != NULL) {
		path = cachetag_wal_relpath_to_path(wal->root, old_checkpoint);
		if (path != NULL)
			(void)unlink(path);
		free(path);
	}
	free(old_checkpoint);
	old_checkpoint = NULL;
	(void)cachetag_wal_fsync_dir(wal->wal_dir);
	(void)cachetag_wal_fsync_dir(wal->root);
	r = 0;
	goto out;
fail:
	r = cachetag_wal_fail_locked(wal, r);
out:
	if (fd >= 0)
		(void)close(fd);
	if (state != NULL)
		XXH3_freeState(state);
	free(write_buffer);
	for (seg = collected; seg != NULL; seg = seg2) {
		seg2 = seg->next;
		cachetag_wal_segment_free(seg);
	}
	free(old_checkpoint);
	free(final_rel);
	free(final_path);
	free(tmp_path);
	AZ(pthread_mutex_unlock(&wal->mtx));
	return (r);
}

static int
cachetag_wal_read_file(const char *path, char **bufp, size_t *lenp)
{
	struct stat st;
	char *buf;
	int fd, r = 0;
	ssize_t n;
	size_t off = 0;

	fd = open(path, O_RDONLY);
	if (fd < 0)
		return (errno);
	if (fstat(fd, &st) != 0) {
		r = errno;
		(void)close(fd);
		return (r);
	}
	if (st.st_size < 0 || (uintmax_t)st.st_size > SIZE_MAX - 1) {
		(void)close(fd);
		return (EFBIG);
	}
	buf = malloc((size_t)st.st_size + 1);
	if (buf == NULL) {
		(void)close(fd);
		return (ENOMEM);
	}
	while (off < (size_t)st.st_size) {
		n = read(fd, buf + off, (size_t)st.st_size - off);
		if (n < 0) {
			if (errno == EINTR)
				continue;
			r = errno;
			break;
		}
		if (n == 0) {
			r = EIO;
			break;
		}
		off += (size_t)n;
	}
	if (close(fd) != 0 && r == 0)
		r = errno;
	if (r != 0) {
		free(buf);
		return (r);
	}
	buf[off] = '\0';
	*bufp = buf;
	*lenp = off;
	return (0);
}

static int
cachetag_wal_segment_is_manifested(const struct cachetag_wal *wal,
    const char *name)
{
	struct cachetag_wal_segment *seg;
	const char *base;

	for (seg = wal->segments; seg != NULL; seg = seg->next) {
		base = strrchr(seg->relpath, '/');
		base = base == NULL ? seg->relpath : base + 1;
		if (strcmp(base, name) == 0)
			return (1);
	}
	return (0);
}

static int
cachetag_wal_manifest_orphan_gc_locked(struct cachetag_wal *wal)
{
	DIR *d;
	struct dirent *de;
	char *path;
	int r, removed;

	r = 0;
	removed = 0;
	d = opendir(wal->wal_dir);
	if (d != NULL) {
		while ((de = readdir(d)) != NULL) {
			if (!cachetag_has_suffix(de->d_name, ".vtw") ||
			    cachetag_wal_segment_is_manifested(wal, de->d_name))
				continue;
			path = cachetag_path_join(wal->wal_dir, de->d_name);
			if (path == NULL) {
				r = ENOMEM;
				break;
			}
			if (unlink(path) == 0 || errno == ENOENT) {
				wal->orphan_files_collected++;
				removed = 1;
			} else
				r = errno;
			free(path);
			if (r != 0)
				break;
		}
		(void)closedir(d);
	}
	if (r != 0)
		return (r);
	d = opendir(wal->root);
	if (d == NULL)
		return (errno);
	while ((de = readdir(d)) != NULL) {
		if (strcmp(de->d_name, "checkpoint.tmp") != 0 &&
		    !(strncmp(de->d_name, "checkpoint-", 11) == 0 &&
		    cachetag_has_suffix(de->d_name, ".vtc")))
			continue;
		if (wal->checkpoint_relpath != NULL &&
		    strcmp(de->d_name, wal->checkpoint_relpath) == 0)
			continue;
		path = cachetag_path_join(wal->root, de->d_name);
		if (path == NULL) {
			r = ENOMEM;
			break;
		}
		if (unlink(path) == 0 || errno == ENOENT) {
			wal->orphan_files_collected++;
			removed = 1;
		} else
			r = errno;
		free(path);
		if (r != 0)
			break;
	}
	(void)closedir(d);
	if (r == 0 && removed) {
		r = cachetag_wal_fsync_dir(wal->wal_dir);
		if (r == 0)
			r = cachetag_wal_fsync_dir(wal->root);
	}
	return (r);
}

static int
cachetag_wal_manifest_load_locked(struct cachetag_wal *wal)
{
	struct cachetag_wal_segment *seg, *scan;
	char *path, *buf = NULL, *line, *next, *checksum_line = NULL;
	char rel[256], namespace_value[256], root_hex[33], root_half[17], *end;
	size_t len = 0, body_len;
	uint64_t checksum, expected, first, last, bytes, max_seq = 0;
	uint64_t checkpoint_generation, checkpoint_purge, checkpoint_wal;
	uint64_t checkpoint_hard, checkpoint_soft, checkpoint_entries;
	uint64_t checkpoint_bytes, checkpoint_checksum, range_next;
	int has_orphan_record = 0, r = 0, saw_magic = 0, saw_version = 0;
	int consumed, saw_namespace = 0, saw_root = 0;
	int saw_checkpoint = 0, active_count = 0;

	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	path = cachetag_path_join(wal->root, "manifest");
	if (path == NULL)
		return (cachetag_wal_fail_locked(wal, ENOMEM));
	r = cachetag_wal_read_file(path, &buf, &len);
	free(path);
	if (r == ENOENT || r == ENOTDIR) {
		r = cachetag_wal_orphan_files_have_records(wal,
		    &has_orphan_record);
		if (r != 0)
			return (cachetag_wal_fail_locked(wal, r));
		if (has_orphan_record)
			return (cachetag_wal_fail_locked(wal, r));
		return (ENODATA);
	}
	if (r != 0)
		return (cachetag_wal_fail_locked(wal, r));
	checksum_line = strstr(buf, "checksum=xxh3:");
	if (checksum_line == NULL || checksum_line == buf ||
	    checksum_line[-1] != '\n') {
		free(buf);
		return (cachetag_wal_fail_locked(wal, EINVAL));
	}
	body_len = (size_t)(checksum_line - buf);
	if (strlen(checksum_line) < strlen("checksum=xxh3:") + 16) {
		free(buf);
		return (cachetag_wal_fail_locked(wal, EINVAL));
	}
	expected = strtoull(checksum_line + strlen("checksum=xxh3:"), &end,
	    16);
	if (end != checksum_line + strlen("checksum=xxh3:") + 16) {
		free(buf);
		return (cachetag_wal_fail_locked(wal, EINVAL));
	}
	checksum = cachetag_wal_checksum(buf, body_len);
	if (checksum != expected) {
		free(buf);
		return (cachetag_wal_fail_locked(wal, EINVAL));
	}
	cachetag_wal_segments_clear(wal);
	for (line = buf; line < checksum_line; line = next) {
		next = strchr(line, '\n');
		if (next == NULL || next > checksum_line)
			break;
		*next++ = '\0';
		if (strcmp(line, "magic=VTM1") == 0) {
			saw_magic = 1;
			continue;
		}
		if (strcmp(line, "version=1") == 0) {
			saw_version = 1;
			continue;
		}
		if (strncmp(line, "generation=", 11) == 0) {
			wal->generation = strtoull(line + 11, NULL, 10);
			continue;
		}
		if (strncmp(line, "namespace=", 10) == 0) {
			if (snprintf(namespace_value, sizeof namespace_value, "%s",
			    line + 10) < 0 || strcmp(namespace_value, wal->namespace) != 0) {
				free(buf);
				return (cachetag_wal_fail_locked(wal, EINVAL));
			}
			saw_namespace = 1;
			continue;
		}
		if (strncmp(line, "root_id=", 8) == 0) {
			if (strlen(line + 8) != 32) {
				free(buf);
				return (cachetag_wal_fail_locked(wal, EINVAL));
			}
			memcpy(root_hex, line + 8, 32);
			root_hex[32] = '\0';
			memcpy(root_half, root_hex, 16);
			root_half[16] = '\0';
			wal->root_id_hi = strtoull(root_half, &end, 16);
			if (end != root_half + 16) {
				free(buf);
				return (cachetag_wal_fail_locked(wal, EINVAL));
			}
			memcpy(root_half, root_hex + 16, 16);
			root_half[16] = '\0';
			wal->root_id_lo = strtoull(root_half, &end, 16);
			if (end != root_half + 16 ||
			    (wal->root_id_hi == 0 && wal->root_id_lo == 0)) {
				free(buf);
				return (cachetag_wal_fail_locked(wal, EINVAL));
			}
			saw_root = 1;
			continue;
		}
		if (strncmp(line, "created_unix_nsec=", 18) == 0)
			continue;
		if (strncmp(line, "last_sequence=", 14) == 0) {
			max_seq = strtoull(line + 14, NULL, 10);
			continue;
		}
		if (strncmp(line, "checkpoint=", 11) == 0) {
			char *validated_path;

			consumed = 0;
			if (saw_checkpoint || sscanf(line + 11,
			    "%255[^,],%" SCNu64 ",%" SCNu64 ",%" SCNu64
			    ",%" SCNu64 ",%" SCNu64 ",%" SCNu64 ",%" SCNu64
			    ",%" SCNu64 "%n", rel, &checkpoint_generation,
			    &checkpoint_purge, &checkpoint_wal, &checkpoint_hard,
			    &checkpoint_soft, &checkpoint_entries, &checkpoint_bytes,
			    &checkpoint_checksum, &consumed) != 9 ||
			    line[11 + consumed] != '\0') {
				free(buf);
				return (cachetag_wal_fail_locked(wal, EINVAL));
			}
			validated_path = cachetag_wal_relpath_to_path(wal->root, rel);
			if (validated_path == NULL ||
			    !cachetag_has_suffix(rel, ".vtc")) {
				free(validated_path);
				free(buf);
				return (cachetag_wal_fail_locked(wal, EINVAL));
			}
			free(validated_path);
			wal->checkpoint_relpath = strdup(rel);
			if (wal->checkpoint_relpath == NULL) {
				free(buf);
				return (cachetag_wal_fail_locked(wal, ENOMEM));
			}
			wal->checkpoint_generation = checkpoint_generation;
			wal->checkpoint_purge_sequence = checkpoint_purge;
			wal->checkpoint_wal_sequence = checkpoint_wal;
			wal->checkpoint_hard_floor = checkpoint_hard;
			wal->checkpoint_soft_floor = checkpoint_soft;
			wal->checkpoint_entries = checkpoint_entries;
			wal->checkpoint_bytes = checkpoint_bytes;
			wal->checkpoint_checksum = checkpoint_checksum;
			saw_checkpoint = 1;
			continue;
		}
		if (strncmp(line, "wal=", 4) == 0) {
			char *validated_path;

			consumed = 0;
			if (sscanf(line + 4, "%255[^,],%" SCNu64 ",%" SCNu64
			    ",%" SCNu64 "%n", rel, &first,
			    &last, &bytes, &consumed) != 4 ||
			    line[4 + consumed] != '\0') {
				free(buf);
				return (cachetag_wal_fail_locked(wal, EINVAL));
			}
			validated_path = cachetag_wal_relpath_to_path(wal->root,
			    rel);
			if (validated_path == NULL) {
				free(buf);
				return (cachetag_wal_fail_locked(wal, EINVAL));
			}
			free(validated_path);
			seg = calloc(1, sizeof *seg);
			if (seg == NULL) {
				free(buf);
				return (cachetag_wal_fail_locked(wal, ENOMEM));
			}
			seg->relpath = strdup(rel);
			if (seg->relpath == NULL) {
				cachetag_wal_segment_free(seg);
				free(buf);
				return (cachetag_wal_fail_locked(wal, ENOMEM));
			}
			seg->first_seq = first;
			seg->last_seq = last;
			seg->bytes = bytes;
			cachetag_wal_segment_append(wal, seg);
			continue;
		}
		if (strncmp(line, "x-", 2) == 0)
			continue;
		free(buf);
		return (cachetag_wal_fail_locked(wal, EINVAL));
	}
	free(buf);
	if (!saw_magic || !saw_version || !saw_namespace || !saw_root)
		return (cachetag_wal_fail_locked(wal, EINVAL));
	range_next = saw_checkpoint ? wal->checkpoint_wal_sequence + 1 : 1;
	if (saw_checkpoint && range_next == 0)
		return (cachetag_wal_fail_locked(wal, EINVAL));
	for (scan = wal->segments; scan != NULL; scan = scan->next) {
		if (scan->first_seq != range_next ||
		    (scan->last_seq != 0 && scan->last_seq < scan->first_seq) ||
		    (scan->last_seq == 0 && scan->next != NULL))
			return (cachetag_wal_fail_locked(wal, EINVAL));
		if (scan->last_seq == 0) {
			active_count++;
			break;
		}
		if (scan->last_seq == UINT64_MAX)
			return (cachetag_wal_fail_locked(wal, EINVAL));
		range_next = scan->last_seq + 1;
	}
	if (active_count > 1)
		return (cachetag_wal_fail_locked(wal, EINVAL));
	if (wal->active_segment == NULL) {
		if (max_seq < wal->checkpoint_wal_sequence)
			max_seq = wal->checkpoint_wal_sequence;
		seg = cachetag_wal_segment_new(max_seq + 1);
		if (seg == NULL)
			return (cachetag_wal_fail_locked(wal, ENOMEM));
		cachetag_wal_segment_append(wal, seg);
	}
	wal->next_seq = max_seq + 1;
	r = cachetag_wal_manifest_orphan_gc_locked(wal);
	if (r != 0)
		return (cachetag_wal_fail_locked(wal, r));
	return (0);
}

static int
cachetag_wal_replay_checkpoint(struct cachetag_wal *wal,
    cachetag_wal_checkpoint_begin_f *begin,
    cachetag_wal_checkpoint_entry_f *entry, void *priv)
{
	struct cachetag_wal_checkpoint_meta meta;
	struct cachetag_wal_checkpoint_entry ent;
	XXH3_state_t *state;
	unsigned char header[TAG_CHECKPOINT_HEADER_LEN];
	unsigned char encoded[TAG_CHECKPOINT_ENTRY_LEN], footer[8], extra;
	char *path;
	uint64_t checksum, u;
	size_t nread;
	int fd, r;

	if (wal->checkpoint_relpath == NULL)
		return (0);
	path = cachetag_wal_relpath_to_path(wal->root, wal->checkpoint_relpath);
	if (path == NULL)
		return (EINVAL);
	fd = open(path, O_RDONLY);
	free(path);
	if (fd < 0)
		return (errno);
	state = XXH3_createState();
	if (state == NULL) {
		(void)close(fd);
		return (ENOMEM);
	}
	r = cachetag_wal_read(fd, header, sizeof header, &nread);
	if (r == 0 && nread != sizeof header)
		r = EIO;
	if (r != 0 || memcmp(header, "VTC1", 4) != 0 ||
	    cachetag_le16dec(header + 4) != 1 ||
	    cachetag_le16dec(header + 6) != TAG_CHECKPOINT_HEADER_LEN ||
	    cachetag_le64dec(header + 8) != wal->checkpoint_generation ||
	    cachetag_le64dec(header + 16) != wal->root_id_hi ||
	    cachetag_le64dec(header + 24) != wal->root_id_lo ||
	    cachetag_le64dec(header + 32) != cachetag_wal_namespace_digest(wal) ||
	    cachetag_le64dec(header + 40) != wal->checkpoint_purge_sequence ||
	    cachetag_le64dec(header + 48) != wal->checkpoint_wal_sequence ||
	    cachetag_le64dec(header + 56) != wal->checkpoint_hard_floor ||
	    cachetag_le64dec(header + 64) != wal->checkpoint_soft_floor ||
	    cachetag_le64dec(header + 72) != wal->checkpoint_entries ||
	    cachetag_le64dec(header + 80) != 0 ||
	    wal->checkpoint_entries >
	    (UINT64_MAX - TAG_CHECKPOINT_HEADER_LEN - TAG_CHECKPOINT_FOOTER_LEN) /
	    TAG_CHECKPOINT_ENTRY_LEN ||
	    wal->checkpoint_bytes != TAG_CHECKPOINT_HEADER_LEN +
	    wal->checkpoint_entries * TAG_CHECKPOINT_ENTRY_LEN +
	    TAG_CHECKPOINT_FOOTER_LEN) {
		if (r == 0)
			r = EINVAL;
		goto out;
	}
	if (XXH3_64bits_reset(state) == XXH_ERROR ||
	    XXH3_64bits_update(state, header, sizeof header) == XXH_ERROR) {
		r = EIO;
		goto out;
	}
	memset(&meta, 0, sizeof meta);
	meta.purge_sequence = wal->checkpoint_purge_sequence;
	meta.wal_sequence = wal->checkpoint_wal_sequence;
	meta.hard_floor = wal->checkpoint_hard_floor;
	meta.soft_floor = wal->checkpoint_soft_floor;
	meta.entries = wal->checkpoint_entries;
	if (begin != NULL) {
		r = begin(priv, &meta);
		if (r != 0)
			goto out;
	}
	for (u = 0; u < meta.entries; u++) {
		r = cachetag_wal_read(fd, encoded, sizeof encoded, &nread);
		if (r == 0 && nread != sizeof encoded)
			r = EIO;
		if (r != 0 ||
		    XXH3_64bits_update(state, encoded, sizeof encoded) == XXH_ERROR) {
			if (r == 0)
				r = EIO;
			goto out;
		}
		ent.fold = cachetag_le64dec(encoded);
		ent.hard_sequence = cachetag_le64dec(encoded + 8);
		ent.soft_sequence = cachetag_le64dec(encoded + 16);
		if (ent.fold == 0 || ent.fold == UINT64_MAX ||
		    (ent.hard_sequence == 0 && ent.soft_sequence == 0) ||
		    ent.hard_sequence > meta.purge_sequence ||
		    ent.soft_sequence > meta.purge_sequence) {
			r = EINVAL;
			goto out;
		}
		if (entry != NULL) {
			r = entry(priv, &ent);
			if (r != 0)
				goto out;
		}
	}
	r = cachetag_wal_read(fd, footer, sizeof footer, &nread);
	if (r == 0 && nread != sizeof footer)
		r = EIO;
	if (r != 0)
		goto out;
	checksum = XXH3_64bits_digest(state);
	if (cachetag_le64dec(footer) != checksum ||
	    checksum != wal->checkpoint_checksum) {
		r = EINVAL;
		goto out;
	}
	r = cachetag_wal_read(fd, &extra, 1, &nread);
	if (r == 0 && nread != 0)
		r = EINVAL;
out:
	XXH3_freeState(state);
	if (close(fd) != 0 && r == 0)
		r = errno;
	return (r);
}

static int
cachetag_wal_replay_segment(struct cachetag_wal *wal, struct cachetag_wal_segment *seg,
    cachetag_wal_replay_f *func, void *priv, uint64_t *max_seqp)
{
	unsigned char hdr[TAG_WAL_HEADER_LEN], *payload = NULL;
	struct cachetag_wal_record rec;
	char *path;
	uint64_t payload_len, record_bytes, stored, checksum, valid_bytes = 0;
	uint64_t valid_records = 0;
	size_t nread;
	int fd, r = 0;

	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	AN(seg);
	if (*max_seqp == UINT64_MAX || seg->first_seq != *max_seqp + 1)
		return (EINVAL);
	path = cachetag_wal_relpath_to_path(wal->root, seg->relpath);
	if (path == NULL)
		return (EINVAL);
	fd = open(path, O_RDONLY);
	free(path);
	if (fd < 0)
		return (errno);
	for (;;) {
		r = cachetag_wal_read(fd, hdr, sizeof hdr, &nread);
		if (r == 0 && nread == 0)
			break;
		if (r == 0 && nread != sizeof hdr)
			r = EIO;
		if (r != 0) {
			if (seg->last_seq == 0 && r == EIO)
				r = 0;
			break;
		}
		if (memcmp(hdr, "VTW1", 4) != 0 || hdr[4] != 1) {
			r = EINVAL;
			if (seg->last_seq == 0)
				r = 0;
			break;
		}
		payload_len = cachetag_le64dec(hdr + 16);
		if (payload_len > SIZE_MAX - TAG_WAL_HEADER_CSUM) {
			r = EFBIG;
			break;
		}
		record_bytes = TAG_WAL_HEADER_LEN + payload_len;
		payload = payload_len > 0 ? malloc((size_t)payload_len) : NULL;
		if (payload_len > 0 && payload == NULL) {
			r = ENOMEM;
			break;
		}
		if (payload_len > 0) {
			r = cachetag_wal_read(fd, payload,
			    (size_t)payload_len, &nread);
			if (r == 0 && nread != (size_t)payload_len)
				r = EIO;
			if (r != 0) {
				if (seg->last_seq == 0 && r == EIO)
					r = 0;
				break;
			}
		}
		stored = cachetag_le64dec(hdr + TAG_WAL_HEADER_CSUM);
		checksum = cachetag_wal_record_checksum(hdr, payload, payload_len);
		if (checksum != stored) {
			r = EINVAL;
			if (seg->last_seq == 0)
				r = 0;
			break;
		}
		memset(&rec, 0, sizeof rec);
		rec.type = hdr[5];
		rec.sequence = cachetag_le64dec(hdr + 8);
		if (*max_seqp == UINT64_MAX || rec.sequence != *max_seqp + 1) {
			r = EINVAL;
			break;
		}
		rec.payload = payload;
		rec.payload_len = payload_len;
		if (func != NULL) {
			r = func(priv, &rec);
			if (r != 0)
				break;
		}
		if (rec.sequence > *max_seqp)
			*max_seqp = rec.sequence;
		valid_records++;
		valid_bytes += record_bytes;
		wal->replay_records++;
		free(payload);
		payload = NULL;
	}
	free(payload);
	if (close(fd) != 0 && r == 0)
		r = errno;
	if (seg->last_seq == 0) {
		seg->records = valid_records;
		seg->bytes = valid_bytes;
	} else if (r == 0 && (seg->last_seq != *max_seqp ||
	    seg->bytes != valid_bytes))
		r = EINVAL;
	return (r);
}

int
cachetag_wal_replay(struct cachetag_wal *wal, cachetag_wal_replay_f *func,
    cachetag_wal_checkpoint_begin_f *checkpoint_begin,
    cachetag_wal_checkpoint_entry_f *checkpoint_entry, void *priv)
{
	struct cachetag_wal_segment *seg;
	uint64_t max_seq = 0;
	int r = 0;

	if (wal == NULL)
		return (0);
	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	AZ(pthread_mutex_lock(&wal->mtx));
	if (wal->replayed) {
		AZ(pthread_mutex_unlock(&wal->mtx));
		return (0);
	}
	r = cachetag_wal_manifest_load_locked(wal);
	if (r == ENODATA) {
		wal->replayed = 1;
		r = 0;
		goto out;
	}
	if (r != 0)
		goto out;
	if (wal->checkpoint_relpath != NULL) {
		r = cachetag_wal_replay_checkpoint(wal, checkpoint_begin,
		    checkpoint_entry, priv);
		if (r != 0)
			goto out;
		max_seq = wal->checkpoint_wal_sequence;
	}
	for (seg = wal->segments; seg != NULL; seg = seg->next) {
		r = cachetag_wal_replay_segment(wal, seg, func, priv, &max_seq);
		if (r != 0)
			break;
	}
	if (r == 0 && max_seq + 1 > wal->next_seq)
		wal->next_seq = max_seq + 1;
	if (r == 0)
		wal->replayed = 1;
out:
	if (r != 0)
		r = cachetag_wal_fail_locked(wal, r);
	AZ(pthread_mutex_unlock(&wal->mtx));
	return (r);
}

void
cachetag_wal_snapshot(struct cachetag_wal *wal, struct cachetag_wal_stats *stats)
{

	AN(stats);
	memset(stats, 0, sizeof *stats);
	if (wal == NULL)
		return;
	CHECK_OBJ_NOTNULL(wal, TAG_WAL_MAGIC);
	AZ(pthread_mutex_lock(&wal->mtx));
	stats->records = wal->records;
	stats->bytes = wal->bytes;
	stats->failures = wal->failures;
	stats->checkpoint_entries = wal->checkpoint_entries;
	stats->checkpoint_wal_sequence = wal->checkpoint_wal_sequence;
	stats->checkpoint_bytes = wal->checkpoint_bytes;
	stats->checkpoint_publications = wal->checkpoint_publications;
	stats->checkpoint_segments_collected =
	    wal->checkpoint_segments_collected;
	stats->orphan_files_collected = wal->orphan_files_collected;
	stats->replay_records = wal->replay_records;
	stats->degraded = wal->degraded;
	AZ(pthread_mutex_unlock(&wal->mtx));
}
