/*-
 * SPDX-License-Identifier: MPL-2.0
 */

#include "config.h"

#include <dlfcn.h>
#include <link.h>
#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

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
#include "vcc_cachetag_if.h"
#include "VSC_cachetag.h"

#include "cachetag_util.h"
#include "vmod_cachetag_index.h"

typedef int fellow_object_attr_size_f(void *, const struct objcore *, size_t *);
typedef void fellow_object_attr_fill_f(void *, const struct objcore *, void *,
    size_t);
typedef int fellow_object_attr_visit_f(void *, const void *, size_t);
typedef uintptr_t fellow_object_attr_register_api_f(const struct stevedore *,
    fellow_object_attr_size_f *, fellow_object_attr_fill_f *, void *);
typedef void fellow_object_attr_unregister_api_f(uintptr_t *);
typedef int fellow_object_attr_visit_api_f(struct worker *, struct objcore *,
    fellow_object_attr_visit_f *, void *);
typedef void fellow_object_attr_test_fail_next_visit_api_f(void);

uintptr_t fellow_object_attr_register(const struct stevedore *,
    fellow_object_attr_size_f *, fellow_object_attr_fill_f *, void *)
    __attribute__((weak));
void fellow_object_attr_unregister(uintptr_t *) __attribute__((weak));
int fellow_object_attr_visit(struct worker *, struct objcore *,
    fellow_object_attr_visit_f *, void *) __attribute__((weak));
void fellow_object_attr_test_fail_next_visit(void) __attribute__((weak));

static pthread_mutex_t cachetag_fellow_api_mtx = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t cachetag_fellow_provider_mtx = PTHREAD_MUTEX_INITIALIZER;
static fellow_object_attr_visit_api_f *cachetag_fellow_visitp;
static fellow_object_attr_test_fail_next_visit_api_f
    *cachetag_fellow_fail_next_visitp;

#define CACHETAG_FELLOW_REG_MAGIC	0x6c637472

struct cachetag_fellow_registration {
	unsigned			magic;
	uintptr_t			handle;
	fellow_object_attr_unregister_api_f	*unregister;
	struct cachetag_fellow_registration	*next;
};

struct cachetag_fellow_register_ctx {
	struct cachetag_fellow_registration	*regs;
	fellow_object_attr_size_f		*size;
	fellow_object_attr_fill_f		*fill;
	void					*priv;
	unsigned				nreg;
	unsigned				api_seen;
};

static void
cachetag_fellow_register_one(struct cachetag_fellow_register_ctx *rc,
    fellow_object_attr_register_api_f *registerp,
    fellow_object_attr_unregister_api_f *unregisterp,
    fellow_object_attr_visit_api_f *visitp,
    fellow_object_attr_test_fail_next_visit_api_f *fail_next_visitp)
{
	struct cachetag_fellow_registration *reg;
	uintptr_t h;

	AN(rc);
	if (registerp != NULL || unregisterp != NULL)
		rc->api_seen = 1;
	if (registerp == NULL || unregisterp == NULL || visitp == NULL)
		return;
	cachetag_fellow_visitp = visitp;
	if (fail_next_visitp != NULL)
		cachetag_fellow_fail_next_visitp = fail_next_visitp;
	h = registerp(NULL, rc->size, rc->fill, rc->priv);
	if (h == 0)
		return;
	ALLOC_OBJ(reg, CACHETAG_FELLOW_REG_MAGIC);
	if (reg == NULL) {
		unregisterp(&h);
		return;
	}
	reg->handle = h;
	reg->unregister = unregisterp;
	reg->next = rc->regs;
	rc->regs = reg;
	rc->nreg++;
}

static int
cachetag_fellow_register_loaded_cb(struct dl_phdr_info *info, size_t size,
    void *priv)
{
	struct cachetag_fellow_register_ctx *rc = priv;
	fellow_object_attr_register_api_f *registerp;
	fellow_object_attr_unregister_api_f *unregisterp;
	fellow_object_attr_visit_api_f *visitp;
	fellow_object_attr_test_fail_next_visit_api_f *fail_next_visitp;
	void *handle;
	int flags;

	(void)size;
	AN(rc);
	if (info == NULL || info->dlpi_name == NULL ||
	    (strstr(info->dlpi_name, "libvmod_slash") == NULL &&
	    strstr(info->dlpi_name, "_vmod_slash") == NULL))
		return (0);
	flags = RTLD_NOW | RTLD_GLOBAL;
#ifdef RTLD_NOLOAD
	handle = dlopen(info->dlpi_name, flags | RTLD_NOLOAD);
	if (handle == NULL)
#endif
		handle = dlopen(info->dlpi_name, flags);
	if (handle == NULL)
		return (0);
	registerp = (fellow_object_attr_register_api_f *)
	    dlsym(handle, "fellow_object_attr_register");
	unregisterp = (fellow_object_attr_unregister_api_f *)
	    dlsym(handle, "fellow_object_attr_unregister");
	visitp = (fellow_object_attr_visit_api_f *)
	    dlsym(handle, "fellow_object_attr_visit");
	fail_next_visitp = (fellow_object_attr_test_fail_next_visit_api_f *)
	    dlsym(handle, "fellow_object_attr_test_fail_next_visit");
	cachetag_fellow_register_one(rc, registerp, unregisterp, visitp,
	    fail_next_visitp);
	return (0);
}

static void
cachetag_fellow_unregister_all(struct cachetag_fellow_registration **regsp)
{
	struct cachetag_fellow_registration *reg, *next;

	AN(regsp);
	reg = *regsp;
	*regsp = NULL;
	while (reg != NULL) {
		CHECK_OBJ_NOTNULL(reg, CACHETAG_FELLOW_REG_MAGIC);
		next = reg->next;
		if (reg->handle != 0 && reg->unregister != NULL)
			reg->unregister(&reg->handle);
		FREE_OBJ(reg);
		reg = next;
	}
}

static unsigned
cachetag_fellow_register_all(struct cachetag_fellow_registration **regsp,
    fellow_object_attr_size_f *size, fellow_object_attr_fill_f *fill,
    void *priv, unsigned *api_seenp)
{
	struct cachetag_fellow_register_ctx rc[1];
	fellow_object_attr_register_api_f *registerp;
	fellow_object_attr_unregister_api_f *unregisterp;
	fellow_object_attr_visit_api_f *visitp;

	AN(regsp);
	AN(size);
	AN(fill);
	AN(api_seenp);
	memset(rc, 0, sizeof rc);
	rc->size = size;
	rc->fill = fill;
	rc->priv = priv;
	PTOK(pthread_mutex_lock(&cachetag_fellow_api_mtx));
	(void)dl_iterate_phdr(cachetag_fellow_register_loaded_cb, rc);
	if (rc->nreg == 0) {
		registerp = fellow_object_attr_register;
		unregisterp = fellow_object_attr_unregister;
		visitp = fellow_object_attr_visit;
		cachetag_fellow_fail_next_visitp =
		    fellow_object_attr_test_fail_next_visit;
		if (registerp == NULL)
			registerp = (fellow_object_attr_register_api_f *)
			    dlsym(RTLD_DEFAULT, "fellow_object_attr_register");
		if (unregisterp == NULL)
			unregisterp = (fellow_object_attr_unregister_api_f *)
			    dlsym(RTLD_DEFAULT, "fellow_object_attr_unregister");
		if (visitp == NULL)
			visitp = (fellow_object_attr_visit_api_f *)
			    dlsym(RTLD_DEFAULT, "fellow_object_attr_visit");
		cachetag_fellow_register_one(rc, registerp, unregisterp,
		    visitp, NULL);
	}
	*regsp = rc->regs;
	*api_seenp = rc->api_seen;
	PTOK(pthread_mutex_unlock(&cachetag_fellow_api_mtx));
	return (rc->nreg);
}

static int
cachetag_fellow_test_fail_next_visit(void)
{
	fellow_object_attr_test_fail_next_visit_api_f *fail_next_visitp;

	PTOK(pthread_mutex_lock(&cachetag_fellow_api_mtx));
	fail_next_visitp = cachetag_fellow_fail_next_visitp;
	PTOK(pthread_mutex_unlock(&cachetag_fellow_api_mtx));
	if (fail_next_visitp == NULL)
		return (0);
	fail_next_visitp();
	return (1);
}

#define TAG_NAMESPACE_MAGIC	0x7461676e
#define TAG_PENDING_MAGIC	0x74616770
#define CACHETAG_FELLOW_ENVELOPE_MAGIC 0x47415443U
#define CACHETAG_FELLOW_ENVELOPE_VERSION_V1 1U
#define CACHETAG_FELLOW_ENVELOPE_VERSION_SINGLETON 2U
#define CACHETAG_FELLOW_ENVELOPE_HEADER_LEN 16U
#define CACHETAG_FELLOW_RECORD_HEADER_LEN 32U
#define CACHETAG_FELLOW_SINGLETON_LEN 40U

enum cachetag_fellow_attr_corruption {
	TAG_FELLOW_ATTR_CORRUPT_NONE = 0,
	TAG_FELLOW_ATTR_CORRUPT_RECORD_LEN,
	TAG_FELLOW_ATTR_CORRUPT_FOLD_COUNT,
	TAG_FELLOW_ATTR_CORRUPT_ENVELOPE_FLAGS,
	TAG_FELLOW_ATTR_CORRUPT_ENVELOPE_RESERVED,
	TAG_FELLOW_ATTR_CORRUPT_RECORD_FLAGS,
	TAG_FELLOW_ATTR_CORRUPT_DUPLICATE_NAMESPACE
};

struct vmod_cachetag_namespace {
	unsigned		magic;
	const struct vcl	*vcl;
	char			*vcl_name;
	struct cachetag_index	*index;
	pthread_mutex_t		mtx;
	struct cachetag_pending	*pending;
	pid_t			warm_pid;
	uintptr_t		obj_cb;
	pid_t			obj_cb_pid;
	uint64_t		namespace_digest_hi;
	uint64_t		namespace_digest_lo;
	unsigned		fellow_direct_active;
	uint16_t		test_next_fellow_attr_version;
	unsigned		test_next_fellow_attr_bad_length;
	enum cachetag_fellow_attr_corruption
				test_next_fellow_attr_corruption;
	struct VSC_cachetag		*vsc;
	struct vsc_seg		*vsc_seg;
	struct vmod_cachetag_namespace *global_next;
};

static int cachetag_namespace_warm(struct vmod_cachetag_namespace *ns);
static void cachetag_namespace_cold(struct vmod_cachetag_namespace *ns);

struct cachetag_pending {
	unsigned		magic;
	struct vmod_cachetag_namespace *ns;
	struct objcore		*oc;
	unsigned		retries;
	unsigned		consumed;
	unsigned		publication_held;
	unsigned		publication_phase;
	uint64_t		publication_seq;
	unsigned		fellow_attr_included;
	size_t			fellow_attr_record_len;
	unsigned		nkeys;
	unsigned		capkeys;
	struct cachetag_registration_snapshot *keys;
	struct cachetag_registration_snapshot inline_keys[TAG_INLINE_KEYS];
	struct cachetag_pending	*next;
};

static pthread_mutex_t cachetag_global_mtx = PTHREAD_MUTEX_INITIALIZER;
static struct vmod_cachetag_namespace *cachetag_namespaces;
static struct cachetag_fellow_registration *cachetag_fellow_regs;
static pid_t cachetag_fellow_regs_pid;
static pid_t cachetag_fellow_absent_pid;

static int cachetag_fellow_attr_size_cb(void *, const struct objcore *,
    size_t *);
static void cachetag_fellow_attr_fill_cb(void *, const struct objcore *, void *,
    size_t);

static void
cachetag_namespace_global_add(struct vmod_cachetag_namespace *ns)
{

	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	PTOK(pthread_mutex_lock(&cachetag_global_mtx));
	ns->global_next = cachetag_namespaces;
	cachetag_namespaces = ns;
	PTOK(pthread_mutex_unlock(&cachetag_global_mtx));
}

static void
cachetag_fellow_provider_release_if_idle(void)
{
	struct cachetag_fellow_registration *regs = NULL;
	struct vmod_cachetag_namespace *ns;
	unsigned active = 0;

	PTOK(pthread_mutex_lock(&cachetag_fellow_provider_mtx));
	PTOK(pthread_mutex_lock(&cachetag_global_mtx));
	for (ns = cachetag_namespaces; ns != NULL; ns = ns->global_next) {
		PTOK(pthread_mutex_lock(&ns->mtx));
		active = ns->fellow_direct_active;
		PTOK(pthread_mutex_unlock(&ns->mtx));
		if (active)
			break;
	}
	if (!active) {
		if (cachetag_fellow_regs_pid == getpid()) {
			regs = cachetag_fellow_regs;
			cachetag_fellow_regs = NULL;
			cachetag_fellow_regs_pid = 0;
		}
		cachetag_fellow_absent_pid = 0;
	}
	PTOK(pthread_mutex_unlock(&cachetag_global_mtx));
	if (regs != NULL)
		cachetag_fellow_unregister_all(&regs);
	PTOK(pthread_mutex_unlock(&cachetag_fellow_provider_mtx));
}

