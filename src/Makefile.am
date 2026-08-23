AM_CFLAGS = $(VINYLAPI_CFLAGS)

vmod_cachetag_symbols_regex = Vmod_cachetag_Data

# Modules

vmod_LTLIBRARIES = \
	libvmod_cachetag.la

libvmod_cachetag_la_LDFLAGS = \
	-export-symbols-regex 'Vmod_cachetag_Data' \
	$(VINYLAPI_LIBS) \
	$(VMOD_LDFLAGS)

libvmod_cachetag_la_SOURCES = \
	cachetag.vsc \
	cachetag_counters.h \
	cachetag_util.h \
	vmod_cachetag.c \
	vmod_cachetag_types.h \
	vmod_cachetag_index.c \
	vmod_cachetag_index.h \
	vmod_cachetag_index_private.h \
	vmod_cachetag_purgemap.c \
	vmod_cachetag_wal.c \
	vmod_cachetag_wal.h \
	vmod_cachetag_xxhash.c \
	xxhash.h

nodist_libvmod_cachetag_la_SOURCES = \
	VSC_cachetag.c \
	VSC_cachetag.h \
	vcc_cachetag_if.c \
	vcc_cachetag_if.h

# vmod_cachetag.vcc is a build product: the always-on core fragment plus the
# fragments selected by configure options are concatenated into the VCC file
# consumed by vmodtool (VINYL_VMODS_GENERATED in configure.ac makes vmodtool
# prefer the builddir copy). The Makefile dependency reruns the concatenation
# when configure flags change.

vmod_cachetag_vcc_fragments = $(srcdir)/vmod_cachetag_core.vcc
if CACHE_TAG_DEMO_DIAGNOSTICS
vmod_cachetag_vcc_fragments += $(srcdir)/vmod_cachetag_demo_diagnostics.vcc
endif
if CACHE_TAG_TEST_HOOKS
vmod_cachetag_vcc_fragments += $(srcdir)/vmod_cachetag_test_hooks.vcc
vmod_cachetag_vcc_fragments += $(srcdir)/vmod_cachetag_set_interning_test_hooks.vcc
endif

vmod_cachetag.vcc: $(vmod_cachetag_vcc_fragments) Makefile
	$(AM_V_GEN) rm -f $@ $@.tmp; \
	for frag in $(vmod_cachetag_vcc_fragments); do \
		cat "$$frag" && printf '\n' || exit 1; \
	done > $@.tmp && mv $@.tmp $@

vmod_cachetag.3: VSC_cachetag.rst
@BUILD_VSC_CACHETAG@
@BUILD_VMOD_CACHETAG@

all-local: vtc/.dirstamp

vtc/.dirstamp:
	$(MKDIR_P) vtc
	: > $@

CLEANFILES = \
	vtc/.dirstamp \
	vtc/.interned-generated \
	$(VTC_TESTS_GENERATED_INTERNED) \
	vmod_cachetag.vcc \
	vmod_vcs_version.txt

# Test suite

check_PROGRAMS = \
	cachetag_wal_test \
	cachetag_counter_dump

# Parity between src/cachetag.vsc and the src/cachetag_counters.h inventory the
# C side is generated from.  The dump program needs no Vinyl headers and no
# link dependencies; the script diffs it against the schema.

cachetag_counter_dump_SOURCES = \
	cachetag_counter_dump.c \
	cachetag_counters.h

cachetag_counter_dump_CFLAGS = \
	$(AM_CFLAGS)

dist_check_SCRIPTS = \
	cachetag_counter_parity.sh

cachetag_wal_test_SOURCES = \
	cachetag_util.h \
	cachetag_wal_test.c \
	vmod_cachetag_wal.c \
	vmod_cachetag_wal.h \
	vmod_cachetag_xxhash.c \
	xxhash.h

cachetag_wal_test_CFLAGS = \
	$(AM_CFLAGS)

cachetag_wal_test_LDADD = \
	$(VINYLAPI_LIBS)

AM_TESTS_ENVIRONMENT = \
	PATH="$(abs_builddir):$(VINYL_TEST_PATH):$(PATH)" \
	LD_LIBRARY_PATH="$(VINYL_LIBRARY_PATH)" \
	CACHETAG_SRCDIR='$(srcdir)' \
	CACHETAG_COUNTER_DUMP='$(abs_builddir)/cachetag_counter_dump' \
	CACHE_TAG_TEST_VSC_PUBLISH_SYNC=1
TEST_EXTENSIONS = .vtc
VTC_LOG_COMPILER = vinyltest -v
AM_VTC_LOG_FLAGS = \
	-p vcl_path="$(abs_top_srcdir)/vcl:$(VINYLAPI_VCLDIR)" \
	-p vmod_path="$(abs_builddir)/.libs:$(vmoddir):$(VINYLAPI_VMODDIR)"

