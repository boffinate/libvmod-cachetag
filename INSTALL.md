# Installing cachetag

`cachetag` is an experimental VMOD for a compatible Vinyl Cache development build. It uses Vinyl's private cache APIs, so build it against the same Vinyl prefix that will run it. This guide covers Vinyl's Default storage and Slash's Buddy and Fellow storage engines.

The commands use `/opt/vinyl` as the installation prefix. Change it to match your setup.

## Before you start

You need:

- a compatible Vinyl Cache source checkout and installed development prefix;
- this `libvmod-cachetag` checkout;
- for Buddy or Fellow, a Slash checkout.

Use a Vinyl build that provides `vinylapi` through `pkg-config`, has persistent-storage support enabled, and includes the private headers that this VMOD needs. The supported and tested target is Vinyl Cache 9.x.

Set the paths you will use below:

```sh
export VINYL_PREFIX=/opt/vinyl
export VINYL_SRC=/path/to/vinyl-cache
export CACHETAG_SRC=/path/to/libvmod-cachetag
export PKG_CONFIG_PATH="$VINYL_PREFIX/lib/pkgconfig:${PKG_CONFIG_PATH}"
export PATH="$VINYL_PREFIX/sbin:$VINYL_PREFIX/bin:${PATH}"
```

On platforms that use an architecture-specific pkg-config directory, add it as well. Common locations include `$VINYL_PREFIX/lib/aarch64-linux-gnu/pkgconfig` and `$VINYL_PREFIX/lib/x86_64-linux-gnu/pkgconfig`.

## Install the cachetag VMOD

Build and install cachetag against the Vinyl prefix:

```sh
cd "$CACHETAG_SRC"
./bootstrap --prefix="$VINYL_PREFIX"
make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
sudo make install
```

The configure summary prints the VMOD directory. `make install` puts `libvmod_cachetag.so` there, normally under the Vinyl prefix's VMOD directory. If Vinyl runs from another prefix or has a custom `vmod_path`, make that directory available to the daemon.

### Optional diagnostic build flags

A default build exposes only the production VCL surface and uses direct per-object volatile membership vectors. The diagnostic flags below add extra namespace methods, while set interning selects an experimental internal representation. All are off by default:

- `--enable-demo-diagnostics` builds the read-only diagnostics `.generation()`, `.purge_seq()`, `.purgemap_entries()`, `.purgemap_slots()`, and `.purgemap_bytes()`.
- `--enable-test-hooks` builds every `.test_*()` fault injector and internal toggler used by the regression suite.
- `--enable-set-interning` canonicalizes and hash-conses multi-fold volatile memberships per namespace. It can reduce resident memory where many objects have the same tag set, but adds sorting, hashing, and registry overhead for every multi-fold attach, so use it only after benchmarking the target workload.

Pass them to `./bootstrap` (or `./configure`) like any other configure argument, for example `./bootstrap --prefix="$VINYL_PREFIX" --enable-set-interning`. The generated VCC interface, the manual page, and the VTC test list follow the selected VCL surface; the set-interning flag does not alter production VCL methods. Its test-only allocation-failure hook requires both `--enable-set-interning` and `--enable-test-hooks`.

Restart Vinyl after installing the VMOD, load the VCL for your selected storage backend, and confirm that Vinyl compiles it successfully before sending production traffic.

## Vinyl Cache's built-in ("Default") storage

Start Vinyl with its normal storage configuration; this example makes the default storage 4 GiB:

```sh
vinyld -sdefault,4G -f /etc/vinyl/default.vcl
```

Load the VMOD and create a memory-only namespace in VCL:

```vcl
vcl 4.1;

import cachetag;

backend default {
    .host = "127.0.0.1";
    .port = "8080";
}

sub vcl_init {
    new tags = cachetag.namespace("default");
}

sub vcl_backend_response {
    if (beresp.http.Cache-Tag) {
        tags.add_header(beresp.http.Cache-Tag);
        unset beresp.http.Cache-Tag;
    }
}

sub vcl_hit {
    if (tags.stale()) {
        return (restart);
    }
}

sub vcl_deliver {
    if (tags.stale()) {
        return (restart);
    }
}
```

Add your authenticated purge endpoint in `vcl_recv` and call `tags.purge()` or `tags.purge_header()`. [USAGE](USAGE.md) has a complete example with an ACL and `PURGE` handling.

## Buddy storage

Buddy is Slash's in-memory storage engine. It works with an ordinary, unpatched Slash build; cachetag's Fellow patch stack is not needed.

Build and install Slash against the same Vinyl prefix:

```sh
export SLASH_SRC=/path/to/slash

cd "$SLASH_SRC"
mkdir -p m4
cp "$VINYL_SRC"/m4/ax_*.m4 m4/
cat > m4/ax_execinfo.m4 <<'EOF'
AC_DEFUN([AX_EXECINFO], [
  AC_CHECK_HEADERS([execinfo.h])
  AC_SEARCH_LIBS([backtrace], [execinfo], [$1], [$2])
])
EOF

VINYLSRC="$VINYL_SRC" ./bootstrap --prefix="$VINYL_PREFIX"
make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
sudo make install
```

