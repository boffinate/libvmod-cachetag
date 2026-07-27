/*-
 * SPDX-License-Identifier: MPL-2.0
 *
 * Shared internal helpers for the cachetag VMOD.
 */

#ifndef CACHETAG_UTIL_H
#define CACHETAG_UTIL_H

#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#define TAG_INLINE_KEYS 4

struct objcore;

/* Non-installed vinyld internal (cache_objhead.h), declared locally. */
void HSH_Kill(struct objcore *);

static inline void
cachetag_le16enc(unsigned char *p, uint16_t v)
{

	p[0] = (unsigned char)v;
	p[1] = (unsigned char)(v >> 8);
}

static inline uint16_t
cachetag_le16dec(const unsigned char *p)
{

	return ((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

static inline void
cachetag_le32enc(unsigned char *p, uint32_t v)
{
	unsigned u;

	for (u = 0; u < 4; u++)
		p[u] = (unsigned char)(v >> (u * 8));
}

static inline uint32_t
cachetag_le32dec(const unsigned char *p)
{
	uint32_t v = 0;
	unsigned u;

	for (u = 0; u < 4; u++)
		v |= (uint32_t)p[u] << (u * 8);
	return (v);
}

static inline void
cachetag_le64enc(unsigned char *p, uint64_t v)
{
	unsigned u;

	for (u = 0; u < 8; u++)
		p[u] = (unsigned char)(v >> (u * 8));
}

static inline uint64_t
cachetag_le64dec(const unsigned char *p)
{
	uint64_t v = 0;
	unsigned u;

	for (u = 0; u < 8; u++)
		v |= (uint64_t)p[u] << (u * 8);
	return (v);
}

static inline char *
cachetag_path_join(const char *a, const char *b)
{
	char *p;
	size_t al, bl, need, slash;

	al = strlen(a);
	bl = strlen(b);
	slash = al > 0 && a[al - 1] != '/';
	need = al + slash + bl + 1;
	p = malloc(need);
	if (p == NULL)
		return (NULL);
	memcpy(p, a, al);
	if (slash)
		p[al++] = '/';
	memcpy(p + al, b, bl + 1);
	return (p);
}

static inline int
cachetag_mkdir_existing(const char *path)
{
	struct stat st;

	if (mkdir(path, 0750) == 0)
		return (0);
	if (errno != EEXIST)
		return (errno);
	if (stat(path, &st) != 0)
		return (errno);
	if (!S_ISDIR(st.st_mode))
		return (ENOTDIR);
	return (0);
}

#endif
