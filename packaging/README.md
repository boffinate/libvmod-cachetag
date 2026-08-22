# Native packaging recipes for libvmod-cachetag

## Status: built, tested, and published once as an experimental pre-release

Both recipes are built in clean rooms by the sibling `vcache-packaging` repository's CI — Debian 13 amd64 under pbuilder, EL9 x86_64 under Mock — installed into fresh containers, and exercised there. The v1.0.0 pre-release of 2026-07-26 was cut from the trunk cohort `vinyl-9.0.0-4b7e68292979`; the current release-track cohort is `vinyl-9.0.1-ac4f719c16f4` (Vinyl 9.0.1, cachetag 1.0.1, minted 2026-07-28). They are Phase 3 of the binary packaging and distribution plan (`devdocs/docs/20260724_1526_plan_binary-packaging-and-distribution.md`).

The blocker that held this directory at "never been built" until 2026-07-25 was never in this repository: cachetag declares `$ABI strict` and must be compiled against an installed `vinyl-cache-dev` / `vinyl-cache-devel` package, and Vinyl Cache 9 has no distribution packages anywhere. Step 7 of the plan — minimal Vinyl 9 Debian and RPM packages with strict-ABI virtual provides — supplied them, and both lanes now build cachetag against the installed development package of a Vinyl built in the same run. Do not "fix" a build failure by pointing these recipes at an unpackaged Vinyl prefix; that defeats the entire point of the exercise.

Scope is the cachetag VMOD only. Vinyl Cache itself is packaged elsewhere.

Lanes covered here are the two first-milestone targets: **Debian 13 (trixie) amd64** and **EL9 x86_64**. Ubuntu, EL10, Arch, FreeBSD and Alpine are deliberately absent; they arrive after this model is proven.

## Layout

```text
packaging/
  README.md                          this file
  check-tokens.sh                    substitution-token validator (runnable today)
  debian/                            debhelper compat 13 source-package skeleton
    changelog                        template; generated from the release manifests
    control
    copyright                        machine-readable (DEP-5)
    libvmod-cachetag.docs
    rules
    source/format                    3.0 (quilt)
  rpm/
    libvmod-cachetag.spec
```

`packaging/debian/` is a template tree, not a live `debian/` directory. The release tooling copies it next to the unpacked upstream tarball as `debian/` after substituting tokens. Keeping it under `packaging/` means each release records the exact recipes used for its artifacts, and it keeps `dpkg-buildpackage` from ever running against a git checkout by accident.

## The two support tracks

The plan defines two separate tracks, and every artifact must say which one it belongs to.

**Our coordinated package cohort** is the primary and simplest path: a Vinyl Cache runtime, its matching development package and every strict-ABI VMOD, all built and tested from one recorded Vinyl source, patch set, build profile and ABI. This is what the recipes here are written for. The dependency on `vinyld-abi-@VINYL_STRICT_ABI@` (Debian) / `vinyld(abi)%{?_isa}` (RPM) binds a cachetag binary to the exact Vinyl build it was compiled against, and the companion dependency on `vinyld-cohort-@COHORT_ID@` (Debian) / `vinyld(cohort-@COHORT_ID@)%{?_isa}` (RPM) binds it to the cohort that build belongs to.

The second dependency is not decoration. The ABI token is derived from the upstream Vinyl source revision, so any package built from that revision — a distribution security backport, a vendor respin, a rebuild with a different patch series or build profile — advertises the identical token. The step-9 transaction matrices in `vcache-packaging/docs/` measured exactly that: on both apt and dnf a same-ABI candidate from a different build upgraded cleanly, with no resolver objection, because the resolver was never asked the question. The cohort id is a digest over the pinned source archive, the ordered patch series and the production build-profile revision, so asking it is now possible. The cost, accepted by the maintainer on 2026-07-25, is that a distribution's own Vinyl package can never satisfy a cohort VMOD; that is what the separate distro-native track below is for.

**Distro-native integration** is a second, additional claim: cachetag compiled and tested against a distribution's own exact Vinyl package revision. Such an artifact belongs to that distro package revision and is not interchangeable with a cohort artifact, even when the upstream version numbers match. Both recipes carry a commented-out variant for the case where a distribution's Vinyl package exposes no ABI virtual provide, in which case the VMOD depends on the exact package version and revision instead:

```text
Depends: vinyl-cache (= 9.0.0-3)
```

That deliberately blocks a Vinyl upgrade until the VMOD has been rebuilt.

The durable support statement, which must accompany any published package:

> Official VMOD binaries are supported with the Vinyl packages from the same repository and release cohort. Distribution-provided Vinyl packages are supported only where a VMOD package has been built and tested specifically against that distribution package revision.