static void
cachetag_namespace_global_remove(struct vmod_cachetag_namespace *ns)
{
	struct vmod_cachetag_namespace **nsp;

	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	PTOK(pthread_mutex_lock(&cachetag_global_mtx));
	for (nsp = &cachetag_namespaces; *nsp != NULL; nsp = &(*nsp)->global_next) {
		if (*nsp == ns) {
			*nsp = ns->global_next;
			ns->global_next = NULL;
			break;
		}
	}
	PTOK(pthread_mutex_unlock(&cachetag_global_mtx));
	cachetag_fellow_provider_release_if_idle();
}

static void
cachetag_vsc_update(struct vmod_cachetag_namespace *ns)
{
	struct cachetag_counters c;

	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (ns->vsc == NULL)
		return;
	cachetag_snapshot_counters(ns->index, &c);

#define CACHETAG_VSC_PUBLISH(name) ns->vsc->name = c.name
#define CACHETAG_VSC_PUBLISH_LOCKWAIT(group, name) \
	ns->vsc->group##_obj_mtx_##name = c.group.name
#define CACHETAG_VSC_PUBLISH_RESIZE(group, name) \
	ns->vsc->group##_##name = c.group.name
#define CACHETAG_VSC_PUBLISH_TIMING(group, name) \
	ns->vsc->group##_##name = c.group.name
	CACHETAG_VSC_PUBLISH(index_memory_bytes);
	CACHETAG_VSC_PUBLISH(volatile_side_table_bytes);
	CACHETAG_VSC_PUBLISH(volatile_side_table_buckets);
	CACHETAG_VSC_PUBLISH(volatile_side_table_grows);
	CACHETAG_VSC_PUBLISH(volatile_side_table_shrinks);
	CACHETAG_VSC_PUBLISH(volatile_object_table_bytes);
	CACHETAG_VSC_PUBLISH(volatile_object_count_sidecar_bytes);
	CACHETAG_VSC_PUBLISH(volatile_object_count_overflow_bytes);
	CACHETAG_VSC_PUBLISH(volatile_interned_sets);
	CACHETAG_VSC_PUBLISH(volatile_interned_set_refs);
	CACHETAG_VSC_PUBLISH(volatile_interned_set_hits);
	CACHETAG_VSC_PUBLISH(volatile_interned_set_misses);
	CACHETAG_VSC_PUBLISH(volatile_interned_set_bytes);
	CACHETAG_VSC_PUBLISH(volatile_interned_table_bytes);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_acquire, calls);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_acquire, usec);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_acquire, max_usec);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_acquire, over_50us);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_acquire, over_250us);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_acquire, over_1ms);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_acquire, over_10ms);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_table_grow, calls);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_table_grow, usec);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_table_grow, max_usec);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_set_alloc, calls);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_set_alloc, usec);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_set_alloc, max_usec);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_candidate_alloc, calls);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_candidate_alloc, usec);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_candidate_alloc, max_usec);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_table_alloc, calls);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_table_alloc, usec);
	CACHETAG_VSC_PUBLISH_TIMING(volatile_interned_table_alloc, max_usec);
	CACHETAG_VSC_PUBLISH(volatile_interned_migration_active);
	CACHETAG_VSC_PUBLISH(volatile_interned_old_table_bytes);
	CACHETAG_VSC_PUBLISH(volatile_interned_detached_set_bytes);
	CACHETAG_VSC_PUBLISH(volatile_interned_detached_table_bytes);
	CACHETAG_VSC_PUBLISH(volatile_interned_table_alloc_failures);
	CACHETAG_VSC_PUBLISH(volatile_interned_table_grow_failures);
	CACHETAG_VSC_PUBLISH(volatile_interned_candidate_discards);
	CACHETAG_VSC_PUBLISH(volatile_object_table_slots);
	CACHETAG_VSC_PUBLISH(volatile_object_table_shrinks);
	CACHETAG_VSC_PUBLISH(volatile_objects);
	CACHETAG_VSC_PUBLISH(volatile_edges);
	CACHETAG_VSC_PUBLISH(volatile_inline_folds);
	CACHETAG_VSC_PUBLISH(volatile_attached);
	CACHETAG_VSC_PUBLISH(volatile_attach_failures);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_probe, calls);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_probe, wait_usec);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_probe, wait_max_usec);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_probe, wait_over_50us);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_probe, wait_over_250us);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_probe, wait_over_1ms);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_probe, wait_over_10ms);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_probe, wait_over_50ms);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_attach, calls);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_attach, wait_usec);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_attach, wait_max_usec);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_attach, wait_over_50us);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_attach, wait_over_250us);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_attach, wait_over_1ms);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_attach, wait_over_10ms);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_attach, wait_over_50ms);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_invalidate, calls);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_invalidate, wait_usec);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_invalidate, wait_max_usec);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_invalidate, wait_over_50us);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_invalidate, wait_over_250us);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_invalidate, wait_over_1ms);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_invalidate, wait_over_10ms);
	CACHETAG_VSC_PUBLISH_LOCKWAIT(request_invalidate, wait_over_50ms);
	CACHETAG_VSC_PUBLISH_RESIZE(object_grow, calls);
	CACHETAG_VSC_PUBLISH_RESIZE(object_grow, old_capacity_last);
	CACHETAG_VSC_PUBLISH_RESIZE(object_grow, new_capacity_last);
	CACHETAG_VSC_PUBLISH_RESIZE(object_grow, usec);
	CACHETAG_VSC_PUBLISH_RESIZE(object_grow, max_usec);
	CACHETAG_VSC_PUBLISH_RESIZE(object_grow, last_usec);
	CACHETAG_VSC_PUBLISH_RESIZE(object_grow, failures);
	CACHETAG_VSC_PUBLISH_RESIZE(object_grow, compact_active_calls);
	CACHETAG_VSC_PUBLISH_RESIZE(object_shrink, calls);
	CACHETAG_VSC_PUBLISH_RESIZE(object_shrink, old_capacity_last);
	CACHETAG_VSC_PUBLISH_RESIZE(object_shrink, new_capacity_last);
	CACHETAG_VSC_PUBLISH_RESIZE(object_shrink, usec);
	CACHETAG_VSC_PUBLISH_RESIZE(object_shrink, max_usec);
	CACHETAG_VSC_PUBLISH_RESIZE(object_shrink, last_usec);
	CACHETAG_VSC_PUBLISH_RESIZE(object_shrink, failures);
	CACHETAG_VSC_PUBLISH_RESIZE(object_shrink, compact_active_calls);
	CACHETAG_VSC_PUBLISH_RESIZE(side_grow_rehash, calls);
	CACHETAG_VSC_PUBLISH_RESIZE(side_grow_rehash, old_capacity_last);
	CACHETAG_VSC_PUBLISH_RESIZE(side_grow_rehash, new_capacity_last);
	CACHETAG_VSC_PUBLISH_RESIZE(side_grow_rehash, usec);
	CACHETAG_VSC_PUBLISH_RESIZE(side_grow_rehash, max_usec);
	CACHETAG_VSC_PUBLISH_RESIZE(side_grow_rehash, last_usec);
	CACHETAG_VSC_PUBLISH_RESIZE(side_grow_rehash, failures);
	CACHETAG_VSC_PUBLISH_RESIZE(side_grow_rehash, compact_active_calls);
	CACHETAG_VSC_PUBLISH_RESIZE(side_shrink_rehash, calls);
	CACHETAG_VSC_PUBLISH_RESIZE(side_shrink_rehash, old_capacity_last);
	CACHETAG_VSC_PUBLISH_RESIZE(side_shrink_rehash, new_capacity_last);
	CACHETAG_VSC_PUBLISH_RESIZE(side_shrink_rehash, usec);
	CACHETAG_VSC_PUBLISH_RESIZE(side_shrink_rehash, max_usec);
	CACHETAG_VSC_PUBLISH_RESIZE(side_shrink_rehash, last_usec);
	CACHETAG_VSC_PUBLISH_RESIZE(side_shrink_rehash, failures);
	CACHETAG_VSC_PUBLISH_RESIZE(side_shrink_rehash, compact_active_calls);
	CACHETAG_VSC_PUBLISH_RESIZE(zero_container_free, calls);
	CACHETAG_VSC_PUBLISH_RESIZE(zero_container_free, old_capacity_last);
	CACHETAG_VSC_PUBLISH_RESIZE(zero_container_free, new_capacity_last);
	CACHETAG_VSC_PUBLISH_RESIZE(zero_container_free, usec);
	CACHETAG_VSC_PUBLISH_RESIZE(zero_container_free, max_usec);
	CACHETAG_VSC_PUBLISH_RESIZE(zero_container_free, last_usec);
	CACHETAG_VSC_PUBLISH_RESIZE(zero_container_free, failures);
	CACHETAG_VSC_PUBLISH_RESIZE(zero_container_free, compact_active_calls);
	CACHETAG_VSC_PUBLISH(record_shrink_calls);
	CACHETAG_VSC_PUBLISH(record_shrink_obj_mtx_wait_usec);
	CACHETAG_VSC_PUBLISH(record_shrink_obj_mtx_wait_max_usec);
	CACHETAG_VSC_PUBLISH(record_shrink_obj_mtx_hold_usec);
	CACHETAG_VSC_PUBLISH(record_shrink_obj_mtx_hold_max_usec);
	CACHETAG_VSC_PUBLISH(record_shrink_obj_mtx_hold_last_usec);
	CACHETAG_VSC_PUBLISH(object_segments);
	CACHETAG_VSC_PUBLISH(object_published_slots);
	CACHETAG_VSC_PUBLISH(object_published_bytes);
	CACHETAG_VSC_PUBLISH(object_count_published_bytes);
	CACHETAG_VSC_PUBLISH(object_segment_grow_publishes);
	CACHETAG_VSC_PUBLISH(object_emergency_segment_allocations);
	CACHETAG_VSC_PUBLISH(object_emergency_segment_old_capacity_max);
	CACHETAG_VSC_PUBLISH(object_segment_detach_batches);
	CACHETAG_VSC_PUBLISH(object_segment_alloc_usec);
	CACHETAG_VSC_PUBLISH(object_segment_alloc_max_usec);
	CACHETAG_VSC_PUBLISH(object_segment_alloc_last_usec);
	CACHETAG_VSC_PUBLISH(object_segment_alloc_failures);
	CACHETAG_VSC_PUBLISH(object_segment_free_usec);
	CACHETAG_VSC_PUBLISH(object_segment_free_max_usec);
	CACHETAG_VSC_PUBLISH(object_segment_free_last_usec);
	CACHETAG_VSC_PUBLISH(side_primary_buckets);
	CACHETAG_VSC_PUBLISH(side_primary_bytes);
	CACHETAG_VSC_PUBLISH(side_primary_live);
	CACHETAG_VSC_PUBLISH(side_primary_tombstones);
	CACHETAG_VSC_PUBLISH(side_retiring_buckets);
	CACHETAG_VSC_PUBLISH(side_retiring_bytes);
	CACHETAG_VSC_PUBLISH(side_retiring_live);
	CACHETAG_VSC_PUBLISH(side_retiring_tombstones);
	CACHETAG_VSC_PUBLISH(side_resize_state);
	CACHETAG_VSC_PUBLISH(side_resize_reason);
	CACHETAG_VSC_PUBLISH(side_migration_cursor);
	CACHETAG_VSC_PUBLISH(side_migration_buckets_remaining);
	CACHETAG_VSC_PUBLISH(side_migration_live_remaining);
	CACHETAG_VSC_PUBLISH(side_migration_batches);
	CACHETAG_VSC_PUBLISH(side_migration_inspected_buckets);
	CACHETAG_VSC_PUBLISH(side_migration_moved_entries);
	CACHETAG_VSC_PUBLISH(side_migration_completions);
	CACHETAG_VSC_PUBLISH(side_destination_alloc_usec);
	CACHETAG_VSC_PUBLISH(side_destination_alloc_max_usec);
	CACHETAG_VSC_PUBLISH(side_destination_alloc_last_usec);
	CACHETAG_VSC_PUBLISH(side_destination_alloc_failures);
	CACHETAG_VSC_PUBLISH(side_retired_free_usec);
	CACHETAG_VSC_PUBLISH(side_retired_free_max_usec);
	CACHETAG_VSC_PUBLISH(side_retired_free_last_usec);
	CACHETAG_VSC_PUBLISH(side_resize_grow_publishes);
	CACHETAG_VSC_PUBLISH(side_resize_attach_grow_publishes);
	CACHETAG_VSC_PUBLISH(side_resize_attach_grow_old_buckets_max);
	CACHETAG_VSC_PUBLISH(side_resize_rebuild_publishes);
	CACHETAG_VSC_PUBLISH(side_resize_shrink_publishes);
	CACHETAG_VSC_PUBLISH(side_resize_shrink_cancellations);
	CACHETAG_VSC_PUBLISH(side_resize_shrink_rollbacks);
	CACHETAG_VSC_PUBLISH(resize_batch_obj_mtx_wait_usec);
	CACHETAG_VSC_PUBLISH(resize_batch_obj_mtx_wait_max_usec);
	CACHETAG_VSC_PUBLISH(resize_batch_obj_mtx_wait_last_usec);
	CACHETAG_VSC_PUBLISH(resize_batch_obj_mtx_hold_usec);
	CACHETAG_VSC_PUBLISH(resize_batch_obj_mtx_hold_max_usec);
	CACHETAG_VSC_PUBLISH(resize_batch_obj_mtx_hold_last_usec);
	CACHETAG_VSC_PUBLISH(resize_batch_obj_mtx_hold_over_2ms);
	CACHETAG_VSC_PUBLISH(resize_batch_obj_mtx_hold_over_5ms);
	CACHETAG_VSC_PUBLISH(resize_batch_obj_mtx_hold_over_10ms);
	CACHETAG_VSC_PUBLISH(resize_low_water_active);
	CACHETAG_VSC_PUBLISH(resize_low_water_starts);
	CACHETAG_VSC_PUBLISH(resize_low_water_restarts);
	CACHETAG_VSC_PUBLISH(resize_low_water_rearms);
	CACHETAG_VSC_PUBLISH(resize_low_water_elapsed_usec);
	CACHETAG_VSC_PUBLISH(resize_low_water_observed_live);
	CACHETAG_VSC_PUBLISH(resize_low_water_target_objects);
	CACHETAG_VSC_PUBLISH(resize_low_water_target_side_buckets);
	CACHETAG_VSC_PUBLISH(resize_low_water_cancellations);
	CACHETAG_VSC_PUBLISH(resize_low_water_cancellation_reason);
	CACHETAG_VSC_PUBLISH(resize_active_bytes);
	CACHETAG_VSC_PUBLISH(resize_retiring_bytes);
	CACHETAG_VSC_PUBLISH(resize_detached_bytes);
	CACHETAG_VSC_PUBLISH(resize_reconciled_bytes);
	CACHETAG_VSC_PUBLISH(parse_errors);
	CACHETAG_VSC_PUBLISH(limit_rejections);
	CACHETAG_VSC_PUBLISH(stale_calls);
	CACHETAG_VSC_PUBLISH(stale_detected);
	CACHETAG_VSC_PUBLISH(purgemap_entries);
	CACHETAG_VSC_PUBLISH(purgemap_table_slots);
	CACHETAG_VSC_PUBLISH(purgemap_tombstones);
	CACHETAG_VSC_PUBLISH(purgemap_empty_slots);
	CACHETAG_VSC_PUBLISH(purgemap_bytes);
	CACHETAG_VSC_PUBLISH(purgemap_hard_floor);
	CACHETAG_VSC_PUBLISH(purgemap_soft_floor);
	CACHETAG_VSC_PUBLISH(purgemap_seq);
	CACHETAG_VSC_PUBLISH(purgemap_prunes);
	CACHETAG_VSC_PUBLISH(purgemap_pruned_entries);
	CACHETAG_VSC_PUBLISH(purgemap_rebuilds_grow);
	CACHETAG_VSC_PUBLISH(purgemap_rebuilds_same_size);
	CACHETAG_VSC_PUBLISH(purgemap_rebuilds_shrink);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_passes);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaimed_entries);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaimed_bytes);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_deferred_pending);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_defer_usec);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_defer_max_usec);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_defer_last_usec);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_filter_usec);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_filter_max_usec);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_filter_last_usec);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_transient_bytes);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_transient_max_bytes);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_table_slots_before);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_table_slots_after);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_table_bytes_before);
	CACHETAG_VSC_PUBLISH(purgemap_auto_reclaim_table_bytes_after);
	CACHETAG_VSC_PUBLISH(purgemap_probe_hard_hits);
	CACHETAG_VSC_PUBLISH(purgemap_probe_soft_hits);
	CACHETAG_VSC_PUBLISH(purgemap_insert_probe_hits);
	CACHETAG_VSC_PUBLISH(purgemap_fellow_attr_objects_written);
	CACHETAG_VSC_PUBLISH(purgemap_fellow_attr_bytes_written);
	CACHETAG_VSC_PUBLISH(purgemap_fellow_direct_probes);
	CACHETAG_VSC_PUBLISH(purgemap_fellow_attr_absent);
	CACHETAG_VSC_PUBLISH(purgemap_fellow_attr_invalid);
	CACHETAG_VSC_PUBLISH(purgemap_fellow_attr_read_failures);
	CACHETAG_VSC_PUBLISH(purgemap_fellow_namespace_records_probed);
	CACHETAG_VSC_PUBLISH(purgemap_fellow_store_invariant_failures);
	CACHETAG_VSC_PUBLISH(purgemap_volatile_fallback_attaches);
	CACHETAG_VSC_PUBLISH(sweep_passes);
	CACHETAG_VSC_PUBLISH(sweep_aborts);
	CACHETAG_VSC_PUBLISH(sweep_scanned);
	CACHETAG_VSC_PUBLISH(sweep_killed);
	CACHETAG_VSC_PUBLISH(sweep_reduced);
	CACHETAG_VSC_PUBLISH(sweep_batches);
	CACHETAG_VSC_PUBLISH(sweep_last_batches);
	CACHETAG_VSC_PUBLISH(sweep_batch_scanned_max);
	CACHETAG_VSC_PUBLISH(sweep_batch_hold_over_2ms);
	CACHETAG_VSC_PUBLISH(sweep_batch_hold_over_5ms);
	CACHETAG_VSC_PUBLISH(sweep_batch_hold_over_10ms);
	CACHETAG_VSC_PUBLISH(sweep_wakeups);
	CACHETAG_VSC_PUBLISH(sweep_iterations);
	CACHETAG_VSC_PUBLISH(sweep_remaining);
	CACHETAG_VSC_PUBLISH(sweep_obj_mtx_wait_usec);
	CACHETAG_VSC_PUBLISH(sweep_obj_mtx_wait_max_usec);
	CACHETAG_VSC_PUBLISH(sweep_obj_mtx_wait_last_usec);
	CACHETAG_VSC_PUBLISH(sweep_obj_mtx_hold_usec);
	CACHETAG_VSC_PUBLISH(sweep_obj_mtx_hold_max_usec);
	CACHETAG_VSC_PUBLISH(sweep_obj_mtx_hold_last_usec);
	CACHETAG_VSC_PUBLISH(sweep_unlocked_gap_usec);
	CACHETAG_VSC_PUBLISH(sweep_unlocked_gap_last_usec);
	CACHETAG_VSC_PUBLISH(sweep_per_object_max_usec);
	CACHETAG_VSC_PUBLISH(sweep_deferred_shrinks);
	CACHETAG_VSC_PUBLISH(sweep_total_usec);
	CACHETAG_VSC_PUBLISH(sweep_total_max_usec);
	CACHETAG_VSC_PUBLISH(sweep_total_last_usec);
	CACHETAG_VSC_PUBLISH(sweep_last_scanned);
	CACHETAG_VSC_PUBLISH(sweep_last_killed);
	CACHETAG_VSC_PUBLISH(sweep_last_reduced);
	CACHETAG_VSC_PUBLISH(sweep_last_objects_before);
	CACHETAG_VSC_PUBLISH(sweep_last_objects_after);
	CACHETAG_VSC_PUBLISH(sweep_last_object_slots_before);
	CACHETAG_VSC_PUBLISH(sweep_last_object_slots_after);
	CACHETAG_VSC_PUBLISH(sweep_last_object_bytes_before);
	CACHETAG_VSC_PUBLISH(sweep_last_object_bytes_after);
	CACHETAG_VSC_PUBLISH(sweep_last_side_buckets_before);
	CACHETAG_VSC_PUBLISH(sweep_last_side_buckets_after);
	CACHETAG_VSC_PUBLISH(sweep_last_side_bytes_before);
	CACHETAG_VSC_PUBLISH(sweep_last_side_bytes_after);
	CACHETAG_VSC_PUBLISH(publication_phase);
	CACHETAG_VSC_PUBLISH(publication_readers_phase0);
	CACHETAG_VSC_PUBLISH(publication_readers_phase1);
	CACHETAG_VSC_PUBLISH(publication_acquires);
	CACHETAG_VSC_PUBLISH(publication_releases);
	CACHETAG_VSC_PUBLISH(reclaim_pending);
	CACHETAG_VSC_PUBLISH(reclaim_phase);
	CACHETAG_VSC_PUBLISH(persist_wal_records);
	CACHETAG_VSC_PUBLISH(persist_wal_bytes);
	CACHETAG_VSC_PUBLISH(persist_checkpoint_entries);
	CACHETAG_VSC_PUBLISH(persist_checkpoint_wal_sequence);
	CACHETAG_VSC_PUBLISH(persist_checkpoint_bytes);
	CACHETAG_VSC_PUBLISH(persist_checkpoint_publications);
	CACHETAG_VSC_PUBLISH(persist_checkpoint_segments_collected);
	CACHETAG_VSC_PUBLISH(persist_orphan_files_collected);
	CACHETAG_VSC_PUBLISH(persist_replay_records);
	CACHETAG_VSC_PUBLISH(persist_failures);
	CACHETAG_VSC_PUBLISH(persist_degraded);
	CACHETAG_VSC_PUBLISH(fellow_replayed_records);
