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

vmod_cachetag.3: VSC_cachetag.rst
@BUILD_VSC_CACHETAG@
@BUILD_VMOD_CACHETAG@

all-local: vtc/.dirstamp

vtc/.dirstamp:
	$(MKDIR_P) vtc
	: > $@

CLEANFILES = \
	vtc/.dirstamp \
	vmod_vcs_version.txt

# Test suite

check_PROGRAMS = \
	cachetag_wal_test

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
	LD_LIBRARY_PATH="$(VINYL_LIBRARY_PATH)"
TEST_EXTENSIONS = .vtc
VTC_LOG_COMPILER = vinyltest -v
AM_VTC_LOG_FLAGS = \
	-p vcl_path="$(abs_top_srcdir)/vcl:$(VINYLAPI_VCLDIR)" \
	-p vmod_path="$(abs_builddir)/.libs:$(vmoddir):$(VINYLAPI_VMODDIR)"

VTC_TESTS = \
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
	vtc/cachetag_c00025.vtc \
	vtc/cachetag_r00001.vtc \
	vtc/cachetag_r00002.vtc \
	vtc/cachetag_r00003.vtc \
	vtc/cachetag_r00004.vtc \
	vtc/cachetag_r00005.vtc \
	vtc/cachetag_r00006.vtc \
	vtc/cachetag_r00008.vtc \
	vtc/cachetag_pm00000.vtc \
	vtc/cachetag_pm00001.vtc \
	vtc/cachetag_pm00002.vtc \
	vtc/cachetag_pm00003.vtc \
	vtc/cachetag_pm00004.vtc \
	vtc/cachetag_pm00005.vtc \
	vtc/cachetag_pm00006.vtc \
	vtc/cachetag_pm00007.vtc \
	vtc/cachetag_pm00008.vtc \
	vtc/cachetag_pm00009.vtc \
	vtc/cachetag_pm00010.vtc \
	vtc/cachetag_pm00011.vtc \
	vtc/cachetag_pm00012.vtc \
	vtc/cachetag_pm00013.vtc \
	vtc/cachetag_pm00014.vtc \
	vtc/cachetag_pm00015.vtc \
	vtc/cachetag_pm00016.vtc \
	vtc/cachetag_pm00017.vtc \
	vtc/cachetag_pm00018.vtc \
	vtc/cachetag_pm00019.vtc \
	vtc/cachetag_pm00020.vtc \
	vtc/cachetag_pm00021.vtc \
	vtc/cachetag_pm00022.vtc \
	vtc/cachetag_pm00023.vtc \
	vtc/cachetag_pm00024.vtc \
	vtc/cachetag_pm00025.vtc \
	vtc/cachetag_pm00026.vtc \
	vtc/cachetag_pm00027.vtc \
	vtc/cachetag_pm00028.vtc

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
	vtc/cachetag_p00090.vtc \
	vtc/cachetag_x00000.vtc

TESTS = \
	cachetag_wal_test \
	$(VTC_TESTS)

# Documentation

dist_doc_DATA = \
	vmod_cachetag.vcc

dist_man_MANS = \
	vmod_cachetag.3

EXTRA_DIST = \
	cachetag.vsc \
	$(VTC_TESTS) \
	$(FELLOW_VTC_TESTS)