## Substitution model

Every recipe here is a template. Tokens are written between at-signs, in upper case, and are replaced from the cohort registry manifests (`registry/cohorts/<cohort-id>.yml` and `registry/targets/<cohort-id>/{debian-13-amd64,el9-x86_64}.yml`) before the native build tool runs. Hand-editing a version, a revision or an ABI hash into these files is a bug: the plan requires package metadata to be generated or validated from the manifests so that an ABI-only cohort rebuild is a routine package-revision bump instead of a hand-edited drift risk.

The registry and its manifest-to-metadata generation live in the sibling [`vcache-packaging`](../../vcache-packaging) repository, not in this one: a cohort is a set of packages built and tested together, so no single VMOD in it can own its identity. Generate this directory's token values with:

```sh
cd ../vcache-packaging
python3 tools/release_tool.py metadata --cohort <cohort-id> --target debian-13-amd64 --format shell
```

That tooling cross-checks each manifest's `cachetag.version` against `AC_INIT` in this repository's `configure.ac`, finding this checkout via `--cachetag-src`, `$CACHETAG_SRC`, or the sibling default. This directory defines the token vocabulary and nothing more; it does not depend on that tooling at build time.

Since 2026-07-26 the packaging repository maintains two Vinyl pin tracks: **release** (upstream release tarball, currently 9.0.1 — what published packages build from) and **trunk** (pinned snapshot plus a scheduled trunk-HEAD harness run — the early-warning lane for Vinyl core changes and `$ABI strict` churn). The examples in the table below come from the release track; a trunk-track build substitutes snapshot-style versions such as `9.0.0~git20260520.25761f8505-1.el9`. See `../../vcache-packaging/docs/20260726_1235_note_two-track-release-and-trunk.md`.

### Token vocabulary

| Token | Source | Example | Used in |
| --- | --- | --- | --- |
| `@COHORT_ID@` | cohort manifest `cohort`; must be usable inside a package name, `^[a-z0-9][a-z0-9+.-]+$`, because both lanes bake it into a virtual package/provide name | `vinyl-9.0.1-ac4f719c16f4` | Debian `Depends` (`vinyld-cohort-@COHORT_ID@`), RPM `Requires` (`vinyld(cohort-@COHORT_ID@)%{?_isa}`), changelog, spec description and `%changelog` |
| `@CACHETAG_VERSION@` | cohort manifest `cachetag.version`, must equal `configure.ac` and the release tag | `1.0.1` | changelog, spec `Version` |
| `@PACKAGE_REVISION@` | target manifest; packaging-only revision, incremented on any rebuild against different Vinyl inputs | `1` | changelog, spec `Release` |
| `@VINYL_PACKAGE_VERSION@` | target manifest; exact version-and-revision of the Vinyl packages in this cohort | Debian `9.0.1-1`; RPM `9.0.1-1.el9`, which is the full `version-release` because that is what an RPM `=` dependency compares against | `Build-Depends`, `BuildRequires` |
| `@VINYL_STRICT_ABI@` | cohort manifest `vinyl.strict_abi`; the trailing hash of `VMOD_ABI_Version` | `423648c4cb6b225b3268ffc337354ea938f5efee` | `Depends`, `Requires`, both build-time assertions |
| `@VINYL_VRT@` | cohort manifest `vinyl.vrt` | `23.0` | `Depends`, `Requires` |
| `@VINYL_VMODDIR@` | target manifest; absolute installed VMOD directory for that distro and architecture | `/usr/lib/x86_64-linux-gnu/vinyl-cache/vmods` | both build-time assertions |
| `@SOURCE_URL@` | release manifest; URL of the tagged source archive | `https://.../libvmod-cachetag-1.0.1.tar.gz` | spec `Source0` |
| `@MAINTAINER_NAME@`, `@MAINTAINER_EMAIL@` | release owner, decided 2026-07-25 | `Boffinate`, `noreply@boffinate.com` — the address does not accept mail; support goes through the issue tracker reachable via `Homepage`/`Vcs-Browser` | `control`, `changelog`, `copyright`, `%changelog` |
| `@DEBIAN_VERSION@` | target manifest; full Debian version, upstream plus revision plus release suffix when the target pins a `dist_tag` (the release-track Debian target pins an empty one) | `1.0.1-1` | `debian/changelog` |
| `@DEBIAN_DISTRIBUTION@` | target manifest; changelog suite | `trixie` | `debian/changelog` |
| `@DEBIAN_DATE@` | release timestamp, RFC 2822, derived from `SOURCE_DATE_EPOCH` | `Fri, 24 Jul 2026 20:00:00 +0000` | `debian/changelog` |
| `@RPM_CHANGELOG_DATE@` | same instant in RPM `%changelog` format | `Fri Jul 24 2026` | spec `%changelog` |