#undef CACHETAG_VSC_PUBLISH_RESIZE
#undef CACHETAG_VSC_PUBLISH_LOCKWAIT
#undef CACHETAG_VSC_PUBLISH_TIMING
#undef CACHETAG_VSC_PUBLISH
}

static char *
cachetag_vsc_ident(const char *vcl, const char *obj, const char *ns)
{
	char *p;
	unsigned char *q;
	size_t l;
	int r;

	AN(vcl);
	AN(obj);
	AN(ns);
	l = strlen(vcl) + 1 + strlen(obj) + 1 + strlen(ns) + 1;
	p = malloc(l);
	if (p == NULL)
		return (NULL);
	r = snprintf(p, l, "%s_%s_%s", vcl, obj, ns);
	assert(r >= 0 && (size_t)r < l);
	for (q = (unsigned char *)p; *q != '\0'; q++) {
		if (!isalnum(*q) && *q != '_')
			*q = '_';
	}
	return (p);
}

static void
cachetag_pending_free(struct cachetag_pending *tp)
{

	CHECK_OBJ_NOTNULL(tp, TAG_PENDING_MAGIC);
	if (tp->keys != tp->inline_keys)
		free(tp->keys);
	FREE_OBJ(tp);
}

static void
cachetag_pending_init_keys(struct cachetag_pending *tp)
{

	CHECK_OBJ_NOTNULL(tp, TAG_PENDING_MAGIC);
	tp->keys = tp->inline_keys;
	tp->nkeys = 0;
	tp->capkeys = TAG_INLINE_KEYS;
}

static void
cachetag_pending_clear_keys(struct cachetag_pending *tp)
{

	CHECK_OBJ_NOTNULL(tp, TAG_PENDING_MAGIC);
	if (tp->keys != tp->inline_keys)
		free(tp->keys);
	if (tp->publication_held) {
		cachetag_publication_exit(tp->ns->index, tp->publication_phase);
		tp->publication_held = 0;
	}
	tp->fellow_attr_included = 0;
	tp->fellow_attr_record_len = 0;
	cachetag_pending_init_keys(tp);
}

static int
cachetag_pending_contains(const struct cachetag_pending *tp,
    const struct cachetag_registration_snapshot *snap)
{
	unsigned u;

	CHECK_OBJ_NOTNULL(tp, TAG_PENDING_MAGIC);
	AN(snap);
	for (u = 0; u < tp->nkeys; u++) {
		if (tp->keys[u].digest_hi == snap->digest_hi &&
		    tp->keys[u].digest_lo == snap->digest_lo)
			return (1);
	}
	return (0);
}

static int
cachetag_pending_collect_unique(struct cachetag_pending *tp,
    const struct cachetag_registration_snapshot *snap)
{
	struct cachetag_registration_snapshot copy;
	struct cachetag_registration_snapshot *p;
	unsigned cap, u;

	CHECK_OBJ_NOTNULL(tp, TAG_PENDING_MAGIC);
	AN(snap);
	for (u = 0; u < tp->nkeys; u++) {
		if (tp->keys[u].digest_hi == snap->digest_hi &&
		    tp->keys[u].digest_lo == snap->digest_lo)
			return (0);
	}
	if (tp->nkeys == tp->capkeys) {
		cap = tp->capkeys * 2;
		if (tp->keys == tp->inline_keys) {
			p = malloc(cap * sizeof *p);
			if (p == NULL)
				return (ENOMEM);
			memcpy(p, tp->inline_keys, tp->nkeys * sizeof *p);
		} else {
			p = realloc(tp->keys, cap * sizeof *p);
			if (p == NULL)
				return (ENOMEM);
		}
		tp->keys = p;
		tp->capkeys = cap;
	}
	copy = *snap;
	if (tp->nkeys > 0)
		copy.reg_seq = tp->keys[0].reg_seq;
	tp->keys[tp->nkeys++] = copy;
	return (0);
}

static struct cachetag_pending *
cachetag_find_pending_locked(struct vmod_cachetag_namespace *ns,
    struct objcore *oc, int include_consumed)
{
	struct cachetag_pending *tp;

	for (tp = ns->pending; tp != NULL; tp = tp->next) {
		CHECK_OBJ_NOTNULL(tp, TAG_PENDING_MAGIC);
		if (tp->oc == oc && (include_consumed || !tp->consumed))
			return (tp);
	}
	return (NULL);
}

