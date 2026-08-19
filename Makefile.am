ACLOCAL_AMFLAGS = -I m4 -I ${VINYLAPI_DATAROOTDIR}/aclocal

DISTCHECK_CONFIGURE_FLAGS = RST2MAN=: $(CACHETAG_ENABLED_CONFIGURE_FLAGS)

SUBDIRS = src

EXTRA_DIST = \
	LICENSE \
	README.md \
	USAGE.md \
	INSTALL.md \
	docs/vmod_cachetag.rst \
	patches/fellow/README.md \
	patches/fellow/0002-add-fellow-object-attr-provider.patch \
	patches/fellow/0003-fix-fellow-shutdown-drain-races.patch \
	patches/fellow/0004-fellow_logcache-bound-entries-by-scratch-capacity.patch \
	patches/fellow/0005-fellow_log-drain-close-time-free-regions-incremental.patch \
	patches/fellow/0006-fellow_logwatcher-stop-without-pre-close-rewrite.patch \
	patches/fellow/0007-fellow_log-avoid-close-time-rewrite-work.patch \
	patches/fellow/0008-fellow_log-write-close-active-block-synchronously.patch \
	patches/fellow/0009-fix-fellow-high-fanout-shutdown-reset.patch \
	patches/fellow/0010-suppress-fellow-logwatcher-rewrites-during-shutdown.patch \
	patches/fellow/0011-skip-fellow-logwatcher-close-flush-on-shutdown.patch \
	patches/fellow/0012-fellow-storage-skip-drain-idle-grace-on-shutdown.patch \
	patches/fellow/0013-fellow-storage-skip-global-shutdown-drain.patch \
	patches/fellow/0014-fellow-object-attr-add-read-failure-test-hook.patch

.PHONY: check-with-vinyl-cache check-fellow-with-vinyl-cache

check-with-vinyl-cache:
	scripts/test-with-vinyl-cache.sh "$(VINYL_CACHE_SRC)"

check-fellow-with-vinyl-cache:
	scripts/test-fellow-with-vinyl-cache.sh "$(VINYL_CACHE_SRC)"