# Both runtime modes need identical core scenarios. Generate interned copies
# from the checked-in default/direct core so their coverage cannot drift apart.

VTC_TESTS_CORE = \
	vtc/cachetag_c00000.vtc \
	vtc/cachetag_c00002.vtc \
	vtc/cachetag_c00006.vtc \
	vtc/cachetag_c00007.vtc \
	vtc/cachetag_c00008.vtc \
	vtc/cachetag_c00009.vtc \
	vtc/cachetag_c00010.vtc \
	vtc/cachetag_c00015.vtc \
	vtc/cachetag_c00016.vtc \
	vtc/cachetag_c00018.vtc \
	vtc/cachetag_c00019.vtc \
	vtc/cachetag_c00021.vtc \
	vtc/cachetag_c00022.vtc \
	vtc/cachetag_c00023.vtc \
	vtc/cachetag_c00024.vtc \
	vtc/cachetag_c00026.vtc \
	vtc/cachetag_c00027.vtc \
	vtc/cachetag_c00028.vtc \
	vtc/cachetag_r00001.vtc \
	vtc/cachetag_r00002.vtc \
	vtc/cachetag_r00003.vtc \
	vtc/cachetag_r00004.vtc \
	vtc/cachetag_r00005.vtc \
	vtc/cachetag_r00006.vtc \
	vtc/cachetag_r00008.vtc \
	vtc/cachetag_r00009.vtc \
	vtc/cachetag_pm00000.vtc \
	vtc/cachetag_pm00001.vtc \
	vtc/cachetag_pm00002.vtc \
	vtc/cachetag_pm00003.vtc \
	vtc/cachetag_pm00004.vtc \
	vtc/cachetag_pm00005.vtc \
	vtc/cachetag_pm00006.vtc \
	vtc/cachetag_pm00008.vtc \
	vtc/cachetag_pm00010.vtc \
	vtc/cachetag_pm00011.vtc \
	vtc/cachetag_pm00012.vtc \
	vtc/cachetag_pm00013.vtc \
	vtc/cachetag_pm00017.vtc \
	vtc/cachetag_pm00018.vtc

# Keep the interning coverage a mechanical transform of the direct/default
# cases. A new core case therefore cannot silently gain only one mode.
VTC_TESTS_INTERNED_CORE = $(VTC_TESTS_CORE:.vtc=_interned.vtc)

# These test-hook cases exercise table and allocation mechanics that are shared
# by both representations. Their generated interned copies intentionally omit
# representation-specific allocation hooks, which have dedicated VTCs below.
VTC_TESTS_SHARED_ALLOCATION_TEST_HOOKS = \
	vtc/cachetag_pm00007.vtc \
	vtc/cachetag_pm00016.vtc \
	vtc/cachetag_pm00019.vtc \
	vtc/cachetag_pm00022.vtc \
	vtc/cachetag_pm00023.vtc \
	vtc/cachetag_pm00025.vtc \
	vtc/cachetag_pm00028.vtc

VTC_TESTS_INTERNED_SHARED_ALLOCATION_TEST_HOOKS = \
	$(VTC_TESTS_SHARED_ALLOCATION_TEST_HOOKS:.vtc=_interned.vtc)
VTC_TESTS_GENERATED_INTERNED = \
	$(VTC_TESTS_INTERNED_CORE) \
	$(VTC_TESTS_INTERNED_SHARED_ALLOCATION_TEST_HOOKS)

VTC_TESTS_DIRECT_ACCOUNTING = \
	vtc/cachetag_pm00027.vtc

VTC_TESTS_INTERNED_ACCOUNTING = \
	vtc/cachetag_pm00027_interned.vtc

vtc/.interned-generated: $(VTC_TESTS_CORE) \
	$(VTC_TESTS_SHARED_ALLOCATION_TEST_HOOKS) \
	$(srcdir)/vtc/generate-interning-variant.sh \
	vtc/.dirstamp
	$(AM_V_GEN) for source in $(VTC_TESTS_CORE) $(VTC_TESTS_SHARED_ALLOCATION_TEST_HOOKS); do \
		destination=$${source%.vtc}_interned.vtc; \
		$(SHELL) $(srcdir)/vtc/generate-interning-variant.sh \
		    $(srcdir)/$$source $$destination || exit 1; \
	done; \
	: > $@

$(VTC_TESTS_GENERATED_INTERNED): vtc/.interned-generated

VTC_TESTS_RUNTIME_MODES = \
	vtc/cachetag_pm00039.vtc \
	vtc/cachetag_pm00041.vtc

VTC_TESTS_DEMO_DIAGNOSTICS = \
	vtc/cachetag_c00025.vtc \
	vtc/cachetag_pm00029.vtc