/*
 * Provider lock order is global namespace registry, then namespace pending
 * mutex.  Size and fill never enter Fellow and Fellow never invokes them while
 * holding a cachetag lock.  The pending view is immutable between the
 * synchronous size/fill calls on one fetch worker.
 */
static int
cachetag_fellow_attr_size_cb(void *priv, const struct objcore *oc, size_t *lenp)
{
	struct cachetag_fellow_namespace_digest {
		uint64_t hi;
		uint64_t lo;
	};
	struct vmod_cachetag_namespace *ns;
	struct cachetag_pending *tp;
	struct cachetag_fellow_namespace_digest *seen = NULL, *new_seen;
	size_t record_len, total = CACHETAG_FELLOW_ENVELOPE_HEADER_LEN;
	uint64_t reg_seq;
	unsigned count = 0, i, u;
	int compact_singleton = 0;
	int error = 0;

	(void)priv;
	CHECK_OBJ_NOTNULL(oc, OBJCORE_MAGIC);
	AN(lenp);
	*lenp = 0;
	PTOK(pthread_mutex_lock(&cachetag_global_mtx));
	for (ns = cachetag_namespaces; ns != NULL; ns = ns->global_next) {
		CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
		PTOK(pthread_mutex_lock(&ns->mtx));
		if (!ns->fellow_direct_active ||
		    !cachetag_persist_enabled(ns->index)) {
			PTOK(pthread_mutex_unlock(&ns->mtx));
			continue;
		}
		tp = cachetag_find_pending_locked(ns, TRUST_ME(oc), 1);
		if (tp == NULL || tp->nkeys == 0) {
			PTOK(pthread_mutex_unlock(&ns->mtx));
			continue;
		}
		for (i = 0; i < count; i++) {
			if (seen[i].hi == ns->namespace_digest_hi &&
			    seen[i].lo == ns->namespace_digest_lo) {
				error = EINVAL;
				break;
			}
		}
		if (error != 0) {
			PTOK(pthread_mutex_unlock(&ns->mtx));
			break;
		}
		reg_seq = tp->keys[0].reg_seq;
		for (u = 1; u < tp->nkeys; u++) {
			if (tp->keys[u].reg_seq != reg_seq) {
				error = EINVAL;
				break;
			}
		}
		if (error != 0) {
			PTOK(pthread_mutex_unlock(&ns->mtx));
			break;
		}
		if (tp->nkeys > UINT16_MAX ||
		    tp->nkeys > (SIZE_MAX - CACHETAG_FELLOW_RECORD_HEADER_LEN) / 8) {
			error = EOVERFLOW;
			PTOK(pthread_mutex_unlock(&ns->mtx));
			break;
		}
		record_len = CACHETAG_FELLOW_RECORD_HEADER_LEN +
		    (size_t)tp->nkeys * 8;
		if (record_len > UINT32_MAX || total > UINT32_MAX - record_len ||
		    count == UINT16_MAX) {
			error = EOVERFLOW;
			PTOK(pthread_mutex_unlock(&ns->mtx));
			break;
		}
		new_seen = realloc(seen, (count + 1) * sizeof *seen);
		if (new_seen == NULL) {
			error = ENOMEM;
			PTOK(pthread_mutex_unlock(&ns->mtx));
			break;
		}
		seen = new_seen;
		seen[count].hi = ns->namespace_digest_hi;
		seen[count].lo = ns->namespace_digest_lo;
		tp->fellow_attr_record_len = record_len;
		tp->fellow_attr_included = 0;
		if (count == 0)
			compact_singleton = tp->nkeys == 1 &&
			    !ns->test_next_fellow_attr_bad_length &&
			    ns->test_next_fellow_attr_corruption ==
			    TAG_FELLOW_ATTR_CORRUPT_NONE;
		else
			compact_singleton = 0;
		total += record_len;
		count++;
		PTOK(pthread_mutex_unlock(&ns->mtx));
	}
	PTOK(pthread_mutex_unlock(&cachetag_global_mtx));
	free(seen);
	if (error != 0)
		return (error);
	if (count != 0)
		*lenp = compact_singleton ? CACHETAG_FELLOW_SINGLETON_LEN : total;
	return (0);
}

static void
cachetag_test_corrupt_fellow_attr(unsigned char *p, size_t len,
    enum cachetag_fellow_attr_corruption corruption, size_t first_record,
    size_t second_record)
{
	uint32_t record_len;

	AN(p);
	switch (corruption) {
	case TAG_FELLOW_ATTR_CORRUPT_NONE:
		break;
	case TAG_FELLOW_ATTR_CORRUPT_RECORD_LEN:
		assert(first_record <= len &&
		    len - first_record >= CACHETAG_FELLOW_RECORD_HEADER_LEN);
		record_len = cachetag_le32dec(p + first_record);
		assert(record_len >= CACHETAG_FELLOW_RECORD_HEADER_LEN + 8);
		cachetag_le32enc(p + first_record, record_len - 8);
		break;
	case TAG_FELLOW_ATTR_CORRUPT_FOLD_COUNT:
		assert(first_record <= len &&
		    len - first_record >= CACHETAG_FELLOW_RECORD_HEADER_LEN);
		cachetag_le16enc(p + first_record + 28, 2);
		break;
	case TAG_FELLOW_ATTR_CORRUPT_ENVELOPE_FLAGS:
		assert(len >= CACHETAG_FELLOW_ENVELOPE_HEADER_LEN);
		cachetag_le16enc(p + 6, 1);
		break;
	case TAG_FELLOW_ATTR_CORRUPT_ENVELOPE_RESERVED:
		assert(len >= CACHETAG_FELLOW_ENVELOPE_HEADER_LEN);
		cachetag_le16enc(p + 14, 1);
		break;
	case TAG_FELLOW_ATTR_CORRUPT_RECORD_FLAGS:
		assert(first_record <= len &&
		    len - first_record >= CACHETAG_FELLOW_RECORD_HEADER_LEN);
		cachetag_le16enc(p + first_record + 30, 1);
		break;
	case TAG_FELLOW_ATTR_CORRUPT_DUPLICATE_NAMESPACE:
		assert(first_record <= len && second_record <= len &&
		    len - first_record >= CACHETAG_FELLOW_RECORD_HEADER_LEN &&
		    len - second_record >= CACHETAG_FELLOW_RECORD_HEADER_LEN);
		memcpy(p + second_record + 4, p + first_record + 4, 16);
		break;
	default:
		WRONG("unknown Fellow attr test corruption");
	}
}

static void
cachetag_fellow_attr_fill_singleton(const struct objcore *oc,
    unsigned char *p, size_t len)
{
	struct vmod_cachetag_namespace *ns;
	struct cachetag_pending *tp;
	uint64_t reg_seq;
	unsigned count = 0;
	uint16_t test_version = 0;

	CHECK_OBJ_NOTNULL(oc, OBJCORE_MAGIC);
	AN(p);
	assert(len == CACHETAG_FELLOW_SINGLETON_LEN);
	memset(p, 0, len);
	cachetag_le32enc(p, CACHETAG_FELLOW_ENVELOPE_MAGIC);
	cachetag_le16enc(p + 4,
	    CACHETAG_FELLOW_ENVELOPE_VERSION_SINGLETON);
	PTOK(pthread_mutex_lock(&cachetag_global_mtx));
	for (ns = cachetag_namespaces; ns != NULL; ns = ns->global_next) {
		PTOK(pthread_mutex_lock(&ns->mtx));
		if (!ns->fellow_direct_active ||
		    !cachetag_persist_enabled(ns->index)) {
			PTOK(pthread_mutex_unlock(&ns->mtx));
			continue;
		}
		tp = cachetag_find_pending_locked(ns, TRUST_ME(oc), 1);
		if (tp == NULL || tp->nkeys == 0) {
			PTOK(pthread_mutex_unlock(&ns->mtx));
			continue;
		}
		assert(count == 0 && tp->nkeys == 1);
		assert(tp->fellow_attr_record_len ==
		    CACHETAG_FELLOW_RECORD_HEADER_LEN + 8);
		assert(!ns->test_next_fellow_attr_bad_length);
		assert(ns->test_next_fellow_attr_corruption ==
		    TAG_FELLOW_ATTR_CORRUPT_NONE);
		reg_seq = tp->keys[0].reg_seq;
		cachetag_le64enc(p + 8, ns->namespace_digest_hi);
		cachetag_le64enc(p + 16, ns->namespace_digest_lo);
		cachetag_le64enc(p + 24, reg_seq);
		cachetag_le64enc(p + 32,
		    cachetag_fold_digest(tp->keys[0].digest_hi,
		    tp->keys[0].digest_lo));
		tp->fellow_attr_included = 1;
		if (ns->test_next_fellow_attr_version != 0) {
			test_version = ns->test_next_fellow_attr_version;
			ns->test_next_fellow_attr_version = 0;
		}
		cachetag_note_fellow_metric(ns->index,
		    TAG_FELLOW_ATTR_OBJECTS_WRITTEN, 1);
		cachetag_note_fellow_metric(ns->index,
		    TAG_FELLOW_ATTR_BYTES_WRITTEN, len);
		count++;
		PTOK(pthread_mutex_unlock(&ns->mtx));
	}
	PTOK(pthread_mutex_unlock(&cachetag_global_mtx));
	assert(count == 1);
	if (test_version != 0)
		cachetag_le16enc(p + 4, test_version);
}

static void
cachetag_fellow_attr_fill_cb(void *priv, const struct objcore *oc, void *dst,
    size_t len)
{
	struct vmod_cachetag_namespace *ns;
	struct cachetag_pending *tp;
	unsigned char *p = dst;
	size_t first_record = 0, second_record = 0;
	size_t record_len, written = CACHETAG_FELLOW_ENVELOPE_HEADER_LEN;
	uint64_t reg_seq;
	unsigned attributed_count = 0, count = 0, u;
	uint16_t test_version = 0;
	unsigned test_bad_length = 0;
	enum cachetag_fellow_attr_corruption test_corruption =
	    TAG_FELLOW_ATTR_CORRUPT_NONE;

	(void)priv;
	CHECK_OBJ_NOTNULL(oc, OBJCORE_MAGIC);
	AN(dst);
	if (len == CACHETAG_FELLOW_SINGLETON_LEN) {
		cachetag_fellow_attr_fill_singleton(oc, p, len);
		return;
	}
	assert(len >= CACHETAG_FELLOW_ENVELOPE_HEADER_LEN && len <= UINT32_MAX);
	memset(dst, 0, len);
	cachetag_le32enc(p, CACHETAG_FELLOW_ENVELOPE_MAGIC);
	cachetag_le16enc(p + 4, CACHETAG_FELLOW_ENVELOPE_VERSION_V1);
	cachetag_le32enc(p + 8, (uint32_t)len);
	PTOK(pthread_mutex_lock(&cachetag_global_mtx));
	for (ns = cachetag_namespaces; ns != NULL; ns = ns->global_next) {
		PTOK(pthread_mutex_lock(&ns->mtx));
		if (!ns->fellow_direct_active ||
		    !cachetag_persist_enabled(ns->index)) {
			PTOK(pthread_mutex_unlock(&ns->mtx));
			continue;
		}
		tp = cachetag_find_pending_locked(ns, TRUST_ME(oc), 1);
		if (tp == NULL || tp->nkeys == 0) {
			PTOK(pthread_mutex_unlock(&ns->mtx));
			continue;
		}
		record_len = CACHETAG_FELLOW_RECORD_HEADER_LEN +
		    (size_t)tp->nkeys * 8;
		assert(tp->fellow_attr_record_len == record_len);
		assert(written <= len && record_len <= len - written);
		if (count == 0)
			first_record = written;
		else if (count == 1)
			second_record = written;
		reg_seq = tp->keys[0].reg_seq;
		cachetag_le32enc(p + written, (uint32_t)record_len);
		cachetag_le64enc(p + written + 4, ns->namespace_digest_hi);
		cachetag_le64enc(p + written + 12, ns->namespace_digest_lo);
		cachetag_le64enc(p + written + 20, reg_seq);
		cachetag_le16enc(p + written + 28, (uint16_t)tp->nkeys);
		for (u = 0; u < tp->nkeys; u++) {
			assert(tp->keys[u].reg_seq == reg_seq);
			cachetag_le64enc(p + written + 32 + (size_t)u * 8,
			    cachetag_fold_digest(tp->keys[u].digest_hi,
			    tp->keys[u].digest_lo));
		}
		tp->fellow_attr_included = 1;
		if (ns->test_next_fellow_attr_version != 0) {
			test_version = ns->test_next_fellow_attr_version;
			ns->test_next_fellow_attr_version = 0;
		}
		if (ns->test_next_fellow_attr_bad_length) {
			test_bad_length = 1;
			ns->test_next_fellow_attr_bad_length = 0;
		}
		if (ns->test_next_fellow_attr_corruption !=
		    TAG_FELLOW_ATTR_CORRUPT_NONE) {
			test_corruption = ns->test_next_fellow_attr_corruption;
			ns->test_next_fellow_attr_corruption =
			    TAG_FELLOW_ATTR_CORRUPT_NONE;
		}
		cachetag_note_fellow_metric(ns->index,
		    TAG_FELLOW_ATTR_OBJECTS_WRITTEN, 1);
		written += record_len;
		count++;
		PTOK(pthread_mutex_unlock(&ns->mtx));
	}
	assert(written == len && count > 0 && count <= UINT16_MAX);
	for (ns = cachetag_namespaces; ns != NULL; ns = ns->global_next) {
		PTOK(pthread_mutex_lock(&ns->mtx));
		if (!ns->fellow_direct_active ||
		    !cachetag_persist_enabled(ns->index)) {
			PTOK(pthread_mutex_unlock(&ns->mtx));
			continue;
		}
		tp = cachetag_find_pending_locked(ns, TRUST_ME(oc), 1);
		if (tp == NULL || tp->nkeys == 0) {
			PTOK(pthread_mutex_unlock(&ns->mtx));
			continue;
		}
		record_len = tp->fellow_attr_record_len +
		    CACHETAG_FELLOW_ENVELOPE_HEADER_LEN / count;
		if (attributed_count <
		    CACHETAG_FELLOW_ENVELOPE_HEADER_LEN % count)
			record_len++;
		cachetag_note_fellow_metric(ns->index,
		    TAG_FELLOW_ATTR_BYTES_WRITTEN, record_len);
		attributed_count++;
		PTOK(pthread_mutex_unlock(&ns->mtx));
	}
	assert(attributed_count == count);
	PTOK(pthread_mutex_unlock(&cachetag_global_mtx));
	cachetag_le16enc(p + 12, (uint16_t)count);
	if (test_version != 0)
		cachetag_le16enc(p + 4, test_version);
	if (test_bad_length)
		cachetag_le32enc(p + 8, (uint32_t)len - 1);
	cachetag_test_corrupt_fellow_attr(p, len, test_corruption,
	    first_record, second_record);
}

