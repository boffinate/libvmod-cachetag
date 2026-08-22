/*
 * xkey-on-Vinyl Cache compatibility header, version 1.
 *
 * varnish-modules 0.28.0 includes <cache/cache_varnishd.h>. Vinyl Cache
 * exposes the private declarations used by xkey through cache/cache_int.h,
 * while supported Varnish retains the original name. This header is
 * intentionally the only compatibility layer: it changes no xkey source or
 * behaviour. Its exact contents are pinned by cache_varnishd.h.sha256 and
 * recorded in every comparative benchmark provenance manifest.
 */
#ifndef CACHETAG_XKEY_COMPAT_CACHE_VARNISHD_H
#define CACHETAG_XKEY_COMPAT_CACHE_VARNISHD_H

#if defined(__has_include) && __has_include(<cache/cache_int.h>)
# include <cache/cache_int.h>
#else
# include <cache/cache_varnishd.h>
#endif

#endif
