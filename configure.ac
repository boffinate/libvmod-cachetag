AC_PREREQ([2.69])
AC_INIT([libvmod-cachetag], [1.0.0])
AC_CONFIG_MACRO_DIR([m4])
AC_CONFIG_AUX_DIR([build-aux])
AC_CONFIG_HEADERS([config.h])

AM_INIT_AUTOMAKE([1.12 -Wall -Werror foreign parallel-tests])
AM_SILENT_RULES([yes])

AC_ARG_VAR([RST2MAN], [the program to build manuals from reStructuredText])
AC_CHECK_PROGS([RST2MAN], [rst2man rst2man.py])

dnl cachetag is a strict-ABI VMOD: src/vmod_cachetag.vcc declares "$ABI strict"
dnl and the sources use private Vinyl Cache headers and structures. It supports
dnl exactly one Vinyl release line, currently Vinyl Cache 9. There is no
dnl maximum-version bound: VINYL_PREREQ's optional second argument uses the
dnl same strverscmp ordering discussed below, under which a development build
dnl reporting "trunk" sorts above every numeric version and would therefore be
dnl rejected by any numeric upper bound. When Vinyl 9.0 is pinned and the
dnl development checkout stops reporting "trunk", the intended value is
dnl VINYL_PREREQ([9.0.0], [10.0.0]).
VINYL_PREREQ([9.0.0])

dnl VINYL_PREREQ compares versions with AS_VERSION_COMPARE, i.e. strverscmp
dnl ordering. Vinyl's own configure.ac still uses AC_INIT([Vinyl Cache],
dnl [trunk]), so vinylapi.pc reports the literal version "trunk", which
dnl strverscmp sorts above every numeric version because 't' > any digit. The
dnl prerequisite above therefore passes vacuously against a development build.
dnl Say so out loud instead of letting a weakened check look like a real one:
dnl the binding compatibility gate for such a build is the exact strict VMOD
dnl ABI, checked by --with-release-manifest below.
case $VINYL_VERSION in
[[0-9]*]) cachetag_vinyl_version_numeric=yes ;;
*) cachetag_vinyl_version_numeric=no ;;
esac
AS_IF([test "x$cachetag_vinyl_version_numeric" = xno],
	[AC_MSG_WARN([Vinyl Cache reports the non-numeric version '$VINYL_VERSION', so the 9.0.0 prerequisite was not actually enforced. Only the strict VMOD ABI identifies such a build; use --with-release-manifest=FILE for a release build.])])

VINYL_VMODS([cachetag])
VINYL_COUNTERS([cachetag])

dnl Optional packaging gate. Absent, configure behaves exactly as before.
CACHETAG_RELEASE_MANIFEST

AM_PROG_AR

LT_PREREQ([2.2.6])
LT_INIT([dlopen disable-static])

AC_ARG_ENABLE([attach-timing],
	AS_HELP_STRING([--enable-attach-timing],
		[enable attach-path timing VSC counters (default: disabled)]),
	[],
	[enable_attach_timing=no])
AS_IF([test "x$enable_attach_timing" = "xyes"],
	[AC_DEFINE([CACHE_TAG_ATTACH_TIMING], [1],
		[Define to 1 to collect attach-path timing counters.])],
	[AC_DEFINE([CACHE_TAG_ATTACH_TIMING], [0],
		[Define to 0 to leave attach-path timing counters disabled.])])

AC_CONFIG_FILES([
	Makefile
	src/Makefile
])

AC_OUTPUT

AS_ECHO("
	==== $PACKAGE_STRING ====

	vinyl:        $VINYL_VERSION
	prefix:       $prefix
	vmoddir:      $vmoddir
	vcldir:       $vcldir
	pkgvcldir:    $pkgvcldir
	attach timing: $enable_attach_timing

	compiler:     $CC
	cflags:       $CFLAGS
	ldflags:      $LDFLAGS
")