struct cachetag_fellow_probe_ctx {
	struct cachetag_index *idx;
	uint64_t digest_hi;
	uint64_t digest_lo;
	int invalid;
	int found;
	int probe;
};

static int
cachetag_fellow_envelope_visit_v1(struct cachetag_fellow_probe_ctx *pc,
    const unsigned char *p, size_t len)
{
	uint64_t digest_hi, digest_lo, reg_seq;
	uint32_t record_len;
	uint16_t count, nfolds;
	size_t off, prev;
	unsigned seen;

	if (len < CACHETAG_FELLOW_ENVELOPE_HEADER_LEN || len > UINT32_MAX ||
	    cachetag_le32dec(p) != CACHETAG_FELLOW_ENVELOPE_MAGIC ||
	    cachetag_le16dec(p + 4) != CACHETAG_FELLOW_ENVELOPE_VERSION_V1 ||
	    cachetag_le16dec(p + 6) != 0 ||
	    cachetag_le32dec(p + 8) != len ||
	    cachetag_le16dec(p + 14) != 0) {
		pc->invalid = 1;
		return (0);
	}
	count = cachetag_le16dec(p + 12);
	off = CACHETAG_FELLOW_ENVELOPE_HEADER_LEN;
	for (seen = 0; seen < count; seen++) {
		if (off > len || len - off < CACHETAG_FELLOW_RECORD_HEADER_LEN)
			goto invalid;
		record_len = cachetag_le32dec(p + off);
		nfolds = cachetag_le16dec(p + off + 28);
		if (record_len < CACHETAG_FELLOW_RECORD_HEADER_LEN ||
		    (record_len & 7) != 0 || nfolds == 0 ||
		    cachetag_le16dec(p + off + 30) != 0 ||
		    record_len != CACHETAG_FELLOW_RECORD_HEADER_LEN +
		    (uint32_t)nfolds * 8 || record_len > len - off)
			goto invalid;
		digest_hi = cachetag_le64dec(p + off + 4);
		digest_lo = cachetag_le64dec(p + off + 12);
		/* Reject duplicate records for every namespace, not only ours. */
		for (prev = CACHETAG_FELLOW_ENVELOPE_HEADER_LEN; prev < off;) {
			uint32_t prev_len = cachetag_le32dec(p + prev);
			if (cachetag_le64dec(p + prev + 4) == digest_hi &&
			    cachetag_le64dec(p + prev + 12) == digest_lo)
				goto invalid;
			prev += prev_len;
		}
		if (digest_hi == pc->digest_hi && digest_lo == pc->digest_lo) {
			reg_seq = cachetag_le64dec(p + off + 20);
			pc->probe = cachetag_purgemap_probe_serialized(pc->idx,
			    reg_seq, p + off + CACHETAG_FELLOW_RECORD_HEADER_LEN,
			    nfolds);
			pc->found = 1;
		}
		off += record_len;
	}
	if (off != len)
		goto invalid;
	return (0);
invalid:
	pc->invalid = 1;
	return (0);
}

static int
cachetag_fellow_envelope_visit_singleton(struct cachetag_fellow_probe_ctx *pc,
    const unsigned char *p, size_t len)
{
	uint64_t digest_hi, digest_lo, reg_seq;

	if (len != CACHETAG_FELLOW_SINGLETON_LEN ||
	    cachetag_le32dec(p) != CACHETAG_FELLOW_ENVELOPE_MAGIC ||
	    cachetag_le16dec(p + 4) !=
	    CACHETAG_FELLOW_ENVELOPE_VERSION_SINGLETON ||
	    cachetag_le16dec(p + 6) != 0) {
		pc->invalid = 1;
		return (0);
	}
	digest_hi = cachetag_le64dec(p + 8);
	digest_lo = cachetag_le64dec(p + 16);
	if (digest_hi == pc->digest_hi && digest_lo == pc->digest_lo) {
		reg_seq = cachetag_le64dec(p + 24);
		pc->probe = cachetag_purgemap_probe_serialized(pc->idx, reg_seq,
		    p + 32, 1);
		pc->found = 1;
	}
	return (0);
}

static int
cachetag_fellow_envelope_visit(void *priv, const void *ptr, size_t len)
{
	struct cachetag_fellow_probe_ctx *pc = priv;
	const unsigned char *p = ptr;

	AN(pc);
	AN(ptr);
	if (len < 8 || cachetag_le32dec(p) != CACHETAG_FELLOW_ENVELOPE_MAGIC) {
		pc->invalid = 1;
		return (0);
	}
	switch (cachetag_le16dec(p + 4)) {
	case CACHETAG_FELLOW_ENVELOPE_VERSION_V1:
		return (cachetag_fellow_envelope_visit_v1(pc, p, len));
	case CACHETAG_FELLOW_ENVELOPE_VERSION_SINGLETON:
		return (cachetag_fellow_envelope_visit_singleton(pc, p, len));
	default:
		pc->invalid = 1;
		return (0);
	}
}

static int
cachetag_fellow_noop_visit(void *priv, const void *ptr, size_t len)
{

	(void)priv;
	AN(ptr);
	(void)len;
	return (0);
}

static int
cachetag_fellow_object_kind(struct worker *wrk, struct objcore *oc)
{
	fellow_object_attr_visit_api_f *visitp;

	CHECK_OBJ_NOTNULL(wrk, WORKER_MAGIC);
	CHECK_OBJ_NOTNULL(oc, OBJCORE_MAGIC);
	PTOK(pthread_mutex_lock(&cachetag_fellow_api_mtx));
	visitp = cachetag_fellow_visitp;
	if (visitp == NULL)
		visitp = fellow_object_attr_visit;
	PTOK(pthread_mutex_unlock(&cachetag_fellow_api_mtx));
	if (visitp == NULL)
		return (-2);
	return (visitp(wrk, oc, cachetag_fellow_noop_visit, NULL));
}

int
cachetag_fellow_attr_probe(struct worker *wrk, struct cachetag_index *idx,
    struct objcore *oc, enum cachetag_purge_mode *modep)
{
	struct cachetag_fellow_probe_ctx pc;
	fellow_object_attr_visit_api_f *visitp;
	int r;

	CHECK_OBJ_NOTNULL(wrk, WORKER_MAGIC);
	CHECK_OBJ_NOTNULL(oc, OBJCORE_MAGIC);
	AN(idx);
	if (modep != NULL)
		*modep = (enum cachetag_purge_mode)-1;
	memset(&pc, 0, sizeof pc);
	pc.idx = idx;
	cachetag_namespace_digest(idx, &pc.digest_hi, &pc.digest_lo);
	PTOK(pthread_mutex_lock(&cachetag_fellow_api_mtx));
	visitp = cachetag_fellow_visitp;
	if (visitp == NULL)
		visitp = fellow_object_attr_visit;
	PTOK(pthread_mutex_unlock(&cachetag_fellow_api_mtx));
	if (visitp == NULL) {
		cachetag_note_fellow_metric(idx, TAG_FELLOW_ATTR_READ_FAILURES, 1);
		return (EIO);
	}
	r = visitp(wrk, oc, cachetag_fellow_envelope_visit, &pc);
	if (r == -2)
		return (0);
	if (r == -1) {
		cachetag_note_fellow_metric(idx, TAG_FELLOW_ATTR_ABSENT, 1);
		return (EIO);
	}
	if (r == -3 || r != 0) {
		cachetag_note_fellow_metric(idx, TAG_FELLOW_ATTR_READ_FAILURES, 1);
		return (EIO);
	}
	cachetag_note_fellow_metric(idx, TAG_FELLOW_DIRECT_PROBES, 1);
	if (pc.invalid) {
		cachetag_note_fellow_metric(idx, TAG_FELLOW_ATTR_INVALID, 1);
		return (EINVAL);
	}
	if (pc.found)
		cachetag_note_fellow_metric(idx,
		    TAG_FELLOW_NAMESPACE_RECORDS_PROBED, 1);
	if (!pc.found || pc.probe == TAG_PM_PROBE_NONE)
		return (0);
	if (modep != NULL)
		*modep = pc.probe == TAG_PM_PROBE_HARD ? TAG_PURGE_HARD :
		    TAG_PURGE_SOFT;
	return (0);
}

static void
cachetag_pending_unlink(struct cachetag_pending *tp)
{
	struct vmod_cachetag_namespace *ns;
	struct cachetag_pending **tpp;

	CHECK_OBJ_NOTNULL(tp, TAG_PENDING_MAGIC);
	ns = tp->ns;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	PTOK(pthread_mutex_lock(&ns->mtx));
	for (tpp = &ns->pending; *tpp != NULL; tpp = &(*tpp)->next) {
		if (*tpp == tp) {
			*tpp = tp->next;
			tp->consumed = 1;
			break;
		}
	}
	PTOK(pthread_mutex_unlock(&ns->mtx));
	if (tp->publication_held) {
		cachetag_publication_exit(ns->index, tp->publication_phase);
		tp->publication_held = 0;
	}
}

static void
cachetag_pending_publication_done(struct cachetag_pending *tp)
{
	CHECK_OBJ_NOTNULL(tp, TAG_PENDING_MAGIC);
	if (!tp->publication_held)
		return;
	cachetag_publication_exit(tp->ns->index, tp->publication_phase);
	tp->publication_held = 0;
}

static void v_matchproto_(vmod_priv_fini_f)
cachetag_pending_fini(VRT_CTX, void *priv)
{
	struct cachetag_pending *tp;

	(void)ctx;
	CAST_OBJ_NOTNULL(tp, priv, TAG_PENDING_MAGIC);
	cachetag_pending_unlink(tp);
	cachetag_pending_free(tp);
}

static const struct vmod_priv_methods cachetag_pending_priv_methods[1] = {{
	.magic = VMOD_PRIV_METHODS_MAGIC,
	.type = "cachetag_pending",
	.fini = cachetag_pending_fini
}};

static struct cachetag_pending *
cachetag_get_pending(VRT_CTX, struct vmod_cachetag_namespace *ns)
{
	struct vmod_priv *priv;
	struct cachetag_pending *tp;
	struct busyobj *bo;

	CHECK_OBJ_NOTNULL(ctx, VRT_CTX_MAGIC);
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	bo = ctx->bo;
	if (bo == NULL || bo->fetch_objcore == NULL) {
		VRT_fail(ctx, "cachetag.add() requires backend fetch context");
		return (NULL);
	}
	priv = VRT_priv_task(ctx, ns);
	if (priv == NULL) {
		VRT_fail(ctx, "cachetag.add(): no PRIV_TASK workspace");
		return (NULL);
	}
	if (priv->priv != NULL) {
		CAST_OBJ_NOTNULL(tp, priv->priv, TAG_PENDING_MAGIC);
		if (tp->oc != bo->fetch_objcore || tp->retries != bo->retries) {
			cachetag_pending_unlink(tp);
			cachetag_pending_clear_keys(tp);
			tp->oc = bo->fetch_objcore;
			tp->retries = bo->retries;
			tp->consumed = 0;
			PTOK(pthread_mutex_lock(&ns->mtx));
			tp->next = ns->pending;
			ns->pending = tp;
			PTOK(pthread_mutex_unlock(&ns->mtx));
		}
		return (tp);
	}
	ALLOC_OBJ(tp, TAG_PENDING_MAGIC);
	if (tp == NULL) {
		VRT_fail(ctx, "cachetag.add(): out of memory");
		return (NULL);
	}
	tp->ns = ns;
	tp->oc = bo->fetch_objcore;
	tp->retries = bo->retries;
	cachetag_pending_init_keys(tp);
	priv->priv = tp;
	priv->methods = cachetag_pending_priv_methods;
	PTOK(pthread_mutex_lock(&ns->mtx));
	tp->next = ns->pending;
	ns->pending = tp;
	PTOK(pthread_mutex_unlock(&ns->mtx));
	return (tp);
}