VTC_TESTS_TEST_HOOKS = \
	vtc/cachetag_pm00007.vtc \
	vtc/cachetag_pm00009.vtc \
	vtc/cachetag_pm00014.vtc \
	vtc/cachetag_pm00015.vtc \
	vtc/cachetag_pm00016.vtc \
	vtc/cachetag_pm00019.vtc \
	vtc/cachetag_pm00020.vtc \
	vtc/cachetag_pm00021.vtc \
	vtc/cachetag_pm00022.vtc \
	vtc/cachetag_pm00023.vtc \
	vtc/cachetag_pm00024.vtc \
	vtc/cachetag_pm00025.vtc \
	vtc/cachetag_pm00026.vtc \
	vtc/cachetag_pm00028.vtc \
	vtc/cachetag_pm00035.vtc \
	vtc/cachetag_pm00036.vtc \
	vtc/cachetag_pm00037.vtc \
	vtc/cachetag_pm00038.vtc \
	vtc/cachetag_pm00042.vtc

VTC_TESTS_MEMBERSHIP_TEST_HOOKS = \
	vtc/cachetag_pm00030.vtc \
	vtc/cachetag_pm00031.vtc \
	vtc/cachetag_pm00032.vtc \
	vtc/cachetag_pm00033.vtc \
	vtc/cachetag_pm00034.vtc \
	vtc/cachetag_pm00040.vtc

VTC_TESTS = \
	$(VTC_TESTS_CORE) \
	$(VTC_TESTS_INTERNED_CORE) \
	$(VTC_TESTS_DIRECT_ACCOUNTING) \
	$(VTC_TESTS_INTERNED_ACCOUNTING) \
	$(VTC_TESTS_RUNTIME_MODES)
if CACHE_TAG_DEMO_DIAGNOSTICS
VTC_TESTS += $(VTC_TESTS_DEMO_DIAGNOSTICS)
endif
if CACHE_TAG_TEST_HOOKS
VTC_TESTS += $(VTC_TESTS_TEST_HOOKS)
VTC_TESTS += $(VTC_TESTS_INTERNED_SHARED_ALLOCATION_TEST_HOOKS)
VTC_TESTS += $(VTC_TESTS_MEMBERSHIP_TEST_HOOKS)
endif

FELLOW_VTC_TESTS = \
	vtc/cachetag_p00000.vtc \
	vtc/cachetag_p00001.vtc \
	vtc/cachetag_p00002.vtc \
	vtc/cachetag_p00003.vtc \
	vtc/cachetag_p00004.vtc \
	vtc/cachetag_p00005.vtc \
	vtc/cachetag_p00006.vtc \
	vtc/cachetag_p00007.vtc \
	vtc/cachetag_p00008.vtc \
	vtc/cachetag_p00009.vtc \
	vtc/cachetag_p00010.vtc \
	vtc/cachetag_p00011.vtc \
	vtc/cachetag_p00012.vtc \
	vtc/cachetag_p00013.vtc \
	vtc/cachetag_p00014.vtc \
	vtc/cachetag_p00015.vtc \
	vtc/cachetag_p00016.vtc \
	vtc/cachetag_p00017.vtc \
	vtc/cachetag_p00018.vtc \
	vtc/cachetag_p00019.vtc \
	vtc/cachetag_p00020.vtc \
	vtc/cachetag_p00021.vtc \
	vtc/cachetag_p00022.vtc \
	vtc/cachetag_p00023.vtc \
	vtc/cachetag_p00090.vtc \
	vtc/cachetag_x00000.vtc

TESTS = \
	cachetag_wal_test \
	cachetag_counter_parity.sh \
	$(VTC_TESTS)

# Documentation

# The combined VCC file is generated per configuration, so it is installed
# from the build tree and never distributed; the fragments it is built from
# are distributed instead.
nodist_doc_DATA = \
	vmod_cachetag.vcc

dist_man_MANS = \
	vmod_cachetag.3

EXTRA_DIST = \
	cachetag.vsc \
	vmod_cachetag_core.vcc \
	vmod_cachetag_demo_diagnostics.vcc \
	vmod_cachetag_test_hooks.vcc \
	vmod_cachetag_set_interning_test_hooks.vcc \
	vtc/generate-interning-variant.sh \
	$(VTC_TESTS_CORE) \
	$(VTC_TESTS_DIRECT_ACCOUNTING) \
	$(VTC_TESTS_INTERNED_ACCOUNTING) \
	$(VTC_TESTS_RUNTIME_MODES) \
	$(VTC_TESTS_DEMO_DIAGNOSTICS) \
	$(VTC_TESTS_TEST_HOOKS) \
	$(VTC_TESTS_SHARED_ALLOCATION_TEST_HOOKS) \
	$(VTC_TESTS_MEMBERSHIP_TEST_HOOKS) \
	$(FELLOW_VTC_TESTS)
