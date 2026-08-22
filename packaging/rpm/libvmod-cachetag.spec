# libvmod-cachetag -- RPM spec for the EL9 lane.
#
# STATUS: first built 2026-07-24, on aarch64, against the EL9 Vinyl Cache 9
# snapshot packages produced by vcache-packaging/recipes/el9. Authoritative
# x86_64 artifacts, and a Mock clean-room build, come later from CI. See
# ../README.md.
#
# This file is a template. At-sign delimited tokens are substituted from the
# release cohort/target manifests before rpmbuild runs. Every token used here
# is documented in ../README.md.
#
# Intended build venue: Mock with an AlmaLinux or Rocky Linux 9 chroot, i.e.
#     mock -r alma+epel-9-x86_64 --buildsrpm --spec libvmod-cachetag.spec \
#          --sources .
#     mock -r alma+epel-9-x86_64 --rebuild libvmod-cachetag-*.src.rpm
# Mock supplies the minimal buildroot that exposes undeclared BuildRequires;
# a host rpmbuild does not and must not be treated as a clean build. Mock needs
# privileges Docker on macOS cannot sensibly grant, so the local process proof
# uses rpmbuild inside a fresh almalinux:9 container instead, and Mock stays a
# CI requirement. EL10 gets its own build, not a rebuild of this one.
#
# Deliberately NOT done here:
#   - no AutoReq/AutoProv disabling: automatically generated ELF requires stay
#     on so the real libc/libvinylapi dependencies are discovered;
#   - no "%%global debug_package %%{nil}": native debuginfo/debugsource
#     generation is left to the EL9 macros;
#   - no daemon, user, service unit, default VCL, tmpfiles entry or restart
#     scriptlet: this package installs a plugin and nothing else;
#   - no hand-copied dependency list shared with the Debian recipe.

# Vinyl Cache reports vmoddir as ${libdir}/vinyl-cache/vmods through
# vinylapi.pc. %%build asserts that the installed development package agrees
# with this expansion and with the target manifest.
%global vinyl_vmoddir %{_libdir}/vinyl-cache/vmods

# The VMOD is a dlopen()ed plugin, not a system library. Suppress the
# automatic soname Provides for it while leaving Requires generation intact.
%global __provides_exclude_from ^%{vinyl_vmoddir}/.*\\.so$

Name:           libvmod-cachetag
Version:        @CACHETAG_VERSION@
Release:        @PACKAGE_REVISION@%{?dist}
Summary:        Tag-based cache invalidation VMOD for Vinyl Cache

License:        MPL-2.0 AND BSD-2-Clause
URL:            https://github.com/boffinate/libvmod-cachetag
Source0:        @SOURCE_URL@

BuildRequires:  gcc
BuildRequires:  make
BuildRequires:  pkgconfig
BuildRequires:  python3
# rst2man; on EL9 this may come from CRB or EPEL. Confirm in Mock and record
# the resolved provider in the target manifest.
BuildRequires:  python3-docutils
# Exact development package from the cohort. A different Vinyl Cache revision
# must not satisfy this build.
BuildRequires:  vinyl-cache-devel = @VINYL_PACKAGE_VERSION@

# cachetag declares "$ABI strict" and uses private Vinyl Cache cache APIs, so
# the binary is valid only against the exact runtime it was compiled against.
# The Vinyl Cache runtime package must Provide these arch-qualified tokens.
Requires:       vinyld(abi)%{?_isa} = @VINYL_STRICT_ABI@
Requires:       vinyld(vrt)%{?_isa} = @VINYL_VRT@
# Provenance, which the ABI token cannot supply. vinyld(abi) is a hash of the
# upstream Vinyl Cache source revision, so a downstream rebuild, a vendor respin
# or a differently patched build from the same revision advertises exactly the
# same token; the EL9 step-9 transaction matrix upgraded such a package with no
# resolver objection at all. The cohort id names the coordinated set of packages
# this VMOD was built and tested inside, and it lives in the provide NAME
# because it contains hyphens and could not be an RPM EVR. Unversioned: the
# cohort id is the whole identity, there is nothing left to compare.
Requires:       vinyld(cohort-@COHORT_ID@)%{?_isa}