static char *
cachetag_trimdup(const char *s, size_t l)
{
	const char *b, *e;
	char *p;
	size_t n;

	b = s;
	e = s + l;
	while (b < e && isspace((unsigned char)*b))
		b++;
	while (e > b && isspace((unsigned char)e[-1]))
		e--;
	n = (size_t)(e - b);
	p = malloc(n + 1);
	if (p == NULL)
		return (NULL);
	memcpy(p, b, n);
	p[n] = '\0';
	return (p);
}

static int
cachetag_has_embedded_ws(const char *s)
{

	for (; *s != '\0'; s++) {
		if (isspace((unsigned char)*s))
			return (1);
	}
	return (0);
}

static int
cachetag_add_key(VRT_CTX, struct vmod_cachetag_namespace *ns, const char *key)
{
	struct cachetag_registration_snapshot snap;
	struct cachetag_pending *tp;
	int r;

	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (key == NULL || *key == '\0') {
		VRT_fail(ctx, "cachetag.add(): empty key");
		return (EINVAL);
	}
	if (ctx->bo != NULL && ctx->bo->fetch_objcore != NULL &&
	    (ctx->bo->fetch_objcore->flags & OC_F_PRIVATE))
		return (0);
	if (cachetag_namespace_warm(ns) != 0) {
		VRT_fail(ctx, "cachetag.add(): warmup failed");
		return (EIO);
	}
	if (cachetag_persist_prepare(ns->index) != 0) {
		VRT_fail(ctx, "cachetag.add(): persistence backend unavailable");
		return (EIO);
	}
	tp = cachetag_get_pending(ctx, ns);
	if (tp == NULL)
		return (EINVAL);
	if (!tp->publication_held) {
		r = cachetag_publication_enter(ns->index,
		    &tp->publication_phase, &tp->publication_seq);
		if (r != 0) {
			VRT_fail(ctx, "cachetag.add(): publication allocation failed");
			return (r);
		}
		tp->publication_held = 1;
	}
	r = cachetag_registration_snapshot(ns->index, key, &snap);
	snap.reg_seq = tp->publication_seq;
	if (r == EINVAL) {
		VRT_fail(ctx, "cachetag.add(): invalid key");
		return (r);
	}
	if (r == E2BIG)
		cachetag_count_limit_rejection(ns->index);
	if (r != 0) {
		VRT_fail(ctx, "cachetag.add(): key allocation failed");
		return (r);
	}
	/* The object limit counts unique keys, so a duplicate of an
	 * already-pending key must be recognized before the limit check. */
	if (cachetag_pending_contains(tp, &snap))
		return (0);
	if (tp->nkeys >= cachetag_get_limits(ns->index)->max_keys_per_object) {
		cachetag_count_limit_rejection(ns->index);
		VRT_fail(ctx, "cachetag.add(): too many keys for object");
		return (E2BIG);
	}
	r = cachetag_pending_collect_unique(tp, &snap);
	if (r != 0) {
		VRT_fail(ctx, "cachetag.add(): pending allocation failed");
		return (r);
	}
	return (r);
}

VCL_VOID v_matchproto_(td_cachetag_namespace_add)
vmod_namespace_add(VRT_CTX, struct vmod_cachetag_namespace *ns, VCL_STRING key)
{

	CHECK_OBJ_NOTNULL(ctx, VRT_CTX_MAGIC);
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	(void)cachetag_add_key(ctx, ns, key);
	cachetag_vsc_update(ns);
}

static int
cachetag_parse_add(VRT_CTX, struct vmod_cachetag_namespace *ns,
    VCL_STRING header, VCL_STRING sep)
{
	const char *p, *q;
	char *tok;
	size_t sepl, hl, tl;
	int r;

	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (header == NULL || *header == '\0')
		return (0);
	if (sep == NULL || *sep == '\0')
		sep = ",";
	sepl = strlen(sep);
	hl = strlen(header);
	if (hl > cachetag_get_limits(ns->index)->max_header_bytes) {
		cachetag_count_limit_rejection(ns->index);
		VRT_fail(ctx, "cachetag.add_header(): header too large");
		return (E2BIG);
	}
	for (p = header; ; p = q + sepl) {
		q = strstr(p, sep);
		tl = q == NULL ? strlen(p) : (size_t)(q - p);
		tok = cachetag_trimdup(p, tl);
		if (tok == NULL)
			return (ENOMEM);
		if (*tok != '\0') {
			if (cachetag_has_embedded_ws(tok)) {
				cachetag_count_parse_error(ns->index);
				free(tok);
				return (EINVAL);
			}
			/* Unique-key count is bounded fail-closed by
			 * max_keys_per_object inside cachetag_add_key();
			 * total parse work is bounded by max_header_bytes. */
			r = cachetag_add_key(ctx, ns, tok);
			if (r != 0) {
				free(tok);
				return (r);
			}
		}
		free(tok);
		if (q == NULL)
			break;
	}
	return (0);
}

static VCL_INT
cachetag_purge_header_tokens(VRT_CTX, struct vmod_cachetag_namespace *ns,
    VCL_STRING header, VCL_STRING sep, VCL_ENUM mode_e)
{
	const char *p, *q;
	char *tok;
	char **tokens, **grown;
	size_t sepl, hl, tl, cap, nkeys = 0, u;
	VCL_INT r;

	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (header == NULL || *header == '\0')
		return (0);
	if (sep == NULL || *sep == '\0')
		sep = ",";
	sepl = strlen(sep);
	hl = strlen(header);
	if (hl > cachetag_get_limits(ns->index)->max_header_bytes) {
		cachetag_count_limit_rejection(ns->index);
		return (-2);
	}
	/* Token count has no independent ceiling: it is bounded by
	 * max_header_bytes above.  The token vector grows on demand. */
	cap = 64;
	tokens = malloc(cap * sizeof *tokens);
	if (tokens == NULL)
		return (-2);
	for (p = header; ; p = q + sepl) {
		q = strstr(p, sep);
		tl = q == NULL ? strlen(p) : (size_t)(q - p);
		tok = cachetag_trimdup(p, tl);
		if (tok == NULL) {
			r = -2;
			goto fail;
		}
		if (*tok != '\0') {
			if (cachetag_has_embedded_ws(tok)) {
				cachetag_count_parse_error(ns->index);
				free(tok);
				r = -3;
				goto fail;
			}
			if (strlen(tok) > cachetag_get_limits(ns->index)->max_key_length) {
				cachetag_count_limit_rejection(ns->index);
				free(tok);
				r = -2;
				goto fail;
			}
			if (nkeys == cap) {
				if (cap > SIZE_MAX / 2 / sizeof *tokens) {
					cachetag_count_limit_rejection(ns->index);
					free(tok);
					r = -2;
					goto fail;
				}
				grown = realloc(tokens, 2 * cap * sizeof *tokens);
				if (grown == NULL) {
					free(tok);
					r = -2;
					goto fail;
				}
				tokens = grown;
				cap *= 2;
			}
			tokens[nkeys++] = tok;
			tok = NULL;
		}
		free(tok);
		if (q == NULL)
			break;
	}
	if (nkeys == 0) {
		free(tokens);
		return (0);
	}
	/* All syntax and per-key limits were checked above.  Durable publication
	 * is intentionally sequential: a later WAL failure may leave earlier
	 * tokens published, but is reported to the caller. */
	r = -1;
	for (u = 0; u < nkeys; u++) {
		if (r == -1)
			r = vmod_namespace_purge(ctx, ns, tokens[u], mode_e);
		free(tokens[u]);
	}
	free(tokens);
	return (r);
 fail:
	for (u = 0; u < nkeys; u++)
		free(tokens[u]);
	free(tokens);
	return (r);
}

VCL_VOID v_matchproto_(td_cachetag_namespace_add_header)
vmod_namespace_add_header(VRT_CTX, struct vmod_cachetag_namespace *ns,
    VCL_STRING header, VCL_STRING sep)
{
	int r;

	CHECK_OBJ_NOTNULL(ctx, VRT_CTX_MAGIC);
	r = cachetag_parse_add(ctx, ns, header, sep);
	if (r != 0)
		VRT_fail(ctx, "cachetag.add_header(): parse or limit failure");
	cachetag_vsc_update(ns);
}

static enum cachetag_purge_mode
cachetag_parse_mode(VCL_ENUM e)
{

	if (e == VENUM(hard))
		return (TAG_PURGE_HARD);
	if (e == VENUM(soft))
		return (TAG_PURGE_SOFT);
	WRONG("illegal tag purge mode");
}

static enum cachetag_wal_fsync
cachetag_parse_wal_fsync(VCL_ENUM e)
{

	if (e == VENUM(strict))
		return (TAG_WAL_FSYNC_STRICT);
	if (e == VENUM(grouped))
		return (TAG_WAL_FSYNC_GROUPED);
	WRONG("illegal tag WAL fsync mode");
}

static struct worker *
cachetag_ctx_worker(VRT_CTX)
{

	CHECK_OBJ_NOTNULL(ctx, VRT_CTX_MAGIC);
	if (ctx->bo != NULL)
		return (ctx->bo->wrk);
	if (ctx->req != NULL)
		return (ctx->req->wrk);
	return (NULL);
}

VCL_INT v_matchproto_(td_cachetag_namespace_purge)
vmod_namespace_purge(VRT_CTX, struct vmod_cachetag_namespace *ns, VCL_STRING key,
    VCL_ENUM mode_e)
{
	VCL_INT r;

	CHECK_OBJ_NOTNULL(ctx, VRT_CTX_MAGIC);
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	r = cachetag_purge(ns->index, key, cachetag_parse_mode(mode_e));
	cachetag_vsc_update(ns);
	return (r);
}

VCL_INT v_matchproto_(td_cachetag_namespace_purge_header)
vmod_namespace_purge_header(VRT_CTX, struct vmod_cachetag_namespace *ns,
    VCL_STRING header, VCL_STRING sep, VCL_ENUM mode_e)
{
	VCL_INT r;

	CHECK_OBJ_NOTNULL(ctx, VRT_CTX_MAGIC);
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	r = cachetag_purge_header_tokens(ctx, ns, header, sep, mode_e);
	cachetag_vsc_update(ns);
	return (r);
}

#if CACHE_TAG_DEMO_DIAGNOSTICS

VCL_INT v_matchproto_(td_cachetag_namespace_generation)
vmod_namespace_generation(VRT_CTX, struct vmod_cachetag_namespace *ns,
    VCL_STRING key)
{
	uint64_t generation;
	int r;

	CHECK_OBJ_NOTNULL(ctx, VRT_CTX_MAGIC);
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	r = cachetag_generation(ns->index, key, &generation);
	if (r == EINVAL)
		return (-3);
	if (r != 0 || generation > INT64_MAX)
		return (-2);
	return ((VCL_INT)generation);
}

static VCL_INT
cachetag_diag_int(uint64_t value)
{

	if (value > INT64_MAX)
		return (-2);
	return ((VCL_INT)value);
}

VCL_INT v_matchproto_(td_cachetag_namespace_purge_seq)
vmod_namespace_purge_seq(VRT_CTX, struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_diag_int(cachetag_purgemap_seq(ns->index)));
}

VCL_INT v_matchproto_(td_cachetag_namespace_purgemap_entries)
vmod_namespace_purgemap_entries(VRT_CTX, struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_diag_int(cachetag_purgemap_entry_count(ns->index)));
}

VCL_INT v_matchproto_(td_cachetag_namespace_purgemap_slots)
vmod_namespace_purgemap_slots(VRT_CTX, struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_diag_int(cachetag_purgemap_slot_count(ns->index)));
}

VCL_INT v_matchproto_(td_cachetag_namespace_purgemap_bytes)
vmod_namespace_purgemap_bytes(VRT_CTX, struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_diag_int(cachetag_purgemap_byte_count(ns->index)));
}

#endif /* CACHE_TAG_DEMO_DIAGNOSTICS */

static int
cachetag_pending_probe(void *priv, struct objcore *oc,
    enum cachetag_purge_mode *modep, int *foundp)
{
	struct vmod_cachetag_namespace *ns;
	struct cachetag_pending *tp;
	int r = 0;

	CAST_OBJ_NOTNULL(ns, priv, TAG_NAMESPACE_MAGIC);
	CHECK_OBJ_NOTNULL(oc, OBJCORE_MAGIC);
	AN(modep);
	AN(foundp);
	*modep = (enum cachetag_purge_mode)-1;
	*foundp = 0;
	PTOK(pthread_mutex_lock(&ns->mtx));
	tp = cachetag_find_pending_locked(ns, oc, 1);
	if (tp != NULL && tp->nkeys > 0) {
		*foundp = 1;
		r = cachetag_purgemap_probe_snapshots(ns->index, tp->keys,
		    tp->nkeys, modep);
	}
	PTOK(pthread_mutex_unlock(&ns->mtx));
	return (r);
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_stale)
vmod_namespace_stale(VRT_CTX, struct vmod_cachetag_namespace *ns)
{
	struct objcore *oc = NULL;
	int r;

	CHECK_OBJ_NOTNULL(ctx, VRT_CTX_MAGIC);
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (ctx->req != NULL)
		oc = ctx->req->objcore;
	r = cachetag_stale(cachetag_ctx_worker(ctx), ns->index, oc,
	    cachetag_pending_probe, ns);
	if (r && oc != NULL) {
		HSH_Kill(oc);
		cachetag_death(ns->index, oc);
	}
	cachetag_vsc_update(ns);
	return (r);
}