### Mapping onto the manifest schemas

The manifest layout landed separately, as `cachetag-cohort/v1`, `cachetag-target/v1` and `cachetag-distro-native/v1`. It moved out of this repository's `release/` directory on 2026-07-24 and now lives in the sibling [`vcache-packaging`](../../vcache-packaging) repository under `registry/`, because a cohort is a set of packages and no single VMOD in it can own its identity. The tokens map onto it as follows, and this section is the place to fix any drift between the two:

| Token | Manifest field |
| --- | --- |
| `@COHORT_ID@` | cohort `cohort` (distro-native lane has no cohort identity and substitutes its target id and distro Vinyl package version instead; it must also **drop** the `vinyld-cohort-` dependency rather than substitute a target id into it, because no distribution package will ever provide one — the exact-package-version variant is that lane's equivalent guard) |
| `@CACHETAG_VERSION@` | cohort/target `cachetag.version` |
| `@PACKAGE_REVISION@` | target `package.revision` |
| `@VINYL_PACKAGE_VERSION@` | target `vinyl_packages.dev_version` for the build dependency; `distro_vinyl.binary_package_version` on the distro-native lane |
| `@VINYL_STRICT_ABI@` | cohort `vinyl.strict_abi`; `distro_vinyl.strict_abi` on the distro-native lane |
| `@VINYL_VRT@` | cohort `vinyl.vrt` |
| `@VINYL_VMODDIR@` | target `install.vmoddir`, with `install.vmoddir_source` recording whether it came from `pkg-config` or was written by hand |
| `@SOURCE_URL@` | release manifest; derived from the tag and archive name |
| `@DEBIAN_VERSION@` | composed from `cachetag.version`, `package.revision` and `target.dist_tag` |
| `@DEBIAN_DISTRIBUTION@` | derived from `target.distro_id` |

Two notes for whoever wires up the generator:

- **`@VINYL_VMODDIR@` now has a manifest field.** `cachetag-target/v1` records it as `install.vmoddir`, per target, because the value differs between `/usr/lib/x86_64-linux-gnu/vinyl-cache/vmods` and `/usr/lib64/vinyl-cache/vmods`; `install.vmoddir_source` records how it was obtained, and a releasable target requires `pkg-config`. That keeps the expected path an auditable manifest claim rather than whatever the buildroot happened to contain. The Debian 13 build confirmed the resolved value is architecture-dependent in exactly this way: on arm64 it is `/usr/lib/aarch64-linux-gnu/vinyl-cache/vmods`.
- **`target.dist_tag` and RPM `%{?dist}`.** The spec uses the native `%{?dist}` macro in `Release:`, so the generator must not also splice `dist_tag` into `@PACKAGE_REVISION@`, or the release string ends up as `1.el9.el9`.

`check-tokens.sh` is the guard for this vocabulary:

```sh
packaging/check-tokens.sh --templates          # every token in the tree is declared
packaging/check-tokens.sh --substituted DIR    # no token survived into a build tree
packaging/check-tokens.sh --list               # print declared token names
```

Run `--substituted` on the generated tree immediately before `dpkg-buildpackage` or `rpmbuild`. An unsubstituted template must never reach a build.

Note that `@VINYL_STRICT_ABI@` and `@VINYL_VMODDIR@` appear twice on purpose: once in package metadata, and once in a build-time assertion that compares the manifest's claim against the installed development package. If the two disagree the build fails rather than producing a package whose ABI dependency is a lie.

## What the packages install

The file list was derived from the build system, not guessed:

| Installed path | Comes from |
| --- | --- |
| `<vmoddir>/libvmod_cachetag.so` | `src/Makefile.am`: `vmod_LTLIBRARIES = libvmod_cachetag.la`, linked with `VMOD_LDFLAGS` (`-module -export-dynamic -avoid-version -shared`), so the installed object is unversioned |
| `/usr/share/man/man3/vmod_cachetag.3` | `src/Makefile.am`: `dist_man_MANS = vmod_cachetag.3` |
| `/usr/share/doc/libvmod-cachetag/vmod_cachetag.vcc` | `src/Makefile.am`: `nodist_doc_DATA = vmod_cachetag.vcc`, into autoconf's default `docdir`. The VCC file is concatenated at build time from the distributed fragments (`vmod_cachetag_core.vcc` plus any fragment enabled by `--enable-demo-diagnostics` / `--enable-test-hooks`), so the installed copy documents exactly the built surface |

`<vmoddir>` is not hard-coded. `vinylapi.pc` defines `vmoddir=${libdir}/vinyl-cache/vmods`, and Vinyl's `vinyl.m4` resolves it with `pkg-config --define-variable=libdir=$libdir`. Both recipes therefore take `libdir` from the installed development package rather than from the packaging default, so the VMOD lands in the directory the packaged runtime actually searches, and then assert the result equals `@VINYL_VMODDIR@`.

Two further files are packaged by the recipes rather than by `make install`: `README.md` and `docs/vmod_cachetag.md` (both in `EXTRA_DIST`, so both present in the release tarball), plus `LICENSE` as the RPM `%license` file — see the gaps section.

Removed during staging: `libvmod_cachetag.la`. Static archives cannot appear, because `configure.ac` uses `LT_INIT([dlopen disable-static])`; the `*.a` sweep is belt and braces. `cachetag_wal_test` is a `check_PROGRAMS` binary and is never installed.

Not installed by anything, deliberately: no daemon, no system user, no service unit, no tmpfiles entry, no default or example VCL, no restart or reload scriptlet. Installing or upgrading this package must not touch a running Vinyl Cache.

## What the Vinyl Cache packages must provide

These recipes are one half of a contract. The Vinyl packaging work (plan step 7) must supply the other half, otherwise the dependencies here are unsatisfiable:

- Debian runtime package: `Provides: vinyld-abi-<hash>, vinyld-vrt (= <major.minor>), vinyld-cohort-<cohort-id>`, where `<hash>` is the trailing field of `VMOD_ABI_Version` in `include/vmod_abi.h`;
- RPM runtime package: `Provides: vinyld(abi)%{?_isa} = <hash>`, `Provides: vinyld(vrt)%{?_isa} = <major.minor>` and the unversioned `Provides: vinyld(cohort-<cohort-id>)%{?_isa}`;
- the cohort provide is unversioned and carries its value in the provide name on both lanes. That asymmetry with the ABI provide is forced, not chosen: a cohort id contains hyphens, and RPM will not accept one in an EVR. Keeping the same shape on Debian keeps the two lanes readable side by side;
- development package (`vinyl-cache-dev` / `vinyl-cache-devel`): private headers including `vmod_abi.h`, `vinylapi.pc`, `vinyl.m4`, and the VMOD/VSC generation tools, depending on the exact matching runtime package.

If the Vinyl packaging chooses different provide names, the two recipes here change with it; the names are not independently meaningful.

## Build venues

Neither lane may be built on a developer laptop and published.

- Debian 13: `dpkg-buildpackage` inside `sbuild` or `pbuilder`, then `lintian`. `dh_shlibdeps` derives the real shared-library dependencies; nothing is hand-listed.
- EL9: Mock with an AlmaLinux or Rocky Linux 9 chroot, `rpmbuild` producing both source and binary RPMs, then `rpmlint`. Mock's minimal buildroot is what exposes an undeclared `BuildRequires`; a host `rpmbuild` does not. Debuginfo is left to native generation. EL10 gets its own build rather than a rebuild of the EL9 artifact. The first real build of this spec (2026-07-24, aarch64) used `rpmbuild` inside a fresh `almalinux:9` container rather than Mock, because Mock needs privileges Docker on macOS cannot sensibly grant; Mock remains a CI requirement and that build is a process proof, not a release artifact. See `vcache-packaging/recipes/el9/`.

## Phase 3 acceptance criteria: what "done" looks like

For each format, from the plan. None of these can be claimed yet.

1. **Package lint succeeds** — `lintian` clean for the `.deb` and the source package, `rpmlint` clean for the SRPM and RPM, with any remaining tag either fixed or justified by a checked-in override that explains itself.
2. **The package contains only intended files** — `dpkg -c` and `rpm -qlp` list exactly the paths in the table above plus the packaging-added docs and the native changelog/copyright files, and no `.la`, no `.a`, no build detritus.
3. **Native shared-library dependencies are correct** — generated by `dh_shlibdeps` and RPM's automatic ELF requires, not written by hand, and covering `libvinylapi` and the C library.
4. **The exact Vinyl ABI dependency is present** — the built package depends on `vinyld-abi-<hash>` / `vinyld(abi)` for the hash recorded in the cohort manifest, verified by reading the built package's metadata rather than by re-reading the template.
5. **Installation against matching Vinyl succeeds** — clean install, the `.so` is in the runtime's configured VMOD directory, a VCL containing `import cachetag` compiles, and the fetch/tag/hit/purge/stale smoke passes. On EL9 this runs with SELinux enforcing, confirming `restorecon` leaves the context `matchpathcon` predicts and that no relevant AVC denial appears.
6. **Every documented upgrade command has a tested, documented resolver outcome** — `apt upgrade`, `apt full-upgrade`, `dnf upgrade`, `dnf upgrade --allowerasing`, `dnf distro-sync`, and direct installation of a mismatched Vinyl package, each run from a retained previous cohort or a synthetic mismatch fixture, with the results published. The supported path must never silently remove an imported VMOD.
7. **The package manifest identifies the cohort or the exact distro-native Vinyl package revision.**
8. **Uninstall removes package-owned files without modifying user VCL or daemon state.**

Criteria 1 through 4 become testable as soon as Vinyl packages exist. Criteria 5 through 8 additionally need a running installed cohort and a retained mismatch fixture.

## Validation performed

Done on the host, without installing anything and without any package tooling:

- `sh -n packaging/check-tokens.sh` — clean.
- `packaging/check-tokens.sh --templates` — every at-sign token in the tree is a declared token; this caught one accidental token-shaped string in a comment.
- A full substitution dry run into a scratch directory with plausible values, then `check-tokens.sh --substituted` — no token survives.
- `make -f debian/rules -n build` on the substituted copy — GNU make parses `debian/rules` and reaches the `dh` sequence.
- The spec's `%build`, `%install` and `%check` shell bodies extracted, RPM macros stubbed out, and `sh -n`-checked — clean.
- `debian/changelog` header and trailer lines matched against the strict Debian changelog grammar, including the two-space separator before the RFC 2822 date.
- The installed-file list cross-checked line by line against `src/Makefile.am`, `configure.ac`, `Makefile.am` and Vinyl's `vinylapi.pc` and `vinyl.m4`, and against the file list of an existing generated source archive.

## Validation deferred

Everything that needs a Debian or RPM toolchain, or a Vinyl package to build against:

- `dpkg-buildpackage`, `dpkg-source` (the `3.0 (quilt)` round trip), `dpkg-parsechangelog`, `dh` execution and every debhelper override actually running;
- `lintian` and `rpmlint`;
- `rpmbuild -bs`/`-bb` and `rpmspec --parse`: the spec has never been parsed by RPM, so macro-expansion errors remain possible;
- resolution of `Build-Depends`/`BuildRequires` in a clean chroot, including whether `python3-docutils` on EL9 comes from AppStream, CRB or EPEL;
- whether the RPM `%doc`-plus-explicit-`%{_pkgdocdir}`-file combination behaves as expected on EL9's rpm, and whether `%configure` needs the explicit `--docdir`;
- the `%global __provides_exclude_from` filter actually suppressing the plugin's soname provide;
- `dh_shlibdeps` and RPM automatic requires output;
- hardening inspection (`hardening-check`, `annocheck`);
- everything in acceptance criteria 5 through 8.

## Known gaps

- **`LICENSE` is distributed** since it was added to `Makefile.am`'s `EXTRA_DIST` (2026-07-24); the spec's `%license LICENSE` builds. `USAGE.md` and `INSTALL.md` joined `EXTRA_DIST` in the public-release rewrite, so both now ship in the archive; neither is packaged. The same rewrite dropped the VMOD reference document, which both recipes do package — that made the v1.0.0 archive unbuildable as a package, and v1.0.1 restores it to `EXTRA_DIST`.
- **`Multi-Arch: same` is not set** on the Debian binary package. Whether it is correct depends on whether the Vinyl packages install VMODs under a multiarch `libdir` or a plain `/usr/lib/vinyl-cache/vmods`, which is decided by plan step 7. Revisit once that is settled; the `@VINYL_VMODDIR@` assertion in `debian/rules` will fail loudly if the assumption drifts.
- **The maintainer address does not accept mail.** The identity was decided 2026-07-25 as `Boffinate <noreply@boffinate.com>`; it is deliberately a no-reply address, so every published package must carry a `Homepage`/`Vcs-Browser` (Debian) or `URL` (RPM) field pointing at the GitHub repository, whose issue tracker is the real support and security-report channel.
- **No `debian/patches`.** There are no downstream patches. If one is ever needed, add `debian/patches/` with a `series` file; the source format already supports it.
- **The build-time test is deliberately narrow.** Both recipes run only the self-contained `cachetag_wal_test` during the package build, because the VTC suite needs `vinyltest` and a matching running runtime. Behavioural coverage of the packaged artifact is the installed-package smoke test's job, and that gate is not optional just because the build ran a unit test.
- **No architecture beyond amd64/x86_64 is implied.** The recipes are architecture-independent in form, but a lane is supported only once it has been built and tested.