# Distro-native variant only: when a distribution's own Vinyl Cache package
# exposes no ABI provide, replace the two Requires above with an exact
# package-version dependency and record the revision in the manifest. The macro
# is escaped because rpm expands macros inside comments too, and an unescaped
# %%{?_isa} here makes rpmlint report a specfile-error on every build.
#Requires:      vinyl-cache%%{?_isa} = @VINYL_PACKAGE_VERSION@

%description
cachetag is a Vinyl Cache VMOD that tags cached objects and invalidates them
by tag. It maintains a purge map so a single tag purge can invalidate every
object carrying the tag, without a full cache ban or a linear ban-list
evaluation.

This package installs only the VMOD shared object, its manual page, its VCC
interface description and documentation. It installs no daemon, service unit,
system user or VCL, and does not restart Vinyl Cache.

Cohort: @COHORT_ID@.
Built against Vinyl Cache
@VINYL_PACKAGE_VERSION@,
VRT @VINYL_VRT@,
strict ABI @VINYL_STRICT_ABI@.

%prep
%autosetup -n %{name}-%{version}

%build
# Refuse to build unless the installed development package puts VMODs where
# the manifest says, so the runtime can actually find the result.
vmoddir=$(pkg-config --define-variable=libdir=%{_libdir} \
	--variable=vmoddir vinylapi)
if [ "$vmoddir" != "%{vinyl_vmoddir}" ] || \
   [ "$vmoddir" != "@VINYL_VMODDIR@" ]; then
	echo "E: vinylapi vmoddir '$vmoddir' does not match %{vinyl_vmoddir}" >&2
	echo "E: and the manifest value '@VINYL_VMODDIR@'" >&2
	exit 1
fi

# Refuse to build unless the installed development package carries the strict
# ABI the manifest promised; otherwise the Requires above would be a lie.
# vmod_abi.h holds:
#   #define VMOD_ABI_Version "Vinyl Cache <version> <hash>"
incdir=$(pkg-config --variable=pkgincludedir vinylapi)
abi=$(sed -n 's/^#define[[:space:]]\+VMOD_ABI_Version[[:space:]]\+"\(.*\)"[[:space:]]*$/\1/p' \
	"$incdir/vmod_abi.h" | awk 'NR == 1 { print $NF }')
if [ "$abi" != "@VINYL_STRICT_ABI@" ]; then
	echo "E: installed Vinyl Cache strict ABI '$abi' does not match the" >&2
	echo "E: manifest value '@VINYL_STRICT_ABI@'" >&2
	exit 1
fi

# %%configure supplies the distribution's hardened compiler and linker flags.
# Do not override them and do not import the upstream diagnostic profile,
# which disables the stack protector.
%configure \
	--disable-static \
	--docdir=%{_pkgdocdir}
%make_build

%install
%make_install
# libtool archives and static archives are not shipped.
find %{buildroot} -name '*.la' -delete
find %{buildroot} -name '*.a' -delete
test -f %{buildroot}%{vinyl_vmoddir}/libvmod_cachetag.so

%check
# The VTC suite needs a running vinyltest against a matching runtime; that is
# the installed-package smoke test's job, after this package is installed.
# Build-time checking is limited to the self-contained WAL unit test.
%make_build check TESTS=cachetag_wal_test

%files
# LICENSE reaches the release tarball through Makefile.am's EXTRA_DIST. That was
# once a blocker recorded here as a prerequisite; it is fixed, and the first real
# EL9 build (2026-07-24) confirmed the file is present in
# libvmod-cachetag-1.0.0.tar.gz.
%license LICENSE
%doc README.md
%doc docs/vmod_cachetag.md
%{vinyl_vmoddir}/libvmod_cachetag.so
%{_mandir}/man3/vmod_cachetag.3*
%{_pkgdocdir}/vmod_cachetag.vcc

%changelog
* @RPM_CHANGELOG_DATE@ @MAINTAINER_NAME@ <@MAINTAINER_EMAIL@> - @CACHETAG_VERSION@-@PACKAGE_REVISION@
- Generated from the release manifests; do not edit by hand.
- Cohort @COHORT_ID@, built against Vinyl Cache @VINYL_PACKAGE_VERSION@.