VCL_INT v_matchproto_(td_cachetag_namespace_pending)
vmod_namespace_pending(VRT_CTX, struct vmod_cachetag_namespace *ns)
{
	struct cachetag_pending *tp;
	VCL_INT n = 0;

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	PTOK(pthread_mutex_lock(&ns->mtx));
	for (tp = ns->pending; tp != NULL; tp = tp->next) {
		CHECK_OBJ_NOTNULL(tp, TAG_PENDING_MAGIC);
		if (!tp->consumed)
			n++;
	}
	PTOK(pthread_mutex_unlock(&ns->mtx));
	cachetag_vsc_update(ns);
	return (n);
}

VCL_INT v_matchproto_(td_cachetag_namespace_objects)
vmod_namespace_objects(VRT_CTX, struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	cachetag_vsc_update(ns);
	return ((VCL_INT)cachetag_object_count(ns->index));
}

VCL_INT v_matchproto_(td_cachetag_namespace_edges)
vmod_namespace_edges(VRT_CTX, struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	cachetag_vsc_update(ns);
	return ((VCL_INT)cachetag_edge_count(ns->index));
}

VCL_INT v_matchproto_(td_cachetag_namespace_compact)
vmod_namespace_compact(VRT_CTX, struct vmod_cachetag_namespace *ns)
{
	VCL_INT r;

	CHECK_OBJ_NOTNULL(ctx, VRT_CTX_MAGIC);
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	r = (VCL_INT)cachetag_compact_all(ns->index);
	cachetag_vsc_update(ns);
	return (r);
}

#if CACHE_TAG_TEST_HOOKS

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_fail_next_key_purge_wal)
vmod_namespace_test_fail_next_key_purge_wal(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_fail_next_key_purge_wal(ns->index));
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_fail_next_persist_prepare)
vmod_namespace_test_fail_next_persist_prepare(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_fail_next_persist_prepare(ns->index));
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_side_initial_buckets)
vmod_namespace_test_side_initial_buckets(VRT_CTX,
    struct vmod_cachetag_namespace *ns, VCL_INT buckets)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (buckets <= 0 || (uint64_t)buckets > UINT32_MAX)
		return (0);
	return (cachetag_test_side_initial_buckets(ns->index,
	    (uint32_t)buckets));
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_abort_next_sweep)
vmod_namespace_test_abort_next_sweep(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_abort_next_sweep(ns->index));
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_force_next_attach_slot_overflow)
vmod_namespace_test_force_next_attach_slot_overflow(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_force_next_attach_slot_overflow(ns->index));
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_fail_next_object_segment_alloc)
vmod_namespace_test_fail_next_object_segment_alloc(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_fail_next_object_segment_alloc(ns->index));
}

#if CACHE_TAG_SET_INTERNING

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_fail_next_intern_alloc)
vmod_namespace_test_fail_next_intern_alloc(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_fail_next_intern_alloc(ns->index));
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_intern_initial_buckets)
vmod_namespace_test_intern_initial_buckets(VRT_CTX,
    struct vmod_cachetag_namespace *ns, VCL_INT buckets)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (buckets <= 0 || (uint64_t)buckets > UINT32_MAX)
		return (0);
	return (cachetag_test_intern_initial_buckets(ns->index,
	    (uint32_t)buckets));
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_fail_next_intern_table_alloc)
vmod_namespace_test_fail_next_intern_table_alloc(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_fail_next_intern_table_alloc(ns->index));
}

VCL_INT v_matchproto_(td_cachetag_namespace_test_intern_migration_active)
vmod_namespace_test_intern_migration_active(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_intern_migration_active(ns->index));
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_intern_worker_hold)
vmod_namespace_test_intern_worker_hold(VRT_CTX,
    struct vmod_cachetag_namespace *ns, VCL_BOOL hold)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_intern_worker_hold(ns->index, hold));
}

VCL_INT v_matchproto_(td_cachetag_namespace_test_intern_migrate_buckets)
vmod_namespace_test_intern_migrate_buckets(VRT_CTX,
    struct vmod_cachetag_namespace *ns, VCL_INT buckets)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (buckets <= 0 || (uint64_t)buckets > UINT32_MAX)
		return (0);
	return (cachetag_test_intern_migrate_buckets(ns->index,
	    (uint32_t)buckets));
}

VCL_INT v_matchproto_(td_cachetag_namespace_test_intern_active_buckets)
vmod_namespace_test_intern_active_buckets(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_intern_active_buckets(ns->index));
}

VCL_INT v_matchproto_(td_cachetag_namespace_test_intern_old_buckets)
vmod_namespace_test_intern_old_buckets(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_intern_old_buckets(ns->index));
}

#endif /* CACHE_TAG_SET_INTERNING */

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_structural_limits)
vmod_namespace_test_structural_limits(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_structural_limits(ns->index));
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_side_fingerprint_bits)
vmod_namespace_test_side_fingerprint_bits(VRT_CTX,
    struct vmod_cachetag_namespace *ns, VCL_INT bits)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (bits < 0 || bits > 32)
		return (0);
	return (cachetag_test_side_fingerprint_bits(ns->index,
	    (uint32_t)bits));
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_side_start_migration)
vmod_namespace_test_side_start_migration(VRT_CTX,
    struct vmod_cachetag_namespace *ns, VCL_INT buckets)
{
	int r;

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (buckets <= 0 || (uint64_t)buckets > UINT32_MAX)
		return (0);
	r = cachetag_test_side_start_migration(ns->index, (uint32_t)buckets);
	cachetag_vsc_update(ns);
	return (r);
}

VCL_INT v_matchproto_(td_cachetag_namespace_test_side_migrate_buckets)
vmod_namespace_test_side_migrate_buckets(VRT_CTX,
    struct vmod_cachetag_namespace *ns, VCL_INT buckets)
{
	int r;

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (buckets <= 0 || (uint64_t)buckets > UINT32_MAX)
		return (0);
	r = cachetag_test_side_migrate_buckets(ns->index, (uint32_t)buckets);
	cachetag_vsc_update(ns);
	return (r);
}

VCL_INT v_matchproto_(td_cachetag_namespace_test_side_migration_active)
vmod_namespace_test_side_migration_active(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{
	int r;

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	r = cachetag_test_side_migration_active(ns->index);
	cachetag_vsc_update(ns);
	return (r);
}

VCL_INT v_matchproto_(td_cachetag_namespace_test_side_table_buckets)
vmod_namespace_test_side_table_buckets(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_side_table_buckets(ns->index));
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_fail_next_side_migration_alloc)
vmod_namespace_test_fail_next_side_migration_alloc(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_test_fail_next_side_migration_alloc(ns->index));
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_resize_low_water_ready)
vmod_namespace_test_resize_low_water_ready(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{
	int r;

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	r = cachetag_test_resize_low_water_ready(ns->index);
	cachetag_vsc_update(ns);
	return (r);
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_resize_worker_drain)
vmod_namespace_test_resize_worker_drain(VRT_CTX,
    struct vmod_cachetag_namespace *ns, VCL_INT timeout_ms)
{
	int r;

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (timeout_ms < 0 || (uint64_t)timeout_ms > UINT32_MAX)
		return (0);
	r = cachetag_test_resize_worker_drain(ns->index, (uint32_t)timeout_ms);
	cachetag_vsc_update(ns);
	return (r);
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_next_fellow_attr_version)
vmod_namespace_test_next_fellow_attr_version(VRT_CTX,
    struct vmod_cachetag_namespace *ns, VCL_INT version)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	if (version <= 0 || (uint64_t)version > UINT16_MAX)
		return (0);
	PTOK(pthread_mutex_lock(&ns->mtx));
	ns->test_next_fellow_attr_version = (uint16_t)version;
	PTOK(pthread_mutex_unlock(&ns->mtx));
	return (1);
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_next_fellow_attr_bad_length)
vmod_namespace_test_next_fellow_attr_bad_length(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	PTOK(pthread_mutex_lock(&ns->mtx));
	ns->test_next_fellow_attr_bad_length = 1;
	PTOK(pthread_mutex_unlock(&ns->mtx));
	return (1);
}

static enum cachetag_fellow_attr_corruption
cachetag_fellow_attr_corruption_e2u(VCL_ENUM corruption)
{

	if (corruption == VENUM(record_len))
		return (TAG_FELLOW_ATTR_CORRUPT_RECORD_LEN);
	if (corruption == VENUM(fold_count))
		return (TAG_FELLOW_ATTR_CORRUPT_FOLD_COUNT);
	if (corruption == VENUM(envelope_flags))
		return (TAG_FELLOW_ATTR_CORRUPT_ENVELOPE_FLAGS);
	if (corruption == VENUM(envelope_reserved))
		return (TAG_FELLOW_ATTR_CORRUPT_ENVELOPE_RESERVED);
	if (corruption == VENUM(record_flags))
		return (TAG_FELLOW_ATTR_CORRUPT_RECORD_FLAGS);
	if (corruption == VENUM(duplicate_namespace))
		return (TAG_FELLOW_ATTR_CORRUPT_DUPLICATE_NAMESPACE);
	WRONG("unknown Fellow attr corruption enum");
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_next_fellow_attr_corruption)
vmod_namespace_test_next_fellow_attr_corruption(VRT_CTX,
    struct vmod_cachetag_namespace *ns, VCL_ENUM corruption)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	PTOK(pthread_mutex_lock(&ns->mtx));
	ns->test_next_fellow_attr_corruption =
	    cachetag_fellow_attr_corruption_e2u(corruption);
	PTOK(pthread_mutex_unlock(&ns->mtx));
	return (1);
}

VCL_BOOL v_matchproto_(td_cachetag_namespace_test_next_fellow_attr_read_failure)
vmod_namespace_test_next_fellow_attr_read_failure(VRT_CTX,
    struct vmod_cachetag_namespace *ns)
{

	(void)ctx;
	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	return (cachetag_fellow_test_fail_next_visit());
}

#endif /* CACHE_TAG_TEST_HOOKS */

static void
cachetag_apply_attach_purge(struct worker *wrk, struct objcore *oc,
    enum cachetag_purge_mode mode)
{

	if (wrk != NULL)
		CHECK_OBJ_NOTNULL(wrk, WORKER_MAGIC);
	CHECK_OBJ_NOTNULL(oc, OBJCORE_MAGIC);
	if (mode == TAG_PURGE_HARD) {
		if (oc->flags & OC_F_BUSY)
			return;
		HSH_Kill(oc);
	} else if (mode == TAG_PURGE_SOFT) {
		EXP_Reduce(oc, VTIM_real(), 0, NAN, NAN);
	}
}

static void v_matchproto_(obj_event_f)
cachetag_obj_cb(struct worker *wrk, void *priv, struct objcore *oc, unsigned event)
{
	struct vmod_cachetag_namespace *ns;
	struct cachetag_pending *tp;
	enum cachetag_purge_mode attach_purge;
	enum cachetag_purge_mode exit_purge = (enum cachetag_purge_mode)-1;
	int exit_kill = 0;
	int r;

	CHECK_OBJ_NOTNULL(wrk, WORKER_MAGIC);
	CHECK_OBJ_NOTNULL(oc, OBJCORE_MAGIC);
	CAST_OBJ_NOTNULL(ns, priv, TAG_NAMESPACE_MAGIC);
	if (event == OEV_EXPIRE) {
		/* Vinyl emits OEV_EXPIRE synchronously for every real cache
		 * departure while oc is still valid, including explicit removal
		 * before nominal TTL. FDO-direct Fellow objects have no volatile
		 * record, so this is a cheap no-op for them. */
		cachetag_death(ns->index, oc);
		cachetag_vsc_update(ns);
		return;
	}
	if (event != OEV_INSERT)
		WRONG("wrong object event");
	PTOK(pthread_mutex_lock(&ns->mtx));
	tp = cachetag_find_pending_locked(ns, oc, 0);
	if (tp != NULL)
		tp->consumed = 1;
	PTOK(pthread_mutex_unlock(&ns->mtx));
	if (tp == NULL) {
		cachetag_vsc_update(ns);
		return;
	}
	attach_purge = (enum cachetag_purge_mode)-1;
	if (oc->flags & OC_F_TRANSIENT) {
		/* Pass, hit-for-miss, hit-for-pass, and private objects are not
		 * reusable cache membership.  A cacheable Fellow allocation that
		 * falls back to Transient has none of these flags and is handled by
		 * the not-Fellow volatile-provider branch below. */
	} else if (cachetag_persist_enabled(ns->index)) {
		if (!tp->fellow_attr_included) {
			if (cachetag_fellow_object_kind(wrk, oc) == -2) {
				cachetag_note_fellow_metric(ns->index,
				    TAG_FELLOW_VOLATILE_FALLBACK_ATTACHES, 1);
				r = cachetag_attach(ns->index, oc, tp->keys,
				    tp->nkeys, &attach_purge);
				if (r == 0 && (attach_purge == TAG_PURGE_HARD ||
				    attach_purge == TAG_PURGE_SOFT))
					exit_purge = attach_purge;
			} else {
				/* A Fellow tagged object without inclusion proof
				 * is unsafe. */
				cachetag_note_fellow_metric(ns->index,
				    TAG_FELLOW_STORE_INVARIANT_FAILURES, 1);
				exit_kill = 1;
			}
		} else if (cachetag_purgemap_probe_snapshots(ns->index, tp->keys,
		    tp->nkeys, &attach_purge) != 0) {
			exit_kill = 1;
		} else if (attach_purge == TAG_PURGE_HARD ||
		    attach_purge == TAG_PURGE_SOFT) {
			exit_purge = attach_purge;
		}
	} else {
		r = cachetag_attach(ns->index, oc, tp->keys, tp->nkeys,
		    &attach_purge);
		if (r == 0 && (attach_purge == TAG_PURGE_HARD ||
		    attach_purge == TAG_PURGE_SOFT))
			exit_purge = attach_purge;
	}
	if (exit_kill) {
		HSH_Kill(oc);
		cachetag_death(ns->index, oc);
	} else if (exit_purge == TAG_PURGE_HARD ||
	    exit_purge == TAG_PURGE_SOFT) {
		cachetag_apply_attach_purge(wrk, oc, exit_purge);
		if (exit_purge == TAG_PURGE_HARD)
			cachetag_death(ns->index, oc);
	}
	cachetag_pending_publication_done(tp);
	cachetag_vsc_update(ns);
}