If Slash is built against an uninstalled Vinyl build tree, give it the generated Vinyl headers explicitly:

```sh
VINYL_BUILD=/path/to/vinyl-build
SLASH_BUILD_CFLAGS="-I$VINYL_BUILD/include -I$VINYL_BUILD/lib/libvsc"
CPPFLAGS="$SLASH_BUILD_CFLAGS" CFLAGS="$SLASH_BUILD_CFLAGS" \
  VINYLSRC="$VINYL_SRC" ./bootstrap --prefix="$VINYL_PREFIX"
```

Restart or start Vinyl with Slash loaded as an extension and a Buddy storage named `buddy`:

```sh
vinyld -E"$VINYL_PREFIX/lib/vinyl-cache/vmods/libvmod_slash.so" \
  -sbuddy=buddy,32G \
  -f /etc/vinyl/default.vcl
```

The installed VMOD directory can vary by prefix. If necessary, replace the `-E` path with the location reported by `make install` or configure Vinyl's `vmod_path` to find it.

If Slash is outside Vinyl's `vmod_path`, import it from its absolute path in VCL:

```vcl
import slash from "/path/to/libvmod_slash.so";
```

Add Slash to your VCL, tune the selected storage in `vcl_init`, and explicitly store responses in it:

```vcl
import slash;
import cachetag;

sub vcl_init {
    slash.tune_buddy(storage.buddy, reserve_chunks = 0);
    new tags = cachetag.namespace("default");
}

sub vcl_backend_response {
    set beresp.storage = storage.buddy;
    if (beresp.http.Cache-Tag) {
        tags.add_header(beresp.http.Cache-Tag);
        unset beresp.http.Cache-Tag;
    }
}
```

Keep the `vcl_hit` and `vcl_deliver` `tags.stale()` checks from the Default example. Buddy uses cachetag's normal volatile membership and does not need `persist_path`.

## Fellow storage

Fellow is Slash's persistent storage engine. It currently requires the cachetag Fellow patch stack. The patches are based on Slash commit `7be4126892dbc58a03f701632e076f312e0332ed`; use that revision, or confirm that every patch applies cleanly to the Slash revision you use.

Start with a clean Slash checkout, apply the patches, then repeat the Slash build and install commands from the Buddy section:

```sh
cd "$SLASH_SRC"
git checkout 7be4126892dbc58a03f701632e076f312e0332ed
git apply "$CACHETAG_SRC"/patches/fellow/*.patch
```

After Slash is rebuilt and installed, create two durable locations owned by the account that runs Vinyl: one for Fellow's storage file and one dedicated to cachetag's namespace state.

```sh
sudo install -d -o vinyl -g vinyl -m 0750 /var/lib/vinyl/cachetag/default
sudo install -d -o vinyl -g vinyl -m 0750 /var/lib/vinyl/fellow
```

Replace `vinyl:vinyl` with the daemon's real user and group. Do not put two cachetag namespaces in the same `persist_path`: the path holds one namespace's WAL and checkpoint manifest. Back up or otherwise preserve both the Fellow storage file and the cachetag directory together; deleting the cachetag directory loses purge history.

Start Vinyl with the Slash extension and a Fellow storage named `fellow`. The final `64KB` is Fellow's expected object-size hint, not a cachetag setting:

```sh
vinyld -E"$VINYL_PREFIX/lib/vinyl-cache/vmods/libvmod_slash.so" \
  -sfellow=fellow,/var/lib/vinyl/fellow/cache.stv,100G,8G,64KB \
  -f /etc/vinyl/default.vcl
```

In VCL, tune Fellow, put cacheable responses into it, and give the cachetag namespace its durable path:

```vcl
import slash;
import cachetag;

sub vcl_init {
    slash.tune_fellow(storage.fellow);
    new tags = cachetag.namespace("default",
        persist_path = "/var/lib/vinyl/cachetag/default");
}

sub vcl_backend_response {
    set beresp.storage = storage.fellow;
    if (beresp.http.Cache-Tag) {
        tags.add_header(beresp.http.Cache-Tag);
        unset beresp.http.Cache-Tag;
    }
}

sub vcl_hit {
    if (tags.stale()) {
        return (restart);
    }
}

sub vcl_deliver {
    if (tags.stale()) {
        return (restart);
    }
}
```

With `persist_path`, cachetag replays purge history before the namespace becomes ready. Fellow stores object membership in its checksummed FDO metadata and cachetag checks it directly on hits, including after a restart. A Fellow object that falls back to transient storage uses volatile cachetag membership instead.

`wal_fsync = grouped` is currently only a compatibility spelling for `strict`; both sync an accepted purge before it returns. Leave the default unless you have a tested reason to change it. The default `purge_history_max_entries = 1000000` bounds retained resident history. Set `0` only if you explicitly want unbounded history.

Fellow needs enough disk for its storage file and enough working-directory space for Slash metadata. On Linux, its memory cache may also need huge pages and a sufficiently high locked-memory limit. Consult Slash's `INSTALL.rst` and `vmod_slash(3)` for sizing and operating-system tuning. If you run Fellow under a restrictive syscall policy, make sure it permits the I/O interface selected when Slash was built; Buddy does not need disk I/O.
