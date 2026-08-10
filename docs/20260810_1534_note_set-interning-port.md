# Set-interning port

**Date:** 2026-08-10
**Task:** Port the private membership-set interning prototype onto rewritten `main` and expose it through `--enable-set-interning`.

## Context

The private `feature/set-interning` branch has no merge base with current `main`, so rebasing its long history would replay obsolete pre-rewrite work. Its original tip remains available as `archive/private-set-interning-prototype`; the new `feature/set-interning` starts from `main` commit `4fe0e48` and ports the final prototype change only.

## Implementation

- A default build retains the existing direct per-object overflow-fold vector representation.
- `--enable-set-interning` sorts and hashes multi-fold memberships, then shares an immutable refcounted set among equal memberships within one namespace index. Single-fold memberships remain inline.
- VSC counters report the enabled registry's set, reference, hit, miss, table, and allocation-byte totals. They remain present and zero in disabled builds so the two configurations are comparable.
- The test-only intern-allocation failure injector is exposed only when both `--enable-set-interning` and `--enable-test-hooks` are configured.
- VTC selection tests each representation's expected accounting, and the default Docker harness enables the experimental option. Passing `CACHE_TAG_CONFIGURE_ARGS=""` continues to exercise the production surface.

## Verification

- `scripts/test-with-vinyl-cache.sh ../vinyl-cache` completed successfully with demo diagnostics, test hooks, and set interning enabled. Both the outer build and the `distcheck` rebuild compiled the generated interfaces and VMOD sources.
- `CACHE_TAG_CHECK_TARGET=check CACHE_TAG_CONFIGURE_ARGS='' scripts/test-with-vinyl-cache.sh ../vinyl-cache` completed successfully with set interning disabled.
- `scripts/test-fellow-with-vinyl-cache.sh ../vinyl-cache` completed successfully with the enabled configuration.
- `git diff --check` completed without whitespace errors.

## Benchmark caveat

No performance conclusion follows from this port. The enabled registry performs sort and hash work for every multi-fold attach, holds a grow-only hash table, and currently allocates or grows while the namespace object mutex is held. Benchmark resident memory, attach latency, purge/sweep latency, lock contention, and distinct-versus-repeated membership distributions before considering a production default.