static void
cachetag_fellow_provider_ensure(pid_t pid, unsigned *readyp,
    unsigned *absentp, unsigned *api_seenp)
{
	struct cachetag_fellow_registration *regs = NULL, *extra = NULL;
	unsigned need_register, api_seen = 0;

	AN(readyp);
	AN(absentp);
	AN(api_seenp);
	PTOK(pthread_mutex_lock(&cachetag_fellow_provider_mtx));
	PTOK(pthread_mutex_lock(&cachetag_global_mtx));
	if (cachetag_fellow_regs_pid != 0 && cachetag_fellow_regs_pid != pid) {
		cachetag_fellow_regs = NULL;
		cachetag_fellow_regs_pid = 0;
	}
	if (cachetag_fellow_absent_pid != 0 &&
	    cachetag_fellow_absent_pid != pid)
		cachetag_fellow_absent_pid = 0;
	need_register = cachetag_fellow_regs_pid != pid;
	PTOK(pthread_mutex_unlock(&cachetag_global_mtx));
	if (need_register)
		(void)cachetag_fellow_register_all(&regs,
		    cachetag_fellow_attr_size_cb, cachetag_fellow_attr_fill_cb,
		    NULL, &api_seen);
	PTOK(pthread_mutex_lock(&cachetag_global_mtx));
	if (cachetag_fellow_regs_pid != pid && regs != NULL) {
		cachetag_fellow_regs = regs;
		cachetag_fellow_regs_pid = pid;
		cachetag_fellow_absent_pid = 0;
		regs = NULL;
	} else if (regs != NULL)
		extra = regs;
	if (cachetag_fellow_regs_pid != pid && need_register && !api_seen)
		cachetag_fellow_absent_pid = pid;
	*readyp = cachetag_fellow_regs != NULL &&
	    cachetag_fellow_regs_pid == pid;
	*absentp = cachetag_fellow_absent_pid == pid;
	*api_seenp = api_seen;
	PTOK(pthread_mutex_unlock(&cachetag_global_mtx));
	if (extra != NULL)
		cachetag_fellow_unregister_all(&extra);
	PTOK(pthread_mutex_unlock(&cachetag_fellow_provider_mtx));
}

static int
cachetag_namespace_warm(struct vmod_cachetag_namespace *ns)
{
	pid_t pid;
	int r;
	uintptr_t h = 0, extra = 0;
	unsigned api_seen = 0;
	unsigned fellow_ready = 0;
	unsigned fellow_absent = 0;
	unsigned obj_ready = 0;

	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	pid = getpid();
	PTOK(pthread_mutex_lock(&ns->mtx));
	if (ns->warm_pid == pid) {
		PTOK(pthread_mutex_unlock(&ns->mtx));
		return (0);
	}
	if (ns->warm_pid != 0 && ns->warm_pid != pid)
		ns->warm_pid = 0;
	PTOK(pthread_mutex_unlock(&ns->mtx));
	r = cachetag_index_start(ns->index);
	if (r != 0)
		return (r);
	PTOK(pthread_mutex_lock(&ns->mtx));
	if (ns->obj_cb != 0 && ns->obj_cb_pid != pid) {
		ns->obj_cb = 0;
		ns->obj_cb_pid = 0;
	}
	if (ns->obj_cb == 0) {
		PTOK(pthread_mutex_unlock(&ns->mtx));
		h = ObjSubscribeEvents(cachetag_obj_cb, ns, OEV_INSERT | OEV_EXPIRE);
		PTOK(pthread_mutex_lock(&ns->mtx));
		if (ns->obj_cb == 0) {
			ns->obj_cb = h;
			ns->obj_cb_pid = pid;
		} else
			extra = h;
	}
	PTOK(pthread_mutex_unlock(&ns->mtx));
	if (extra != 0)
		ObjUnsubscribeEvents(&extra);
	PTOK(pthread_mutex_lock(&ns->mtx));
	obj_ready = (ns->obj_cb != 0 && ns->obj_cb_pid == pid);
	PTOK(pthread_mutex_unlock(&ns->mtx));
	if (cachetag_persist_enabled(ns->index)) {
		cachetag_fellow_provider_ensure(pid, &fellow_ready,
		    &fellow_absent, &api_seen);
	} else {
		fellow_ready = 1;
		fellow_absent = 1;
	}
	if (cachetag_persist_enabled(ns->index) && !fellow_ready) {
		cachetag_namespace_cold(ns);
		return (ENOSYS);
	}
	if (obj_ready && (fellow_ready || fellow_absent || !api_seen)) {
		PTOK(pthread_mutex_lock(&ns->mtx));
		ns->fellow_direct_active = fellow_ready &&
		    cachetag_persist_enabled(ns->index);
		ns->warm_pid = pid;
		PTOK(pthread_mutex_unlock(&ns->mtx));
	}
	cachetag_vsc_update(ns);
	return (0);
}

static void
cachetag_namespace_cold(struct vmod_cachetag_namespace *ns)
{
	uintptr_t h;

	CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
	PTOK(pthread_mutex_lock(&ns->mtx));
	ns->warm_pid = 0;
	ns->fellow_direct_active = 0;
	if (ns->obj_cb_pid == getpid()) {
		h = ns->obj_cb;
		ns->obj_cb = 0;
		ns->obj_cb_pid = 0;
	} else {
		h = 0;
	}
	PTOK(pthread_mutex_unlock(&ns->mtx));
	cachetag_index_stop(ns->index);
	if (h != 0)
		ObjUnsubscribeEvents(&h);
	cachetag_index_detach_all(ns->index);
	cachetag_fellow_provider_release_if_idle();
}

VCL_VOID v_matchproto_(td_cachetag_namespace__init)
vmod_namespace__init(VRT_CTX, struct vmod_cachetag_namespace **nsp,
    const char *vcl_name, VCL_STRING name, VCL_INT max_keys_per_object,
    VCL_INT max_key_length, VCL_INT max_tag_header_bytes,
    VCL_STRING persist_path, VCL_ENUM wal_fsync, VCL_INT wal_segment_bytes,
    VCL_DURATION sweep_interval, VCL_INT purge_history_max_entries,
    VCL_INT sweep_batch_objects, VCL_DURATION sweep_batch_hold,
    VCL_DURATION sweep_batch_yield)
{
	struct vmod_cachetag_namespace *ns;
	char *vsc_name;
	struct cachetag_limits limits;
	struct cachetag_persist_config persist;
	int r;

	CHECK_OBJ_NOTNULL(ctx, VRT_CTX_MAGIC);
	AN(nsp);
	AZ(*nsp);
	cachetag_limits_default(&limits);
	if (max_keys_per_object > 0)
		limits.max_keys_per_object = (unsigned)max_keys_per_object;
	if (max_key_length > 0)
		limits.max_key_length = (unsigned)max_key_length;
	if (max_tag_header_bytes > 0)
		limits.max_header_bytes = (unsigned)max_tag_header_bytes;
	if (sweep_interval >= 0)
		limits.purgemap_sweep_interval = sweep_interval;
	if (purge_history_max_entries >= 0)
		limits.purgemap_history_max_entries =
		    (uint64_t)purge_history_max_entries;
	if (sweep_batch_objects > 0)
		limits.purgemap_sweep_batch_objects =
		    (uint64_t)sweep_batch_objects;
	if (sweep_batch_hold >= 0)
		limits.purgemap_sweep_batch_usec =
		    (uint64_t)(sweep_batch_hold * 1000000.0);
	if (sweep_batch_yield >= 0)
		limits.purgemap_sweep_batch_yield_usec =
		    (uint64_t)(sweep_batch_yield * 1000000.0);
	memset(&persist, 0, sizeof persist);
	persist.path = persist_path;
	persist.wal_fsync = cachetag_parse_wal_fsync(wal_fsync);
	if (wal_segment_bytes > 0)
		persist.wal_segment_bytes = (uint64_t)wal_segment_bytes;

	ALLOC_OBJ(ns, TAG_NAMESPACE_MAGIC);
	AN(ns);
	ns->vcl = ctx->vcl;
	ns->vcl_name = strdup(vcl_name);
	AN(ns->vcl_name);
	PTOK(pthread_mutex_init(&ns->mtx, NULL));
	ns->index = cachetag_index_new(name, &limits, &persist);
	AN(ns->index);
	cachetag_namespace_digest(ns->index, &ns->namespace_digest_hi,
	    &ns->namespace_digest_lo);
	vsc_name = cachetag_vsc_ident(VCL_Name(ctx->vcl), ns->vcl_name,
	    cachetag_namespace_name(ns->index));
	AN(vsc_name);
	ns->vsc = VSC_cachetag_New(NULL, &ns->vsc_seg, vsc_name);
	free(vsc_name);
	cachetag_namespace_global_add(ns);
	r = cachetag_namespace_warm(ns);
	if (r != 0) {
		VRT_fail(ctx, "cachetag.namespace(): warmup failed: %s",
		    strerror(r));
		cachetag_namespace_global_remove(ns);
		if (ns->vsc != NULL)
			VSC_cachetag_Destroy(&ns->vsc_seg);
		cachetag_index_delete(&ns->index);
		PTOK(pthread_mutex_destroy(&ns->mtx));
		free(ns->vcl_name);
		FREE_OBJ(ns);
		return;
	}
	*nsp = ns;
}

VCL_VOID v_matchproto_(td_cachetag_namespace__fini)
vmod_namespace__fini(struct vmod_cachetag_namespace **nsp)
{
	struct vmod_cachetag_namespace *ns;
	struct cachetag_pending *tp, *tp2;

	TAKE_OBJ_NOTNULL(ns, nsp, TAG_NAMESPACE_MAGIC);
	cachetag_namespace_global_remove(ns);
	cachetag_namespace_cold(ns);
	for (tp = ns->pending; tp != NULL; tp = tp2) {
		tp2 = tp->next;
		cachetag_pending_free(tp);
	}
	if (ns->vsc != NULL)
		VSC_cachetag_Destroy(&ns->vsc_seg);
	cachetag_index_delete(&ns->index);
	PTOK(pthread_mutex_destroy(&ns->mtx));
	free(ns->vcl_name);
	FREE_OBJ(ns);
}

int v_matchproto_(vmod_event_f)
vmod_event_function(VRT_CTX, struct vmod_priv *priv, enum vcl_event_e e)
{
	struct vmod_cachetag_namespace *ns, **list = NULL;
	size_t count = 0, u = 0;
	int r = 0;

	(void)ctx;
	(void)priv;
	PTOK(pthread_mutex_lock(&cachetag_global_mtx));
	for (ns = cachetag_namespaces; ns != NULL; ns = ns->global_next) {
		CHECK_OBJ_NOTNULL(ns, TAG_NAMESPACE_MAGIC);
		if (ns->vcl == ctx->vcl)
			count++;
	}
	if (count != 0)
		list = calloc(count, sizeof *list);
	if (list != NULL) {
		for (ns = cachetag_namespaces; ns != NULL; ns = ns->global_next) {
			if (ns->vcl == ctx->vcl)
				list[u++] = ns;
		}
	}
	PTOK(pthread_mutex_unlock(&cachetag_global_mtx));
	if (count != 0 && list == NULL)
		return (-1);
	/* VCL lifecycle events are manager-thread serialized.  Drop the registry
	 * lock before entering Fellow registration/unregistration paths. */
	for (u = 0; u < count; u++) {
		ns = list[u];
		switch (e) {
		case VCL_EVENT_WARM:
			r = cachetag_namespace_warm(ns);
			if (r != 0) {
				free(list);
				return (-1);
			}
			break;
		case VCL_EVENT_COLD:
			cachetag_namespace_cold(ns);
			break;
		default:
			break;
		}
	}
	free(list);
	return (0);
}
